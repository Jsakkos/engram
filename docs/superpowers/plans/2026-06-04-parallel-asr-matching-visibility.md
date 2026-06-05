# Parallel ASR Matching + Honest Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the shared Whisper model real parallelism via faster-whisper `num_workers`, tie the match-admission semaphore to that real capacity so the dashboard stops overstating `MATCHING`, and surface the active ASR backend.

**Architecture:** A single pure helper (`resolve_asr_runtime`) in the ASR model layer computes `(workers, cpu_threads)` from the detected device and the requested worker count (`config.max_concurrent_matches`), clamped to hardware. That one value flows to three consumers: the shared `WhisperModel` (parallel inference), the `asyncio.Semaphore` in `JobManager` (honest admission), and a new `GET /api/asr-status` endpoint (a read-only dashboard badge).

**Tech Stack:** Python 3.11, FastAPI, faster-whisper / ctranslate2, psutil, pytest/pytest-asyncio; React + TypeScript + Vite frontend.

**Spec:** `docs/superpowers/specs/2026-06-04-parallel-asr-matching-visibility.md`

---

## File Structure

**Backend**
- `backend/app/matcher/asr_models.py` — add `AsrRuntime`, `GPU_WORKER_CAP`, `detect_asr_device()`, `resolve_asr_runtime()`; thread `requested_workers` into `FasterWhisperModel` and both `_model_cache` keys (Tasks 1, 2).
- `backend/app/matcher/episode_identification.py` — `EpisodeMatcher` carries `requested_workers` into a shared `_model_config()` helper (Task 3).
- `backend/app/core/curator.py` — pass `config.max_concurrent_matches` into `EpisodeMatcher` (Task 4).
- `backend/app/services/job_manager.py` — size the semaphore from `resolve_asr_runtime(...).workers` (Task 5).
- `backend/app/api/routes.py` — `GET /api/asr-status` (Task 6).
- `backend/tests/unit/test_asr_runtime.py` — new test module (Tasks 1, 2).
- `backend/tests/unit/test_api_routes.py` — extend for the new endpoint (Task 6).

**Frontend**
- `frontend/src/app/components/AsrStatusBadge.tsx` — new self-contained badge (Task 7).
- `frontend/src/app/App.tsx` — mount the badge (Task 7).
- `frontend/src/components/ConfigWizard.tsx` — honest hint text + input bound (Task 8).

---

## Task 1: ASR runtime sizing helpers (pure, fully unit-tested)

**Files:**
- Modify: `backend/app/matcher/asr_models.py` (top-of-file imports + new module-level helpers)
- Test: `backend/tests/unit/test_asr_runtime.py` (create)

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/unit/test_asr_runtime.py`:

```python
"""Unit tests for ASR runtime sizing (resolve_asr_runtime / detect_asr_device)."""

from unittest.mock import patch

from app.matcher.asr_models import (
    GPU_WORKER_CAP,
    AsrRuntime,
    resolve_asr_runtime,
)


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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && uv run pytest tests/unit/test_asr_runtime.py -v`
Expected: FAIL — `ImportError: cannot import name 'GPU_WORKER_CAP'` (helpers not defined yet).

- [ ] **Step 3: Add the imports and helpers**

In `backend/app/matcher/asr_models.py`, add to the existing top-of-file imports (the module already imports `ctranslate2`; add these two):

```python
import psutil
from dataclasses import dataclass
```

Then add these module-level definitions near the top of the file, after the imports and the `_model_cache = {}` line:

```python
GPU_WORKER_CAP = 4  # Conservative parallel-stream cap on GPU (VRAM auto-sizing is a future enhancement).


@dataclass(frozen=True)
class AsrRuntime:
    """Resolved ASR execution parameters — the single source of truth for sizing.

    Consumed by the shared WhisperModel (num_workers/cpu_threads), the JobManager
    match semaphore (workers == admission slots, so the dashboard cannot overstate
    MATCHING), and the /api/asr-status endpoint.
    """

    device: str  # "cuda" | "cpu"
    compute_type: str  # "float16" (cuda) | "int8" (cpu)
    workers: int
    cpu_threads: int | None  # None on GPU (not applicable)


