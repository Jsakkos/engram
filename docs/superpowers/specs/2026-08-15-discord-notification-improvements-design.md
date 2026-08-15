# Discord Notification Improvements: Design

**Date:** 2026-08-15
**Status:** Approved, ready for planning

## Motivation

Two user requests drive this work:

1. **Disc identity in notifications.** When ripping a box set, every disc produces the
   same notification text. The embed description defaults to `**{{{title}}}**`, where
   `title` resolves to `detected_title or volume_label`; for a box set that is the show
   name on every disc. There is no way to tell disc 3 of 6 from disc 4 of 6.
2. **Review-needed notifications.** Notifications fire only on `COMPLETED` and `FAILED`.
   A job that parks in `REVIEW_NEEDED` is silent, so the walk-away workflow stalls
   without telling anyone.

A review of the existing implementation surfaced additional gaps, several of which are
in scope here (see Scope below).

## Current implementation

```
JobStateMachine.transition() -> terminal (COMPLETED | FAILED) only
  -> JobManager._notify_discord_on_terminal   (on_terminal_state hook)
     -> asyncio.create_task(_send_discord_notification)
        -> get_config(); return if no discord_webhook_url
        -> session.get(DiscJob, job_id)
        -> build_template_context(job) -> render_discord_template() -> notify_discord()
```

- `backend/app/core/discord_notifier.py`: 14 allowed template variables, chevron
  rendering, and a single `notify_discord()` that hardcodes a green/red, checkmark/cross
  binary plus an embed title of `f"Disc {state.title()}"`.
- Config: `discord_webhook_url`, `discord_template_completed`, `discord_template_failed`.
- Templates are validated live from ConfigWizard via
  `POST /api/validate/discord-template`, and re-validated server-side on
  `PUT /api/config`.
- Delivery is fire-and-forget (`create_task`), 10s httpx timeout, all errors swallowed to
  a `logger.warning`.

### Problems identified

1. **Disc identity is absent from the payload.** `volume_label`, `disc_number`, and
   `discdb_disc_slug` all exist on `DiscJob` but are not exposed to templates.
2. **`{{drive}}` is misnamed.** It renders `job.volume_label`, not `drive_id`
   (`discord_notifier.py:72`), and the ConfigWizard hint reinforces the wrong meaning.
   Changing its value would silently alter existing saved templates.
3. **`notify_discord` hardcodes a two-state binary.** A review event would render as
   "Disc Review_Needed" with a red error color.
4. **No structured embed.** Everything is crammed into one description string; no fields,
   timestamp, footer, thumbnail, or link.
5. **No way to verify a webhook.** The URL is redacted to `***` after save, so a user
   cannot confirm it works until the next disc finishes.
6. **Weak notification delivery.** Discord embeds without message `content` produce a
   low-salience notification. A review ping that does not reach the user's phone, and
   does not link to the thing needing review, does not do its job.

## Scope

In scope:

- Structured embed with guaranteed disc identity (request 1)
- Review-needed notifications (request 2)
- Mention on review; clickable deep link to the review UI
- Per-event enable toggles (completed / failed / review)
- "Send test message" button and backing endpoint
- Episode manifest on completion
- TMDB poster thumbnail
- `{{drive}}` back-compat fix

Out of scope (deliberately deferred):

- Non-Discord notification transports (ntfy, Apprise, generic webhook)
- Mentions on events other than review
- Notification batching or rate limiting

## Design

### Section 1: Config surface

Six new `AppConfig` fields:

| Field | Type | Default | Notes |
|---|---|---|---|
| `discord_template_review` | `str` | `""` | Third template; `""` means use the built-in default |
| `discord_notify_completed` | `bool` | `True` | `sa_column_kwargs={"server_default": text("1")}` |
| `discord_notify_failed` | `bool` | `True` | same |
| `discord_notify_review` | `bool` | `True` | same |
| `discord_mention_review` | `str` | `""` | e.g. `<@1234>` or `@here`; sent as message `content` |
| `dashboard_base_url` | `str` | `""` | e.g. `http://192.168.1.50:5173` |

Each field must be added in **four** places or Pydantic silently drops it on update:
`AppConfig` (model), `ConfigUpdate` (request), `ConfigResponse` (response), and
`ConfigWizard` (frontend state plus save payload). An Alembic revision is required; frozen
builds converge schema through the `database.py` reconcilers rather than Alembic.

None of the new fields are sensitive, so all are returned unredacted from
`GET /api/config`, unlike `discord_webhook_url`, which stays `***`.

**Validation: `dashboard_base_url` must not use `is_safe_remote_url`.** That guard rejects
private and internal hosts, and a LAN dashboard address is precisely what this field
holds. It needs its own narrow check instead:

- scheme in `{http, https}`
- non-empty host
- no embedded credentials (`user:pass@`)

