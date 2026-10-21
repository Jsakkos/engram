# Settings Export and Import Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Export the app configuration to a versioned JSON file (secrets opt-in) and import one back through the `PUT /api/config` validators, with a per-field preview (#731).

**Architecture:** The PUT handler's validation body moves into a reusable `validate_config_update`. A new `app/services/config_transfer.py` owns the field sets, file building and per-field preview. Three thin routes in `routes.py` (gated by `require_localhost_or_lan`) call it. The frontend adds a "Backup & Transfer" settings section plus an `ImportSettingsDialog`, with an onboarding entry point.

**Tech Stack:** FastAPI + Pydantic + SQLModel (backend, `uv run pytest`), React 19 + TS + vitest/RTL (frontend, `npm run test:unit`).

**Spec:** `docs/superpowers/specs/2026-10-20-settings-export-import-design.md`

## Global Constraints

- Secrets (exactly): `makemkv_key`, `tmdb_api_key`, `tvdb_api_key`, `ai_api_key`, `opensubtitles_api_key`, `opensubtitles_password`, `discdb_api_key`, `discord_webhook_url`.
- Always excluded: `setup_complete`, `allow_lan_access`, `fingerprint_disclosure_accepted`.
- File: `format: "engram-settings"`, `schema_version: 1`, `engram_version` from `app.__version__`, `exported_at` ISO UTC, `includes_secrets` bool, `settings` object.
- No secret value in any URL, log line, or preview response. Secret rows show `"***"` or `""` only.
- Body limit 256 KB (413). Malformed file 400. Apply with any bad selected key 422, nothing written.
- Never touch the real `backend/engram.db`; tests use the existing test DB fixtures. Running servers use port 8250 / VITE 5423 with a throwaway `DATABASE_URL`.
- No em dashes in prose (docs, CHANGELOG, UI copy).

## Review Focus

1. Export file from an older build lacking newer keys: must leave those settings untouched (Task 2 test `test_apply_leaves_unmentioned_settings`).
2. File carrying `"tmdb_api_key": ""`: must not blank the stored key (Task 2 `test_apply_empty_secret_does_not_blank`).
3. Hand-edited file with a wrong type (`"max_concurrent_matches": "three"`): row invalid with a message, not a 500 (Task 2 `test_preview_type_error_is_field_error`).
4. Windows path exported, imported on Linux/Docker: flagged missing and unselected, not an error (Task 2 `test_preview_missing_path_unselected`).
5. A LAN peer with LAN access off hitting export with `include_secrets=true`: 403 (Task 3 `test_transfer_routes_refuse_lan_peer`).

---

### Task 1: Extract `validate_config_update` from the PUT handler

**Files:**
- Modify: `backend/app/api/routes.py` (`update_config`, ~2037-2210)
- Test: existing `backend/tests/unit/test_api_routes.py` and any `test_*config*` unit files (refactor guard)

**Interfaces:**
- Produces: `async def validate_config_update(update_data: dict) -> dict` in `routes.py` (module-level, below `ConfigUpdate`). Runs every check currently in `update_config` from the SSRF block through `episode_ordering_preference`, including path normalization and the library-writability check; raises the same `HTTPException`s with the same status codes and details; returns the normalized dict. Does NOT include the disclosure-timestamp stamping, the DB write, or the `resume_parked_discs` call (those stay in the PUT handler).

- [ ] **Step 1:** Run `uv run pytest tests/unit -k "config" -p no:cacheprovider -q` and record the pass count as the baseline.
- [ ] **Step 2:** Move the validation block into `validate_config_update`; `update_config` becomes: build `update_data`, `update_data = await validate_config_update(update_data)`, disclosure stamping, write, resume.
- [ ] **Step 3:** Re-run the Step 1 command. Expected: identical pass count, no failures.
- [ ] **Step 4:** `uv run ruff check . && uv run ruff format --check .` clean. Commit `refactor(config): extract validate_config_update from PUT /api/config (#731)`.

### Task 2: `config_transfer` service (field sets, export, preview, apply)

**Files:**
- Create: `backend/app/services/config_transfer.py`
- Modify: `backend/app/services/config_service.py` (replace the local `sensitive_fields` set with an import of `SECRET_FIELDS`)
- Test: `backend/tests/unit/test_config_transfer.py`

