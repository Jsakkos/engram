"""Unit tests for app.config helpers."""

import sys

from app.config import is_frozen


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
