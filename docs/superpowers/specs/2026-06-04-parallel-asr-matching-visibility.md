# Parallel ASR Matching + Honest Dashboard (Piece A)

**Date:** 2026-06-04
**Status:** Approved design, pending implementation plan
**Scope:** Backend ASR concurrency + minimal dashboard visibility. GPU release bundling is a separate effort (Piece B) and is explicitly out of scope here.

## Problem

On the dashboard, multiple tracks display as `MATCHING` simultaneously but only one makes real progress (e.g. one track at 58%, four frozen at 5%), even with `max_concurrent_matches` set to 8. The machine shows low CPU activity throughout.

Root cause (verified):

1. **The shared Whisper model is single-worker.** `FasterWhisperModel.load()` constructs `WhisperModel(...)` with no `num_workers`, so it defaults to `num_workers=1` ([asr_models.py:172](../../../backend/app/matcher/asr_models.py)). All concurrent matches funnel through one shared, cached model instance (`get_cached_model` → module-level `_model_cache`, [asr_models.py:399](../../../backend/app/matcher/asr_models.py)). With one worker, ctranslate2 serializes concurrent `transcribe()` calls behind an internal queue — the active title transcribes while the others **block** (consuming ~0 CPU). That is why the CPU looks idle: it is not saturated, it is waiting on a single worker.

2. **The admission semaphore is decoupled from real capacity.** `max_concurrent_matches` sizes an `asyncio.Semaphore` that admits N titles into the `MATCHING` state ([job_manager.py:175](../../../backend/app/services/job_manager.py), [matching_coordinator.py:719](../../../backend/app/services/matching_coordinator.py)) — but the model only services one at a time. So the dashboard's `MATCHING` count overstates real progress: there is a hidden second queue (waiting-for-ASR-worker) the UI never shows. Tracks waiting for a *semaphore* slot correctly show `QUEUED`; tracks waiting for the *model worker* misleadingly show `MATCHING`.

3. **Config changes apply only on restart, silently.** The semaphore is initialized once in `job_manager.start()`; nothing re-initializes it when the config changes. The UI gives no hint that a restart is required.

The fix for the performance symptom and the fix for the visibility symptom are the same change: give the shared model real parallelism, and tie the admission count to that real capacity so the dashboard cannot overstate progress.

## Goals

- Parallelize ASR so multiple titles transcribe at once, on both CPU and GPU, using faster-whisper's supported `num_workers` (shared weights — no per-worker weight-memory multiplication).
- Make the dashboard honest: exactly the number of tracks that can really be transcribing show `MATCHING`; the rest show `QUEUED`.
- Surface which ASR backend is active (CPU/GPU, compute type, worker count) and make the "restart to apply" behavior explicit.

## Non-Goals (deferred)

- GPU libraries in the shipped release binary (Piece B).
- Model-size / device selection in the UI (e.g. `large-v3` on GPU).
- VRAM-based automatic worker sizing (a fixed conservative GPU cap is used instead).
- Live apply-without-restart (would require draining in-flight matches).
- Splitting the fast chromaprint path into its own non-ASR concurrency lane.

## Design

### 1. Single source of truth for ASR runtime sizing

Add one pure helper in the ASR/model layer:

```
resolve_asr_runtime(device: str, requested_workers: int) -> AsrRuntime
    # AsrRuntime: workers: int, cpu_threads: int | None
```

- **CPU:** `workers = clamp(requested_workers, 1, physical_cores)`;
  `cpu_threads = max(1, physical_cores // workers)` so `workers * cpu_threads ≈ physical_cores` (no thread oversubscription — the trap that makes naive parallelism *slower*). `physical_cores = psutil.cpu_count(logical=False)` (psutil is already a dependency).
- **GPU (`cuda`):** `workers = clamp(requested_workers, 1, GPU_WORKER_CAP)` with `GPU_WORKER_CAP = 4` (conservative default; float16 inference streams are cheap but not free). `cpu_threads = None` (not applicable).

`requested_workers` is `config.max_concurrent_matches`. The helper is the single place the worker math lives, consumed by the model loader, the semaphore, and the status endpoint, so all three always agree.

### 2. Wire workers/threads into the shared model

