"""Regression tests for EpisodeMatcher thread-safety under parallel ASR.

The shared ``curator`` singleton holds ONE ``EpisodeMatcher`` per (show, tmdb_id).
Since #336 (parallel ASR) several titles match concurrently via
``asyncio.to_thread``, so multiple threads call ``identify_episode`` on the SAME
matcher instance. The per-season working state (the TF-IDF matcher built from a
season's references) must NOT be shared across those calls: a Season 9 title
mid-scan whose references get rebuilt to Season 8 by a concurrent thread matches
its audio against the wrong season → zero votes → bogus review.

These tests force that cross-season interleaving deterministically.
"""

import threading
from pathlib import Path

import pytest

from app.matcher.episode_identification import EpisodeMatcher


def _write_srt(path: Path, token: str) -> None:
    """Write a minimal SRT whose dialogue is dominated by a unique ``token``.

    Shared filler keeps every reference non-degenerate without overlapping the
    discriminating token, so a transcript of ``token`` matches exactly one episode
    with a clear margin.
    """
    body = (token + " ") * 40 + ("the and a of to it " * 8)
    blocks = []
    for i in range(1, 6):
        start = f"00:00:{i * 2:02d},000"
        end = f"00:00:{i * 2 + 1:02d},000"
        blocks.append(f"{i}\n{start} --> {end}\n{body}")
    path.write_text("\n\n".join(blocks), encoding="utf-8")


@pytest.fixture
def two_season_cache(tmp_path: Path) -> Path:
    """A scraping cache (data/<tmdb_id>/) with two seasons of sentinel episodes."""
    data = tmp_path / "data" / "1400"
    data.mkdir(parents=True)
    _write_srt(data / "Show - S08E01.srt", "alpha")
    _write_srt(data / "Show - S08E02.srt", "bravo")
    _write_srt(data / "Show - S09E01.srt", "charlie")
    _write_srt(data / "Show - S09E02.srt", "delta")
    return tmp_path


def test_concurrent_cross_season_match_keeps_each_thread_on_its_own_season(
    two_season_cache, monkeypatch
):
    """Two concurrent identify_episode calls for different seasons must each match
    their OWN season's references — not whichever season a sibling thread last built.

    The handshake forces the exact interleaving that corrupts the shared TF-IDF
    slot: thread A (Season 8) starts scanning, then thread B (Season 9) rebuilds
    the references mid-scan before A casts its first vote.
    """
    matcher = EpisodeMatcher(two_season_cache, "Show", expected_tmdb_id=1400, model_name="small")

    # No ffmpeg / Whisper: fixed duration, fake audio paths encoding the source,
    # and a transcript determined by which file's audio is being "heard".
    monkeypatch.setattr(
        "app.matcher.episode_identification.get_video_duration", lambda *a, **k: 1400.0
    )
    monkeypatch.setattr(
        matcher,
        "extract_audio_chunk",
        lambda video_file, start_time, duration=None: f"{video_file}|{start_time}",
    )

    a_scanning = threading.Event()  # thread A has started its scan
    b_rebuilt = threading.Event()  # thread B has rebuilt the (shared) references

    class FakeModel:
        def transcribe(self, audio_path):
            ap = str(audio_path)
            if "S08E02" in ap:  # thread A's audio -> should match S08E02
                if not a_scanning.is_set():
                    a_scanning.set()
                    # Block A mid-scan until B has rebuilt the references.
                    b_rebuilt.wait(timeout=15)
                return {"text": ("bravo " * 40)}
            if "S09E01" in ap:  # thread B's audio -> should match S09E01
                b_rebuilt.set()
                return {"text": ("charlie " * 40)}
            return {"text": ""}

    monkeypatch.setattr(
        "app.matcher.episode_identification.get_cached_model", lambda cfg: FakeModel()
    )

    results: dict[str, dict] = {}

    def run(tag, video_name, season):
        results[tag] = matcher.identify_episode(
            Path(f"/fake/{video_name}.mkv"), str(two_season_cache), season, num_points=6
        )

    thread_a = threading.Thread(target=run, args=("A", "S08E02", 8))
    thread_b = threading.Thread(target=run, args=("B", "S09E01", 9))

    thread_a.start()
    a_scanning.wait(timeout=15)  # ensure A is mid-scan before B clobbers references
    thread_b.start()
    thread_a.join(timeout=60)
    thread_b.join(timeout=60)

    # Thread A's audio is Season 8 episode 2; it must NOT be lost to a Season-9
    # reference rebuild triggered by thread B.
    assert results["A"] is not None
    assert (results["A"]["season"], results["A"]["episode"]) == (8, 2), (
        f"thread A clobbered: got {results['A'].get('season')}x{results['A'].get('episode')} "
        f"(details={results['A'].get('match_details')})"
    )
    # Thread B is the correct-season control.
    assert results["B"] is not None
    assert (results["B"]["season"], results["B"]["episode"]) == (9, 1)


def test_tfidf_cache_reuses_per_season_and_isolates_across_seasons(two_season_cache):
    """The per-signature TF-IDF cache reuses one matcher within a season (no
    rebuild churn) but hands different seasons DISTINCT matchers (no clobber)."""
    matcher = EpisodeMatcher(two_season_cache, "Show", expected_tmdb_id=1400, model_name="small")
    s8_files = matcher.get_reference_files(8)
    s9_files = matcher.get_reference_files(9)
    sig8 = ("scraping", tuple(str(rf) for rf in s8_files))
    sig9 = ("scraping", tuple(str(rf) for rf in s9_files))

    m8a = matcher._get_tfidf_matcher(sig8, using_precomputed=False, reference_files=s8_files)
    m8b = matcher._get_tfidf_matcher(sig8, using_precomputed=False, reference_files=s8_files)
    m9 = matcher._get_tfidf_matcher(sig9, using_precomputed=False, reference_files=s9_files)

    assert m8a is m8b  # same season -> cached, reused (no per-call rebuild)
    assert m8a is not m9  # different season -> isolated instances
    assert set(m8a.ref_file_order) != set(m9.ref_file_order)