def detect_asr_device() -> str:
    """Return 'cuda' when a CUDA device is visible to ctranslate2, else 'cpu'."""
    try:
        return "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
    except Exception:  # noqa: BLE001 — any probe failure means no usable GPU
        return "cpu"


def resolve_asr_runtime(device: str, requested_workers: int) -> AsrRuntime:
    """Resolve (workers, cpu_threads) from a requested worker count, clamped to hardware.

    CPU: workers clamp to physical cores; cpu_threads = cores // workers so the total
    thread count stays ~= cores (avoids the oversubscription that makes naive
    parallelism slower). GPU: workers clamp to GPU_WORKER_CAP; cpu_threads is N/A.
    """
    requested = max(1, int(requested_workers or 1))
    if device == "cuda":
        return AsrRuntime(
            device="cuda",
            compute_type="float16",
            workers=min(requested, GPU_WORKER_CAP),
            cpu_threads=None,
        )
    cores = psutil.cpu_count(logical=False) or psutil.cpu_count(logical=True) or 1
    workers = max(1, min(requested, cores))
    cpu_threads = max(1, cores // workers)
    return AsrRuntime(
        device="cpu", compute_type="int8", workers=workers, cpu_threads=cpu_threads
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && uv run pytest tests/unit/test_asr_runtime.py -v`
Expected: PASS (all 7 tests).

- [ ] **Step 5: Lint and commit**

```bash
cd backend && uv run ruff check app/matcher/asr_models.py tests/unit/test_asr_runtime.py
git add backend/app/matcher/asr_models.py backend/tests/unit/test_asr_runtime.py
git commit -m "feat(asr): add resolve_asr_runtime hardware-clamped sizing helper"
```

---

## Task 2: Pass workers/threads to the shared model + fix cache keys

**Files:**
- Modify: `backend/app/matcher/asr_models.py` — `FasterWhisperModel.__init__` (123-138), `load()` (149-220), `create_asr_model` (360-396), `get_cached_model` (399-416)
- Test: `backend/tests/unit/test_asr_runtime.py` (add a class)

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/unit/test_asr_runtime.py`:

```python
class TestModelConstruction:
    """FasterWhisperModel must pass num_workers/cpu_threads and key the cache by them."""

    def _fake_whisper(self):
        """A WhisperModel stand-in that records constructor kwargs."""
        calls = []

        class FakeWhisperModel:
            def __init__(self, model_name, **kwargs):
                calls.append({"model_name": model_name, **kwargs})

        return FakeWhisperModel, calls

    def test_cpu_model_gets_num_workers_and_cpu_threads(self):
        import app.matcher.asr_models as m

        m._model_cache.clear()
        Fake, calls = self._fake_whisper()
        with patch("faster_whisper.WhisperModel", Fake), patch(
            "app.matcher.asr_models.psutil.cpu_count", return_value=8
        ):
            model = m.FasterWhisperModel("small", device="cpu", requested_workers=4)
            model.load()
        assert calls[0]["num_workers"] == 4
        assert calls[0]["cpu_threads"] == 2  # 8 // 4

    def test_cache_key_varies_by_requested_workers(self):
        import app.matcher.asr_models as m

        m._model_cache.clear()
        Fake, calls = self._fake_whisper()
        with patch("faster_whisper.WhisperModel", Fake), patch(
            "app.matcher.asr_models.psutil.cpu_count", return_value=8
        ):
            m.get_cached_model({"type": "whisper", "name": "small", "device": "cpu", "requested_workers": 2})
            m.get_cached_model({"type": "whisper", "name": "small", "device": "cpu", "requested_workers": 4})
        # Two distinct worker counts -> two distinct constructions, not a stale reuse.
        assert len(calls) == 2
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && uv run pytest tests/unit/test_asr_runtime.py::TestModelConstruction -v`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'requested_workers'`.

- [ ] **Step 3: Implement — `FasterWhisperModel.__init__`**

Replace the existing `__init__` (lines 123-138) with:

```python
    def __init__(
        self, model_name: str = "small", device: str | None = None, requested_workers: int = 1
    ):
        """
        Initialize Faster Whisper model.

        Args:
            model_name: Whisper model size (tiny, base, small, medium, large-v3)
            device: Device to run on ('cpu', 'cuda', or None for auto-detect)
            requested_workers: Desired parallel ASR workers; resolve_asr_runtime
                clamps this to hardware at load time.
        """
        if model_name.startswith("openai/whisper-"):
            model_name = model_name.replace("openai/whisper-", "")

        self.requested_workers = max(1, int(requested_workers or 1))

        # Ensure NVIDIA libraries are in PATH for Windows CUDA support
        if device == "cuda" or (device is None and ctranslate2.get_cuda_device_count() > 0):
            _ensure_nvidia_libraries()

        super().__init__(model_name, device)
```

- [ ] **Step 4: Implement — `load()` cache key + WhisperModel kwargs**

In `load()`, replace the cache_key line (154):

```python
        cache_key = f"faster_whisper_{self.model_name}_{self.device}"
```

with:

```python
        runtime = resolve_asr_runtime(self.device, self.requested_workers)
        cache_key = (
            f"faster_whisper_{self.model_name}_{self.device}"
            f"_w{runtime.workers}_t{runtime.cpu_threads}"
        )
```

Replace the primary `WhisperModel(...)` construction (lines 172-177):

```python
                self._model = WhisperModel(
                    self.model_name,
                    device=self.device,
                    compute_type=compute_type,
                    download_root=None,  # Use default cache location
                )
```

with (adds num_workers always; cpu_threads only when applicable):

```python
                _kwargs = {
                    "device": self.device,
                    "compute_type": compute_type,
                    "download_root": None,  # Use default cache location
                    "num_workers": runtime.workers,
                }
                if runtime.cpu_threads is not None:
                    _kwargs["cpu_threads"] = runtime.cpu_threads
                self._model = WhisperModel(self.model_name, **_kwargs)
```

Replace the CUDA-to-CPU fallback construction (lines 200-207) so the fallback re-resolves for CPU:

```python
                    self.device = "cpu"
                    compute_type = "int8"
                    cpu_runtime = resolve_asr_runtime("cpu", self.requested_workers)
                    self._model = WhisperModel(
                        self.model_name,
                        device=self.device,
                        compute_type=compute_type,
                        download_root=None,
                        num_workers=cpu_runtime.workers,
                        cpu_threads=cpu_runtime.cpu_threads,
                    )
```

- [ ] **Step 5: Implement — `create_asr_model` forwards `requested_workers`**

In `create_asr_model`, after `device = model_config.get("device")` (line 376) add:

```python
    requested_workers = model_config.get("requested_workers", 1)
```

and update both `FasterWhisperModel(...)` calls (lines 384 and 391) to pass it:

```python
        return FasterWhisperModel(model_name, device, requested_workers=requested_workers)
```
```python
        return FasterWhisperModel("small", device, requested_workers=requested_workers)
```

- [ ] **Step 6: Implement — `get_cached_model` cache key**

In `get_cached_model`, replace the cache_key line (409):

```python
    cache_key = f"{model_config.get('type', '')}_{model_config.get('name', '')}_{model_config.get('device', 'auto')}"
```

with:

```python
    cache_key = (
        f"{model_config.get('type', '')}_{model_config.get('name', '')}"
        f"_{model_config.get('device', 'auto')}_w{model_config.get('requested_workers', 1)}"
    )
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `cd backend && uv run pytest tests/unit/test_asr_runtime.py -v`
Expected: PASS (all tests, including `TestModelConstruction`).

- [ ] **Step 8: Lint and commit**

```bash
cd backend && uv run ruff check app/matcher/asr_models.py tests/unit/test_asr_runtime.py
git add backend/app/matcher/asr_models.py backend/tests/unit/test_asr_runtime.py
git commit -m "feat(asr): give the shared Whisper model num_workers/cpu_threads"
```

---

## Task 3: `EpisodeMatcher` carries `requested_workers` into model config

**Files:**
- Modify: `backend/app/matcher/episode_identification.py` — `EpisodeMatcher.__init__` (820-844), `transcribe_full` model_config (1143), `identify_episode` model_config (1361-1365)
- Test: `backend/app/matcher/test_episode_identification.py` (add a test alongside the existing `TestEpisodeMatcherConfiguration`)

- [ ] **Step 1: Write the failing test**

Append to `backend/app/matcher/test_episode_identification.py`:

```python
class TestEpisodeMatcherWorkers:
    def test_model_config_includes_requested_workers(self):
        from pathlib import Path

        from app.matcher.episode_identification import EpisodeMatcher

        matcher = EpisodeMatcher(
            cache_dir=Path.home() / ".engram" / "cache",
            show_name="Test Show",
            requested_workers=5,
        )
        cfg = matcher._model_config()
        assert cfg["requested_workers"] == 5
        assert cfg["type"] == "whisper"
        assert cfg["name"] == matcher.model_name

    def test_requested_workers_defaults_to_one(self):
        from pathlib import Path

        from app.matcher.episode_identification import EpisodeMatcher

        matcher = EpisodeMatcher(
            cache_dir=Path.home() / ".engram" / "cache", show_name="Test Show"
        )
        assert matcher._model_config()["requested_workers"] == 1
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd backend && uv run pytest app/matcher/test_episode_identification.py::TestEpisodeMatcherWorkers -v`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'requested_workers'`.

- [ ] **Step 3: Implement — add the param and a shared `_model_config()` helper**

In `EpisodeMatcher.__init__` (820-832), add the parameter after `model_name="small",`:

```python
        model_name="small",
        requested_workers=1,
        expected_tmdb_id=None,
    ):
```

and store it alongside `self.model_name = model_name` (line 841):

```python
        self.requested_workers = max(1, int(requested_workers or 1))
```

Add this method to `EpisodeMatcher` (place it just above `transcribe_full`, near line 1127):

```python
    def _model_config(self) -> dict:
        """Single source for the ASR model_config dict (keeps both call sites DRY)."""
        return {
            "type": "whisper",
            "name": self.model_name,
            "device": self.device,
            "requested_workers": self.requested_workers,
        }
```

Replace the inline dict in `transcribe_full` (line 1143):

```python
        model_config = {"type": "whisper", "name": self.model_name, "device": self.device}
```

with:

```python
        model_config = self._model_config()
```

Replace the inline dict in `identify_episode` (lines 1361-1365):

```python
            model_config = {
                "type": "whisper",
                "name": self.model_name,
                "device": self.device,
            }
```

with:

```python
            model_config = self._model_config()
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd backend && uv run pytest app/matcher/test_episode_identification.py::TestEpisodeMatcherWorkers -v`
Expected: PASS.

- [ ] **Step 5: Run the existing matcher config tests (no regressions)**

Run: `cd backend && uv run pytest app/matcher/test_episode_identification.py -v`
Expected: PASS (existing `TestEpisodeMatcherConfiguration` still green).

- [ ] **Step 6: Lint and commit**

```bash
cd backend && uv run ruff check app/matcher/episode_identification.py app/matcher/test_episode_identification.py
git add backend/app/matcher/episode_identification.py backend/app/matcher/test_episode_identification.py
git commit -m "feat(asr): thread requested_workers through EpisodeMatcher model_config"
```

---

## Task 4: Curator passes `config.max_concurrent_matches` into the matcher

**Files:**
- Modify: `backend/app/core/curator.py` — `_ensure_initialized` `EpisodeMatcher(...)` construction (102-107)
- Test: `backend/tests/unit/test_curator_workers.py` (create)

- [ ] **Step 1: Write the failing test**

Create `backend/tests/unit/test_curator_workers.py`:

```python
"""The curator forwards config.max_concurrent_matches as the matcher's requested_workers."""

from unittest.mock import MagicMock, patch


def test_ensure_initialized_passes_concurrency_as_workers():
    from app.core.curator import EpisodeCurator

    fake_config = MagicMock()
    fake_config.subtitles_cache_path = None
    fake_config.max_concurrent_matches = 7

    with patch("app.matcher.episode_identification.EpisodeMatcher") as MockMatcher, patch(
        "app.services.config_service.get_config_sync", return_value=fake_config
    ), patch("app.matcher.tmdb_client.fetch_show_id", return_value=None), patch(
        "app.matcher.tmdb_client.fetch_show_details", return_value=None
    ):
        curator = EpisodeCurator()
        curator._ensure_initialized("Test Show")

    assert MockMatcher.call_args.kwargs["requested_workers"] == 7
```

> Note: `EpisodeCurator` is defined at `backend/app/core/curator.py:30` (module singleton `curator` at line 737). Instantiating a fresh `EpisodeCurator()` in the test is fine.

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd backend && uv run pytest tests/unit/test_curator_workers.py -v`
Expected: FAIL — `KeyError: 'requested_workers'` (not passed yet).

- [ ] **Step 3: Implement**

In `_ensure_initialized`, update the `EpisodeMatcher(...)` construction (lines 102-107) to pass the requested workers (the `config` local is already populated at line 93):

```python
            self._matcher = EpisodeMatcher(
                cache_dir=self._cache_dir,
                show_name=canonical_name,
                min_confidence=self.LOW_CONFIDENCE_THRESHOLD,
                requested_workers=(config.max_concurrent_matches if config else 1),
                expected_tmdb_id=tmdb_id,
            )
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd backend && uv run pytest tests/unit/test_curator_workers.py -v`
Expected: PASS.

- [ ] **Step 5: Lint and commit**

```bash
cd backend && uv run ruff check app/core/curator.py tests/unit/test_curator_workers.py
git add backend/app/core/curator.py tests/unit/test_curator_workers.py
git commit -m "feat(asr): forward max_concurrent_matches as matcher requested_workers"
```

---

## Task 5: Size the match semaphore from real worker capacity

**Files:**
- Modify: `backend/app/services/job_manager.py` — `start()` semaphore init (174-181) and the startup log line (207)
- Test: `backend/tests/unit/test_semaphore_sizing.py` (create)

- [ ] **Step 1: Write the failing test**

Create `backend/tests/unit/test_semaphore_sizing.py`:

```python
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
```

- [ ] **Step 2: Run the test to verify it fails or passes**

Run: `cd backend && uv run pytest tests/unit/test_semaphore_sizing.py -v`
Expected: PASS for both (this asserts the helper→semaphore contract; `init_semaphore` already exists). If `MatchingCoordinator.__new__` cannot set `_match_semaphore`, the test will error — in that case construct it normally and call `init_semaphore`. This test locks the contract Step 3 wires into `JobManager`.

- [ ] **Step 3: Implement the JobManager wiring**

In `backend/app/services/job_manager.py` `start()`, replace lines 174-181:

```python
        # Initialize matching concurrency limiter
        concurrency = max(1, config.max_concurrent_matches)
        if concurrency != config.max_concurrent_matches:
            logger.warning(
                f"Invalid max_concurrent_matches={config.max_concurrent_matches} "
                f"in config, using {concurrency}"
            )
        self._matching.init_semaphore(concurrency)
```

with:

```python
        # Initialize matching concurrency limiter from REAL ASR capacity, so the
        # dashboard's MATCHING count can't exceed what can actually be transcribing.
        from app.matcher.asr_models import detect_asr_device, resolve_asr_runtime

        _asr_runtime = resolve_asr_runtime(detect_asr_device(), config.max_concurrent_matches)
        self._matching.init_semaphore(_asr_runtime.workers)
```

Replace the startup log line (207):

```python
        logger.info(f"Job manager started (max_concurrent_matches={concurrency})")
```

with:

```python
        logger.info(
            f"Job manager started (asr_device={_asr_runtime.device}, "
            f"asr_workers={_asr_runtime.workers}, cpu_threads={_asr_runtime.cpu_threads}, "
            f"requested={config.max_concurrent_matches})"
        )
```

- [ ] **Step 4: Run the test + existing job_manager unit tests**

Run: `cd backend && uv run pytest tests/unit/test_semaphore_sizing.py tests/unit/test_job_manager.py tests/unit/test_stuck_job_recovery.py -v`
Expected: PASS (the existing stuck-job/semaphore tests call `init_semaphore` directly and are unaffected).

- [ ] **Step 5: Lint and commit**

```bash
cd backend && uv run ruff check app/services/job_manager.py tests/unit/test_semaphore_sizing.py
git add backend/app/services/job_manager.py tests/unit/test_semaphore_sizing.py
git commit -m "feat(matching): size match semaphore from real ASR worker capacity"
```

---

## Task 6: `GET /api/asr-status` endpoint

**Files:**
- Modify: `backend/app/api/routes.py` — add a new route (place it near the other read-only status routes; the file uses `router = APIRouter(prefix="/api")` at line 40)
- Test: `backend/tests/unit/test_api_routes.py` (add a test)

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/unit/test_api_routes.py` (this file already constructs an `AsyncClient` against the app — mirror its existing client fixture/usage):

```python
class TestAsrStatusEndpoint:
    async def test_asr_status_reports_cpu_runtime(self, client):
        from unittest.mock import patch

        with patch("app.matcher.asr_models.detect_asr_device", return_value="cpu"), patch(
            "app.matcher.asr_models.psutil.cpu_count", return_value=8
        ):
            resp = await client.get("/api/asr-status")
        assert resp.status_code == 200
        body = resp.json()
        assert body["device"] == "cpu"
        assert body["compute_type"] == "int8"
        assert body["workers"] >= 1
        assert "max_concurrent_matches" in body
        assert "model" in body
```

> Note: use whatever `client` fixture `test_api_routes.py` already defines (the file at lines 34/212 sets up config and an async client). If the module isn't `asyncio`-marked globally, add `@pytest.mark.asyncio` to the test.

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd backend && uv run pytest tests/unit/test_api_routes.py::TestAsrStatusEndpoint -v`
Expected: FAIL — 404 (route not defined).

- [ ] **Step 3: Implement the endpoint**

Add to `backend/app/api/routes.py` (anywhere among the route handlers; keep it self-contained). Alias the config getter to avoid colliding with the `get_config` route handler defined at line 1100:

```python
@router.get("/asr-status")
async def get_asr_status():
    """Resolved ASR backend for the dashboard badge (read-only, no secrets)."""
    from app.matcher.asr_models import detect_asr_device, resolve_asr_runtime
    from app.services.config_service import get_config as _get_app_config

    config = await _get_app_config()
    runtime = resolve_asr_runtime(detect_asr_device(), config.max_concurrent_matches)
    return {
        "device": runtime.device,
        "compute_type": runtime.compute_type,
        "model": "small",  # current hardcoded matcher default (EpisodeMatcher.model_name)
        "workers": runtime.workers,
        "cpu_threads": runtime.cpu_threads,
        "max_concurrent_matches": config.max_concurrent_matches,
    }
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd backend && uv run pytest tests/unit/test_api_routes.py::TestAsrStatusEndpoint -v`
Expected: PASS.

- [ ] **Step 5: Lint and commit**

```bash
cd backend && uv run ruff check app/api/routes.py tests/unit/test_api_routes.py
git add backend/app/api/routes.py tests/unit/test_api_routes.py
git commit -m "feat(api): add GET /api/asr-status for the dashboard ASR badge"
```

---

## Task 7: Frontend ASR mode badge

**Files:**
- Create: `frontend/src/app/components/AsrStatusBadge.tsx`
- Modify: `frontend/src/app/App.tsx` — mount the badge in the filter/view strip (just after `<SvTopBar ... />`, around line 199)

- [ ] **Step 1: Create the badge component**

Create `frontend/src/app/components/AsrStatusBadge.tsx`:

```tsx
import { useEffect, useState } from "react";

type AsrStatus = {
  device: string;
  compute_type: string;
  model: string;
  workers: number;
  cpu_threads: number | null;
  max_concurrent_matches: number;
};

/** Read-only chip showing the resolved ASR backend (device · compute · N workers). */
export function AsrStatusBadge() {
  const [status, setStatus] = useState<AsrStatus | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetch("/api/asr-status")
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (!cancelled) setStatus(data);
      })
      .catch(() => {
        /* badge is best-effort; stay hidden on failure */
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (!status) return null;

  const accent = status.device === "cuda" ? "#22d3ee" : "#8893a8";
  const label = `ASR: ${status.device.toUpperCase()} · ${status.compute_type} · ${status.workers}w`;

  return (
    <span
      title={`Whisper "${status.model}" · requested ${status.max_concurrent_matches}${
        status.cpu_threads != null ? ` · ${status.cpu_threads} threads/worker` : ""
      } · restart to change`}
      style={{
        fontFamily: "'JetBrains Mono', monospace",
        fontSize: 11,
        letterSpacing: "0.08em",
        color: accent,
        border: `1px solid ${accent}44`,
        background: `${accent}0a`,
        padding: "2px 8px",
        whiteSpace: "nowrap",
      }}
    >
      {label}
    </span>
  );
}
```

- [ ] **Step 2: Mount the badge in App.tsx**

In `frontend/src/app/App.tsx`, add the import near the other component imports (top of file):

```tsx
import { AsrStatusBadge } from "./components/AsrStatusBadge";
```

Then render it inside the filter/view-mode strip. The strip begins around line 200 with `<div style={{ padding: "10px 28px", ... display: "flex", alignItems: "center", ... }}>`. Add the badge as the last child of that flex row, pushed to the right:

```tsx
        <div style={{ marginLeft: "auto" }}>
          <AsrStatusBadge />
        </div>
```

> If the strip already has a right-aligned cluster (e.g. the grid/list view toggle), place `<AsrStatusBadge />` immediately before that cluster instead of adding a second `marginLeft: auto`.

- [ ] **Step 3: Build to verify it compiles**

Run: `cd frontend && npm run build`
Expected: TypeScript check + Vite build succeed (no type errors). If `node_modules` is missing in this worktree, run `npm install` first, then `git checkout package-lock.json` before committing (the committed lockfile is stale and `install` rewrites it).

- [ ] **Step 4: Manual visual check (optional but recommended)**

With a backend running (`DEBUG=true`) and `npm run dev`, confirm the chip reads e.g. `ASR: CUDA · float16 · 4w` (your GPU-from-source setup) or `ASR: CPU · int8 · Nw`.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/app/components/AsrStatusBadge.tsx frontend/src/app/App.tsx
git commit -m "feat(ui): show resolved ASR backend as a dashboard badge"
```

---

## Task 8: ConfigWizard — honest hint + input bound

**Files:**
- Modify: `frontend/src/components/ConfigWizard.tsx` — the Max Concurrent Matches field (1239-1251)

- [ ] **Step 1: Update the hint text and input bounds**

Replace the input + hint block (lines 1240-1250) with:

```tsx
                            <input
                                id="maxConcurrentMatches"
                                type="number"
                                min={1}
                                max={8}
                                value={config.maxConcurrentMatches}
                                onChange={(e) => handleInputChange('maxConcurrentMatches', Math.max(1, Math.min(8, parseInt(e.target.value) || 1)))}
                            />
                            <span className="form-hint">
                                Requested number of episodes transcribed in parallel. Automatically
                                clamped to your hardware (CPU cores, or a GPU limit). Takes effect
                                after a backend restart.
                            </span>
```

- [ ] **Step 2: Build to verify it compiles**

Run: `cd frontend && npm run build`
Expected: success.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/ConfigWizard.tsx
git commit -m "fix(ui): clarify Max Concurrent Matches semantics + restart note"
```

---

## Task 9: Full backend regression + final verification

**Files:** none (verification only)

- [ ] **Step 1: Run the full backend test suite**

Run: `cd backend && uv run pytest tests/unit/ app/matcher/test_episode_identification.py -v`
Expected: PASS. Investigate any failure before proceeding (the known pre-existing `test_movie_ambiguous_rip_first_workflow` flake is unrelated to this change).

- [ ] **Step 2: Lint the whole touched surface**

Run: `cd backend && uv run ruff check app/ tests/`
Expected: no errors.

- [ ] **Step 3: Manual end-to-end sanity (optional)**

Start one backend (`DEBUG=true`), set `max_concurrent_matches` high, and confirm via logs (`Job manager started (asr_device=..., asr_workers=...)`) and `GET /api/asr-status` that the resolved worker count matches expectation, and that on a multi-track disc exactly `workers` tracks show `MATCHING` while the rest show `QUEUED`.

---

## Self-Review Notes

- **Spec coverage:** §1 sizing → Task 1; §2 model wiring + cache keys → Tasks 2-4; §3 semaphore honesty → Task 5; §5 visibility (`/api/asr-status` + badge) → Tasks 6-7; config hint/restart → Task 8. GPU bundling, model-size UI, VRAM auto-sizing, live-apply remain out of scope (spec non-goals).
- **Type consistency:** `AsrRuntime(device, compute_type, workers, cpu_threads)` and `resolve_asr_runtime(device, requested_workers)` / `detect_asr_device()` are used with identical signatures across Tasks 1, 2, 5, 6. The `model_config` key is `requested_workers` everywhere (Tasks 2, 3). The endpoint is `/api/asr-status` in both Task 6 and Task 7.
- **Restart semantics:** `num_workers` is fixed at model load; the plan does not attempt live re-init (a spec non-goal) and the ConfigWizard hint states this.
```
