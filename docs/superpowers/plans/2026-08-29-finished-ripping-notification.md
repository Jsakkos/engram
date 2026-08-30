# Finished-Ripping Notification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a fourth Discord notification event that fires when Engram finishes with the optical drive, so a disc that parks in review still pings at the moment it is copied and ejected.

**Architecture:** A `RIPPED_EVENT` declared beside (not inside) the `EVENTS: dict[JobState, ...]` table, because "ripped" is a hardware milestone rather than a job state. `JobManager._send_discord_notification` is generalized from taking a `JobState` to taking a `NotificationEvent` plus optional extra template context. A new `_release_drive()` helper becomes the single chokepoint for the three places that finish with the drive, replacing three near-duplicate eject blocks.

**Tech Stack:** Python 3.11 / FastAPI / SQLModel / Alembic / pytest on the backend; React 18 + TypeScript + Vitest on the frontend. `uv` for all Python commands.

**Spec:** `docs/superpowers/specs/2026-08-29-finished-ripping-notification-design.md`

---

## File Structure

**Modify:**

- `backend/app/models/app_config.py` : two new config fields
- `backend/migrations/versions/<new>_add_discord_ripped_notification.py` : new file, adds the two columns
- `backend/app/api/routes.py` : `ConfigResponse`, `ConfigUpdate`, the template-validation field list, the `get_config` response builder
- `backend/app/core/discord_notifier.py` : `RIPPED_EVENT`, `DEFAULT_TEMPLATES`, `ALLOWED_TEMPLATE_VARS`, `build_template_context`, `build_embed_fields`
- `backend/app/services/job_manager.py` : notifier signature, `_release_drive`, three call sites
- `frontend/src/components/ConfigWizard.tsx` : six separate legs (see Task 6)
- `CLAUDE.md`, `CHANGELOG.md`

**Test:**

- `backend/tests/unit/test_discord_notifier.py` : notifier + config defaults + signature
- `backend/tests/unit/test_disc_eject.py` : `_release_drive` and the three call sites (this file already owns the eject stubs)
- `frontend/src/components/ConfigWizard.test.tsx` : round-trip of both new fields

**Repo conventions that apply throughout:**

- All Python commands run through `uv` from `backend/`: `uv run pytest`, `uv run ruff check .`
- Ruff: line length 100, double quotes
- Commit style: one commit per task, imperative subject

---

## Task 1: Config fields and migration

**Files:**
- Modify: `backend/app/models/app_config.py:213-240`
- Create: `backend/migrations/versions/a4c81f6b2e39_add_discord_ripped_notification.py`
- Test: `backend/tests/unit/test_discord_notifier.py:528`

- [ ] **Step 1: Write the failing test**

Replace the existing `test_app_config_notification_defaults` in
`backend/tests/unit/test_discord_notifier.py` with this version, and add the
second test directly below it:

```python
def test_app_config_notification_defaults():
    """New notification fields default to on, with blank templates/mention/link."""
    from app.models.app_config import AppConfig

    config = AppConfig()
    assert config.discord_template_review == ""
    assert config.discord_notify_completed is True
    assert config.discord_notify_failed is True
    assert config.discord_notify_review is True
    assert config.discord_mention_review == ""
    assert config.dashboard_base_url == ""


def test_ripped_notification_defaults_off():
    """The one toggle that defaults OFF: an existing user's channel must not
    double in volume just because they upgraded."""
    from app.models.app_config import AppConfig

    config = AppConfig()
    assert config.discord_notify_ripped is False
    assert config.discord_template_ripped == ""


def test_ripped_column_server_default_is_zero():
    """The other three toggles carry server_default 1 so a NULL reads as
    enabled and can never silently mute someone. For a new opt-in event that
    rationale inverts: a NULL must read as OFF, or upgrading turns it on."""
    from app.models.app_config import AppConfig

    column = AppConfig.__table__.columns["discord_notify_ripped"]
    assert str(column.server_default.arg) == "0"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd backend && uv run pytest tests/unit/test_discord_notifier.py -k "ripped" -v
```

Expected: FAIL with `AttributeError: 'AppConfig' object has no attribute 'discord_notify_ripped'`

- [ ] **Step 3: Add the fields**

In `backend/app/models/app_config.py`, immediately after the
`discord_notify_review` field, add:

```python
    # The one toggle that defaults OFF, and the one whose server_default is 0.
    # The three above read a NULL as enabled (see _send_discord_notification's
    # `is False` check) so an out-of-band schema change can never silently mute
    # a user's notifications. This event is new and opt-in, so that rationale
    # inverts: a NULL here must read as off, or upgrading switches it on for
    # everyone and doubles the message volume of anyone already using
    # discord_notify_completed.
    discord_notify_ripped: bool = Field(
        default=False, sa_column_kwargs={"server_default": text("0")}
    )
```

And immediately after `discord_template_review`, add:

```python
    discord_template_ripped: str = ""
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd backend && uv run pytest tests/unit/test_discord_notifier.py -k "ripped or notification_defaults" -v
```

Expected: PASS (3 tests)

- [ ] **Step 5: Write the Alembic migration**

Create `backend/migrations/versions/a4c81f6b2e39_add_discord_ripped_notification.py`.
`c3e17a2b8d40` is the current head; verify with
`cd backend && uv run alembic heads` before committing, and correct
`down_revision` if it has moved.

```python
"""add discord ripped notification settings

Revision ID: a4c81f6b2e39
Revises: c3e17a2b8d40
Create Date: 2026-08-29 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a4c81f6b2e39"
down_revision: str | Sequence[str] | None = "c3e17a2b8d40"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "app_config",
        sa.Column(
            "discord_template_ripped", sa.String(), nullable=False, server_default=sa.text("''")
        ),
    )
    # server_default 0, unlike the other three notify toggles: this event is
    # opt-in, so an upgraded row must come back OFF.
    op.add_column(
        "app_config",
        sa.Column(
            "discord_notify_ripped", sa.Boolean(), nullable=False, server_default=sa.text("0")
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("app_config", schema=None) as batch_op:
        batch_op.drop_column("discord_notify_ripped")
        batch_op.drop_column("discord_template_ripped")
```

