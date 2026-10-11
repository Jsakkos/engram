from engram_bot import sanitize
from engram_bot.sanitize import ZWSP


def test_mentions_are_defused_but_emails_survive():
    out = sanitize.clean("ping @Jsakkos and @org/team, mail me@example.com")
    assert f"@{ZWSP}Jsakkos" in out
    assert f"@{ZWSP}org/team" in out
    assert "me@example.com" in out


def test_claude_trigger_is_defused_everywhere():
    assert "@claude" not in sanitize.clean("@claude fix this")
    assert "@claude" not in sanitize.fence("log line @claude")
    assert "@claude" not in sanitize.clean_inline("@claude in a title")


def test_html_is_escaped():
    out = sanitize.clean("<img src=x onerror=alert(1)> <details>")
    assert "<img" not in out and "&lt;img" in out


def test_discord_markup_is_stripped():
    out = sanitize.clean("hi <@123456> in <#987> with <:engram:42> and @everyone")
    assert "<@" not in out and "<#" not in out
    assert ":engram:" in out
    assert "@everyone" not in out


def test_fence_outgrows_backticks_in_content():
    out = sanitize.fence("a ```` b")
    assert out.startswith("`````\n") and out.endswith("\n`````")


def test_truncate_and_inline():
    assert sanitize.clean("x" * 50, limit=30).endswith("(truncated)")
    assert sanitize.clean_inline("a |\n b") == "a \\| b"