**Interfaces:**
- Consumes: `validate_config_update` (Task 1), `ConfigUpdate` (routes.py; import inside functions to avoid the cycle), `config_service.get_config` / `update_config`.
- Produces:
  - `SECRET_FIELDS: frozenset[str]`, `EXCLUDED_FIELDS: frozenset[str]`, `PATH_FIELDS: frozenset[str]` = `staging_path, library_movies_path, library_tv_path, backup_path, discdb_export_path, import_watch_path, makemkv_path, ffmpeg_path, fpcalc_path`, `NULLABLE_FIELDS = {"import_watch_path", "fingerprint_server_url"}`, `SCHEMA_VERSION = 1`, `MAX_IMPORT_BYTES = 256 * 1024`.
  - `def exportable_fields() -> list[str]`: `ConfigUpdate.model_fields` minus `EXCLUDED_FIELDS`, in declaration order.
  - `async def build_export(include_secrets: bool) -> dict`
  - `class SettingsFileError(ValueError)` (message is user-facing)
  - `def parse_settings_file(data: object) -> tuple[dict, list[str]]` returns `(settings, warnings)`; raises `SettingsFileError` for non-dict, wrong `format`, non-dict `settings`. Warning when `schema_version > SCHEMA_VERSION`.
  - `async def preview_import(data: object) -> dict` with keys `schema_version, engram_version, includes_secrets, warnings, fields, unknown`; each field `{key, status: "change"|"same"|"invalid", current, incoming, secret, path_missing, error, selected}`.
  - `class ImportRejected(Exception)` with `.errors: dict[str, str]`
  - `async def apply_import(data: object, keys: list[str]) -> list[str]` returns applied keys.

