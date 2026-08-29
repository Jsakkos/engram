# Finished-Ripping Discord Notification

**Date:** 2026-08-29
**Status:** Approved, ready for implementation planning

## Problem

Engram's Discord notifications fire on three job states: `COMPLETED`, `FAILED`,
and `REVIEW_NEEDED`. All three are keyed to *what the job needs next*, not to
*what the hardware just finished*.

The consequence is that a disc which cannot be automatically identified goes
`RIPPING -> REVIEW_NEEDED`, then sits until a human finishes the review, and only
then reaches `COMPLETED`. There is no event at the moment the bits come off the
disc and the tray opens.

For a user whose workflow is "swap discs as they finish, review later in a
batch", that is the only moment they actually care about. Today they either get
a Review Needed ping (which arrives at the right time but is about the wrong
thing, and is absent for discs that identify cleanly) or a Completed ping (which
for a review disc arrives hours later, after they have already walked away).

## Goal

Add a fourth notification event that fires when Engram is finished with the
drive, independent of whether the job later parks in review.

## Non-goals

- Reworking the notification config into levels or presets. Four independent
  checkboxes stay legible; a preset layer would add a second source of truth for
  the same booleans.
- Notifications for import jobs. Imports skip `RIPPING` entirely, so they never
  reach an eject site. This is correct: there is no disc.
- Any change to the existing three events' behavior or defaults.

## Design

### The event

"Ripped" is not a `JobState`, so it cannot live in `EVENTS: dict[JobState,
NotificationEvent]`. It is declared beside that table instead:

```python
RIPPED_EVENT = NotificationEvent("ripped", "Disc Ripped", "💿", 0x3B82F6)
```

Keeping it out of `EVENTS` preserves the meaning of that dict as "notifiable job
states" and leaves `test_event_table_covers_the_three_notifiable_states` and
`test_event_table_has_no_entry_for_transient_states` green and untouched.

### Notifier generalization

`JobManager._send_discord_notification(job_id, state)` currently derives the
event key from a `JobState`. It changes to:

```python
async def _send_discord_notification(
    self, job_id: int, event: NotificationEvent, *, extra_context: dict | None = None
) -> None
```

The `EVENTS.get(state)` lookup and its "non-notifiable state" warning move up
into the two existing callers, `_notify_discord_on_terminal` and
`_notify_discord_on_review`. Everything downstream of the lookup is already
keyed on `event.key` and needs no structural change:

- toggles dict gains `"ripped": config.discord_notify_ripped`
- templates dict gains `"ripped": config.discord_template_ripped`
- `DEFAULT_TEMPLATES` gains `"ripped": "**{{{title}}}**"`

### Rip outcome

The event fires from three code paths with different meanings. Rather than three
labels, one label carries a `Status` field:

| Path | `rip_outcome` |
|------|---------------|
| Normal end of rip | `Complete` |
| Mid-rip eject (user pressed eject) | `Stopped early` |
| End of a re-rip | `Re-rip` |

`rip_outcome` joins `ALLOWED_TEMPLATE_VARS` and is fed from `extra_context`, not
from the `DiscJob` row: the outcome is knowledge the call site has and the row
does not. `build_template_context` renders it empty for every other event, in
keeping with the existing "empty value drops" convention.

`build_embed_fields` gains a `Status` field emitted only when
`event.key == "ripped"`, alongside the existing conditional `Episodes`,
`Reason`, and `Library` fields.

### Trigger: one chokepoint, three call sites

All three eject sites already perform the same pair of operations,
`eject_disc(drive_id)` followed by `self._drive_monitor.notify_ejected(drive_id)`,
and two of them wrap it in an identical `auto_eject_enabled` check and an
identical `except (OSError, RuntimeError)` handler. That duplication exists
today. The notification is the third thing that belongs with it.

```python
async def _release_drive(
    self, job_id: int, drive_id: str, outcome: str, *, respect_auto_eject: bool = True
) -> bool:
    """Finished with the drive: eject if configured, re-arm the sentinel, notify."""
```

`respect_auto_eject=False` serves `eject_disc_for_job`, which ejects
unconditionally and needs the boolean result back to tell the user whether the
tray actually opened.

The notification fires outside the eject branch, not inside it. When
`auto_eject_enabled` is off no tray opens, but the disc is still copied and the
user still wants to know.

Call sites:

