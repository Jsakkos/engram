"""Engram's robot-mode parsers against real MakeMKV 2.0.0 output.

The fixture is verbatim ``makemkvcon -r --cache=1 info disc:9999`` output from
MakeMKV 2.0.0, compiled by docker/install-makemkv.sh in the nightly "MakeMKV
install check" workflow (ubuntu-26.04 and the Docker image's bookworm base gave
byte-identical output). The runner has no optical drive, so it covers the
startup banner, drive enumeration and the open-failure path, not a disc scan;
TINFO/SINFO/CINFO ids are guarded separately by scripts/check_makemkv_protocol.py.
"""

from pathlib import Path

import pytest

from app.api.validation import _extract_makemkv_version
from app.core.disc_source import parse_drive_listing
from app.core.extractor import (
    MakeMKVExtractor,
    _is_region_mismatch,
    _last_msg_text,
    detect_license_failure,
)

FIXTURE = Path(__file__).parent.parent / "fixtures" / "makemkv_robot"


@pytest.fixture(scope="module")
def no_drive_output() -> str:
    return (FIXTURE / "v2.0.0_linux_info_no_drive.txt").read_text(encoding="utf-8")


def test_version_banner_is_read(no_drive_output):
    assert _extract_makemkv_version(no_drive_output) == "MakeMKV v2.0.0 linux(x64-release)"


def test_empty_drive_slots_map_to_nothing(no_drive_output):
    # 2.0 still prints all 16 slots (AP_MaxCdromDevices) in the 7-field DRV form;
    # empty device strings must be dropped, not mapped to disc indices.
    drv_lines = [ln for ln in no_drive_output.splitlines() if ln.startswith("DRV:")]
    assert len(drv_lines) == 16
    assert parse_drive_listing(no_drive_output) == {}


def test_parse_drive_listing_reads_a_2x_style_populated_row(no_drive_output):
    # Same field layout as the empty rows above, with a drive present.
    output = no_drive_output.replace(
        'DRV:0,256,999,0,"","",""',
        'DRV:0,2,999,12,"BD-RE HL-DT-ST BD-RE  WH16NS60 1.02","SHOW_S1D1","/dev/sr0"',
    )
    assert parse_drive_listing(output) == {"/dev/sr0": 0}


def test_no_drive_output_is_not_a_licence_or_region_failure(no_drive_output):
    # MSG:5074 (update check) and MSG:5042 (no drives) must not trip either
    # detector; a false licence error would block every rip.
    assert detect_license_failure(no_drive_output) is None
    assert not any(_is_region_mismatch(ln) for ln in no_drive_output.splitlines())


def test_last_message_is_the_open_failure(no_drive_output):
    assert _last_msg_text(no_drive_output) == "Failed to open disc"


def test_scan_parser_yields_no_titles(no_drive_output):
    titles, disc_name = MakeMKVExtractor(makemkv_path=Path("mmk"))._parse_disc_info(no_drive_output)
    assert titles == []
    assert disc_name == ""
