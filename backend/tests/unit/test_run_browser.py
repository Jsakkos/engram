import os
import threading
import webbrowser

import run  # backend/run.py (pytest runs from backend/)


class _FakeTimer:
    """Captures (interval, fn) and runs fn immediately on .start()."""

    last = None

    def __init__(self, interval, fn, args=None, kwargs=None):
        self.interval, self.fn = interval, fn
        self.args, self.kwargs = args or [], kwargs or {}
        _FakeTimer.last = self

    def start(self):
        self.fn(*self.args, **self.kwargs)


def test_headless_main_skips_schedule_browser_open(monkeypatch):
    """main() must not call _schedule_browser_open when ENGRAM_HEADLESS=1."""
    scheduled = []
    monkeypatch.setenv("ENGRAM_HEADLESS", "1")
    monkeypatch.setattr(run, "_schedule_browser_open", lambda url, **kw: scheduled.append(url))
    monkeypatch.setattr("app.config.is_frozen", lambda: True)
    monkeypatch.setattr("app.core.network.resolve_startup_host", lambda *a, **kw: "127.0.0.1")
    import uvicorn

    monkeypatch.setattr(uvicorn, "run", lambda *a, **kw: None)
    run.main()
    assert scheduled == [], "headless build must not open a browser"


def test_normal_launch_opens_tab(monkeypatch):
    opened = []
    monkeypatch.setattr(threading, "Timer", _FakeTimer)
    monkeypatch.setattr(webbrowser, "open", lambda url: opened.append(url))
    run._schedule_browser_open("http://localhost:8000", updated=False)
    assert opened == ["http://localhost:8000"]


def test_updated_relaunch_suppresses_tab_when_client_connects(monkeypatch):
    opened = []
    monkeypatch.setattr(threading, "Timer", _FakeTimer)
    monkeypatch.setattr(webbrowser, "open", lambda url: opened.append(url))
    from app.api.websocket import manager

    monkeypatch.setattr(manager, "active_connections", ["client"])  # tab reconnected
    run._schedule_browser_open("http://localhost:8000", updated=True)
    assert opened == []  # existing tab reconnected -> no new tab


def test_updated_relaunch_opens_tab_when_no_client(monkeypatch):
    opened = []
    monkeypatch.setattr(threading, "Timer", _FakeTimer)
    monkeypatch.setattr(webbrowser, "open", lambda url: opened.append(url))
    from app.api.websocket import manager

    monkeypatch.setattr(manager, "active_connections", [])  # old tab gone / port changed
    run._schedule_browser_open("http://localhost:8000", updated=True)
    assert opened == ["http://localhost:8000"]  # safeguard opened one


# #791: every process Engram starts inherits its working directory. A frozen Windows
# build launched from Explorer (or relaunched by the update helper) has the install dir
# as its cwd, so the browser webbrowser.open starts keeps that dir open after Engram
# exits, and the update helper's `move install -> .old` fails on every update.


def test_release_install_dir_moves_cwd_to_home_on_frozen_windows(monkeypatch):
    moved = []
    monkeypatch.setattr(run.sys, "platform", "win32")
    monkeypatch.setattr(run.os, "chdir", moved.append)
    run._release_install_dir(frozen=True)
    assert moved == [os.path.expanduser("~")]


def test_release_install_dir_is_a_noop_in_dev(monkeypatch):
    """Dev mode resolves ./engram.db against the cwd, so it must keep it."""
    moved = []
    monkeypatch.setattr(run.sys, "platform", "win32")
    monkeypatch.setattr(run.os, "chdir", moved.append)
    run._release_install_dir(frozen=False)
    assert moved == []


def test_release_install_dir_is_a_noop_off_windows(monkeypatch):
    moved = []
    monkeypatch.setattr(run.sys, "platform", "linux")
    monkeypatch.setattr(run.os, "chdir", moved.append)
    run._release_install_dir(frozen=True)
    assert moved == []


def test_release_install_dir_swallows_chdir_errors(monkeypatch):
    def boom(path):
        raise OSError("gone")

    monkeypatch.setattr(run.sys, "platform", "win32")
    monkeypatch.setattr(run.os, "chdir", boom)
    run._release_install_dir(frozen=True)  # must not raise


def test_main_releases_install_dir_before_opening_the_browser(monkeypatch):
    """The cwd must move after settings load (a .env beside engram.exe is read from the
    cwd at import) and before anything is launched (the browser, then uvicorn)."""
    calls = []
    monkeypatch.delenv("ENGRAM_HEADLESS", raising=False)
    monkeypatch.setattr(run, "_release_install_dir", lambda frozen: calls.append(("cwd", frozen)))
    monkeypatch.setattr(run, "_schedule_browser_open", lambda url, **kw: calls.append("browser"))
    monkeypatch.setattr("app.config.is_frozen", lambda: True)
    monkeypatch.setattr("app.core.network.resolve_startup_host", lambda *a, **kw: "127.0.0.1")
    import uvicorn

    monkeypatch.setattr(uvicorn, "run", lambda *a, **kw: calls.append("uvicorn"))
    run.main()
    assert calls == [("cwd", True), "browser", "uvicorn"]
