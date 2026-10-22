"""A segmented, half-hour-numbered season is never gap-healed or grafted (#760).

Job 100's log showed the heal at work on Hey Arnold! S2: "missing 17 of 37
episode(s): S02E21 ... fetching" from Addic7ed. Diffing a 20-entry half-hour
corpus against a 37-entry segment roster yields a meaningless gap list, and the
scrapers that would fill it index the show by half-hour too, so whatever they
return lands in data/ under the wrong TMDB code and is then grafted into the vote.

Only a season the pack marks ``segmented`` qualifies. ``divergent`` alone also
describes an ordinary season the pack is merely missing a few episodes of, which
is exactly what the heal exists for.
"""

import json
from unittest.mock import patch

import numpy as np
import pytest
from scipy import sparse

from app.matcher import testing_service as ts
from app.matcher.episode_identification import EpisodeMatcher, reference_coverage
from app.matcher.vectorizer_config import (
    CACHE_FORMAT_VERSION,
    build_hashing_vectorizer,
    compute_idf,
    vectorizer_config_hash,
)

TEXTS = [
    "detective solves the murder in the old mansion at midnight",
    "the spaceship crew explores a distant alien planet far from earth",
]


def _write_pack(tmp_path, marker, codes=("S02E01", "S02E02")):
    precomputed = tmp_path / "precomputed"
    show_dir = precomputed / "537"
    show_dir.mkdir(parents=True)
    counts = build_hashing_vectorizer().transform(TEXTS[: len(codes)])
    np.save(precomputed / "idf.npy", compute_idf(counts))
    u16 = sparse.csr_matrix(
        (counts.data.astype(np.uint16), counts.indices, counts.indptr), shape=counts.shape
    )
    sparse.save_npz(show_dir / "S02.npz", u16)
    (show_dir / "S02.index.json").write_text(json.dumps(list(codes)))
    entry = {"tmdb_id": 537, "name": "Hey Arnold!", "seasons": [2], "episode_counts": {}}
    if marker is not None:
        entry["season_numbering"] = {"2": marker}
    (precomputed / "manifest.json").write_text(
        json.dumps(
            {
                "cache_format_version": CACHE_FORMAT_VERSION,
                "vectorizer_config_hash": vectorizer_config_hash(),
                "shows": {"537": entry},
            }
        )
    )


DIVERGENT = {"scheme": "divergent", "roster_size": 37}
SEGMENTED = {"scheme": "divergent", "roster_size": 37, "segmented": True}
CROSSWALKED = {
    "scheme": "divergent",
    "roster_size": 37,
    "segmented": True,
    "crosswalk": {"S02E01": "S02E01-E02", "S02E02": "S02E03-E04"},
}
AIRED = {"scheme": "tmdb_aired", "roster_size": 2}


def _write_stale_scraped_srt(tmp_path, code="S02E21"):
    data_dir = tmp_path / "data" / "537"
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / f"Hey Arnold! - {code}.srt").write_text(
        "1\n00:00:01,000 --> 00:00:30,000\na chef cooks pasta in a small kitchen\n",
        encoding="utf-8",
    )


# --------------------------------------------------------------------------- heal


def test_skip_result_records_that_the_season_is_segmented(tmp_path):
    _write_pack(tmp_path, CROSSWALKED)
    skip = ts._precomputed_skip_result(tmp_path, "Hey Arnold!", 2, expected_tmdb_id=537)
    assert skip["segmented"] is True
    assert [e["code"] for e in skip["episodes"]] == ["S02E01-E02", "S02E03-E04"]


def test_heal_skipped_for_segmented_season(tmp_path):
    _write_pack(tmp_path, SEGMENTED)
    skip = ts._precomputed_skip_result(tmp_path, "Hey Arnold!", 2, expected_tmdb_id=537)
    with (
        patch.object(ts, "fetch_season_details", return_value=37),
        patch.object(ts, "_fetch_episodes") as mock_fetch,
    ):
        healed = ts._heal_precomputed_gaps(
            skip, "Hey Arnold!", 2, tmdb_id=537, cache_path=tmp_path, config=None
        )
    assert healed == skip
    mock_fetch.assert_not_called()


@pytest.mark.parametrize("marker", [AIRED, DIVERGENT], ids=["aired", "incomplete"])
def test_heal_runs_for_a_season_that_is_not_segmented(tmp_path, marker):
    # DIVERGENT without "segmented" is an ordinary season the pack is missing
    # episodes of: the Mad Men S02E05 case the heal was written for.
    _write_pack(tmp_path, marker)
    skip = ts._precomputed_skip_result(tmp_path, "Hey Arnold!", 2, expected_tmdb_id=537)
    fetched = {3: {"code": "S02E03", "status": "not_found", "path": None, "source": None}}
    with (
        patch.object(ts, "fetch_season_details", return_value=3),
        patch.object(ts, "_fetch_episodes", return_value=(fetched, [])) as mock_fetch,
    ):
        ts._heal_precomputed_gaps(
            skip, "Hey Arnold!", 2, tmdb_id=537, cache_path=tmp_path, config=None
        )
    mock_fetch.assert_called_once()
    assert list(mock_fetch.call_args.args[3]) == [3]


# --------------------------------------------------------------------------- augment


def test_augment_does_not_graft_into_divergent_season(tmp_path):
    _write_pack(tmp_path, CROSSWALKED)
    _write_stale_scraped_srt(tmp_path)
    matcher = EpisodeMatcher(cache_dir=tmp_path, show_name="Hey Arnold!", expected_tmdb_id=537)
    _matrix, codes, _idf = matcher._load_precomputed_season(2)
    assert codes == ["S02E01-E02", "S02E03-E04"]


def test_augment_does_not_graft_into_uncrosswalked_segmented_season(tmp_path):
    _write_pack(tmp_path, SEGMENTED)
    _write_stale_scraped_srt(tmp_path)
    matcher = EpisodeMatcher(cache_dir=tmp_path, show_name="Hey Arnold!", expected_tmdb_id=537)
    _matrix, codes, _idf = matcher._load_precomputed_season(2)
    assert codes == ["S02E01", "S02E02"]


@pytest.mark.parametrize("marker", [AIRED, DIVERGENT], ids=["aired", "incomplete"])
def test_augment_grafts_into_a_season_that_is_not_segmented(tmp_path, marker):
    _write_pack(tmp_path, marker)
    _write_stale_scraped_srt(tmp_path, code="S02E03")
    matcher = EpisodeMatcher(cache_dir=tmp_path, show_name="Hey Arnold!", expected_tmdb_id=537)
    _matrix, codes, _idf = matcher._load_precomputed_season(2)
    assert codes == ["S02E01", "S02E02", "S02E03"]


# --------------------------------------------------------------------------- coverage


def test_reference_coverage_expands_combined_codes(tmp_path):
    _write_pack(tmp_path, CROSSWALKED)
    cov = reference_coverage(tmp_path, 537, "Hey Arnold!", 2, [1, 2, 3, 4, 5])
    assert cov == {
        "S02E01": "precomputed",
        "S02E02": "precomputed",
        "S02E03": "precomputed",
        "S02E04": "precomputed",
        "S02E05": "missing",
    }


def test_reference_coverage_ignores_scraped_srts_in_a_segmented_season(tmp_path):
    # They are never grafted into the vote, so the review page must not call
    # their episodes covered.
    _write_pack(tmp_path, SEGMENTED)
    _write_stale_scraped_srt(tmp_path, code="S02E21")
    cov = reference_coverage(tmp_path, 537, "Hey Arnold!", 2, [21])
    assert cov == {"S02E21": "missing"}
