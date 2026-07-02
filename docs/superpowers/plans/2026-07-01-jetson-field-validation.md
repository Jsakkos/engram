# Jetson Field-Validation Follow-Up Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fold two real-hardware-validated fixes into `backend/scripts/jetson_gpu_setup.sh` (bundled `libstdc++`/`libgcc_s` shadowing the Jetson system libs, and the missing `~/.engram/cuda/` runtime-cache population), update `docs/development/jetson.md` from "requires validation" to field-validated, and add a new docs page distilling a contributed guide for building the optical-drive kernel modules JetPack doesn't ship (a host-OS prerequisite for USB Blu-ray/DVD drives on Jetson, unrelated to Engram's build).

**Architecture:** No CI or build-system changes — both source PDFs describe on-device, host-specific procedures that cannot be prebuilt on a generic GitHub-hosted ARM runner (no Tegra GPU, and kernel modules are tied to the exact running `uname -r`). The existing two-stage design (CPU-only CI bundle + on-device `jetson_gpu_setup.sh`) stays; this plan only closes the gap between what that script does today and what real Jetson hardware actually needed, then documents both procedures accurately.

**Tech Stack:** Bash (`jetson_gpu_setup.sh`), Markdown/MkDocs (`docs/development/`), no Python/test changes — `backend/tests/unit/test_cuda_runtime.py::test_is_cuda_runtime_present_requires_complete_manifest_and_files` already covers the manifest contract this plan relies on (verified: `Path.is_file()` follows symlinks, so the script's symlink-based cache satisfies it without any app-code change).

---

## Background (read before starting)

Two community-contributed PDFs document a working Jetson Orin NX setup (JetPack 6.2.2 / Jetson Linux R36.5.0 / kernel `5.15.185-tegra`):

1. **CUDA GPU setup guide** — validates and extends `backend/scripts/jetson_gpu_setup.sh`. It found two steps the script doesn't currently perform:
   - Move `_internal/libstdc++.so.6*` and `_internal/libgcc_s.so.1` (bundled by PyInstaller from the Ubuntu 24.04 CI runner) out of the active path, because JetPack 6.2.2's Ubuntu 22.04 base hits `GLIBC_2.36`/`GLIBC_2.38 not found` if Engram loads them instead of the Jetson system `libstdc++`.
   - Populate `~/.engram/cuda/<RUNTIME_VERSION>/` (the cache `backend/app/matcher/cuda_runtime.py` checks via `is_cuda_runtime_present()`) with symlinks to JetPack's system cuDNN/cuBLAS/cudart/nvrtc/nvJitLink libraries plus a `manifest.json`, since JetPack ships these system-wide and there's nothing to download.
   - It also surfaced that **GPU must be explicitly enabled** (`POST /api/asr/gpu/enable` or Settings → Matching → GPU Acceleration) — this is already true of the app, but neither the script's printed next-steps nor `docs/development/jetson.md`'s "Confirm GPU is active" section currently says so, so a user could do everything right and still see `device: cpu`.
2. **Optical-drive kernel module guide** — a from-source Linux kernel module build (`cdrom.ko`, `sr_mod.ko`, `sg.ko`, `udf.ko`, `isofs.ko`, `nls_utf8.ko`, optional `uas.ko`/`crc-itu-t.ko`) so a USB Blu-ray/DVD drive shows up as `/dev/sr0` on JetPack, which stock JetPack doesn't build as loadable modules. This is **not CI-buildable** — kernel modules are tied via `vermagic` to the exact `uname -r` they were built against, so a GitHub runner's kernel (or any kernel other than the user's own) produces unusable modules. It's a pure host-OS/docs concern.

Relevant existing code (read, don't re-derive):
- [`backend/scripts/jetson_gpu_setup.sh`](../../backend/scripts/jetson_gpu_setup.sh) — the on-device script this plan extends.
- [`docs/development/jetson.md`](../../docs/development/jetson.md) — the doc this plan updates.
- [`backend/app/matcher/cuda_runtime.py:51`](../../backend/app/matcher/cuda_runtime.py) — `RUNTIME_VERSION = "cudnn9.19.0.56-cublas12.9.1.4"`, the cache directory name the new script step must match exactly.
- [`backend/app/matcher/cuda_runtime.py:162-182`](../../backend/app/matcher/cuda_runtime.py) — `is_cuda_runtime_present()`: requires `<cache_dir>/manifest.json` with `"complete": true` and every name in `"files"` existing under `cache_dir` (symlinks satisfy `Path.is_file()`).
- [`backend/app/matcher/cuda_runtime.py:346-379`](../../backend/app/matcher/cuda_runtime.py) — `_preload_linux()`: globs `*.so*` in the cache dir and `ctypes.CDLL`-preloads each one with retries, so it doesn't matter which exact filenames are symlinked in as long as the manifest lists them.
- `.github/workflows/release.yml:312` (`build-linux-arm64` job) — confirms the CI-built bundle is always cp311 (pins `python-version: "3.11"`), so the script's existing `python3.11`-only assumption is correct as-is; out of scope to change.
- `README.md:89` and `docs/development/gpu.md:16-23` — both already link to `docs/development/jetson.md`; no nav changes needed (the page is intentionally linked-but-not-in-`mkdocs.yml`-nav, matching the existing pattern).

---

## Task 1: Add the glibc-backup step to `jetson_gpu_setup.sh`

**Files:**
- Modify: `backend/scripts/jetson_gpu_setup.sh`

- [ ] **Step 1: Insert the new step 8 after the CTranslate2 swap section**

In `backend/scripts/jetson_gpu_setup.sh`, find this exact block (the end of "step 7", right before the closing `cat <<EOF` epilogue):

```bash
SO_DIR="$(dirname "$(find "$UNPACK" "$WORK/ct2-install" -name 'libctranslate2.so*' | head -n1)")"
[ -d "$SO_DIR" ] || err "could not locate built libctranslate2.so"
cp -a "$SO_DIR"/libctranslate2.so* "$BUNDLE_DIR/_internal/"

cat <<EOF
```

Replace it with (inserting a new "step 8" section before the epilogue):

```bash
SO_DIR="$(dirname "$(find "$UNPACK" "$WORK/ct2-install" -name 'libctranslate2.so*' | head -n1)")"
[ -d "$SO_DIR" ] || err "could not locate built libctranslate2.so"
cp -a "$SO_DIR"/libctranslate2.so* "$BUNDLE_DIR/_internal/"

# --- 8. Back up bundled libstdc++/libgcc_s that can shadow Jetson system libs
# PyInstaller bundles _internal/libstdc++.so.6* and _internal/libgcc_s.so.1 from
# the Ubuntu 24.04 CI runner. On JetPack 6.2.2 (Ubuntu 22.04 base) these can
# shadow the Jetson system libstdc++ that the freshly-built CUDA/cuDNN libs
# expect, producing "GLIBC_2.36 not found" / "GLIBC_2.38 not found" at import
# time. Move them aside so the dynamic linker falls through to the system copy.
info "Backing up bundled libstdc++/libgcc_s that can shadow the Jetson system libs..."
GLIBC_BACKUP="$BUNDLE_DIR/_internal/incompatible-glibc-backup"
mkdir -p "$GLIBC_BACKUP"
for f in "$BUNDLE_DIR"/_internal/libstdc++.so.6 "$BUNDLE_DIR"/_internal/libstdc++.so.6.* \
         "$BUNDLE_DIR"/_internal/libgcc_s.so.1; do
  [ -e "$f" ] || continue
  mv -v "$f" "$GLIBC_BACKUP/"
done

cat <<EOF
```

- [ ] **Step 2: Verify the script still parses**

Run: `bash -n backend/scripts/jetson_gpu_setup.sh`
Expected: no output, exit code 0 (a bash syntax check — this cannot be exercised end-to-end without real Jetson hardware, so syntax validation plus the manual review in Task 3 is the available verification).

- [ ] **Step 3: Commit**

```bash
git add backend/scripts/jetson_gpu_setup.sh
git commit -m "fix(jetson): back up bundled libstdc++/libgcc_s to avoid GLIBC mismatch on JetPack 6.2.2"
```

---

## Task 2: Add the CUDA runtime cache step + update the epilogue

**Files:**
- Modify: `backend/scripts/jetson_gpu_setup.sh`

- [ ] **Step 1: Add a `RUNTIME_VERSION` constant near the other version defaults**

Find (in the "3. Resolve target versions" section):

```bash
CT2_VERSION="${CT2_VERSION:-${BUNDLED_CT2:-4.6.3}}"
CUDA_ARCH="${CUDA_ARCH:-87}"   # Orin default; override for Xavier (72) etc.
JOBS="${JOBS:-$(nproc)}"
```

Replace with:

```bash
CT2_VERSION="${CT2_VERSION:-${BUNDLED_CT2:-4.6.3}}"
CUDA_ARCH="${CUDA_ARCH:-87}"   # Orin default; override for Xavier (72) etc.
JOBS="${JOBS:-$(nproc)}"
# Must match RUNTIME_VERSION in backend/app/matcher/cuda_runtime.py — bump both
# together, or the app will look in the wrong ~/.engram/cuda/ subdirectory and
# treat the runtime as absent even after this script runs.
CUDA_RUNTIME_VERSION="cudnn9.19.0.56-cublas12.9.1.4"
```

- [ ] **Step 2: Insert the new step 9 after the (now-present) step 8**

Find the block added in Task 1 (ending with the `for f in ...; do ... done` loop) followed by the epilogue:

```bash
for f in "$BUNDLE_DIR"/_internal/libstdc++.so.6 "$BUNDLE_DIR"/_internal/libstdc++.so.6.* \
         "$BUNDLE_DIR"/_internal/libgcc_s.so.1; do
  [ -e "$f" ] || continue
  mv -v "$f" "$GLIBC_BACKUP/"
done

cat <<EOF
```

Replace with:

```bash
for f in "$BUNDLE_DIR"/_internal/libstdc++.so.6 "$BUNDLE_DIR"/_internal/libstdc++.so.6.* \
         "$BUNDLE_DIR"/_internal/libgcc_s.so.1; do
  [ -e "$f" ] || continue
  mv -v "$f" "$GLIBC_BACKUP/"
done

# --- 9. Create the Engram CUDA runtime cache from JetPack libraries ---------
# backend/app/matcher/cuda_runtime.py normally downloads cuDNN/cuBLAS wheels
# into ~/.engram/cuda/<RUNTIME_VERSION>/ and preloads every *.so it finds there
# before the first WhisperModel load. On Jetson, JetPack already ships matching
# cuDNN/cuBLAS system-wide, so symlink them into that same cache layout instead
# of downloading — this is also what flips gpu_runtime_installed to true in
# GET /api/asr-status.
CUDA_CACHE_DIR="$HOME/.engram/cuda/$CUDA_RUNTIME_VERSION"
info "Creating Engram CUDA runtime cache at $CUDA_CACHE_DIR..."
rm -rf "$CUDA_CACHE_DIR"
mkdir -p "$CUDA_CACHE_DIR"
"$PY311" - "$CUDA_CACHE_DIR" "$CUDA_HOME" <<'PY'
import glob
import json
import os
import sys
from pathlib import Path

cache = Path(sys.argv[1])
cuda_home = sys.argv[2]
patterns = [
    f"{cuda_home}/lib64/libcublas*.so*",
    f"{cuda_home}/lib64/libcudart*.so*",
    f"{cuda_home}/lib64/libnvrtc*.so*",
    f"{cuda_home}/lib64/libnvJitLink*.so*",
    "/usr/lib/aarch64-linux-gnu/libcudnn*.so*",
    "/lib/aarch64-linux-gnu/libcudnn*.so*",
]
files = []
for pattern in patterns:
    for src in glob.glob(pattern):
        src_path = Path(src)
        if not src_path.exists():
            continue
        dest = cache / src_path.name
        if dest.exists() or dest.is_symlink():
            dest.unlink()
        os.symlink(str(src_path), str(dest))
        files.append(src_path.name)
files = sorted(set(files))
if not files:
    sys.exit(f"No CUDA/cuDNN libraries found under: {', '.join(patterns)}")
(cache / "manifest.json").write_text(
    json.dumps({"version": cache.name, "files": files, "complete": True}, indent=2),
    encoding="utf-8",
)
print(f"Created Engram CUDA runtime cache with {len(files)} libraries:")
for name in files:
    print("  " + name)
PY

cat <<EOF
```

- [ ] **Step 3: Update the printed epilogue to mention the new steps and the GPU-enable call**

Find:

```bash
cat <<EOF

$(info "Done.")
GPU CTranslate2 (v$CT2_VERSION, sm_$CUDA_ARCH) installed into:
  $BUNDLE_DIR

Next steps:
  1. (Re)start Engram:        cd "$BUNDLE_DIR" && ./engram
  2. Confirm the device:      curl localhost:8000/api/asr-status   # expect device:"cuda"
  3. Run a match and watch GPU load with:  sudo tegrastats
  4. ~/.engram/engram.log should show "Loaded ... on cuda" (no "Falling back to CPU").

If ASR still reports CPU, see docs/development/jetson.md (device detection and
JetPack version notes). To revert, restore $CT2_PKG_DIR.cpu-backup.
EOF
```

Replace with:

```bash
cat <<EOF

$(info "Done.")
GPU CTranslate2 (v$CT2_VERSION, sm_$CUDA_ARCH) installed into:
  $BUNDLE_DIR

Bundled libstdc++/libgcc_s that could shadow the Jetson system libs were moved
to:
  $GLIBC_BACKUP
A CUDA runtime cache (symlinked to the JetPack cuDNN/cuBLAS libraries) was
created at:
  $CUDA_CACHE_DIR

Next steps:
  1. (Re)start Engram:        cd "$BUNDLE_DIR" && ./engram
  2. Enable GPU ASR (it is off by default even once detected):
                               curl -X POST localhost:8000/api/asr/gpu/enable
  3. Restart Engram again so the new device takes effect, then confirm:
                               curl localhost:8000/api/asr-status   # expect device:"cuda"
  4. Run a match and watch GPU load with:  sudo tegrastats
  5. ~/.engram/engram.log should show "Loaded ... on cuda" (no "Falling back to CPU").

If ASR still reports CPU, see docs/development/jetson.md (device detection and
JetPack version notes). To revert, restore $CT2_PKG_DIR.cpu-backup.
EOF
```

- [ ] **Step 4: Verify the script still parses**

Run: `bash -n backend/scripts/jetson_gpu_setup.sh`
Expected: no output, exit code 0.

- [ ] **Step 5: Dry-run the embedded Python cache-builder against fixture files**

This exercises the exact logic that will run on a real Jetson, using fake files
in place of JetPack's system libraries, and confirms the manifest it writes
satisfies the app's real `is_cuda_runtime_present()` contract. Run from the
repo root (adjust paths for your shell; this uses bash):

```bash
WORKDIR=$(mktemp -d)
mkdir -p "$WORKDIR/cuda_home/lib64" "$WORKDIR/aarch64/lib" "$WORKDIR/cache"
touch "$WORKDIR/cuda_home/lib64/libcublas.so.12" \
      "$WORKDIR/cuda_home/lib64/libcudart.so.12" \
      "$WORKDIR/aarch64/lib/libcudnn.so.9"

python3 - "$WORKDIR/cache" "$WORKDIR/cuda_home" <<'PY'
import glob, json, os, sys
from pathlib import Path
cache = Path(sys.argv[1]); cuda_home = sys.argv[2]
patterns = [
    f"{cuda_home}/lib64/libcublas*.so*",
    f"{cuda_home}/lib64/libcudart*.so*",
    f"{cuda_home}/lib64/libnvrtc*.so*",
    f"{cuda_home}/lib64/libnvJitLink*.so*",
    str(Path(sys.argv[1]).parent / "aarch64/lib/libcudnn*.so*"),
]
files = []
for pattern in patterns:
    for src in glob.glob(pattern):
        src_path = Path(src)
        dest = cache / src_path.name
        if dest.exists() or dest.is_symlink():
            dest.unlink()
        os.symlink(str(src_path), str(dest))
        files.append(src_path.name)
files = sorted(set(files))
(cache / "manifest.json").write_text(json.dumps({"version": cache.name, "files": files, "complete": True}, indent=2))
print("files:", files)
PY

cd backend && uv run python -c "
from pathlib import Path
from app.matcher.cuda_runtime import is_cuda_runtime_present
import sys
cache = Path(sys.argv[1])
assert is_cuda_runtime_present(cache) is True, 'manifest not accepted'
print('is_cuda_runtime_present ->', is_cuda_runtime_present(cache))
" "$WORKDIR/cache"
```

Expected: `is_cuda_runtime_present -> True`, proving the symlink+manifest strategy
satisfies the real app contract (not a re-implementation of it).

- [ ] **Step 6: Commit**

```bash
git add backend/scripts/jetson_gpu_setup.sh
git commit -m "feat(jetson): populate the CUDA runtime cache from JetPack libs, document GPU-enable step"
```

---

## Task 3: Update `docs/development/jetson.md` with the validated procedure

**Files:**
- Modify: `docs/development/jetson.md`

- [ ] **Step 1: Flip the status admonition from "requires validation" to field-validated**

Find:

```markdown
> **Status: requires validation on real Jetson hardware.** This is a best-effort,
> well-signposted procedure, not a turnkey guarantee. Build times on a Jetson are
> long (tens of minutes).
```

Replace with:

```markdown
> **Status: validated on real Jetson hardware** (Orin NX, JetPack 6.2.2 / Jetson
> Linux R36.5.0) by a community member. Build times on a Jetson are still long
> (tens of minutes), and JetPack/L4T versions other than 6.2.2 are untested.
```

- [ ] **Step 2: Extend the "What it does" list from 4 to 6 items**

Find:

```markdown
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
```

Replace with:

```markdown
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
```

- [ ] **Step 3: Add the missing "enable GPU" step before "Confirm GPU is active"**

Find:

```markdown
## Confirm GPU is active

```bash
curl localhost:8000/api/asr-status      # expect device:"cuda"
sudo tegrastats                         # watch GPU load while a match runs
# ~/.engram/engram.log shows "Loaded ... on cuda" (no "Falling back to CPU")
```
```

Replace with:

```markdown
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
```

- [ ] **Step 4: Add a GLIBC troubleshooting bullet + convenience launcher section**

Find:

```markdown
### If it still reports CPU

`gpu_detected()` in `cuda_runtime.py` is the raw hardware probe. On JetPack 6
`nvidia-smi` exists; on some Jetson setups the probe may not see the integrated
Tegra GPU. If ASR stays on CPU after the swap, this is the most likely cause —
file an issue with `tegrastats`/`nvidia-smi` output so we can extend the probe
with a Tegra check (e.g. `/etc/nv_tegra_release`).

To revert to the CPU build, restore `_internal/ctranslate2.cpu-backup` over
`_internal/ctranslate2`.
```

Replace with:

```markdown
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
```

- [ ] **Step 5: Commit**

```bash
git add docs/development/jetson.md
git commit -m "docs(jetson): mark GPU setup validated on real hardware, document GPU-enable step"
```

---

## Task 4: Create `docs/development/jetson-optical-drive.md`

**Files:**
- Create: `docs/development/jetson-optical-drive.md`

- [ ] **Step 1: Write the new doc page**

Create `docs/development/jetson-optical-drive.md` with this exact content:

```markdown
# Jetson: USB Blu-ray/DVD kernel modules

> **Scope:** this is a **host-OS / Linux kernel** procedure, independent of
> Engram's own build. It gets a USB Blu-ray/DVD drive recognized as `/dev/sr0`
> so MakeMKV (and therefore Engram) can see it at all. It is **not** something
> that can be automated in Engram's release CI: kernel modules are tied via
> `vermagic` to the *exact* `uname -r` they were built against, so modules built
> on any machine other than the target Jetson — including a GitHub-hosted
> runner — are unusable there. Rebuild on each device instead of copying
> `.ko` files between Jetsons, even ones on the same JetPack version, unless
> `uname -r` is identical.
>
> **Validated on:** JetPack 6.2.2 / Jetson Linux R36.5.0, kernel
> `5.15.185-tegra`, by a community member. Different JetPack/kernel versions
> will need matching NVIDIA BSP downloads (see below) and may hit different
> `CONFIG_*` defaults.

## What this covers

- Holding NVIDIA L4T kernel/BSP packages before running `apt upgrade`, so a
  routine package update doesn't silently replace the kernel your custom
  modules are built for.
- Downloading the matching NVIDIA BSP and kernel source packages.
- Building `cdrom.ko`, `sr_mod.ko`, `sg.ko`, `udf.ko`, `isofs.ko`,
  `nls_utf8.ko`, and optionally `uas.ko`/`crc-itu-t.ko`.
- Installing, loading, and testing the modules against a real drive.
- A portable backup tarball, reusable only on another Jetson with the exact
  same `uname -r`.

## Required/optional kernel modules

| Module | Purpose | Notes |
|---|---|---|
| `cdrom.ko` | Generic CD/DVD-ROM class support | Build first if `CONFIG_CDROM=m`; other modules need its symbols. |
| `sr_mod.ko` | SCSI CD/DVD/BD block device support | Creates `/dev/sr0` for optical media. |
| `sg.ko` | SCSI generic access | Useful for `sg3_utils`, diagnostics, ripping/playback tools. |
| `udf.ko` | UDF filesystem | Required for most DVD and Blu-ray data discs. |
| `isofs.ko` | ISO9660 filesystem | Required for older CD/DVD data discs. |
| `nls_utf8.ko` | UTF-8 filename support | Commonly needed for readable filenames. |
| `uas.ko` | USB Attached SCSI | Only if `CONFIG_USB_UAS=m`. Skip if built in. |
| `usb-storage.ko` | USB mass storage | Often built in — don't force it as a module if `CONFIG_USB_STORAGE=y`. |
| `crc-itu-t.ko` | CRC dependency used by some filesystems | Only exists if `CONFIG_CRC_ITU_T=m`. Skip if built in. |

## Exact R36.5.0 download links

Use the links matching your exact release — for R36.5.0:

- Jetson Linux BSP: `https://developer.download.nvidia.com/embedded/L4T/r36_Release_v5.0/release/Jetson_Linux_R36.5.0_aarch64.tbz2`
- BSP sources: `https://developer.download.nvidia.com/embedded/L4T/r36_Release_v5.0/sources/public_sources.tbz2`
- Sample root filesystem: `https://developer.download.nvidia.com/embedded/L4T/r36_Release_v5.0/release/Tegra_Linux_Sample-Root-Filesystem_R36.5.0_aarch64.tbz2`
- Release SHA hashes: `https://developer.download.nvidia.com/embedded/L4T/r36_Release_v5.0/release/release_sha_hashes.txt`

## Part 1 — `apt upgrade` without changing the kernel

Run this before any package upgrade on a system whose custom optical-drive
modules must keep matching the running kernel exactly.

```bash
mkdir -p $HOME/apt-upgrade-no-kernel-backup
uname -a | tee $HOME/apt-upgrade-no-kernel-backup/uname-before.txt
uname -r | tee $HOME/apt-upgrade-no-kernel-backup/kernel-before.txt
dpkg -l | grep -E 'nvidia-l4t|linux-image|linux-headers|linux-modules' \
  | tee $HOME/apt-upgrade-no-kernel-backup/kernel-packages-before.txt
apt-mark showhold | tee $HOME/apt-upgrade-no-kernel-backup/holds-before.txt
```

Hold the Jetson kernel/BSP packages:

```bash
for pkg in nvidia-l4t-core nvidia-l4t-kernel nvidia-l4t-kernel-dtbs \
           nvidia-l4t-kernel-headers nvidia-l4t-kernel-oot-modules \
           nvidia-l4t-kernel-oot-headers nvidia-l4t-display-kernel \
           nvidia-l4t-bootloader nvidia-l4t-initrd nvidia-l4t-jetson-io; do
  if dpkg -s "$pkg" >/dev/null 2>&1; then
    echo "Holding $pkg"; sudo apt-mark hold "$pkg"
  else
    echo "Not installed, skipping $pkg"
  fi
done
apt-mark showhold | grep nvidia-l4t
```

Simulate, and only continue if nothing kernel-related would install:

```bash
sudo apt update
sudo apt -s upgrade | tee $HOME/apt-upgrade-no-kernel-backup/upgrade-simulation.txt
grep -Ei 'Inst nvidia-l4t-kernel|Inst nvidia-l4t-kernel-dtbs|Inst nvidia-l4t-kernel-headers|Inst nvidia-l4t-kernel-oot|Inst nvidia-l4t-display-kernel|Inst nvidia-l4t-bootloader|Inst nvidia-l4t-initrd|Inst linux-image|Inst linux-headers|Inst linux-modules' \
  $HOME/apt-upgrade-no-kernel-backup/upgrade-simulation.txt \
  || echo "No kernel installs found in simulation."
```

> Only continue if the last line printed is exactly `No kernel installs found
> in simulation.`

Then run the real upgrade (never `full-upgrade`/`dist-upgrade` here — those can
still pull in a kernel change) and confirm `uname -r` is unchanged before and
after:

```bash
sudo apt upgrade
echo "Before:"; cat $HOME/apt-upgrade-no-kernel-backup/kernel-before.txt
echo "After:"; uname -r
```

## Part 2 — Build the kernel modules

Build natively on the Jetson (avoids cross-compile toolchain mistakes).

### Install build tools

```bash
sudo apt update
sudo apt install -y build-essential bc flex bison libssl-dev libelf-dev \
  dwarves zstd git wget tar xz-utils kmod
sudo apt install -y nvidia-l4t-kernel-headers
export KREL="$(uname -r)"
```

### Download and unpack the BSP/sources

```bash
mkdir -p $HOME/jp622-r3650-build
cd $HOME/jp622-r3650-build
wget -O Jetson_Linux_R36.5.0_aarch64.tbz2 \
  https://developer.download.nvidia.com/embedded/L4T/r36_Release_v5.0/release/Jetson_Linux_R36.5.0_aarch64.tbz2
wget -O public_sources.tbz2 \
  https://developer.download.nvidia.com/embedded/L4T/r36_Release_v5.0/sources/public_sources.tbz2
tar xf Jetson_Linux_R36.5.0_aarch64.tbz2
tar xf public_sources.tbz2 -C $HOME/jp622-r3650-build
cd $HOME/jp622-r3650-build/Linux_for_Tegra/source
tar xf kernel_src.tbz2
tar xf kernel_oot_modules_src.tbz2
tar xf nvidia_kernel_display_driver_source.tbz2
```

### Prepare the source tree to match the running kernel exactly

```bash
cd $HOME/jp622-r3650-build/Linux_for_Tegra/source/kernel/kernel-jammy-src
export ARCH=arm64
export KDIR="$PWD"

if [ -r /proc/config.gz ]; then
  zcat /proc/config.gz > .config
elif [ -r "/boot/config-${KREL}" ]; then
  cp "/boot/config-${KREL}" .config
else
  echo "ERROR: could not find the running kernel config"; exit 1
fi

BASE="$(make -s ARCH=arm64 kernelversion)"
SUFFIX="${KREL#$BASE}"
scripts/config --set-str CONFIG_LOCALVERSION "$SUFFIX"
echo "" > .scmversion   # prevent a stray '+' from being appended
make ARCH=arm64 olddefconfig

echo "Running kernel: $KREL"
echo "Build kernel:   $(make -s ARCH=arm64 kernelrelease)"
```

> Both lines must read exactly the same kernel string before continuing.

### Configure optical-drive support without converting built-in features to modules

```bash
set_mod_if_not_builtin() {
  local sym="$1"
  if grep -q "^${sym}=y" .config; then
    echo "${sym} is already built in; leaving it built in."
  else
    scripts/config --module "$sym"
  fi
}
set_yes_if_not_module_or_builtin() {
  local sym="$1"
  if grep -q "^${sym}=y" .config; then
    echo "${sym} is already built in."
  elif grep -q "^${sym}=m" .config; then
    echo "${sym} is already a module."
  else
    scripts/config --enable "$sym"
  fi
}

set_mod_if_not_builtin CONFIG_CDROM
set_mod_if_not_builtin CONFIG_BLK_DEV_SR
set_mod_if_not_builtin CONFIG_CHR_DEV_SG
set_mod_if_not_builtin CONFIG_UDF_FS
set_mod_if_not_builtin CONFIG_ISO9660_FS
scripts/config --enable CONFIG_JOLIET
scripts/config --enable CONFIG_ZISOFS
set_mod_if_not_builtin CONFIG_NLS_UTF8
set_mod_if_not_builtin CONFIG_CRC_ITU_T
set_yes_if_not_module_or_builtin CONFIG_USB_STORAGE
set_mod_if_not_builtin CONFIG_USB_UAS

make ARCH=arm64 olddefconfig
```

### Prepare the tree and build each module

```bash
make ARCH=arm64 prepare
make ARCH=arm64 modules_prepare

SYM=""
if [ -f "/lib/modules/${KREL}/build/Module.symvers" ]; then
  SYM="/lib/modules/${KREL}/build/Module.symvers"
else
  SYM="$(find /usr/src -path "*${KREL}*" -name Module.symvers 2>/dev/null | head -n1)"
fi
[ -n "$SYM" ] && [ -f "$SYM" ] && cp "$SYM" "$KDIR/Module.symvers"

make ARCH=arm64 -j"$(nproc)" M=drivers/cdrom modules
EXTRA_SYMS=""
[ -f "$KDIR/drivers/cdrom/Module.symvers" ] && EXTRA_SYMS="$KDIR/drivers/cdrom/Module.symvers"
make ARCH=arm64 -j"$(nproc)" M=drivers/scsi KBUILD_EXTRA_SYMBOLS="$EXTRA_SYMS" modules
make ARCH=arm64 -j"$(nproc)" M=fs/udf KBUILD_EXTRA_SYMBOLS="$EXTRA_SYMS" modules
make ARCH=arm64 -j"$(nproc)" M=fs/isofs KBUILD_EXTRA_SYMBOLS="$EXTRA_SYMS" modules
make ARCH=arm64 -j"$(nproc)" M=fs/nls modules
make ARCH=arm64 -j"$(nproc)" M=drivers/usb/storage modules
make ARCH=arm64 -j"$(nproc)" M=lib modules
```

`cdrom.ko` won't exist if `CONFIG_CDROM=y` (built in) — that's fine. Same for
any module whose `CONFIG_*` ended up `=y` instead of `=m`.

### Verify vermagic before installing anything

```bash
for ko in drivers/cdrom/cdrom.ko drivers/scsi/sr_mod.ko drivers/scsi/sg.ko \
          fs/udf/udf.ko fs/isofs/isofs.ko fs/nls/nls_utf8.ko \
          drivers/usb/storage/uas.ko drivers/usb/storage/usb-storage.ko \
          lib/crc-itu-t.ko; do
  [ -f "$ko" ] && { echo; echo "$ko"; modinfo "$ko" | grep vermagic; }
done
```

> Every module that exists must report a vermagic starting with your `uname -r`
> (e.g. `5.15.185-tegra`). If it doesn't, `CONFIG_LOCALVERSION`/`.scmversion`
> weren't set correctly — redo the "prepare the source tree" step above.

### Install, load, and enable at boot

```bash
install_ko_if_exists() {
  [ -f "$1" ] && sudo install -D -m 0644 "$1" "$2" || echo "Skipping missing/built-in: $1"
}
install_ko_if_exists drivers/cdrom/cdrom.ko "/lib/modules/${KREL}/kernel/drivers/cdrom/cdrom.ko"
install_ko_if_exists drivers/scsi/sr_mod.ko "/lib/modules/${KREL}/kernel/drivers/scsi/sr_mod.ko"
install_ko_if_exists drivers/scsi/sg.ko "/lib/modules/${KREL}/kernel/drivers/scsi/sg.ko"
install_ko_if_exists fs/udf/udf.ko "/lib/modules/${KREL}/kernel/fs/udf/udf.ko"
install_ko_if_exists fs/isofs/isofs.ko "/lib/modules/${KREL}/kernel/fs/isofs/isofs.ko"
install_ko_if_exists fs/nls/nls_utf8.ko "/lib/modules/${KREL}/kernel/fs/nls/nls_utf8.ko"
install_ko_if_exists drivers/usb/storage/uas.ko "/lib/modules/${KREL}/kernel/drivers/usb/storage/uas.ko"
install_ko_if_exists drivers/usb/storage/usb-storage.ko "/lib/modules/${KREL}/kernel/drivers/usb/storage/usb-storage.ko"
install_ko_if_exists lib/crc-itu-t.ko "/lib/modules/${KREL}/kernel/lib/crc-itu-t.ko"
sudo depmod -a "$KREL"

sudo modprobe cdrom 2>/dev/null || true
sudo modprobe sr_mod
sudo modprobe sg
sudo modprobe udf
sudo modprobe isofs
sudo modprobe nls_utf8 2>/dev/null || true
sudo modprobe uas 2>/dev/null || true
lsmod | grep -E 'cdrom|sr_mod|sg|udf|isofs|nls_utf8|uas|crc_itu_t'

sudo tee /etc/modules-load.d/optical-drive.conf >/dev/null <<'EOF'
cdrom
sr_mod
sg
udf
isofs
nls_utf8
uas
EOF
# Remove uas from the autoload list if it isn't available as a module:
modinfo uas >/dev/null 2>&1 || sudo sed -i '/^uas$/d' /etc/modules-load.d/optical-drive.conf
```

## Test the drive

```bash
sudo apt install -y lsscsi sg3-utils udftools
# Plug in the Blu-ray/DVD drive, then:
lsusb; lsscsi; lsblk -f
ls -l /dev/sr* /dev/cdrom /dev/dvd /dev/sg* 2>/dev/null
dmesg | tail -100
```

You want to see `/dev/sr0`. Mount test:

```bash
sudo mkdir -p /mnt/bluray && sudo mount -t udf -o ro /dev/sr0 /mnt/bluray && ls -la /mnt/bluray
sudo umount /mnt/bluray
```

## Part 3 — Back up for reuse on the same kernel

Only restore this tarball onto another Jetson with the **exact same `uname -r`**
— for a different kernel, rebuild instead.

```bash
KREL="$(uname -r)"
STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP="$HOME/optical-kmods-${KREL}-${STAMP}"
mkdir -p "$BACKUP/modules" "$BACKUP/config-files"
for f in "/lib/modules/${KREL}/kernel/drivers/cdrom/cdrom.ko" \
         "/lib/modules/${KREL}/kernel/drivers/scsi/sr_mod.ko" \
         "/lib/modules/${KREL}/kernel/drivers/scsi/sg.ko" \
         "/lib/modules/${KREL}/kernel/fs/udf/udf.ko" \
         "/lib/modules/${KREL}/kernel/fs/isofs/isofs.ko" \
         "/lib/modules/${KREL}/kernel/fs/nls/nls_utf8.ko" \
         "/lib/modules/${KREL}/kernel/drivers/usb/storage/uas.ko" \
         "/lib/modules/${KREL}/kernel/drivers/usb/storage/usb-storage.ko" \
         "/lib/modules/${KREL}/kernel/lib/crc-itu-t.ko"; do
  [ -f "$f" ] && sudo cp --parents "$f" "$BACKUP/modules/"
done
[ -f /etc/modules-load.d/optical-drive.conf ] && \
  sudo cp --parents /etc/modules-load.d/optical-drive.conf "$BACKUP/config-files/"
tar -czf "${BACKUP}.tar.gz" -C "$(dirname "$BACKUP")" "$(basename "$BACKUP")"
```

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `grep: .config: no such file or directory` | `.config` was never copied into the kernel source tree. | Redo the config-copy step from inside `kernel-jammy-src`. |
| `invalid module format` | Module vermagic doesn't match `uname -r`. | Fix `CONFIG_LOCALVERSION`/`.scmversion`, rebuild, recheck `modinfo vermagic`. |
| `unknown symbol` such as `register_cdrom` | `sr_mod`/`udf`/`isofs` didn't know about `cdrom`'s `Module.symvers`. | Build `cdrom` first and pass `KBUILD_EXTRA_SYMBOLS` to dependent builds. |
| `exported twice` for `usb_storage` symbols | USB storage is already built into `vmlinux`. | Leave `CONFIG_USB_STORAGE=y`; don't force `usb-storage.ko`. |
| `unknown filesystem type udf` | `udf.ko` missing, not installed, or not loaded. | Install `udf.ko`, run `depmod`, `sudo modprobe udf`. |
| `/dev/sr0` never appears | `sr_mod`/USB/SCSI path not loaded or drive not detected. | Check `lsusb`, `lsscsi`, `dmesg`; `modprobe sr_mod sg uas` as applicable. |

## See also

- [Jetson CUDA GPU setup](./jetson.md) — the on-device step for GPU-accelerated
  ASR, a separate concern from this page.
```

- [ ] **Step 2: Confirm the docs build cleanly**

Run (from the repo root):

```bash
uv run --with mkdocs-material --with "mkdocstrings[python]" mkdocs build
```

Expected: build succeeds (per project convention, expect the existing ~18
unrelated warnings but no new errors/broken-link warnings referencing
`jetson-optical-drive.md` or `jetson.md`).

- [ ] **Step 3: Commit**

```bash
git add docs/development/jetson-optical-drive.md
git commit -m "docs(jetson): add validated USB Blu-ray/DVD kernel module build guide"
```

---

## Task 5: CHANGELOG entry

**Files:**
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Add an `[Unreleased]` → `### Fixed` bullet**

Find:

```markdown
### Fixed

- **Subtitle cache directory no longer fails to create with a permission-denied warning on startup.**
```

Replace with (new bullet inserted above the existing one):

```markdown
### Fixed

- **Jetson GPU setup script now works on real hardware, not just in theory.** A community member's field validation on a Jetson Orin NX (JetPack 6.2.2 / R36.5.0) found `jetson_gpu_setup.sh` was missing two steps: it didn't move the PyInstaller-bundled `libstdc++.so.6`/`libgcc_s.so.1` aside (causing `GLIBC_2.36`/`GLIBC_2.38 not found` on JetPack's older Ubuntu 22.04 base) and didn't populate the `~/.engram/cuda/` runtime cache from JetPack's system cuDNN/cuBLAS (so `/api/asr-status` never reported `gpu_runtime_installed: true`). Both are now automated by the script; `docs/development/jetson.md` is updated to "validated," and a new `docs/development/jetson-optical-drive.md` documents building the Blu-ray/DVD kernel modules JetPack doesn't ship for USB optical drives.
- **Subtitle cache directory no longer fails to create with a permission-denied warning on startup.**
```

(Keep the rest of that bullet's existing text unchanged — only insert the new
bullet above it.)

- [ ] **Step 2: Commit**

```bash
git add CHANGELOG.md
git commit -m "docs: changelog entry for Jetson field-validation fixes"
```

- [ ] **Step 3: After opening the PR, append the PR number**

Once `gh pr create` returns a PR number, edit the new CHANGELOG bullet to end
with `(#NNN)` (matching every other entry in the file) and amend or add a
follow-up commit — don't leave it unnumbered in the final PR.

---

## Task 6: Final review

- [ ] **Step 1: Re-read the full diff**

```bash
git diff main --stat
git diff main
```

Confirm: `jetson_gpu_setup.sh` still reads top-to-bottom sensibly (step
numbers 1–9 in order, no leftover duplicate `cat <<EOF`), both docs pages
cross-link correctly, and the CHANGELOG bullet reads as prose (not a diff
artifact).

- [ ] **Step 2: Re-run the syntax and dry-run checks from Tasks 1–2 one more time against the final file**

```bash
bash -n backend/scripts/jetson_gpu_setup.sh
```

Expected: exit code 0.

- [ ] **Step 3: Hand off per `superpowers:finishing-a-development-branch`**

This plan doesn't include opening the PR itself — follow the user's normal
branch/PR workflow (feature branch, PR referencing no issue number since this
originates from a contributed guide rather than a filed issue) once all tasks
above are checked off.
