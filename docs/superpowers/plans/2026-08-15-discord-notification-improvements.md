# Discord Notification Improvements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Put disc identity into every Discord notification so box-set rips are trackable, and add a review-needed notification that actually reaches the user.

**Architecture:** `discord_notifier.py` moves from a hardcoded completed/failed binary to an event table plus a structured embed builder. Disc identity, season, episodes, duration, tracks and subtitles become real Discord embed fields (skip-if-empty), so they appear without anyone editing a template; the user template continues to render as the embed description. Review notifications hook `JobStateMachine.on_transition` (the chokepoint every review-parking path flows through) rather than `on_terminal_state`, which by definition cannot carry a non-terminal state.

**Tech Stack:** Python 3.11, FastAPI, SQLModel + aiosqlite, Alembic, chevron (mustache), httpx, pytest; React 18 + TypeScript + Vite, vitest.

**Spec:** `docs/superpowers/specs/2026-08-15-discord-notification-improvements-design.md`

---

## File Structure

**Created:**

| Path | Responsibility |
|---|---|
| `backend/app/core/tmdb_poster.py` | Single home for "given a DiscJob, what is its TMDB poster URL". Extracted from the API route so both the route and the notifier share one implementation. |
| `backend/migrations/versions/b7d3f9a1c204_add_discord_notification_settings.py` | Adds the six new `app_config` columns. Add-column only, per the `database.py` self-heal contract. |
| `frontend/src/utils/discordWebhookTest.ts` | Fetch wrapper for the test-webhook endpoint, mirroring `discordTemplateValidation.ts`. |

**Modified:**

| Path | Change |
|---|---|
| `backend/app/core/discord_notifier.py` | Event table, embed builder, field builder, episode manifest, dashboard link, new template variables. The bulk of the work. |
| `backend/app/core/security.py` | Add `is_safe_dashboard_url`, deliberately distinct from `is_safe_remote_url`. |
| `backend/app/models/app_config.py` | Six new fields. |
| `backend/app/api/routes.py` | `ConfigUpdate` + `ConfigResponse` fields, `dashboard_base_url` validation, review template validation, `get_job_poster` slimmed to a wrapper. |
| `backend/app/api/validation.py` | `POST /api/validate/discord-webhook`. |
| `backend/app/services/job_state_machine.py` | `on_transition` callbacks receive `from_state`. |
| `backend/app/services/job_manager.py` | Two observers gain a parameter; new `_notify_discord_on_review`; `_send_discord_notification` generalized. |
| `frontend/src/components/ConfigWizard.tsx` | Six new controls plus the test button. |
| `backend/tests/unit/test_discord_notifier.py` | Existing `notify_discord` tests updated for the new signature; new coverage. |

**Note on scale:** `discord_notifier.py` is currently 118 lines and will roughly triple. That is still a focused, single-responsibility module (build and send a Discord notification), so no split is warranted.

## Two hazards that apply to every task

**Leaked notification tasks in tests.** Terminal-state dispatch schedules
`_send_discord_notification` via `asyncio.create_task`. Any test that drives a job to
`COMPLETED` or `FAILED` and does not stub it leaks a task holding a StaticPool connection
past the end of the test, which surfaces later as an unrelated flaky failure. Either patch
it (`patch.object(job_manager, "_send_discord_notification", new_callable=AsyncMock)`) or
leave the job non-terminal. This plan's tests call `_send_discord_notification` directly
rather than through a transition, which sidesteps the problem; new tests you add may not.

**Worktree database.** A fresh worktree's `backend/engram.db` is often a zero-byte stub, and
tests that touch the DB fail with "no such table" until `init_db()` has run once. If Task 1
or 2 fails that way, start the backend once (`cd backend && uv run uvicorn app.main:app`),
let it initialize, then stop it.

---

## Task 1: Config fields and migration

**Files:**
- Modify: `backend/app/models/app_config.py:207-212`
- Create: `backend/migrations/versions/b7d3f9a1c204_add_discord_notification_settings.py`
- Test: `backend/tests/unit/test_discord_notifier.py`

**Context you need:** `AppConfig` is a SQLModel table. `init_db()` runs `create_all`, then `_add_missing_columns()` (which backfills columns from SQLModel metadata for frozen builds where Alembic is unavailable), then Alembic. Because both paths converge, an Alembic revision must contain *only* `add_column` calls; mixing in other DDL breaks the duplicate-column self-heal in `database.py`.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/unit/test_discord_notifier.py`:

```python
# --------------------------------------------------------------------------- #
# New notification config fields
# --------------------------------------------------------------------------- #


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/unit/test_discord_notifier.py::test_app_config_notification_defaults -v`
Expected: FAIL with `AttributeError: 'AppConfig' object has no attribute 'discord_template_review'`

- [ ] **Step 3: Add the fields**

In `backend/app/models/app_config.py`, replace the Notifications block (currently lines 207-212):

```python
    # Notifications
    discord_webhook_url: str = ""  # Discord webhook URL for job completion alerts
    # Discord embed description templates (chevron/mustache {{var}} syntax).
    # "" means use the built-in default (see app.core.discord_notifier).
    discord_template_completed: str = ""
    discord_template_failed: str = ""
    discord_template_review: str = ""
    # Per-event enable toggles. Stored NOT NULL with a server_default of 1, but
    # the notifier treats None as enabled anyway (see _send_discord_notification)
    # so a NULL left by an out-of-band schema change can never silently mute
    # notifications.
    discord_notify_completed: bool = Field(default=True, sa_column_kwargs={"server_default": text("1")})
    discord_notify_failed: bool = Field(default=True, sa_column_kwargs={"server_default": text("1")})
    discord_notify_review: bool = Field(default=True, sa_column_kwargs={"server_default": text("1")})
    # Message `content` sent alongside the embed on review events, e.g. "<@1234>"
    # or "@here". Embeds do not resolve mentions; only `content` does, which is
    # what makes a review ping actually reach a phone.
    discord_mention_review: str = ""
    # Base URL of this Engram dashboard, e.g. "http://192.168.1.50:5173". When
    # set, notifications link to /history/{job_id}. Deliberately NOT validated
    # with is_safe_remote_url: a LAN address is the expected value here.
    dashboard_base_url: str = ""
```

`Field` and `text` are already imported at the top of the file.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/unit/test_discord_notifier.py::test_app_config_notification_defaults -v`
Expected: PASS

- [ ] **Step 5: Create the Alembic revision**

Create `backend/migrations/versions/b7d3f9a1c204_add_discord_notification_settings.py`:

```python
"""add discord notification settings

Revision ID: b7d3f9a1c204
Revises: f1a2b3c4d5e6
Create Date: 2026-08-15 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b7d3f9a1c204"
down_revision: str | Sequence[str] | None = "f1a2b3c4d5e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "app_config",
        sa.Column(
            "discord_template_review", sa.String(), nullable=False, server_default=sa.text("''")
        ),
    )
    op.add_column(
        "app_config",
        sa.Column(
            "discord_notify_completed",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("1"),
        ),
    )
    op.add_column(
        "app_config",
        sa.Column(
            "discord_notify_failed", sa.Boolean(), nullable=False, server_default=sa.text("1")
        ),
    )
    op.add_column(
        "app_config",
        sa.Column(
            "discord_notify_review", sa.Boolean(), nullable=False, server_default=sa.text("1")
        ),
    )
    op.add_column(
        "app_config",
        sa.Column(
            "discord_mention_review", sa.String(), nullable=False, server_default=sa.text("''")
        ),
    )
    op.add_column(
        "app_config",
        sa.Column("dashboard_base_url", sa.String(), nullable=False, server_default=sa.text("''")),
    )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("app_config", schema=None) as batch_op:
        batch_op.drop_column("dashboard_base_url")
        batch_op.drop_column("discord_mention_review")
        batch_op.drop_column("discord_notify_review")
        batch_op.drop_column("discord_notify_failed")
        batch_op.drop_column("discord_notify_completed")
        batch_op.drop_column("discord_template_review")
```

- [ ] **Step 6: Verify the migration chain is linear**

Run: `cd backend && uv run alembic heads`
Expected: exactly one line, `b7d3f9a1c204 (head)`. More than one head means the `down_revision` is wrong.

- [ ] **Step 7: Run the migration test suite**

Run: `cd backend && uv run pytest tests/unit/test_database_migration.py -v`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add backend/app/models/app_config.py backend/migrations/versions/b7d3f9a1c204_add_discord_notification_settings.py backend/tests/unit/test_discord_notifier.py
git commit -m "feat(discord): add notification config fields (review template, toggles, mention, dashboard URL)"
```

---

## Task 2: Four-way config sync and URL validation

**Files:**
- Modify: `backend/app/core/security.py` (append after `is_safe_remote_url`)
- Modify: `backend/app/api/routes.py:343-346` (`ConfigResponse`), `:432-435` (`ConfigUpdate`), `:1617-1623` (response builder), `:1718-1725` (template validation loop)
- Test: `backend/tests/unit/test_validation.py`

**Context you need:** A field present in `AppConfig` but missing from `ConfigUpdate` is silently dropped by Pydantic with no error, and a field missing from `ConfigResponse` never reaches the frontend. This is the recurring failure mode in this codebase. All four places must change together.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/unit/test_validation.py`:

```python
# --------------------------------------------------------------------------- #
# is_safe_dashboard_url
# --------------------------------------------------------------------------- #


def test_dashboard_url_accepts_lan_address():
    """A private LAN address is the EXPECTED value here, unlike is_safe_remote_url."""
    from app.core.security import is_safe_dashboard_url

    assert is_safe_dashboard_url("http://192.168.1.50:5173") is True
    assert is_safe_dashboard_url("http://localhost:5173") is True
    assert is_safe_dashboard_url("https://engram.example.com") is True


def test_dashboard_url_rejects_non_http_scheme():
    """The value becomes a clickable embed url, so javascript: must not survive."""
    from app.core.security import is_safe_dashboard_url

    assert is_safe_dashboard_url("javascript:alert(1)") is False
    assert is_safe_dashboard_url("file:///etc/passwd") is False


def test_dashboard_url_rejects_credentials_and_empty_host():
    from app.core.security import is_safe_dashboard_url

    assert is_safe_dashboard_url("http://user:pass@192.168.1.50:5173") is False
    assert is_safe_dashboard_url("http://") is False
    assert is_safe_dashboard_url("") is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/unit/test_validation.py -k dashboard_url -v`
Expected: FAIL with `ImportError: cannot import name 'is_safe_dashboard_url'`

- [ ] **Step 3: Add the guard**

Append to `backend/app/core/security.py` (after `is_safe_remote_url`):

```python
def is_safe_dashboard_url(url: str) -> bool:
    """Return True if ``url`` is usable as this dashboard's own base URL.

    Deliberately NOT is_safe_remote_url. That guard rejects private, loopback
    and link-local hosts to prevent SSRF, but the dashboard base URL is a LAN
    address by design and the server never fetches it: the value is only
    embedded as a clickable link in a Discord notification. The threat model is
    therefore scheme injection (javascript:, data:, file:) and credential
    leakage, not request forgery.
    """
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
    except ValueError:
        return False

    if parsed.scheme not in ("http", "https"):
        return False
    if not host:
        return False
    # Credentials in a URL that gets posted to a chat channel leak them.
    if parsed.username or parsed.password:
        return False
    return True
```

`urlparse` is already imported at the top of `security.py`.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/unit/test_validation.py -k dashboard_url -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Add the fields to `ConfigResponse`**

In `backend/app/api/routes.py`, replace the Notifications block inside `ConfigResponse` (currently lines 343-346):

```python
    # Notifications
    discord_webhook_url: str = ""
    discord_template_completed: str = ""
    discord_template_failed: str = ""
    discord_template_review: str = ""
    discord_notify_completed: bool = True
    discord_notify_failed: bool = True
    discord_notify_review: bool = True
    discord_mention_review: str = ""
    dashboard_base_url: str = ""
```

- [ ] **Step 6: Add the fields to `ConfigUpdate`**

Replace the Notifications block inside `ConfigUpdate` (currently lines 432-435):

```python
    # Notifications
    discord_webhook_url: str | None = None
    discord_template_completed: str | None = None
    discord_template_failed: str | None = None
    discord_template_review: str | None = None
    discord_notify_completed: bool | None = None
    discord_notify_failed: bool | None = None
    discord_notify_review: bool | None = None
    discord_mention_review: str | None = None
    dashboard_base_url: str | None = None
```

