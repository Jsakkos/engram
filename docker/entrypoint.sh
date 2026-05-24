#!/usr/bin/env bash
#
# Engram container entrypoint. Runs as root to set up state, install/register
# MakeMKV, and grant optical-drive access, then drops to an unprivileged user
# (PUID/PGID) via gosu before launching the server.
set -euo pipefail

PUID="${PUID:-1000}"
PGID="${PGID:-1000}"
INSTALL_DIR="${MAKEMKV_INSTALL_DIR:-/config/makemkv}"
SETTINGS="/config/.MakeMKV/settings.conf"

# 1. Persistent state directories (all under /config because HOME=/config).
mkdir -p /config/.engram /config/.MakeMKV /config/.cache/huggingface "${INSTALL_DIR}"

# 2. Create the runtime user/group at the requested PUID/PGID.
if ! getent group "${PGID}" >/dev/null 2>&1; then
    groupadd -g "${PGID}" engram
fi
if ! getent passwd "${PUID}" >/dev/null 2>&1; then
    useradd -u "${PUID}" -g "${PGID}" -d /config -s /usr/sbin/nologin engram
fi
USER_NAME="$(getent passwd "${PUID}" | cut -d: -f1)"

# 3. First-run MakeMKV compile (skips if already built for this version).
#    MAKEMKV_SKIP_INSTALL lets CI / smoke tests boot without the slow compile.
if [ -z "${MAKEMKV_SKIP_INSTALL:-}" ]; then
    if [ ! -x "${INSTALL_DIR}/bin/makemkvcon" ]; then
        if ! /usr/local/bin/install-makemkv.sh; then
            echo "WARNING: MakeMKV install failed — ripping is unavailable until resolved." >&2
        fi
    fi
fi

# Make makemkvcon discoverable on PATH and its libraries loadable.
if [ -x "${INSTALL_DIR}/bin/makemkvcon" ]; then
    ln -sf "${INSTALL_DIR}/bin/makemkvcon" /usr/local/bin/makemkvcon
    echo "${INSTALL_DIR}/lib" > /etc/ld.so.conf.d/makemkv.conf
    ldconfig
fi

# 4. Seed the MakeMKV license from MAKEMKV_APP_KEY (merge, don't clobber).
if [ -n "${MAKEMKV_APP_KEY:-}" ]; then
    touch "${SETTINGS}"
    # Rebuild the file with the new app_Key first, then the other lines. Using
    # printf '%s' (not sed) keeps the key value out of any regex/replacement
    # context, so a key with characters special to sed can't corrupt the file.
    {
        printf 'app_Key = "%s"\n' "${MAKEMKV_APP_KEY}"
        grep -vE '^[[:space:]]*app_Key[[:space:]]*=' "${SETTINGS}" || true
    } > "${SETTINGS}.tmp"
    mv "${SETTINGS}.tmp" "${SETTINGS}"
    # The server runs as PUID and (re)writes this file on startup, so it must own it.
    chown "${PUID}:${PGID}" "${SETTINGS}"
fi

# 5. Optical-drive access: the host's /dev/sr0 group GID varies, so add the
#    runtime user to whatever group owns the device node.
if [ -e /dev/sr0 ]; then
    SR_GID="$(stat -c '%g' /dev/sr0)"
    if [ "${SR_GID}" != "0" ]; then
        if ! getent group "${SR_GID}" >/dev/null 2>&1; then
            groupadd -g "${SR_GID}" optical
        fi
        SR_GROUP="$(getent group "${SR_GID}" | cut -d: -f1)"
        usermod -aG "${SR_GROUP}" "${USER_NAME}"
    fi
fi

# 6. Own the persistent state as the runtime user. A recursive chown over the
#    whole volume (whisper models, caches) is slow, so only run it when the
#    PUID/PGID change — tracked by a stamp file. Files the app creates later are
#    already owned correctly because it runs as this user.
OWNERSHIP_STAMP="/config/.ownership-${PUID}-${PGID}"
if [ ! -f "${OWNERSHIP_STAMP}" ]; then
    chown -R "${PUID}:${PGID}" /config
    touch "${OWNERSHIP_STAMP}"
fi

# 7. Drop privileges (gosu by name applies the user's supplementary groups,
#    including the optical group joined above) and exec the server.
exec gosu "${USER_NAME}" "$@"
