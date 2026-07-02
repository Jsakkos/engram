#!/usr/bin/env bash
#
# jetson_gpu_setup.sh — enable CUDA-accelerated ASR for the Engram arm64 bundle
# on an NVIDIA Jetson.
#
# WHY THIS EXISTS
# ---------------
# Engram transcribes episode audio with faster-whisper / CTranslate2. On x86_64
# the PyPI `ctranslate2` wheel ships a CUDA-enabled build, so GPU "just works"
# once cuDNN/cuBLAS are present. On aarch64 the PyPI wheel is **CPU-only** — there
# is no CUDA aarch64 wheel — so the shipped `engram-linux-arm64.tar.gz` runs ASR
# on the CPU. To get GPU on a Jetson you must compile CTranslate2 from source
# against the device's JetPack CUDA toolkit and swap the result into the bundle.
#
# This script automates that. It is the on-device counterpart to the CPU-only CI
# bundle, and it must be run ON THE JETSON (it compiles for the local GPU).
#
# IMPORTANT: this path requires validation on real Jetson hardware. Treat it as a
# best-effort, well-signposted procedure rather than a turnkey guarantee.
#
# USAGE
#   ./jetson_gpu_setup.sh /path/to/extracted/engram
#
#   where the argument is the extracted bundle directory that contains the
#   `engram` launcher and the `_internal/` folder (i.e. the dir created by
#   `tar xzf engram-linux-arm64.tar.gz`).
#
# OVERRIDES (environment variables)
#   CUDA_ARCH   CUDA compute capability to target. Auto-detected when possible;
#               defaults to 87 (Orin family). Use 72 for Xavier (AGX/NX/NANO),
#               87 for Orin (AGX/NX/Nano), 53/62 for older TX/Nano.
#   CT2_VERSION CTranslate2 git tag to build. Defaults to the version baked into
#               the bundle (read from _internal/ctranslate2/version.py) so the
#               compiled C++ library matches the bundled Python bindings.
#   JOBS        Parallel build jobs (defaults to nproc).
#
set -euo pipefail

err()  { printf '\033[31merror:\033[0m %s\n' "$*" >&2; exit 1; }
info() { printf '\033[36m==>\033[0m %s\n' "$*"; }

# --- 1. Validate arguments + host -------------------------------------------
BUNDLE_DIR="${1:-}"
[ -n "$BUNDLE_DIR" ] || err "usage: $0 /path/to/extracted/engram"
BUNDLE_DIR="$(cd "$BUNDLE_DIR" && pwd)" || err "bundle dir not found: $1"
CT2_PKG_DIR="$BUNDLE_DIR/_internal/ctranslate2"
[ -d "$CT2_PKG_DIR" ] || err "not an Engram bundle (missing $CT2_PKG_DIR)"

[ "$(uname -m)" = "aarch64" ] || err "this script must run on the Jetson (aarch64); got $(uname -m)"

# A Jetson exposes its integrated GPU via the Tegra device nodes / L4T release
# file. Bail early with a clear message rather than building something unusable.
if [ ! -e /etc/nv_tegra_release ] && [ ! -e /dev/nvgpu ] && [ ! -e /dev/nvhost-gpu ]; then
  err "no Jetson/Tegra GPU detected (no /etc/nv_tegra_release or /dev/nvhost-gpu).
     This script is for NVIDIA Jetson with JetPack installed."
fi

# --- 2. Locate the JetPack CUDA toolkit -------------------------------------
CUDA_HOME="${CUDA_HOME:-/usr/local/cuda}"
[ -x "$CUDA_HOME/bin/nvcc" ] || err "CUDA toolkit not found at $CUDA_HOME (set CUDA_HOME).
     Install it with JetPack:  sudo apt-get install nvidia-jetpack"
info "CUDA toolkit: $("$CUDA_HOME/bin/nvcc" --version | sed -n 's/.*release \([0-9.]*\).*/\1/p' | tr -d '\n') at $CUDA_HOME"

# --- 3. Resolve target versions ---------------------------------------------
# Match the CTranslate2 C++ library to the Python bindings shipped in the bundle,
# otherwise the swapped-in libctranslate2.so won't match the frozen _ext module.
BUNDLED_CT2="$(sed -n 's/^__version__ *= *["'"'"']\([0-9.]*\)["'"'"'].*/\1/p' \
  "$CT2_PKG_DIR/version.py" 2>/dev/null || true)"
