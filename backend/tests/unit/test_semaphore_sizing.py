"""The match semaphore is sized to resolved ASR workers, not the raw config value."""

from unittest.mock import patch

from app.matcher.asr_models import resolve_asr_runtime
from app.services.matching_coordinator import MatchingCoordinator


def test_semaphore_value_equals_resolved_workers_cpu():
    # 16 cores, requested 4 -> 4 workers -> 4 admission slots.
    with patch("app.matcher.asr_models.psutil.cpu_count", return_value=16):
        runtime = resolve_asr_runtime("cpu", requested_workers=4)
    coord = MatchingCoordinator.__new__(MatchingCoordinator)  # no full __init__ needed
    coord._match_semaphore = None
    coord.init_semaphore(runtime.workers)
    assert coord._match_semaphore._value == 4


def test_semaphore_clamped_when_request_exceeds_cores():
    with patch("app.matcher.asr_models.psutil.cpu_count", return_value=8):
        runtime = resolve_asr_runtime("cpu", requested_workers=32)
    coord = MatchingCoordinator.__new__(MatchingCoordinator)
    coord._match_semaphore = None
    coord.init_semaphore(runtime.workers)
    assert coord._match_semaphore._value == 8  # clamped to cores
