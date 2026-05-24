"""Bridge Engram's stored MakeMKV license key into MakeMKV's own settings file.

``makemkvcon`` reads its registration key from ``settings.conf`` (the ``app_Key``
line) — a file entirely separate from Engram's config database. The desktop build
sidesteps this because users register MakeMKV out-of-band via its GUI, but a
headless/containerized install has no GUI. This module upserts the key into
``settings.conf`` (merging, never clobbering unrelated settings) so ripping works
once the user enters their key in the Engram UI or via ``MAKEMKV_APP_KEY``.
"""

import logging
import os
import re
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

# Matches a settings.conf line that assigns app_Key, e.g.  app_Key = "T-..."
_APP_KEY_LINE = re.compile(r"^\s*app_Key\s*=", re.IGNORECASE)


def makemkv_settings_path() -> Path:
    """Return the per-OS location of MakeMKV's settings.conf."""
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(appdata) / "MakeMKV" / "settings.conf"
    # Linux and macOS both use ~/.MakeMKV/settings.conf.
    return Path.home() / ".MakeMKV" / "settings.conf"


def write_makemkv_settings(key: str, settings_path: Path | None = None) -> bool:
    """Upsert ``app_Key`` into MakeMKV's settings.conf.

    Preserves all other lines in the file. Blank keys are ignored (no-op), and a
    rewrite is skipped when the stored key already matches — both so this is safe
    to call unconditionally on startup and on every config save.

    Returns True if the file was written, False if nothing changed.
    """
    if not key or not key.strip():
        return False

    key = key.strip()
    path = settings_path or makemkv_settings_path()
    new_line = f'app_Key = "{key}"'

    original_text = path.read_text(encoding="utf-8") if path.exists() else ""
    existing_lines = original_text.splitlines()

    replaced = False
    out_lines: list[str] = []
    for line in existing_lines:
        if _APP_KEY_LINE.match(line):
            if replaced:
                continue  # Drop any duplicate app_Key lines.
            out_lines.append(new_line)
            replaced = True
        else:
            out_lines.append(line)

    if not replaced:
        out_lines.append(new_line)

    new_contents = "\n".join(out_lines) + "\n"
    if original_text == new_contents:
        return False  # Idempotent: nothing to do.

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(new_contents, encoding="utf-8")
    except OSError as e:
        logger.warning(f"Could not write MakeMKV settings to {path}: {e}", exc_info=True)
        return False

    logger.info(f"Registered MakeMKV key in {path}")
    return True
