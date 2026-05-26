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
    # The download page no longer lists Linux tarballs directly; scrape the
    # hash file link instead (e.g. makemkv-sha-1.18.3.txt) — same version
    # format, present on every release.
    curl -fsSL "${DL_BASE}/" \
        | grep -oE 'makemkv-sha-[0-9]+\.[0-9]+\.[0-9]+\.txt' \
        | head -n1 \
        | sed -E 's/makemkv-sha-([0-9.]+)\.txt/\1/'
}

# Detect-only mode: resolve latest version, print it, and exit. Used by CI to
# verify the download page is still parseable without running the full compile.
if [ "${MAKEMKV_DETECT_ONLY:-}" = "1" ]; then
    DETECTED="$(resolve_latest_version || true)"
    if [ -z "${DETECTED}" ]; then
        echo "ERROR: could not resolve latest MakeMKV version — download page format may have changed" >&2
        exit 1
    fi
    echo "${DETECTED}"
    exit 0
fi

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

# NOTE: MakeMKV publishes no checksums on its download page, so these tarballs
# are trusted on the basis of HTTPS to makemkv.com alone. Operators wanting a
# stronger guarantee can cross-check the SHA256 hashes posted in the MakeMKV
# forum release thread (https://www.makemkv.com/forum/viewtopic.php?t=1053).
for part in oss bin; do
    echo "    downloading makemkv-${part}-${VERSION}.tar.gz"
    curl -fSL -o "makemkv-${part}.tar.gz" "${DL_BASE}/makemkv-${part}-${VERSION}.tar.gz"
    tar xzf "makemkv-${part}.tar.gz"
    # Fail clearly if the archive didn't expand to the expected directory rather
    # than cd'ing into a missing/unexpected path later.
    if [ ! -d "makemkv-${part}-${VERSION}" ]; then
        echo "ERROR: makemkv-${part}-${VERSION}.tar.gz did not extract to the expected directory" >&2
        exit 1
    fi
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
