"""Unit tests for app.config helpers."""

import sys

from app.config import Settings, is_frozen


class TestIsFrozen:
    """is_frozen() must treat EITHER sys.frozen or sys._MEIPASS as authoritative.

    Regression guard for the false "dev mode" bug: a packaged build reached the
    bundled frontend (served off sys._MEIPASS) yet reported sys.frozen falsy, so
    the updater wrongly refused to self-update. Either signal now means frozen.
    """

    def test_false_when_neither_signal_present(self, monkeypatch):
        monkeypatch.delattr(sys, "frozen", raising=False)
        monkeypatch.delattr(sys, "_MEIPASS", raising=False)
        assert is_frozen() is False

    def test_true_when_sys_frozen_set(self, monkeypatch):
        monkeypatch.delattr(sys, "_MEIPASS", raising=False)
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        assert is_frozen() is True

    def test_true_when_meipass_set_but_frozen_unset(self, monkeypatch):
        # The exact divergence the user hit: UI served (=> _MEIPASS) but
        # sys.frozen falsy. Must still report frozen.
        monkeypatch.delattr(sys, "frozen", raising=False)
        monkeypatch.setattr(sys, "_MEIPASS", "/tmp/_MEIxxxx", raising=False)
        assert is_frozen() is True

    def test_true_when_both_set(self, monkeypatch):
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr(sys, "_MEIPASS", "/tmp/_MEIxxxx", raising=False)
        assert is_frozen() is True


class TestDbEcho:
    """SQLAlchemy echo must be controlled by db_echo, NOT debug.

    Regression guard for E2E flakiness: the E2E backend runs with DEBUG=true to
    expose the /api/simulate/* endpoints, but echoing every SQL statement during
    a simulated rip floods stdout and stalls the single-worker event loop,
    causing visibility-assertion timeouts. db_echo is decoupled from debug and
    defaults off. ``_env_file=None`` skips any local backend/.env so the test is
    deterministic regardless of the developer's environment.
    """

    def test_db_echo_defaults_false(self, monkeypatch):
        monkeypatch.delenv("DB_ECHO", raising=False)
        assert Settings(_env_file=None).db_echo is False

    def test_debug_true_does_not_enable_db_echo(self, monkeypatch):
        # The exact E2E configuration: DEBUG on, DB_ECHO unset. Echo must stay off.
        monkeypatch.setenv("DEBUG", "true")
        monkeypatch.delenv("DB_ECHO", raising=False)
        settings = Settings(_env_file=None)
        assert settings.debug is True
        assert settings.db_echo is False

    def test_db_echo_env_opt_in(self, monkeypatch):
        monkeypatch.setenv("DB_ECHO", "true")
        assert Settings(_env_file=None).db_echo is True