- [ ] **Step 6: Verify the migration applies cleanly**

```bash
cd backend && uv run alembic heads
```

Expected: exactly one head, `a4c81f6b2e39 (head)`. If two heads print, the
`down_revision` above is stale; set it to the other head and re-run.

- [ ] **Step 7: Commit**

```bash
git add backend/app/models/app_config.py backend/migrations/versions/a4c81f6b2e39_add_discord_ripped_notification.py backend/tests/unit/test_discord_notifier.py
git commit -m "feat(config): add discord_notify_ripped and discord_template_ripped"
```

---

## Task 2: API layer plumbing

A field missing from any one of `AppConfig`, `ConfigUpdate`, or `ConfigResponse`
is silently dropped by Pydantic with no error. This task covers the two API legs.

**Files:**
- Modify: `backend/app/api/routes.py:350-358` (ConfigResponse), `:446-454` (ConfigUpdate), `:1717-1731` (response builder), `:1827-1836` (template validation list)
- Test: `backend/tests/unit/test_api_routes.py`

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/unit/test_api_routes.py`:

```python
@pytest.mark.asyncio
async def test_config_round_trips_ripped_notification_fields(client):
    """The three-way sync: a field missing from ConfigUpdate or ConfigResponse
    is dropped silently, so assert it survives a PUT and comes back on GET."""
    resp = await client.put(
        "/api/config",
        json={"discord_notify_ripped": True, "discord_template_ripped": "**{{{title}}}** ripped"},
    )
    assert resp.status_code == 200

    resp = await client.get("/api/config")
    assert resp.status_code == 200
    body = resp.json()
    assert body["discord_notify_ripped"] is True
    assert body["discord_template_ripped"] == "**{{{title}}}** ripped"


@pytest.mark.asyncio
async def test_config_rejects_unknown_var_in_ripped_template(client):
    """The ripped template must be validated server-side like the other three."""
    resp = await client.put(
        "/api/config", json={"discord_template_ripped": "{{nonsense_variable}}"}
    )
    assert resp.status_code == 400
    assert "nonsense_variable" in resp.json()["detail"]
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd backend && uv run pytest tests/unit/test_api_routes.py -k "ripped" -v
```

Expected: FAIL. The first with `KeyError: 'discord_notify_ripped'`, the second
with `assert 200 == 400` (the unknown variable is not yet validated).

- [ ] **Step 3: Add the field to ConfigResponse**

In `backend/app/api/routes.py`, in the `ConfigResponse` class after
`discord_notify_review: bool = True`:

```python
    discord_notify_ripped: bool = False
```

And after `discord_template_review: str = ""`:

```python
    discord_template_ripped: str = ""
```

- [ ] **Step 4: Add the field to ConfigUpdate**

In the `ConfigUpdate` class, after `discord_notify_review: bool | None = None`:

```python
    discord_notify_ripped: bool | None = None
```

And after `discord_template_review: str | None = None`:

```python
    discord_template_ripped: str | None = None
```

- [ ] **Step 5: Add the field to the response builder**

At `routes.py:1723`, after `discord_template_review=config.discord_template_review or "",`:

```python
        discord_template_ripped=config.discord_template_ripped or "",
```

At `routes.py:1729`, after `discord_notify_review=config.discord_notify_review is not False,`:

```python
        # `is True`, not `is not False`: the other three read a NULL as enabled,
        # this one must read a NULL as disabled. See the field comment in
        # app_config.py.
        discord_notify_ripped=config.discord_notify_ripped is True,
```

- [ ] **Step 6: Add the template to the validation list**

At `routes.py:1830`, extend the tuple of validated template fields:

```python
        "discord_template_completed",
        "discord_template_failed",
        "discord_template_review",
        "discord_template_ripped",
```

- [ ] **Step 7: Run tests to verify they pass**

```bash
cd backend && uv run pytest tests/unit/test_api_routes.py -k "ripped" -v
```

Expected: PASS (2 tests)

- [ ] **Step 8: Commit**

```bash
git add backend/app/api/routes.py backend/tests/unit/test_api_routes.py
git commit -m "feat(api): expose discord ripped notification config"
```

---

## Task 3: The notifier event

**Files:**
- Modify: `backend/app/core/discord_notifier.py:33-40` (EVENTS), `:42-70` (ALLOWED_TEMPLATE_VARS), `:72-83` (DEFAULT_TEMPLATES), `:118-155` (build_template_context), `:243-270` (build_embed_fields)
- Test: `backend/tests/unit/test_discord_notifier.py`

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/unit/test_discord_notifier.py`:

```python
# --------------------------------------------------------------------------- #
# Ripped event
# --------------------------------------------------------------------------- #


def test_ripped_event_is_not_in_the_state_keyed_table():
    """RIPPED_EVENT lives beside EVENTS, not in it: "ripped" is a hardware
    milestone, not a JobState, and EVENTS means "notifiable job states"."""
    from app.core.discord_notifier import EVENTS, RIPPED_EVENT
    from app.models.disc_job import JobState

    assert RIPPED_EVENT not in EVENTS.values()
    assert set(EVENTS) == {JobState.COMPLETED, JobState.FAILED, JobState.REVIEW_NEEDED}
    assert RIPPED_EVENT.key == "ripped"
    assert RIPPED_EVENT.label == "Disc Ripped"


def test_default_template_exists_for_ripped():
    from app.core.discord_notifier import DEFAULT_TEMPLATES

    assert DEFAULT_TEMPLATES["ripped"] == "**{{{title}}}**"


def test_rip_outcome_is_an_allowed_template_var():
    from app.core.discord_notifier import ALLOWED_TEMPLATE_VARS, validate_discord_template

    assert "rip_outcome" in ALLOWED_TEMPLATE_VARS
    assert validate_discord_template("{{title}} was {{rip_outcome}}") is None


def test_build_template_context_renders_rip_outcome_empty_by_default():
    """The outcome is knowledge the call site has and the DiscJob row does not,
    so the row-derived context leaves it blank."""
    job = DiscJob(drive_id="E:", content_type=ContentType.TV, detected_title="The Wire")
    context = build_template_context(job, 1)
    assert context["rip_outcome"] == ""


def test_status_field_appears_only_on_the_ripped_event():
    from app.core.discord_notifier import EVENTS, RIPPED_EVENT, build_embed_fields
    from app.models.disc_job import JobState

    job = DiscJob(drive_id="E:", content_type=ContentType.MOVIE, detected_title="Inception")

    ripped = build_embed_fields(job, [], RIPPED_EVENT, rip_outcome="Stopped early")
    assert {"name": "Status", "value": "Stopped early", "inline": True} in ripped

    completed = build_embed_fields(job, [], EVENTS[JobState.COMPLETED], rip_outcome="Complete")
    assert not [f for f in completed if f["name"] == "Status"]


def test_status_field_dropped_when_outcome_is_blank():
    """Empty values drop, matching how Season, Reason and Library behave."""
    from app.core.discord_notifier import RIPPED_EVENT, build_embed_fields

    job = DiscJob(drive_id="E:", content_type=ContentType.MOVIE, detected_title="Inception")
    fields = build_embed_fields(job, [], RIPPED_EVENT, rip_outcome="")
    assert not [f for f in fields if f["name"] == "Status"]
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd backend && uv run pytest tests/unit/test_discord_notifier.py -k "ripped or rip_outcome or status_field" -v
```

