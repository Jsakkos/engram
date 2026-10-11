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


def test_redacts_secret_shapes():
    text = (
        "hook https://discord.com/api/webhooks/1/abc key=SUPERSECRET1 "
        "T-" + "a" * 60 + " sk-ant-abcdefghijkl ghp_" + "b" * 30 + " /home/bob/x "
        "token: eyJhbGciOiJIUzI1NiJ9.payload.sig monkey=banana"
    )
    out = sanitize.redact(text)
    for secret in ("webhooks/1", "SUPERSECRET1", "T-aaa", "sk-ant-abc", "ghp_bbb", "bob", "eyJ"):
        assert secret not in out
    assert "monkey=banana" in out  # \b keeps ordinary words alone


def test_published_text_is_redacted():
    assert "SUPERSECRET1" not in sanitize.clean("my key=SUPERSECRET1")
    assert "SUPERSECRET1" not in sanitize.fence("my key=SUPERSECRET1")


def test_mentions_in_code_spans_and_brackets_are_defused_too():
    for text in ("`@claude` fix", "(@claude)", "**@claude**", "x `@Jsakkos`"):
        assert "@claude" not in sanitize.clean(text) and "@Jsakkos" not in sanitize.clean(text)
