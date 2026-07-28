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
# CI runs this script with `sudo -E` so ldconfig can write /etc/ld.so.cache.
set -euo pipefail

INSTALL_DIR="${MAKEMKV_INSTALL_DIR:-/config/makemkv}"
VERSION="${MAKEMKV_VERSION:-latest}"
DL_BASE="https://www.makemkv.com/download"
MARKER="${INSTALL_DIR}/.installed-version"

# Version-resolution failure modes, kept distinct so callers can tell an
# upstream outage (nothing we can fix) from a page-format change (our scraping
# logic is broken and needs updating). CI branches on these; see the
# "Verify MakeMKV version detection" step in .github/workflows/docker.yml.
EXIT_FETCH_FAILED=2
EXIT_PARSE_FAILED=3

# Resolves the latest MakeMKV version, or returns EXIT_FETCH_FAILED /
# EXIT_PARSE_FAILED. curl's own error goes to stderr (-S), so the log still
# shows why the fetch failed.
resolve_latest_version() {
    local page version

    # Retry transient failures before giving up. --retry-all-errors is needed
    # because makemkv.com sits behind Cloudflare, whose origin-unreachable
    # codes (520-527) are not in curl's default retryable set.
    if ! page="$(curl -fsSL --max-time 30 --retry 3 --retry-delay 5 --retry-all-errors "${DL_BASE}/")"; then
        return "${EXIT_FETCH_FAILED}"
    fi

    # The download page no longer lists Linux tarballs directly; scrape the
    # hash file link instead (e.g. makemkv-sha-1.18.3.txt) — same version
    # format, present on every release. A no-match grep exits non-zero, which
    # is the parse-failure case rather than an error worth propagating.
    version="$(printf '%s' "${page}" \
        | grep -oE 'makemkv-sha-[0-9]+\.[0-9]+\.[0-9]+\.txt' \
        | head -n1 \
        | sed -E 's/makemkv-sha-([0-9.]+)\.txt/\1/')" || version=""

    if [ -z "${version}" ]; then
        return "${EXIT_PARSE_FAILED}"
    fi

    printf '%s\n' "${version}"
}

# Detect-only mode: resolve latest version, print it, and exit. Used by CI to
# verify the download page is still parseable without running the full compile.
# Exits 2 if makemkv.com could not be reached, 3 if it was reached but no
# version could be parsed out of the page.
if [ "${MAKEMKV_DETECT_ONLY:-}" = "1" ]; then
    DETECT_STATUS=0
    DETECTED="$(resolve_latest_version)" || DETECT_STATUS=$?
    case "${DETECT_STATUS}" in
        0)
            echo "${DETECTED}"
            exit 0
            ;;
        "${EXIT_FETCH_FAILED}")
            echo "ERROR: could not reach ${DL_BASE}/ to check the latest MakeMKV version." >&2
            exit "${EXIT_FETCH_FAILED}"
            ;;
        "${EXIT_PARSE_FAILED}")
            echo "ERROR: fetched ${DL_BASE}/ but found no version in it; the download page format has changed." >&2
            exit "${EXIT_PARSE_FAILED}"
            ;;
        *)
            echo "ERROR: version detection failed unexpectedly (status ${DETECT_STATUS})." >&2
            exit 1
            ;;
    esac
fi

if [ "${VERSION}" = "latest" ]; then
    RESOLVE_STATUS=0
    VERSION="$(resolve_latest_version)" || RESOLVE_STATUS=$?
    if [ "${RESOLVE_STATUS}" -ne 0 ]; then
        if [ "${RESOLVE_STATUS}" -eq "${EXIT_FETCH_FAILED}" ]; then
            echo "ERROR: could not reach ${DL_BASE}/ to resolve the latest MakeMKV version." >&2
            echo "       makemkv.com may be down; check your network and try again." >&2
        else
            echo "ERROR: fetched ${DL_BASE}/ but found no version in it; the download page format has changed." >&2
        fi
        echo "       Set MAKEMKV_VERSION to a specific release (e.g. 1.18.1) to skip detection." >&2
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