Expected: FAIL with `ImportError: cannot import name 'RIPPED_EVENT'`

- [ ] **Step 3: Declare the event**

In `backend/app/core/discord_notifier.py`, directly below the `EVENTS` dict:

```python
# Deliberately NOT in EVENTS: "the disc is copied and out of the drive" is a
# hardware milestone, not a JobState, and it must fire for a disc that goes on
# to park in REVIEW_NEEDED. Delivered from JobManager._release_drive rather than
# from a state-machine callback.
RIPPED_EVENT = NotificationEvent("ripped", "Disc Ripped", "💿", 0x3B82F6)
```

- [ ] **Step 4: Register the template var and default**

Add `"rip_outcome",` to the `ALLOWED_TEMPLATE_VARS` frozenset, and add to
`DEFAULT_TEMPLATES`:

```python
DEFAULT_TEMPLATE_RIPPED = "**{{{title}}}**"

DEFAULT_TEMPLATES = {
    "completed": DEFAULT_TEMPLATE_COMPLETED,
    "failed": DEFAULT_TEMPLATE_FAILED,
    "review": DEFAULT_TEMPLATE_REVIEW,
    "ripped": DEFAULT_TEMPLATE_RIPPED,
}
```

`build_template_context` needs no change for the `job is None` branch: it
already fills every var in `ALLOWED_TEMPLATE_VARS` with `""`. For the populated
branch, add to the returned dict:

```python
        "rip_outcome": "",
```

with the comment:

```python
        # Filled by the caller via extra_context, not derived from the row:
        # whether this was a clean rip, a mid-rip abort or a re-rip is known
        # only at the eject site.
```

- [ ] **Step 5: Add the Status field**

Change the `build_embed_fields` signature and add the field. New signature:

```python
def build_embed_fields(
    job: DiscJob | None, titles: list, event: NotificationEvent, *, rip_outcome: str = ""
) -> list[dict]:
```

Add as the first entry of the `candidates` list, above the existing `Disc` field:

```python
        _field("Status", rip_outcome if event.key == "ripped" else ""),
```

- [ ] **Step 6: Thread it through build_embed**

Add a `rip_outcome: str = ""` keyword-only parameter to `build_embed` and pass
it down:

```python
def build_embed(
    job: DiscJob | None,
    titles: list,
    event: NotificationEvent,
    description: str,
    *,
    poster_url: str | None = None,
    link_url: str | None = None,
    rip_outcome: str = "",
) -> dict:
```

and in the body change the `fields` entry to:

```python
        "fields": build_embed_fields(job, titles, event, rip_outcome=rip_outcome),
```

- [ ] **Step 7: Run tests to verify they pass**

```bash
cd backend && uv run pytest tests/unit/test_discord_notifier.py -v
```

Expected: PASS, all tests in the file including the pre-existing ones.

- [ ] **Step 8: Commit**

```bash
git add backend/app/core/discord_notifier.py backend/tests/unit/test_discord_notifier.py
git commit -m "feat(notifier): add RIPPED_EVENT with a rip_outcome status field"
```

---

## Task 4: Generalize the notifier dispatch

`_send_discord_notification(job_id, state)` derives the event from a `JobState`.
The ripped event has no state, so the lookup moves up into the callers.

**Files:**
- Modify: `backend/app/services/job_manager.py:1407-1525`
- Test: `backend/tests/unit/test_discord_notifier.py:274-525`

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/unit/test_discord_notifier.py`:

```python
@pytest.mark.asyncio
async def test_send_notification_accepts_an_event_and_extra_context():
    """The generalized signature: callers pass a NotificationEvent, and
    extra_context supplies vars the DiscJob row cannot provide."""
    from app.core.discord_notifier import RIPPED_EVENT
    from app.database import async_session
    from app.services.config_service import update_config
    from app.services.job_manager import job_manager

    await update_config(
        discord_webhook_url="https://discord.com/api/webhooks/1/tok",
        discord_notify_ripped=True,
        discord_template_ripped="{{title}} was {{rip_outcome}}",
    )

    async with async_session() as session:
        job = DiscJob(
            drive_id="E:",
            content_type=ContentType.TV,
            detected_title="The Wire",
            volume_label="THE_WIRE_S1D1",
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)
        job_id = job.id

    with patch("app.core.discord_notifier.notify_discord", new_callable=AsyncMock) as mock_notify:
        await job_manager._send_discord_notification(
            job_id, RIPPED_EVENT, extra_context={"rip_outcome": "Stopped early"}
        )

    mock_notify.assert_called_once()
    embed = mock_notify.call_args[0][2]
    assert embed["description"] == "The Wire was Stopped early"
    assert {"name": "Status", "value": "Stopped early", "inline": True} in embed["fields"]