| Site | Function | Outcome |
|------|----------|---------|
| `job_manager.py` ~3163 | `_run_ripping`, end of rip | `Complete` |
| `job_manager.py` ~2595 | `rerip_titles` | `Re-rip` |
| `job_manager.py` ~1264 | `eject_disc_for_job` | `Stopped early` |

`rerip_title_manual` delegates to `rerip_titles`, so the middle row covers both
the automatic and the manual re-rip. There are three sites, not four.

**Rejected alternative:** hooking the state machine's `on_transition` on leaving
`RIPPING`. It is elegant and consistent with how `REVIEW_NEEDED` is wired, but a
re-rip runs while the job sits in `REVIEW_NEEDED` and never enters `RIPPING`, so
it structurally cannot cover that path.

### Carve-outs

Three behaviors fall out of the existing control flow and must be preserved
deliberately:

1. **No double-fire on abort.** The site-1 eject is already guarded by
   `and not ejected_mid_rip`. That flag now also means "site 3 already sent the
   notification", so the notify inherits the same guard. Without it, a mid-rip
   abort produces two pings.
2. **Site 3 fires only from `RIPPING`.** `eject_disc_for_job` also serves
   `IDENTIFYING`, where it ejects and cancels the job. Nothing was copied, so
   "Disc Ripped" would be false. The notify goes inside the existing
   `state == JobState.RIPPING` branch, not above the fork.
3. **A hard MakeMKV failure never reaches site 1.** `_run_ripping` returns via
   `_fail_job` above the eject when `not result.success and not
   result.stalled_titles`. Such a disc gets the Failed event and no ripped
   event. This is the one documented exception to "ripped fires for every disc".

Delivery follows the existing pattern: `asyncio.create_task(...)`,
fire-and-forget, so an unreachable webhook can never stall the pipeline or delay
the eject.

## Configuration

Two new `AppConfig` fields:

```python
discord_notify_ripped: bool = Field(default=False, sa_column_kwargs={"server_default": text("0")})
discord_template_ripped: str = ""
```

**The default is off, and the `server_default` is `0`, unlike the other three
toggles.** Existing users must see no change in notification volume on upgrade.
The notifier's `is False` check treats NULL as enabled, a deliberate choice so an
out-of-band schema change can never silently mute a user's notifications. For a
new opt-in event that rationale inverts: a NULL must read as off. The comment
above the toggle block in `app_config.py` needs amending to record that this
field is the exception and why.

Alembic migration follows `b7d3f9a1c204_add_discord_notification_settings.py`.

### Three-way sync

A field missed in any leg is silently dropped by Pydantic:

1. `AppConfig` (`app/models/app_config.py`) plus the Alembic migration
2. `ConfigUpdate` and `ConfigResponse` (`app/api/routes.py`)
3. `ConfigWizard.tsx`: interface field, initial state, load mapping with
   `?? false`, save payload, checkbox, template textarea

Plus a fourth leg specific to templates: `discordTemplateErrors` is typed
`{completed; failed; review}` with a matching three-way `Promise.all` in the
debounced live validator. Both need a `ripped` key, or the new template silently
skips validation while the other three keep working.

## Testing

**Notifier unit tests** (`tests/unit/test_discord_notifier.py`):

- ripped event respects `discord_notify_ripped` and is off by default
- `{{rip_outcome}}` renders in a custom template and is empty for other events
- the `Status` field appears only for `event.key == "ripped"`
- `test_app_config_notification_defaults` extends to assert the new default

**Trigger tests:**

- one per call site, asserting the outcome string reaching the notifier
- the abort path fires exactly once, guarding the double-fire carve-out
- `eject_disc_for_job` from `IDENTIFYING` fires nothing
- a hard rip failure fires Failed and not Ripped

**Frontend:** extend `ConfigWizard.test.tsx` for the round-trip of both new
fields, including that the template participates in live validation.

**Hazard to avoid:** unit tests that drive a job to a terminal state leak a
Discord `create_task` holding a StaticPool connection. The ripped event is
non-terminal, so these tests can exercise it without pushing the job terminal.

## Documentation

`CLAUDE.md`'s "Notification events" bullet describes `EVENTS` as the map from
`JobState` to presentation. It needs a sentence noting that the ripped event
sits outside that map because it is a hardware milestone rather than a state,
and fires from `_release_drive`.
