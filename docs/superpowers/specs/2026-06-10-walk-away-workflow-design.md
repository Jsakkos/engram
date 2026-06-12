# Walk-Away Workflow: Rip-First Deferral + Transcript Cache + Disc-Hash Recognition

## Context

[Issue #370's comment](https://github.com/Jsakkos/engram/issues/370#issuecomment-4673060578) (craigthings, 2026-06-10): the #370 fix added a **pre-rip** season prompt, so a headless-server user now visits the web app twice — once at disc insert (season prompt), once at job end (review). They propose ripping everything first and pooling all human-in-the-loop moments into a single end-of-job review: "drop a disc and walk away, check in once at the end if needed."

We agree, and go further. The friction in the commenter's version — "user still waits for matching after answering at review" — is solved by caching ASR transcripts: re-matching with corrected metadata becomes a ~ms TF-IDF operation instead of a 5–10 min Whisper re-run. Three workstreams, in order:

- **Phase A** — persistent ASR transcript cache + nested scan grid + background pre-transcription (makes deferred review *fast*)
- **Phase B** — rip-first deferral of all identity prompts (makes review *pooled*, often *zero-touch*)
- **Phase C** — disc-hash recognition in the fingerprint network (makes review *unnecessary* for discs the network has seen)

### Key facts established by exploration

- **Ripping needs almost nothing.** Only TV-vs-movie content type influences title selection; `tmdb_id`/`season` are pure matching-time inputs. Four conditions park a job in REVIEW_NEEDED *before* ripping today (`identification_coordinator.py`): (A) unreadable label, (B) TV + TMDB lookup failed, (C) same-name no-year collision, (D) the #370 unknown-season gate (`_gate_unknown_season_disc`, line 519). The ambiguous-movie flow (lines ~448-477) already rips first — precedent exists.
- **ASR transcripts are metadata-independent.** A track's Whisper output is identical regardless of candidate show/season. Today it's memoized only in-memory (`EpisodeMatcher.transcriptions`, session-only, lost on restart). Whisper is ~5–10 min/file; TF-IDF matching is ~1ms/chunk.
- **Scan grids don't nest today.** 10-point standard scan and 25-point deep re-match (`STRICT_SCAN_POINTS`) compute *different* offsets, so cached chunk transcripts can't be reused across depths without grid alignment.
- **The disc hash already flows.** `compute_content_hash` (extractor.py:147-202, BD `BDMV/STREAM` + DVD `VIDEO_TS`) runs unconditionally at insert and rides along with every fingerprint contribution; the server already counts `(pseudonym, disc_content_hash)` pairs in promotion. Missing: per-title scan metadata in the payload, a `disc_canonical` aggregate, and a lookup endpoint.

### Decisions (user-confirmed)

1. **Unknown season** → rip + auto cross-season match (machinery exists: all-seasons subtitle prefetch + `_match_across_seasons`); review only if inconclusive. Season-prompt CTA stays available as an optional shortcut.
2. **Ambiguous TV-vs-movie** → rip permissively (all titles ≥ 15 min, else longest), decide at review.
3. **One plan, phases A → B → C, each its own PR** (C is two PRs: server first, then client).

### Push-backs incorporated (vs. the original proposal)

- Cache **transcripts (text)**, not extracted audio segments: tiny (≤1 KB/chunk), skips *both* ffmpeg and Whisper; WAVs stay ephemeral temp files.
- "Index the audio tracks" needs nothing new — chromaprint blobs are already persisted per `DiscTitle`.
- "Extract remaining segments on review" requires the nested grid (below); with it, "remaining" is well-defined and the deep re-match ladder reuses everything.

---

## Phase A — Transcript cache + nested scan grid + prewarm (PR 1)

### A1. Canonical nested scan grid

`episode_identification.py` (~line 1528): replace the inline scan-point loop with a module-level `canonical_scan_points(video_duration, *, skip_initial, skip_final=120, chunk_len=30, num_points=10)`. Levels `n_k = 9·2^k + 1` → 10, 19, 37, 73, 145; each level inserts midpoints of the previous, so **every shallower level is a strict subset of every deeper one**. `num_points` snaps UP to the nearest level.

**Use integer arithmetic**: `point = skip_initial + (i * available) // (n - 1)` (multiply before divide). Rationally, level-k point `i` ≡ level-k+1 point `2i`; integer floor division preserves this exactly, where per-level float `interval` can differ in the last ulp and break the subset property after truncation. Offsets may shift ≤1 s vs today's float path — immaterial for 30 s chunks; don't write a regression test pinning today's exact float offsets.

Snap depth constants to the lattice: `STRICT_SCAN_POINTS` 25 → **37** (`matching_coordinator.py:34`); `_CONFLICT_FIXED_DEPTHS` (25, 50) → **(37, 73)** and snap the full-coverage tier to the largest level ≤ `_full_coverage_points` (`finalization_coordinator.py:132`, `:212`). Risk to note in PR: escalation passes get ~48% denser but fully reuse the shallower pass's transcripts.

### A2. Persistent transcript store

New `backend/app/matcher/transcript_store.py`, modeled on `tmdb_persistent_cache.py` (per-thread sqlite3 + WAL + init lock — matcher is sync code in threads). DB: `~/.engram/cache/transcripts.sqlite`.

```sql
CREATE TABLE transcripts (
  file_key TEXT NOT NULL,      -- sha1(f"{resolved_path}|{size_bytes}|{mtime_ns}")
  start_s INTEGER NOT NULL, duration_s INTEGER NOT NULL,
  model_key TEXT NOT NULL,     -- output-affecting ASR identity
  text TEXT NOT NULL, created_at INTEGER NOT NULL, last_used_at INTEGER NOT NULL,
  PRIMARY KEY (file_key, start_s, duration_s, model_key));
```

- `model_key`: new helper `model_output_key()` in `asr_models.py` = `f"{type}_{name}_{device}_{compute_type}"` (compute_type IS output-affecting — int8 vs float16; worker counts are NOT, keep them out).
- File identity via path+size+mtime_ns: re-rips get fresh keys automatically; no hashing of 30 GB files.
- Eviction: size-capped LRU (~100k rows, prune by `last_used_at` every ~200 puts). Do **not** evict on staging cleanup — rows are tiny, dead rows are harmless, and surviving restarts is the headline win.
- Every get/put in try/except: cache failure degrades to "just transcribe", never breaks matching.

Wire into `EpisodeMatcher`: factor the memoized-transcribe block (lines 1595-1613) into `transcribe_chunk_cached()` with lookup order L1 dict → store → ffmpeg+Whisper (write-through both). Same layering in `transcribe_full` (line ~1277) — full-file transcripts persist when produced, just not prewarmed.

### A3. Background pre-transcription ("fill the grid")

New `backend/app/services/transcription_prewarm.py`: `TranscriptionPrewarmer` with a dedicated `EpisodeMatcher` (constructor args matching `EpisodeCurator._ensure_initialized` so `model_key`s align), per-job `asyncio.Task` dict, and the `MatchingCoordinator._match_semaphore`.

- `start_for_job(job_id)`: for each ripped title's file, walk the 10-point lattice; for each missing store row: **acquire the match semaphore per chunk**, `asyncio.to_thread(transcribe_chunk_cached)`, release. Per-chunk acquisition means a live match waits at most one chunk — no priority queue needed. Idempotent (get-before-compute).
- `cancel_for_job(job_id)`: wired to `JobStateMachine.on_terminal_state` plus the entry of `re_identify` / `set_name_and_resume` / rerun-matching (the real match owns the GPU; it reuses whatever got cached).
- Full-file prewarm gated behind config flag, default **off** (grid is what re-match needs; full-file is 5–10 min of speculative GPU per file).
- Phase A triggers: TV job parks in REVIEW_NEEDED with rematchable titles (`check_job_completion`), ambiguous-movie post-rip review. Phase B adds the awaiting-identity trigger.

Config: `enable_background_pretranscription: bool = True`, `pretranscribe_full_file: bool = False` — **three-way sync** (AppConfig model + ConfigUpdate + ConfigResponse/GET + ConfigWizard) + Alembic migration with server_default (frozen builds converge via `_add_missing_columns`).

### A4. Tests

- Unit: lattice subset/coverage property tests; store round-trip, invalidation on size/mtime change, LRU prune, corrupt-DB resilience; prewarm scheduler (per-chunk acquire/release, cancellation) with stub semaphore; fake ASR model counting `transcribe()` calls — a *fresh matcher instance* (simulated restart) re-matching the same file does **zero** ASR calls.
- Integration: park a job in review with real fixture MKVs → prewarm fills exactly 10 rows → re-identify completes with mocked-ASR never invoked.
- Manual: `POST /api/simulate/insert-disc-from-staging` with real MKVs → land in review → watch `transcripts.sqlite` rows → `POST /api/jobs/{id}/re-identify` → log shows cache hits, sub-second per-title match.

---

## Phase B — Rip-first deferral of identity prompts (PR 2)

### B1. State/UX model

New nullable `DiscJob.identity_prompt_json` (`disc_job.py` + migration): `{"kind": "name"|"season"|"reidentify", "reason": "<text>"}`. Do **not** overload `review_reason` while RIPPING — too much code couples it to REVIEW_NEEDED. `identity_prompt_json` = non-blocking CTA; `review_reason` = blocking pooled review. At rip end an unanswered prompt converts into `review_reason`; answering clears it.

Gate rework in `identify_disc` (`identification_coordinator.py`):

| Today's pre-rip park | New behavior |
|---|---|
| (A) unreadable label (~:339) | prompt `kind=name`, permissive title selection, → RIPPING |
| (B) TV + tmdb_id None (~:394) | prompt `kind=name` (keep "merged without separators" literal — frontend contract), skip prefetch, → RIPPING |
| (C) no-year/ambiguous collision (~:371) | prompt `kind=reidentify` (keep `candidates_json`), skip prefetch, → RIPPING |
| (D) unknown season (`_gate_unknown_season_disc` :519) | no park: all-seasons subtitle prefetch (existing `detected_season is None` path, :585-608), prompt `kind=season` ("select a season" literal preserved) as optional shortcut, → RIPPING, cross-season matching runs automatically |

Permissive selection helper (content type UNKNOWN/ambiguous): select all titles ≥ 900 s, else longest. Ambiguous-movie flow unchanged.

### B2. Matching gate + convergence

- `JobManager._on_title_ripped` (job_manager.py:2411-2424): dispatch `match_single_file` only when identity ready (`detected_title` set AND `identity_prompt_json is None`; season may be unknown — cross-season handles it). Otherwise leave title `QUEUED` (already counts as active in `check_job_completion`, so no premature finalize). On a mid-rip answer, dispatch matching for every QUEUED title with `output_filename` (mirror the dispatch loop in `identify_from_staging`).
- `_run_ripping` post-rip (job_manager.py:~2120): **re-read `content_type` from DB** (the captured local goes stale after a mid-rip answer). Identity ready → MATCHING (today). Pending → REVIEW_NEEDED with `review_reason` from the prompt + `prewarmer.start_for_job()`.
- `set_name_and_resume` / `re_identify` accept `state == RIPPING`: update metadata (+ existing `_resolve_missing_tmdb_id`), clear prompt, kick subtitle prefetch, dispatch ripped QUEUED titles, **no state change, no new rip task** — `JobManager.set_name_and_resume` (:798-810) must branch instead of unconditionally spawning `_run_ripping` (double-rip hazard). Extend `re_identify`'s has_ripped branch: a "movie" answer routes to the multi-title movie resolution path, not MATCHING.
- Season pinning in `MatchingCoordinator._match_single_file_inner`: when `job.detected_season is None` and **≥2 MATCHED titles agree on a season with zero MATCHED disagreement**, pin `job.detected_season` (persist + broadcast) so later titles take the cheap single-season path and chromaprint prepass re-engages. Per-title "review only if inconclusive" falls out of the existing calibrated-confidence gate (< 0.7 → review) — no new threshold.

### B3. Frontend

- `Job` type gains `identity_prompt`; include it in REST jobs payload AND `broadcast_job_update` (remember the REST/WS serializer-drift hazard — both sides or the UI silently defaults).
- `promptSelection.ts`: `classifyPromptJob` checks `identity_prompt.kind` first (any non-terminal state), falls back to `review_reason` substrings; `selectPromptJobs` widens to `ripping || review_needed`. `shouldAutoOpenPrompt` (only-active-job rule, P13) unchanged.
- `adapters.ts`: `promptKind` for ripping jobs; `App.tsx` routes `'reidentify'` → ReIdentifyModal; verify modals don't optimistically assume state flips to RIPPING on submit.
- `simulation_service.py`: add `identity_pending` param so sims can produce a RIPPING job carrying a prompt (sim bypasses `identify_disc`; required for E2E).

### B4. Tests

- Backend unit: each gate reaches RIPPING with the right prompt; `_on_title_ripped` gating + retroactive dispatch; mid-rip `set_name_and_resume` mutates without state change/second rip; post-rip pending → pooled REVIEW_NEEDED; season pinning (2-agree/0-disagree; conflict → no pin).
- Frontend unit: `promptSelection.test.ts` ripping-state classification + auto-open + dismissal.
- Integration: unreadable label → rip → mid-rip answer → matching → zero review stops; and no-answer → exactly one pooled REVIEW_NEEDED.
- Manual sim: insert with `identity_pending=name` → assert ripping + CTA → `set-name` mid-rip → still ripping, prompt cleared → MATCHING at rip end. Caveat (state in PR): sim bypasses `identify_disc`, so gate rewiring is covered by integration tests only.
- Risks to note: junk discs now consume rip time/staging space before identity confirmation (accepted); ambiguous-type discs rip more titles; `review_reason` is now strictly "blocking review".

---

## Phase C — Disc-hash recognition (PR 3: server repo; PR 4: engram client)

Server work on a **branch** in `C:\Github\engram-fingerprint-server` (main auto-deploys to prod).

### C1. Server (engram-fingerprint-server)

- `migrations/003_disc_recognition.sql`: `disc_contribution` (pseudonym, disc_content_hash BLOB, tmdb_id, content_type, season, canonical `titles_json` + sha256 `titles_digest`, dedupe unique index on (pseudonym, hash, digest)) and `disc_canonical` (hash PK, tmdb_id, content_type, season, titles_json, tier, unique_contributors, mean_confidence).
- `POST /v1/contribute-disc` (new route + zod schema; per-title rows mirror `DiscDbTitleMapping`: title_index, duration_seconds, size_bytes, assignment {episode|main_movie|extra|discarded, season, episode}, match_confidence, match_source). Separate endpoint, not a `/v1/contribute` field — avoids a wire_format_version bump for every client.
- `GET /v1/identify-disc?hash=<b64url>` → `{disc: null}` or `{disc: {tmdb_id, content_type, season, tier, unique_contributors, mean_confidence, titles}}`. Single indexed point read.
- `disc_promotion.ts` in the nightly cron: group by (hash, digest), latest row per pseudonym, exclude flagged contributors and mean confidence < 0.7; tiers per the existing ladder (≥3 pseudonyms + mean ≥0.85 → canonical, ≥2 → confirmed, ≥1 → candidate); conflicting digests → most-contributors wins, runner-up with ≥2 caps tier at confirmed. **Anti-feedback rule: exclude `match_source == "network_disc"` rows from counting** — a client that applied a network mapping must not confirm it.
- `/v1/forget` cascades to `disc_contribution`.
- Vitest: validation, dedupe, tiering incl. digest conflict + network_disc exclusion, identify hit/miss, forget.

### C2. Client (engram)

- **Read path**: new `backend/app/core/fingerprint_disc_classifier.py` — `identify_disc_via_network(content_hash, server_url)`, 3–5 s timeout, best-effort. Called in `_run_classification` **before** the TheDiscDB block, gated on `enable_fingerprint_identification` (same trust domain, no new flag) + `job.content_hash`.
  - `canonical`: override like the DiscDB ≥0.90 path — set content_type/tmdb_id/name, confidence 0.99, `classification_source="fingerprint_network"`, no review; convert titles → `DiscDbTitleMapping` list **filtered to titles whose (duration ±2 s, size ±1%) match a scanned title** (guards MakeMKV-version drift), feed through existing `_set_discdb_mappings` + `discdb_mappings_json` so `try_discdb_assignment` auto-assigns episodes at rip time, zero ASR. Parameterize `try_discdb_assignment`'s hardcoded `"discdb"` source → `"network_disc"`.
  - `confirmed`: identity only (skips all Phase B prompts); chromaprint/ASR verify episodes as today. `candidate`: ignore.
- **Write path**: `DiscContributionQueue` model (mirror `FingerprintContribution`) + migration. Enqueue on COMPLETED via `on_terminal_state`: require content_hash + tmdb_id + known type + ≥1 real assignment; build rows from `DiscTitle` fields; **skip enqueue when every assignment's source is `network_disc`**. Drain inside `ContributionUploader._drain` after the episode sweep — same consent gates (`enable_fingerprint_contributions` + `fingerprint_disclosure_accepted`), retry semantics, and audit JSONL (`kind: "disc"`).
- **Privacy**: disc records are release-level (pressed-disc layout), not personal; pseudonym remains the only identifier, `/v1/forget` covers it. Update JIT disclosure copy + settings text + docs privacy page; no new toggle.

### C3. Tests / verification

- Client unit: per-tier override behavior; tolerance rejection; enqueue payload golden test; network_disc skip rule; consent gating.
- Integration: mocked httpx canonical response → zero `match_single_file` dispatches, all mapped titles pre-assigned.
- Manual: `fingerprint_server_url` → local `wrangler dev`; contribute the same disc under 2–3 pseudonyms, run promotion, re-insert → no prompts, no matching. Caveat: sim jobs never get `content_hash` (sim bypasses `_create_job_for_disc`) — optionally add a `content_hash` sim param.

---

## What does NOT change

Chromaprint prepass + 0.90 gate; organizer; review-queue episode-assignment UX (only adds identity/type prompts, removes pre-rip parks); subtitle download mechanics (only *when* prefetch starts); episode fingerprint contributions; pack serving; `compute_content_hash`.

## Verification (end-to-end, after all phases)

1. Backend `uv run pytest` (unit/integration/pipeline tiers), frontend `npm run test:unit` + `npm run test:e2e` (DEBUG=true backend; use worktree-isolated ports per CLAUDE.md).
2. Walk-away scenario, real or staged disc: insert season-less multi-season disc → rips with CTA visible, no modal block → no user action → single REVIEW_NEEDED at end (or zero if cross-season decisive) → answer season → titles match in seconds (transcript cache hits in log).
3. Mid-rip answer scenario: answer the CTA during rip → matching dispatches for already-ripped titles → job completes with zero review stops.
4. Network scenario (Phase C): second insert of a community-known disc → identified + episode-assigned with no prompts and no ASR.
5. Kill this session's servers before each PR (CLAUDE.md rule).

## Follow-ups (out of scope)

- Reply on issue #370 summarizing the direction (offer after plan approval).
- Push/ntfy notification when a job lands in review (headless UX complement).
- Speculative both-candidate matching for same-name twins using cached transcripts (noise-floor disambiguation — show-identity-collision item 3).
- On implementation start: copy this plan into `docs/superpowers/specs/2026-06-10-walk-away-workflow-design.md` per repo convention.