Per-field validation algorithm (`_validate_field(key, value) -> tuple[normalized | None, error | None]`): `ConfigUpdate(**{key: value})` catching `pydantic.ValidationError` (error = first error's `msg`), then `await validate_config_update({key: value})` catching `HTTPException` (error = `detail`). Skip rule before validating: empty-string secret, or `None` for a key not in `NULLABLE_FIELDS`, is treated as absent (not a row). Path existence: `os.path.exists` on the normalized value, run via `asyncio.to_thread`; `selected = status == "change" and not path_missing`.

- [ ] **Step 1: Write failing tests** in `test_config_transfer.py` (use the repo's existing unit DB fixture pattern; stub `config_service.update_config` side effects only if they touch the filesystem outside tmp):
  - `test_every_config_update_field_is_classified`: `set(ConfigUpdate.model_fields) == set(exportable_fields()) | EXCLUDED_FIELDS`, and `SECRET_FIELDS <= set(exportable_fields())`.
  - `test_export_without_secrets_omits_secrets_and_excluded`: seed `tmdb_api_key="eyJsecret"`, `staging_path=tmp`; `build_export(False)` has `format == "engram-settings"`, `schema_version == 1`, `includes_secrets is False`, no key from `SECRET_FIELDS | EXCLUDED_FIELDS` in `settings`, `settings["staging_path"] == str(tmp)`.
  - `test_export_with_secrets_includes_them`: `build_export(True)["settings"]["tmdb_api_key"] == "eyJsecret"`.
  - `test_round_trip_restores_non_secret_settings`: seed non-defaults for ~10 varied fields (paths in tmp, `extras_policy="skip"`, `naming_tv_show_format`, `discord_notify_ripped=True`, `max_concurrent_matches=4`, a Discord template), export, reset the row to `AppConfig()` defaults, `apply_import(export, exportable non-secret keys)`, assert every seeded value restored.
  - `test_preview_unknown_keys_listed_not_applied`: `settings={"future_thing": 1}` → `unknown == ["future_thing"]`, `apply_import(..., ["future_thing"])` raises `ImportRejected`.
  - `test_preview_invalid_values_use_put_messages`: `naming_episode_format="{nope}"`, `extras_policy="maybe"`, `discord_webhook_url="http://127.0.0.1/x"` → each row `status == "invalid"`, `selected is False`, `error` contains the field name or the PUT detail text.
  - `test_preview_type_error_is_field_error`: `max_concurrent_matches="three"` → invalid row, no exception.
  - `test_preview_secret_rows_carry_no_values`: stored and incoming TMDB keys differ → row `secret is True`, `status == "change"`, `current == incoming == "***"`, and the raw incoming string appears nowhere in `json.dumps(preview)`.
  - `test_preview_missing_path_unselected`: `library_tv_path="Z:\\nope\\TV"` (non-existent) → `path_missing is True`, `selected is False`, `status == "change"`.
  - `test_parse_rejects_bad_files`: non-dict, `format="other"`, `settings=[]` each raise `SettingsFileError`; `schema_version=99` yields a warning.
  - `test_apply_rejects_excluded_and_invalid_atomically`: keys `["extras_policy", "allow_lan_access"]` with valid `extras_policy="skip"` → `ImportRejected` with `allow_lan_access` in `.errors`; stored `extras_policy` unchanged.
  - `test_apply_leaves_unmentioned_settings`: seed `always_review=True`, import file without it, `always_review` still True.
  - `test_apply_empty_secret_does_not_blank`: stored TMDB key, file `"tmdb_api_key": ""`, preview has no TMDB row, key still stored.
  - `test_secrets_never_logged`: with loguru capture (see existing caplog/loguru patterns in `tests/unit`, run without `-p no:logging`), `build_export(True)` and `apply_import` with a secret leave no secret value in captured text.
- [ ] **Step 2:** `uv run pytest tests/unit/test_config_transfer.py -q`. Expected: fails on import of `app.services.config_transfer`.
- [ ] **Step 3:** Implement the module per Interfaces; point `config_service.update_config` at `SECRET_FIELDS`. Logging: key names and counts only.
- [ ] **Step 4:** Re-run Step 2 command plus Task 1's baseline command. Expected: all pass.
- [ ] **Step 5:** Ruff clean. Commit `feat(config): settings export/import service (#731)`.

### Task 3: Routes

**Files:**
- Modify: `backend/app/api/routes.py` (new routes next to `PUT /config`)
- Test: `backend/tests/unit/test_config_transfer_routes.py`

**Interfaces:**
- Consumes: Task 2 functions and exceptions.
- Produces (all `dependencies=[Depends(require_localhost_or_lan)]`):
  - `GET /api/config/export?include_secrets=false` → `JSONResponse(build_export(...))` with `Content-Disposition: attachment; filename="engram-settings-YYYY-MM-DD.json"` (`-with-keys` before `.json` when secrets included) and `Cache-Control: no-store`.
  - `POST /api/config/import/preview` raw JSON body → `preview_import` result.
  - `POST /api/config/import/apply` body `{"file": object, "keys": list[str]}` (Pydantic model `SettingsImportApply`) → `{"applied": [...]}`.
  - Body over `MAX_IMPORT_BYTES` (check `await request.body()` length) → 413; invalid JSON or `SettingsFileError` → 400 with the message; `ImportRejected` → 422 `{"detail": {"errors": {...}}}`.

- [ ] **Step 1: Write failing tests** (ASGITransport client fixture as in `test_api_routes.py`):
  - `test_export_headers`: 200, `content-disposition` contains `engram-settings-` and `.json`, `cache-control == "no-store"`; with `include_secrets=true` filename contains `-with-keys`.
  - `test_preview_endpoint_round_trip`: POST the export body → 200 with `fields` list.
  - `test_preview_bad_json_400` and `test_preview_wrong_format_400`.
  - `test_preview_oversize_413`: body of 300 KB.
  - `test_apply_endpoint_applies_and_422s`: valid keys → `applied` echoes them; an unknown key → 422 with it in `detail.errors`.
  - `test_transfer_routes_refuse_lan_peer`: client with `client=("172.18.0.14", 1234)` and `allow_lan_access=False` → 403 on all three routes (including `include_secrets=true`).
- [ ] **Step 2:** Run the file. Expected: 404s / failures.
- [ ] **Step 3:** Implement the routes.
- [ ] **Step 4:** Run the file plus Task 2's tests. Expected: pass. Ruff clean.
- [ ] **Step 5:** Commit `feat(api): settings export/import endpoints (#731)`.

### Task 4: Frontend dialog and settings section

**Files:**
- Create: `frontend/src/components/settings/settingsTransfer.ts` (API helpers + key-to-label map), `frontend/src/components/ImportSettingsDialog.tsx`, `frontend/src/components/ImportSettingsDialog.test.tsx`, `frontend/src/components/SettingsTransferSection.tsx`, `frontend/src/components/SettingsTransferSection.test.tsx`
- Modify: `frontend/src/components/ConfigWizard.tsx` (add `{ key: 'transfer', label: 'Backup & Transfer', step: 6 }` to `SETTINGS_SECTIONS` for settings mode only, render `SettingsTransferSection` at step 6; onboarding step 1 link; after apply re-run the existing load-config logic, extracted into a `reloadConfig()` callback)

**Interfaces:**
- Consumes: Task 3 endpoints.
- Produces:
  - `downloadSettings(includeSecrets: boolean): Promise<void>` (fetch → blob → object URL → temporary `<a download>`; filename from `Content-Disposition`).
  - `previewSettingsImport(file: unknown): Promise<ImportPreview>`; `applySettingsImport(file: unknown, keys: string[]): Promise<string[]>` (throws `Error` carrying the server's per-key errors on 422).
  - `settingLabel(key: string): string` (map, fallback humanizes `snake_case`).
  - `<ImportSettingsDialog file={object} preview={ImportPreview} onClose onApplied={(keys) => void} hasUnsavedEdits={boolean} />`
  - `<SettingsTransferSection onImported={() => void} />`

Copy (exact): checkbox "Include keys and passwords"; warning "Anyone with this file can use your MakeMKV, TMDB, AI and Discord credentials. Store it like a password."; onboarding link "Restoring from another install? Import a settings file"; secret row text "will be replaced"; missing path badge "Not found on this machine"; apply button "Apply N settings"; unsaved warning "Your unsaved changes in Settings will be replaced."

- [ ] **Step 1: Write failing vitest tests:**
  - Dialog: groups render (Changes, Not found on this machine, Invalid, Unchanged collapsed, Not recognized collapsed); checkboxes default from `selected`; invalid rows' checkboxes disabled and show `error`; secret row shows "will be replaced" and no `***`/value text; clicking Apply calls `applySettingsImport` with only ticked keys; button text updates with the count; `hasUnsavedEdits` shows the unsaved warning.
  - Section: ticking "Include keys and passwords" shows the warning; Download calls `fetch` with `/api/config/export?include_secrets=true`; choosing a file with invalid JSON shows an inline error without calling preview.
- [ ] **Step 2:** `npm run test:unit -- ImportSettingsDialog SettingsTransferSection`. Expected: fail (modules missing).
- [ ] **Step 3:** Implement; reuse existing modal/panel classes from neighbouring settings components.
- [ ] **Step 4:** Re-run Step 2, then full `npm run test:unit`, `npm run lint`, `npm run build`. Expected: all pass. (Worktree needs `npm install` first; do not commit lockfile changes.)
- [ ] **Step 5:** Commit `feat(settings): export/import UI (#731)`.

### Task 5: Rebase on #794, unsaved-edits wiring, docs, changelog, live check, PR

**Files:**
- Modify: `frontend/src/components/ConfigWizard.tsx` (pass #794's dirty flag as `hasUnsavedEdits`), `docs/getting-started/configuration.md`, `CHANGELOG.md`

- [ ] **Step 1:** If PR #794 has merged, `git fetch origin && git rebase origin/main`, resolve `ConfigWizard.tsx`, wire its dirty state into `hasUnsavedEdits`. If it has not merged, pass `false`, note it in the PR, and leave this step for a follow-up commit.
- [ ] **Step 2:** Docs: add "Moving settings to another install" to `configuration.md` (export, keys opt-in and its warning, never-exported fields and why, preview statuses, missing paths unticked). CHANGELOG `[Unreleased]` → `### Added`: "Export and import settings from Settings > Backup & Transfer, with keys excluded unless you opt in and a preview of every change before it applies (#731)."
- [ ] **Step 3:** Live check: backend on 8250 with `DATABASE_URL=sqlite+aiosqlite:///./engram-731.db`, frontend `VITE_PORT=5423 VITE_BACKEND_PORT=8250`; export without and with keys, import into a fresh second DB, confirm preview groups and that applied settings show in Settings. Screenshot to `artifacts/`.
- [ ] **Step 4:** Full backend unit tier in the background (`uv run pytest tests/unit -q`), ruff, frontend unit/lint/build. Kill ports 8250 and 5423 only.
- [ ] **Step 5:** Commit, push, `gh pr create` with "Fixes #731", then comment `@claude please review this PR`.
