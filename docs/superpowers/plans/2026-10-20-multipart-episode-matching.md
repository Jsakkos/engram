# Multi-part Episode Matching (Half-hour to Segment Crosswalk) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** File Hey Arnold! S2 Disc 2 (and other segment-format shows) under the TMDB segment codes each half-hour track holds (`S02E11-E12`, `S02E13`, ...) instead of under the subtitle pack's half-hour number.

**Architecture:** The pack builder derives a per-season crosswalk (corpus code to TMDB code) from OpenSubtitles release titles plus TMDB air-date structure, and ships it inside the existing `divergent` numbering marker. The runtime translates precomputed reference codes through one chokepoint, drops unmapped references, stops gap-healing divergent seasons, and treats a crosswalked combined code as an explained conjoined track.

**Tech Stack:** Python 3.11+, uv, pytest, SQLModel; React/TypeScript only for one review-notice code (Task 7).

**Spec:** `docs/superpowers/specs/2026-10-20-multipart-episode-matching-design.md`

## Global Constraints

- `CACHE_FORMAT_VERSION` stays `"3"`. No manifest key is renamed or removed; the marker's `scheme` stays `divergent` when a crosswalk is present.
- Crosswalk values are produced by `format_episode_code` and parsed only through `app/core/episode_codes.py` (`parse_episode_code`, `episode_parts`, `is_multi_episode`). No new local `S(\d+)E(\d+)` parser on `matched_episode` (`tests/unit/test_matched_episode_consumers.py` enforces this).
- A crosswalk maps within one season only. A target in another season is unmapped.
- Unmapped references are dropped from the vote, never kept under their corpus code.
- `MIN_TITLE_CHARS = 4`; `GROUP_SPLIT_TOLERANCE = 0.25` (fraction of the season's median broadcast-group runtime).
- Never read or write the real `~/.engram/cache` in tests; use `tmp_path`.
- Prose (comments, docs, CHANGELOG, PR text): no em dashes.
- Backend commands from `backend/`: `uv run pytest ...`, `uv run ruff check .`, `uv run ruff format .`.

## Review Focus

1. **A pack with a crosswalk read by an older client.** Expect: identical behaviour to today (scheme still `divergent`, extra keys ignored). Test in Task 3 (`test_manifest_with_crosswalk_still_loads_on_legacy_path`).
2. **A crosswalk whose key is not in the shipped index** (pack built from a different harvest than the index). Expect: that entry ignored, rest applied. Test in Task 3 (`test_crosswalk_key_missing_from_index_is_ignored`).
3. **A user's `data/` dir already holding scraped `S02E21.srt` from an earlier heal.** Expect: not grafted into a divergent season. Test in Task 4.
4. **Crosswalk that maps every code to nothing** (all unmapped). Expect: the season behaves as "divergent, no crosswalk" (Task 7 routing), not as an empty corpus that refuses matching. Test in Task 3 (`test_all_unmapped_crosswalk_is_treated_as_absent`).
5. **OpenSubtitles search raising or returning junk during a nightly build.** Expect: marker still written, crosswalk from structure only. Test in Task 2.

---

### Task 1: `numbering_crosswalk` derivation module

**Files:**
- Create: `backend/app/matcher/numbering_crosswalk.py`
- Create: `backend/tests/fixtures/numbering_crosswalk/hey_arnold_s2.json`, `dexters_lab_s1.json`, `hey_arnold_s3.json`
- Test: `backend/tests/unit/test_numbering_crosswalk.py`

Fixtures are trimmed from the live captures made on 2026-10-20 (scratchpad `tmdb/tv_537_season_2.json`, `tv_4229_season_1.json`, `tv_537_season_3.json`, `os_ha_s2.json`, `os_dl_s1.json`, `os_ha_s3.json`). Each fixture file: `{"season": N, "corpus_codes": [...], "roster": [{"episode_number", "name", "air_date", "runtime"}], "releases": {"<ep>": ["<release>", ...]}}`. Keep release strings verbatim; drop all other OpenSubtitles fields (uploader names etc.). Corpus codes: HA S2 `S02E01..S02E20`, Dexter's S1 `S01E01..S01E13`, HA S3 `S03E01..S03E19`.

**Interfaces:**
- Produces:
  - `MIN_TITLE_CHARS = 4`, `GROUP_SPLIT_TOLERANCE = 0.25`
  - `@dataclass(frozen=True) class RosterEpisode: episode_number: int; name: str; air_date: str; runtime: int`
  - `@dataclass(frozen=True) class Crosswalk: mapping: dict[str, str]; sources: dict[str, int]` (sources keys: `"release_title"`, `"air_date_gap"`, `"unmapped"`)
  - `broadcast_groups(roster: list[RosterEpisode]) -> list[list[int]]`
  - `derive_crosswalk(season: int, corpus_codes: list[str], roster: list[RosterEpisode], releases_by_episode: dict[int, list[str]] | None) -> Crosswalk`
  - `crosswalk_applies(marker: dict | None) -> bool`: True iff `marker["scheme"] == "divergent"` and `marker["crosswalk"]` is a non-empty dict
  - `translate_codes(codes: list[str], marker: dict | None) -> list[str | None] | None`: None when `crosswalk_applies` is False; otherwise one entry per input code (translated code, or None = drop). A crosswalk whose translation would drop every code returns None (treated as absent).

- [ ] **Step 1: Write the failing tests**

```python
def test_hey_arnold_s2_matches_reporter_expectation(ha_s2):
    cw = derive_crosswalk(2, ha_s2.codes, ha_s2.roster, ha_s2.releases)
    assert [cw.mapping.get(f"S02E{n:02d}") for n in range(6, 11)] == [
        "S02E11-E12", "S02E13", "S02E14-E15", "S02E16-E17", "S02E18-E19"]
    assert cw.mapping["S02E05"] == "S02E09-E10"      # typo'd release, filled by gap
    assert cw.mapping["S02E15"] == "S02E28-E29"      # E29 title only, expanded to its group
    assert "S02E19" not in cw.mapping                # Part Time Friends lives in TMDB S3
    assert cw.sources == {"release_title": 18, "air_date_gap": 1, "unmapped": 1}

def test_dexters_lab_s1_conflicting_uploads_fall_back_to_structure(dl_s1):
    cw = derive_crosswalk(1, dl_s1.codes, dl_s1.roster, dl_s1.releases)
    assert cw.mapping["S01E01"] == "S01E01-E03"
    assert cw.mapping["S01E12"] == "S01E34-E35"
    assert cw.mapping["S01E13"] == "S01E36-E38"
    assert cw.sources == {"release_title": 0, "air_date_gap": 13, "unmapped": 0}

def test_same_day_double_broadcast_is_split_by_runtime(ha_s3):
    assert [30, 31] in broadcast_groups(ha_s3.roster)
    assert [32, 33] in broadcast_groups(ha_s3.roster)
    cw = derive_crosswalk(3, ha_s3.codes, ha_s3.roster, ha_s3.releases)
    assert cw.mapping["S03E16"] == "S03E30-E31"
```

Synthetic edge cases in the same file (small hand-built rosters):
`test_release_naming_nothing_is_dissent_not_abstention`, `test_disagreeing_releases_give_no_anchor`, `test_title_inside_show_name_prefix_is_ignored` (text before the `SxxEyy` marker is not searched), `test_title_shorter_than_min_chars_is_ignored`, `test_longer_title_wins_over_contained_title` ("Mermaid Man and Barnacle Boy" vs "... II"), `test_inverted_anchors_are_both_dropped`, `test_group_straddling_an_anchor_leaves_gap_unmapped`, `test_missing_slot_inside_gap_keeps_neighbours_aligned` (corpus E01,E02,E04 with 4 groups maps E04 to group 4), `test_no_releases_and_no_air_dates_maps_nothing`, `test_values_are_strictly_increasing_and_non_overlapping` (property over all three fixtures).
`translate_codes`: `test_translate_returns_none_without_crosswalk`, `test_translate_drops_unmapped`, `test_all_unmapped_crosswalk_is_treated_as_absent`.

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/unit/test_numbering_crosswalk.py -v`
Expected: FAIL, `ModuleNotFoundError: app.matcher.numbering_crosswalk`

- [ ] **Step 3: Implement `numbering_crosswalk.py`**

Algorithm per spec 5.1 to 5.3 (the 2026-10-20 prototype in the session scratchpad, `proto_crosswalk.py`, passes the three fixture expectations and may be used as reference):
- Normalize = lowercase alphanumerics concatenated (`"".join(re.findall(r"[a-z0-9]+", s.lower()))`).
- Groups: maximal runs of consecutive roster entries sharing a non-empty `air_date`; a group whose summed runtime is within `GROUP_SPLIT_TOLERANCE * median` of `k * median` (k >= 2, at least k episodes) splits into k parts by cumulative runtime; otherwise unchanged.
- Release text searched = text after the first `s\d{1,2}e\d{1,3}` match (whole string if none). Anchor requires every release for the episode to yield the same non-empty title set; expand to containing groups; groups must be contiguous; anchors must be strictly increasing and non-overlapping (drop both sides of any violation).
- Gap fill by corpus slot numbers, equal-count rule only.
- Module docstring: why (spec section 1, one paragraph) and that it is pure and shared by the builders and the runtime.

- [ ] **Step 4: Run tests to verify pass**

Run: `uv run pytest tests/unit/test_numbering_crosswalk.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/matcher/numbering_crosswalk.py backend/tests/unit/test_numbering_crosswalk.py backend/tests/fixtures/numbering_crosswalk/
git commit -m "feat(matcher): derive half-hour to segment crosswalk for divergent seasons (#760)"
```

### Task 2: Builders emit the crosswalk

**Files:**
- Modify: `backend/scripts/build_subtitle_cache.py` (`_season_numbering_entry` ~655, call site ~988)
- Modify: `backend/scripts/pack_subtitle_cache.py` (wrapper ~94, call site ~345)
- Modify: `backend/app/matcher/testing_service.py` (add release lookup)
- Test: `backend/tests/unit/test_cache_numbering_marker.py`, `backend/tests/unit/test_build_subtitle_cache.py`

**Interfaces:**
- Consumes: `derive_crosswalk`, `RosterEpisode` (Task 1); `fetch_season_episodes(show_id, season, api_key)` (existing, returns `name`, `runtime`, `air_date`).
- Produces:
  - `testing_service.season_release_titles(show_id: str, season: int) -> dict[int, list[str]] | None`: one OpenSubtitles `search(parent_tmdb_id=..., season_number=..., languages="en", type="episode")` through `_os_client_and_reason` + `os_api_call`; groups `release` strings by `episode_number` for results whose `season_number == season`. None when no credentials, no client, or any exception (logged with `exc_info=True`). Never downloads.
  - `build_subtitle_cache._season_numbering_entry(tmdb_id: int | None, season: int, codes: list[str], release_lookup=season_release_titles) -> dict`: unchanged output for non-divergent seasons; for `divergent`, adds `crosswalk` (omitted when empty) and `crosswalk_sources`.
  - `pack_subtitle_cache._season_numbering_entry(tmdb_id, season, codes, offline)`: offline keeps the `unknown` short-circuit and never calls the lookup.

- [ ] **Step 1: Write failing tests**

`test_divergent_season_carries_crosswalk` (monkeypatch `fetch_season_details` to 37, `fetch_season_episodes` and the lookup to the HA S2 fixture; assert `entry["crosswalk"]["S02E06"] == "S02E11-E12"` and `entry["scheme"] == "divergent"`), `test_tmdb_aired_season_has_no_crosswalk`, `test_unknown_season_has_no_crosswalk_and_no_lookup` (lookup mock asserts not called), `test_offline_pack_never_calls_release_lookup`, `test_release_lookup_failure_falls_back_to_structure` (lookup returns None; Dexter's fixture still gets 13 entries), `test_release_lookup_exception_does_not_abort` (lookup raises; marker still has `scheme`), `test_empty_crosswalk_is_omitted`. For `season_release_titles`: `test_release_titles_grouped_by_episode_and_season_filtered`, `test_release_titles_none_without_credentials`.

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/unit/test_cache_numbering_marker.py tests/unit/test_build_subtitle_cache.py -v`
Expected: new tests FAIL (signature takes an int today)

- [ ] **Step 3: Implement** the signature change at both call sites (pass the `codes` list both scripts already build), the lookup, and the crosswalk fields. Wrap the roster fetch + lookup + derivation in one broad `except Exception` that logs and returns the marker without a crosswalk (same rationale as the existing roster-lookup catch).

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/unit/test_cache_numbering_marker.py tests/unit/test_build_subtitle_cache.py tests/unit/test_numbering_scheme.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git commit -am "feat(cache): publish crosswalk in divergent season markers (#760)"
```

### Task 3: Validator and runtime chokepoint

**Files:**
- Modify: `backend/scripts/validate_subtitle_cache.py` (`_check_season_numbering` ~75)
- Modify: `backend/app/matcher/episode_identification.py` (`precomputed_episode_codes` ~273, `EpisodeMatcher.precomputed_numbering` ~1363, `_load_precomputed_season` ~1391, `stamp_numbering` ~119)
- Test: `backend/tests/unit/test_validate_subtitle_cache.py`, `backend/tests/unit/test_precomputed_cache.py`

**Interfaces:**
- Consumes: `crosswalk_applies`, `translate_codes` (Task 1).
- Produces:
  - `episode_identification.season_marker(manifest: dict | None, show_name: str, season: int, expected_tmdb_id) -> dict | None`: raw marker dict for the resolved show entry (same `_resolve_corpus_entry` + `_tmdb_id_mismatch` refusal), including `unknown`. `precomputed_numbering` becomes a thin filter over it.
  - `precomputed_episode_codes(...)` returns translated codes with dropped entries removed.
  - `_load_precomputed_season` returns the matrix restricted to kept rows (`ref_matrix[keep]`) with translated codes.
  - `stamp_numbering(match_stats, numbering)`: when `crosswalk_applies(numbering)`, stamps `numbering_scheme="tmdb_aired"`, `numbering_crosswalk=True`, `pack_numbering_scheme="divergent"` (plus `pack_roster_size` as today).

- [ ] **Step 1: Write failing tests**

Validator: `test_crosswalk_on_non_divergent_season_fails`, `test_crosswalk_key_not_in_index_fails`, `test_crosswalk_value_unparseable_fails`, `test_crosswalk_value_other_season_fails`, `test_crosswalk_values_overlapping_fails`, `test_crosswalk_values_not_increasing_fails`, `test_valid_crosswalk_passes_and_reports_mapped_counts`.
Runtime (manifest + npz/index written to `tmp_path`, HA S2 shaped, 20 rows): `test_precomputed_codes_are_translated` (`precomputed_episode_codes` returns 19 codes, contains `"S02E11-E12"`, lacks `"S02E19"`), `test_load_precomputed_drops_unmapped_rows` (matrix has 19 rows, aligned with codes: row for corpus E06 carries code `"S02E11-E12"`), `test_crosswalk_key_missing_from_index_is_ignored`, `test_manifest_with_crosswalk_still_loads_on_legacy_path` (`load_precomputed_manifest` and `precomputed_numbering` return as before), `test_stamp_numbering_crosswalked_reads_tmdb_aired`, `test_stamp_numbering_divergent_without_crosswalk_unchanged`, `test_all_unmapped_crosswalk_is_treated_as_absent` (codes untranslated, stamp stays `divergent`).

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/unit/test_validate_subtitle_cache.py tests/unit/test_precomputed_cache.py tests/unit/test_cache_numbering_marker.py -v`
Expected: new tests FAIL

- [ ] **Step 3: Implement.** The marker is a per-call local (singleton matcher is shared across threads); never cache it on `self`. The TF-IDF cache signature for precomputed mode must change when the crosswalk changes the code list (include the translated codes or the marker in the signature).

- [ ] **Step 4: Run to verify pass** (same command). Expected: PASS

- [ ] **Step 5: Commit**

```bash
git commit -am "feat(matcher): translate precomputed references through the season crosswalk (#760)"
```

### Task 4: No healing or grafting for divergent seasons; coverage expansion

**Files:**
- Modify: `backend/app/matcher/testing_service.py` (`_precomputed_skip_result` ~356, `_heal_precomputed_gaps` ~476)
- Modify: `backend/app/matcher/episode_identification.py` (`_augment_with_downloaded_srts` ~1467, `reference_coverage` ~324)
- Test: `backend/tests/unit/test_precomputed_augmentation.py`, `backend/tests/unit/test_reference_coverage.py`, the existing heal tests (grep `_heal_precomputed_gaps` under `tests/unit`)

**Interfaces:**
- Consumes: `season_marker` (Task 3), `episode_parts`.
- Produces: skip dict gains `"numbering_scheme": <marker scheme or None>`; heal returns the skip dict unchanged when it is `"divergent"`; `_augment_with_downloaded_srts(ref_matrix, episode_codes, season_number, *, divergent: bool = False)` returns inputs unchanged when `divergent`.

- [ ] **Step 1: Write failing tests**

`test_heal_skipped_for_divergent_season` (fetch mocks assert `_fetch_episodes` not called; log names the reason), `test_heal_unchanged_for_tmdb_aired_season`, `test_augment_does_not_graft_into_divergent_season` (a `Hey Arnold! - S02E21.srt` in `data/537/`), `test_augment_unchanged_for_aired_season`, `test_reference_coverage_expands_combined_codes` (`"S02E11-E12"` marks E11 and E12 `precomputed`; E19 not covered by anything stays `missing` only if no code covers it).

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/unit/test_precomputed_augmentation.py tests/unit/test_reference_coverage.py -v` plus the heal test file.
Expected: new tests FAIL

- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run to verify pass.** Expected: PASS
- [ ] **Step 5: Commit**

```bash
git commit -am "fix(matcher): stop gap-healing seasons numbered by half-hour (#760)"
```

### Task 5: Coordinator: explained hint and combined-code flattening

**Files:**
- Modify: `backend/app/services/matching_coordinator.py` (`_combined_code` ~394, `_apply_multi_episode_review` ~487)
- Test: `backend/tests/unit/test_matching_coordinator.py`, `backend/tests/unit/test_multi_episode.py`

**Interfaces:**
- Consumes: `match_details["numbering_crosswalk"]` (Task 3), `episode_parts`, `is_multi_episode`.
- Produces: no new names.

- [ ] **Step 1: Write failing tests**

```python
def test_crosswalked_combined_code_explains_matching_hint():
    title = _title(matched_episode="S01E01-E03",
                   details={"numbering_crosswalk": True, "numbering_scheme": "tmdb_aired",
                            "multi_episode": {"is_multi_episode": False}})
    assert _apply_multi_episode_review(title, conjoined_hint=3) is False
    assert title.state == TitleState.MATCHED

def test_crosswalked_code_with_wrong_part_count_still_parks():
    title = _title(matched_episode="S01E01-E02", details={"numbering_crosswalk": True, ...})
    assert _apply_multi_episode_review(title, conjoined_hint=3) is True

def test_hint_without_crosswalk_still_parks():  # existing behaviour pinned

def test_combined_code_flattens_combined_parts():
    assert _combined_code(["S02E11-E12", "S02E13"]) == "S02E11-E13"
    assert _combined_code(["S02E11-E12", "S03E01"]) is None
```

Plus `test_crosswalked_two_segment_track_is_not_contributed` (`_may_contribute_fingerprint("S02E11-E12", None, {...}) is False`, pins existing rule).

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/unit/test_matching_coordinator.py tests/unit/test_multi_episode.py -v -k "crosswalk or combined_code or hint"`
Expected: new tests FAIL

- [ ] **Step 3: Implement.** In `_combined_code`, flatten with `episode_parts` before the existing season/count checks. In `_apply_multi_episode_review`, before parking a hint-only track: if `details.get("numbering_crosswalk")` and `len(episode_parts(title.matched_episode)) == conjoined_hint`, log and return False.
- [ ] **Step 4: Run to verify pass.** Expected: PASS
- [ ] **Step 5: Commit**

```bash
git commit -am "fix(matching): accept a crosswalked combined code as the conjoined answer (#760)"
```

### Task 6: End-to-end pin for the reporter's disc

**Files:**
- Test: `backend/tests/unit/test_numbering_crosswalk_e2e.py`

**Interfaces:**
- Consumes: Tasks 1 to 5; `organize_tv_episode` (existing).

- [ ] **Step 1: Write the test**

`test_hey_arnold_s2_disc2_files_segment_ranges`: build a `tmp_path` pack from the HA S2 fixture (crosswalk derived via Task 1, written into the manifest marker; 20-row npz whose row *k* is a one-hot-ish vector); load through `EpisodeMatcher._load_precomputed_season(2)` with `cache_dir=tmp_path`; feed `TfidfMatcher.match` the text vector of corpus row E06..E10; assert the top codes are `["S02E11-E12", "S02E13", "S02E14-E15", "S02E16-E17", "S02E18-E19"]`. Then `organize_tv_episode` with `"S02E11-E12"` into a `tmp_path` library asserts the file name `Hey Arnold! - S02E11-E12.mkv`. If driving `TfidfMatcher` with synthetic vectors proves awkward, assert on the loaded `(matrix, codes)` row alignment instead and keep the organizer assertion.

- [ ] **Step 2: Run.** `uv run pytest tests/unit/test_numbering_crosswalk_e2e.py -v`. Expected: PASS (Tasks 1 to 5 already implement it; a failure here is a real integration bug to fix in the owning task).
- [ ] **Step 3: Commit**

```bash
git add backend/tests/unit/test_numbering_crosswalk_e2e.py
git commit -m "test: pin Hey Arnold! S2 Disc 2 segment filing end to end (#760)"
```

### Task 7: Divergent season without a crosswalk goes to review (only if maintainer approves spec decision 2)

**Files:**
- Modify: `backend/app/services/matching_coordinator.py` (error code beside `MULTI_EPISODE_ERROR_CODE` ~223, `_NON_REMATCHABLE_REVIEW_ERRORS` ~229, `_route_unconfirmable_title` ~380)
- Modify: `frontend/src/components/ReviewQueue/Inspector.tsx:21` (`REVIEW_NOTICE_CODES`) and `Inspector.test.tsx`
- Test: `backend/tests/unit/test_matching_coordinator.py`

**Interfaces:**
- Produces: `NUMBERING_DIVERGENT_ERROR_CODE = "numbering_divergent"`; `_apply_divergent_numbering_review(title) -> bool`, called last in `_route_unconfirmable_title`, which returns the code when it parks.

- [ ] **Step 1: Write failing tests**

`test_divergent_uncrosswalked_match_is_parked` (details `numbering_scheme == "divergent"`, state MATCHED, code `S02E06` → state REVIEW, `matched_episode` kept as pre-fill, `details["error"] == "numbering_divergent"`), `test_crosswalked_match_is_not_parked`, `test_tmdb_aired_match_is_not_parked`, `test_numbering_divergent_is_not_rematchable` (`"numbering_divergent" in _NON_REMATCHABLE_REVIEW_ERRORS`), `test_prior_error_message_carried_forward` (mirror the multi-episode carry-forward). Frontend: `Inspector.test.tsx` case showing the notice for `numbering_divergent`.

Message copy (exact): "The reference subtitles for this season are numbered differently from TMDB (usually by broadcast half-hour instead of by segment), so {code} may name the wrong episode. Check it and assign the TMDB episode(s) this track holds."

- [ ] **Step 2: Run to verify failure.** `uv run pytest tests/unit/test_matching_coordinator.py -v -k divergent` and `npm run test:unit -- Inspector` (from `frontend/`). Expected: FAIL
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run to verify pass.** Same commands. Expected: PASS
- [ ] **Step 5: Commit**

```bash
git commit -am "fix(matching): hold divergent-numbered matches for review when no crosswalk applies (#760)"
```

### Task 8: Docs, changelog, full verification, PR

**Files:**
- Modify: `CLAUDE.md` (Key Patterns: one bullet after "Episode codes are multi-episode capable" describing the crosswalk, its chokepoint, the no-heal rule, and that the scheme stays `divergent`)
- Modify: `CHANGELOG.md` (`[Unreleased]` / `### Fixed`)
- Modify: spec status line to "approved; implemented in PR #NNN"

- [ ] **Step 1: CHANGELOG entry**

```markdown
- Segment-format TV discs (Hey Arnold!, Dexter's Laboratory, SpongeBob) are filed under the TMDB segments each track holds, such as `S02E11-E12`, instead of under the subtitle pack's half-hour number, which named the wrong episode. Takes full effect once the nightly subtitle pack is rebuilt. (#760)
```

- [ ] **Step 2: Lint and full backend suite** (background; the unit tier runs several minutes)

Run: `uv run ruff check . && uv run ruff format --check . && uv run pytest tests/unit tests/pipeline -q`
Expected: all pass. If caplog tests misbehave, re-run those files alone (no `-p no:logging`).

- [ ] **Step 3: Frontend** (only if Task 7 shipped): `npm run lint && npm run test:unit && npm run build` from `frontend/`. Expected: pass.

- [ ] **Step 4: Dash check** on changed prose: `git diff origin/main -- '*.md' '*.py' | grep -P "\xe2\x80\x94|\xe2\x80\x93"` returns nothing new.

- [ ] **Step 5: Commit, push, PR**

```bash
git commit -am "docs: crosswalk notes for #760"
git push -u origin claude/760-multipart-episodes-f0571a
gh pr create --title "fix: file segment-show tracks under their TMDB segments (#760)" --body "<summary, root cause table, rollout note about the nightly pack, test plan>\n\nFixes #760\n\n🤖 Generated with [Claude Code](https://claude.com/claude-code)"
gh pr comment <N> --body "@claude please review this PR"
```

PR body must say the server-side nightly (`build_subtitle_cache.py` on the cache host) needs redeploying before users see the fix, and that until then Task 7 (if shipped) holds affected discs for review.
