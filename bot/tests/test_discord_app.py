from engram_bot.discord_app import DraftButton, chunk


def test_chunk_respects_limit_and_prefers_paragraphs():
    text = ("para one " * 50).strip() + "\n\n" + ("para two " * 300).strip()
    parts = chunk(text, limit=2000)
    assert all(len(p) <= 2000 for p in parts)
    assert parts[0].startswith("para one") and parts[0].endswith("para one")
    assert all(not p.startswith("ara") for p in parts)  # never split inside a word
    assert " ".join(parts).split() == text.split()


def test_button_custom_ids_round_trip():
    import re

    button = DraftButton("file", 17)
    match = re.fullmatch(DraftButton.__discord_ui_compiled_template__, button.custom_id)
    assert match and match["action"] == "file" and match["id"] == "17"
    assert DraftButton("file", 1, comment=True).item.label == "Add to issue"


async def test_failed_turn_still_replies(monkeypatch):
    from types import SimpleNamespace as NS

    from engram_bot import discord_app
    from engram_bot.agent import FALLBACK_TEXT
    from engram_bot.store import Store

    sent = []

    class Typing:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    class Thread:
        id = 1
        guild = NS(get_member=lambda _id: NS(display_name="ann", created_at=None))

        def typing(self):
            return Typing()

        async def send(self, text):
            sent.append(text)

    bot = discord_app.EngramBot.__new__(discord_app.EngramBot)
    bot.settings = NS(daily_budget_usd=10, max_turns_per_user_per_hour=99)
    bot.store = Store(":memory:")
    bot.store.add_thread(1, 7, "forum")
    bot._locks = discord_app.defaultdict(discord_app.asyncio.Lock)
    bot._latest_trigger = {}
    bot.filing_block = lambda user: ""

    async def boom(*a, **k):
        raise RuntimeError("discord fell over")

    bot._history = boom
    await bot.handle_turn(Thread(), bot.store.thread(1), NS(id=7, display_name="ann"))
    assert sent == [FALLBACK_TEXT]


class FakeAttachment:
    def __init__(self, id, filename, data, content_type="application/zip"):
        self.id = id
        self.filename = filename
        self.size = len(data)
        self.content_type = content_type
        self._data = data
        self.reads = 0

    async def read(self):
        self.reads += 1
        return self._data


def fake_bot():
    from types import SimpleNamespace as NS

    from engram_bot import discord_app
    from engram_bot.store import Store

    bot = discord_app.EngramBot.__new__(discord_app.EngramBot)
    bot.settings = NS(history_limit=40)
    bot.store = Store(":memory:")
    bot._attachment_cache = discord_app.OrderedDict()
    bot._bundle_cache = discord_app.OrderedDict()
    return bot


async def test_history_reads_bundles_and_keeps_the_reporters_latest(monkeypatch):
    from types import SimpleNamespace as NS

    import discord

    from engram_bot import discord_app
    from tests.test_diagnostics import bundle

    me = NS(id=999, mention="<@999>")
    monkeypatch.setattr(discord_app.EngramBot, "user", property(lambda self: me))
    bot = fake_bot()
    bot.store.add_thread(1, 7, "forum")
    reporter = NS(id=7, bot=False, display_name="ann")
    helper = NS(id=8, bot=False, display_name="bob")

    def msg(id, author, content, attachments=()):
        return NS(
            id=id,
            author=author,
            content=content,
            attachments=list(attachments),
            type=discord.MessageType.default,
        )

    old = FakeAttachment(10, "old.zip", bundle())
    newest = FakeAttachment(11, "engram-bug-report-job-42.zip", bundle())
    other = FakeAttachment(12, "theirs.zip", bundle())
    junk = FakeAttachment(13, "photos.zip", b"not a zip")
    messages = [
        msg(1, reporter, "it broke", [old]),
        msg(2, reporter, "newer bundle", [newest]),
        msg(3, helper, "mine too", [other, junk]),
    ]

    class Thread:
        id = 1

        def history(self, limit):
            async def gen():
                for m in reversed(messages):
                    yield m

            return gen()

    items, chosen = await bot._history(Thread(), bot.store.thread(1))
    assert chosen.filename == "engram-bug-report-job-42.zip"  # reporter's, not the helper's
    assert "Engram: 0.39.0" in items[1].text
    assert "photos.zip: not a readable Engram diagnostics bundle" in items[2].text

    await bot._history(Thread(), bot.store.thread(1))
    assert newest.reads == 1  # parsed once, then cached


