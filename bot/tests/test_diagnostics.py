import io
import json
import zipfile

import pytest

from engram_bot import diagnostics, intake
from engram_bot.diagnostics import NotABundle

# Mirrors backend/app/api/routes.py::_build_bundle_markdown.
REPORT = """## Bug Report

**Engram version**: 0.39.0
**OS**: Windows 11
**Python**: 3.12.4
**Build**: frozen (sys.frozen=True, _MEIPASS=True)
**MakeMKV**: 1.18.1
**FFmpeg**: not found

### Job Context
- **ID**: 42
- **Label**: SHOW_S1D1
- **Type**: tv
- **State**: failed
- **Error**: Matching failed: no subtitles

### Configuration
- **staging_path**: ~/Engram/staging
- **extras_policy**: keep

### Tracks

| # | Duration (s) | Chapters | Resolution | State | Episode | Conf | Source |
|---|---|---|---|---|---|---|---|
| 0 | 1320 | 5 | 720x480 | failed | - | 0.0 | - |
"""

DETAIL = {
    "id": 42,
    "state": "failed",
    "error_message": "Matching failed: no subtitles",
    "classification_source": "tmdb",
    "classification_confidence": 0.92,
    "tmdb_name": "Some Show",
    "detected_season": 1,
    "titles": [
        {"state": "failed", "matched_episode": None},
        {"state": "matched", "matched_episode": "S01E02"},
    ],
}

LOGS = "\n".join(
    [
        "2026-10-10 10:00:00 | INFO     | job=42 | app.x:f:1 - starting",
        "2026-10-10 10:00:01 | WARNING  | job=42 | app.x:f:2 - slow provider",
        "2026-10-10 10:00:02 | ERROR    | job=42 | app.x:f:3 - no subtitles for S01E01",
        "2026-10-10 10:00:03 | INFO     | job=42 | app.x:f:4 - webhook "
        + "https://discord.com/api/webhooks/123/abcdef",
    ]
)


def make_zip(members: dict[str, str | bytes], **kw) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, **kw) as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return buf.getvalue()


def bundle(**extra) -> bytes:
    members = {
        "report.md": REPORT,
        "job-detail.json": json.dumps(DETAIL),
        "job-logs.txt": LOGS,
        "rip.log": "MSG:5010 Failed to open disc\nMSG:1 all good\n",
    }
    members.update(extra)
    return make_zip(members)


def test_parses_versions_job_and_errors():
    d = diagnostics.parse(bundle(), "engram-bug-report-job-42.zip")
    assert (d.engram_version, d.os, d.makemkv) == ("0.39.0", "Windows 11", "1.18.1")
    assert d.job["ID"] == "42" and d.job["State"] == "failed"
    assert "Error" in d.job
    assert [ln.split(" - ")[-1] for ln in d.error_lines] == [
        "slow provider",
        "no subtitles for S01E01",
    ]
    assert d.makemkv_problems == ["rip.log: MSG:5010 Failed to open disc"]


def test_summary_for_model_is_compact_and_factual():
    text = diagnostics.parse(bundle(), "b.zip").summary_for_model()
    assert "Engram: 0.39.0" in text and "OS: Windows 11" in text
    assert "State=failed" in text
    assert "2 tracks (failed: 1, matched: 1); 1 matched to an episode" in text
    assert "tmdb_name=Some Show" in text
    assert "no subtitles for S01E01" in text
    assert len(text) < 6000


def test_secrets_and_user_paths_are_redacted():
    leaky = LOGS + "\n... | ERROR | job=42 | x - C:\\Users\\alice\\rips failed key=ABCDEFGH123"
    d = diagnostics.parse(bundle(**{"job-logs.txt": leaky}), "b.zip")
    joined = "\n".join(d.error_lines + d.log_tail)
    assert "discord.com/api/webhooks" not in joined
    assert "alice" not in joined and "C:\\Users\\<user>" in joined
    assert "ABCDEFGH123" not in joined


def test_issue_section_is_sanitized_and_collapsed():
    evil = REPORT.replace("SHOW_S1D1", "@claude <script>x</script>")
    section = diagnostics.parse(bundle(**{"report.md": evil}), "b.zip").issue_section()
    assert section.startswith("<details>") and section.rstrip().endswith("</details>")
    assert "@claude" not in section and "<script>" not in section
    assert "#### Job log: errors and warnings" in section
    assert "| 0 | 1320 |" in section  # markdown tables survive