- [ ] **Step 7: Populate them in the response builder**

Replace the Notifications block in the `ConfigResponse(...)` construction (currently lines 1617-1623):

```python
        # Notifications
        discord_webhook_url="***" if config.discord_webhook_url else "",
        # Coalesce None->"" defensively: a DB upgraded by an early 0.26.0 build may
        # already hold NULL here (see database._add_missing_columns), and
        # ConfigResponse requires str — a bare None would 500 GET /api/config.
        discord_template_completed=config.discord_template_completed or "",
        discord_template_failed=config.discord_template_failed or "",
        discord_template_review=config.discord_template_review or "",
        # `is not False` rather than `or True`: a NULL toggle reads as enabled,
        # matching the notifier, so an out-of-band schema change can't mute
        # notifications without the user ever asking for that.
        discord_notify_completed=config.discord_notify_completed is not False,
        discord_notify_failed=config.discord_notify_failed is not False,
        discord_notify_review=config.discord_notify_review is not False,
        discord_mention_review=config.discord_mention_review or "",
        dashboard_base_url=config.dashboard_base_url or "",
    )
```

- [ ] **Step 8: Validate the review template and the dashboard URL on save**

In `backend/app/api/routes.py`, replace the template validation loop (currently lines 1718-1725) with:

```python
    # Validate Discord notification templates before persisting
    from app.core.discord_notifier import validate_discord_template

    for field in (
        "discord_template_completed",
        "discord_template_failed",
        "discord_template_review",
    ):
        if update_data.get(field):
            error = validate_discord_template(update_data[field])
            if error:
                raise HTTPException(status_code=422, detail=f"{field}: {error}")

    # Validate the dashboard base URL. NOT is_safe_remote_url; see the docstring
    # on is_safe_dashboard_url; a LAN address is the expected value and the
    # server never fetches this URL.
    if update_data.get("dashboard_base_url"):
        from app.core.security import is_safe_dashboard_url

        if not is_safe_dashboard_url(update_data["dashboard_base_url"]):
            raise HTTPException(
                status_code=422,
                detail="dashboard_base_url must be an http/https URL with no embedded credentials",
            )
```

- [ ] **Step 9: Write the round-trip test**

Append to `backend/tests/unit/test_api_routes.py`:

```python
@pytest.mark.asyncio
async def test_config_round_trips_new_notification_fields(client):
    """Every new notification field survives PUT -> GET. Catches the silent
    Pydantic drop that happens when a field is added to AppConfig but not to
    ConfigUpdate."""
    payload = {
        "discord_template_review": "Review: {{title}}",
        "discord_notify_completed": False,
        "discord_notify_failed": True,
        "discord_notify_review": True,
        "discord_mention_review": "<@1234>",
        "dashboard_base_url": "http://192.168.1.50:5173",
    }
    put = await client.put("/api/config", json=payload)
    assert put.status_code == 200

    got = (await client.get("/api/config")).json()
    assert got["discord_template_review"] == "Review: {{title}}"
    assert got["discord_notify_completed"] is False
    assert got["discord_mention_review"] == "<@1234>"
    assert got["dashboard_base_url"] == "http://192.168.1.50:5173"

    # Restore defaults so later tests in this module see a clean config.
    await client.put(
        "/api/config",
        json={
            "discord_template_review": "",
            "discord_notify_completed": True,
            "discord_mention_review": "",
            "dashboard_base_url": "",
        },
    )


@pytest.mark.asyncio
async def test_config_rejects_javascript_dashboard_url(client):
    resp = await client.put("/api/config", json={"dashboard_base_url": "javascript:alert(1)"})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_config_rejects_bad_review_template(client):
    resp = await client.put("/api/config", json={"discord_template_review": "{{bogus}}"})
    assert resp.status_code == 422
```

- [ ] **Step 10: Run the tests**

Run: `cd backend && uv run pytest tests/unit/test_api_routes.py -k "notification_fields or dashboard_url or review_template" tests/unit/test_validation.py -k dashboard_url -v`
Expected: PASS

- [ ] **Step 11: Commit**

```bash
git add backend/app/core/security.py backend/app/api/routes.py backend/tests/unit/test_validation.py backend/tests/unit/test_api_routes.py
git commit -m "feat(discord): sync new notification config through ConfigUpdate/ConfigResponse, add dashboard URL guard"
```

---

## Task 3: Event table and embed-based `notify_discord`

**Files:**
- Modify: `backend/app/core/discord_notifier.py`
- Modify: `backend/app/services/job_manager.py:1232-1266` (`_send_discord_notification`)
- Test: `backend/tests/unit/test_discord_notifier.py:23-101` (rewrite), plus new tests

**Context you need:** `notify_discord` currently branches on `state == "completed"` for colour and emoji, and builds the embed title as `f"Disc {state.title()}"`. A review event would render "Disc Review_Needed" in red. This task replaces the binary with a lookup table and moves embed construction out of `notify_discord`, which becomes a pure transport function. The signature changes, so the four existing `notify_discord` tests and the one caller change in the same commit.

- [ ] **Step 1: Write the failing tests**

Replace the entire `notify_discord unit tests` block in `backend/tests/unit/test_discord_notifier.py` (lines 18-101, from the `# notify_discord unit tests` banner through `test_notify_discord_swallows_http_errors`) with:

```python
# --------------------------------------------------------------------------- #
# Event table
# --------------------------------------------------------------------------- #


def test_event_table_covers_the_three_notifiable_states():
    from app.core.discord_notifier import EVENTS
    from app.models.disc_job import JobState

    assert set(EVENTS) == {JobState.COMPLETED, JobState.FAILED, JobState.REVIEW_NEEDED}
    assert EVENTS[JobState.COMPLETED].key == "completed"
    assert EVENTS[JobState.COMPLETED].color == 0x00B97A
    assert EVENTS[JobState.FAILED].key == "failed"
    assert EVENTS[JobState.FAILED].color == 0xE53935
    assert EVENTS[JobState.REVIEW_NEEDED].key == "review"
    assert EVENTS[JobState.REVIEW_NEEDED].label == "Review Needed"


def test_event_table_has_no_entry_for_transient_states():
    """RIPPING and friends must not resolve to an event; that guard is what stops
    a mid-pipeline transition from posting to Discord."""
    from app.core.discord_notifier import EVENTS
    from app.models.disc_job import JobState

    assert EVENTS.get(JobState.RIPPING) is None
    assert EVENTS.get(JobState.MATCHING) is None


# --------------------------------------------------------------------------- #
# notify_discord unit tests
# --------------------------------------------------------------------------- #


def _mock_http_client():
    """AsyncClient double whose .post records the call and reports success."""
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    return mock_client


@pytest.mark.asyncio
async def test_notify_discord_noop_on_empty_url():
    """Empty webhook URL → no HTTP call made."""
    with patch("httpx.AsyncClient") as mock_client_cls:
        await notify_discord("", job_id=1, embed={"title": "x"})
        mock_client_cls.assert_not_called()


@pytest.mark.asyncio
async def test_notify_discord_posts_the_embed_verbatim():
    """notify_discord is pure transport: it wraps the embed and posts, nothing else."""
    mock_client = _mock_http_client()
    embed = {"title": "✅ Disc Completed", "description": "**The Wire**", "color": 0x00B97A}

    with patch("httpx.AsyncClient", return_value=mock_client):
        await notify_discord(
            "https://discord.com/api/webhooks/123/abc", job_id=5, embed=embed
        )

    url, kwargs = mock_client.post.call_args[0][0], mock_client.post.call_args[1]
    assert url == "https://discord.com/api/webhooks/123/abc"
    assert kwargs["json"]["embeds"] == [embed]


@pytest.mark.asyncio
async def test_notify_discord_omits_content_key_when_blank():
    """A blank content must not be sent as an empty message body."""
    mock_client = _mock_http_client()

    with patch("httpx.AsyncClient", return_value=mock_client):
        await notify_discord("https://discord.com/api/webhooks/1/a", job_id=1, embed={}, content="")

    assert "content" not in mock_client.post.call_args[1]["json"]


@pytest.mark.asyncio
async def test_notify_discord_includes_content_when_set():
    """The mention rides as message content, which is the only part Discord pings on."""
    mock_client = _mock_http_client()

    with patch("httpx.AsyncClient", return_value=mock_client):
        await notify_discord(
            "https://discord.com/api/webhooks/1/a", job_id=1, embed={}, content="<@1234>"
        )

    assert mock_client.post.call_args[1]["json"]["content"] == "<@1234>"


@pytest.mark.asyncio
async def test_notify_discord_swallows_http_errors():
    """HTTP errors are caught and logged, never raised."""
    import httpx

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(side_effect=httpx.HTTPError("timeout"))
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("httpx.AsyncClient", return_value=mock_client):
        await notify_discord("https://discord.com/api/webhooks/123/abc", job_id=3, embed={})


# --------------------------------------------------------------------------- #
# build_embed
# --------------------------------------------------------------------------- #


def test_build_embed_sets_title_description_and_color_from_event():
    from app.core.discord_notifier import EVENTS, build_embed
    from app.models.disc_job import JobState

    job = DiscJob(drive_id="E:", content_type=ContentType.TV, detected_title="The Wire")
    embed = build_embed(job, [], EVENTS[JobState.COMPLETED], "**The Wire**")

    assert embed["title"] == "✅ Disc Completed"
    assert embed["description"] == "**The Wire**"
    assert embed["color"] == 0x00B97A
    assert "timestamp" in embed
    assert embed["footer"]["text"].startswith("Engram")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/unit/test_discord_notifier.py -k "event_table or notify_discord or build_embed" -v`
Expected: FAIL with `ImportError: cannot import name 'EVENTS'`

- [ ] **Step 3: Implement the event table and the embed builder**

In `backend/app/core/discord_notifier.py`, replace the module imports and add the new code. The new header:

```python
"""Discord webhook notifications for job lifecycle events."""

from dataclasses import dataclass
from datetime import UTC, datetime

import chevron
import httpx
from chevron.tokenizer import ChevronError, tokenize
from loguru import logger

from app import __version__
from app.models.disc_job import DiscJob, JobState


@dataclass(frozen=True)
class NotificationEvent:
    """Presentation for one notifiable job state.

    Replaces the old `state == "completed"` binary. `key` is the config suffix
    (discord_template_<key>, discord_notify_<key>), deliberately "review" rather
    than "review_needed" so config field names stay readable.
    """

    key: str
    label: str
    emoji: str
    color: int


EVENTS: dict[JobState, NotificationEvent] = {
    JobState.COMPLETED: NotificationEvent("completed", "Disc Completed", "✅", 0x00B97A),
    JobState.FAILED: NotificationEvent("failed", "Disc Failed", "❌", 0xE53935),
    JobState.REVIEW_NEEDED: NotificationEvent("review", "Review Needed", "🔍", 0xF59E0B),
}
```

Replace `notify_discord` entirely:

```python
def build_embed(
    job: DiscJob | None,
    titles: list,
    event: NotificationEvent,
    description: str,
    *,
    poster_url: str | None = None,
    link_url: str | None = None,
) -> dict:
    """Assemble the Discord embed for one notification.

    The user template renders into `description`; everything else is structured
    so disc identity shows up without anyone editing a template.
    """
    embed: dict = {
        "title": f"{event.emoji} {event.label}",
        "description": description,
        "color": event.color,
        "timestamp": datetime.now(UTC).isoformat(),
        "footer": {"text": f"Engram v{__version__}"},
    }
    if link_url:
        embed["url"] = link_url
    if poster_url:
        embed["thumbnail"] = {"url": poster_url}
    return embed


async def notify_discord(webhook_url: str, job_id: int, embed: dict, content: str = "") -> None:
    """POST a prebuilt embed to webhook_url. No-op if URL is empty.

    Pure transport: it knows nothing about job semantics. `content` is the only
    part Discord resolves mentions in, so it carries the configured mention and
    never rendered template output.
    """
    if not webhook_url:
        return

    payload: dict = {"embeds": [embed]}
    if content:
        payload["content"] = content

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(webhook_url, json=payload)
            resp.raise_for_status()
        logger.debug(f"Discord notification sent for job {job_id}")
    except Exception:
        logger.warning(f"Discord notification failed for job {job_id}", exc_info=True)
```

Also add the review default next to the existing two:

