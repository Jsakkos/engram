"""Shared helpers for the walk-away identity prompt (``identity_prompt_json``).

This lives in a leaf module (stdlib-only imports) deliberately: ``job_manager``
imports both ``MatchingCoordinator`` and ``FinalizationCoordinator`` at module
level, so a helper hanging off ``JobManager`` would force the coordinators into
deferred in-function imports to dodge the cycle. Both coordinators and any
future caller can import this directly.
"""

import json


def prompt_kind(identity_prompt_json: str | None) -> str | None:
    """Best-effort ``kind`` of a serialized identity prompt.

    Returns the prompt's ``kind`` string, or None when no prompt is set or the
    JSON is malformed / not a dict / has a non-string kind. This is NOT a
    blocking-ness judgment — ``JobManager._blocking_identity_prompt`` owns that
    (with fail-closed semantics for malformed payloads). This helper exists for
    callers that only need to recognize a specific kind (e.g. retiring a
    ``"season"`` CTA) and must never treat a malformed prompt as that kind.
    """
    if not identity_prompt_json:
        return None
    try:
        prompt = json.loads(identity_prompt_json)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(prompt, dict):
        return None
    kind = prompt.get("kind")
    return kind if isinstance(kind, str) else None
