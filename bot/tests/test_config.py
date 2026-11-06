import pytest

from engram_bot.config import Settings

BASE = {"DISCORD_TOKEN": "d", "ANTHROPIC_API_KEY": "a"}


def env(monkeypatch, **values):
    for name in (
        "GITHUB_TOKEN",
        "GITHUB_APP_ID",
        "GITHUB_APP_PRIVATE_KEY",
        "GITHUB_APP_PRIVATE_KEY_FILE",
        "BOT_ALLOWED_LABELS",
    ):
        monkeypatch.delenv(name, raising=False)
    for k, v in {**BASE, **values}.items():
        monkeypatch.setenv(k, v)


def test_token_setup_ignores_a_missing_key_mount(monkeypatch, tmp_path):
    env(monkeypatch, GITHUB_TOKEN="t", GITHUB_APP_PRIVATE_KEY_FILE=str(tmp_path / "absent.pem"))
    s = Settings.from_env()
    assert s.github_token == "t" and s.github_app_private_key is None
    assert s.repo_owner == "Jsakkos" and s.repo_name == "engram"
    assert s.model == "claude-haiku-5-5"


def test_app_key_is_read_from_file(monkeypatch, tmp_path):
    key = tmp_path / "app.pem"
    key.write_text("PEM")
    env(monkeypatch, GITHUB_APP_ID="1", GITHUB_APP_PRIVATE_KEY_FILE=str(key))
    assert Settings.from_env().github_app_private_key == "PEM"


def test_missing_credentials_fail_fast(monkeypatch):
    env(monkeypatch)
    with pytest.raises(SystemExit):
        Settings.from_env()


def test_label_list_override(monkeypatch):
    env(monkeypatch, GITHUB_TOKEN="t", BOT_ALLOWED_LABELS="bug, ux,")
    assert Settings.from_env().allowed_labels == ("bug", "ux")


def test_docs_repo_is_separate_from_the_issue_repo(monkeypatch):
    env(monkeypatch, GITHUB_TOKEN="t", GITHUB_REPO="me/engram-bot-test")
    s = Settings.from_env()
    assert (s.github_repo, s.docs_repo) == ("me/engram-bot-test", "Jsakkos/engram")
    monkeypatch.setenv("BOT_DOCS_REPO", "me/engram-fork")
    assert Settings.from_env().docs_repo == "me/engram-fork"
