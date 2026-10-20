"""The whole bot, end to end, with only the outside world faked.

Real: EngramBot's routing and buttons, Agent's tool loop, FollowUp, Store, the GitHub
client (talking to an in-memory GitHub over httpx.MockTransport), intake rendering,
diagnostics parsing and sanitizing. Fake: Discord's gateway objects and Claude's replies.

The story: a user posts in the help forum with a diagnostics bundle; the bot searches,
drafts, and the user files it; the maintainer asks a question on GitHub, which reaches the
thread and is answered through the Reply button; the issue is closed and then released;
the user's 👎 lands in the weekly digest.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace as NS

import discord
import httpx
import pytest

from engram_bot import discord_app, intake
from engram_bot.agent import Agent, Prices
from engram_bot.config import Settings
from engram_bot.discord_app import EngramBot, render_digest
from engram_bot.github import GitHub
from engram_bot.store import Store
from tests.test_agent import FakeMessages, reply, text, tool
from tests.test_diagnostics import bundle

FORUM_ID = 55


def iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


REPO = "Jsakkos/engram"


class FakeGitHubServer:
    """Just enough of the REST API, with state, to exercise the bot's calls."""

    def __init__(self) -> None:
        self.issues: dict[int, dict] = {
            754: self._issue(754, "Skipping full-disc track fails with HTTP 400", "2026-10-01"),
        }
        self.comments: list[dict] = []
        self.release = {
            "tag_name": "v0.39.0",
            "name": "v0.39.0",
            "published_at": "2026-10-09T00:00:00Z",
            "html_url": "https://github.com/x/releases/v0.39.0",
        }
        self.searches: list[str] = []
        self.next_number = 800
        self.next_comment = 9000
        self.clock = iso(datetime.now(UTC))

    def _issue(self, number: int, title: str, updated: str, body: str = "", labels=()):
        return {
            "number": number,
            "title": title,
            "state": "open",
            "state_reason": None,
            "closed_at": None,
            "html_url": f"https://github.com/{REPO}/issues/{number}",
            "labels": [{"name": n} for n in labels],
            "body": body,
            "comments": 0,
            "updated_at": updated,
        }

    def comment(self, number: int, body: str, author="Jsakkos", assoc="OWNER", created=None):
        self.next_comment += 1
        c = {
            "id": self.next_comment,
            "issue_url": f"https://api.github.com/repos/{REPO}/issues/{number}",
            "user": {"login": author, "type": "User"},
            "author_association": assoc,
            "body": body,
            "html_url": f"https://github.com/{REPO}/issues/{number}#c{self.next_comment}",
            "created_at": created or self.clock,
            "updated_at": created or self.clock,
        }
        self.comments.append(c)
        self.issues[number]["updated_at"] = c["updated_at"]
        return c

    def handle(self, request: httpx.Request) -> httpx.Response:
        path, method = request.url.path, request.method
        base = f"/repos/{REPO}"
        if path == "/search/issues":
            self.searches.append(request.url.params["q"])
            return httpx.Response(200, json={"items": [self.issues[754]]})
        if path == f"{base}/labels" and method == "GET":
            names = ["bug", "enhancement", "documentation", "ux", "from-discord", "needs-triage"]
            return httpx.Response(200, json=[{"name": n} for n in names])
        if path == f"{base}/labels" and method == "POST":
            return httpx.Response(422, json={"message": "already_exists"})
        if path == f"{base}/issues" and method == "POST":
            data = json.loads(request.content)
            self.next_number += 1
            issue = self._issue(
                self.next_number, data["title"], self.clock, data["body"], data["labels"]
            )
            self.issues[issue["number"]] = issue
            return httpx.Response(201, json=issue)
        if path == f"{base}/issues" and method == "GET":
            since = request.url.params["since"]
            changed = [i for i in self.issues.values() if i["updated_at"] >= since]
            return httpx.Response(200, json=changed)
        if path == f"{base}/issues/comments":
            since = request.url.params["since"]
            return httpx.Response(200, json=[c for c in self.comments if c["updated_at"] >= since])
        if m := re.fullmatch(rf"{base}/issues/(\d+)/comments", path):
            c = self.comment(int(m[1]), json.loads(request.content)["body"], author="bot[bot]")
            c["user"]["type"] = "Bot"
            return httpx.Response(201, json=c)
        if m := re.fullmatch(rf"{base}/issues/(\d+)", path):
            issue = self.issues.get(int(m[1]))
            return httpx.Response(200, json=issue) if issue else httpx.Response(404, json={})
        if path == f"{base}/releases/latest":
            return httpx.Response(200, json=self.release)
        return httpx.Response(404, json={"message": f"unhandled {method} {path}"})


