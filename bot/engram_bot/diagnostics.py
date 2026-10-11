"""Read an Engram diagnostics bundle (the per-job ``.zip`` from the dashboard).

The backend builds it in ``download_bug_report_bundle`` (``backend/app/api/routes.py``):
``report.md``, ``job-detail.json``, ``job-logs.txt`` and, when present, MakeMKV's raw
``scan.log`` / ``rip.log``. Users attach it in Discord; the bot turns it into

- a compact summary the model reads (versions, job state, the errors), so it can
  troubleshoot and fill the draft's environment fields without asking; and
- a collapsed "Diagnostics" section the bot appends to the issue itself, so the evidence
  reaches GitHub verbatim instead of being paraphrased by the model.

The archive is untrusted input from the internet. Only the known member names are read,
each with a hard byte cap (a zip bomb can lie about its sizes), and everything goes through
``sanitize.redact`` on top of the backend's own sanitization, which misses some secret shapes
(webhook URLs, MakeMKV keys) and only strips the *server's* home directory.
"""

from __future__ import annotations

import io
import json
import re
import zipfile
from dataclasses import dataclass, field

from . import sanitize

MAX_ZIP_BYTES = 8 * 1024 * 1024
MAX_MEMBER_BYTES = 2 * 1024 * 1024
MAX_MEMBERS = 64
MEMBERS = ("report.md", "job-detail.json", "job-logs.txt", "scan.log", "rip.log")

REPORT_ISSUE_CHARS = 8_000
LOG_ISSUE_CHARS = 10_000
LOG_PROMPT_LINES = 25
LOG_LINE_CHARS = 240  # per line in the model summary; keeps it well under the history cap

_ERROR_LINE = re.compile(r"\|\s*(ERROR|CRITICAL)\s*\||Traceback|\bException\b|\bError:")
_WARNING_LINE = re.compile(r"\|\s*WARNING\s*\|")
_MAKEMKV_PROBLEM = re.compile(r"(?i)\b(error|fail(ed|ure)?|can't|cannot|corrupt|timeout)\b")
_REPORT_FIELD = re.compile(r"^\*\*(?P<key>[^*]+)\*\*:\s*(?P<value>.*)$", re.M)
_JOB_FIELD = re.compile(r"^- \*\*(?P<key>[^*]+)\*\*:\s*(?P<value>.*)$", re.M)


class NotABundle(ValueError):
    """The attachment isn't a readable Engram diagnostics bundle. The message says why."""


@dataclass
class Diagnostics:
    filename: str
    engram_version: str = ""
    os: str = ""
    makemkv: str = ""
    job: dict[str, str] = field(default_factory=dict)  # ID, Label, Type, State, Error
    detail: dict = field(default_factory=dict)
    report_md: str = ""
    error_lines: list[str] = field(default_factory=list)
    log_tail: list[str] = field(default_factory=list)
    makemkv_problems: list[str] = field(default_factory=list)
    members: list[str] = field(default_factory=list)

    # -- for the model --------------------------------------------------------------

    def summary_for_model(self) -> str:
        lines = [f"[attached diagnostics bundle {self.filename}, read by the bot]"]
        env = ", ".join(
            f"{k}: {v}"
            for k, v in (
                ("Engram", self.engram_version),
                ("OS", self.os),
                ("MakeMKV", self.makemkv),
            )
            if v
        )
        if env:
            lines.append(env)
        if self.job:
            lines.append("Job: " + "; ".join(f"{k}={v}" for k, v in self.job.items()))
        facts = self._detail_facts()
        if facts:
            lines.append("Detail: " + "; ".join(facts))
        if self.error_lines:
            lines.append(f"Errors and warnings from the job log (last {LOG_PROMPT_LINES}):")
            lines.append("```")
            lines += [_shorten(ln, LOG_LINE_CHARS) for ln in self.error_lines[-LOG_PROMPT_LINES:]]
            lines.append("```")
        elif self.log_tail:
            lines.append("No errors in the job log. Its last lines:")
            lines.append("```")
            lines += [_shorten(ln, LOG_LINE_CHARS) for ln in self.log_tail[-10:]]
            lines.append("```")
        if self.makemkv_problems:
            lines.append("MakeMKV log problems:")
            lines.append("```")
            lines += [_shorten(ln, LOG_LINE_CHARS) for ln in self.makemkv_problems[-10:]]
            lines.append("```")
        lines.append(
            "[If this becomes an issue, the bot attaches the bundle's report and log excerpt "
            "to it automatically; you don't need to copy them into the logs field.]"
        )
        return "\n".join(lines)

    def _detail_facts(self) -> list[str]:
        d = self.detail
        facts = []
        for key in (
            "review_reason",
            "error_message",
            "classification_source",
            "classification_confidence",
            "tmdb_name",
            "tmdb_year",
            "detected_season",
            "tmdb_degraded_reason",
            "episode_namespace",
        ):
            value = d.get(key)
            if value not in (None, "", 0, 0.0):
                facts.append(f"{key}={_shorten(str(value), 160)}")
        titles = d.get("titles") or []
        if titles:
            states: dict[str, int] = {}
            for t in titles:
                states[str(t.get("state"))] = states.get(str(t.get("state")), 0) + 1
            matched = sum(1 for t in titles if t.get("matched_episode"))
            breakdown = ", ".join(f"{k}: {v}" for k, v in sorted(states.items()))
            facts.append(f"{len(titles)} tracks ({breakdown}); {matched} matched to an episode")
        return facts

    # -- for the issue --------------------------------------------------------------

    def preview(self) -> str:
        job = self.job.get("ID")
        what = f"job {job}" if job else self.filename
        n = len(self.error_lines)
        return f"{what}: report + {n} error/warning log line(s) will be attached"

    def issue_section(self) -> str:
        """Collapsed markdown block appended to the issue body. Sanitized here."""
        title = f"Diagnostics bundle ({sanitize.clean_inline(self.filename, 80)})"
        parts = [f"<details>\n<summary>{title}</summary>\n"]
        if self.report_md:
            parts.append(sanitize.clean(self.report_md, REPORT_ISSUE_CHARS))
        log = self.error_lines or self.log_tail
        if log:
            label = "Job log: errors and warnings" if self.error_lines else "Job log: last lines"
            parts.append(f"#### {label}\n\n" + sanitize.fence("\n".join(log), LOG_ISSUE_CHARS))
        if self.makemkv_problems:
            parts.append(
                "#### MakeMKV log problems\n\n"
                + sanitize.fence("\n".join(self.makemkv_problems[-40:]), 4_000)
            )
        parts.append("</details>")
        return "\n\n".join(parts)