```python
DEFAULT_TEMPLATE_COMPLETED = "**{{{title}}}**"
DEFAULT_TEMPLATE_FAILED = "**{{{title}}}**"
DEFAULT_TEMPLATE_REVIEW = "**{{{title}}}**"

DEFAULT_TEMPLATES = {
    "completed": DEFAULT_TEMPLATE_COMPLETED,
    "failed": DEFAULT_TEMPLATE_FAILED,
    "review": DEFAULT_TEMPLATE_REVIEW,
}
```

- [ ] **Step 4: Update the single caller**

In `backend/app/services/job_manager.py`, replace the body of `_send_discord_notification` (lines 1232-1266). This is an interim shape; Task 10 finishes it.

```python
    async def _send_discord_notification(self, job_id: int, state: JobState) -> None:
        """Send the Discord embed. Runs as a background task; all errors are swallowed."""
        try:
            from app.core.discord_notifier import (
                DEFAULT_TEMPLATES,
                EVENTS,
                build_embed,
                build_template_context,
                notify_discord,
                render_discord_template,
            )
            from app.services.config_service import get_config

            config = await get_config()
            if not config.discord_webhook_url:
                return

            event = EVENTS.get(state)
            if event is None:
                logger.warning(
                    f"Job {job_id}: Discord notification requested for non-notifiable state {state}"
                )
                return

            async with async_session() as session:
                job = await session.get(DiscJob, job_id)

            templates = {
                "completed": config.discord_template_completed,
                "failed": config.discord_template_failed,
                "review": config.discord_template_review,
            }
            template = templates[event.key] or DEFAULT_TEMPLATES[event.key]

            context = build_template_context(job, job_id)
            description = render_discord_template(template, context)
            embed = build_embed(job, [], event, description)
            await notify_discord(config.discord_webhook_url, job_id, embed)
        except Exception as e:
            logger.warning(f"Job {job_id}: Discord notification failed: {e}", exc_info=True)
```

- [ ] **Step 5: Update the existing `_send_discord_notification` tests for the new signature**

In `backend/tests/unit/test_discord_notifier.py`, the tests that assert on positional args of the mocked `notify_discord` now read the embed. Apply these replacements:

In `test_send_notification_fires_on_completed`, replace the assertion block after `mock_notify.assert_called_once()`:

```python
    embed = mock_notify.call_args[0][2]
    assert embed["description"] == "**Breaking Bad**"
    assert "Completed" in embed["title"]
```

In `test_send_notification_fires_on_failed`, replace the two lines after `mock_notify.assert_called_once()`:

```python
    embed = mock_notify.call_args[0][2]
    assert "Failed" in embed["title"]
    assert embed["color"] == 0xE53935
```

In `test_send_notification_falls_back_to_volume_label`:

```python
    assert mock_notify.call_args[0][2]["description"] == "**UNKNOWN_DISC**"
```

In `test_send_notification_uses_configured_completed_template`:

```python
    assert mock_notify.call_args[0][2]["description"] == "Done: Breaking Bad (BREAKING_BAD_S1D1)"
```

In `test_send_notification_uses_configured_failed_template`, the template contains an em dash in the existing test. Replace both the template and the assertion with a colon so the file carries no em dashes:

```python
        discord_template_failed="Failed: {{title}}: {{error}}",
```
```python
    assert mock_notify.call_args[0][2]["description"] == "Failed: BAD_DISC: disc unreadable"
```

Rename `test_send_notification_noop_for_non_terminal_state` to `test_send_notification_noop_for_non_notifiable_state` and update its docstring to "Called with a state that has no event mapping (RIPPING) → notify_discord never called."

- [ ] **Step 6: Run the full notifier suite**

Run: `cd backend && uv run pytest tests/unit/test_discord_notifier.py -v`
Expected: PASS (all tests)

- [ ] **Step 7: Commit**

```bash
git add backend/app/core/discord_notifier.py backend/app/services/job_manager.py backend/tests/unit/test_discord_notifier.py
git commit -m "refactor(discord): replace completed/failed binary with an event table and embed builder"
```

---

## Task 4: Disc identity and embed fields

**Files:**
- Modify: `backend/app/core/discord_notifier.py`
- Test: `backend/tests/unit/test_discord_notifier.py`

**Context you need:** This is user request 1. For a box set, `detected_title` is the show name on every disc, so the description alone cannot distinguish disc 3 from disc 4. `DiscJob` carries `volume_label` (raw, e.g. `ARRESTED_DEVELOPMENT_S1D1`), `disc_number`, and `discdb_disc_slug` (e.g. `S01D01`); none are currently surfaced. Fields are skip-if-empty so one builder serves TV, movies and all three events without a matrix of conditionals.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/unit/test_discord_notifier.py`:

```python
# --------------------------------------------------------------------------- #
# format_disc_identity / build_embed_fields
# --------------------------------------------------------------------------- #


def test_disc_identity_prefers_volume_label():
    from app.core.discord_notifier import format_disc_identity

    job = DiscJob(drive_id="E:", volume_label="ARRESTED_DEVELOPMENT_S1D1", disc_number=1)
    assert format_disc_identity(job) == "ARRESTED_DEVELOPMENT_S1D1"


def test_disc_identity_appends_disc_number_beyond_the_first():
    """The whole point of request 1: disc 3 of a box set must not read like disc 1."""
    from app.core.discord_notifier import format_disc_identity

    job = DiscJob(drive_id="E:", volume_label="THE_WIRE_S1", disc_number=3)
    assert format_disc_identity(job) == "THE_WIRE_S1 (Disc 3)"


def test_disc_identity_falls_back_to_discdb_slug():
    from app.core.discord_notifier import format_disc_identity

    job = DiscJob(drive_id="E:", volume_label="", discdb_disc_slug="S01D02", disc_number=2)
    assert format_disc_identity(job) == "S01D02"


def test_disc_identity_falls_back_to_disc_number():
    from app.core.discord_notifier import format_disc_identity

    job = DiscJob(drive_id="E:", volume_label="", discdb_disc_slug=None, disc_number=4)
    assert format_disc_identity(job) == "Disc 4"


def test_embed_fields_include_disc_and_season_for_tv():
    from app.core.discord_notifier import EVENTS, build_embed_fields
    from app.models.disc_job import JobState

    job = DiscJob(
        drive_id="E:",
        content_type=ContentType.TV,
        detected_title="The Wire",
        detected_season=1,
        volume_label="THE_WIRE_S1D3",
        disc_number=3,
        total_titles=6,
    )
    fields = build_embed_fields(job, [], EVENTS[JobState.COMPLETED])
    by_name = {f["name"]: f["value"] for f in fields}

    assert by_name["Disc"] == "THE_WIRE_S1D3 (Disc 3)"
    assert by_name["Season"] == "Season 1"
    assert by_name["Tracks"] == "6 titles"


def test_embed_fields_omit_season_for_movies():
    """Skip-if-empty is what lets one builder serve movies and TV without a
    conditional matrix; a blank 'Season: ' row would be noise."""
    from app.core.discord_notifier import EVENTS, build_embed_fields
    from app.models.disc_job import JobState

    job = DiscJob(
        drive_id="E:",
        content_type=ContentType.MOVIE,
        detected_title="Inception",
        volume_label="INCEPTION_2010",
    )
    fields = build_embed_fields(job, [], EVENTS[JobState.COMPLETED])
    assert "Season" not in {f["name"] for f in fields}


def test_embed_fields_show_review_reason_on_review_event():
    from app.core.discord_notifier import EVENTS, build_embed_fields
    from app.models.disc_job import JobState

    job = DiscJob(
        drive_id="E:",
        content_type=ContentType.TV,
        volume_label="MYSTERY_DISC",
        review_reason="Could not match 3 titles",
    )
    fields = build_embed_fields(job, [], EVENTS[JobState.REVIEW_NEEDED])
    by_name = {f["name"]: f["value"] for f in fields}
    assert by_name["Reason"] == "Could not match 3 titles"


def test_embed_fields_show_error_on_failed_event():
    from app.core.discord_notifier import EVENTS, build_embed_fields
    from app.models.disc_job import JobState

    job = DiscJob(drive_id="E:", volume_label="BAD_DISC", error_message="disc unreadable")
    fields = build_embed_fields(job, [], EVENTS[JobState.FAILED])
    by_name = {f["name"]: f["value"] for f in fields}
    assert by_name["Reason"] == "disc unreadable"


def test_embed_fields_report_subtitles_with_failures():
    from app.core.discord_notifier import EVENTS, build_embed_fields
    from app.models.disc_job import JobState

    job = DiscJob(
        drive_id="E:",
        volume_label="X",
        subtitle_status="partial",
        subtitles_downloaded=18,
        subtitles_total=20,
        subtitles_failed=2,
    )
    by_name = {
        f["name"]: f["value"] for f in build_embed_fields(job, [], EVENTS[JobState.COMPLETED])
    }
    assert by_name["Subtitles"] == "18/20, 2 failed"


def test_embed_fields_empty_when_job_is_none():
    """Job vanished before the background task re-fetched it: no crash, no fields."""
    from app.core.discord_notifier import EVENTS, build_embed_fields
    from app.models.disc_job import JobState

    assert build_embed_fields(None, [], EVENTS[JobState.COMPLETED]) == []


def test_build_embed_attaches_fields():
    from app.core.discord_notifier import EVENTS, build_embed
    from app.models.disc_job import JobState

    job = DiscJob(drive_id="E:", content_type=ContentType.TV, volume_label="THE_WIRE_S1D3")
    embed = build_embed(job, [], EVENTS[JobState.COMPLETED], "**The Wire**")
    assert any(f["name"] == "Disc" for f in embed["fields"])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/unit/test_discord_notifier.py -k "disc_identity or embed_fields" -v`
Expected: FAIL with `ImportError: cannot import name 'format_disc_identity'`

- [ ] **Step 3: Implement the field builder**

Add to `backend/app/core/discord_notifier.py`, above `build_embed`:

```python
def format_disc_identity(job: DiscJob) -> str:
    """Human-readable identity for THIS disc, not the show it belongs to.

    A box set's detected_title is identical on every disc, which is exactly the
    problem this solves. Falls back volume_label -> discdb_disc_slug -> disc N.
    """
    disc_number = job.disc_number or 1
    if job.volume_label:
        if disc_number > 1:
            return f"{job.volume_label} (Disc {disc_number})"
        return job.volume_label
    if job.discdb_disc_slug:
        return job.discdb_disc_slug
    return f"Disc {disc_number}"


def _field(name: str, value: str, inline: bool = True) -> dict | None:
    """Build one embed field, or None when the value is empty.

    Emptiness is the universal drop signal: it lets a single field list serve
    movies, TV, imports and all three events without conditional branching.
    """
    return {"name": name, "value": value, "inline": inline} if value else None


def build_embed_fields(job: DiscJob | None, titles: list, event: NotificationEvent) -> list[dict]:
    """Structured embed fields for one notification. Empty values are dropped."""
    if job is None:
        return []

    is_tv = job.content_type == ContentType.TV
    reason = job.review_reason if event.key == "review" else job.error_message

    subtitles = ""
    if job.subtitle_status:
        subtitles = f"{job.subtitles_downloaded}/{job.subtitles_total}"
        if job.subtitles_failed:
            subtitles += f", {job.subtitles_failed} failed"

    candidates = [
        _field("Disc", format_disc_identity(job)),
        _field("Season", f"Season {job.detected_season}" if is_tv and job.detected_season else ""),
        _field(
            "Episodes",
            summarize_episodes(titles) if event.key == "completed" else "",
            inline=False,
        ),
        _field("Duration", _format_duration(job)),
        _field("Tracks", f"{job.total_titles} titles" if job.total_titles else ""),
        _field("Subtitles", subtitles),
        _field("Reason", reason or "", inline=False),
        _field("Library", job.final_path or "" if event.key == "completed" else "", inline=False),
    ]
    return [f for f in candidates if f is not None]
```

Add `ContentType` to the model import at the top of the file:

```python
from app.models.disc_job import ContentType, DiscJob, JobState
```

`summarize_episodes` does not exist yet. Add a temporary stub directly above `build_embed_fields` so this task's tests pass on their own; Task 5 replaces it:

```python
def summarize_episodes(titles: list) -> str:
    """Replaced in Task 5. Returns "" so the Episodes field is dropped."""
    return ""