@pytest.mark.asyncio
async def test_ripped_notification_suppressed_when_toggle_off():
    """Default-off means an upgraded user gets nothing until they opt in."""
    from app.core.discord_notifier import RIPPED_EVENT
    from app.database import async_session
    from app.services.config_service import update_config
    from app.services.job_manager import job_manager

    await update_config(
        discord_webhook_url="https://discord.com/api/webhooks/1/tok",
        discord_notify_ripped=False,
    )

    async with async_session() as session:
        job = DiscJob(drive_id="E:", content_type=ContentType.TV, detected_title="The Wire")
        session.add(job)
        await session.commit()
        await session.refresh(job)
        job_id = job.id

    with patch("app.core.discord_notifier.notify_discord", new_callable=AsyncMock) as mock_notify:
        await job_manager._send_discord_notification(
            job_id, RIPPED_EVENT, extra_context={"rip_outcome": "Complete"}
        )

    mock_notify.assert_not_called()
```

- [ ] **Step 2: Update the existing call sites in the test file**

The signature change breaks eight existing tests that pass a `JobState`
positionally. In `backend/tests/unit/test_discord_notifier.py`, rename the call
in each of these to `_send_discord_notification_for_state`:

- `test_send_notification_noop_when_no_webhook`
- `test_send_notification_noop_for_non_notifiable_state`
- `test_send_notification_fires_on_completed`
- `test_send_notification_fires_on_failed`
- `test_send_notification_falls_back_to_volume_label`
- `test_send_notification_uses_configured_completed_template`
- `test_send_notification_uses_configured_failed_template`
- `test_send_notification_swallows_internal_errors`

For example:

```python
        await job_manager._send_discord_notification_for_state(job_id, JobState.COMPLETED)
```

Then update the two tests that patch the method by name.
In `test_terminal_callback_schedules_task`:

```python
    with patch.object(
        job_manager, "_send_discord_notification_for_state", new_callable=AsyncMock
    ) as mock_send:
        await job_manager._notify_discord_on_terminal(1, JobState.COMPLETED)
        await asyncio.sleep(0)  # yield to let the task start

    mock_send.assert_called_once_with(1, JobState.COMPLETED)
```

In `test_advance_job_via_state_machine_fires_notification`, change the same
`patch.object` target to `"_send_discord_notification_for_state"`; the
`mock_send.call_args[0][1] == JobState.COMPLETED` assertion at the end stays as
written.

- [ ] **Step 3: Run tests to verify they fail**

```bash
cd backend && uv run pytest tests/unit/test_discord_notifier.py -v
```

Expected: FAIL. The two new tests with `TypeError` on the unexpected
`extra_context` keyword, and the eight renamed ones with
`AttributeError: 'JobManager' object has no attribute '_send_discord_notification_for_state'`.

- [ ] **Step 4: Split the dispatch**

In `backend/app/services/job_manager.py`, replace the `_send_discord_notification`
signature and its event lookup. The new state-keyed wrapper goes directly above it:

```python
    async def _send_discord_notification_for_state(self, job_id: int, state: JobState) -> None:
        """Resolve a JobState to its NotificationEvent and send.

        Kept separate from _send_discord_notification because the ripped event
        has no JobState to resolve from: it is a hardware milestone, not a
        state. Both state-driven hooks funnel through here.
        """
        from app.core.discord_notifier import EVENTS

        event = EVENTS.get(state)
        if event is None:
            logger.warning(
                f"Job {job_id}: Discord notification requested for non-notifiable state {state}"
            )
            return
        await self._send_discord_notification(job_id, event)

    async def _send_discord_notification(
        self,
        job_id: int,
        event: "NotificationEvent",
        *,
        extra_context: dict[str, str] | None = None,
    ) -> None:
        """Send the Discord notification for one job event.

        Single path for completed, failed, review and ripped. Runs as a
        background task; all errors are swallowed so a slow or unreachable
        webhook can never affect the pipeline.

        ``extra_context`` supplies template vars the DiscJob row cannot provide.
        Today that is ``rip_outcome``, which is known only at the eject site.
        """
```

Inside the body, delete the `event = EVENTS.get(state)` block and its
`if event is None:` guard (they now live in the wrapper), remove `EVENTS` from
the function-local import list, and make these three edits:

Extend the toggles dict:

```python
            toggles = {
                "completed": config.discord_notify_completed,
                "failed": config.discord_notify_failed,
                "review": config.discord_notify_review,
            }
            if toggles.get(event.key) is False:
                return
            # Opt-in event: only an explicit True enables it, so a NULL or a
            # missing column reads as off rather than as on.
            if event.key == "ripped" and config.discord_notify_ripped is not True:
                return
```

Extend the templates dict:

```python
            templates = {
                "completed": config.discord_template_completed,
                "failed": config.discord_template_failed,
                "review": config.discord_template_review,
                "ripped": config.discord_template_ripped,
            }
```

Merge the extra context and pass the outcome to the embed:

```python
            context = build_template_context(job, job_id, titles)
            if extra_context:
                context.update(extra_context)
            description = render_discord_template(template, context)

            poster_url = await resolve_poster_url(job) if job else None
            link_url = build_dashboard_link(config.dashboard_base_url, job_id)
            content = config.discord_mention_review if event.key == "review" else ""

            embed = build_embed(
                job,
                titles,
                event,
                description,
                poster_url=poster_url,
                link_url=link_url,
                rip_outcome=(extra_context or {}).get("rip_outcome", ""),
            )
```

- [ ] **Step 5: Repoint the two state-driven hooks**

In `_notify_discord_on_terminal` (around line 1461):

```python
        asyncio.create_task(self._send_discord_notification_for_state(job_id, state))
```

In `_notify_discord_on_review` (around line 1418), the same:

```python
        asyncio.create_task(self._send_discord_notification_for_state(job_id, state))
```

Add the type-checking import for the annotation to the `if TYPE_CHECKING:` block
that already exists at `job_manager.py:19`:

```python
    from app.core.discord_notifier import NotificationEvent
