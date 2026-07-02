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

> **Status: validated on real Jetson hardware** (Orin NX, JetPack 6.2.2 / Jetson
> Linux R36.5.0) by a community member. Build times on a Jetson are still long
> (tens of minutes), and JetPack/L4T versions other than 6.2.2 are untested.

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
5. Moves the PyInstaller-bundled `libstdc++.so.6*`/`libgcc_s.so.1` (from the
   Ubuntu 24.04 CI runner) to `_internal/incompatible-glibc-backup/` so Engram
   falls through to the Jetson system `libstdc++` instead of hitting
   `GLIBC_2.36`/`GLIBC_2.38 not found` on JetPack 6.2.2's Ubuntu 22.04 base.
6. Symlinks JetPack's system cuDNN/cuBLAS/cudart/nvrtc/nvJitLink libraries into
   `~/.engram/cuda/<RUNTIME_VERSION>/` with a `manifest.json`, so
   `backend/app/matcher/cuda_runtime.py`'s `is_cuda_runtime_present()` check
   (and the `/api/asr-status` `gpu_runtime_installed` field) sees the runtime as
   present without downloading anything.

JetPack already provides cuDNN and cuBLAS system-wide, so the on-demand wheel
download in `backend/app/matcher/cuda_runtime.py` (used on x86_64) is only used
as the fallback path here — step 6 above populates the same cache directly.

## Enable GPU ASR

GPU is not used just because it's detected — it must be explicitly enabled,
either in the UI (Settings → Matching → GPU Acceleration → Enable) or via the
API, then Engram must be restarted for the new device to take effect:

```bash
curl -X POST localhost:8000/api/asr/gpu/enable
# restart Engram (Ctrl-C then ./engram, or your launcher script)
```

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

If Engram fails to start (or ASR silently stays on CPU) with `GLIBC_2.36 not
found` or `GLIBC_2.38 not found` in `~/.engram/engram.log`, Engram is loading
the bundled `_internal/libstdc++.so.6`/`libgcc_s.so.1` instead of the Jetson
system copy. `jetson_gpu_setup.sh` moves these aside automatically; if you're
troubleshooting a bundle the script already ran against, re-check they're
still in `_internal/incompatible-glibc-backup/` and not restored.

To revert to the CPU build, restore `_internal/ctranslate2.cpu-backup` over
`_internal/ctranslate2`.

## Optional: a permanent launcher

To avoid re-running the setup script's printed steps on every launch, wrap them
in a small script:

```bash
mkdir -p ~/bin
cat > ~/bin/start-engram <<'EOF'
#!/bin/bash
cd "$HOME/engram" || exit 1
exec ./engram
EOF
chmod +x ~/bin/start-engram
```

Then start Engram with `~/bin/start-engram`. `jetson_gpu_setup.sh` doesn't need
`LD_LIBRARY_PATH` set manually — the CUDA cache created in step 6 above is
preloaded in-process by `register_cuda_runtime()` at startup, and moving the
bundled `libstdc++`/`libgcc_s` aside (step 5) is enough for the dynamic linker
to resolve the Jetson system copies on its own.

## USB Blu-ray/DVD drives

If MakeMKV doesn't see a USB optical drive on Jetson (`/dev/sr0` never appears),
JetPack doesn't ship the optical-drive kernel modules by default. See
[Jetson optical drive kernel modules](./jetson-optical-drive.md) for a validated
build procedure — this is a host-OS/kernel concern independent of Engram.
