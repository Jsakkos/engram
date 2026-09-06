"""What a job points MakeMKV at.

MakeMKV accepts three source forms: ``dev:<drive or device>``, ``disc:<index>``
and ``file:<path>`` (a backup folder or an ISO). Engram historically assumed the
first, so "is this source a physical drive?" was answered ad hoc with string
tests at every call site that needed eject, sentinel re-arm, drive locking or
progress labelling. This module is the single answer.

Pure apart from ``resolve_disc_index``, which shells out to makemkvcon to map a
drive letter to a MakeMKV disc index.
"""

from __future__ import annotations

import asyncio
import logging
import re
import subprocess
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

logger = logging.getLogger(__name__)

# "iso:" is Engram's own spec kind, not a MakeMKV one: MakeMKV reads an ISO
# through the same file: source as a folder. Keeping them distinct lets the UI
# and the import scanner say which one the user picked without re-stat-ing disk.
_SCHEMES = ("dev:", "disc:", "file:", "iso:")

# A bare Windows drive letter ("E:", "E:\") or a POSIX device path.
_BARE_DRIVE_RE = re.compile(r"^(?:[A-Za-z]:\\?|/dev/[A-Za-z0-9/]+)$")


class SourceKind(StrEnum):
    """What kind of thing a job reads from."""

    DRIVE = "drive"
    BACKUP = "backup"
    ISO = "iso"


@dataclass(frozen=True)
class DiscSource:
    """A MakeMKV source, and the behaviour that depends on which kind it is."""

    kind: SourceKind
    value: str
    # Which MakeMKV scheme to emit for a DRIVE. Preserved rather than recomputed
    # so a caller that resolved "E:" to "disc:0" keeps that resolution.
    _scheme: str = "dev:"

    @classmethod
    def parse(cls, spec: str) -> DiscSource:
        """Build a source from a stored spec string or a bare drive identifier."""
        if not spec:
            raise ValueError("Empty disc source spec")

        for scheme in _SCHEMES:
            if spec.startswith(scheme):
                # split on the FIRST colon only: "file:D:\backups\x" must keep
                # its Windows drive-letter colon.
                value = spec[len(scheme) :]
                if not value:
                    raise ValueError(f"Disc source spec has no value: {spec!r}")
                if scheme == "file:":
                    return cls(SourceKind.BACKUP, value)
                if scheme == "iso:":
                    return cls(SourceKind.ISO, value)
                return cls(SourceKind.DRIVE, value, scheme)

        if _BARE_DRIVE_RE.match(spec):
            return cls(SourceKind.DRIVE, spec, "dev:")

        raise ValueError(f"Unrecognized disc source spec: {spec!r}")

    @classmethod
    def from_job(cls, job) -> DiscSource:
        """Build a source from a DiscJob, honouring legacy rows.

        ``source_spec`` is None on every row written before this feature, where
        the source was always the drive. A manual-import job (``drive_id ==
        "import"``) has no MakeMKV source at all, so asking for one is a bug in
        the caller rather than something to paper over.
        """
        spec = getattr(job, "source_spec", None)
        if spec:
            return cls.parse(spec)
        drive_id = job.drive_id
        if not drive_id or drive_id == "import":
            raise ValueError(f"Job has no MakeMKV source: drive_id={drive_id!r}, source_spec=None")
        return cls.parse(drive_id)

    @classmethod
    def for_backup(cls, path: Path | str) -> DiscSource:
        """A source reading from a completed backup folder."""
        return cls(SourceKind.BACKUP, str(path))

    @property
    def spec(self) -> str:
        """The argument to hand makemkvcon."""
        if self.kind is SourceKind.DRIVE:
            return f"{self._scheme}{self.value}"
        if self.kind is SourceKind.ISO:
            return f"iso:{self.value}"
        return f"file:{self.value}"

    @property
    def makemkv_arg(self) -> str:
        """The argument makemkvcon actually accepts.

        An ISO is read through the same ``file:`` source as a folder; the ISO
        kind exists for Engram's own bookkeeping, not for MakeMKV's.
        """
        if self.kind is SourceKind.ISO:
            return f"file:{self.value}"
        return self.spec

    @property
    def is_physical(self) -> bool:
        """Whether this source is a real optical drive.

        Gates eject, sentinel re-arm, disc-hash computation and every other
        behaviour that only makes sense for hardware.
        """
        return self.kind is SourceKind.DRIVE

    @property
    def lock_key(self) -> str:
        """Key for the per-source MakeMKV serialization lock.

        Physical drives normalize so "E:", "dev:E:" and "dev:E:\\" contend for
        one lock. ``parse`` has already stripped the scheme by the time it
        reaches ``value``, so only the trailing separator is left to normalize.
        A file source keys on its own path instead: two makemkvcon processes
        reading different backups do not contend, and a backup on drive E: must
        not block the optical drive at E:.
        """
        if self.is_physical:
            return "drive:" + self.value.rstrip("\\")
        return "path:" + str(Path(self.value))


