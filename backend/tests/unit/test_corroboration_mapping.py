"""The chromaprint-accepted match source maps to the corroboration contribution source."""

from app.services.matching_coordinator import _MATCH_SOURCE_TO_CONTRIB


def test_chromaprint_maps_to_corroboration():
    assert _MATCH_SOURCE_TO_CONTRIB.get("engram_chromaprint") == "engram_chromaprint_corroboration"


def test_existing_sources_unchanged():
    # Spot-check that the additive change didn't disturb existing entries.
    assert _MATCH_SOURCE_TO_CONTRIB.get("engram") == "engram_asr"
