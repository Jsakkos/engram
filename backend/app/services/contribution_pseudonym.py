"""Per-install pseudonym generation for fingerprint contributions.

The pseudonym is a UUIDv4 stored in `app_config.contribution_pseudonym`. It is
intentionally not tied to any user identity; rotating it deletes the contribution
history on the server side (Phase 2). Phase 1 only needs to generate and persist it.
"""

from __future__ import annotations

import re
import uuid

_UUID_V4_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")


def generate_pseudonym() -> str:
    """Return a fresh UUIDv4 string."""
    return str(uuid.uuid4())


def validate_pseudonym(value: object) -> bool:
    """True if `value` is a syntactically-valid UUIDv4 string."""
    return isinstance(value, str) and bool(_UUID_V4_RE.match(value))
