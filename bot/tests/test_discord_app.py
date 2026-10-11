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