- `model_config` (built in `identify_episode` / `transcribe_full`, [episode_identification.py:1361](../../../backend/app/matcher/episode_identification.py)) carries `requested_workers` down to the model layer.
- `FasterWhisperModel.__init__`/`load()` calls `resolve_asr_runtime(self.device, requested_workers)` and passes `num_workers=workers` and (CPU only) `cpu_threads=cpu_threads` to `WhisperModel(...)`.
- **Cache-key fix:** both cache keys that index `_model_cache` (the one in `load()`, [asr_models.py:154](../../../backend/app/matcher/asr_models.py), and the one in `get_cached_model`, [asr_models.py:409](../../../backend/app/matcher/asr_models.py)) must include the resolved `workers`/`cpu_threads`, so a config change can never hand back a stale single-worker instance.
- `requested_workers` flows from config → curator (`_ensure_initialized`) → `EpisodeIdentifier` → `model_config`. The curator learns the requested value from config (resolved once; exact threading is an implementation detail for the plan).

### 3. Couple the semaphore to real capacity (the honesty fix)

`job_manager.start()` computes `runtime = resolve_asr_runtime(detected_device, config.max_concurrent_matches)` and calls `init_semaphore(runtime.workers)` instead of using the raw config value ([job_manager.py:175](../../../backend/app/services/job_manager.py)). Now a title cannot enter `MATCHING` without a real worker behind it — exactly `runtime.workers` tracks show `MATCHING`, the rest show `QUEUED`.

**Accepted tradeoff:** the fast chromaprint prepass also rides this semaphore, so fingerprint-only matches are throttled to `runtime.workers` as well. Acceptable for v1 (the prepass is fast); a dedicated non-ASR fast lane is a possible later refinement, noted as a non-goal.

### 4. Apply-on-restart, stated honestly

`num_workers` is fixed at model-load time, so the setting continues to take effect on backend restart (current behavior). The visibility fix is to stop hiding this: the ConfigWizard field for `max_concurrent_matches` gains a "takes effect after restart" hint. Live rebuild-on-save is deferred (non-goal).

### 5. Visibility surface

- **Honest counts** — fall out of §3 automatically; no extra UI logic. The frontend already distinguishes `QUEUED` (muted chip, 0%) from `MATCHING` ([TrackGrid.tsx:38](../../../frontend/src/app/components/TrackGrid.tsx)).
- **ASR mode badge** — a small read-only indicator: `ASR: CUDA · float16 · N workers` or `ASR: CPU · int8 · N workers`.
- **New endpoint** `GET /api/asr-status` returns the resolved runtime: `{ device, compute_type, model, workers, cpu_threads, max_concurrent_matches }`, computed via the same `resolve_asr_runtime` helper + the device/compute-type logic in `FasterWhisperModel`. Rendered in the dashboard (the existing "ACTIVE OPERATION" / "THROUGHPUT" panel area is a natural home). Text only — no live throughput graph in v1.

## Configuration

Reuse the existing `max_concurrent_matches` field — **no schema change** (it already exists across `AppConfig`, `ConfigUpdate`, `ConfigResponse`, and `ConfigWizard`, so the config three-way-sync hazard does not apply). Its semantics change from "admission slots" to "requested parallel ASR workers (hardware-clamped)". Default stays `2`.

## Affected components (orientation for the plan)

- `backend/app/matcher/asr_models.py` — `resolve_asr_runtime` helper; `FasterWhisperModel` passes `num_workers`/`cpu_threads`; cache keys include the resolved values.
- `backend/app/matcher/episode_identification.py` — `model_config` carries `requested_workers`.
- `backend/app/core/curator.py` — flow `requested_workers` (from config) into `EpisodeIdentifier`.
- `backend/app/services/job_manager.py` — size the semaphore from `resolve_asr_runtime(...).workers`.
- `backend/app/api/routes.py` — `GET /api/asr-status`.
- `frontend/src/...` — ASR mode badge on the dashboard; "restart to apply" hint in ConfigWizard.

## Testing

- **Unit:** `resolve_asr_runtime` math across core counts and CPU-vs-GPU (pure function); cache key includes worker params; semaphore initialized to the clamped value, not the raw config value.
- **Integration:** with `workers = N`, exactly N titles reach `MATCHING` and the rest stay `QUEUED` (extends the existing semaphore/QUEUED tests in `tests/unit/test_stuck_job_recovery.py` / `tests/unit/test_job_manager.py`).
- **API:** `GET /api/asr-status` returns the resolved runtime fields.

## Risks

- **Thread oversubscription on CPU** if `cpu_threads` is not divided down — mitigated by the §1 formula; covered by unit tests.
- **GPU memory** — bounded by the conservative `GPU_WORKER_CAP`; VRAM auto-sizing is a deferred enhancement, not a v1 risk.
- **Stale cached model** on config change — mitigated by folding worker params into both cache keys (§2).
- **faster-whisper concurrency** — `WhisperModel(num_workers=N)` with concurrent `transcribe()` from N threads is the library's supported parallelism path; no change to call sites' threading model (still `asyncio.to_thread` per title).
