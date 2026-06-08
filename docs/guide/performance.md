# Performance & Hardware

Engram runs comfortably on modest hardware, but a few choices — GPU vs CPU for transcription,
where you put the staging directory, and how many matches run in parallel — make a large
difference to how fast a stack of discs gets through the pipeline. This page explains the
knobs that already exist and how to plan hardware around them.

The expensive stages are **ripping** (disk + optical drive throughput, handled by MakeMKV) and
**episode matching** (audio transcription via faster-whisper). Everything else — identification,
organization, the dashboard — is cheap by comparison.

## Hardware at a glance

These are practical recommendations, not enforced minimums. Engram will start and run on less.

| Component | Works on | Recommended | Why it matters |
|-----------|----------|-------------|----------------|
| **CPU** | Any modern 64-bit CPU | 4+ **physical** cores | More cores let you raise `max_concurrent_matches` so several titles transcribe at once. |
| **RAM** | ~4 GB | 8 GB+ | The Whisper model plus ripping buffers; more headroom helps when matching several titles in parallel. |
| **GPU** | None — CPU is fully supported | NVIDIA GPU with CUDA | Optional. Speeds up **matching only** (not ripping). Auto-detected when present. |
| **Storage (staging)** | HDD / NAS | **SSD on the same volume as your library** | Transcription re-reads each file, and same-volume organization is an instant rename instead of a full copy. |

## GPU acceleration (faster-whisper ASR)

