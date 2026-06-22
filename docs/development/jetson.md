# NVIDIA Jetson (Linux arm64)

Engram ships a native **Linux arm64 (aarch64)** build —
`engram-linux-arm64.tar.gz` — that runs on NVIDIA Jetson and other aarch64 Linux
devices. It is produced by the `build-linux-arm64` job in
[`.github/workflows/release.yml`](../../.github/workflows/release.yml) on a
GitHub-hosted `ubuntu-24.04-arm` runner, so it goes through the same smoke test,
CA-bundle / fpcalc / TLS self-test, and manifest checks as the x86_64 build.

## Install (CPU ASR — works out of the box)

1. Download **`engram-linux-arm64.tar.gz`** from the
   [Releases](https://github.com/Jsakkos/engram/releases) page.
2. Extract and run:

   ```bash
   tar xzf engram-linux-arm64.tar.gz
   cd engram
   ./engram
   ```

3. The Config Wizard opens in your browser. FFmpeg is required for episode
   matching — install it with `sudo apt-get install ffmpeg`.

The bundled `fpcalc` audio fingerprinter is the aarch64 Chromaprint 1.6.0 build
(Chromaprint 1.5.1, used on other platforms, never shipped an arm64 binary).

> **ASR runs on the CPU in this build.** That is a hard constraint of the
> ecosystem, not a configuration choice — see below.

## Why GPU ASR needs an extra on-device step

Engram transcribes audio with faster-whisper, which runs on
[CTranslate2](https://opennmt.net/CTranslate2/). CTranslate2 supports **NVIDIA
CUDA only**. On x86_64 the PyPI `ctranslate2` wheel ships a CUDA-enabled build,
so GPU works once cuDNN/cuBLAS are present (see [gpu.md](./gpu.md)).

**On aarch64 the PyPI `ctranslate2` wheel is CPU-only** — there is no
CUDA-enabled aarch64 wheel. So a generic CI bundle, and the GitHub arm64 runner
that builds it (no JetPack/L4T/Tegra CUDA), can only produce a CPU build. GPU on
Jetson requires compiling CTranslate2 from source against the device's JetPack
CUDA toolkit, targeting the device's compute capability:

| Jetson family | `CUDA_ARCH` |
|---------------|-------------|
| Orin (AGX / NX / Nano) | `87` |
| Xavier (AGX / NX) | `72` |
| TX2 / earlier | `62` / `53` |

## Enable GPU ASR (`jetson_gpu_setup.sh`)

> **Status: requires validation on real Jetson hardware.** This is a best-effort,
> well-signposted procedure, not a turnkey guarantee. Build times on a Jetson are
> long (tens of minutes).

Prerequisites:

- **JetPack 6.x** (CUDA 12.x, Ubuntu 22.04 base). The bundle is built against
  Python 3.11; JetPack 6's system stack aligns with upstream CUDA 12, which is
  what CTranslate2 4.6 targets. Older JetPack (5.x / CUDA 11.4) is **not**
  supported by this path.
- The extracted `engram-linux-arm64` bundle.

Run the helper against the extracted bundle:

```bash
# from the extracted bundle's backend scripts, or download the script from the repo
CUDA_ARCH=87 ./jetson_gpu_setup.sh /path/to/extracted/engram
```

What it does ([`backend/scripts/jetson_gpu_setup.sh`](../../backend/scripts/jetson_gpu_setup.sh)):

1. Verifies it's running on a Jetson (Tegra device nodes / `/etc/nv_tegra_release`)
   and locates the JetPack CUDA toolkit (`$CUDA_HOME`, default `/usr/local/cuda`).
2. Reads the CTranslate2 version baked into the bundle
   (`_internal/ctranslate2/version.py`) so the compiled C++ library matches the
   bundled Python bindings.
3. Builds CTranslate2 with
   `-DWITH_CUDA=ON -DWITH_CUDNN=ON -DWITH_MKL=OFF -DWITH_OPENBLAS=ON`
   (MKL is x86-only; OpenBLAS is the aarch64 equivalent) for the target
   `CMAKE_CUDA_ARCHITECTURES`.
4. Builds the matching Python bindings and swaps `libctranslate2.so*` +
   `ctranslate2/_ext*.so` into the bundle, backing up the CPU-only build to
   `_internal/ctranslate2.cpu-backup`.

JetPack already provides cuDNN and cuBLAS system-wide, so the on-demand wheel
download in `backend/app/matcher/cuda_runtime.py` (used on x86_64) is **not**
needed here — it remains a fallback.

## Confirm GPU is active

```bash
curl localhost:8000/api/asr-status      # expect device:"cuda"
sudo tegrastats                         # watch GPU load while a match runs
# ~/.engram/engram.log shows "Loaded ... on cuda" (no "Falling back to CPU")
```

The effective device is resolved once at startup (`job_manager.start()` →
`set_asr_device()`), and every call site reads it through `detect_asr_device()`,
so the `/api/asr-status` badge can't disagree with the model loader.

### If it still reports CPU

`gpu_detected()` in `cuda_runtime.py` is the raw hardware probe. On JetPack 6
`nvidia-smi` exists; on some Jetson setups the probe may not see the integrated
Tegra GPU. If ASR stays on CPU after the swap, this is the most likely cause —
file an issue with `tegrastats`/`nvidia-smi` output so we can extend the probe
with a Tegra check (e.g. `/etc/nv_tegra_release`).

To revert to the CPU build, restore `_internal/ctranslate2.cpu-backup` over
`_internal/ctranslate2`.
