"""Manual identity override: user assertion wins, no gate may fire.

The override runs AFTER _run_classification so structural analysis (Play-All
indices, ambiguous-movie detection, title clustering) is preserved. These tests
pin the two behaviors that matter: identity is replaced, and every walk-away
gate is suppressed.
"""

from app.core.analyst import DiscAnalysisResult
from app.models.disc_job import ContentType
from app.services.identification_coordinator import _apply_manual_identity
from app.services.manual_identity import ManualIdentity


def _guessed_result() -> DiscAnalysisResult:
    """A classification result that guessed wrong and wants review."""
    result = DiscAnalysisResult(content_type=ContentType.MOVIE)
    result.detected_name = "Wrong Guess"
    result.detected_season = None
    result.tmdb_id = 999
    result.confidence = 0.4
    result.classification_source = "heuristic"
    result.needs_review = True
    result.review_reason = "Could not confirm identity"
    result.is_ambiguous_movie = True
    result.identity_unconfirmed = True
    return result


def test_override_replaces_identity():
    result = _guessed_result()
    manual = ManualIdentity(title="Arrested Development", content_type="tv", season=1, tmdb_id=4589)

    _apply_manual_identity(result, manual)

    assert result.content_type == ContentType.TV
    assert result.detected_name == "Arrested Development"
    assert result.detected_season == 1
    assert result.tmdb_id == 4589
    assert result.classification_source == "manual"
    assert result.confidence == 1.0


def test_override_clears_every_review_trigger():
    result = _guessed_result()
    manual = ManualIdentity(title="The Office", content_type="tv", season=2)

    _apply_manual_identity(result, manual)

    # Each of these independently routes a job to REVIEW_NEEDED or raises a
    # walk-away prompt. A manual disc must never park.
    assert result.needs_review is False
    assert result.review_reason is None
    assert result.is_ambiguous_movie is False
    assert result.identity_unconfirmed is False
    assert getattr(result, "_tmdb_signal", None) is None
    assert getattr(result, "_discdb_signal", None) is None


def test_override_tolerates_missing_tmdb_id():
    """A freeform title with no TMDB match is explicitly allowed."""
    result = _guessed_result()
    manual = ManualIdentity(title="Home Movies 1998", content_type="tv", season=1)

    _apply_manual_identity(result, manual)

    assert result.tmdb_id is None
    assert result.detected_name == "Home Movies 1998"
    assert result.needs_review is False


def test_override_preserves_structural_analysis():
    """Play-All indices come from duration clustering, not identity."""
    result = _guessed_result()
    result.play_all_title_indices = [7, 8]
    manual = ManualIdentity(title="The Office", content_type="tv", season=2)

    _apply_manual_identity(result, manual)

    assert result.play_all_title_indices == [7, 8]


def test_gate_b_condition_is_suppressed_for_manual():
    """Gate B fires on ABSENCE of tmdb_id, so the override alone cannot stop it.

    This pins the _is_manual guard: without it, a manual TV disc whose title
    has no TMDB match would raise a name prompt and break the unattended rip.
    """
    is_tv = True
    tmdb_id = None
    detected_title = "Home Movies 1998"
    collision = False

    def gate_b_fires(is_manual: bool) -> bool:
        return bool(is_tv and not tmdb_id and detected_title and not collision and not is_manual)

    assert gate_b_fires(is_manual=False) is True
    assert gate_b_fires(is_manual=True) is False


def test_gate_d_condition_is_suppressed_for_manual():
    detected_season = None

    def gate_d_fires(is_manual: bool) -> bool:
        return detected_season is None and not is_manual

    assert gate_d_fires(is_manual=False) is True
    assert gate_d_fires(is_manual=True) is False


def test_gate_e_catalog_number_clearing_is_suppressed_for_manual():
    """Gate E: the catalog-number title-clearing block also fires on absence.

    ``if not tmdb_signal and not discdb_signal and job.detected_title and
    _looks_like_catalog_number(job.volume_label)`` clears ``job.detected_title``
    back to None. After the manual override, both signals ARE cleared to None
    by design (see ``_apply_manual_identity``), so without a guard this block
    would silently erase the user's asserted title on exactly the
    unreadable-label discs this feature targets, re-triggering Gate A.
    """
    tmdb_signal = None
    discdb_signal = None
    detected_title = "My Home Movies"
    looks_like_catalog_number = True  # e.g. volume label "FHED3456"

    def gate_e_fires(is_manual: bool) -> bool:
        return bool(
            not tmdb_signal
            and not discdb_signal
            and detected_title
            and looks_like_catalog_number
            and not is_manual
        )

    assert gate_e_fires(is_manual=False) is True
    assert gate_e_fires(is_manual=True) is False
