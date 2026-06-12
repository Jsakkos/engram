"""Shared helpers for the walk-away identity prompt (``identity_prompt_json``).

This lives in a leaf module (stdlib-only imports) deliberately: ``job_manager``
imports both ``MatchingCoordinator`` and ``FinalizationCoordinator`` at module
level, so a helper hanging off ``JobManager`` would force the coordinators into
deferred in-function imports to dodge the cycle. Both coordinators and any
future caller can import this directly.
"""

import json
from typing import Literal, TypedDict

# Prompt kinds that BLOCK matching: there is no confirmed show identity, so
# titles park in QUEUED and the rip-end convergence converts the prompt into a
# pooled review. ``"season"`` is deliberately absent — the show identity IS
# confirmed and cross-season matching proceeds without an answer. Consumers:
# JobManager._blocking_identity_prompt (fail-closed parser), the simulation
# service's QUEUED-parking mirrors, and routes' identity_pending validation.
BLOCKING_KINDS = frozenset({"name", "reidentify"})

# Resume contract between IdentificationCoordinator's answer endpoints
# (set_name_and_resume / re_identify) and JobManager._apply_identity_resume_action.
# Semantics are documented on IdentificationCoordinator.set_name_and_resume;
# only "start_rip" may spawn a rip task (the double-rip hazard).
ResumeAction = Literal[
    "start_rip",
    "dispatch_matches",
    "release_movie_titles",
    "resolve_movie",
    "rerun_matching",
]


class IdentityResumeResult(TypedDict):
    """Return shape of ``IdentificationCoordinator.set_name_and_resume``."""

    job_id: int
    resume_action: ResumeAction


class ReIdentifyResumeResult(IdentityResumeResult):
    """Return shape of ``IdentificationCoordinator.re_identify``."""

    has_ripped: bool


def mid_rip_resume_action(is_tv: bool) -> ResumeAction:
    """Resume action for a mid-rip identity answer (shared by both endpoints).

    TV → release identity-parked QUEUED titles into episode matching;
    non-TV → flip them to MATCHED and let the running rip's movie tail finish.
    """
    return "dispatch_matches" if is_tv else "release_movie_titles"


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
