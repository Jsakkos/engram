# Settings export and import (#731)

Status: design, awaiting approval. Part of the 1.0 epic (#734), Tier 3, paired with #730
(back up `engram.db` before migrations, PR #802).

## Why

Every setting lives in the `app_config` row of `engram.db`. Moving to a new machine,
switching between the packaged build and Docker, or recovering from a lost database means
re-entering every path, preference, template and key by hand. The API only has
`GET`/`PUT /api/config`, and `GET` redacts every secret, so even a scripted copy cannot
carry keys across.

Success: export on machine A, import on a fresh install on machine B, and every non-secret
setting is back; keys come too only when the user explicitly asked for them.

## Goals

- **Export** a versioned JSON file of the configuration, secrets excluded unless the user
  ticks "include keys and passwords".
- **Import** a file through the same validation and normalization as `PUT /api/config`,
  with a per-field preview of what will change before anything is written.
- Gate both endpoints like other host-level actions.

## Non-goals

- Backing up jobs, history, show-ordering preferences, caches or the database itself (#730
  covers the DB file).
- Encrypting the export. A file with secrets is protected by where the user keeps it; the
  UI says so.
- Syncing settings between running instances.
- Settings that are not user-editable today (anything outside `ConfigUpdate`).

## Anti-goals (would make this a failure even if it works)

- A secret appearing in a URL, a log line, a preview response, or an export the user did not
  opt into.
- An import that bypasses a validator `PUT /api/config` enforces (SSRF guards on webhook and
  fingerprint URLs, AI model/base-URL checks, template and naming-format checks, library
  writability, path normalization).
- An import that silently blanks a stored secret, or resets settings the file does not
  mention.
- An import that flips machine-local security posture (LAN binding) or per-install consent
  (fingerprint disclosure) without the user doing it on this machine.

## File format

```json
{
  "format": "engram-settings",
  "schema_version": 1,
  "engram_version": "0.41.0",
  "exported_at": "2026-10-20T14:03:00Z",
  "includes_secrets": false,
  "settings": {
    "staging_path": "D:\\Engram\\staging",
    "library_tv_path": "\\\\nas\\media\\TV",
    "naming_episode_format": "{show} - S{season:02d}E{episode:02d}",
    "discord_notify_ripped": true
  }
}
```

- Filename: `engram-settings-YYYY-MM-DD.json` (`-with-keys` suffix when secrets are
  included, so the file announces what it holds).
- `settings` holds only exportable keys (below), with their stored values. Secrets are
  **absent** (not `""`) when not included.
- `schema_version` starts at 1. It is bumped only if a key changes meaning or shape; adding a
  setting does not bump it (absent keys are already handled).

### Compatibility rules

| Case | Behavior |
|------|----------|
| Key missing from the file (older export) | Setting left unchanged. Never reset to default. |
| Key this version does not know (newer export, typo) | Listed in the preview as "not recognized, will be skipped". Never applied. Not an error, so a newer file still imports. |
| Known key, invalid value | Listed with the validator's message, unticked and not selectable. Apply refuses it if sent anyway. |
| `schema_version` greater than supported | Preview shows a warning; known keys still validate field by field. |
| `format` not `engram-settings`, `settings` not an object, not JSON | 400 with a plain message. Nothing previewed. |
| Secret key present with an empty string | Treated as absent (matches `update_config`'s never-blank-a-secret rule). |
| `null` value | Applied only for the two fields PUT already lets you clear (`import_watch_path`, `fingerprint_server_url`); elsewhere treated as absent. |

`same` vs `change` compares the normalized incoming value with the stored one. Row labels in
the UI come from a small key-to-label map in the frontend, falling back to the humanized key,
so a setting added later still renders.

This reads the issue's "import rejects unknown or invalid fields with clear messages" as:
neither is ever applied, and each gets a message; the rest of the file can still import.
(My call; the alternative is all-or-nothing, see "Calls I made".)

## Which fields

Exportable = the `ConfigUpdate` fields minus a small excluded set. Defined once as a
constant next to `ConfigUpdate` so a new setting is exported by default and a unit test
fails if a `ConfigUpdate` field is neither exported nor explicitly excluded.

**Secrets** (exported only with the opt-in; same set as `config_service.update_config`'s
`sensitive_fields`, which is made a shared constant):
`makemkv_key`, `tmdb_api_key`, `tvdb_api_key`, `ai_api_key`, `opensubtitles_api_key`,
`opensubtitles_password`, `discdb_api_key`, `discord_webhook_url`.

**Excluded always** (per-install state, not preferences):

| Field | Why |
|-------|-----|
| `setup_complete` | The importing machine should still finish its own wizard. |
| `allow_lan_access` | Network exposure of this machine; must be switched on deliberately, here. Also read before uvicorn binds, so importing it would be a surprise on next restart. |
| `fingerprint_disclosure_accepted` | Consent is given per install, with its timestamp stamped server-side. |

Everything else in `ConfigUpdate` is exported: paths (library, staging, backup, export,
import watch), tool paths (`makemkv_path`, `ffmpeg_path`, `fpcalc_path`), preferences,
thresholds, timeouts, naming formats, Discord templates and toggles, AI provider and model,
DiscDB, OpenSubtitles username, fingerprint server URL.

Fields outside `ConfigUpdate` (`subtitles_cache_path`, `matcher_min_confidence`,
`precomputed_cache_*`, `ripping_stall_timeout`, `enable_fingerprint_identification`,
`contribution_pseudonym`, update-check state, disclosure timestamp) are not exported:
they are not user-editable today, and import goes through `ConfigUpdate`.

## Paths on a different machine

Path fields (library, staging, backup, DiscDB export, import watch, and the three tool
paths) are checked in the preview, after normalization: if the path does not exist on this
machine the row is flagged "not found on this machine" and **unticked by default**. The user
can tick it anyway (a NAS share not mounted yet is legitimate, matching `PUT`'s
`create=False` stance). An existing-but-unwritable library path is invalid, exactly as `PUT`
rejects it.

## Backend

### Shared validation

Extract the body of `update_config` (the PUT handler) into
`validate_config_update(update_data: dict) -> dict` in a new `app/services/config_transfer.py`
(or kept in `routes.py` if import cycles bite). It runs every existing check and the path
normalization and returns the normalized dict, raising `HTTPException` as today. `PUT
/api/config` calls it, so its behavior is unchanged and covered by the existing tests.

The preview needs every error at once rather than the first, so it validates per field:
build `ConfigUpdate(**{key: value})` (type errors become messages) then call
`validate_config_update({key: value})`, catching `HTTPException` and recording its detail.
Cross-field rules do not exist in the PUT path today, so per-field validation loses nothing.

### Endpoints (all `Depends(require_localhost_or_lan)`)

- `GET /api/config/export?include_secrets=false` returns the file as
  `application/json` with `Content-Disposition: attachment` and `Cache-Control: no-store`.
  The flag is a boolean, never a secret. Logs `Exported settings (keys included: yes/no)`,
  never values.
- `POST /api/config/import/preview` with the file JSON as the body. Returns:

  ```json
  {
    "schema_version": 1, "engram_version": "0.40.2", "includes_secrets": true,
    "warnings": ["This file was written by a newer Engram ..."],
    "fields": [
      {"key": "library_tv_path", "status": "change", "current": "...", "incoming": "...",
       "secret": false, "path_missing": true, "error": null, "selected": false},
      {"key": "tmdb_api_key", "status": "change", "current": "***", "incoming": "***",
       "secret": true, "path_missing": false, "error": null, "selected": true}
    ],
    "unknown": ["some_future_setting"]
  }
  ```

  `status` is `change`, `same`, or `invalid`. Secret rows never carry a value in either
  direction: `current`/`incoming` are `"***"` or `""`, and `same`/`change` is computed
  server-side. Nothing is written.
- `POST /api/config/import/apply` with `{"file": {...}, "keys": ["..."]}`. Stateless: the
  client re-sends the file rather than the server holding a pending import. Re-runs the full
  parse and per-field validation on the selected keys; if any selected key is unknown,
  excluded or invalid it returns 422 listing them and writes nothing. Otherwise calls
  `config_service.update_config(**normalized)` once, so the existing side effects (TMDB
  cache clear, TVDB token reset, MakeMKV `settings.conf` bridge, `ensure_paths_exist`) run.
  Returns `{"applied": [...]}`. Logs the applied key names only.

Request size: the body is a few KB; reject over 256 KB with 413 so the endpoint is not a
memory sink.

## Frontend

- **Settings mode**: a sixth section, "Import & Export", in `SETTINGS_SECTIONS`
  (settings-mode only; the onboarding stepper keeps its five steps). It holds:
  - **Export settings**: an "Include keys and passwords" checkbox (off), with a warning when
    ticked ("Anyone with this file can use your MakeMKV, TMDB, AI and Discord credentials.
    Store it like a password."), and a download button. The download is a `fetch` turned
    into a blob, so nothing sensitive is in a URL the browser history keeps.
  - **Import settings**: a file picker. Choosing a file calls preview and opens an
    `ImportSettingsDialog` listing rows grouped Changes / Not found on this machine /
    Invalid / Unchanged (collapsed) / Not recognized (collapsed), with a checkbox per
    change row, defaults from `selected`. Secret rows say "will be replaced" instead of
    showing values. "Apply N settings" calls apply, then reloads the form from
    `GET /api/config`.
- **Onboarding step 1**: a small "Restoring from another install? Import a settings file"
  link that opens the same picker and dialog, so a fresh install can import before walking
  the wizard. After applying, the wizard reloads its values and stays at step 1 so the user
  confirms paths; `setup_complete` is never imported.
- **Unsaved edits**: if the form has unsaved changes (the dirty tracking PR #794 adds), the
  import dialog's Apply button warns that those edits will be replaced by the reload. This
  part is built after #794 merges and the branch is rebased.

## Docs and changelog

- `docs/getting-started/configuration.md`: a "Moving settings to another install" section
  covering export, the keys opt-in and its warning, what is never exported, and how the
  preview treats missing paths.
- `CHANGELOG.md` `[Unreleased]` `### Added` entry referencing #731.

## Tests

Backend unit (`tests/unit/test_config_transfer.py`):
- every `ConfigUpdate` field is either exportable or in the excluded set (drift guard);
- export without secrets omits all eight secret keys and the excluded keys; with secrets
  includes them; the response never includes secrets when the flag is false; log capture
  shows no secret value in either mode;
- round trip: export from a configured DB, reset to defaults, apply all, compare every
  non-secret exportable field (the acceptance criterion);
- preview: unknown keys listed and not applied; invalid values (bad naming format, internal
  webhook URL, bad `extras_policy`, wrong type) reported with the PUT validator's message;
  secret rows carry no values; missing paths flagged and unselected; bad `format` / non-JSON
  400; oversize 413;
- apply: refuses unknown/excluded/invalid selected keys with 422 and writes nothing; leaves
  unmentioned settings and absent secrets untouched; empty-string secret does not blank;
- both endpoints 403 for a LAN peer when `allow_lan_access` is off (ASGITransport
  `client=("172.18.0.14", ...)`, per the known loopback default).

`PUT /api/config` existing tests stay green unchanged (refactor guard).

Frontend vitest: the dialog renders groups and defaults, secret rows show no values, apply
sends only ticked keys, export checkbox warning appears.

## Calls I made (please check)

1. Partial import is allowed: invalid and unknown fields are reported and skipped, valid
   ones can still be applied. Alternative: any invalid field blocks the whole file.
2. Excluded always: `setup_complete`, `allow_lan_access`, `fingerprint_disclosure_accepted`.
3. Tool paths are exported (same-OS moves benefit) but, like other paths, unticked by
   default when missing on this machine.
4. Both endpoints use `require_localhost_or_lan`, so a Docker user with LAN access on can
   export keys from another device. Alternative: secrets-included export is `require_localhost`
   only, which would make keys unexportable from a headless Docker install.
5. Onboarding gets an import entry point on step 1, not just Settings.
6. No encryption of the export.

## Amendments during implementation (2026-10-20)

Made after an automated security review of the first backend commits, and during the UI work:

- **Import bodies must be `application/json` (415 otherwise).** The routes read the raw body
  for the size cap, which skipped FastAPI's own rule of parsing JSON only when declared. A
  cross-site `text/plain` POST needs no CORS preflight, so without this any web page could
  apply a settings file.
- **The preview never touches a UNC path from the file.** Neither the existence check nor
  `validate_config_update`'s library writability probe (new `probe_paths=False`) runs for a
  `\server\share` value, so previewing a crafted file cannot make Windows authenticate to the
  host it names. Such rows carry `path_unchecked: true`, are grouped as "Network paths (not
  checked)" and are unticked. Apply still runs every check on what the user ticked.
- **A keys-included export checks the Host header**: loopback names and IP literals only.
  DNS rebinding reaches the loopback API under the attacker's host name and can then read the
  response; this is the only response that carries plaintext secrets. `dashboard_base_url` is
  deliberately not trusted as an allowlist entry, since the same page could set it through
  `PUT /api/config`. Export without keys is unaffected.
- An unset (`None`) value is left out of the export rather than written as `null`, except for
  the two clearable fields, because import reads `null` as "leave alone" elsewhere.
- Settings keys that are excluded (per-install) appear under "Not recognized" when present in a
  file, the same as keys this version does not know.
- The section is named **Import & Export** (not "Backup & Transfer", which read like the
  disc-backup feature), and the review renders inline in the Settings pane instead of as a
  second modal.
