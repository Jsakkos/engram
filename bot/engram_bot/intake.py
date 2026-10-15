"""Issue drafts: the schema the model fills, and how a draft becomes a GitHub issue.

The model never writes the issue body. It fills a fixed set of fields through the
``draft_issue`` tool; this module validates them and renders the body, so every issue
from Discord has the same layout and every field passes through the sanitizer.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from . import sanitize

KINDS = ("bug", "feature", "docs")
MAX_BODY_CHARS = 60_000

KIND_LABEL = {"bug": "bug", "feature": "enhancement", "docs": "documentation"}
KIND_TITLE = {"bug": "Bug report", "feature": "Feature request", "docs": "Documentation"}

_STRING_FIELDS = (
    "title",
    "summary",
    "engram_version",
    "os",
    "install_method",
    "drive_model",
    "disc",
    "steps_to_reproduce",
    "expected_behavior",
    "actual_behavior",
    "use_case",
    "proposed_solution",
    "logs",
)


def draft_tool(allowed_labels: tuple[str, ...]) -> dict:
    """The ``draft_issue`` tool definition. Stable for a given label set (prompt cache)."""
    text = {"type": "string"}
    label_items = {"type": "string", "enum": list(allowed_labels)} if allowed_labels else text
    return {
        "name": "draft_issue",
        "description": (
            "Show the user a draft GitHub issue for them to confirm. Nothing is filed until "
            "the user presses the File button, so call this once you have the essentials; "
            "the user can ask for changes afterwards and you call it again with the "
            "revision. For a bug, the essentials are what happened, what they expected, "
            "and their Engram version and OS; for a feature, the problem it solves. Use an "
            "empty string for any field that does not apply or that the user could not "
            "provide; never invent values. Set duplicate_of to an existing issue number "
            "when search_issues found the same problem, so the report is added to that "
            "issue as a comment instead of opening a new one; otherwise 0. When the user "
            "attached a diagnostics bundle, the bot attaches it to the issue; set "
            "include_diagnostics to false only if they ask to leave it out."
        ),
        "strict": True,
        "input_schema": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                *_STRING_FIELDS,
                "kind",
                "labels",
                "duplicate_of",
                "include_diagnostics",
            ],
            "properties": {
                "kind": {"type": "string", "enum": list(KINDS)},
                "title": {
                    "type": "string",
                    "description": "Specific, under 80 characters, no 'Bug:' prefix.",
                },
                "summary": {
                    "type": "string",
                    "description": "Two to four sentences in the user's terms.",
                },
                "engram_version": text,
                "os": {"type": "string", "description": "e.g. 'Windows 11', 'Ubuntu 24.04'."},
                "install_method": {
                    "type": "string",
                    "description": "'Windows installer', 'Linux binary', 'Docker', 'source'.",
                },
                "drive_model": text,
                "disc": {"type": "string", "description": "Disc title or volume label."},
                "steps_to_reproduce": {
                    "type": "string",
                    "description": "Numbered steps, one per line.",
                },
                "expected_behavior": text,
                "actual_behavior": text,
                "use_case": {
                    "type": "string",
                    "description": "Feature requests: the problem the user is trying to solve.",
                },
                "proposed_solution": text,
                "logs": {
                    "type": "string",
                    "description": "Log lines or error text the user pasted, verbatim.",
                },
                "labels": {
                    "type": "array",
                    "items": label_items,
                    "description": "Area labels that clearly apply. May be empty.",
                },
                "duplicate_of": {"type": "integer"},
                "include_diagnostics": {"type": "boolean"},
            },
        },
    }


@dataclass
class Draft:
    kind: str
    title: str
    summary: str
    engram_version: str = ""
    os: str = ""
    install_method: str = ""
    drive_model: str = ""
    disc: str = ""
    steps_to_reproduce: str = ""
    expected_behavior: str = ""
    actual_behavior: str = ""
    use_case: str = ""
    proposed_solution: str = ""
    logs: str = ""
    labels: list[str] = field(default_factory=list)
    duplicate_of: int = 0
    include_diagnostics: bool = True
    # Set by the bot, not the model: the reporter's latest diagnostics bundle, rendered.
    diagnostics: str = ""
    diagnostics_note: str = ""

    @classmethod
    def from_tool_input(cls, data: dict, allowed_labels: tuple[str, ...]) -> Draft:
        """Validate model output. Raises ``ValueError`` with a message the model can act on."""
        kind = data.get("kind")
        if kind not in KINDS:
            raise ValueError(f"kind must be one of {', '.join(KINDS)}")
        values = {name: str(data.get(name) or "").strip() for name in _STRING_FIELDS}
        if not values["title"]:
            raise ValueError("title is required")
        if not values["summary"]:
            raise ValueError("summary is required")
        labels = [lbl for lbl in data.get("labels") or [] if lbl in allowed_labels]
        try:
            duplicate_of = max(0, int(data.get("duplicate_of") or 0))
        except (TypeError, ValueError):
            duplicate_of = 0
        return cls(
            kind=kind,
            labels=sorted(set(labels)),
            duplicate_of=duplicate_of,
            include_diagnostics=data.get("include_diagnostics") is not False,
            **values,
        )

    def to_json(self) -> dict:
        return asdict(self)

    @classmethod
    def from_json(cls, data: dict) -> Draft:
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in known})

    def attach_diagnostics(self, section: str, note: str) -> None:
        if self.include_diagnostics:
            self.diagnostics = section
            self.diagnostics_note = note

    @property
    def is_comment(self) -> bool:
        return self.duplicate_of > 0

    def issue_title(self) -> str:
        return sanitize.clean_inline(self.title, 120)

    def issue_labels(self, always: tuple[str, ...]) -> list[str]:
        return sorted({*always, KIND_LABEL[self.kind], *self.labels})


@dataclass(frozen=True)
class Reporter:
    """Who reported it, and where. Rendered into the issue footer."""

    display_name: str
    thread_url: str


def _environment_table(d: Draft) -> str:
    rows = [
        ("Engram version", d.engram_version),
        ("OS", d.os),
        ("Install method", d.install_method),
        ("Optical drive", d.drive_model),
        ("Disc", d.disc),
    ]
    rows = [(k, v) for k, v in rows if v]
    if not rows:
        return ""
    lines = ["| | |", "|---|---|"]
    lines += [f"| {k} | {sanitize.clean_inline(v)} |" for k, v in rows]
    return "\n".join(lines)


def _section(heading: str, body: str, limit: int = 4000) -> str:
    return f"### {heading}\n\n{sanitize.clean(body, limit)}" if body.strip() else ""


def render_body(d: Draft, reporter: Reporter) -> str:
    parts = [_section("Summary", d.summary)]
    if d.kind == "bug":
        env = _environment_table(d)
        if env:
            parts.append("### Environment\n\n" + env)
        parts += [
            _section("Steps to reproduce", d.steps_to_reproduce),
            _section("Expected behavior", d.expected_behavior),
            _section("Actual behavior", d.actual_behavior),
        ]
    else:
        parts += [
            _section("Use case", d.use_case),
            _section("Proposed solution", d.proposed_solution),
        ]
        env = _environment_table(d)
        if env:
            parts.append("### Environment\n\n" + env)
    logs = ""
    if d.logs.strip():
        logs = "<details>\n<summary>Logs</summary>\n\n" + sanitize.fence(d.logs) + "\n\n</details>"
    diagnostics = d.diagnostics if d.include_diagnostics else ""
    footer = _footer(reporter)

    def assemble() -> str:
        return "\n\n".join(p for p in [*parts, logs, diagnostics, footer] if p)

    # GitHub rejects bodies over 65,536 characters. Shed the bulkiest optional parts first.
    body = assemble()
    if len(body) > MAX_BODY_CHARS and logs:
        logs = "_Pasted logs omitted for length; they are in the Discord thread._"
        body = assemble()
    if len(body) > MAX_BODY_CHARS and diagnostics:
        diagnostics = "_Diagnostics omitted for length; the bundle is in the Discord thread._"
        body = assemble()
    return body


def render_comment(d: Draft, reporter: Reporter) -> str:
    """A duplicate report, added to the existing issue instead of opening a new one."""
    intro = "Another report of this from Discord."
    body = render_body(d, reporter)
    return f"{intro}\n\n{body}"


def _footer(reporter: Reporter) -> str:
    name = sanitize.clean_inline(reporter.display_name, 80)
    return (
        "---\n"
        f"_Reported on Discord by **{name}** · [conversation]({reporter.thread_url}) · "
        "drafted by the Engram support bot and confirmed by the reporter._"
    )


def preview_fields(d: Draft) -> list[tuple[str, str]]:
    """(name, value) pairs for the Discord embed preview. Discord caps a field at 1024."""

    def cap(text: str) -> str:
        return text if len(text) <= 1000 else text[:990] + " …"

    fields: list[tuple[str, str]] = []
    env = ", ".join(v for v in (d.engram_version, d.os, d.install_method, d.drive_model) if v)
    if env:
        fields.append(("Environment", cap(env)))
    if d.kind == "bug":
        for name, value in (
            ("Steps to reproduce", d.steps_to_reproduce),
            ("Expected", d.expected_behavior),
            ("Actual", d.actual_behavior),
        ):
            if value:
                fields.append((name, cap(value)))
    else:
        for name, value in (("Use case", d.use_case), ("Proposed solution", d.proposed_solution)):
            if value:
                fields.append((name, cap(value)))
    if d.logs:
        fields.append(("Logs", f"{len(d.logs.splitlines())} line(s) attached"))
    if d.diagnostics and d.include_diagnostics:
        fields.append(("Diagnostics", cap(d.diagnostics_note or "bundle attached")))
    return fields


def describe_for_history(d: Draft, draft_id: int) -> str:
    """How a posted draft reads back to the model on later turns (embeds have no text)."""
    target = f"comment on #{d.duplicate_of}" if d.is_comment else f"new {d.kind} issue"
    return (
        f"[Draft {draft_id} shown to the user as a {target}: title={d.title!r}; "
        f"summary={d.summary!r}; labels={d.labels}]"
    )