async def test_turn_attaches_reporters_bundle_to_the_draft():
    from types import SimpleNamespace as NS

    from engram_bot import diagnostics, discord_app, history, intake
    from engram_bot.agent import TurnResult
    from engram_bot.store import Store
    from tests.test_diagnostics import bundle

    parsed = diagnostics.parse(bundle(), "job.zip")
    fields = {name: "" for name in intake._STRING_FIELDS}
    fields.update(kind="bug", title="T", summary="S", labels=[], duplicate_of=0)
    draft = intake.Draft.from_tool_input(fields, ())
    delivered = []

    class Typing:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    class Thread:
        id = 1
        guild = NS(get_member=lambda _id: NS(display_name="ann"))

        def typing(self):
            return Typing()

    bot = discord_app.EngramBot.__new__(discord_app.EngramBot)
    bot.settings = NS(daily_budget_usd=10, max_turns_per_user_per_hour=99, escalation_model="")
    bot.store = Store(":memory:")
    bot.store.add_thread(1, 7, "forum")
    bot._locks = discord_app.defaultdict(discord_app.asyncio.Lock)
    bot._latest_trigger = {}
    bot.filing_block = lambda user: ""
    bot.kb = NS(text="K")

    async def fake_history(thread, info):
        return [history.Item("user", "broken", "ann")], parsed

    async def fake_respond(conversation, context, knowledge, model=None):
        return TurnResult(text="draft", draft=draft)

    async def fake_deliver(thread, info, result):
        delivered.append(result)

    bot._history = fake_history
    bot.agent = NS(respond=fake_respond)
    bot._deliver = fake_deliver
    await bot.handle_turn(Thread(), bot.store.thread(1), NS(id=7, display_name="ann"))
    assert delivered[0].draft.diagnostics.startswith("<details>")
    assert delivered[0].draft.diagnostics_note == parsed.preview()


def bare_bot(**settings):
    from types import SimpleNamespace as NS

    from engram_bot import discord_app
    from engram_bot.store import Store

    bot = discord_app.EngramBot.__new__(discord_app.EngramBot)
    defaults = dict(
        escalation_model="",
        escalation_after_turns=6,
        maintainer_log_channel_id=None,
        digest_weekday=0,
        digest_hour_utc=9,
        github_repo="o/r",
        maintainer_role_id=None,
        max_turns_per_user_per_hour=99,
    )
    bot.settings = NS(**{**defaults, **settings})
    bot.store = Store(":memory:")
    return bot


def test_escalation_only_when_configured_and_warranted():
    convo = [{"role": "user", "content": "q"}] * 3
    assert bare_bot().turn_model(1, convo) is None
    bot = bare_bot(escalation_model="claude-sonnet-5-5", escalation_after_turns=3)
    assert bot.turn_model(1, convo) == "claude-sonnet-5-5"
    assert bot.turn_model(1, convo[:2]) is None
    bot.store.add_bot_message(100, 1, "claude-haiku-5-5")
    bot.store.set_feedback(100, 7, -1)
    assert bot.turn_model(1, convo[:1]) == "claude-sonnet-5-5"


async def test_reactions_on_bot_answers_become_feedback(monkeypatch):
    from types import SimpleNamespace as NS

    from engram_bot import discord_app

    monkeypatch.setattr(discord_app.EngramBot, "user", property(lambda self: NS(id=999)))
    bot = bare_bot()
    bot.store.add_bot_message(100, 1, "m")

    def ev(emoji, user=7, message=100):
        return NS(emoji=emoji, user_id=user, message_id=message)

    await bot.on_raw_reaction_add(ev("👎"))
    await bot.on_raw_reaction_add(ev("🎉"))  # not feedback
    await bot.on_raw_reaction_add(ev("👍", message=555))  # not a bot answer
    await bot.on_raw_reaction_add(ev("👍", user=999))  # the bot itself
    assert bot.store.thread_downvoted(1)
    await bot.on_raw_reaction_remove(ev("👎"))
    assert not bot.store.thread_downvoted(1)