CT2_VERSION="${CT2_VERSION:-${BUNDLED_CT2:-4.6.3}}"
CUDA_ARCH="${CUDA_ARCH:-87}"   # Orin default; override for Xavier (72) etc.
JOBS="${JOBS:-$(nproc)}"
info "Building CTranslate2 v$CT2_VERSION for CUDA arch sm_$CUDA_ARCH (bundle has ${BUNDLED_CT2:-unknown})"
if [ -n "$BUNDLED_CT2" ] && [ "$CT2_VERSION" != "$BUNDLED_CT2" ]; then
  info "WARNING: building $CT2_VERSION but bundle ships $BUNDLED_CT2 — bindings may mismatch."
fi

# --- 4. Build dependencies ---------------------------------------------------
# CTranslate2 defaults to Intel MKL (x86-only); on aarch64 use OpenBLAS instead.
info "Installing build dependencies (sudo)..."
sudo apt-get update
sudo apt-get install -y --no-install-recommends \
  git build-essential cmake libopenblas-dev

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
cd "$WORK"

# --- 5. Build CTranslate2 with CUDA -----------------------------------------
info "Cloning CTranslate2 v$CT2_VERSION..."
git clone --depth 1 --branch "v$CT2_VERSION" --recursive \
  https://github.com/OpenNMT/CTranslate2.git
cd CTranslate2

info "Configuring (CUDA on, cuDNN on, MKL off, OpenBLAS)..."
cmake -B build -DCMAKE_BUILD_TYPE=Release \
  -DWITH_CUDA=ON -DWITH_CUDNN=ON \
  -DWITH_MKL=OFF -DWITH_OPENBLAS=ON \
  -DOPENMP_RUNTIME=COMP \
  -DCMAKE_CUDA_ARCHITECTURES="$CUDA_ARCH" \
  -DCMAKE_INSTALL_PREFIX="$WORK/ct2-install"
info "Compiling (this takes a while on a Jetson)..."
cmake --build build --config Release -j "$JOBS"
cmake --install build

# --- 6. Build the matching Python bindings ----------------------------------
# Build the bindings against the SAME CTranslate2 we just compiled so _ext.so
# links the CUDA library. We target the bundle's interpreter (cp311) via a
# disposable venv to avoid touching system Python.
info "Building Python bindings..."
# Must match the bundle's cp311 ABI — a cp310 _ext.so (JetPack 6 ships Python
# 3.10 as `python3`) won't import in the frozen 3.11 runtime. Fail loudly here
# rather than producing an ImportError after the swap.
PY311="${PYTHON:-python3.11}"
command -v "$PY311" >/dev/null || err "python3.11 is required to match the bundle's cp311 ABI \
(got $("$PY311" --version 2>&1 || echo none)). Install it (e.g. sudo apt-get install python3.11 \
python3.11-venv) or set PYTHON."
"$PY311" -m venv "$WORK/venv"
# shellcheck disable=SC1091
source "$WORK/venv/bin/activate"
pip install --upgrade pip wheel setuptools pybind11
export CTRANSLATE2_ROOT="$WORK/ct2-install"
cd python
pip wheel . -w "$WORK/wheel" --no-deps
deactivate

# --- 7. Swap the CUDA build into the Engram bundle --------------------------
# Replace the CPU-only ctranslate2 in the bundle with the freshly built CUDA one:
#   - libctranslate2.so*           -> _internal/ (next to the loader's rpath)
#   - ctranslate2/_ext*.so + *.py  -> _internal/ctranslate2/
info "Backing up the CPU-only ctranslate2 ($CT2_PKG_DIR.cpu-backup)..."
rm -rf "$CT2_PKG_DIR.cpu-backup"
cp -a "$CT2_PKG_DIR" "$CT2_PKG_DIR.cpu-backup"

UNPACK="$WORK/unpack"
mkdir -p "$UNPACK"
# A wheel is a zip; extract with the stdlib so we don't depend on `unzip` being
# installed (a fresh L4T image doesn't ship it).
WHEEL="$(echo "$WORK"/wheel/ctranslate2-*.whl)"
( cd "$UNPACK" && "$PY311" -m zipfile -e "$WHEEL" . )

info "Installing CUDA ctranslate2 into the bundle..."
# Python package files (incl. the compiled _ext*.so).
cp -a "$UNPACK"/ctranslate2/. "$CT2_PKG_DIR"/
# The shared library: prefer the one the wheel bundles; fall back to the install
# tree. Copy the whole soname set from its directory (real file + version
# symlinks, e.g. libctranslate2.so.4 -> libctranslate2.so.4.6.3) preserving
# links — _ext links against the SONAME, so dropping the symlink breaks dlopen.
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