class FakeMessage:
    def __init__(self, id, author, content="", embed=None, view=None, attachments=()):
        self.id = id
        self.author = author
        self.content = content or ""
        self.embeds = [embed] if embed else []
        self.view = view
        self.attachments = list(attachments)
        self.type = discord.MessageType.default
        self.mentions: list = []
        self.guild = NS(id=1)

    async def edit(self, embed=None, view=None):
        self.embeds = [embed] if embed else []
        self.view = view


class Typing:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakeThread(discord.Thread):
    """A forum post. Only the attributes the bot touches."""

    def __init__(self, bot_user, reporter):  # noqa: D107 - deliberately skips Thread.__init__
        self.id = 4242
        self.parent_id = FORUM_ID
        self.owner_id = reporter.id
        self._bot_user = bot_user
        self._members = {reporter.id: reporter}
        self.messages: list[FakeMessage] = []
        self._next = 5000

    @property
    def parent(self):
        return None

    @property
    def guild(self):
        return NS(id=1, get_member=self._members.get)

    @property
    def jump_url(self):
        return f"https://discord.com/channels/1/{self.id}"

    def post(self, author, content, attachments=()) -> FakeMessage:
        msg_id = self.id if not self.messages else self._next_id()
        message = FakeMessage(msg_id, author, content, attachments=attachments)
        message.channel = self
        self.messages.append(message)
        return message

    def _next_id(self) -> int:
        self._next += 1
        return self._next

    async def send(self, content=None, embed=None, view=None, **_):
        message = FakeMessage(self._next_id(), self._bot_user, content, embed, view)
        self.messages.append(message)
        return message

    def history(self, limit=100):
        async def newest_first():
            for m in reversed(self.messages[-limit:]):
                yield m

        return newest_first()

    async def fetch_message(self, message_id):
        return next(m for m in self.messages if m.id == message_id)

    def typing(self):
        return Typing()

    def bot_texts(self) -> list[str]:
        return [m.content for m in self.messages if m.author is self._bot_user and m.content]


class FakeAttachment:
    def __init__(self, id, filename, data):
        self.id, self.filename, self.size = id, filename, len(data)
        self.content_type = "application/zip"
        self._data = data

    async def read(self):
        return self._data


class Response:
    def __init__(self):
        self.sent, self.modal = [], None

    async def defer(self, **_):
        pass

    async def send_message(self, text, **_):
        self.sent.append(text)

    async def send_modal(self, modal):
        self.modal = modal

    async def edit_message(self, **_):
        pass


class Followup:
    def __init__(self):
        self.sent = []

    async def send(self, text, **_):
        self.sent.append(text)


def press(user, thread, message=None):
    return NS(
        user=user,
        guild=thread.guild,
        channel=thread,
        message=message,
        response=Response(),
        followup=Followup(),
    )


def draft_input(**over):
    data = {name: "" for name in intake._STRING_FIELDS}
    data.update(
        kind="bug",
        title="Matching fails with 'no subtitles' on a TV disc",
        summary="Every track on SHOW_S1D1 fails to match. @claude please fix",
        steps_to_reproduce="1. Insert disc\n2. Wait for matching",
        expected_behavior="Episodes are matched",
        actual_behavior="All tracks fail",
        engram_version="0.39.0",
        os="Windows 11",
        labels=["ux", "invented-label"],
        duplicate_of=0,
        include_diagnostics=True,
    )
    data.update(over)
    return data


