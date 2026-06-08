"""Unit tests for ASR runtime sizing (resolve_asr_runtime / detect_asr_device)."""

import threading
from unittest.mock import patch

import pytest

from app.matcher.asr_models import (
    GPU_WORKER_CAP,
    AsrRuntime,
    FasterWhisperModel,
    _model_cache,
    cuda_compute_type,
    detect_asr_device,
    get_cached_model,
    gpu_detected,
    resolve_asr_runtime,
    set_asr_device,
)


@pytest.fixture(autouse=True)
def _reset_asr_device_override():
    """The effective-device override is a module global; reset it around every test."""
    set_asr_device(None)
    yield
    set_asr_device(None)


class TestDetectAsrDevice:
    def test_returns_cuda_when_device_present(self):
        with patch("app.matcher.asr_models.ctranslate2.get_cuda_device_count", return_value=1):
            assert detect_asr_device() == "cuda"

    def test_returns_cpu_when_no_device(self):
        with patch("app.matcher.asr_models.ctranslate2.get_cuda_device_count", return_value=0):
            assert detect_asr_device() == "cpu"

    def test_returns_cpu_when_probe_raises(self):
        with patch(
            "app.matcher.asr_models.ctranslate2.get_cuda_device_count",
            side_effect=RuntimeError("no cuda runtime"),
        ):
            assert detect_asr_device() == "cpu"


class TestEffectiveDeviceOverride:
    """The honesty fix: detect_asr_device reports the *usable* device, not just the hardware."""

    def test_override_wins_over_probe(self):
        # A GPU is visible, but the startup decision pinned CPU (e.g. user hasn't enabled it
        # or the CUDA libs aren't installed) — the badge/semaphore must read CPU.
        with patch("app.matcher.asr_models.ctranslate2.get_cuda_device_count", return_value=1):
            assert gpu_detected() is True
            set_asr_device("cpu")
            assert detect_asr_device() == "cpu"

    def test_override_can_pin_cuda(self):
        set_asr_device("cuda")
        assert detect_asr_device() == "cuda"

    def test_gpu_detected_ignores_override(self):
        set_asr_device("cpu")
        with patch("app.matcher.asr_models.ctranslate2.get_cuda_device_count", return_value=2):
            assert gpu_detected() is True


class TestCudaComputeType:
    """Older GPUs (no Compute Capability >= 7.0) can't do float16 — pick a supported type."""

    def test_prefers_float16_when_supported(self):
        with patch(
            "app.matcher.asr_models.ctranslate2.get_supported_compute_types",
            return_value={"int8", "int8_float16", "float16", "float32"},
        ):
            assert cuda_compute_type() == "float16"

    def test_falls_back_to_int8_float16_on_pascal(self):
        with patch(
            "app.matcher.asr_models.ctranslate2.get_supported_compute_types",
            return_value={"int8", "int8_float16", "float32"},
        ):
            assert cuda_compute_type() == "int8_float16"

    def test_falls_back_to_float16_when_probe_unavailable(self):
        with patch(
            "app.matcher.asr_models.ctranslate2.get_supported_compute_types",
            side_effect=RuntimeError("no cuda"),
        ):
            assert cuda_compute_type() == "float16"


class TestResolveAsrRuntimeCpu:
    def test_divides_threads_so_total_matches_cores(self):
        with patch("app.matcher.asr_models.psutil.cpu_count", return_value=16):
            rt = resolve_asr_runtime("cpu", requested_workers=4)
        assert rt == AsrRuntime(device="cpu", compute_type="int8", workers=4, cpu_threads=4)

    def test_clamps_workers_to_physical_cores(self):
        with patch("app.matcher.asr_models.psutil.cpu_count", return_value=8):
            rt = resolve_asr_runtime("cpu", requested_workers=32)
        assert rt.workers == 8
        assert rt.cpu_threads == 1  # 8 // 8

    def test_floor_division_never_yields_zero_threads(self):
        with patch("app.matcher.asr_models.psutil.cpu_count", return_value=6):
            rt = resolve_asr_runtime("cpu", requested_workers=6)
        assert rt.workers == 6
        assert rt.cpu_threads == 1  # max(1, 6 // 6)

    def test_requested_below_one_becomes_one(self):
        with patch("app.matcher.asr_models.psutil.cpu_count", return_value=8):
            rt = resolve_asr_runtime("cpu", requested_workers=0)
        assert rt.workers == 1
        assert rt.cpu_threads == 8

    def test_missing_core_count_falls_back_to_one(self):
        with patch("app.matcher.asr_models.psutil.cpu_count", return_value=None):
            rt = resolve_asr_runtime("cpu", requested_workers=4)
        assert rt.workers == 1
        assert rt.cpu_threads == 1