def parse(data: bytes, filename: str) -> Diagnostics:
    if len(data) > MAX_ZIP_BYTES:
        raise NotABundle("it's larger than 8 MB")
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as e:
        raise NotABundle("it isn't a valid zip file") from e
    with zf:
        infos = zf.infolist()
        if len(infos) > MAX_MEMBERS:
            raise NotABundle("it has too many files to be an Engram bundle")
        by_name = {info.filename: info for info in infos if not info.is_dir()}
        if "report.md" not in by_name and "job-detail.json" not in by_name:
            raise NotABundle("it has no report.md or job-detail.json")
        texts = {name: _read(zf, by_name[name]) for name in MEMBERS if name in by_name}

    diag = Diagnostics(filename=filename, members=sorted(texts))
    report = texts.get("report.md", "")
    diag.report_md = report
    fields = {m["key"].strip(): m["value"].strip() for m in _REPORT_FIELD.finditer(report)}
    diag.engram_version = fields.get("Engram version", "")
    diag.os = fields.get("OS", "")
    diag.makemkv = fields.get("MakeMKV", "")
    diag.job = {
        m["key"].strip(): _shorten(m["value"].strip(), 300)
        for m in _JOB_FIELD.finditer(_section(report, "### Job Context"))
    }

    if "job-detail.json" in texts:
        try:
            detail = json.loads(texts["job-detail.json"])
        except json.JSONDecodeError:
            detail = {}
        diag.detail = detail if isinstance(detail, dict) else {}

    log_lines = [ln for ln in texts.get("job-logs.txt", "").splitlines() if ln.strip()]
    diag.error_lines = [
        ln for ln in log_lines if _ERROR_LINE.search(ln) or _WARNING_LINE.search(ln)
    ][-200:]
    diag.log_tail = log_lines[-40:]
    for name in ("scan.log", "rip.log"):
        problems = [ln for ln in texts.get(name, "").splitlines() if _MAKEMKV_PROBLEM.search(ln)]
        diag.makemkv_problems += [f"{name}: {ln}" for ln in problems[-20:]]
    return diag


def _read(zf: zipfile.ZipFile, info: zipfile.ZipInfo) -> str:
    """Read at most MAX_MEMBER_BYTES (decompressed), whatever the header claims."""
    try:
        with zf.open(info) as fh:
            raw = fh.read(MAX_MEMBER_BYTES)
    except (zipfile.BadZipFile, RuntimeError, NotImplementedError, OSError) as e:
        # RuntimeError: encrypted member; NotImplementedError: unsupported compression.
        raise NotABundle(f"{info.filename} couldn't be read") from e
    return sanitize.redact(raw.decode("utf-8", errors="replace"))


def _section(markdown: str, heading: str) -> str:
    start = markdown.find(heading)
    if start < 0:
        return ""
    end = markdown.find("\n#", start + len(heading))
    return markdown[start : end if end >= 0 else len(markdown)]


def _shorten(text: str, limit: int = 400) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"