Episode matching transcribes each ripped title with [faster-whisper](https://github.com/SYSTRAN/faster-whisper),
which runs on NVIDIA CUDA via CTranslate2. GPU transcription is several times faster than CPU for
the same model — the single biggest lever on matching throughput.

GPU acceleration is **opt-in** and available on **Windows and Linux with an NVIDIA GPU**. macOS and
AMD GPUs have no GPU path in the engine and always run on CPU (`int8`).

### Enable it

In **Settings → Matching → GPU Acceleration**:

1. When an NVIDIA GPU is detected, Engram offers a one-time **Download & enable** (after you accept
   the [NVIDIA CUDA EULA](https://docs.nvidia.com/cuda/eula/index.html)). The required cuDNN + cuBLAS
   libraries (~1.2 GB) are **not** bundled into the build — they're fetched on demand into
   `~/.engram/cuda/`, where they **persist across app updates** (like your database and subtitle
   cache).
2. The download runs in the background; you can keep using Engram while it completes.
3. **Restart the backend** to activate. Matching then runs on the GPU using the best compute type
   your card supports — `float16`, or `int8_float16`/`float32` on older GPUs (e.g. Pascal) that
   can't do `float16`. CPU continues to use `int8`.

This works in the **prebuilt standalone build** — the CUDA libraries are downloaded at runtime, not
compiled in. (Developers running from source can instead install them via pip with
`uv sync --extra gpu`; see below.)

!!! note "Docker"
    In a container, GPU access additionally requires the host NVIDIA driver, the
    [NVIDIA Container Toolkit](https://github.com/NVIDIA/nvidia-container-toolkit), and running with
    `--gpus all`. With those in place the same in-app download applies. See the
    [Docker guide](../deployment/docker.md).

### Verify which backend is active

Engram exposes the resolved ASR runtime at **`GET /api/asr-status`** (also shown as the ASR badge on
the dashboard). The `device` field is the **effective** device — what transcription actually runs on
— so the badge can't claim CUDA while silently falling back to CPU:

```bash
curl http://localhost:8000/api/asr-status
```

```json
{
  "device": "cuda",
  "compute_type": "float16",
  "model": "small",
  "workers": 4,
  "cpu_threads": null,
  "max_concurrent_matches": 4,
  "gpu_detected": true,
  "gpu_enabled": true,
  "gpu_runtime_installed": true,
  "gpu_state": "active"
}
```

`gpu_state` tells you exactly where you stand:

- `active` — running on the GPU.
- `available_not_enabled` / `available_not_installed` — an NVIDIA GPU is present but acceleration is
  off, or the runtime hasn't been downloaded yet. The dashboard badge shows **CPU · GPU available →**;
  click it to open Settings.
- `downloading` / `installing` — the CUDA runtime download is in progress.
- `unsupported_os` — macOS or another platform with no NVIDIA path.
- `unavailable` — a supported OS with no NVIDIA GPU.

### Running from source (developers)

When running from source you can install the CUDA libraries via pip instead of the in-app download:

```bash
cd backend
uv sync --extra gpu
```

Engram falls back to these pip-installed libraries automatically, so the GPU Acceleration setting
reports the runtime already installed — just enable it and restart.

### Whisper model

The matcher currently uses the **`small`** model (≈465 MB, English-capable, good accuracy on
CPU). It is hardcoded — there is no user-facing model selector yet. For reference, faster-whisper
models trade size for accuracy and speed:

| Model | Disk size | Quality | Speed | GPU |
|-------|-----------|---------|-------|-----|
| `tiny` | 75 MB | basic | fastest | not required |
| `base` | 145 MB | good | fast | not required |
| **`small`** (default) | 465 MB | better | medium | not required |
| `medium` | 1.5 GB | best | slow | recommended |
| `large-v3` | 3 GB | best | slowest | 10 GB+ VRAM |

The model downloads once on first use and is cached locally; larger models also use proportionally
more memory (VRAM on GPU, RAM on CPU).

## Storage: SSD vs HDD vs NAS

Ripped and imported MKVs land in the **staging directory** (`staging_path`) first, then get moved
into your library when a job completes.

!!! tip "Keep staging and your library on the same volume"
    Organization uses an atomic `shutil.move()`. On the **same filesystem** that's an instant
    rename — no data is copied. Across **different volumes** (e.g. staging on your system SSD,
    library on a NAS) it becomes a full copy-then-delete of every file, which is slow and uses
    temporary double space. Put `staging_path`, `library_movies_path`, and `library_tv_path` on
    the same drive when you can.

Beyond the move, transcription **reads each title's audio repeatedly** while sampling it. That
makes staging I/O latency matter:

- **SSD** — recommended for staging. Fast random reads keep matching fed.
- **HDD** — works fine; expect somewhat slower matching and (cross-volume) organization.
- **NAS / network share** — usable for the **final library**, but avoid it for **staging**:
  network latency and seek times slow both transcription and the copy fallback. If you must
  stage on a NAS, expect noticeably longer matching.

### Sizing the staging directory

Staging needs roughly **(number of discs ripping at once) × (disc size)** of free space, plus
headroom for the copy fallback if your library is on another volume. A dual-layer Blu-ray rip can
be **30–50 GB**; a DVD is a few GB. Staging is reclaimed automatically per the
`staging_cleanup_policy` setting (default: delete on success).

## Concurrency tuning

`max_concurrent_matches` (default **2**) controls how many titles transcribe in parallel. It's the
main throughput knob once ripping is done.

How it works under the hood:

- One shared Whisper model serves all matches (using faster-whisper's `num_workers`), so raising
  the value adds **CPU/RAM pressure, not duplicated model weights/VRAM**.
- The value is clamped to your hardware by `resolve_asr_runtime()`: on **CPU** it's capped at your
  physical core count (and CPU threads are divided across workers to avoid oversubscription); on
  **GPU** it's capped at 4 parallel streams.
- It also sizes the matching admission semaphore, so at most that many titles show **MATCHING** at
  once; the rest wait in **QUEUED**. (You'll see this on the dashboard during a multi-title disc or
  bulk import.)

Guidance:

- **CPU-only, multi-core:** raising it to ~4–8 (within your core count) speeds up bulk work.
- **GPU:** keep it modest to avoid VRAM spikes; the GPU cap of 4 already bounds it.

!!! note "Takes effect after a restart"
    `num_workers` is fixed when the Whisper model loads, so changes to `max_concurrent_matches`
    apply on the next **backend restart**.

## Bulk imports and large libraries

There's no special batch parallelism for big imports — many files and seasons are naturally
serialized by the matching semaphore, so steady-state matching throughput is roughly
`max_concurrent_matches` titles at a time. Practical advice for migrating a large library:

- Raise `max_concurrent_matches` to suit your cores/GPU before starting.
- Make sure the precomputed subtitle cache is in place (it ships and installs on first run) so
  matching isn't waiting on subtitle downloads.
- Kick off large imports when you don't need the machine, and watch the dashboard ASR badge to
  confirm `workers > 1`.

See the [Import Watch Folder guide](import-watch-folder.md) for how to feed Engram a whole library
of pre-ripped files.

## Troubleshooting performance

| Symptom | Likely cause | What to check |
|---------|--------------|---------------|
| Many titles **MATCHING** but only one progresses; low CPU | Only one ASR worker is active | `GET /api/asr-status` — if `workers` is 1, raise `max_concurrent_matches` and restart |
| Matching slower than expected on a capable PC | Running on CPU unexpectedly | `device` in `/api/asr-status`; verify your CUDA install / use a `--extra gpu` source build |
| Organization step is slow / uses lots of temp space | Staging and library are on different volumes | Move them onto the same filesystem so the move is an atomic rename |
| Identification stalls or is slow | Network latency to TMDB / subtitle / TheDiscDB services | This affects identification only, not ripping; check connectivity |
| Staging fills up the disk | Concurrent rips × disc size exceeds free space | Free space, or stagger rips; confirm `staging_cleanup_policy` |
