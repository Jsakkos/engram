"""Unit tests for the GPU badge-state derivation (_gpu_state in routes)."""

from unittest.mock import patch

from app.api.routes import _gpu_state

_IDLE = {"state": "idle"}


def _state(device, detected, installed, *, supported=True, download=None):
    with patch("app.matcher.cuda_runtime.is_supported_platform", return_value=supported):
        return _gpu_state(
            device=device,
            detected=detected,
            installed=installed,
            downloading=download or _IDLE,
        )


def test_active_when_device_is_cuda():
    assert _state("cuda", True, True) == "active"


def test_downloading_overrides_everything():
    assert _state("cpu", True, False, download={"state": "downloading"}) == "downloading"
    assert _state("cpu", True, True, download={"state": "installing"}) == "installing"


def test_failed_download_surfaces_as_error():
    # gpu_state must agree with gpu_download.state so the field doesn't hide a failed download.
    assert _state("cpu", True, False, download={"state": "error"}) == "error"


def test_gpu_present_but_libs_missing():
    assert _state("cpu", detected=True, installed=False) == "available_not_installed"


def test_gpu_present_and_installed_but_disabled():
    assert _state("cpu", detected=True, installed=True) == "available_not_enabled"


def test_unsupported_os_takes_precedence_over_no_gpu():
    assert _state("cpu", detected=False, installed=False, supported=False) == "unsupported_os"


def test_supported_os_without_gpu_is_unavailable():
    assert _state("cpu", detected=False, installed=False, supported=True) == "unavailable"