@pytest.fixture
def world(monkeypatch):
    monkeypatch.setattr(discord_app, "DEBOUNCE_SECONDS", 0)
    bot_user = NS(id=999, mention="<@999>", display_name="Engram Bot")
    monkeypatch.setattr(EngramBot, "user", property(lambda self: bot_user))
    reporter = NS(
        id=7,
        bot=False,
        display_name="ann",
        created_at=datetime(2020, 1, 1, tzinfo=UTC),
        roles=[],
    )
    server = FakeGitHubServer()
    gh = GitHub(REPO, token="t", transport=httpx.MockTransport(server.handle))
    claude = FakeMessages([])
    settings = Settings(discord_token="x", help_forum_id=FORUM_ID, maintainer_name="Jonathan")
    agent = Agent(
        NS(messages=claude),
        gh,
        model="claude-haiku-5-5",
        effort="medium",
        max_tokens=8000,
        system_prompt="SYSTEM",
        allowed_labels=settings.allowed_labels,
        prices=Prices(0.10, 0.50, 0.01, 0.20),
    )
    store = Store(":memory:")
    bot = EngramBot(settings, store, agent, NS(text="DOCS"), gh)
    thread = FakeThread(bot_user, reporter)
    bot.get_channel = lambda channel_id: thread if channel_id == thread.id else None
    return NS(bot=bot, server=server, claude=claude, store=store, thread=thread, user=reporter)


async def test_forum_post_to_filed_issue_to_release(world):
    bot, server, claude, store, thread, ann = (
        world.bot,
        world.server,
        world.claude,
        world.store,
        world.thread,
        world.user,
    )
    await bot._prepare_labels()

    # 1. A forum post with a diagnostics bundle. Claude searches, then drafts.
    claude.responses += [
        reply(tool("search_issues", {"query": "matching no subtitles"}), stop="tool_use"),
        reply(
            text("Thanks, the bundle shows the matcher found no subtitles. Here's a draft."),
            tool("draft_issue", draft_input(), id="t2"),
            stop="tool_use",
        ),
    ]
    zip_file = FakeAttachment(1, "engram-bug-report-job-42.zip", bundle())
    post = thread.post(ann, "Matching fails on every track", attachments=[zip_file])
    await bot.on_message(post)

    assert server.searches == [f"repo:{REPO} is:issue matching no subtitles"]
    # The model saw the parsed bundle, not the raw zip.
    first_user_turn = claude.calls[0]["messages"][0]["content"]
    assert "Engram: 0.39.0" in first_user_turn and "no subtitles for S01E01" in first_user_turn
    draft_msg = thread.messages[-1]
    assert draft_msg.embeds and draft_msg.view is not None
    assert "Diagnostics" in [f.name for f in draft_msg.embeds[0].fields]

    # 2. The reporter presses File issue.
    (draft_id,) = [int(c.custom_id.rsplit(":", 1)[1]) for c in draft_msg.view.children[:1]]
    click = press(ann, thread, draft_msg)
    await bot.on_draft_button(click, "file", draft_id)
    assert click.followup.sent[0].startswith("Filed as #801")
    issue = server.issues[801]
    assert {lb["name"] for lb in issue["labels"]} == {"bug", "ux", "from-discord", "needs-triage"}
    assert "@claude" not in issue["body"]
    assert "Diagnostics bundle (engram-bug-report-job-42.zip)" in issue["body"]
    assert "https://discord.com/channels/1/4242" in issue["body"]
    assert draft_msg.view is None  # buttons removed after filing

    # A second click can't file twice.
    again = press(ann, thread, draft_msg)
    await bot.on_draft_button(again, "file", draft_id)
    assert "already filed" in again.response.sent[0]
    assert len([n for n in server.issues if n > 800]) == 1

    # 3. The follow-through loop's first run only sets its baseline.
    # The timeline starts now: follows are stamped with the real clock when filed, so a
    # fixed date would make the maintainer's comment look older than the report.
    t0 = datetime.now(UTC).replace(microsecond=0) + timedelta(minutes=1)
    before = len(thread.messages)
    await bot.followup.poll(t0)
    assert len(thread.messages) == before

    # 4. The maintainer asks a question on GitHub; it reaches the thread with a Reply button.
    server.clock = iso(t0 + timedelta(minutes=5))
    server.comment(801, "Which optical drive is this?")
    await bot.followup.poll(t0 + timedelta(minutes=10))
    relay_msg = thread.messages[-1]
    assert "Jsakkos" in relay_msg.content and "Which optical drive" in relay_msg.content
    (button,) = relay_msg.view.children
    relay_id = int(button.custom_id.rsplit(":", 1)[1])

    # 5. The reporter answers through the button; it lands on GitHub, credited and sanitized.
    click = press(ann, thread)
    await bot.on_reply_button(click, relay_id)
    assert click.response.modal is not None
    server.clock = iso(t0 + timedelta(minutes=15))
    await bot.post_reply(click, relay_id, "An LG WH16NS40, @everyone")
    answer = server.comments[-1]
    assert "LG WH16NS40" in answer["body"] and "ann" in answer["body"]
    assert "@everyone" not in answer["body"]
    # ...and the next poll doesn't echo the bot's own comment back into Discord.
    count = len(thread.messages)
    await bot.followup.poll(t0 + timedelta(minutes=20))
    assert len(thread.messages) == count

    # 6. Closed as completed, then a release ships.
    server.issues[801].update(
        state="closed",
        state_reason="completed",
        closed_at=iso(t0 + timedelta(hours=1)),
        updated_at=iso(t0 + timedelta(hours=1)),
    )
    await bot.followup.poll(t0 + timedelta(hours=1, minutes=5))
    assert "closed as completed" in thread.bot_texts()[-1]
    server.release = {
        "tag_name": "v0.40.0",
        "name": "v0.40.0",
        "published_at": iso(t0 + timedelta(hours=12)),
        "html_url": "https://github.com/x/releases/v0.40.0",
    }
    await bot.followup.poll(t0 + timedelta(days=1))
    assert "v0.40.0" in thread.bot_texts()[-1] and "#801" in thread.bot_texts()[-1]
    await bot.followup.poll(t0 + timedelta(days=1, minutes=10))
    assert sum("v0.40.0" in t for t in thread.bot_texts()) == 1

    # 7. A 👎 on the bot's answer shows up in the weekly digest.
    answer_msg = next(m for m in thread.messages if m.content.startswith("Thanks, the bundle"))
    await bot.on_raw_reaction_add(NS(emoji="👎", user_id=ann.id, message_id=answer_msg.id))
    digest = render_digest(store.digest(0), REPO, 1)
    assert "#801 Matching fails" in digest
    assert f"https://discord.com/channels/1/4242/{answer_msg.id}" in digest


