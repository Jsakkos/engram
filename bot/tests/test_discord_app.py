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
    bot.settings = NS(daily_budget_usd=10, max_turns_per_user_per_hour=99)
    bot.store = Store(":memory:")
    bot.store.add_thread(1, 7, "forum")
    bot._locks = discord_app.defaultdict(discord_app.asyncio.Lock)
    bot._latest_trigger = {}
    bot.filing_block = lambda user: ""
    bot.kb = NS(text="K")

    async def fake_history(thread, info):
        return [history.Item("user", "broken", "ann")], parsed

    async def fake_respond(conversation, context, knowledge):
        return TurnResult(text="draft", draft=draft)

    async def fake_deliver(thread, info, result):
        delivered.append(result)

    bot._history = fake_history
    bot.agent = NS(respond=fake_respond)
    bot._deliver = fake_deliver
    await bot.handle_turn(Thread(), bot.store.thread(1), NS(id=7, display_name="ann"))
    assert delivered[0].draft.diagnostics.startswith("<details>")
    assert delivered[0].draft.diagnostics_note == parsed.preview()