```

- [ ] **Step 6: Run tests to verify they pass**

```bash
cd backend && uv run pytest tests/unit/test_discord_notifier.py -v
```

Expected: PASS, every test in the file.

- [ ] **Step 7: Lint**

```bash
cd backend && uv run ruff check app tests && uv run ruff format --check app tests
```

Expected: `All checks passed!`

- [ ] **Step 8: Commit**

```bash
git add backend/app/services/job_manager.py backend/tests/unit/test_discord_notifier.py
git commit -m "refactor(notifier): dispatch on NotificationEvent instead of JobState"
```

---

## Task 5: The `_release_drive` chokepoint

Three sites finish with the drive. All three call `eject_disc` then
`notify_ejected`; two wrap it in the same `auto_eject_enabled` check and the
same `except (OSError, RuntimeError)`. This collapses them into one method that
also sends the notification.

**One behavior difference must be preserved, not unified.** Sites 1 and 2 call
`notify_ejected` whenever `eject_disc` did not raise, ignoring its return value.
Site 3 calls it only when `eject_disc` returned `True`, with the comment "after
a failed eject the sentinel must stay armed". Site 3's version is more correct
in principle, but `eject_disc` returns `False` unconditionally on macOS and for
a Linux drive ID that fails `_OPTICAL_DRIVE_RE`, so applying it to sites 1 and 2
would stop re-arming the sentinel on those platforms. The difference is
therefore parameterized and documented, not resolved.

**Files:**
- Modify: `backend/app/services/job_manager.py:1264-1332` (site 3), `:2595-2601` (site 2), `:3163-3171` (site 1)
- Test: `backend/tests/unit/test_disc_eject.py`

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/unit/test_disc_eject.py`. These reuse the file's
existing `_install_eject_stubs` and `_seed_job_in_state` helpers.

```python
# --- _release_drive ---------------------------------------------------------


@pytest.mark.unit
async def test_release_drive_notifies_with_the_outcome(monkeypatch):
    job_id = await _seed_job_in_state(JobState.RIPPING)
    _install_eject_stubs(monkeypatch, eject_ok=True)
    sent: list[tuple] = []

    async def fake_send(job_id_, event, *, extra_context=None):
        sent.append((job_id_, event.key, extra_context))

    monkeypatch.setattr(job_manager, "_send_discord_notification", fake_send)

    await job_manager._release_drive(job_id, "Z:", "Complete")
    await asyncio.sleep(0)  # let the fire-and-forget task run

    assert sent == [(job_id, "ripped", {"rip_outcome": "Complete"})]


@pytest.mark.unit
async def test_release_drive_notifies_even_when_auto_eject_is_off(monkeypatch):
    """No tray opens, but the disc is still copied and the user still wants to
    know. The notify sits outside the eject branch for exactly this case."""
    from app.services.config_service import update_config

    await update_config(auto_eject_enabled=False)
    job_id = await _seed_job_in_state(JobState.RIPPING)
    stubs = _install_eject_stubs(monkeypatch, eject_ok=True)
    sent: list[tuple] = []

    async def fake_send(job_id_, event, *, extra_context=None):
        sent.append((job_id_, event.key, extra_context))

    monkeypatch.setattr(job_manager, "_send_discord_notification", fake_send)

    ejected = await job_manager._release_drive(job_id, "Z:", "Complete")
    await asyncio.sleep(0)

    assert ejected is False
    assert stubs.calls == [], "auto-eject off must not open the tray"
    assert sent == [(job_id, "ripped", {"rip_outcome": "Complete"})]

    await update_config(auto_eject_enabled=True)


@pytest.mark.unit
async def test_release_drive_rearms_sentinel_on_a_falsy_eject_by_default(monkeypatch):
    """eject_disc returns False unconditionally on macOS and for a Linux drive
    id that fails the optical-drive regex. The automatic sites must still
    re-arm the sentinel there, or the next disc is never detected."""
    job_id = await _seed_job_in_state(JobState.RIPPING)
    stubs = _install_eject_stubs(monkeypatch, eject_ok=False)
    monkeypatch.setattr(job_manager, "_send_discord_notification", AsyncMock())

    await job_manager._release_drive(job_id, "Z:", "Complete")

    assert stubs.notified == ["Z:"]


@pytest.mark.unit
async def test_release_drive_skips_rearm_on_failed_eject_when_asked(monkeypatch):
    """The user-initiated path reports `ejected` back to the UI and must leave
    the sentinel armed so it sees the disc when popped by hand."""
    job_id = await _seed_job_in_state(JobState.RIPPING)
    stubs = _install_eject_stubs(monkeypatch, eject_ok=False)
    monkeypatch.setattr(job_manager, "_send_discord_notification", AsyncMock())

    await job_manager._release_drive(
        job_id, "Z:", "Stopped early", respect_auto_eject=False, rearm_on_failed_eject=False
    )

    assert stubs.notified == []


# --- call-site coverage -----------------------------------------------------


@pytest.mark.unit
async def test_eject_while_ripping_sends_a_stopped_early_notification(monkeypatch):
    job_id = await _seed_job_in_state(JobState.RIPPING)
    _install_eject_stubs(monkeypatch, eject_ok=True)
    sent: list[tuple] = []

    async def fake_send(job_id_, event, *, extra_context=None):
        sent.append((job_id_, event.key, extra_context))

    monkeypatch.setattr(job_manager, "_send_discord_notification", fake_send)

    await job_manager.eject_disc_for_job(job_id)
    await asyncio.sleep(0)

    assert sent == [(job_id, "ripped", {"rip_outcome": "Stopped early"})]


@pytest.mark.unit
async def test_eject_while_identifying_sends_no_ripped_notification(monkeypatch):
    """Nothing was copied, so "Disc Ripped" would be a lie. The notify sits
    inside the RIPPING branch, below the IDENTIFYING fork."""
    job_id = await _seed_job_in_state(JobState.IDENTIFYING)
    _install_eject_stubs(monkeypatch, eject_ok=True)
    mock_send = AsyncMock()
    monkeypatch.setattr(job_manager, "_send_discord_notification", mock_send)

    await job_manager.eject_disc_for_job(job_id)
    await asyncio.sleep(0)

    mock_send.assert_not_called()
```