# makemkvcon -r info disc:9999 lists drives without touching a disc. 9999 is
# MakeMKV's documented "no such drive" index: the scan fails, but the DRV lines
# describing every drive are printed first, which is all we want.
_DRIVE_LISTING_INDEX = "disc:9999"

# DRV:index,visible,enabled,flags,"drive name","disc name","device"
# Known limitation: the quoted fields are matched with "[^"]*", so a drive or
# disc name containing an escaped quote drops that whole line. The failure is
# safe (the drive is simply missing from the mapping and the caller degrades to
# a direct rip) but silent, so look here first if one specific drive will never
# back up.
_DRV_RE = re.compile(r'^DRV:(\d+),\d+,\d+,\d+,"[^"]*","[^"]*","([^"]*)"')

_DRIVE_LISTING_TIMEOUT = 30.0


def parse_drive_listing(output: str) -> dict[str, int]:
    """Map each device identifier in a makemkvcon DRV listing to its disc index.

    Slots with an empty device string are drives MakeMKV enumerated but cannot
    use, and are omitted. A malformed line is skipped rather than raised on: a
    single unparseable row must not cost us the whole mapping.
    """
    mapping: dict[str, int] = {}
    for line in output.splitlines():
        m = _DRV_RE.match(line.strip())
        if not m:
            continue
        device = m.group(2).strip()
        if device:
            mapping[device] = int(m.group(1))
    return mapping


def _run_drive_listing(makemkv_path: str) -> str:
    """Run the drive enumeration synchronously (called via asyncio.to_thread)."""
    result = subprocess.run(
        [makemkv_path, "-r", "info", _DRIVE_LISTING_INDEX],
        capture_output=True,
        text=True,
        timeout=_DRIVE_LISTING_TIMEOUT,
        check=False,
    )
    # Non-zero is expected: index 9999 does not exist. The DRV lines we want are
    # printed before the failure, so stdout is used regardless of return code.
    return result.stdout


async def resolve_disc_index(drive: str, makemkv_path: str) -> str | None:
    """Return the ``disc:N`` spec for a drive, or None if it cannot be resolved.

    ``makemkvcon backup`` accepts only ``disc:N``, so a backup cannot start
    without this. None is a normal outcome, not an error: the caller degrades to
    a direct rip.
    """
    normalized = drive.replace("dev:", "").rstrip("\\")
    try:
        output = await asyncio.to_thread(_run_drive_listing, makemkv_path)
    except (OSError, subprocess.SubprocessError) as e:
        logger.warning(f"Could not enumerate MakeMKV drives: {e}", exc_info=True)
        return None

    mapping = parse_drive_listing(output)
    for device, index in mapping.items():
        if device.rstrip("\\").lower() == normalized.lower():
            return f"disc:{index}"

    # An empty listing and a missing drive both end in None, but they are very
    # different problems: no drives at all means MakeMKV itself is unusable
    # (missing binary, expired licence, corrupt install), which would otherwise
    # read in the logs exactly like the routine "this drive cannot be backed up"
    # case that the caller degrades from every day.
    if not mapping:
        logger.warning(
            "MakeMKV listed no drives at all; check that MakeMKV is installed "
            f"and licensed. Cannot back up from {normalized}"
        )
    else:
        logger.warning(
            f"Drive {normalized} not found in MakeMKV drive listing "
            f"(saw: {sorted(mapping)}); cannot back up"
        )
    return None
