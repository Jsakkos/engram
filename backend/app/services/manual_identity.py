"""Armed manual-identity payloads, keyed by optical drive.

Leaf module with stdlib-only imports, deliberately: both ``api/routes.py``
and ``services/job_manager.py`` import it, and hanging this off JobManager
would force routes into deferred in-function imports to dodge a cycle. Same
reasoning as ``identity_prompts.py``.

State is in-memory and one-shot by design. A backend restart clears every
armed drive, which is the only implicit expiry this feature has (there is no
timer). See the design spec for why that is acceptable.
"""

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class ManualIdentity:
    """A user-asserted disc identity, awaiting a disc."""

    title: str
    content_type: str  # "tv" | "movie"
    season: int | None = None
    tmdb_id: int | None = None
    disc_number: int | None = None

    def to_dict(self) -> dict:
        """JSON-safe form for the WebSocket payload."""
        return asdict(self)


class ArmStore:
    """In-memory, drive-keyed, one-shot store of armed identities."""

    def __init__(self) -> None:
        self._armed: dict[str, ManualIdentity] = {}

    def arm(self, drive_id: str, identity: ManualIdentity) -> None:
        """Arm a drive, replacing any existing payload for it."""
        self._armed[drive_id] = identity

    def peek(self, drive_id: str) -> ManualIdentity | None:
        """Read without consuming (for API validation / reconnect sync)."""
        return self._armed.get(drive_id)

    def consume(self, drive_id: str) -> ManualIdentity | None:
        """Read and clear. Called exactly once, by the disc-insert handler."""
        return self._armed.pop(drive_id, None)

    def disarm(self, drive_id: str) -> bool:
        """Clear a drive. Returns whether anything was actually armed."""
        return self._armed.pop(drive_id, None) is not None

    def all_armed(self) -> dict[str, ManualIdentity]:
        """Snapshot of every armed drive, for client reconnect sync."""
        return dict(self._armed)


arm_store = ArmStore()
