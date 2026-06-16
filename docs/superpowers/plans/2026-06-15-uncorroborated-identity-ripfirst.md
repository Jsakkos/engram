# Uncorroborated TV Identity Rip-First Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A TV disc whose TMDB identity is found but not corroborated by the on-disc label rips-first with a non-blocking "Confirm title" prompt (deferred to the pooled end-of-run review) instead of parking in `REVIEW_NEEDED` before ripping.

**Architecture:** Two fixes. (1) Safety net — the analyst flags the uncorroborated-identity case with a typed `identity_unconfirmed` flag, and the identification coordinator routes that flag through the existing walk-away Gate C `_rip_first_with_prompt(kind="reidentify")` path. (2) Optimization — `_parse_volume_label` strips trailing rip-annotation tokens (e.g. `ok`) so common cases corroborate cleanly and show no prompt at all.

**Tech Stack:** Python 3.11, FastAPI/SQLModel, pytest (`uv run pytest`), ruff. Spec: `docs/superpowers/specs/2026-06-15-uncorroborated-identity-ripfirst-design.md`.

All commands run from `backend/`.

---

### Task 1: Strip trailing rip-annotation tokens in `_parse_volume_label`

**Files:**
- Modify: `backend/app/core/analyst.py` (add `_LABEL_JUNK_TOKENS` constant near `_ACRONYM_STOPWORDS` ~line 117; add strip block in `_parse_volume_label` after `label = label.strip()` ~line 1018)
- Test: `backend/tests/unit/test_analyst.py`

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/unit/test_analyst.py` (the module already imports `DiscAnalyst`):

```python
class TestParseVolumeLabelJunkTokens:
    """Trailing rip-annotation tokens (e.g. 'ok') are stripped from the name."""

    def test_strips_trailing_ok(self):
        assert DiscAnalyst._parse_volume_label("DS9S3D2 ok") == ("Ds9", 3, 2)

    def test_clean_label_unchanged(self):
        assert DiscAnalyst._parse_volume_label("DS9S2D4") == ("Ds9", 2, 4)

    def test_strips_multiple_trailing_junk_tokens(self):
        assert DiscAnalyst._parse_volume_label("DS9S3D2 ok done") == ("Ds9", 3, 2)

    def test_preserves_non_trailing_junk_token(self):
        # A leading 'OK' that is part of the real title must survive.
        name, season, disc = DiscAnalyst._parse_volume_label("OK_KO_S1D1")
        assert name == "Ok Ko"

    def test_all_junk_label_not_emptied(self):
        # A label that is only a junk token keeps it rather than becoming None.
        name, season, disc = DiscAnalyst._parse_volume_label("OK_S1D1")
        assert name == "Ok"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_analyst.py::TestParseVolumeLabelJunkTokens -v`
Expected: `test_strips_trailing_ok` and `test_strips_multiple_trailing_junk_tokens` FAIL on the assertion (e.g. `("Ds9 Ok", 3, 2) != ("Ds9", 3, 2)`); the clean-label / non-trailing / all-junk tests already pass (they don't depend on the strip). No `NameError` — `_LABEL_JUNK_TOKENS` isn't referenced until Step 4.

- [ ] **Step 3: Add the constant**

In `backend/app/core/analyst.py`, immediately after the `_ACRONYM_STOPWORDS` definition (~line 117):

```python
# Trailing annotation tokens users append to a finished rip's label
# (e.g. "DS9S3D2 ok"). Stripped trailing-only in _parse_volume_label so the
# show name still corroborates against TMDB; an unlisted token is caught by the
# rip-first reidentify gate instead, so this list only needs the common ones.
_LABEL_JUNK_TOKENS: frozenset[str] = frozenset(
    {"OK", "DONE", "RIP", "RIPPED", "COPY", "BACKUP", "BAK", "FINAL"}
)
```

- [ ] **Step 4: Add the strip block**

In `_parse_volume_label`, between `label = label.strip()` (~line 1018) and `name = label.title() if label else None` (~line 1021), insert:

```python
        # Drop trailing rip-annotation tokens (e.g. "DS9 OK" -> "DS9"). Trailing
        # only, and never empties the name (the > 1 guard): an unstripped junk
        # token still rips-first via the reidentify gate.
        tokens = label.split()
        while len(tokens) > 1 and tokens[-1] in _LABEL_JUNK_TOKENS:
            tokens.pop()
        label = " ".join(tokens)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_analyst.py::TestParseVolumeLabelJunkTokens -v`
Expected: PASS (5 passed)

- [ ] **Step 6: Commit**

```bash
git add app/core/analyst.py tests/unit/test_analyst.py
git commit -m "fix(analyst): strip trailing rip-annotation tokens from volume labels