async def test_duplicate_report_becomes_a_comment_and_is_followed(world):
    bot, server, claude, thread, ann = (
        world.bot,
        world.server,
        world.claude,
        world.thread,
        world.user,
    )
    claude.responses += [
        reply(
            text("This looks like #754."),
            tool("draft_issue", draft_input(duplicate_of=754)),
            stop="tool_use",
        )
    ]
    await bot.on_message(thread.post(ann, "Skipping a track gives HTTP 400"))
    draft_msg = thread.messages[-1]
    assert draft_msg.view.children[0].item.label == "Add to issue"
    draft_id = int(draft_msg.view.children[0].custom_id.rsplit(":", 1)[1])
    click = press(ann, thread, draft_msg)
    await bot.on_draft_button(click, "file", draft_id)
    assert click.followup.sent[0].startswith("Added your report to #754")
    assert server.comments[-1]["body"].startswith("Another report of this from Discord.")
    assert 754 in world.store.followed_issues()
    assert world.store.is_own_comment(server.comments[-1]["id"])


async def test_someone_else_cannot_file_the_reporters_draft(world):
    bot, claude, thread, ann = world.bot, world.claude, world.thread, world.user
    claude.responses += [reply(text("Draft:"), tool("draft_issue", draft_input()), stop="tool_use")]
    await bot.on_message(thread.post(ann, "bug"))
    draft_msg = thread.messages[-1]
    draft_id = int(draft_msg.view.children[0].custom_id.rsplit(":", 1)[1])
    stranger = NS(id=8, bot=False, display_name="eve", roles=[])
    click = press(stranger, thread, draft_msg)
    await bot.on_draft_button(click, "file", draft_id)
    assert "Only the person who reported this" in click.response.sent[0]
    assert all(n <= 800 for n in world.server.issues)
