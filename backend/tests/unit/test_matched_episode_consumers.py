"""Every module that parses matched_episode must say how it treats TheTVDB codes.

A TheTVDB-numbered job stores TVDB codes in matched_episode (spec 2026-10-08).
Code that publishes or compares against TMDB identity must translate through
app.core.episode_namespace; code that only displays or files the stored code
is safe as-is. A new consumer fails this test until it is classified here.
"""

from pathlib import Path

APP = Path(__file__).resolve().parents[2] / "app"
PARSERS = ("parse_episode_code", "episode_parts", "discdb_episode_fields")

CLASSIFIED = {
    # Translates through episode_namespace (to_tmdb_code / from_tmdb_code / roster).
    "api/routes.py": "namespace-aware",
    "core/discdb_exporter.py": "namespace-aware",
    "services/disc_contribution_queue.py": "namespace-aware",
    "services/matching_coordinator.py": "namespace-aware",
    # Uses the stored code as the output number; correct in either namespace.
    "core/organizer.py": "display-safe",
    "core/discord_notifier.py": "display-safe",
    "services/job_manager.py": "display-safe",
    # The parser itself.
    "core/episode_codes.py": "parser",
}


def _consumers() -> set[str]:
    found = set()
    for path in APP.rglob("*.py"):
        text = path.read_text("utf-8")
        if "matched_episode" in text and any(p in text for p in PARSERS):
            found.add(path.relative_to(APP).as_posix())
    return found


def test_every_matched_episode_parser_is_classified():
    unclassified = _consumers() - set(CLASSIFIED)
    assert not unclassified, (
        f"Classify these in CLASSIFIED (namespace-aware or display-safe): {sorted(unclassified)}"
    )


def test_classification_has_no_stale_entries():
    stale = set(CLASSIFIED) - _consumers()
    assert not stale, f"No longer parse matched_episode; remove: {sorted(stale)}"
