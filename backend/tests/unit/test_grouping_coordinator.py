"""Unit tests for GroupingCoordinator key derivation."""

from app.models.disc_job import ContentType, DiscJob, JobState
from app.services.grouping_coordinator import derive_group_key


def _job(**kwargs) -> DiscJob:
    """Build a DiscJob with sensible defaults for grouping tests."""
    defaults = {
        "id": 1,
        "drive_id": "E:",
        "volume_label": None,
        "content_type": ContentType.UNKNOWN,
        "state": JobState.COMPLETED,
        "tmdb_id": None,
        "detected_season": None,
    }
    defaults.update(kwargs)
    return DiscJob(**defaults)


# -----------------------------------------------------------------------------
# Label-based keys (preferred path)
# -----------------------------------------------------------------------------


def test_label_with_season_and_disc_yields_label_key():
    job = _job(volume_label="FOR_ALL_MANKIND_S1_D2", content_type=ContentType.TV)
    assert derive_group_key(job) == ("label", "FOR ALL MANKIND", 1)


def test_label_with_combined_season_disc_yields_label_key():
    job = _job(volume_label="THE_OFFICE_S01D03", content_type=ContentType.TV)
    assert derive_group_key(job) == ("label", "THE OFFICE", 1)


def test_label_key_ignores_disc_number_collision():
    # Two discs of the same season yield the same group key (disc number stripped).
    d1 = _job(volume_label="FAM_S1_D1", content_type=ContentType.TV)
    d4 = _job(volume_label="FAM_S1_D4", content_type=ContentType.TV)
    assert derive_group_key(d1) == derive_group_key(d4)


def test_label_key_distinguishes_seasons():
    s1 = _job(volume_label="FRIENDS_S1_D1", content_type=ContentType.TV)
    s2 = _job(volume_label="FRIENDS_S2_D1", content_type=ContentType.TV)
    assert derive_group_key(s1) != derive_group_key(s2)


def test_label_key_normalizes_whitespace_and_case():
    a = _job(volume_label="for_all_mankind_s1_d1", content_type=ContentType.TV)
    b = _job(volume_label="FOR_ALL_MANKIND_S1_D2", content_type=ContentType.TV)
    assert derive_group_key(a) == derive_group_key(b)


# -----------------------------------------------------------------------------
# TMDB fallback (label parse fails or returns no season)
# -----------------------------------------------------------------------------


def test_tv_without_label_falls_back_to_tmdb_tv_key():
    job = _job(
        volume_label="LOGICAL_VOLUME_ID",  # generic, parser rejects
        content_type=ContentType.TV,
        tmdb_id=87567,
        detected_season=1,
    )
    assert derive_group_key(job) == ("tmdb_tv", 87567, 1)


def test_movie_falls_back_to_tmdb_movie_key():
    job = _job(
        volume_label=None,
        content_type=ContentType.MOVIE,
        tmdb_id=27205,
    )
    assert derive_group_key(job) == ("tmdb_movie", 27205)


def test_label_parse_without_season_falls_back_to_tmdb():
    # "FIREFLY_DISC1" parses to (Firefly, None, 1) — no season → fallback.
    job = _job(
        volume_label="FIREFLY_DISC1",
        content_type=ContentType.TV,
        tmdb_id=1437,
        detected_season=1,
    )
    assert derive_group_key(job) == ("tmdb_tv", 1437, 1)


def test_label_and_tmdb_keys_never_collide():
    # Even if the values overlap by coincidence, the source tag distinguishes them.
    label_keyed = _job(volume_label="SHOW_S1_D1", content_type=ContentType.TV)
    tmdb_keyed = _job(content_type=ContentType.TV, tmdb_id=1, detected_season=1)
    assert derive_group_key(label_keyed) != derive_group_key(tmdb_keyed)


# -----------------------------------------------------------------------------
# No signal at all → solo group (None)
# -----------------------------------------------------------------------------


def test_no_label_no_tmdb_yields_none():
    job = _job(volume_label=None, content_type=ContentType.UNKNOWN, tmdb_id=None)
    assert derive_group_key(job) is None


def test_generic_label_no_tmdb_yields_none():
    job = _job(volume_label="LOGICAL_VOLUME_ID", content_type=ContentType.UNKNOWN, tmdb_id=None)
    assert derive_group_key(job) is None


def test_unknown_content_type_with_tmdb_yields_none():
    # Content type must be set to TV or MOVIE for TMDB fallback to fire.
    job = _job(content_type=ContentType.UNKNOWN, tmdb_id=42, detected_season=1)
    assert derive_group_key(job) is None
