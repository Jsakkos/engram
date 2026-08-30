"""Understanding and checking the paths a user configures.

Engram writes into directories the user names in Settings, and those are the
settings most likely to be wrong in a way nothing notices: a typo, a share that
isn't mounted, a Docker volume owned by another uid. Left unchecked the mistake
surfaces hours later as an organize failure after a completed rip, which is the
worst possible moment to learn about it.

Network locations get first-class treatment here because they are the normal case
for a media library. On Windows that means UNC paths (``\\\\server\\share``);
elsewhere a share is an ordinary mount point and needs nothing special — but a
Windows-style UNC typed on Linux is a mistake worth naming rather than silently
treating as a relative directory.
"""

import contextlib
import os
import platform
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path

IS_WINDOWS = platform.system() == "Windows"


@dataclass
class PathStatus:
    """The answer to "will Engram actually be able to use this path?"."""

    path: str
    exists: bool
    writable: bool
    is_network: bool
    free_bytes: int | None = None
    # None when the path is usable; otherwise a sentence the user can act on.
    reason: str | None = None
    # A path that is fine to save but that Engram cannot see right now — an
    # unmounted share, typically. Distinct from an outright error: saving it is
    # reasonable, using it today is not.
    unreachable: bool = False

    @property
    def ok(self) -> bool:
        return self.exists and self.writable and self.reason is None


def is_unc_path(value: str) -> bool:
    r"""True for a Windows network path (``\\server\share\...``).

    Accepts the forward-slash spelling too (``//server/share``): it is what a
    browser address bar and most shells produce, and Windows itself accepts it.

    The slash spelling is recognised on Windows only. POSIX allows a leading
    ``//`` on an ordinary absolute path — ``//mnt/media`` names the same
    directory as ``/mnt/media`` — so reading it as a UNC path on Linux rejected a
    working path with advice to mount a share that was already mounted. The
    backslash spelling stays UNC everywhere, since it cannot be a POSIX path.
    """
    stripped = value.strip()
    if len(stripped) <= 2:
        return False
    first, second = stripped[0], stripped[1]
    if first == "\\" and second == "\\":
        return True
    if first not in "\\/" or second not in "\\/":
        return False
    return IS_WINDOWS


def normalize_user_path(value: str | None) -> str:
    r"""Clean up a path a human typed or pasted, without changing what it means.

    Handles the mundane damage of copy/paste — surrounding whitespace, the quotes
    Windows' "Copy as path" adds — then expands ``~``. UNC paths are normalized to
    backslashes on Windows so ``//server/share`` and ``\\server\share`` are stored
    identically rather than as two settings that look different and behave the
    same.
    """
    if not value:
        return ""
    cleaned = value.strip().strip('"').strip("'").strip()
    if not cleaned:
        return ""

    if is_unc_path(cleaned):
        if IS_WINDOWS:
            body = cleaned[2:].replace("/", "\\")
            # Collapse repeats inside the body; the leading pair is the marker.
            while "\\\\" in body:
                body = body.replace("\\\\", "\\")
            return "\\\\" + body.rstrip("\\")
        # Not Windows: leave it exactly as typed. Rewriting it would disguise a
        # real mistake (a UNC path cannot be opened directly on Linux/macOS);
        # describe_path names it instead.
        return cleaned

    expanded = os.path.expanduser(cleaned)
    # normpath settles the separator style (a pasted "C:/Users/me" and a typed
    # "C:\Users\me" are one setting) and resolves any "..", without touching the
    # filesystem — this must stay usable for a path that does not exist yet.
    expanded = os.path.normpath(expanded)
    # Trailing separators are noise except on a drive root ("C:\") — trimming
    # that would turn an absolute path into a relative one.
    if len(expanded) > 3:
        expanded = expanded.rstrip("\\/") or expanded
    return expanded


def describe_path(value: str | None, *, probe_write: bool = True) -> PathStatus:
    r"""Check a configured directory and explain, in one sentence, what's wrong.

    The write check is a real touch/unlink probe rather than a permissions
    calculation: on network shares and Docker volumes the mode bits routinely
    disagree with what the server can actually do, and only writing settles it.

    Never raises — a settings screen asking "is this path ok?" must always get an
    answer, even for a nonsense value.
    """
    raw = normalize_user_path(value)
    if not raw:
        return PathStatus(
            path="",
            exists=False,
            writable=False,
            is_network=False,
            reason="No path set.",
        )

    network = is_unc_path(raw)
    if network and not IS_WINDOWS:
        return PathStatus(
            path=raw,
            exists=False,
            writable=False,
            is_network=True,
            reason=(
                "Windows network paths (\\\\server\\share) can't be opened directly on this "
                "platform. Mount the share and use its mount point instead."
            ),
        )

    path = Path(raw)
    if not network and not path.is_absolute():
        return PathStatus(
            path=raw,
            exists=False,
            writable=False,
            is_network=False,
            reason="Use a full path, not a relative one — otherwise it depends on where Engram was started.",
        )

    try:
        exists = path.exists()
    except OSError as e:
        # A dead share can make even exists() fail rather than return False.
        return PathStatus(
            path=raw,
            exists=False,
            writable=False,
            is_network=network,
            unreachable=True,
            reason=f"Can't reach this location: {e.strerror or e}",
        )

    if not exists:
        if network:
            return PathStatus(
                path=raw,
                exists=False,
                writable=False,
                is_network=True,
                unreachable=True,
                reason=(
                    "This share isn't reachable right now. Check the server is on and that "
                    "Windows has credentials for it (open it once in Explorer to sign in)."
                ),
            )
        return PathStatus(
            path=raw,
            exists=False,
            writable=False,
            is_network=False,
            reason="This folder doesn't exist. Engram will create it when it first needs it.",
        )

    if not path.is_dir():
        return PathStatus(
            path=raw,
            exists=True,
            writable=False,
            is_network=network,
            reason="That's a file, not a folder.",
        )

    free_bytes: int | None = None
    with contextlib.suppress(OSError):
        free_bytes = shutil.disk_usage(str(path)).free

    if not probe_write:
        return PathStatus(
            path=raw, exists=True, writable=True, is_network=network, free_bytes=free_bytes
        )

    # Unique probe name: two checks (or a check racing an organize) must not
    # unlink each other's probe and report a good path as broken.
    probe = path / f".engram-write-test-{uuid.uuid4().hex}"
    try:
        probe.touch()
    except OSError as e:
        return PathStatus(
            path=raw,
            exists=True,
            writable=False,
            is_network=network,
            free_bytes=free_bytes,
            reason=f"Engram can't write here: {e.strerror or e}",
        )
    finally:
        with contextlib.suppress(OSError):
            probe.unlink()

    return PathStatus(
        path=raw, exists=True, writable=True, is_network=network, free_bytes=free_bytes
    )