Add `from unittest.mock import AsyncMock` to the file's imports if it is not
already present, and `import asyncio` likewise.

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd backend && uv run pytest tests/unit/test_disc_eject.py -k "release_drive or notification" -v
```

Expected: FAIL with `AttributeError: 'JobManager' object has no attribute '_release_drive'`

- [ ] **Step 3: Write `_release_drive`**

Add to `JobManager` in `backend/app/services/job_manager.py`, directly above
`eject_disc_for_job`:

```python
    async def _release_drive(
        self,
        job_id: int,
        drive_id: str,
        outcome: str,
        *,
        respect_auto_eject: bool = True,
        rearm_on_failed_eject: bool = True,
    ) -> bool:
        """Finished with the drive: eject if configured, re-arm the sentinel, notify.

        The single chokepoint for every "Engram is done with this disc" moment,
        so the notification cannot drift out of sync with the eject.

        The Discord ping fires OUTSIDE the eject branch on purpose: with
        auto-eject off no tray opens, but the disc is still copied and the user
        still wants to know.

        ``rearm_on_failed_eject`` exists because eject_disc returns False
        unconditionally on macOS and for a Linux drive id that fails
        _OPTICAL_DRIVE_RE. The automatic call sites must re-arm the sentinel
        regardless, or the next disc is never detected on those platforms. The
        user-initiated path passes False: it reports `ejected` straight back to
        the UI, so a drive that genuinely refused must leave the sentinel armed
        to catch the disc when the user pops it by hand.

        Returns whether the tray actually opened.
        """
        # Function-local import: the eject tests patch app.core.sentinel.eject_disc
        # and rely on the name resolving at call time. Hoisting this to module
        # scope silently defeats every eject stub and opens the physical tray
        # under test.
        from app.core.discord_notifier import RIPPED_EVENT
        from app.core.sentinel import eject_disc
        from app.services.config_service import get_config

        safe_job = sanitize_log_value(job_id)
        safe_drive = sanitize_log_value(drive_id)

        should_eject = True
        if respect_auto_eject:
            config = await get_config()
            should_eject = bool(config and config.auto_eject_enabled)

        ejected = False
        if should_eject:
            raised = False
            try:
                ejected = await asyncio.to_thread(eject_disc, drive_id)
            except (OSError, RuntimeError) as e:
                raised = True
                logger.warning(f"Job {safe_job}: could not eject disc from {safe_drive}: {e}")
            if ejected or (rearm_on_failed_eject and not raised):
                self._drive_monitor.notify_ejected(drive_id)
            elif not ejected:
                logger.warning(
                    f"Job {safe_job}: drive {safe_drive} did not open; "
                    f"the disc can be removed by hand"
                )

        asyncio.create_task(
            self._send_discord_notification(
                job_id, RIPPED_EVENT, extra_context={"rip_outcome": outcome}
            )
        )
        return ejected
```

`get_config` is imported function-locally everywhere else in `job_manager.py`
(see lines 326, 1431), never at module scope. The local import above matches
that convention; do not hoist it.

**One spec-listed behavior has no dedicated test.** The third carve-out, "a hard
MakeMKV failure returns via `_fail_job` above the eject and so fires Failed but
not Ripped", holds structurally because the early return sits above the call
site. Covering it would mean mocking a full `_run_ripping` invocation, which no
existing test in this tree does. Verify it by reading the early return at
`_run_ripping`'s `if not result.success and not result.stalled_titles:` and
confirming the `_release_drive` call added in step 5 is below it. Do not add a
mock-heavy test for it.

- [ ] **Step 4: Rewrite site 3 (`eject_disc_for_job`)**

In `eject_disc_for_job`, delete the block that runs from
`from app.core.sentinel import eject_disc` through the `else:` branch logging
"did not open", and replace it with:

```python
        ejected = await self._release_drive(
            job_id,
            drive_id,
            "Stopped early",
            respect_auto_eject=False,
            rearm_on_failed_eject=False,
        )
```

Then move that call so it sits **inside** the `RIPPING` path only. The
`IDENTIFYING` branch must eject without notifying, since nothing was copied:

```python
        if state == JobState.IDENTIFYING:
            from app.core.sentinel import eject_disc

            ejected = False
            try:
                ejected = await asyncio.to_thread(eject_disc, drive_id)
            except (OSError, RuntimeError) as e:
                logger.warning(f"Job {safe_job}: eject of {safe_drive} raised: {e}")
            if ejected:
                self._drive_monitor.notify_ejected(drive_id)
            await self.cancel_job(job_id)
            logger.info(f"Job {safe_job}: ejected during identify, job cancelled")
            return {"ejected": ejected, "action": "job_cancelled"}

        # RIPPING: tracks were produced, so this is a real "done with the disc"
        # moment and gets the ripped notification.
        ejected = await self._release_drive(
            job_id,
            drive_id,
            "Stopped early",
            respect_auto_eject=False,
            rearm_on_failed_eject=False,
        )
        logger.info(
            f"Job {safe_job}: disc ejected mid-rip; finished titles continue, "
            f"unfinished titles route to review"
        )
        return {"ejected": ejected, "action": "rip_stopped"}
```

The `await asyncio.to_thread(self._extractor.eject_abort, job_id)` call above
this stays exactly where it is: MakeMKV must release its handle before the tray
is asked to open, and `test_eject_while_ripping_stops_rip_and_does_not_cancel`
asserts that ordering via `stubs.calls == ["abort", "eject"]`.

- [ ] **Step 5: Rewrite site 1 (end of rip)**

In `_run_ripping`, replace:

```python
            # Eject disc and reset sentinel state so a new disc insert is detected
            if rip_config and rip_config.auto_eject_enabled and not ejected_mid_rip:
                try:
                    from app.core.sentinel import eject_disc

                    await asyncio.to_thread(eject_disc, drive_id)
                    self._drive_monitor.notify_ejected(drive_id)
                except (OSError, RuntimeError) as e:
                    logger.warning(f"Could not eject disc from {drive_id}: {e}")
```

with:

```python
            # Eject disc and reset sentinel state so a new disc insert is detected.
            # `ejected_mid_rip` guards the notification as well as the eject: the
            # abort path already opened the tray AND already sent its own
            # "Stopped early" ping, so re-notifying here would double-fire.
            if not ejected_mid_rip:
                await self._release_drive(job_id, drive_id, "Complete")
