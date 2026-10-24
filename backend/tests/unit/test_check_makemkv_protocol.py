"""Unit tests for scripts/check_makemkv_protocol.py.

The script reads a MakeMKV release's apdefs.h and fails when a robot-mode id
Engram parses has moved. The `mkp` fixture lives in conftest.py.
"""

from app.core.extractor import _SINFO_AUDIO, _SINFO_SUBTITLES

# Shaped like makemkv-oss-2.0.0/makemkvgui/inc/lgpl/apdefs.h, trimmed to the
# definitions the checker reads plus a few neighbours it must ignore.
HEADER_2_0_0 = """
typedef enum _AP_ItemAttributeId
{
  ap_iaUnknown=0,
  ap_iaType=1,
  ap_iaName=2,
  ap_iaLangCode=3,
  ap_iaLangName=4,
  ap_iaChapterCount=8,
  ap_iaDuration=9,
  ap_iaDiskSize=10,
  ap_iaDiskSizeBytes=11,
  ap_iaSourceFileName=16,
  ap_iaVideoSize=19,
  ap_iaSegmentsCount=25,
  ap_iaSegmentsMap=26,
  ap_iaOutputFileName=27,
  ap_iaMetadataLanguageCode=28,
  ap_iaMaxValue
} AP_ItemAttributeId;
static const unsigned long APP_TTREE_VIDEO=6201;
static const unsigned long APP_TTREE_AUDIO=6202;
static const unsigned long APP_TTREE_SUBPICTURE=6203;
"""


def test_current_header_has_no_drift(mkp):
    assert mkp.find_drift(HEADER_2_0_0) == []


def test_moved_attribute_id_is_reported(mkp):
    moved = HEADER_2_0_0.replace("ap_iaDuration=9,", "ap_iaDuration=90,")
    assert mkp.find_drift(moved) == ["ap_iaDuration: Engram expects 9, header defines 90"]


def test_removed_definition_is_reported(mkp):
    removed = HEADER_2_0_0.replace("  ap_iaSegmentsMap=26,\n", "")
    assert mkp.find_drift(removed) == ["ap_iaSegmentsMap: Engram expects 26, header defines none"]


def test_stream_type_codes_match_the_extractor(mkp):
    # The extractor keys SINFO stream types on these codes; the checker must
    # guard the same values, not its own copy of them.
    assert mkp.EXPECTED["APP_TTREE_AUDIO"] == int(_SINFO_AUDIO)
    assert mkp.EXPECTED["APP_TTREE_SUBPICTURE"] == int(_SINFO_SUBTITLES)


def test_main_exit_codes(mkp, tmp_path):
    good = tmp_path / "good.h"
    good.write_text(HEADER_2_0_0, encoding="utf-8")
    bad = tmp_path / "bad.h"
    bad.write_text(HEADER_2_0_0.replace("ap_iaName=2,", "ap_iaName=3,"), encoding="utf-8")

    assert mkp.main([str(good)]) == 0
    assert mkp.main([str(bad)]) == 1
    assert mkp.main([]) == 2