The purpose is rejecting `javascript:` and similar, because the value becomes a clickable
embed `url`. Trailing slashes are normalized away before joining `/history/{job_id}`.

`discord_mention_review` requires no sanitizing. Discord does not resolve mentions inside
embeds, only in message `content`, and `content` carries nothing but this configured
string. Rendered template output never reaches `content`.

### Section 2: `discord_notifier.py` refactor

Replace the `state == "completed"` binary with an event table:

```python
@dataclass(frozen=True)
class NotificationEvent:
    key: str      # "completed" | "failed" | "review"
    label: str    # "Disc Completed" | "Disc Failed" | "Review Needed"
    emoji: str    # checkmark / cross / magnifier
    color: int    # green / red / amber

EVENTS: dict[JobState, NotificationEvent]
```

New module functions:

- `build_embed_fields(job, titles, event) -> list[dict]`
- `summarize_episodes(titles) -> str`
- `build_embed(job, titles, event, description, poster_url, link_url) -> dict`

`notify_discord(webhook_url, job_id, embed, content="")` takes a prebuilt embed and stops
knowing anything about job semantics.

Fields are **skip-if-empty**, so a movie never renders a blank Season row:

| Field | Value | Shown when |
|---|---|---|
| Disc | `ARRESTED_DEVELOPMENT_S1D1 (Disc 1)` | always (see fallback below) |
| Season | `Season 1` | TV |
| Episodes | manifest (see Section 3) | COMPLETED and TV |
| Duration | `1h 24m` | `completed_at` set |
| Tracks | `12 titles` | `total_titles > 0` |
| Subtitles | `18/20, 2 failed` | `subtitle_status` set |
| Reason | `review_reason` or `error_message` | review / failed |
| Library | `final_path` | COMPLETED |

Disc field fallback chain: `volume_label`, then `discdb_disc_slug`, then
`Disc {disc_number}`. `disc_number` is appended in parentheses when `volume_label` is
present and `disc_number > 1`, or when `discdb_disc_slug` is absent.

The embed additionally carries `timestamp` (ISO-8601), `footer` (Engram version),
`thumbnail` (Section 4), and `url` (deep link, when `dashboard_base_url` is configured).

### Section 3: Episode manifest

`summarize_episodes(titles)` filters to `TitleState.COMPLETED`, non-extra rows, parses
`matched_episode` in `SxxEyy` form, sorts, and collapses contiguous runs:

- contiguous: `S01E01-E04 (4 episodes)`
- gapped: `S01E01-E03, S01E06 (4 episodes)`
- single: `S01E01 (1 episode)`
- nothing matched: `""`, which the skip-if-empty rule drops

Rows whose `matched_episode` does not parse are counted in the total but not placed in a
range.

Movies use `detected_title (tmdb_year)` plus edition when set.

This requires `_send_discord_notification` to load `DiscTitle` rows for the job; today it
performs only `session.get(DiscJob, job_id)`. The notification runs as a background task,
so the extra query is off the critical path.

### Section 4: Poster thumbnail

The TMDB poster lookup is currently inlined in `get_job_poster`
(`backend/app/api/routes.py:1830`). Extract it to
`app/core/tmdb_poster.py::resolve_poster_url(job) -> str | None`; the route becomes a thin
wrapper with no behavior change.

The notifier calls it best-effort inside `try/except`. A TMDB failure or timeout costs the
thumbnail, never the notification.

### Section 5: Review trigger

`REVIEW_NEEDED` is not terminal, so `on_terminal_state` cannot carry it. The correct
chokepoint is `on_transition`, which the transcript prewarmer already uses for exactly
this purpose: every review-parking path flows through it, and it fires only after the
transition has committed.

`on_transition` callbacks currently receive `(job_id, to_state)`. Without `from_state`
there is no way to distinguish a genuine entry into review from a same-state re-broadcast,
since `transition()` returns early-True when `from_state == to_state` and still
re-broadcasts.

**Contract change:** widen `on_transition` callbacks to `(job_id, to_state, from_state)`.
`job_state_machine.py:159` already holds `from_state` in scope. The two existing
observers gain a third parameter they ignore:

- `JobManager._note_activity_on_transition` (`job_manager.py:1177`)
- `JobManager._start_prewarm_on_review` (`job_manager.py:1184`)

New observer:

```python
def _notify_discord_on_review(self, job_id, to_state, from_state) -> None:
    if to_state != JobState.REVIEW_NEEDED or from_state == JobState.REVIEW_NEEDED:
        return
    asyncio.create_task(self._send_discord_notification(job_id, to_state))
```

`on_transition` observers are synchronous, so `create_task` matches the existing
prewarmer pattern.

**Dedup rule:** suppress only same-state re-entry. A job that goes
`REVIEW_NEEDED -> IDENTIFYING -> REVIEW_NEEDED` (re-identify, still ambiguous) re-notifies.
That is a new question for the user, not a duplicate of the previous one.

