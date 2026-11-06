"""GitHub → Discord follow-through for issues the bot filed or commented on.

Polls GitHub rather than taking webhooks, so the bot works on a home server with no public
URL. Each poll costs three requests: issues changed since the last poll, comments changed
since the last poll, and the latest release.

What it posts in the originating thread:
- the issue closing (completed / not planned / duplicate) or reopening;
- a maintainer's comment on the issue, with a **Reply on GitHub** button;
- a new release that should contain the fix for an issue closed as completed.

Discord-free: the bot hands in a ``send`` callable, so this is testable on its own.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

from .github import GitHub, GitHubError
from .store import Store

log = logging.getLogger(__name__)

MAINTAINER_ASSOCIATIONS = frozenset({"OWNER", "MEMBER", "COLLABORATOR"})
RELAY_CHARS = 1500
# Poll windows overlap by this much so a clock difference can't skip an update; the state
# comparison and the relay table make the overlap harmless.
OVERLAP = timedelta(minutes=2)

# send(thread_id, content, relay_id) -> posted message id, or None if it couldn't be posted.
# A relay_id asks for a "Reply on GitHub" button bound to that relay.
Send = Callable[[int, str, int | None], Awaitable[int | None]]


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def state_message(number: int, title: str, url: str, state: str, reason: str | None) -> str:
    ref = f"[#{number}](<{url}>) ({title[:120]})"
    if state == "open":
        return f"🔁 {ref} was reopened."
    if reason == "completed":
        return (
            f"✅ {ref} was closed as completed. Fixes usually ship in the next release; "
            "I'll post here when it's out."
        )
    if reason == "duplicate":
        return f"🔗 {ref} was closed as a duplicate of another issue. The issue links to it."
    if reason == "not_planned":
        return f"{ref} was closed as not planned. The issue explains why."
    return f"{ref} was closed."


def relay_message(number: int, author: str, body: str, url: str) -> str:
    excerpt = body.strip()
    if len(excerpt) > RELAY_CHARS:
        excerpt = excerpt[:RELAY_CHARS].rstrip() + " …"
    quoted = "\n".join(f"> {line}" if line else ">" for line in excerpt.splitlines())
    return (
        f"💬 **{author}** commented on [#{number}](<{url}>):\n{quoted}\n"
        "Press **Reply on GitHub** to answer there, or reply here and I'll help you put an "
        "answer together."
    )


def release_message(number: int, tag: str, url: str) -> str:
    return f"🎉 Engram **{tag}** is out and should include the fix for #{number}: <{url}>"


class FollowUp:
    def __init__(
        self, github: GitHub, store: Store, send: Send, releases: GitHub | None = None
    ) -> None:
        """``github`` is the repo issues are filed in; ``releases`` is the repo whose
        releases ship the fixes (Engram itself, BOT_DOCS_REPO). They differ when the bot
        files into a test repo; "Engram vX is out" always means the app's release."""
        self._gh = github
        self._releases = releases or github
        self._store = store
        self._send = send

    async def poll(self, now: datetime | None = None) -> None:
        now = now or datetime.now(UTC)
        since_raw = self._store.get_meta("poll_since")
        if since_raw is None:
            # First run: start from now rather than replaying the repo's history.
            self._store.set_meta("poll_since", _iso(now))
            await self._check_release(first_run=True)
            return
        since = _iso(_parse(since_raw) - OVERLAP)
        followed = self._store.followed_issues()
        try:
            if followed:
                await self._check_issues(since, followed)
                await self._check_comments(since, followed)
        except GitHubError as e:
            # Leave the cursor where it was so the next poll covers this window again.
            log.warning("follow-through poll failed: %s", e)
            return
        self._store.set_meta("poll_since", _iso(now))
        # Release notices don't depend on the cursor, so a failure here (e.g. a token that
        # can't read releases) must not hold back issue and comment notices.
        try:
            await self._check_release()
        except GitHubError as e:
            log.warning("release check failed: %s", e)

    async def _check_issues(self, since: str, followed: set[int]) -> None:
        for change in await self._gh.issues_updated_since(since):
            if change.number not in followed:
                continue
            # Store and compare the same normalized pair: GitHub reports a reopened issue as
            # ("open", "reopened"), but an open follow is stored with no reason. Comparing the
            # raw pair re-posted "was reopened" on every later poll that saw the issue.
            reason = change.state_reason if change.state == "closed" else None
            for f in self._store.follows_for(change.number):
                if (f.state, f.state_reason) == (change.state, reason):
                    continue
                text = state_message(change.number, change.title, change.url, change.state, reason)
                if await self._send(f.thread_id, text, None) is None:
                    continue  # try again next poll
                self._store.set_follow_state(f.id, change.state, reason, change.closed_at)

    async def _check_comments(self, since: str, followed: set[int]) -> None:
        for c in await self._gh.comments_since(since):
            if c.issue_number not in followed or c.author_is_bot:
                continue
            if c.association not in MAINTAINER_ASSOCIATIONS:
                continue
            if self._store.is_own_comment(c.id):
                continue
            created = _parse(c.created_at).timestamp()
            for f in self._store.follows_for(c.issue_number):
                if created < f.filed_at:
                    continue  # older discussion, not news to this reporter
                relay_id = self._store.add_relay(
                    c.id, c.issue_number, f.thread_id, f.reporter_id, c.author, c.url
                )
                if relay_id is None:
                    continue  # already relayed (comment edited, or overlapping window)
                text = relay_message(c.issue_number, c.author, c.body, c.url)
                if await self._send(f.thread_id, text, relay_id) is None:
                    self._store.remove_relay(relay_id)

    async def _check_release(self, first_run: bool = False) -> None:
        release = await self._releases.latest_release()
        if release is None:
            return
        tag = release["tag"]
        self._store.set_meta("release_tag", tag)  # informational only
        if first_run or not release.get("published_at"):
            return
        published = _parse(release["published_at"])
        # Every unannounced follow closed as completed before the latest release was cut gets
        # its notice. Nothing else gates this, so a failed post is simply retried next poll.
        for f in self._store.follows_awaiting_release():
            if not f.closed_at or _parse(f.closed_at) > published:
                continue  # closed after this release was cut; it'll be in the next one
            text = release_message(f.issue_number, tag, release["url"])
            if await self._send(f.thread_id, text, None) is not None:
                self._store.set_follow_release(f.id, tag)
