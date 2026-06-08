# GPU acceleration (ASR / faster-whisper)

Engram transcribes episode audio with **faster-whisper**, which runs on
[CTranslate2](https://opennmt.net/CTranslate2/). CTranslate2 supports **NVIDIA CUDA only** —
there is no Metal/CoreML path (so Apple Silicon stays on CPU) and AMD ROCm is not used. GPU
ASR is therefore available on **Windows + NVIDIA** and **Linux + NVIDIA**; everything else runs
on the CPU with `int8`.

## Support matrix

| OS | GPU | Result |
|----|-----|--------|
| Windows | NVIDIA (CC ≥ 7.0) | ✅ CUDA `float16` |
| Windows | NVIDIA (Pascal, CC 6.x) | ✅ CUDA `int8_float16`/`float32` (auto-selected) |
| Linux | NVIDIA | ✅ CUDA |
| Windows/Linux | AMD / Intel / none | CPU `int8` |
| macOS | Apple / AMD | CPU `int8` (no GPU path in the engine) |

## Why the libraries aren't bundled

GPU inference needs **cuDNN 9 + cuBLAS** (CTranslate2 4.6 → CUDA ≥ 12.3 / cuDNN 9). Those
libraries are ~1.2 GB and CTranslate2 `dlopen`s them lazily by name, so PyInstaller's static
analysis never bundles them. Shipping them in every build would triple the download for the
CPU-only majority. Instead they're fetched **on demand, opt-in**.

Note: CTranslate2's own CUDA-capable extension *is* inside the `ctranslate2` wheel, so
`ctranslate2.get_cuda_device_count()` works in the frozen build — that's why a GPU is
*detected* even before the math libraries exist.

## Runtime download (end users)

Settings → Matching → **GPU Acceleration**:

1. The panel detects an NVIDIA GPU and offers a one-time **Download & enable** (~1.2 GB after
   accepting the NVIDIA CUDA EULA).
2. `POST /api/asr/gpu/enable` starts a background download of the pinned cuDNN/cuBLAS wheels
   (`backend/app/matcher/cuda_runtime.py`), SHA256-verified, extracted to
   `~/.engram/cuda/<version>/`. Progress is reported via `GET /api/asr-status` and the
   `gpu_status` WebSocket message.
3. The download lives outside the install dir, so it **survives app updates** (like the DB and
   subtitle cache) and the updater needs no changes.
4. **Restart the backend** to activate. On restart `job_manager.start()` registers the libs
   (Windows `os.add_dll_directory`; Linux ordered `ctypes.CDLL` preload) and pins the effective
   device with `set_asr_device("cuda")`.

`POST /api/asr/gpu/disable` reverts to CPU on the next restart (the cached libraries are kept).

## Developer setup (no download)

```bash
cd backend
uv sync -E gpu      # installs nvidia-cudnn-cu12 + nvidia-cublas-cu12
```

`register_cuda_runtime()` falls back to the pip `nvidia.*` packages when the download cache is
absent, so `uv sync -E gpu` is all a developer needs. Enable the toggle in Settings (it'll
report the runtime already installed) and restart.

Verify:

```bash
curl localhost:8000/api/asr-status      # device:"cuda", gpu_state:"active"
# run a match; ~/.engram/engram.log should show "Loaded ... on cuda" (no "Falling back to CPU")
# watch nvidia-smi for the python/uvicorn process during matching
```

## How the device decision stays honest

Historically four call sites each probed `get_cuda_device_count()`, so the status badge could
claim CUDA while the model silently fell back to CPU. Now the **effective** device is resolved
once at startup (after the libs are registered) and pinned via `set_asr_device()`. Everything —
the `/api/asr-status` badge, the match semaphore, and the model loader — reads it through
`detect_asr_device()`. `gpu_detected()` remains the raw hardware probe used only to decide
whether to offer the toggle.