A label like 'DS9S3D2 ok' parsed the show name as 'Ds9 Ok', breaking TMDB
corroboration. Strip a small denylist of trailing tokens (ok/done/rip/...) so
the disc corroborates cleanly. Trailing-only, never empties the name.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Add `identity_unconfirmed` flag to the analyst result

**Files:**
- Modify: `backend/app/core/analyst.py` (add field to `DiscAnalysisResult` ~line 226; set it at the two `_uncorroborated_review_reason` sites ~line 577 and ~line 727)
- Test: `backend/tests/unit/test_analyst.py`

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/unit/test_analyst.py` (module already imports `TmdbSignal`, `ContentType`, `_make_titles`, `_default_config`):

```python
class TestUncorroboratedIdentityFlag:
    """Uncorroborated TV identity sets identity_unconfirmed for the rip-first gate."""

    def _ds9_signal(self):
        return TmdbSignal(
            ContentType.TV, 0.7, tmdb_id=580, tmdb_name="Star Trek: Deep Space Nine"
        )

    def test_uncorroborated_tv_identity_sets_flag(self):
        analyst = DiscAnalyst(config=_default_config())
        titles = _make_titles([45, 45, 45, 45])
        # 'ZZZJUNK' is neither similar to nor an initialism of the TMDB name,
        # so corroboration fails and the identity is unconfirmed.
        result = analyst.analyze(
            titles,
            volume_label="ZZZJUNK_S3D2",
            tmdb_signal=self._ds9_signal(),
            disc_title="ZZZJUNK",
        )
        assert result.content_type == ContentType.TV
        assert result.needs_review is True
        assert result.identity_unconfirmed is True

    def test_corroborated_identity_not_flagged(self):
        analyst = DiscAnalyst(config=_default_config())
        titles = _make_titles([45, 45, 45, 45])
        # 'DS9' is the digit-initialism of 'Deep Space Nine' -> corroborated.
        result = analyst.analyze(
            titles,
            volume_label="DS9S2D4",
            tmdb_signal=self._ds9_signal(),
            disc_title="DS9S2D4",
        )
        assert result.needs_review is False
        assert result.identity_unconfirmed is False

    def test_default_result_flag_is_false(self):
        from app.core.analyst import DiscAnalysisResult

        assert DiscAnalysisResult(content_type=ContentType.TV).identity_unconfirmed is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_analyst.py::TestUncorroboratedIdentityFlag -v`
Expected: FAIL with `AttributeError: 'DiscAnalysisResult' object has no attribute 'identity_unconfirmed'`

- [ ] **Step 3: Add the dataclass field**

In `backend/app/core/analyst.py`, in `DiscAnalysisResult`, immediately after `is_ambiguous_movie: bool = False` (~line 226):

```python
    # TV identity found on TMDB but not corroborated by the on-disc label — a
    # best-guess identity the user confirms later. Routes the job to the
    # rip-first reidentify gate (walk-away) instead of a pre-rip park. Set only
    # at the two _uncorroborated_review_reason sites below.
    identity_unconfirmed: bool = False
```

- [ ] **Step 4: Set the flag at the TMDB-only escalation site**

In `analyze`, find the block (~line 576):

```python
                tmdb_only.needs_review = True
                tmdb_only.review_reason = _uncorroborated_review_reason(effective_name, tmdb_signal)
            return tmdb_only