def test_render_digest():
    import time

    from engram_bot.discord_app import render_digest
    from engram_bot.store import Store

    s = Store(":memory:")
    assert "A quiet week." in render_digest(s.digest(0), "o/r")
    s.add_thread(1, 7, "forum")
    d = s.add_draft(1, 7, {"title": "Skip fails"})
    s.claim_draft(d)
    s.finish_draft(d, 760, "https://gh/760")
    s.add_bot_message(100, 1, "m")
    s.set_feedback(100, 7, -1)
    text = render_digest(s.digest(time.time() - 60), "o/r", 42)
    assert "#760 Skip fails <https://gh/760>" in text
    assert "https://discord.com/channels/42/1/100" in text
    assert "A quiet week." not in text


async def test_digest_posts_weekly_at_the_configured_time(monkeypatch):
    from datetime import UTC, datetime, timedelta

    import discord

    sent = []

    class Channel(discord.abc.Messageable):
        guild = None

        async def _get_channel(self):
            return self

        async def send(self, text):
            sent.append(text)

    bot = bare_bot(maintainer_log_channel_id=5)
    bot.get_channel = lambda _id: Channel()
    monday_8 = datetime(2026, 10, 12, 8, 0, tzinfo=UTC)
    assert not await bot.maybe_post_digest(monday_8)  # before the hour
    assert await bot.maybe_post_digest(monday_8 + timedelta(hours=1))
    assert not await bot.maybe_post_digest(monday_8 + timedelta(hours=2))  # once a week
    assert not await bot.maybe_post_digest(monday_8 + timedelta(days=3))  # wrong weekday
    assert await bot.maybe_post_digest(monday_8 + timedelta(days=7, hours=1))
    assert len(sent) == 2 and "weekly digest" in sent[0]


class FakeResponse:
    def __init__(self):
        self.messages, self.modal, self.deferred = [], None, False

    async def send_message(self, text, ephemeral=False):
        self.messages.append(text)

    async def send_modal(self, modal):
        self.modal = modal

    async def defer(self, thinking=False):
        self.deferred = True


class FakeFollowup:
    def __init__(self):
        self.messages = []

    async def send(self, text):
        self.messages.append(text)


def interaction(user_id):
    from types import SimpleNamespace as NS

    return NS(
        user=NS(id=user_id, display_name="ann @claude"),
        channel=NS(jump_url="https://discord.com/channels/1/2"),
        response=FakeResponse(),
        followup=FakeFollowup(),
    )


async def test_reply_on_github_flow():
    from engram_bot.github import CommentRef

    bot = bare_bot()
    relay_id = bot.store.add_relay(500, 10, 2, 7, "Jsakkos", "https://gh/c/500")
    posted = []

    class GH:
        async def create_comment(self, number, body):
            posted.append((number, body))
            return CommentRef(id=900, url="https://gh/c/900")

    bot.gh = GH()

    stranger = interaction(8)
    await bot.on_reply_button(stranger, relay_id)
    assert stranger.response.modal is None and "Only the person" in stranger.response.messages[0]

    reporter = interaction(7)
    await bot.on_reply_button(reporter, relay_id)
    assert reporter.response.modal is not None

    bot.filing_block = lambda user: "their Discord account is too new"
    blocked = interaction(7)
    await bot.post_reply(blocked, relay_id, "spam")
    assert posted == [] and "too new" in blocked.response.messages[0]

    bot.filing_block = lambda user: "they've reached today's limit for new issues"
    await bot.post_reply(reporter, relay_id, "It's an LG WH16NS40 @someone")
    number, body = posted[0]
    assert number == 10
    assert "LG WH16NS40" in body and "@someone" not in body and "@claude" not in body
    assert "https://discord.com/channels/1/2" in body
    assert bot.store.is_own_comment(900)  # never relayed back
    assert "Posted your reply on #10" in reporter.followup.messages[0]


async def test_send_to_thread_outcomes():
    from types import SimpleNamespace as NS

    import discord

    from engram_bot import discord_app

    bot = bare_bot()
    sent = []

    class Thread(discord.Thread):
        def __init__(self):
            pass

        async def send(self, content, view=None):
            sent.append((content, view))
            return NS(id=321)

    bot.get_channel = lambda _id: Thread()
    assert await bot.send_to_thread(1, "hello", None) == 321
    assert await bot.send_to_thread(1, "reply?", 5) == 321
    assert sent[0][1] is None and isinstance(sent[1][1].children[0], discord_app.ReplyButton)

    async def gone(_id):
        raise discord.NotFound(NS(status=404, reason="nf"), "gone")

    bot.get_channel = lambda _id: None
    bot.fetch_channel = gone
    assert await bot.send_to_thread(1, "x", None) == 0  # don't retry a deleted thread
