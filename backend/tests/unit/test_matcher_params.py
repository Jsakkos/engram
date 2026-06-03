"""The deep re-match path passes denser-sampling / higher-vote overrides down
to the matcher. This locks the curator -> identify_episode plumbing so a future
refactor can't silently drop the kwargs."""

from pathlib import Path
from unittest.mock import Mock

import pytest

from app.core.curator import EpisodeCurator


@pytest.mark.unit
async def test_match_single_file_forwards_overrides_to_matcher():
    curator = EpisodeCurator()
    curator._matcher = Mock()
    curator._matcher.identify_episode.return_value = {
        "season": 1,
        "episode": 5,
        "confidence": 0.9,
        "match_details": {"vote_count": 6},
        "runner_ups": [],
    }
    curator._initialized = True
    curator._current_show = "Some Show"
    curator._cache_dir = Path("/tmp/cache")

    await curator.match_single_file(
        Path("/tmp/title.mkv"),
        series_name="Some Show",
        season=1,
        num_points=25,
        min_vote_count=4,
    )

    curator._matcher.identify_episode.assert_called_once()
    args = curator._matcher.identify_episode.call_args.args
    # (video_file, temp_dir, season, progress_callback, num_points, min_vote_count)
    assert args[2] == 1
    assert args[4] == 25
    assert args[5] == 4


@pytest.mark.unit
def test_confidence_accept_floor_default_and_override(tmp_path):
    """The ranked-voting gate accepts a decisive match on calibrated confidence
    once it clears confidence_accept_floor. Lock the default (0.70, mirroring the
    curator's auto-organize gate) and that it stays overridable."""
    from app.matcher.episode_identification import CONFIDENCE_ACCEPT_FLOOR, EpisodeMatcher

    assert CONFIDENCE_ACCEPT_FLOOR == 0.70

    default = EpisodeMatcher(cache_dir=tmp_path, show_name="X", model_name="tiny")
    assert default.confidence_accept_floor == CONFIDENCE_ACCEPT_FLOOR

    overridden = EpisodeMatcher(
        cache_dir=tmp_path, show_name="X", model_name="tiny", confidence_accept_floor=0.55
    )
    assert overridden.confidence_accept_floor == 0.55
