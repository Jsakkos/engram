"""Settings export and import (#731).

An export is a versioned JSON file of every user-editable setting (the
``ConfigUpdate`` fields) minus per-install state. Secrets go in only when the
user asks. An import is previewed field by field and then applied through the
same ``validate_config_update`` that ``PUT /api/config`` uses, so a file cannot
store anything the Settings form would refuse.

Secret values never leave this module except inside an export the user opted
into: preview rows mask them in both directions and log lines carry key names
only.
"""

import asyncio
import os
from datetime import UTC, datetime

from fastapi import HTTPException
from loguru import logger
from pydantic import ValidationError

from app import __version__
from app.core.paths import IS_WINDOWS, is_unc_path, normalize_user_path
from app.services.config_service import SECRET_FIELDS, get_config, update_config

__all__ = [
    "EXCLUDED_FIELDS",
    "FILE_FORMAT",
    "MAX_IMPORT_BYTES",
    "NULLABLE_FIELDS",
    "PATH_FIELDS",
    "REVIEW_FIELDS",
    "SCHEMA_VERSION",
    "SECRET_FIELDS",
    "ImportRejected",
    "SettingsFileError",
    "apply_import",
    "build_export",
    "exportable_fields",
    "parse_settings_file",
    "preview_import",
]

FILE_FORMAT = "engram-settings"
# Bumped only when an existing key changes meaning or shape. Adding a setting
# does not bump it: a key missing from an older file is simply left alone.
SCHEMA_VERSION = 1
MAX_IMPORT_BYTES = 256 * 1024

# Per-install state, never exported or imported. setup_complete: the new
# machine still walks its own wizard. allow_lan_access: network exposure is
# switched on deliberately, on the machine it exposes. The disclosure flag:
# consent is given per install and its timestamp is stamped server-side.
EXCLUDED_FIELDS = frozenset(
    {"setup_complete", "allow_lan_access", "fingerprint_disclosure_accepted"}
)

# Checked for existence in the preview, because a file from another machine
# routinely names folders and binaries this one does not have.
PATH_FIELDS = frozenset(
    {
        "staging_path",
        "library_movies_path",
        "library_tv_path",
        "backup_path",
        "discdb_export_path",
        "import_watch_path",
        "makemkv_path",
        "ffmpeg_path",
        "fpcalc_path",
    }
)

# Settings that choose a server Engram sends keys or data to, or a program it
# runs. A shared file that changes one would pair this install's stored key
# with a host the file picked, or swap in another executable, so these rows
# are never pre-ticked: the user opts in to each after seeing the new value.
REVIEW_FIELDS = frozenset(
    {
        "discdb_api_url",
        "ai_local_base_url",
        "fingerprint_server_url",
        "makemkv_path",
        "ffmpeg_path",
        "fpcalc_path",
    }
)

# The two fields PUT /api/config lets a client clear to null. Elsewhere a null
# in the file is treated as absent.
NULLABLE_FIELDS = frozenset({"import_watch_path", "fingerprint_server_url"})


class SettingsFileError(ValueError):
    """The upload is not an Engram settings file. The message is user-facing."""


class ImportRejected(Exception):
    """One or more selected keys cannot be applied; nothing was written."""

    def __init__(self, errors: dict[str, str]):
        super().__init__(f"{len(errors)} setting(s) rejected")
        self.errors = errors


def _config_update_model():
    # Imported lazily: routes.py imports the services layer at module load.
    from app.api.routes import ConfigUpdate

    return ConfigUpdate


def exportable_fields() -> list[str]:
    """``ConfigUpdate`` fields minus per-install state, in declaration order."""
    return [k for k in _config_update_model().model_fields if k not in EXCLUDED_FIELDS]


async def build_export(include_secrets: bool) -> dict:
    """The settings file for the current configuration."""
    config = await get_config()
    settings = {}
    for key in exportable_fields():
        if key in SECRET_FIELDS and not include_secrets:
            continue
        value = getattr(config, key, None)
        # An unset value is left out rather than written as null, because import
        # reads a null as "leave alone" everywhere but the clearable fields.
        if value is None and key not in NULLABLE_FIELDS:
            continue
        settings[key] = value
    logger.info(
        f"Exported settings ({len(settings)} keys, keys and passwords included: "
        f"{'yes' if include_secrets else 'no'})"
    )
    return {
        "format": FILE_FORMAT,
        "schema_version": SCHEMA_VERSION,
        "engram_version": __version__,
        "exported_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "includes_secrets": include_secrets,
        "settings": settings,
    }