class TestResolveAsrRuntimeGpu:
    @pytest.fixture(autouse=True)
    def _stub_supported_compute_types(self):
        # resolve_asr_runtime("cuda") asks ctranslate2 which compute types the GPU supports.
        # On a CPU-only runner that probe returns a set without float16 (→ float32), so pin a
        # float16-capable set to keep these worker-sizing tests hardware-independent.
        with patch(
            "app.matcher.asr_models.ctranslate2.get_supported_compute_types",
            return_value={"float16", "int8_float16", "float32"},
        ):
            yield

    def test_caps_workers_at_gpu_cap(self):
        rt = resolve_asr_runtime("cuda", requested_workers=8)
        assert rt == AsrRuntime(
            device="cuda", compute_type="float16", workers=GPU_WORKER_CAP, cpu_threads=None
        )

    def test_below_cap_is_passed_through(self):
        rt = resolve_asr_runtime("cuda", requested_workers=2)
        assert rt.workers == 2
        assert rt.cpu_threads is None
        assert rt.compute_type == "float16"


class TestModelConstruction:
    """FasterWhisperModel must pass num_workers/cpu_threads and key the cache by them."""

    def _fake_whisper(self):
        """A WhisperModel stand-in that records constructor kwargs."""
        calls = []

        class FakeWhisperModel:
            def __init__(self, model_name, **kwargs):
                calls.append({"model_name": model_name, **kwargs})

            def transcribe(self, *args, **kwargs):
                # CUDA self-verification calls next(model.transcribe(...)[0], None)
                return iter([]), None

        return FakeWhisperModel, calls

    def test_cpu_model_gets_num_workers_and_cpu_threads(self):
        _model_cache.clear()
        Fake, calls = self._fake_whisper()
        with (
            patch("faster_whisper.WhisperModel", Fake),
            patch("app.matcher.asr_models.psutil.cpu_count", return_value=8),
        ):
            model = FasterWhisperModel("small", device="cpu", requested_workers=4)
            model.load()
        assert calls[0]["num_workers"] == 4
        assert calls[0]["cpu_threads"] == 2  # 8 // 4

    def test_cache_key_varies_by_requested_workers(self):
        _model_cache.clear()
        Fake, calls = self._fake_whisper()
        with (
            patch("faster_whisper.WhisperModel", Fake),
            patch("app.matcher.asr_models.psutil.cpu_count", return_value=8),
        ):
            get_cached_model(
                {"type": "whisper", "name": "small", "device": "cpu", "requested_workers": 2}
            )
            get_cached_model(
                {"type": "whisper", "name": "small", "device": "cpu", "requested_workers": 4}
            )
        # Two distinct worker counts -> two distinct constructions, not a stale reuse.
        assert len(calls) == 2

    def test_gpu_model_passes_workers_and_omits_cpu_threads(self):
        _model_cache.clear()
        Fake, calls = self._fake_whisper()
        with (
            patch("faster_whisper.WhisperModel", Fake),
            patch("app.matcher.asr_models.ctranslate2.get_cuda_device_count", return_value=1),
        ):
            model = FasterWhisperModel("small", device="cuda", requested_workers=3)
            model.load()
        assert calls[0]["num_workers"] == 3
        assert "cpu_threads" not in calls[0]  # GPU path must not pass cpu_threads

    def test_concurrent_get_cached_model_constructs_once(self):
        # N threads racing on a cold cache must build exactly ONE shared model, not N
        # (the _model_cache_lock guards the check-create-store). Without the lock this
        # would usually construct several models, each with N workers (N**2 threads).
        _model_cache.clear()
        Fake, calls = self._fake_whisper()
        cfg = {"type": "whisper", "name": "small", "device": "cpu", "requested_workers": 2}
        barrier = threading.Barrier(8)

        def worker():
            barrier.wait()  # release all threads together to maximize the race window
            get_cached_model(cfg)

        with (
            patch("faster_whisper.WhisperModel", Fake),
            patch("app.matcher.asr_models.psutil.cpu_count", return_value=8),
        ):
            threads = [threading.Thread(target=worker) for _ in range(8)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
        assert len(calls) == 1
