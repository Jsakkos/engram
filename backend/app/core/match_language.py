"""Which language a job's episodes are matched in (issue #241, phase 2).

A show whose TMDB ``original_language`` is not English is matched in that
language when references exist in it. Like the episode namespace, the language
rides in a ContextVar, bound beside it wherever references are read, so the
matcher's ``corpus_dir_name`` call sites, precomputed reads and audio plan pick
it up without a parameter threaded through every signature.
``asyncio.to_thread`` copies it into worker threads.

English is the default and changes nothing: no folder suffix, no new key.
Another language gets its own reference folder (``<id>@de``, after the
namespace suffix: ``<id>@tvdb@de``) and never reads the published pack, which
is English.
"""

from __future__ import annotations

import contextlib
from contextvars import ContextVar

from app.core.languages import normalize_language

ENGLISH = "en"

_language_var: ContextVar[str] = ContextVar("match_language", default=ENGLISH)

# TMDB's own codes that are not ISO 639: "cn" is Cantonese, "xx" is "no language".
_TMDB_CODES: dict[str, str | None] = {"cn": "zh", "xx": None}


def current_match_language() -> str:
    return _language_var.get()


@contextlib.contextmanager
def match_language_context(language: str | None):
    """Bind ``language`` for the enclosed code (None or an unknown code means English)."""
    token = _language_var.set(normalize_language(language) or ENGLISH)
    try:
        yield
    finally:
        _language_var.reset(token)


def language_dir_suffix() -> str:
    """Suffix for the reference-cache folder; empty for English."""
    language = current_match_language()
    return "" if language == ENGLISH else f"@{language}"


def is_local_only_corpus_dir(name: str) -> bool:
    """A reference folder that is never published, migrated or normalized.

    ``@tvdb`` (TheTVDB numbering) and ``@<lang>`` (another language) both mark
    references the published pack, which is TMDB-numbered English, must not hold.
    """
    return "@" in name


def content_language(details: dict | None) -> str | None:
    """A show's original language from its TMDB details; None when unknown."""
    raw = (details or {}).get("original_language")
    if not raw:
        return None
    code = str(raw).strip().lower()
    if code in _TMDB_CODES:
        return _TMDB_CODES[code]
    return normalize_language(code)