def test_no_errors_falls_back_to_log_tail():
    quiet = "2026-10-10 | INFO | job=1 | a - fine"
    d = diagnostics.parse(bundle(**{"job-logs.txt": quiet}), "b.zip")
    assert d.error_lines == [] and d.log_tail == [quiet]
    assert "Job log: last lines" in d.issue_section()


@pytest.mark.parametrize(
    "data, reason",
    [
        (b"not a zip", "valid zip"),
        (make_zip({"readme.txt": "hi"}), "no report.md"),
        (make_zip({f"f{i}.txt": "x" for i in range(100)}), "too many files"),
        (b"x" * (diagnostics.MAX_ZIP_BYTES + 1), "larger than 8 MB"),
    ],
)
def test_rejects_things_that_are_not_bundles(data, reason):
    with pytest.raises(NotABundle, match=reason):
        diagnostics.parse(data, "x.zip")


def test_decompression_is_capped():
    # 50 MB of zeros compresses to ~50 KB; only MAX_MEMBER_BYTES may be inflated.
    bomb = make_zip({"report.md": REPORT, "job-logs.txt": b"\0" * (50 * 1024 * 1024)})
    assert len(bomb) < 1024 * 1024
    d = diagnostics.parse(bomb, "bomb.zip")
    assert sum(len(x) for x in d.log_tail) <= diagnostics.MAX_MEMBER_BYTES


def test_unknown_members_are_ignored():
    d = diagnostics.parse(bundle(**{"../../etc/passwd": "root"}), "b.zip")
    assert "../../etc/passwd" not in d.members
    assert d.members == ["job-detail.json", "job-logs.txt", "report.md", "rip.log"]


def test_draft_attaches_bundle_unless_user_opted_out():
    d = diagnostics.parse(bundle(), "b.zip")
    base = {name: "" for name in intake._STRING_FIELDS}
    base.update(kind="bug", title="T", summary="S", labels=[], duplicate_of=0)
    reporter = intake.Reporter("ann", "https://t")

    draft = intake.Draft.from_tool_input({**base, "include_diagnostics": True}, ())
    draft.attach_diagnostics(d.issue_section(), d.preview())
    body = intake.render_body(draft, reporter)
    assert "Diagnostics bundle (b.zip)" in body
    assert body.index("Diagnostics bundle") < body.index("Reported on Discord")
    assert ("Diagnostics", d.preview()) in intake.preview_fields(draft)

    opted_out = intake.Draft.from_tool_input({**base, "include_diagnostics": False}, ())
    opted_out.attach_diagnostics(d.issue_section(), d.preview())
    assert "Diagnostics bundle" not in intake.render_body(opted_out, reporter)


def test_model_summary_stays_bounded_with_long_noisy_logs():
    from engram_bot import history

    noisy = "\n".join(f"t | ERROR    | job=1 | x - {'e' * 2000}" for _ in range(500))
    d = diagnostics.parse(bundle(**{"job-logs.txt": noisy}), "b.zip")
    text = d.summary_for_model()
    assert len(text) < history.MAX_MESSAGE_CHARS - 2000
    assert text.endswith("into the logs field.]")


def test_lan_paths_in_a_real_style_bundle_never_reach_the_issue():
    unc = r"\\192.168.86.83\NAS Downloads\Dexter's Laboratory\Season 3\Disc 5\D2_t07.mkv"
    logs = "\n".join(
        f"2026-10-14 10:00:0{i} | ERROR    | job=12 | matcher:match:1 - failed {unc}"
        for i in range(5)
    )
    report = REPORT.replace("~/Engram/staging", r"\\192.168.86.83\NAS Downloads\staging")
    d = diagnostics.parse(bundle(**{"job-logs.txt": logs, "report.md": report}), "b.zip")
    for text in (d.issue_section(), d.summary_for_model()):
        assert "192.168" not in text and "NAS Downloads" not in text
        assert r"<share>\Dexter's Laboratory\Season 3\Disc 5\D2_t07.mkv" in text