`_send_discord_notification(job_id, state)` generalizes:

1. Map `state` to a `NotificationEvent`; return if the state is not one of the three.
2. Return if the per-event toggle for that event is off.
3. Select the matching template; fall back to the built-in default when blank.
4. Build context, render description, build fields, resolve poster, build link.
5. Attach `content` (mention) only for the review event.
6. Attach embed `url` only when `dashboard_base_url` is set.
7. POST.

### Section 6: Template variables

Added to `ALLOWED_TEMPLATE_VARS`: `volume_label`, `drive_id`, `disc_number`,
`discdb_disc_slug`, `review_reason`, `episodes`, `state`.

`{{drive}}` continues to render `volume_label` so existing saved templates do not silently
change meaning. The ConfigWizard hint relabels it as a deprecated alias for
`{{volume_label}}`.

Built-in defaults stay `**{{{title}}}**` for all three events, including the new
`DEFAULT_TEMPLATE_REVIEW`. The structured fields now carry the detail, so no existing
user's output regresses and the disc identity appears without anyone editing a template.

### Section 7: Test-webhook endpoint

`POST /api/validate/discord-webhook`, body `{webhook_url?: str}`.

- Blank or omitted `webhook_url` means use the saved config value, so a user can test an
  already-saved (redacted) webhook.
- **Must** run the same `is_safe_remote_url` guard as `PUT /api/config`. Without it the
  endpoint is an unauthenticated SSRF probe against internal hosts.
- Posts a sample embed built from a synthetic `DiscJob` through the real `build_embed`
  path, so a test message exercises the production builder.
- Returns the existing `ValidationResponse` shape.

### Section 8: Frontend

ConfigWizard, Notifications group gains:

- Review template textarea. The debounced live-validation effect goes from two templates
  to three.
- Three toggle checkboxes (completed / failed / review).
- Mention input, with a hint that it accepts `<@userid>`, `<@&roleid>`, or `@here`.
- Dashboard base URL input, with a hint explaining it makes notifications clickable.
- "Send test message" button with `idle | testing | ok | error` status.

The variables hint table is updated for the seven new variables and the `{{drive}}`
deprecation note.

`frontend/src/utils/discordTemplateValidation.ts` needs no changes.

## Testing

**Unit, `backend/tests/unit/test_discord_notifier.py`**

- Event table maps each of the three states to the right label, emoji, and color.
- `build_embed_fields` skip-empty behavior for a movie vs a TV job.
- Disc field fallback chain (`volume_label`, `discdb_disc_slug`, `Disc N`).
- `summarize_episodes`: contiguous, gapped, single, empty, unparseable rows, movie.
- New template variables pass validation; unknown ones still fail.
- Mention attached only on review.
- Embed `url` present only when `dashboard_base_url` is set.
- Poster resolution failure degrades to an embed with no thumbnail.

**Unit, job_manager**

- Each per-event toggle suppresses its own event and no other.
- Review notification fires on entry to `REVIEW_NEEDED`.
- Review notification suppressed when `from_state == REVIEW_NEEDED`.
- Review notification re-fires after `REVIEW_NEEDED -> IDENTIFYING -> REVIEW_NEEDED`.

**Unit, validation**

- `dashboard_base_url` accepts a LAN http address, rejects `javascript:` and credentialed
  URLs.
- `POST /api/validate/discord-webhook` rejects internal hosts.

**Regression risk**

- `backend/tests/unit/test_completion_skipped.py` and
  `backend/tests/unit/test_watchdog_cancel_attribution.py` exercise the transition path
  and may break on the `on_transition` signature widening.
- Any test that drives a job to a terminal state must stub
  `_send_discord_notification`, or the leaked `create_task` holds a StaticPool connection
  open past the test.

## Build order

1. Config fields, migration, four-way sync, validation (Section 1)
2. Notifier refactor: event table, embed builder, fields (Section 2). **Request 1 lands
   here.**
3. Episode manifest (Section 3) and poster extraction (Section 4)
4. `on_transition` widening, review trigger, unified send path (Sections 5 and 6).
   **Request 2 lands here.**
5. Test-webhook endpoint (Section 7)
6. ConfigWizard UI (Section 8), docs, CHANGELOG

## Risks

- **`on_transition` signature widening** touches the stale-job watchdog and the transcript
  prewarmer. Both changes are one line each, but the watchdog is load-bearing for
  stuck-job recovery.
- **`dashboard_base_url` is deliberately exempt from `is_safe_remote_url`.** This is
  correct, because the server never fetches the URL and only embeds it as a link, but the
  exemption must be documented at the call site so a later security pass does not "fix"
  it into uselessness.
- **Config field sync** is the recurring failure mode for this codebase: a field present
  in `AppConfig` but missing from `ConfigUpdate` is silently dropped with no error.