def parse_settings_file(data: object) -> tuple[dict, list[str]]:
    """Return ``(settings, warnings)`` or raise ``SettingsFileError``."""
    if not isinstance(data, dict) or data.get("format") != FILE_FORMAT:
        raise SettingsFileError("This is not an Engram settings file.")
    settings = data.get("settings")
    if not isinstance(settings, dict):
        raise SettingsFileError("The settings file has no settings in it.")
    warnings: list[str] = []
    version = data.get("schema_version")
    if isinstance(version, int) and version > SCHEMA_VERSION:
        warnings.append(
            f"This file was written by a newer Engram ({data.get('engram_version') or 'unknown'}). "
            "Settings this version knows are checked one by one; the rest are skipped."
        )
    return settings, warnings


def _is_absent(key: str, value: object) -> bool:
    """A blank secret or a stray null is "not in the file", never a clear."""
    if value is None:
        return key not in NULLABLE_FIELDS
    return key in SECRET_FIELDS and isinstance(value, str) and not value.strip()


async def _validate_field(
    key: str, value: object, *, probe_paths: bool = True
) -> tuple[object, str | None]:
    """``(normalized, None)`` or ``(None, error)``, using PUT's own checks."""
    from app.api.routes import validate_config_update

    try:
        coerced = _config_update_model()(**{key: value}).model_dump()[key]
    except ValidationError as exc:
        # msg only: the full error repr echoes the input, which may be a secret.
        return None, f"{key}: {exc.errors()[0]['msg']}"
    try:
        normalized = await validate_config_update({key: coerced}, probe_paths=probe_paths)
    except HTTPException as exc:
        return None, str(exc.detail)
    return normalized.get(key, coerced), None


def _is_network_path(key: str, value: object) -> bool:
    r"""A path field the preview must not touch. Probing a network location makes
    Windows authenticate to whatever host an uploaded file names.

    A UNC check alone is not enough: the NT namespace (``\??\UNC\host``) and
    ``\Device\Mup`` reach the network with a single leading separator. So
    anything rooted in a separator rather than a drive letter is left unchecked
    on Windows, and a leading backslash (never a real POSIX path) everywhere.
    """
    if key not in PATH_FIELDS or not isinstance(value, str):
        return False
    path = normalize_user_path(value)
    if is_unc_path(path) or path.startswith("\\"):
        return True
    return IS_WINDOWS and path.startswith("/")


def _path_missing(key: str, value: object) -> bool:
    return (
        key in PATH_FIELDS and isinstance(value, str) and bool(value) and not os.path.exists(value)
    )


async def preview_import(data: object) -> dict:
    """Per-field preview of what an import would change. Writes nothing."""
    settings, warnings = parse_settings_file(data)
    importable = set(exportable_fields())
    config = await get_config()

    fields: list[dict] = []
    unknown: list[str] = []
    for key, raw in settings.items():
        if key not in importable:
            unknown.append(key)
            continue
        if _is_absent(key, raw):
            continue
        secret = key in SECRET_FIELDS
        current = getattr(config, key, None)
        unchecked = _is_network_path(key, raw)
        normalized, error = await _validate_field(key, raw, probe_paths=not unchecked)
        path_missing = False
        if error:
            status = "invalid"
        else:
            status = "same" if normalized == current else "change"
            if not unchecked:
                path_missing = await asyncio.to_thread(_path_missing, key, normalized)
        fields.append(
            {
                "key": key,
                "status": status,
                "current": ("***" if current else "") if secret else current,
                "incoming": "***" if secret else (raw if error else normalized),
                "secret": secret,
                "path_missing": path_missing,
                "path_unchecked": unchecked and not error,
                "needs_review": key in REVIEW_FIELDS,
                "error": error,
                "selected": status == "change"
                and not path_missing
                and not unchecked
                and key not in REVIEW_FIELDS,
            }
        )

    return {
        "schema_version": data.get("schema_version"),
        "engram_version": data.get("engram_version"),
        "includes_secrets": bool(data.get("includes_secrets")),
        "warnings": warnings,
        "fields": fields,
        "unknown": unknown,
    }


async def apply_import(data: object, keys: list[str]) -> list[str]:
    """Validate the selected keys and write them in one update, or raise."""
    settings, _ = parse_settings_file(data)
    importable = set(exportable_fields())

    errors: dict[str, str] = {}
    normalized: dict[str, object] = {}
    for key in dict.fromkeys(keys):
        if key not in importable:
            errors[key] = f"{key} is not a setting that can be imported"
            continue
        if key not in settings or _is_absent(key, settings[key]):
            errors[key] = f"{key} is not in the settings file"
            continue
        value, error = await _validate_field(key, settings[key])
        if error:
            errors[key] = error
        else:
            normalized[key] = value
    if errors:
        logger.warning(f"Settings import rejected: {sorted(errors)}")
        raise ImportRejected(errors)

    if normalized:
        await update_config(**normalized)
    logger.info(f"Settings import applied: {sorted(normalized)}")
    return list(normalized)