```

Then extend `build_embed` to attach the fields. Replace its embed construction:

```python
    embed: dict = {
        "title": f"{event.emoji} {event.label}",
        "description": description,
        "color": event.color,
        "timestamp": datetime.now(UTC).isoformat(),
        "footer": {"text": f"Engram v{__version__}"},
        "fields": build_embed_fields(job, titles, event),
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/unit/test_discord_notifier.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/core/discord_notifier.py backend/tests/unit/test_discord_notifier.py
git commit -m "feat(discord): surface disc identity and structured fields in the embed"
```

---

## Task 5: Episode manifest

**Files:**
- Modify: `backend/app/core/discord_notifier.py` (replace the Task 4 stub)
- Test: `backend/tests/unit/test_discord_notifier.py`

**Context you need:** `DiscTitle.matched_episode` holds a canonical aired-order code like `"S01E03"`. Only `TitleState.COMPLETED`, non-extra rows count. Collapsing contiguous runs is what makes a 6-episode disc read as one line instead of six.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/unit/test_discord_notifier.py`:

```python
# --------------------------------------------------------------------------- #
# summarize_episodes
# --------------------------------------------------------------------------- #


def _title(episode: str | None, state=None, is_extra: bool = False):
    from app.models.disc_job import DiscTitle, TitleState

    return DiscTitle(
        job_id=1,
        title_index=0,
        duration_seconds=1200,
        matched_episode=episode,
        state=state or TitleState.COMPLETED,
        is_extra=is_extra,
    )


def test_summarize_episodes_collapses_a_contiguous_run():
    from app.core.discord_notifier import summarize_episodes

    titles = [_title(f"S01E0{n}") for n in (1, 2, 3, 4)]
    assert summarize_episodes(titles) == "S01E01-E04 (4 episodes)"


def test_summarize_episodes_preserves_gaps():
    from app.core.discord_notifier import summarize_episodes

    titles = [_title("S01E01"), _title("S01E02"), _title("S01E03"), _title("S01E06")]
    assert summarize_episodes(titles) == "S01E01-E03, S01E06 (4 episodes)"


def test_summarize_episodes_single_episode():
    from app.core.discord_notifier import summarize_episodes

    assert summarize_episodes([_title("S01E01")]) == "S01E01 (1 episode)"


def test_summarize_episodes_spans_seasons_separately():
    """A disc straddling a season boundary must not collapse S01E12 and S02E01."""
    from app.core.discord_notifier import summarize_episodes

    titles = [_title("S01E11"), _title("S01E12"), _title("S02E01")]
    assert summarize_episodes(titles) == "S01E11-E12, S02E01 (3 episodes)"


def test_summarize_episodes_ignores_extras_and_incomplete_titles():
    from app.core.discord_notifier import summarize_episodes
    from app.models.disc_job import TitleState

    titles = [
        _title("S01E01"),
        _title("S01E02", is_extra=True),
        _title("S01E03", state=TitleState.FAILED),
    ]
    assert summarize_episodes(titles) == "S01E01 (1 episode)"


def test_summarize_episodes_empty_when_nothing_matched():
    from app.core.discord_notifier import summarize_episodes

    assert summarize_episodes([]) == ""
    assert summarize_episodes([_title(None)]) == ""


def test_summarize_episodes_counts_unparseable_rows_without_ranging_them():
    """A completed title with a non-standard code still happened; report the count
    honestly rather than pretending the disc had one fewer episode."""
    from app.core.discord_notifier import summarize_episodes

    titles = [_title("S01E01"), _title("special-1")]
    assert summarize_episodes(titles) == "S01E01 (2 episodes)"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/unit/test_discord_notifier.py -k summarize_episodes -v`
Expected: FAIL, `assert '' == 'S01E01-E04 (4 episodes)'` (the Task 4 stub returns "")

- [ ] **Step 3: Implement it**

In `backend/app/core/discord_notifier.py`, replace the Task 4 stub with:

```python
_EPISODE_CODE = re.compile(r"^S(\d+)E(\d+)$", re.IGNORECASE)


def summarize_episodes(titles: list) -> str:
    """One-line manifest of what actually landed, e.g. "S01E01-E04 (4 episodes)".

    Counts every completed, non-extra title, but only ranges the ones whose
    matched_episode parses as SxxEyy. Ranges never span a season boundary.
    """
    from app.models.disc_job import TitleState

    landed = [t for t in titles if t.state == TitleState.COMPLETED and not t.is_extra]
    if not landed:
        return ""

    parsed: list[tuple[int, int]] = []
    for title in landed:
        match = _EPISODE_CODE.match((title.matched_episode or "").strip())
        if match:
            parsed.append((int(match.group(1)), int(match.group(2))))

    count = len(landed)
    noun = "episode" if count == 1 else "episodes"
    if not parsed:
        return ""

    parsed = sorted(set(parsed))
    runs: list[list[tuple[int, int]]] = [[parsed[0]]]
    for season, episode in parsed[1:]:
        prev_season, prev_episode = runs[-1][-1]
        if season == prev_season and episode == prev_episode + 1:
            runs[-1].append((season, episode))
        else:
            runs.append([(season, episode)])

    parts = []
    for run in runs:
        start_season, start_episode = run[0]
        start = f"S{start_season:02d}E{start_episode:02d}"
        if len(run) == 1:
            parts.append(start)
        else:
            parts.append(f"{start}-E{run[-1][1]:02d}")

    return f"{', '.join(parts)} ({count} {noun})"
```

Add `import re` to the imports at the top of the file.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/unit/test_discord_notifier.py -k summarize_episodes -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Run the whole notifier suite for regressions**

Run: `cd backend && uv run pytest tests/unit/test_discord_notifier.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/app/core/discord_notifier.py backend/tests/unit/test_discord_notifier.py
git commit -m "feat(discord): add episode manifest to completion notifications"
```

---

## Task 6: Extract the poster resolver

**Files:**
- Create: `backend/app/core/tmdb_poster.py`
- Modify: `backend/app/api/routes.py:1830-1878` (`get_job_poster`)
- Test: `backend/tests/unit/test_discord_notifier.py`

**Context you need:** The TMDB poster lookup is inlined in the API route today. The notifier needs the same logic, so it moves to a shared module and the route becomes a wrapper. Behavior must not change: prefer `job.tmdb_id` (exact), fall back to a name search, return `None` on any failure.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/unit/test_discord_notifier.py`:

```python
# --------------------------------------------------------------------------- #
# resolve_poster_url
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_resolve_poster_url_returns_none_without_api_key():
    from app.core.tmdb_poster import resolve_poster_url
    from app.services.config_service import update_config

    await update_config(tmdb_api_key="")
    job = DiscJob(drive_id="E:", content_type=ContentType.TV, tmdb_id=1396)
    assert await resolve_poster_url(job) is None


@pytest.mark.asyncio
async def test_resolve_poster_url_uses_tmdb_id_when_present():
    from app.core.tmdb_poster import resolve_poster_url
    from app.services.config_service import update_config

    await update_config(tmdb_api_key="eyJtest")
    job = DiscJob(drive_id="E:", content_type=ContentType.TV, tmdb_id=1396)

    response = MagicMock()
    response.status_code = 200
    response.json = MagicMock(return_value={"poster_path": "/abc.jpg"})

    with patch("requests.get", return_value=response) as mock_get:
        url = await resolve_poster_url(job)

    assert url is not None and url.endswith("/abc.jpg")
    assert "/tv/1396" in mock_get.call_args[0][0]

    await update_config(tmdb_api_key="")


@pytest.mark.asyncio
async def test_resolve_poster_url_swallows_network_errors():
    """A TMDB hiccup costs the thumbnail, never the notification."""
    from app.core.tmdb_poster import resolve_poster_url
    from app.services.config_service import update_config

    await update_config(tmdb_api_key="eyJtest")
    job = DiscJob(drive_id="E:", content_type=ContentType.TV, tmdb_id=1396)

    with patch("requests.get", side_effect=RuntimeError("network dead")):
        assert await resolve_poster_url(job) is None

    await update_config(tmdb_api_key="")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/unit/test_discord_notifier.py -k resolve_poster -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.core.tmdb_poster'`

- [ ] **Step 3: Create the module**

Create `backend/app/core/tmdb_poster.py`:

```python
"""Resolve a TMDB poster URL for a DiscJob.

Extracted from the /api/jobs/{id}/poster route so the Discord notifier and the
dashboard share one implementation. Best-effort by contract: every failure path
returns None rather than raising, because both callers treat the poster as
decoration.
"""

import asyncio

import requests
from loguru import logger

from app.models.disc_job import DiscJob


async def resolve_poster_url(job: DiscJob) -> str | None:
    """Return the TMDB poster URL for ``job``, or None.

    Prefers the authoritative ``job.tmdb_id`` (exact, immune to a garbled
    detected_title); falls back to a name search only when no id is set.
    """
    from app.core.tmdb_classifier import _build_auth
    from app.matcher.tmdb_client import BASE_IMAGE_URL
    from app.services.config_service import get_config

    config = await get_config()
    api_key = config.tmdb_api_key
    if not api_key:
        return None

    media = "movie" if job.content_type == "movie" else "tv"
    headers, params = _build_auth(api_key)

    try:
        if job.tmdb_id:
            # int() sanitizes the id into the URL (no path/host injection, and clears
            # CodeQL's partial-SSRF taint); the guard above ensures it is set.
            detail_url = f"https://api.themoviedb.org/3/{media}/{int(job.tmdb_id)}"
            response = await asyncio.to_thread(
                requests.get, detail_url, headers=headers, params=params, timeout=10
            )
            if response.status_code == 200:
                poster_path = response.json().get("poster_path")
                if poster_path:
                    return f"{BASE_IMAGE_URL}{poster_path}"
            return None

        if not job.detected_title:
            return None
        search_url = f"https://api.themoviedb.org/3/search/{media}"
        params["query"] = job.detected_title
        response = await asyncio.to_thread(
            requests.get, search_url, headers=headers, params=params, timeout=10
        )
        if response.status_code == 200:
            results = response.json().get("results", [])
            if results and results[0].get("poster_path"):
                return f"{BASE_IMAGE_URL}{results[0]['poster_path']}"
    except Exception as e:
        logger.warning(f"Error fetching poster: {e}", exc_info=True)

    return None
```

- [ ] **Step 4: Slim the route to a wrapper**

In `backend/app/api/routes.py`, replace the whole `get_job_poster` function (lines 1830-1878) with:

```python
@router.get("/jobs/{job_id}/poster")
async def get_job_poster(job: DiscJob = Depends(get_job_or_404)) -> dict:
    """Get the TMDB poster URL for a job."""
    from app.core.tmdb_poster import resolve_poster_url

    return {"poster_url": await resolve_poster_url(job)}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/unit/test_discord_notifier.py -k resolve_poster -v`
Expected: PASS (3 tests)

- [ ] **Step 6: Verify the route still works**

Run: `cd backend && uv run pytest tests/unit/test_api_routes.py -k poster -v`
Expected: PASS, or "no tests ran" if the route has no existing coverage. If no tests ran, that is acceptable; the extraction is verbatim.

- [ ] **Step 7: Commit**

```bash
git add backend/app/core/tmdb_poster.py backend/app/api/routes.py backend/tests/unit/test_discord_notifier.py
git commit -m "refactor(tmdb): extract poster resolution so the notifier and the API share it"
```

---

## Task 7: Thumbnail and dashboard deep link

**Files:**
- Modify: `backend/app/core/discord_notifier.py`
- Test: `backend/tests/unit/test_discord_notifier.py`

**Context you need:** `build_embed` already accepts `poster_url` and `link_url` keyword arguments from Task 3 but nothing computes the link yet. Discord rejects an embed whose `url` is not a valid absolute URL, so an unset `dashboard_base_url` must produce no key at all rather than an empty string.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/unit/test_discord_notifier.py`:

```python
# --------------------------------------------------------------------------- #
# build_dashboard_link / thumbnail
# --------------------------------------------------------------------------- #


def test_dashboard_link_built_from_base_url():
    from app.core.discord_notifier import build_dashboard_link

    assert build_dashboard_link("http://192.168.1.50:5173", 42) == (
        "http://192.168.1.50:5173/history/42"
    )


def test_dashboard_link_tolerates_trailing_slash():
    from app.core.discord_notifier import build_dashboard_link

    assert build_dashboard_link("http://192.168.1.50:5173/", 42) == (
        "http://192.168.1.50:5173/history/42"
    )


def test_dashboard_link_none_when_unset():
    """No base URL must yield no embed url key at all; Discord rejects an empty one."""
    from app.core.discord_notifier import build_dashboard_link

    assert build_dashboard_link("", 42) is None


def test_dashboard_link_none_for_unsafe_url():
    from app.core.discord_notifier import build_dashboard_link

    assert build_dashboard_link("javascript:alert(1)", 42) is None


def test_build_embed_omits_url_and_thumbnail_when_not_supplied():
    from app.core.discord_notifier import EVENTS, build_embed
    from app.models.disc_job import JobState

    job = DiscJob(drive_id="E:", volume_label="X")
    embed = build_embed(job, [], EVENTS[JobState.COMPLETED], "d")
    assert "url" not in embed
    assert "thumbnail" not in embed


def test_build_embed_attaches_url_and_thumbnail_when_supplied():
    from app.core.discord_notifier import EVENTS, build_embed
    from app.models.disc_job import JobState

    job = DiscJob(drive_id="E:", volume_label="X")
    embed = build_embed(
        job,
        [],
        EVENTS[JobState.REVIEW_NEEDED],
        "d",
        poster_url="https://image.tmdb.org/t/p/w500/abc.jpg",
        link_url="http://192.168.1.50:5173/history/7",
    )
    assert embed["url"] == "http://192.168.1.50:5173/history/7"
    assert embed["thumbnail"]["url"] == "https://image.tmdb.org/t/p/w500/abc.jpg"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/unit/test_discord_notifier.py -k "dashboard_link or url_and_thumbnail" -v`
Expected: FAIL with `ImportError: cannot import name 'build_dashboard_link'`

- [ ] **Step 3: Implement `build_dashboard_link`**

Add to `backend/app/core/discord_notifier.py`, above `build_embed`:

```python
def build_dashboard_link(base_url: str, job_id: int) -> str | None:
    """Deep link to this job's detail page, or None when no base URL is configured.

    Re-validates the stored value rather than trusting it: config can be written
    by an older build or edited in the DB directly, and an unvalidated string
    here becomes a clickable link in a chat channel.
    """
    from app.core.security import is_safe_dashboard_url

    if not base_url or not is_safe_dashboard_url(base_url):
        return None
    return f"{base_url.rstrip('/')}/history/{job_id}"
```

`build_embed` already handles both keyword arguments (Task 3, Step 3), so no change is needed there.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/unit/test_discord_notifier.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/core/discord_notifier.py backend/tests/unit/test_discord_notifier.py
git commit -m "feat(discord): add poster thumbnail and dashboard deep link to embeds"
```

---

## Task 8: New template variables

**Files:**
- Modify: `backend/app/core/discord_notifier.py` (`ALLOWED_TEMPLATE_VARS`, `build_template_context`)
- Test: `backend/tests/unit/test_discord_notifier.py`

**Context you need:** `{{drive}}` currently renders `job.volume_label`, not the drive letter. Changing its meaning would silently alter every saved template that uses it, so it keeps its value and gains correctly-named siblings. `build_template_context` gains an optional `titles` parameter; it must default to `None` so the existing two-argument callers and tests keep working.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/unit/test_discord_notifier.py`:

```python
# --------------------------------------------------------------------------- #
# New template variables
# --------------------------------------------------------------------------- #


def test_new_template_variables_validate():
    template = (
        "{{volume_label}} {{drive_id}} {{disc_number}} {{discdb_disc_slug}} "
        "{{review_reason}} {{episodes}} {{state}}"
    )
    assert validate_discord_template(template) is None


def test_drive_still_renders_volume_label_for_back_compat():
    """{{drive}} is misnamed but shipped; changing its value would silently break
    saved templates. It keeps volume_label and {{drive_id}} is the correct name."""
    job = DiscJob(drive_id="E:", volume_label="THE_WIRE_S1D3")
    context = build_template_context(job, job_id=1)
    assert context["drive"] == "THE_WIRE_S1D3"
    assert context["volume_label"] == "THE_WIRE_S1D3"
    assert context["drive_id"] == "E:"


def test_context_exposes_disc_and_review_metadata():
    from app.models.disc_job import JobState

    job = DiscJob(
        drive_id="E:",
        volume_label="THE_WIRE_S1D3",
        disc_number=3,
        discdb_disc_slug="S01D03",
        review_reason="Could not match 3 titles",
        state=JobState.REVIEW_NEEDED,
    )
    context = build_template_context(job, job_id=1)
    assert context["disc_number"] == "3"
    assert context["discdb_disc_slug"] == "S01D03"
    assert context["review_reason"] == "Could not match 3 titles"
    assert context["state"] == "review_needed"


def test_context_episodes_populated_from_titles():
    from app.models.disc_job import DiscTitle, TitleState

    job = DiscJob(drive_id="E:", volume_label="THE_WIRE_S1D1")
    titles = [
        DiscTitle(
            job_id=1,
            title_index=i,
            duration_seconds=1200,
            matched_episode=f"S01E0{i + 1}",
            state=TitleState.COMPLETED,
        )
        for i in range(2)
    ]
    context = build_template_context(job, job_id=1, titles=titles)
    assert context["episodes"] == "S01E01-E02 (2 episodes)"


def test_context_episodes_blank_without_titles():
    """Default argument keeps every existing two-arg caller working."""
    job = DiscJob(drive_id="E:", volume_label="X")
    assert build_template_context(job, job_id=1)["episodes"] == ""
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/unit/test_discord_notifier.py -k "template_variables or drive_still or disc_and_review or context_episodes" -v`
Expected: FAIL with `KeyError: 'volume_label'`

- [ ] **Step 3: Extend the allowed set and the context builder**

In `backend/app/core/discord_notifier.py`, replace `ALLOWED_TEMPLATE_VARS`:

```python
ALLOWED_TEMPLATE_VARS = frozenset(
    {
        "title",
        # Misnamed: renders volume_label, not the drive letter. Kept for
        # back-compat with saved templates; {{volume_label}} is the correct name
        # and {{drive_id}} is the actual drive.
        "drive",
        "volume_label",
        "drive_id",
        "disc_number",
        "discdb_disc_slug",
        "job_id",
        "content_type",
        "season",
        "tmdb_name",
        "tmdb_year",
        "duration",
        "error",
        "review_reason",
        "episodes",
        "state",
        "subtitle_status",
        "subtitles_downloaded",
        "subtitles_total",
        "subtitles_failed",
        "path",
        "total_titles",
    }
)
```

Replace `build_template_context`:

```python
def build_template_context(
    job: DiscJob | None, job_id: int, titles: list | None = None
) -> dict[str, str]:
    """Build the chevron render context from a DiscJob row.

    ``titles`` is optional so every existing two-argument caller keeps working;
    without it, {{episodes}} renders empty.
    """
    if job is None:
        return {"title": f"Job #{job_id}"} | dict.fromkeys(ALLOWED_TEMPLATE_VARS - {"title"}, "")

    title = job.detected_title or job.volume_label or f"Job #{job_id}"
    return {
        "title": title,
        "drive": job.volume_label or "",
        "volume_label": job.volume_label or "",
        "drive_id": job.drive_id or "",
        "disc_number": str(job.disc_number) if job.disc_number is not None else "",
        "discdb_disc_slug": job.discdb_disc_slug or "",
        "job_id": str(job.id or job_id),
        "content_type": job.content_type.value.replace("_", " ").title(),
        "season": str(job.detected_season) if job.detected_season is not None else "",
        "tmdb_name": job.tmdb_name or "",
        "tmdb_year": str(job.tmdb_year) if job.tmdb_year is not None else "",
        "duration": _format_duration(job),
        "error": job.error_message or "",
        "review_reason": job.review_reason or "",
        "episodes": summarize_episodes(titles or []),
        "state": job.state.value,
        "subtitle_status": job.subtitle_status or "",
        "subtitles_downloaded": str(job.subtitles_downloaded),
        "subtitles_total": str(job.subtitles_total),
        "subtitles_failed": str(job.subtitles_failed),
        "path": job.final_path or "",
        "total_titles": str(job.total_titles),
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/unit/test_discord_notifier.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/core/discord_notifier.py backend/tests/unit/test_discord_notifier.py
git commit -m "feat(discord): add disc, review and episode template variables"
```

---

## Task 9: `on_transition` carries `from_state`

**Files:**
- Modify: `backend/app/services/job_state_machine.py:71-78` (docstring), `:156-161` (dispatch)
- Modify: `backend/app/services/job_manager.py:1177-1187` (two observers)
- Test: `backend/tests/unit/test_discord_notifier.py`

**Context you need:** `transition()` returns early-True when `from_state == to_state` and still re-broadcasts, so without `from_state` an observer cannot tell a genuine entry into review from a re-broadcast of a job already parked there. `from_state` is already in scope at `job_state_machine.py:117`. This task only widens the contract; the review notification arrives in Task 10.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/unit/test_discord_notifier.py`:

```python
# --------------------------------------------------------------------------- #
# on_transition contract
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_on_transition_callbacks_receive_from_state():
    """Observers need from_state to distinguish entering a state from a same-state
    re-broadcast, which transition() also performs."""
    from app.database import async_session
    from app.models import JobState
    from app.services.event_broadcaster import EventBroadcaster
    from app.services.job_state_machine import JobStateMachine

    broadcaster = MagicMock(spec=EventBroadcaster)
    broadcaster.broadcast_job_state_changed = AsyncMock()
    machine = JobStateMachine(broadcaster)

    seen = []
    machine.on_transition(lambda job_id, to_state, from_state: seen.append((to_state, from_state)))

    async with async_session() as session:
        job = DiscJob(drive_id="E:", content_type=ContentType.TV, state=JobState.RIPPING)
        session.add(job)
        await session.commit()
        await session.refresh(job)
        await machine.transition(job, JobState.REVIEW_NEEDED, session, broadcast=False)

    assert seen == [(JobState.REVIEW_NEEDED, JobState.RIPPING)]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/unit/test_discord_notifier.py::test_on_transition_callbacks_receive_from_state -v`
Expected: FAIL with `TypeError: <lambda>() missing 1 required positional argument: 'from_state'`

- [ ] **Step 3: Widen the dispatch**

In `backend/app/services/job_state_machine.py`, update the `on_transition` docstring (lines 71-78):

```python
    def on_transition(self, callback) -> None:
        """Register a callback invoked after every successful state transition.

        Callback signature: callback(job_id: int, to_state: JobState,
        from_state: JobState). ``from_state`` is what lets an observer tell a
        genuine entry into a state from the same-state re-broadcast this method
        also performs (see can_transition). Synchronous and best-effort:
        exceptions are logged, never raised.
        """
        self._on_transition_callbacks.append(callback)
```

Update the dispatch loop (lines 156-161):

```python
        # Notify transition observers (e.g. watchdog activity clock). Best-effort.
        for cb in self._on_transition_callbacks:
            try:
                cb(job.id, to_state, from_state)
            except Exception as e:
                logger.error(f"Job {job.id}: transition callback failed: {e}", exc_info=True)
```

- [ ] **Step 4: Update the two existing observers**

In `backend/app/services/job_manager.py`, replace both observers (lines 1177-1187):

```python
    def _note_activity_on_transition(
        self, job_id: int, state: JobState, from_state: JobState
    ) -> None:
        """on_transition observer: reset the clock on phase change, drop it on terminal."""
        if state in (JobState.COMPLETED, JobState.FAILED):
            self._last_activity.pop(job_id, None)
        else:
            self._last_activity[job_id] = time.monotonic()

    def _start_prewarm_on_review(
        self, job_id: int, state: JobState, from_state: JobState
    ) -> None:
        """on_transition observer: prewarm transcripts when a job parks in review."""
        if state == JobState.REVIEW_NEEDED:
            self._prewarmer.kickoff(job_id)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/unit/test_discord_notifier.py::test_on_transition_callbacks_receive_from_state -v`
Expected: PASS

- [ ] **Step 6: Run the suites that exercise the transition path**

Run: `cd backend && uv run pytest tests/unit/test_completion_skipped.py tests/unit/test_watchdog_cancel_attribution.py -v`
Expected: PASS. If either fails with a `TypeError` about argument count, it registers its own `on_transition` callback; add the third parameter there too.

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/job_state_machine.py backend/app/services/job_manager.py backend/tests/unit/test_discord_notifier.py
git commit -m "refactor(state-machine): pass from_state to on_transition observers"
```

---

## Task 10: Review notification and unified send path

**Files:**
- Modify: `backend/app/services/job_manager.py` (callback registration around line 287, new `_notify_discord_on_review`, `_send_discord_notification`)
- Test: `backend/tests/unit/test_discord_notifier.py`

**Context you need:** This is user request 2. `_send_discord_notification` becomes the single path for all three events: it applies the per-event toggle, loads `DiscTitle` rows for the episode manifest, resolves the poster, builds the deep link, and attaches the mention on review only. A `None` toggle reads as enabled, so a NULL column can never silently mute notifications.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/unit/test_discord_notifier.py`:

```python
# --------------------------------------------------------------------------- #
# Review notification and per-event toggles
# --------------------------------------------------------------------------- #


async def _make_job(**kwargs):
    """Persist a job and return its id."""
    from app.database import async_session

    async with async_session() as session:
        job = DiscJob(drive_id="E:", **kwargs)
        session.add(job)
        await session.commit()
        await session.refresh(job)
        return job.id


@pytest.mark.asyncio
async def test_review_observer_fires_on_entry_to_review():
    from app.models import JobState
    from app.services.job_manager import job_manager

    with patch.object(
        job_manager, "_send_discord_notification", new_callable=AsyncMock
    ) as mock_send:
        job_manager._notify_discord_on_review(7, JobState.REVIEW_NEEDED, JobState.MATCHING)
        await asyncio.sleep(0)

    mock_send.assert_called_once_with(7, JobState.REVIEW_NEEDED)


@pytest.mark.asyncio
async def test_review_observer_suppresses_same_state_rebroadcast():
    """transition() re-broadcasts same-state; that must not re-ping the channel."""
    from app.models import JobState
    from app.services.job_manager import job_manager

    with patch.object(
        job_manager, "_send_discord_notification", new_callable=AsyncMock
    ) as mock_send:
        job_manager._notify_discord_on_review(7, JobState.REVIEW_NEEDED, JobState.REVIEW_NEEDED)
        await asyncio.sleep(0)

    mock_send.assert_not_called()


@pytest.mark.asyncio
async def test_review_observer_ignores_other_states():
    from app.models import JobState
    from app.services.job_manager import job_manager

    with patch.object(
        job_manager, "_send_discord_notification", new_callable=AsyncMock
    ) as mock_send:
        job_manager._notify_discord_on_review(7, JobState.RIPPING, JobState.IDENTIFYING)
        await asyncio.sleep(0)

    mock_send.assert_not_called()


@pytest.mark.asyncio
async def test_review_notification_uses_review_event_and_mention():
    from app.models import JobState
    from app.services.config_service import update_config
    from app.services.job_manager import job_manager

    await update_config(
        discord_webhook_url="https://discord.com/api/webhooks/1/tok",
        discord_mention_review="<@1234>",
        dashboard_base_url="http://192.168.1.50:5173",
    )
    job_id = await _make_job(
        content_type=ContentType.TV,
        detected_title="The Wire",
        volume_label="THE_WIRE_S1D3",
        disc_number=3,
        review_reason="Could not match 3 titles",
        state=JobState.REVIEW_NEEDED,
    )

    with patch("app.core.discord_notifier.notify_discord", new_callable=AsyncMock) as mock_notify:
        with patch("app.core.tmdb_poster.resolve_poster_url", new_callable=AsyncMock,
                   return_value=None):
            await job_manager._send_discord_notification(job_id, JobState.REVIEW_NEEDED)

    embed = mock_notify.call_args[0][2]
    assert "Review Needed" in embed["title"]
    assert embed["url"] == f"http://192.168.1.50:5173/history/{job_id}"
    by_name = {f["name"]: f["value"] for f in embed["fields"]}
    assert by_name["Disc"] == "THE_WIRE_S1D3 (Disc 3)"
    assert by_name["Reason"] == "Could not match 3 titles"
    assert mock_notify.call_args.kwargs["content"] == "<@1234>"

    await update_config(discord_mention_review="", dashboard_base_url="")


@pytest.mark.asyncio
async def test_mention_not_attached_to_completed_event():
    from app.models import JobState
    from app.services.config_service import update_config
    from app.services.job_manager import job_manager

    await update_config(
        discord_webhook_url="https://discord.com/api/webhooks/1/tok",
        discord_mention_review="<@1234>",
    )
    job_id = await _make_job(content_type=ContentType.MOVIE, volume_label="INCEPTION_2010")

    with patch("app.core.discord_notifier.notify_discord", new_callable=AsyncMock) as mock_notify:
        with patch("app.core.tmdb_poster.resolve_poster_url", new_callable=AsyncMock,
                   return_value=None):
            await job_manager._send_discord_notification(job_id, JobState.COMPLETED)

    assert mock_notify.call_args.kwargs["content"] == ""

    await update_config(discord_mention_review="")


@pytest.mark.asyncio
async def test_per_event_toggle_suppresses_only_its_own_event():
    from app.models import JobState
    from app.services.config_service import update_config
    from app.services.job_manager import job_manager

    await update_config(
        discord_webhook_url="https://discord.com/api/webhooks/1/tok",
        discord_notify_review=False,
        discord_notify_completed=True,
    )
    job_id = await _make_job(content_type=ContentType.TV, volume_label="X")

    with patch("app.core.discord_notifier.notify_discord", new_callable=AsyncMock) as mock_notify:
        with patch("app.core.tmdb_poster.resolve_poster_url", new_callable=AsyncMock,
                   return_value=None):
            await job_manager._send_discord_notification(job_id, JobState.REVIEW_NEEDED)
            assert mock_notify.call_count == 0
            await job_manager._send_discord_notification(job_id, JobState.COMPLETED)
            assert mock_notify.call_count == 1

    await update_config(discord_notify_review=True)


@pytest.mark.asyncio
async def test_completion_notification_includes_episode_manifest():
    """The box-set payoff: a finished disc reports what actually landed."""
    from app.database import async_session
    from app.models import JobState
    from app.models.disc_job import DiscTitle, TitleState
    from app.services.config_service import update_config
    from app.services.job_manager import job_manager

    await update_config(discord_webhook_url="https://discord.com/api/webhooks/1/tok")
    job_id = await _make_job(
        content_type=ContentType.TV, detected_title="The Wire", volume_label="THE_WIRE_S1D1"
    )

    async with async_session() as session:
        for i in range(3):
            session.add(
                DiscTitle(
                    job_id=job_id,
                    title_index=i,
                    duration_seconds=1200,
                    matched_episode=f"S01E0{i + 1}",
                    state=TitleState.COMPLETED,
                )
            )
        await session.commit()

    with patch("app.core.discord_notifier.notify_discord", new_callable=AsyncMock) as mock_notify:
        with patch("app.core.tmdb_poster.resolve_poster_url", new_callable=AsyncMock,
                   return_value=None):
            await job_manager._send_discord_notification(job_id, JobState.COMPLETED)

    by_name = {f["name"]: f["value"] for f in mock_notify.call_args[0][2]["fields"]}
    assert by_name["Episodes"] == "S01E01-E03 (3 episodes)"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/unit/test_discord_notifier.py -k "review_observer or review_notification or mention_not or per_event_toggle or episode_manifest" -v`
Expected: FAIL with `AttributeError: 'JobManager' object has no attribute '_notify_discord_on_review'`

- [ ] **Step 3: Add the review observer**

In `backend/app/services/job_manager.py`, add after `_start_prewarm_on_review`:

```python
    def _notify_discord_on_review(
        self, job_id: int, state: JobState, from_state: JobState
    ) -> None:
        """on_transition observer: ping Discord when a job parks in review.

        REVIEW_NEEDED is not terminal, so on_terminal_state cannot carry it;
        on_transition is the single chokepoint every review-parking path flows
        through. Same-state re-entry is suppressed (transition() re-broadcasts
        without a real state change), but REVIEW -> IDENTIFYING -> REVIEW does
        re-notify: that is a new question for the user, not a duplicate.
        """
        if state != JobState.REVIEW_NEEDED or from_state == JobState.REVIEW_NEEDED:
            return
        asyncio.create_task(self._send_discord_notification(job_id, state))
```

- [ ] **Step 4: Register it**

In `backend/app/services/job_manager.py`, after the `state_machine.on_transition(self._start_prewarm_on_review)` line (around line 297), add:

```python
        # Review-needed Discord ping. Registered on on_transition rather than
        # on_terminal_state because REVIEW_NEEDED is not a terminal state.
        state_machine.on_transition(self._notify_discord_on_review)
```

- [ ] **Step 5: Finish `_send_discord_notification`**

Replace the whole method body written in Task 3:

```python
    async def _send_discord_notification(self, job_id: int, state: JobState) -> None:
        """Send the Discord notification for one job event.

        Single path for completed, failed and review. Runs as a background task;
        all errors are swallowed so a slow or unreachable webhook can never
        affect the pipeline.
        """
        try:
            from sqlmodel import select

            from app.core.discord_notifier import (
                DEFAULT_TEMPLATES,
                EVENTS,
                build_dashboard_link,
                build_embed,
                build_template_context,
                notify_discord,
                render_discord_template,
            )
            from app.core.tmdb_poster import resolve_poster_url
            from app.services.config_service import get_config

            config = await get_config()
            if not config.discord_webhook_url:
                return

            event = EVENTS.get(state)
            if event is None:
                logger.warning(
                    f"Job {job_id}: Discord notification requested for non-notifiable state {state}"
                )
                return

            # `is False` rather than falsy: a NULL column left by an out-of-band
            # schema change must read as enabled, never as "user muted this".
            toggles = {
                "completed": config.discord_notify_completed,
                "failed": config.discord_notify_failed,
                "review": config.discord_notify_review,
            }
            if toggles[event.key] is False:
                return

            async with async_session() as session:
                job = await session.get(DiscJob, job_id)
                result = await session.execute(
                    select(DiscTitle).where(DiscTitle.job_id == job_id)
                )
                titles = list(result.scalars().all())

            templates = {
                "completed": config.discord_template_completed,
                "failed": config.discord_template_failed,
                "review": config.discord_template_review,
            }
            template = templates[event.key] or DEFAULT_TEMPLATES[event.key]

            context = build_template_context(job, job_id, titles)
            description = render_discord_template(template, context)

            poster_url = await resolve_poster_url(job) if job else None
            link_url = build_dashboard_link(config.dashboard_base_url, job_id)
            content = config.discord_mention_review if event.key == "review" else ""

            embed = build_embed(
                job, titles, event, description, poster_url=poster_url, link_url=link_url
            )
            await notify_discord(
                config.discord_webhook_url, job_id, embed, content=content or ""
            )
        except Exception as e:
            logger.warning(f"Job {job_id}: Discord notification failed: {e}", exc_info=True)
```

- [ ] **Step 6: Run the new tests**

Run: `cd backend && uv run pytest tests/unit/test_discord_notifier.py -k "review_observer or review_notification or mention_not or per_event_toggle or episode_manifest" -v`
Expected: PASS (7 tests)

- [ ] **Step 7: Run the whole notifier suite**

Run: `cd backend && uv run pytest tests/unit/test_discord_notifier.py -v`
Expected: PASS. Earlier tests that assert `mock_notify.call_args[0][2]` still hold because `embed` remains the third positional argument.

- [ ] **Step 8: Commit**

```bash
git add backend/app/services/job_manager.py backend/tests/unit/test_discord_notifier.py
git commit -m "feat(discord): notify on REVIEW_NEEDED with mention, deep link and per-event toggles"
```

---

## Task 11: Test-webhook endpoint

**Files:**
- Modify: `backend/app/api/validation.py` (append after `validate_discord_template_endpoint`)
- Test: `backend/tests/unit/test_validation.py`

**Context you need:** The webhook URL is redacted to `***` in `GET /api/config`, so the frontend cannot re-send it to test. A blank `webhook_url` therefore means "use the saved one". The endpoint must reuse the `is_safe_remote_url` guard that `PUT /api/config` applies; without it, an unauthenticated caller could use this endpoint to probe internal hosts.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/unit/test_validation.py`:

```python
# --------------------------------------------------------------------------- #
# POST /api/validate/discord-webhook
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_test_webhook_rejects_internal_host(client):
    """Without the SSRF guard this endpoint is an internal-host probe."""
    resp = await client.post(
        "/api/validate/discord-webhook", json={"webhook_url": "http://169.254.169.254/latest"}
    )
    assert resp.status_code == 200
    assert resp.json()["valid"] is False


@pytest.mark.asyncio
async def test_test_webhook_errors_when_nothing_configured(client):
    from app.services.config_service import update_config

    await update_config(discord_webhook_url="")
    resp = await client.post("/api/validate/discord-webhook", json={})
    assert resp.json()["valid"] is False
    assert "no webhook" in resp.json()["error"].lower()


@pytest.mark.asyncio
async def test_test_webhook_posts_a_sample_embed(client):
    from unittest.mock import AsyncMock, patch

    from app.services.config_service import update_config

    await update_config(discord_webhook_url="https://discord.com/api/webhooks/1/tok")

    with patch("app.core.discord_notifier.notify_discord", new_callable=AsyncMock) as mock_notify:
        resp = await client.post("/api/validate/discord-webhook", json={})

    assert resp.json()["valid"] is True
    mock_notify.assert_called_once()
    embed = mock_notify.call_args[0][2]
    assert any(f["name"] == "Disc" for f in embed["fields"])

    await update_config(discord_webhook_url="")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/unit/test_validation.py -k test_webhook -v`
Expected: FAIL with 404 (the route does not exist)

- [ ] **Step 3: Implement the endpoint**

Append to `backend/app/api/validation.py`, after `validate_discord_template_endpoint`:

```python
class DiscordWebhookTestRequest(BaseModel):
    """Request model for the Discord webhook smoke test.

    A blank/omitted url means "use the saved one": GET /api/config redacts the
    stored webhook to "***", so the frontend has nothing to send back.
    """

    webhook_url: str | None = None


@router.post("/validate/discord-webhook", response_model=ValidationResponse)
async def test_discord_webhook(request: DiscordWebhookTestRequest) -> ValidationResponse:
    """Post a sample notification so the user can confirm the webhook works.

    Builds the sample through the real build_embed path, so a passing test
    message proves the production builder works, not just the URL.
    """
    from app.core.discord_notifier import EVENTS, build_embed, notify_discord
    from app.core.security import is_safe_remote_url
    from app.models.disc_job import ContentType, DiscJob, JobState
    from app.services.config_service import get_config

    config = await get_config()
    webhook_url = (request.webhook_url or "").strip() or config.discord_webhook_url
    if not webhook_url:
        return ValidationResponse(valid=False, error="No webhook URL configured or supplied")

    # Same guard PUT /api/config applies. Without it this endpoint becomes an
    # unauthenticated probe against internal hosts.
    if not is_safe_remote_url(webhook_url):
        return ValidationResponse(
            valid=False,
            error="Webhook URL must be an http/https URL pointing to a non-internal host",
        )

    sample = DiscJob(
        drive_id="E:",
        content_type=ContentType.TV,
        detected_title="Engram Test",
        volume_label="ENGRAM_TEST_S1D1",
        detected_season=1,
        disc_number=1,
        total_titles=6,
        state=JobState.COMPLETED,
    )
    embed = build_embed(sample, [], EVENTS[JobState.COMPLETED], "**Engram Test**")

    # Positional, matching every other call site: the tests assert on
    # call_args[0][2] being the embed.
    try:
        await notify_discord(webhook_url, 0, embed)
    except Exception as e:  # notify_discord swallows its own errors; belt and braces
        logger.warning(f"Discord webhook test failed: {e}", exc_info=True)
        return ValidationResponse(valid=False, error=f"Failed to post: {e}")

    return ValidationResponse(valid=True)
```

Note: `notify_discord` swallows HTTP errors internally, so a bad-but-well-formed URL reports `valid=True`. That is acceptable for a smoke test and keeps the transport function's contract intact; the user sees the message arrive or not.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/unit/test_validation.py -k test_webhook -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/validation.py backend/tests/unit/test_validation.py
git commit -m "feat(discord): add POST /api/validate/discord-webhook smoke test"
```

---

## Task 12: ConfigWizard UI

**Files:**
- Create: `frontend/src/utils/discordWebhookTest.ts`
- Modify: `frontend/src/components/ConfigWizard.tsx`
- Test: `frontend/src/components/ConfigWizard.test.tsx`

**Context you need:** Run `npm install` first if `node_modules` is absent in this worktree; it rewrites `package-lock.json` with a large diff, so `git checkout package-lock.json` before committing. `ConfigData` is a flat interface; every field needs adding in four places within the component: the interface, the `useState` initializer, the `loadConfig` setter, and the save payload.

- [ ] **Step 1: Create the fetch wrapper**

Create `frontend/src/utils/discordWebhookTest.ts`:

```typescript
/**
 * Discord webhook smoke test against POST /api/validate/discord-webhook.
 *
 * Same three-outcome shape as requestDiscordTemplateValidation: 'ok' | 'invalid'
 * (the server could not post) | 'error' (the check itself could not run).
 */
export type DiscordWebhookTestResult =
  | { status: 'ok' }
  | { status: 'invalid'; error: string }
  | { status: 'error'; error: string };

export async function requestDiscordWebhookTest(
  webhookUrl: string,
): Promise<DiscordWebhookTestResult> {
  let response: Response;
  try {
    response = await fetch('/api/validate/discord-webhook', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      // Blank means "use the saved webhook": GET /api/config redacts it to "***",
      // so there is nothing for the form to send back.
      body: JSON.stringify({ webhook_url: webhookUrl || null }),
    });
  } catch (err) {
    console.error('Discord webhook test request failed (network):', err);
    return { status: 'error', error: "Couldn't reach the backend" };
  }

  if (!response.ok) {
    return { status: 'error', error: `Test endpoint returned HTTP ${response.status}` };
  }

  try {
    const result = await response.json();
    return result.valid
      ? { status: 'ok' }
      : { status: 'invalid', error: result.error || 'Webhook test failed' };
  } catch {
    return { status: 'error', error: "Couldn't read the response" };
  }
}
```

- [ ] **Step 2: Add the fields to `ConfigData`**

In `frontend/src/components/ConfigWizard.tsx`, replace the three Discord lines in the `ConfigData` interface (currently lines 178-180):

```typescript
    discordWebhookUrl: string;
    discordTemplateCompleted: string;
    discordTemplateFailed: string;
    discordTemplateReview: string;
    discordNotifyCompleted: boolean;
    discordNotifyFailed: boolean;
    discordNotifyReview: boolean;
    discordMentionReview: string;
    dashboardBaseUrl: string;
```

- [ ] **Step 3: Add them to the state initializer**

Replace lines 260-262:

```typescript
        discordWebhookUrl: '',
        discordTemplateCompleted: '',
        discordTemplateFailed: '',
        discordTemplateReview: '',
        discordNotifyCompleted: true,
        discordNotifyFailed: true,
        discordNotifyReview: true,
        discordMentionReview: '',
        dashboardBaseUrl: '',
```

- [ ] **Step 4: Load them from the API**

Replace lines 379-381:

```typescript
                    discordWebhookUrl: data.discord_webhook_url === '***' ? '' : (data.discord_webhook_url || ''),
                    discordTemplateCompleted: data.discord_template_completed || '',
                    discordTemplateFailed: data.discord_template_failed || '',
                    discordTemplateReview: data.discord_template_review || '',
                    discordNotifyCompleted: data.discord_notify_completed ?? true,
                    discordNotifyFailed: data.discord_notify_failed ?? true,
                    discordNotifyReview: data.discord_notify_review ?? true,
                    discordMentionReview: data.discord_mention_review || '',
                    dashboardBaseUrl: data.dashboard_base_url || '',
```

- [ ] **Step 5: Send them on save**

Replace lines 567-569:

```typescript
                    ...optional('discord_webhook_url', config.discordWebhookUrl),
                    discord_template_completed: config.discordTemplateCompleted,
                    discord_template_failed: config.discordTemplateFailed,
                    discord_template_review: config.discordTemplateReview,
                    discord_notify_completed: config.discordNotifyCompleted,
                    discord_notify_failed: config.discordNotifyFailed,
                    discord_notify_review: config.discordNotifyReview,
                    discord_mention_review: config.discordMentionReview,
                    dashboard_base_url: config.dashboardBaseUrl,
```

- [ ] **Step 6: Extend live template validation to three templates**

Replace the error state declaration (line 266):

```typescript
    const [discordTemplateErrors, setDiscordTemplateErrors] = useState<{completed: string; failed: string; review: string}>({completed: '', failed: '', review: ''});
    const [webhookTest, setWebhookTest] = useState<{status: 'idle' | 'testing' | 'ok' | 'error', error?: string}>({status: 'idle'});
```

Replace the validation effect (lines 395-414):

```typescript
    useEffect(() => {
        const timer = window.setTimeout(async () => {
            const [completedResult, failedResult, reviewResult] = await Promise.all([
                config.discordTemplateCompleted
                    ? requestDiscordTemplateValidation(config.discordTemplateCompleted)
                    : Promise.resolve({ status: 'valid' as const }),
                config.discordTemplateFailed
                    ? requestDiscordTemplateValidation(config.discordTemplateFailed)
                    : Promise.resolve({ status: 'valid' as const }),
                config.discordTemplateReview
                    ? requestDiscordTemplateValidation(config.discordTemplateReview)
                    : Promise.resolve({ status: 'valid' as const }),
            ]);
            setDiscordTemplateErrors({
                completed: completedResult.status === 'invalid' ? completedResult.error : '',
                failed: failedResult.status === 'invalid' ? failedResult.error : '',
                review: reviewResult.status === 'invalid' ? reviewResult.error : '',
            });
        }, 400);
        return () => window.clearTimeout(timer);
    }, [config.discordTemplateCompleted, config.discordTemplateFailed, config.discordTemplateReview]);
```

Add the import next to the existing one (line 12):

```typescript
import { requestDiscordWebhookTest } from '../utils/discordWebhookTest';
```

Add the handler next to `handleInputChange` (after line 482):

```typescript
    const handleTestWebhook = async () => {
        setWebhookTest({ status: 'testing' });
        const result = await requestDiscordWebhookTest(config.discordWebhookUrl);
        setWebhookTest(
            result.status === 'ok' ? { status: 'ok' } : { status: 'error', error: result.error },
        );
    };
```

- [ ] **Step 7: Add the controls**

In the Notifications group, immediately after the webhook URL `form-group` closing `</div>` (currently line 1906), insert:

```tsx
                        <div className="form-group">
                            <SvActionButton type="button" onClick={handleTestWebhook} disabled={webhookTest.status === 'testing'}>
                                {webhookTest.status === 'testing' ? 'Sending...' : 'Send test message'}
                            </SvActionButton>
                            {webhookTest.status === 'ok' && (
                                <span style={{color: '#22c55e', fontSize: '0.85rem', marginLeft: '0.75rem'}}>✓ Sent</span>
                            )}
                            {webhookTest.status === 'error' && (
                                <span style={{color: '#ef4444', fontSize: '0.85rem', marginLeft: '0.75rem'}}>✗ {webhookTest.error}</span>
                            )}
                            <span className="form-hint">
                                Posts a sample notification. Leave the field above blank to test the saved webhook.
                            </span>
                        </div>

                        <div className="form-group">
                            <label htmlFor="dashboardBaseUrl">Dashboard URL</label>
                            <input
                                id="dashboardBaseUrl"
                                type="url"
                                value={config.dashboardBaseUrl}
                                onChange={(e) => handleInputChange('dashboardBaseUrl', e.target.value)}
                                placeholder="http://192.168.1.50:5173"
                            />
                            <span className="form-hint">
                                Where this dashboard is reachable on your network. When set, notifications
                                link straight to the job, so a review ping is one tap from the review screen.
                            </span>
                        </div>

                        <div className="form-group checkbox-group">
                            <label className="checkbox-label">
                                <input
                                    type="checkbox"
                                    checked={config.discordNotifyCompleted}
                                    onChange={(e) => handleInputChange('discordNotifyCompleted', e.target.checked)}
                                />
                                <span className="checkbox-text">
                                    <strong>Notify when a disc completes</strong>
                                </span>
                            </label>
                        </div>

                        <div className="form-group checkbox-group">
                            <label className="checkbox-label">
                                <input
                                    type="checkbox"
                                    checked={config.discordNotifyFailed}
                                    onChange={(e) => handleInputChange('discordNotifyFailed', e.target.checked)}
                                />
                                <span className="checkbox-text">
                                    <strong>Notify when a disc fails</strong>
                                </span>
                            </label>
                        </div>

                        <div className="form-group checkbox-group">
                            <label className="checkbox-label">
                                <input
                                    type="checkbox"
                                    checked={config.discordNotifyReview}
                                    onChange={(e) => handleInputChange('discordNotifyReview', e.target.checked)}
                                />
                                <span className="checkbox-text">
                                    <strong>Notify when a disc needs review</strong>
                                    <span className="checkbox-hint">
                                        Engram pings the channel when a disc parks waiting for you, so a
                                        walk-away rip does not stall silently.
                                    </span>
                                </span>
                            </label>
                        </div>

                        <div className="form-group">
                            <label htmlFor="discordMentionReview">Review Mention</label>
                            <input
                                id="discordMentionReview"
                                type="text"
                                value={config.discordMentionReview}
                                onChange={(e) => handleInputChange('discordMentionReview', e.target.value)}
                                placeholder="<@123456789012345678>"
                            />
                            <span className="form-hint">
                                Sent alongside the review notification so Discord actually pushes it to your
                                phone. Accepts {'<@userid>'}, {'<@&roleid>'}, or @here. Leave blank for no mention.
                            </span>
                        </div>
```

- [ ] **Step 8: Add the review template field**

Immediately after the Failed Message `form-group` closing `</div>` (currently line 1944), insert:

```tsx
                        <div className="form-group">
                            <label htmlFor="discordTemplateReview">Review Needed Message</label>
                            <textarea
                                id="discordTemplateReview"
                                value={config.discordTemplateReview}
                                onChange={(e) => handleInputChange('discordTemplateReview', e.target.value)}
                                placeholder="**{{title}}**"
                                rows={2}
                            />
                            {discordTemplateErrors.review && (
                                <span style={{color: '#ef4444', fontSize: '0.85rem'}}>✗ {discordTemplateErrors.review}</span>
                            )}
                            <span className="form-hint">
                                Same variables as above, plus {'{{'}review_reason{'}}'}. Leave blank to use the default.
                            </span>
                        </div>
```

- [ ] **Step 9: Update the variables hint**

Replace the Completed Message hint (currently lines 1920-1925):

```tsx
                            <span className="form-hint">
                                Available variables: {'{{'}title{'}}'}, {'{{'}volume_label{'}}'}, {'{{'}drive_id{'}}'},{' '}
                                {'{{'}disc_number{'}}'}, {'{{'}discdb_disc_slug{'}}'}, {'{{'}content_type{'}}'},{' '}
                                {'{{'}season{'}}'}, {'{{'}episodes{'}}'}, {'{{'}state{'}}'}, {'{{'}tmdb_name{'}}'},{' '}
                                {'{{'}tmdb_year{'}}'}, {'{{'}duration{'}}'}, {'{{'}subtitle_status{'}}'}, {'{{'}path{'}}'},{' '}
                                {'{{'}total_titles{'}}'}. ({'{{'}drive{'}}'} is a deprecated alias for {'{{'}volume_label{'}}'}.){' '}
                                Disc name, season and episodes also appear automatically as fields, so a blank
                                template still identifies the disc. Use triple braces ({'{{{'}var{'}}}'}) instead of
                                double to avoid HTML-escaping characters like {'&'}, {'<'}, {'>'}.
                            </span>
```

- [ ] **Step 10: Write the component test**

The Notifications group lives inside `case 5:` of `renderStepContent()`, which the settings
nav labels **Preferences**. The test must click that nav entry first; the section is not
rendered until then.

First extend `mockApi` in `frontend/src/components/ConfigWizard.test.tsx` so the new
endpoint is routed. Add this line next to the existing `discord-template` line:

```tsx
                if (url.includes('/api/validate/discord-webhook')) return { valid: true };
```

Then append this block to the file (top level, after the existing `describe` blocks):

```tsx
describe('ConfigWizard: Discord notification settings', () => {
    async function openPreferences() {
        render(<ConfigWizard {...noop} isOnboarding={false} />);
        const nav = await screen.findByRole('navigation', { name: /settings sections/i });
        fireEvent.click(within(nav).getByRole('button', { name: 'Preferences' }));
    }

    it('renders the review notification controls', async () => {
        await openPreferences();

        expect(await screen.findByLabelText(/Review Needed Message/i)).toBeInTheDocument();
        expect(screen.getByLabelText(/Review Mention/i)).toBeInTheDocument();
        expect(screen.getByLabelText(/Dashboard URL/i)).toBeInTheDocument();
        expect(screen.getByRole('button', { name: /Send test message/i })).toBeInTheDocument();
    });

    it('reports success after a webhook test', async () => {
        await openPreferences();

        fireEvent.click(await screen.findByRole('button', { name: /Send test message/i }));
        await waitFor(() => expect(screen.getByText(/✓ Sent/)).toBeInTheDocument());
    });
});
```

`render`, `screen`, `fireEvent`, `waitFor`, `within` and `noop` are all already imported or
defined in this file.

- [ ] **Step 11: Run the frontend checks**

Run: `cd frontend && npm run test:unit -- ConfigWizard`
Expected: PASS

Run: `cd frontend && npm run build`
Expected: no TypeScript errors

Run: `cd frontend && npm run lint`
Expected: no new errors

- [ ] **Step 12: Commit**

If `npm install` rewrote the lockfile: `git checkout frontend/package-lock.json` first.

```bash
git add frontend/src/utils/discordWebhookTest.ts frontend/src/components/ConfigWizard.tsx frontend/src/components/ConfigWizard.test.tsx
git commit -m "feat(ui): add review notification, toggles, mention, dashboard URL and webhook test to ConfigWizard"
```

---

## Task 13: Documentation and changelog

**Files:**
- Modify: `CHANGELOG.md`
- Modify: `docs/troubleshooting.md`
- Modify: `CLAUDE.md`

- [ ] **Step 1: Add the changelog entries**

Under `## [Unreleased]` in `CHANGELOG.md`, add (creating the subsections if absent):

```markdown
### Added

- Discord notifications now identify the disc. Every notification carries structured
  fields for disc name and number, season, duration, track count and subtitle status, so
  box-set rips are distinguishable without editing a template.
- Completion notifications list the episodes that landed, e.g. `S01E01-E04 (4 episodes)`.
- Discord notifications when a disc parks in review, with an optional mention so the ping
  reaches your phone and an optional deep link straight to the review screen.
- Per-event notification toggles for completed, failed and review-needed.
- A "Send test message" button in Settings, so a webhook can be verified at setup time
  instead of on the next finished disc.
- TMDB poster thumbnails on notifications.
- New template variables: `volume_label`, `drive_id`, `disc_number`, `discdb_disc_slug`,
  `review_reason`, `episodes`, `state`.

### Changed

- `{{drive}}` is now documented as a deprecated alias for `{{volume_label}}`. It keeps
  rendering the disc volume label, so existing templates are unaffected.
```

- [ ] **Step 2: Document the settings**

`docs/troubleshooting.md` mentions Discord only as a community link; there is no settings
section to extend. Add a new one before the diagnostics-bundle section:

```markdown
## Discord notifications aren't arriving

Engram posts to a Discord webhook when a disc completes, fails, or parks waiting for you.
Settings live under **Settings → Preferences → Notifications**.

**First, prove the webhook works.** Click **Send test message**. A sample notification
should appear in the channel within a second or two. If it doesn't:

- Re-create the webhook in Discord under **Channel Settings → Integrations → Webhooks**
  and paste the fresh URL. Webhook URLs are revoked when the channel or the webhook is
  deleted, and Engram cannot tell a revoked URL from a working one.
- Check that the URL starts with `https://discord.com/api/webhooks/`.

**The test works but real discs are silent.** Check the three per-event toggles. Each
event has its own switch, so it's possible to have completions enabled and review
notifications turned off (or the reverse).

**Notifications arrive but don't alert my phone.** Discord does not push notifications for
embed content. Set **Review Mention** to your user ID (`<@123456789012345678>`), a role
(`<@&987654321098765432>`), or `@here`. That value is sent as the message body, which is
the part Discord actually pings on. To find your user ID, enable **Developer Mode** in
Discord's Advanced settings, then right-click your name and choose **Copy User ID**.

**Notifications aren't clickable.** Set **Dashboard URL** to the address you use to reach
Engram from other devices, for example `http://192.168.1.50:5173`. Notifications then link
straight to the job. Leave it blank and notifications still arrive, just without a link.

**Every disc in my box set looks the same.** Each notification carries a **Disc** field
built from the disc's volume label plus its disc number, so `THE_WIRE_S1 (Disc 3)` is
distinct from disc 4. If the field shows only `Disc 1`, the disc reported no volume label
and Engram has no better identity to use; naming the disc during identification fixes it
for that job.
```

- [ ] **Step 3: Update CLAUDE.md**

Replace the Discord bullet under "Key Configuration Fields":

```markdown
- **Discord notifications**: `discord_template_completed` / `discord_template_failed` /
  `discord_template_review` (customizable embed description, chevron `{{var}}` mustache
  syntax, see `app/core/discord_notifier.py::ALLOWED_TEMPLATE_VARS`; empty string = built-in
  default). Per-event toggles `discord_notify_completed` / `_failed` / `_review`.
  `discord_mention_review` rides as message `content` (embeds do not resolve mentions).
  `dashboard_base_url` makes notifications link to `/history/{job_id}`; it is deliberately
  exempt from `is_safe_remote_url` because a LAN address is the expected value and the
  server never fetches it.
```

Add to the "Key Patterns" list:

```markdown
- **Notification events**: `EVENTS` in `app/core/discord_notifier.py` maps `JobState` to
  presentation. Terminal events arrive via `on_terminal_state`; `REVIEW_NEEDED` arrives via
  `on_transition`, which receives `(job_id, to_state, from_state)` so a same-state
  re-broadcast does not re-notify.
```

- [ ] **Step 4: Verify no em dashes were introduced**

Run:
```bash
cd backend && uv run python -c "
import pathlib
for p in ['../CHANGELOG.md','../CLAUDE.md','../docs/troubleshooting.md']:
    t = pathlib.Path(p).read_text(encoding='utf-8')
    print(p, t.count(chr(0x2014)) + t.count(chr(0x2013)))
"
```
Expected: the counts must not have increased from before your edits. (`CLAUDE.md` and `CHANGELOG.md` already contain some.)

- [ ] **Step 5: Run the full backend unit suite**

Run: `cd backend && uv run pytest tests/unit/ -q`
Expected: PASS. This takes several minutes; do not run it inside a subagent with a short timeout.

- [ ] **Step 6: Lint**

Run: `cd backend && uv run ruff check . && uv run ruff format --check .`
Expected: no errors

- [ ] **Step 7: Commit**

```bash
git add CHANGELOG.md CLAUDE.md docs/troubleshooting.md
git commit -m "docs: document Discord notification improvements"
```

---

## Manual verification

Before opening the PR, exercise the two requested features end to end with simulation.

- [ ] **Step 1: Start the backend**

```bash
cd backend && DEBUG=true uv run uvicorn app.main:app --port 8000
```

Never use `--reload`; it spawns a second drive sentinel and duplicates disc events.

- [ ] **Step 2: Configure a webhook**

Open Settings, paste a real Discord webhook URL, set the Dashboard URL to
`http://localhost:5173`, set a Review Mention, and click "Send test message". A sample
embed with a `Disc` field should arrive.

- [ ] **Step 3: Verify disc identity on completion**

```bash
curl -X POST localhost:8000/api/simulate/insert-disc -H "Content-Type: application/json" -d '{"volume_label":"ARRESTED_DEVELOPMENT_S1D3","content_type":"tv","simulate_ripping":true}'
```

Then step the job to COMPLETED with `POST /api/simulate/step-job/{id}` (repeat until the
state is `completed`). Confirm the notification shows `ARRESTED_DEVELOPMENT_S1D3 (Disc 3)`
in the Disc field.

- [ ] **Step 4: Verify the review notification**

Drive a job into `REVIEW_NEEDED` (an ambiguous simulated disc, or set the state directly
in the DB and transition through the state machine). Confirm the notification is amber,
titled "Review Needed", carries the Reason field, pings the configured mention, and links
to `/history/{job_id}`.

- [ ] **Step 5: Stop the servers**

```powershell
Get-NetTCPConnection -LocalPort 8000,5173 -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique | ForEach-Object { Stop-Process -Id $_ -Force }
```

Orphaned `uvicorn` or `makemkvcon` processes cause duplicate jobs and drive conflicts.