```

Change to:

```python
                tmdb_only.needs_review = True
                tmdb_only.review_reason = _uncorroborated_review_reason(effective_name, tmdb_signal)
                tmdb_only.identity_unconfirmed = True
            return tmdb_only
```

- [ ] **Step 5: Set the flag in `_apply_tmdb_signal`**

In `_apply_tmdb_signal`, find the block (~line 724):

```python
            elif not result.needs_review and result.content_type == ContentType.TV:
                result.needs_review = True
                result.review_reason = _uncorroborated_review_reason(
                    result.detected_name, tmdb_signal
                )
```

Change to:

```python
            elif not result.needs_review and result.content_type == ContentType.TV:
                result.needs_review = True
                result.review_reason = _uncorroborated_review_reason(
                    result.detected_name, tmdb_signal
                )
                result.identity_unconfirmed = True
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_analyst.py::TestUncorroboratedIdentityFlag -v`
Expected: PASS (3 passed)

- [ ] **Step 7: Run the full analyst test file (no regressions)**

Run: `uv run pytest tests/unit/test_analyst.py -v`
Expected: PASS (all existing + new tests)

- [ ] **Step 8: Commit**

```bash
git add app/core/analyst.py tests/unit/test_analyst.py
git commit -m "feat(analyst): flag uncorroborated TV identity with identity_unconfirmed

Typed flag (mirrors is_ambiguous_movie) set at the two uncorroborated-identity
escalation sites. Lets the identification coordinator route these to the
rip-first reidentify gate instead of parking, without string-matching the
review reason.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Route `identity_unconfirmed` through Gate C in the coordinator

**Files:**
- Modify: `backend/app/services/identification_coordinator.py` (the `if _collision:` branch ~line 568)
- Test: `backend/tests/unit/test_identify_rip_first_gates.py`

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/unit/test_identify_rip_first_gates.py` (helpers `_make_analysis`, `_bare_coord`, `_seed_identifying_job`, `_reload_job`, `gate_env`, `_GATE_TITLES` already exist in this file):

```python
@pytest.mark.unit
class TestGateCUncorroboratedIdentity:
    async def test_uncorroborated_identity_rips_first_with_reidentify(self, gate_env):
        """An uncorroborated-but-known TV identity rips first with a reidentify
        prompt (walk-away) instead of parking — like a same-name collision."""
        job_id = await _seed_identifying_job("DS9S3D2_OK")
        reason = (
            "Couldn't confirm disc 'Ds9 Ok' is 'Star Trek: Deep Space Nine' "
            "(TMDB #580). Confirm or correct the title."
        )
        analysis = _make_analysis(
            ContentType.TV,
            "Ds9 Ok",
            season=3,
            tmdb_id=580,
            needs_review=True,
            review_reason=reason,
        )
        analysis.identity_unconfirmed = True
        coord = _bare_coord(analysis, _GATE_TITLES, "DS9S3D2_OK")

        await coord.identify_disc(job_id)

        job = await _reload_job(job_id)
        assert job.state == JobState.RIPPING
        assert job.review_reason is None
        prompt = json.loads(job.identity_prompt_json)
        assert prompt == {"kind": "reidentify", "reason": reason}
        coord._run_ripping.assert_awaited_once_with(job_id)
        # Never parked.
        assert not any(state == JobState.REVIEW_NEEDED.value for state, _ in gate_env)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/unit/test_identify_rip_first_gates.py::TestGateCUncorroboratedIdentity -v`
Expected: FAIL — the job parks: `assert job.state == JobState.RIPPING` fails with `JobState.REVIEW_NEEDED` (the analysis falls into the catch-all park branch).

- [ ] **Step 3: Extend the Gate C branch**

In `backend/app/services/identification_coordinator.py`, replace the Gate C block (~lines 561-576). Find:

```python
                    # Gate C (walk-away B2): same-name collision (ambiguous
                    # identity / no-year twin) — identity is uncertain but the
                    # twins are persisted (candidates_json, set above) for the
                    # ReIdentifyModal quick-pick, so rip first with a
                    # reidentify prompt instead of parking. Prefetch was
                    # already skipped for collisions (downloading by the
                    # tentative name would fetch the wrong show's subtitles).
                    if _collision:
                        await self._rip_first_with_prompt(
                            job,
                            session,
                            job_id,
                            kind="reidentify",
                            reason=analysis.review_reason,
                        )
                        return
