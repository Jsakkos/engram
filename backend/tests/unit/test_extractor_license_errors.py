"""MakeMKV licence failures (expired beta key / too-old beta build) are named, not generic.

The fixtures are robot-mode (``-r``) output shaped like what makemkvcon prints when
its licence check refuses to open a disc. They are reconstructed from the MakeMKV
forum reports cited in #714, not captured from a live run: if a real capture shows
different wording, update ``_LICENSE_PATTERNS`` in ``app/core/extractor.py`` and the
fixtures here together.
"""

import asyncio
from pathlib import Path
from unittest.mock import patch

import pytest

from app.core.errors import MakeMKVError, MakeMKVLicenseError
from app.core.extractor import (
    LICENSE_KEY_EXPIRED_MESSAGE,
    LICENSE_VERSION_EXPIRED_MESSAGE,
    MakeMKVExtractor,
    detect_license_failure,
)

KEY_EXPIRED = (
    'MSG:1005,0,1,"MakeMKV v1.17.8 win(x64-release) started","%1 started","MakeMKV v1.17.8"\n'
    'MSG:5021,516,1,"Evaluation period has expired. Please purchase the license key to '
    'continue using the program.","%1","x"\n'
    'MSG:5010,0,0,"Failed to open disc","Failed to open disc"\n'
)
VERSION_EXPIRED = (
    'MSG:1005,0,1,"MakeMKV v1.16.1 win(x64-release) started","%1 started","MakeMKV v1.16.1"\n'
    'MSG:5021,516,1,"This application version is too old. Please download the latest '
    "version at https://www.makemkv.com/download/ or enter a registration key to continue "
    'using the program.","%1","x"\n'
)
ORDINARY_FAILURE = (
    'MSG:1005,0,1,"MakeMKV v1.17.8 win(x64-release) started","%1 started","MakeMKV v1.17.8"\n'
    "MSG:2003,0,2,\"Error 'Scsi error - MEDIUM ERROR:UNRECOVERED READ ERROR' occurred\","
    '"Error \'%1\' occurred","x"\n'
    'MSG:5010,0,0,"Failed to open disc","Failed to open disc"\n'
)


def test_detects_expired_key():
    assert detect_license_failure(KEY_EXPIRED) == LICENSE_KEY_EXPIRED_MESSAGE


def test_detects_expired_version():
    assert detect_license_failure(VERSION_EXPIRED) == LICENSE_VERSION_EXPIRED_MESSAGE


def test_ordinary_failure_stays_generic():
    assert detect_license_failure(ORDINARY_FAILURE) is None


def test_ignores_non_msg_lines():
    # Title text on a disc must not be able to trigger a licence diagnosis.
    assert detect_license_failure('TINFO:0,2,0,"This application version is too old"\n') is None


def test_messages_are_actionable():
    assert "forum" in LICENSE_KEY_EXPIRED_MESSAGE.lower()
    assert "Settings" in LICENSE_KEY_EXPIRED_MESSAGE
    assert "update" in LICENSE_VERSION_EXPIRED_MESSAGE.lower()
    assert "—" not in LICENSE_KEY_EXPIRED_MESSAGE + LICENSE_VERSION_EXPIRED_MESSAGE


def test_license_error_is_a_makemkv_error():
    assert issubclass(MakeMKVLicenseError, MakeMKVError)


def _scan(stdout: str, returncode: int):
    ext = MakeMKVExtractor(Path("makemkvcon64.exe"))
    with patch("app.core.extractor.subprocess.Popen") as popen:
        popen.return_value.communicate.return_value = (stdout, "")
        popen.return_value.returncode = returncode
        return asyncio.run(ext.scan_disc("E:"))


@pytest.mark.parametrize(
    "output,message",
    [
        (KEY_EXPIRED, LICENSE_KEY_EXPIRED_MESSAGE),
        (VERSION_EXPIRED, LICENSE_VERSION_EXPIRED_MESSAGE),
    ],
)
def test_scan_raises_named_error(output, message):
    with pytest.raises(MakeMKVLicenseError) as exc:
        _scan(output, returncode=1)
    assert str(exc.value) == message


def test_scan_ordinary_failure_still_returns_empty():
    assert _scan(ORDINARY_FAILURE, returncode=1) == ([], "")