```

The `rip_config and rip_config.auto_eject_enabled` check moves inside
`_release_drive`, which re-reads config itself.

- [ ] **Step 6: Rewrite site 2 (re-rip)**

In `rerip_titles`, replace:

```python
        # Free the drive for the next disc.
        if cfg and cfg.auto_eject_enabled:
            try:
                from app.core.sentinel import eject_disc

                await asyncio.to_thread(eject_disc, drive_id)
                self._drive_monitor.notify_ejected(drive_id)
            except (OSError, RuntimeError) as e:
                logger.warning(f"Job {sanitize_log_value(job_id)}: eject after re-rip failed: {e}")
```

with:

```python
        # Free the drive for the next disc.
        await self._release_drive(job_id, drive_id, "Re-rip")
```

`rerip_title_manual` delegates here, so the manual re-rip is covered by the
same line.

- [ ] **Step 7: Run the eject tests**

```bash
cd backend && uv run pytest tests/unit/test_disc_eject.py -v
```

Expected: PASS, including the six pre-existing `eject_disc_for_job` tests.

- [ ] **Step 8: Run the wider rip and job-manager suites for regressions**

```bash
cd backend && uv run pytest tests/unit/test_job_manager.py tests/unit/test_identity_answer_midrip.py tests/unit/test_rip_disc_title_map.py tests/unit/test_identify_rip_first_gates.py -q
```

Expected: PASS. These exercise `_run_ripping` and are the most likely place a
mistake in step 5 surfaces.

- [ ] **Step 9: Lint and commit**

```bash
cd backend && uv run ruff check app tests && uv run ruff format --check app tests
```

```bash
git add backend/app/services/job_manager.py backend/tests/unit/test_disc_eject.py
git commit -m "feat(notifications): fire a ripped event from the _release_drive chokepoint"
```

---

## Task 6: ConfigWizard

Six separate legs in one file. Missing any one leaves the field half-wired with
no error: the last two are the ones that silently disable validation rather than
visibly breaking.

**Files:**
- Modify: `frontend/src/components/ConfigWizard.tsx` (lines 180-187, 269-281, 396-403, 415-441, 613-620, 2044-2060, 2119-2135, 2257-2265)
- Test: `frontend/src/components/ConfigWizard.test.tsx`

- [ ] **Step 1: Write the failing test**

Append to `frontend/src/components/ConfigWizard.test.tsx`, following the
existing round-trip tests in that file for the mock/render helpers:

```tsx
it('round-trips the ripped notification toggle and template', async () => {
    const user = userEvent.setup();
    renderWizard();

    const toggle = await screen.findByLabelText(/notify when a disc finishes ripping/i);
    expect(toggle).not.toBeChecked();

    await user.click(toggle);
    const template = screen.getByLabelText(/finished ripping message/i);
    await user.clear(template);
    await user.type(template, '{{{{title}}}} done');

    await user.click(screen.getByRole('button', { name: /save/i }));

    const body = JSON.parse(fetchMock.mock.calls.at(-1)![1].body);
    expect(body.discord_notify_ripped).toBe(true);
    expect(body.discord_template_ripped).toBe('{{title}} done');
});
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd frontend && npm run test:unit -- ConfigWizard
```

Expected: FAIL with `Unable to find a label with the text of: /notify when a disc finishes ripping/i`

- [ ] **Step 3: Leg 1, the interface**

At line 186, after `discordNotifyReview: boolean;`:

```tsx
    discordNotifyRipped: boolean;
```

At line 183, after `discordTemplateReview: string;`:

```tsx
    discordTemplateRipped: string;
```

- [ ] **Step 4: Leg 2, initial state**

At line 275, after `discordNotifyReview: true,`:

```tsx
        discordNotifyRipped: false,
```

At line 272, after `discordTemplateReview: '',`:

```tsx
        discordTemplateRipped: '',
```

Widen the `discordTemplateErrors` state type at line 281:

```tsx
    const [discordTemplateErrors, setDiscordTemplateErrors] = useState<{completed: string; failed: string; review: string; ripped: string}>({completed: '', failed: '', review: '', ripped: ''});
```

- [ ] **Step 5: Leg 3, the load mapping**

At line 402, after `discordNotifyReview: data.discord_notify_review ?? true,`:

```tsx
                    discordNotifyRipped: data.discord_notify_ripped ?? false,
```

Note `?? false`, not `?? true`: this event is opt-in, so a server that has not
yet been migrated must render the box unchecked.

At line 399, after `discordTemplateReview: data.discord_template_review || '',`:

```tsx
                    discordTemplateRipped: data.discord_template_ripped || '',
```

- [ ] **Step 6: Leg 4, live validation**

Replace the `Promise.all` destructuring and the `setDiscordTemplateErrors` call
in the `useEffect` at line 418 with the four-way version, and add the new field
to the dependency array:

```tsx
            const [completedResult, failedResult, reviewResult, rippedResult] = await Promise.all([
                config.discordTemplateCompleted
                    ? requestDiscordTemplateValidation(config.discordTemplateCompleted)
                    : Promise.resolve({ status: 'valid' as const }),
                config.discordTemplateFailed
                    ? requestDiscordTemplateValidation(config.discordTemplateFailed)
                    : Promise.resolve({ status: 'valid' as const }),
                config.discordTemplateReview
                    ? requestDiscordTemplateValidation(config.discordTemplateReview)
                    : Promise.resolve({ status: 'valid' as const }),
                config.discordTemplateRipped
                    ? requestDiscordTemplateValidation(config.discordTemplateRipped)
                    : Promise.resolve({ status: 'valid' as const }),
            ]);
            setDiscordTemplateErrors({
                completed: completedResult.status === 'invalid' ? completedResult.error : '',
                failed: failedResult.status === 'invalid' ? failedResult.error : '',
                review: reviewResult.status === 'invalid' ? reviewResult.error : '',
                ripped: rippedResult.status === 'invalid' ? rippedResult.error : '',
            });
        }, 400);
        return () => window.clearTimeout(timer);
    }, [config.discordTemplateCompleted, config.discordTemplateFailed, config.discordTemplateReview, config.discordTemplateRipped]);