```

Replace with:

```python
                    # Gate C (walk-away B2): identity is uncertain but we have a
                    # best guess — rip first with a reidentify prompt instead of
                    # parking. Two cases: a same-name collision (twins persisted
                    # in candidates_json for the ReIdentifyModal quick-pick;
                    # prefetch already skipped) OR an uncorroborated single TMDB
                    # identity (analysis.identity_unconfirmed; tmdb_id is real, so
                    # the prefetch above ran). The user confirms via the
                    # "Confirm title" CTA any time, or it converges to the pooled
                    # review at rip end (B4).
                    if _collision or analysis.identity_unconfirmed:
                        await self._rip_first_with_prompt(
                            job,
                            session,
                            job_id,
                            kind="reidentify",
                            reason=analysis.review_reason,
                        )
                        return
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/unit/test_identify_rip_first_gates.py::TestGateCUncorroboratedIdentity -v`
Expected: PASS

- [ ] **Step 5: Run the whole rip-first gates file (the still-parks pin must hold)**

Run: `uv run pytest tests/unit/test_identify_rip_first_gates.py -v`
Expected: PASS — in particular `TestOtherReviewPathsStillPark::test_type_conflict_review_still_parks_before_ripping` still parks (its analysis has `identity_unconfirmed=False`).

- [ ] **Step 6: Commit**

```bash
git add app/services/identification_coordinator.py tests/unit/test_identify_rip_first_gates.py
git commit -m "fix(identify): rip-first for uncorroborated TV identity instead of parking

Route analysis.identity_unconfirmed through the walk-away Gate C reidentify
path so a disc with a found-but-uncorroborated TMDB identity (e.g. label
'DS9S3D2 ok') rips immediately and defers the 'Confirm title' to the pooled
end-of-run review, restoring the walk-away guarantee. Content-type conflicts
still park.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Verify the broader suite and lint

**Files:** none (verification only)

- [ ] **Step 1: Run the analyst + identification + state-machine unit tests**

Run: `uv run pytest tests/unit/test_analyst.py tests/unit/test_analyst_ambiguity.py tests/unit/test_analyst_name_number.py tests/unit/test_identify_rip_first_gates.py -v`
Expected: PASS (no regressions)

- [ ] **Step 2: Run the identity-collision integration test (Gate C end-to-end pin)**

Run: `uv run pytest tests/integration/test_show_identity_collision.py -v`
Expected: PASS (the collision seam is unchanged by the OR extension)

- [ ] **Step 3: Lint and format**

Run: `uv run ruff check app/core/analyst.py app/services/identification_coordinator.py tests/unit/test_analyst.py tests/unit/test_identify_rip_first_gates.py`
Then: `uv run ruff format app/core/analyst.py app/services/identification_coordinator.py tests/unit/test_analyst.py tests/unit/test_identify_rip_first_gates.py`
Expected: no lint errors; format reports files unchanged or formats them.

- [ ] **Step 4: Commit any format changes (if ruff format changed files)**

```bash
git add -A
git commit -m "style: ruff format uncorroborated-identity changes

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

(If ruff format changed nothing, skip this step.)

---

## Notes for the implementer

- **Empty worktree DB:** these are unit tests using the in-memory `_unit_session_factory`; they do not need `backend/engram.db`. If integration tests in Task 4 Step 2 fail with `no such table: app_config`, run `uv run python -c "import asyncio; from app.database import init_db; asyncio.run(init_db())"` first (a known worktree env gap, not a regression).
- **Line numbers are approximate** — anchor on the quoted code, not the line number.
- **Do not** touch the four existing gates, `_rip_first_with_prompt`, or `BLOCKING_KINDS` — `reidentify` is already a blocking kind, so the B3 QUEUED-parking and B4 convergence already cover the new routing.
