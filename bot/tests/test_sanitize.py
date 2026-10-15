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


UNC_LINE = (
    "2026-10-14 | WARNING  | job=12 | matcher - no subtitles for "
    r"\\192.168.86.83\NAS Downloads\Dexter's Laboratory\Season 3\Disc 5\D2_t07.mkv"
)


def test_unc_share_is_redacted_but_the_path_is_kept():
    out = sanitize.redact(UNC_LINE)
    assert "192.168" not in out and "NAS Downloads" not in out
    assert r"\\<host>\<share>\Dexter's Laboratory\Season 3\Disc 5\D2_t07.mkv" in out


def test_unc_variants():
    cases = {
        r"\\nas\media\TV\x.mkv": r"\\<host>\<share>\TV\x.mkv",
        r"\\nas\media": r"\\<host>\<share>",
        "'\\\\\\\\nas\\\\media\\\\TV'": "'\\\\\\\\<host>\\\\<share>\\\\TV'",  # repr, doubled
        "//nas/media/TV/x.mkv": "//<host>/<share>/TV/x.mkv",
        "smb://nas.local/media/TV": "smb://<host>/<share>/TV",
    }
    for raw, expected in cases.items():
        assert sanitize.redact(raw) == expected, raw


def test_urls_are_not_mistaken_for_unc_paths():
    text = "see https://github.com/Jsakkos/engram/issues/1 and https://jsakkos.github.io/engram/"
    assert sanitize.redact(text) == text


def test_private_ips_are_redacted_public_ones_kept():
    text = (
        "dashboard http://192.168.1.50:8000/history 10.0.0.7 172.20.3.4 100.101.2.3 "
        "public 8.8.8.8 1.1.1.1 172.32.0.1 version 10.2.3"
    )
    out = sanitize.redact(text)
    assert "http://<lan-ip>:8000/history" in out
    for private in ("192.168.1.50", "10.0.0.7", "172.20.3.4", "100.101.2.3"):
        assert private not in out
    for public in ("8.8.8.8", "1.1.1.1", "172.32.0.1", "version 10.2.3"):
        assert public in out


def test_lan_details_never_reach_an_issue():
    # Both publishing paths: prose fields and fenced logs.
    for rendered in (sanitize.clean(UNC_LINE), sanitize.fence(UNC_LINE)):
        assert "192.168" not in rendered and "NAS Downloads" not in rendered


def test_windows_paths_in_reprs_are_not_mistaken_for_shares():
    text = r"'C:\\Program Files\\MakeMKV\\makemkvcon64.exe' and D:\Rips\Show\t01.mkv"
    assert sanitize.redact(text) == text