```

- [ ] **Step 7: Leg 5, the save payload**

At line 619, after `discord_notify_review: config.discordNotifyReview,`:

```tsx
                    discord_notify_ripped: config.discordNotifyRipped,
```

At line 616, after `discord_template_review: config.discordTemplateReview,`:

```tsx
                    discord_template_ripped: config.discordTemplateRipped,
```

- [ ] **Step 8: Leg 6, the JSX**

After the "Notify when a disc needs review" checkbox group (ending around line
2059), add:

```tsx
                        <div className="form-group checkbox-group">
                            <label className="checkbox-label">
                                <input
                                    type="checkbox"
                                    checked={config.discordNotifyRipped}
                                    onChange={(e) => handleInputChange('discordNotifyRipped', e.target.checked)}
                                />
                                <span className="checkbox-text">
                                    Notify when a disc finishes ripping
                                    <span className="checkbox-hint">
                                        Fires the moment the disc is copied and ejected, before any matching or
                                        review. Use this if you swap discs as they finish and review later.
                                    </span>
                                </span>
                            </label>
                        </div>
```

After the "Review Needed Message" textarea group (ending around line 2135), add:

```tsx
                        <div className="form-group">
                            <label htmlFor="discordTemplateRipped">Finished Ripping Message</label>
                            <textarea
                                id="discordTemplateRipped"
                                value={config.discordTemplateRipped}
                                onChange={(e) => handleInputChange('discordTemplateRipped', e.target.value)}
                                placeholder="**{{title}}**"
                                rows={2}
                            />
                            {discordTemplateErrors.ripped && (
                                <span style={{color: '#ef4444', fontSize: '0.85rem'}}>✗ {discordTemplateErrors.ripped}</span>
                            )}
                            <span className="form-hint">
                                Same variables as above, plus {'{{'}rip_outcome{'}}'}, which reads Complete,
                                Stopped early or Re-rip. Leave blank to use the default.
                            </span>
                        </div>
```

Extend both Save buttons' `disabled` expressions at lines 2257 and 2265:

```tsx
                            disabled={isSaving || !!discordTemplateErrors.completed || !!discordTemplateErrors.failed || !!discordTemplateErrors.review || !!discordTemplateErrors.ripped}
```

- [ ] **Step 9: Run tests and build**

```bash
cd frontend && npm run test:unit -- ConfigWizard
```

Expected: PASS

```bash
cd frontend && npm run build && npm run lint
```

Expected: build succeeds with no TypeScript errors, lint clean. A missed leg in
the interface or state shows up here as a TS error.

- [ ] **Step 10: Commit**

```bash
git add frontend/src/components/ConfigWizard.tsx frontend/src/components/ConfigWizard.test.tsx
git commit -m "feat(ui): add finished-ripping notification settings"
```

---

## Task 7: Documentation

**Files:**
- Modify: `CLAUDE.md` (the "Notification events" bullet under Key Patterns), `CHANGELOG.md`

- [ ] **Step 1: Update the CLAUDE.md pattern note**

Replace the existing "Notification events" bullet with:

```markdown
- **Notification events**: `EVENTS` in `app/core/discord_notifier.py` maps `JobState` to
  presentation. Terminal events arrive via `on_terminal_state`; `REVIEW_NEEDED` arrives via
  `on_transition`, which receives `(job_id, to_state, from_state)` so a same-state
  re-broadcast does not re-notify. `RIPPED_EVENT` sits deliberately OUTSIDE `EVENTS`: "the
  disc is copied and out of the drive" is a hardware milestone, not a `JobState`, and it must
  fire for a disc that then parks in review. It is delivered from `JobManager._release_drive`,
  the single chokepoint for the three places that finish with the drive (end of rip, mid-rip
  eject, end of re-rip), and carries a `rip_outcome` of Complete / Stopped early / Re-rip.
  Unlike the other three toggles it defaults OFF with `server_default 0`, because an opt-in
  event must read a NULL as disabled rather than enabled.
```

- [ ] **Step 2: Update the changelog**

Add under `## [Unreleased]`, creating the `### Added` subsection if absent:

```markdown
### Added

- Discord notification when a disc finishes ripping, fired the moment the disc is copied and
  ejected rather than when the job reaches its final state. Discs that need manual review now
  ping at the point the drive is free instead of staying silent until the review is done. Off
  by default; enable it under Notifications in Settings.
```

- [ ] **Step 3: Verify no unintended dashes**

House style forbids em and en dashes in prose:

```bash
LC_ALL=C grep -nE $'\xe2\x80\x94|\xe2\x80\x93' CLAUDE.md CHANGELOG.md
```

Expected: no output.

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md CHANGELOG.md
git commit -m "docs: describe the finished-ripping notification event"
```

---

## Task 8: Full verification

- [ ] **Step 1: Backend unit suite**

```bash
cd backend && uv run pytest tests/unit -q
```

Expected: PASS. This tier takes several minutes; do not add `-p no:logging` to
speed it up, it breaks roughly ten `caplog`-based tests in this repo.

- [ ] **Step 2: Backend integration suite**

```bash
cd backend && uv run pytest tests/integration -q
```

Expected: PASS.

- [ ] **Step 3: Frontend**

```bash
cd frontend && npm run test:unit && npm run build && npm run lint
```

Expected: all three clean.

- [ ] **Step 4: Manual smoke test with a simulated disc**

Start the backend with `DEBUG=true`, enable the toggle and set a webhook in
Settings, then:

```bash
curl -X POST localhost:8000/api/simulate/insert-disc -H "Content-Type: application/json" -d '{"volume_label":"THE_WIRE_S1D1","content_type":"tv","simulate_ripping":true}'
```

Expected: a "Disc Ripped" embed in the channel with a Status field reading
`Complete`, arriving before any Completed or Review Needed embed for the same job.

Note that simulation drives the job through `simulation_service`, which does not
call `_run_ripping`. If no ripped embed appears, verify against a real disc or
by calling `_release_drive` directly before concluding the wiring is broken.

- [ ] **Step 5: Stop this session's servers**

Per the repo's Important Rules, kill the `uvicorn` and `makemkvcon` processes
this session started before opening the PR, scoped to the ports you launched on.
