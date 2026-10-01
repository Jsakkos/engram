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


# --- enabled-but-not-on-GPU states (#694) -------------------------------------------------
# Before these, an enabled flag was invisible to the UI: "enabled, restart pending" and
# "enabled, but the libraries failed to load" both rendered as "Enable GPU acceleration",
# which read as the setting silently resetting itself.


def _enabled_state(device, detected, installed, *, fallback=None, download=None):
    with patch("app.matcher.cuda_runtime.is_supported_platform", return_value=True):
        return _gpu_state(
            device=device,
            detected=detected,
            installed=installed,
            downloading=download or _IDLE,
            enabled=True,
            fallback_reason=fallback,
        )


def test_enabled_since_startup_with_libs_is_restart_pending():
    assert _enabled_state("cpu", True, True) == "restart_pending"


def test_enabled_but_libs_failed_to_load_at_startup():
    assert _enabled_state("cpu", True, True, fallback="register_failed") == "enabled_not_active"


def test_enabled_but_libs_missing_at_startup():
    assert _enabled_state("cpu", True, False, fallback="runtime_missing") == "enabled_not_active"


def test_missing_runtime_downloaded_in_session_becomes_restart_pending():
    # Startup pinned "runtime_missing"; the user then re-downloads from the panel. Once the
    # libs are on disk the stale reason must not keep reporting a failure.
    assert _enabled_state("cpu", True, True, fallback="runtime_missing") == "restart_pending"


def test_enabled_without_libs_and_no_startup_attempt_offers_download():
    # Flag on but libs gone mid-run: the only useful action is the download.
    assert _enabled_state("cpu", True, False) == "available_not_installed"


def test_enabled_and_running_on_gpu_is_active():
    assert _enabled_state("cuda", True, True) == "active"


def test_download_progress_still_wins_over_enabled_states():
    assert (
        _enabled_state("cpu", True, False, fallback="runtime_missing", download={"state": "error"})
        == "error"
    )


def test_set_asr_device_records_and_clears_fallback_reason():
    from app.matcher import asr_models

    try:
        asr_models.set_asr_device("cpu", gpu_fallback_reason="register_failed")
        assert asr_models.gpu_fallback_reason() == "register_failed"
        asr_models.set_asr_device("cpu")
        assert asr_models.gpu_fallback_reason() is None
    finally:
        asr_models.set_asr_device(None)
