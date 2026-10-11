"""One bot turn: call Claude with the docs, run its tool calls, return text and/or a draft.

The model has two read-only tools (issue search and issue lookup) and ``draft_issue``,
which only *proposes* an issue. Filing happens later, in bot code, when the user presses
the File button. So nothing the model is talked into can create an issue on its own.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

import anthropic

from . import intake
from .github import GitHub, GitHubError
from .prompts import TurnContext

log = logging.getLogger(__name__)

MAX_STEPS = 6
LONG_PROMPT_TOKENS = 100_000

SEARCH_TOOL = {
    "name": "search_issues",
    "description": (
        "Search the Engram repository's GitHub issues (open and closed) by keywords. Use it "
        "before drafting an issue to find duplicates, and when a user asks whether a "
        "problem is known. Returns up to six matches with number, state and title."
    ),
    "strict": True,
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["query"],
        "properties": {
            "query": {
                "type": "string",
                "description": "A few distinctive keywords, e.g. 'skip track HTTP 400'.",
            }
        },
    },
}

GET_ISSUE_TOOL = {
    "name": "get_issue",
    "description": (
        "Read one Engram GitHub issue: title, state, labels and the start of its body. Use "
        "it to check whether a search hit really is the user's problem, or when a user "
        "asks about an issue number."
    ),
    "strict": True,
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["number"],
        "properties": {"number": {"type": "integer"}},
    },
}


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read: int = 0
    cache_write: int = 0
    cost_usd: float = 0.0

    def add(self, u, prices: Prices) -> None:
        inp = u.input_tokens or 0
        out = u.output_tokens or 0
        read = getattr(u, "cache_read_input_tokens", 0) or 0
        write = getattr(u, "cache_creation_input_tokens", 0) or 0
        self.input_tokens += inp
        self.output_tokens += out
        self.cache_read += read
        self.cache_write += write
        self.cost_usd += prices.cost(inp, out, read, write)


@dataclass(frozen=True)
class Prices:
    """USD per million tokens. Only used for the spend cap, so an estimate is fine."""

    input: float
    output: float
    cache_read: float
    cache_write: float
    long_prompt_multiplier: float = 1.0

    def cost(self, inp: int, out: int, read: int, write: int) -> float:
        mult = self.long_prompt_multiplier if inp + read + write > LONG_PROMPT_TOKENS else 1.0
        return (
            mult
            * (
                inp * self.input
                + out * self.output
                + read * self.cache_read
                + write * self.cache_write
            )
            / 1_000_000
        )


@dataclass
class TurnResult:
    text: str
    draft: intake.Draft | None = None
    usage: Usage = field(default_factory=Usage)
    tools_used: list[str] = field(default_factory=list)


FALLBACK_TEXT = (
    "Sorry, I couldn't put an answer together just now. Someone from the team will take "
    "a look at this thread."
)
REFUSAL_TEXT = "Sorry, that's not something I can help with."
DRAFT_TEXT = "Here's a draft issue. Check it over, then press **File issue** when it looks right."


class Agent:
    def __init__(
        self,
        client: anthropic.AsyncAnthropic,
        github: GitHub,
        *,
        model: str,
        effort: str,
        max_tokens: int,
        system_prompt: str,
        allowed_labels: tuple[str, ...],
        prices: Prices,
    ) -> None:
        self._client = client
        self._gh = github
        self._model = model
        self._effort = effort
        self._max_tokens = max_tokens
        self._system_prompt = system_prompt
        self.allowed_labels = allowed_labels
        self._prices = prices
        self._draft_tool = intake.draft_tool(allowed_labels)

    def set_allowed_labels(self, labels: tuple[str, ...]) -> None:
        self.allowed_labels = labels
        self._draft_tool = intake.draft_tool(labels)

    def _tools(self, can_draft: bool) -> list[dict]:
        tools = [SEARCH_TOOL, GET_ISSUE_TOOL]
        if can_draft:
            tools.append(self._draft_tool)
        return tools

    def _system(self, knowledge: str) -> list[dict]:
        return [
            {"type": "text", "text": self._system_prompt},
            {
                "type": "text",
                "text": knowledge or "(The knowledge base failed to load.)",
                "cache_control": {"type": "ephemeral", "ttl": "1h"},
            },
        ]

    async def respond(
        self, conversation: list[dict], context: TurnContext, knowledge: str
    ) -> TurnResult:
        """``conversation`` is history from ``history.build_messages`` (ends on a user turn)."""
        can_draft = context.mode != "ask" and context.can_file
        tools = self._tools(can_draft)
        messages = [*conversation, {"role": "system", "content": context.render()}]
        result = TurnResult(text="")

        for _ in range(MAX_STEPS):
            try:
                response = await self._client.messages.create(
                    model=self._model,
                    max_tokens=self._max_tokens,
                    system=self._system(knowledge),
                    tools=tools,
                    messages=messages,
                    output_config={"effort": self._effort},
                    cache_control={"type": "ephemeral"},
                )
            except anthropic.APIStatusError as e:
                log.error("Claude API error %s: %s", e.status_code, e.message)
                result.text = result.text or FALLBACK_TEXT
                return result
            except anthropic.APIConnectionError:
                log.error("Claude API unreachable", exc_info=True)
                result.text = result.text or FALLBACK_TEXT
                return result

            result.usage.add(response.usage, self._prices)
            text = "\n\n".join(b.text for b in response.content if b.type == "text").strip()

            if response.stop_reason == "refusal":
                result.text = REFUSAL_TEXT
                return result

            tool_calls = [b for b in response.content if b.type == "tool_use"]
            if response.stop_reason != "tool_use" or not tool_calls:
                result.text = text or result.text or FALLBACK_TEXT
                if response.stop_reason == "max_tokens":
                    log.warning("turn hit max_tokens")
                return result

            messages.append({"role": "assistant", "content": response.content})
            tool_results = []
            for call in tool_calls:
                result.tools_used.append(call.name)
                content, is_error, draft = await self._run_tool(call.name, call.input)
                if draft is not None:
                    result.draft = draft
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": call.id,
                        "content": content,
                        "is_error": is_error,
                    }
                )
            if result.draft is not None:
                # The draft is the turn's output; skip a round trip just for a caption.
                result.text = text or DRAFT_TEXT
                return result
            if text:
                result.text = text
            messages.append({"role": "user", "content": tool_results})

        log.warning("turn ran out of steps")
        result.text = result.text or FALLBACK_TEXT
        return result

    async def _run_tool(self, name: str, args: dict) -> tuple[str, bool, intake.Draft | None]:
        try:
            if name == "search_issues":
                hits = await self._gh.search_issues(str(args.get("query", ""))[:200])
                if not hits:
                    return "No matching issues.", False, None
                lines = [
                    f"#{h.number} [{h.state}{'/' + h.state_reason if h.state_reason else ''}] "
                    f"{h.title} — {h.url}"
                    for h in hits
                ]
                return "\n".join(lines), False, None
            if name == "get_issue":
                issue = await self._gh.get_issue(int(args.get("number", 0)))
                if issue is None:
                    return "No such issue.", True, None
                body = issue.body[:3000]
                payload = {
                    "number": issue.number,
                    "title": issue.title,
                    "state": issue.state,
                    "state_reason": issue.state_reason,
                    "labels": list(issue.labels),
                    "comments": issue.comments,
                    "url": issue.url,
                    "body": body,
                }
                return json.dumps(payload), False, None
            if name == "draft_issue":
                try:
                    draft = intake.Draft.from_tool_input(args, self.allowed_labels)
                except ValueError as e:
                    return f"Draft rejected: {e}. Fix it and call draft_issue again.", True, None
                return "Draft shown to the user with File / Edit / Cancel buttons.", False, draft
        except GitHubError as e:
            log.warning("tool %s failed: %s", name, e)
            return f"GitHub is unavailable right now ({e}).", True, None
        return f"Unknown tool {name}.", True, None
