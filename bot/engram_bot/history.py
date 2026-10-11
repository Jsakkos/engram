"""Turn a Discord thread into a Messages API conversation.

The thread is the source of truth, so each turn rebuilds the history from it. Only text
goes back to the model (never earlier thinking blocks), which keeps the request valid
however Discord's view of the thread changes: edits, deletions, other people joining.
"""

from __future__ import annotations

from dataclasses import dataclass

MAX_MESSAGE_CHARS = 6000


@dataclass(frozen=True)
class Item:
    role: str  # user | assistant
    text: str
    author: str = ""


def build_messages(items: list[Item]) -> list[dict]:
    """Merge consecutive same-role items; user text is prefixed with the speaker's name
    because a thread can have several people in it. The result starts and ends on a user
    turn, or is empty."""
    merged: list[dict] = []
    for item in items:
        text = item.text.strip()
        if not text:
            continue
        if len(text) > MAX_MESSAGE_CHARS:
            text = text[:MAX_MESSAGE_CHARS] + "\n… (message truncated)"
        if item.role == "user" and item.author:
            text = f"{item.author}: {text}"
        if merged and merged[-1]["role"] == item.role:
            merged[-1]["content"] += "\n\n" + text
        else:
            merged.append({"role": item.role, "content": text})
    while merged and merged[0]["role"] != "user":
        merged.pop(0)
    while merged and merged[-1]["role"] != "user":
        merged.pop()
    return merged
