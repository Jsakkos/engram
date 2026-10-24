"""Make Discord-sourced text safe to publish in a GitHub issue.

Everything a user (or the model, which read the user) wrote is untrusted. On GitHub that
text must not ping people (``@user``, ``@org/team``), must not reach the repo's
``@claude`` workflow trigger, and must not inject raw HTML into the rendered issue.
"""

from __future__ import annotations

import html
import re

ZWSP = "​"

# GitHub mention syntax: @name or @org/team, not preceded by a word char (so emails pass).
# Backticks are NOT exempt: workflows match the raw body, where `@claude` in a code span
# still contains "@claude".
_MENTION = re.compile(r"(?<!\w)@(?=[A-Za-z0-9][A-Za-z0-9-]*)")
# Discord's raw mention / channel / role / emoji markup.
_DISCORD_USER = re.compile(r"<@!?\d+>")
_DISCORD_ROLE = re.compile(r"<@&\d+>")
_DISCORD_CHANNEL = re.compile(r"<#\d+>")
_DISCORD_EMOJI = re.compile(r"<a?:(\w+):\d+>")
_EVERYONE = re.compile(r"@(everyone|here)\b")

# A UNC prefix starts a token (line start, space, quote, bracket, "="). Without this, a
# Windows path logged as a repr ("C:\\Program Files\\MakeMKV") would look like one.
_UNC_START = r"(?<![^\s'\"(\[=])"

# Secrets and personal paths. Engram's diagnostics are already scrubbed by the backend, but
# users also paste raw logs, and the backend misses some shapes (webhook URLs, MakeMKV keys)
# and only knows its own home directory.
_REDACTIONS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"https://(?:\w+\.)?discord(?:app)?\.com/api/webhooks/\S+"), "<discord-webhook>"),
    (re.compile(r"\bT-[A-Za-z0-9@_-]{40,}"), "<makemkv-key>"),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{20,}(?:\.[A-Za-z0-9_-]+)*"), "<token>"),
    (re.compile(r"\bsk-ant-[A-Za-z0-9_-]{10,}"), "<anthropic-key>"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"), "<github-token>"),
    (
        re.compile(r"(?i)\b(api[_-]?key|key|token|password|passwd|secret)\s*[=:]\s*[^\s,;'\"]{6,}"),
        r"\1=<redacted>",
    ),
    (re.compile(r"(?i)\b([A-Z]:\\Users\\)[^\\\s]+"), r"\1<user>"),
    (re.compile(r"(/home/|/Users/)[^/\s]+"), r"\1<user>"),
    # Network shares name the user's LAN. Keep the rest of the path: it's what makes a log
    # line useful. Separators may be doubled when a path was logged as a Python repr.
    # Share names can contain spaces, so a share runs to the next separator; a bare
    # "\\host\share" at the end of a path is handled by the second pattern.
    (
        re.compile(_UNC_START + r"(\\{2,4})[^\\/\s'\"]+(\\{1,2})[^\\\n'\"]+?(?=\\)"),
        r"\1<host>\2<share>",
    ),
    (
        re.compile(_UNC_START + r"(\\{2,4})[^\\/\s'\"]+(\\{1,2})[^\\\s'\"]+"),
        r"\1<host>\2<share>",
    ),
    (re.compile(r"(?i)\b(smb|nfs|afp|cifs)://[^/\s]+(/[^/\n]+)?"), r"\1://<host>/<share>"),
    # //host/share/ (forward-slash UNC); not URLs, whose // follows a scheme's colon.
    (re.compile(r"(?<![:\w/])//[^/\s]+/[^/\n]+?(?=/)"), "//<host>/<share>"),
    # Private-range IPv4 (RFC 1918 and the 100.64/10 CGNAT range Tailscale uses), anywhere:
    # bare, in URLs (http://192.168.1.5:8000) or left over in a path.
    (
        re.compile(
            r"(?<![\d.])(?:10(?:\.\d{1,3}){3}|192\.168(?:\.\d{1,3}){2}"
            r"|172\.(?:1[6-9]|2\d|3[01])(?:\.\d{1,3}){2}"
            r"|100\.(?:6[4-9]|[7-9]\d|1[01]\d|12[0-7])(?:\.\d{1,3}){2})(?![\d.]*\d)"
        ),
        "<lan-ip>",
    ),
)


def redact(text: str) -> str:
    for pattern, replacement in _REDACTIONS:
        text = pattern.sub(replacement, text)
    return text


def strip_discord_markup(text: str) -> str:
    text = _DISCORD_USER.sub("@user", text)
    text = _DISCORD_ROLE.sub("@role", text)
    text = _DISCORD_CHANNEL.sub("#channel", text)
    text = _DISCORD_EMOJI.sub(r":\1:", text)
    return text


def neutralize_mentions(text: str) -> str:
    """Break ``@name`` so GitHub renders it as text instead of a notification."""
    text = _EVERYONE.sub(lambda m: "@" + ZWSP + m.group(1), text)
    return _MENTION.sub("@" + ZWSP, text)


def truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 20)].rstrip() + "\n… (truncated)"


def clean(text: str, limit: int = 4000) -> str:
    """Prose field: strip Discord markup, escape HTML, defuse mentions, cap length."""
    text = redact(strip_discord_markup(text or "")).strip()
    text = html.escape(text, quote=False)
    text = neutralize_mentions(text)
    return truncate(text, limit)


def clean_inline(text: str, limit: int = 200) -> str:
    """Single-line field (titles, table cells)."""
    text = " ".join((text or "").split())
    text = clean(text, limit)
    return text.replace("|", "\\|")


def fence(text: str, limit: int = 20000, lang: str = "") -> str:
    """Wrap verbatim text (logs) in a code fence longer than any backtick run inside it.

    Code blocks are not HTML-rendered and GitHub does not resolve mentions inside them.
    Mentions are still defused, because workflows match on the raw body text
    (``contains(github.event.issue.body, '@claude')``), not the rendered issue.
    """
    text = redact(strip_discord_markup(text or "")).strip("\n")
    text = truncate(neutralize_mentions(text), limit)
    longest = max((len(m) for m in re.findall(r"`+", text)), default=0)
    ticks = "`" * max(3, longest + 1)
    return f"{ticks}{lang}\n{text}\n{ticks}"
