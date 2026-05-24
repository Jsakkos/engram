#!/usr/bin/env bash
#
# First-run MakeMKV installer.
#
# Downloads the official MakeMKV source from makemkv.com and compiles it into a
# persistent directory (default /config/makemkv) so the published image never
# redistributes MakeMKV binaries. Idempotent: skips work when the requested
# version is already built. Technique adapted from jlesage/docker-makemkv.
#
# Build is CLI-only (./configure --disable-gui) so Qt is not required.
set -euo pipefail

INSTALL_DIR="${MAKEMKV_INSTALL_DIR:-/config/makemkv}"
VERSION="${MAKEMKV_VERSION:-latest}"
DL_BASE="https://www.makemkv.com/download"
MARKER="${INSTALL_DIR}/.installed-version"

resolve_latest_version() {
    # The download page lists the current tarball, e.g. makemkv-bin-1.18.1.tar.gz
    curl -fsSL "${DL_BASE}/" \
        | grep -oE 'makemkv-bin-[0-9]+\.[0-9]+\.[0-9]+\.tar\.gz' \
        | head -n1 \
        | sed -E 's/makemkv-bin-([0-9.]+)\.tar\.gz/\1/'
}

if [ "${VERSION}" = "latest" ]; then
    VERSION="$(resolve_latest_version || true)"
    if [ -z "${VERSION}" ]; then
        echo "ERROR: could not resolve the latest MakeMKV version from ${DL_BASE}/" >&2
        echo "       Set MAKEMKV_VERSION to a specific release (e.g. 1.18.1)." >&2
        exit 1
    fi
fi

if [ -x "${INSTALL_DIR}/bin/makemkvcon" ] && [ "$(cat "${MARKER}" 2>/dev/null || true)" = "${VERSION}" ]; then
    echo "MakeMKV ${VERSION} already installed at ${INSTALL_DIR}; skipping."
    exit 0
fi

echo "==> Installing MakeMKV ${VERSION} into ${INSTALL_DIR} (one-time compile, this can take a few minutes)..."
mkdir -p "${INSTALL_DIR}"

WORK="$(mktemp -d)"
trap 'rm -rf "${WORK}"' EXIT
cd "${WORK}"

for part in oss bin; do
    echo "    downloading makemkv-${part}-${VERSION}.tar.gz"
    curl -fSL -o "makemkv-${part}.tar.gz" "${DL_BASE}/makemkv-${part}-${VERSION}.tar.gz"
    tar xzf "makemkv-${part}.tar.gz"
done

# 1. Open-source part: shared libraries + makemkvcon, GUI disabled.
echo "    building makemkv-oss"
cd "${WORK}/makemkv-oss-${VERSION}"
./configure --prefix="${INSTALL_DIR}" --disable-gui
make -j"$(nproc)"
make install

# 2. Binary part: precompiled blobs + data. The Makefile prompts to accept the
#    EULA on first build; pre-creating tmp/eula_accepted answers it non-interactively.
echo "    installing makemkv-bin"
cd "${WORK}/makemkv-bin-${VERSION}"
mkdir -p tmp
echo "accepted" > tmp/eula_accepted
make install PREFIX="${INSTALL_DIR}"

echo "${VERSION}" > "${MARKER}"
echo "==> MakeMKV ${VERSION} installed at ${INSTALL_DIR}."
