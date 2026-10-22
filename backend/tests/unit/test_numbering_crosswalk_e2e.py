"""Hey Arnold! S2 Disc 2, from published pack to library filename (#760).

The reporter's disc held five half-hour tracks. Engram matched them to the pack's
half-hour references S02E06..S02E10 and filed them under those codes, which TMDB
uses for unrelated segments. With the builder's crosswalk in the manifest, the
same votes must come out as the TMDB segments each half-hour holds, and the
organizer must name the files with the range form.
"""

import json
from pathlib import Path

import numpy as np
from scipy import sparse

from app.core.organizer import organize_tv_episode
from app.matcher.episode_identification import EpisodeMatcher, TfidfMatcher, stamp_numbering
from app.matcher.numbering_crosswalk import RosterEpisode, derive_crosswalk
from app.matcher.vectorizer_config import (
    CACHE_FORMAT_VERSION,
    build_hashing_vectorizer,
    compute_idf,
    vectorizer_config_hash,
)

FIXTURE = Path(__file__).parent.parent / "fixtures" / "numbering_crosswalk" / "hey_arnold_s2.json"


def _dialogue(n: int) -> str:
    """Distinct stand-in dialogue for corpus half-hour ``n``."""
    return " ".join(f"halfhour{n}word{j}" for j in range(40))


def _publish_pack(cache_dir: Path) -> None:
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    codes = data["corpus_codes"]
    roster = [RosterEpisode(**e) for e in data["roster"]]
    releases = {int(k): v for k, v in data["releases"].items()}
    crosswalk = derive_crosswalk(2, codes, roster, releases)

    precomputed = cache_dir / "precomputed"
    show_dir = precomputed / "537"
    show_dir.mkdir(parents=True)
    counts = build_hashing_vectorizer().transform([_dialogue(n) for n in range(1, 21)])
    np.save(precomputed / "idf.npy", compute_idf(counts))
    sparse.save_npz(
        show_dir / "S02.npz",
        sparse.csr_matrix(
            (counts.data.astype(np.uint16), counts.indices, counts.indptr), shape=counts.shape
        ),
    )
    (show_dir / "S02.index.json").write_text(json.dumps(codes))
    (precomputed / "manifest.json").write_text(
        json.dumps(
            {
                "cache_format_version": CACHE_FORMAT_VERSION,
                "vectorizer_config_hash": vectorizer_config_hash(),
                "shows": {
                    "537": {
                        "tmdb_id": 537,
                        "name": "Hey Arnold!",
                        "seasons": [2],
                        "episode_counts": {"2": 20},
                        "season_numbering": {
                            "2": {
                                "scheme": "divergent",
                                "roster_size": 37,
                                "segmented": True,
                                "crosswalk": crosswalk.mapping,
                                "crosswalk_sources": crosswalk.sources,
                            }
                        },
                    }
                },
            }
        )
    )


def test_hey_arnold_s2_disc2_files_segment_ranges(tmp_path):
    _publish_pack(tmp_path)
    matcher = EpisodeMatcher(cache_dir=tmp_path, show_name="Hey Arnold!", expected_tmdb_id=537)
    ref_matrix, codes, idf = matcher._load_precomputed_season(2)
    tfidf = TfidfMatcher()
    tfidf.load_precomputed(ref_matrix, codes, idf)

    # Disc tracks 1-5 hold half-hours 6-10 (what the matcher voted for in job 100).
    top = [tfidf.match(_dialogue(n))[0][0] for n in range(6, 11)]
    assert top == ["S02E11-E12", "S02E13", "S02E14-E15", "S02E16-E17", "S02E18-E19"]

    stats: dict = {}
    stamp_numbering(stats, matcher.precomputed_numbering(2))
    assert stats["numbering_scheme"] == "tmdb_aired"
    assert stats["numbering_crosswalk"] is True

    source = tmp_path / "C1_t00.mkv"
    source.write_bytes(b"x" * 1024)
    result = organize_tv_episode(
        source_file=source,
        show_name="Hey Arnold!",
        episode_code=top[0],
        library_path=tmp_path / "library",
    )
    assert result["success"] is True
    assert result["final_path"].name == "Hey Arnold! - S02E11-E12.mkv"
    assert result["final_path"].parent.name == "Season 02"
