"""Engram entry point for PyInstaller frozen builds.

Wraps the entire import and startup sequence in error handling so that
any crash — including module-level import errors — keeps the console
window open with a visible traceback.

The startup runs under an ``if __name__ == "__main__"`` guard with
``multiprocessing.freeze_support()`` called first. In a frozen build the
multiprocessing ``spawn`` start method (the default on macOS and Windows)
relaunches *this same executable* for every worker process. Without the
guard each relaunch would re-run the whole startup — re-binding the port,
re-opening the browser, and spawning yet more workers — a fork-bomb that
opens an endless stream of browser tabs until the machine gives out.
``freeze_support()`` intercepts those relaunches and exits before reaching
``main()``.
"""

import multiprocessing
import os
import socket
import sys
import traceback


def _selftest() -> int:
    """Verify the bundled runtime can complete a TLS handshake (`engram --selftest`).

    Catches the packaging defect where certifi's ``cacert.pem`` is absent, which makes
    ``ssl.create_default_context()`` raise ``FileNotFoundError`` and kills every HTTPS
    call. Returns 0 on success. A missing/unloadable CA bundle is a build defect
    (non-zero); a transient network error is tolerated (0) so CI never flakes on it.
    """
    import ssl

    import certifi

    ca = certifi.where()
    if not os.path.exists(ca):
        print(f"SELFTEST FAIL: CA bundle not bundled at {ca}")
        return 2
    try:
        import httpx

        httpx.get("https://api.github.com", timeout=10.0)
        print("SELFTEST OK: HTTPS request succeeded with bundled CA bundle")
        return 0
    except Exception as exc:  # noqa: BLE001 — classify, then re-decide exit code
        # A missing/unloadable CA bundle surfaces as FileNotFoundError or ssl.SSLError
        # (directly or as the __cause__/__context__). That's a build defect -> fail.
        # Pure connect/timeout errors are tolerated so CI isn't flaky.
        chain = [exc, exc.__cause__, exc.__context__]
        if any(isinstance(e, (FileNotFoundError, ssl.SSLError)) for e in chain if e):
            print(f"SELFTEST FAIL: TLS/CA error: {exc!r}")
            return 3
        print(f"SELFTEST WARN: tolerated network error: {exc!r}")
        return 0


def _find_free_port(host: str, preferred: int, max_attempts: int = 20) -> int:
    """Return *preferred* if available, otherwise the next free port."""
    for offset in range(max_attempts):
        port = preferred + offset
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind((host, port))
                return port
        except OSError:
            continue
    # Last resort: let the OS pick
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((host, 0))
        return s.getsockname()[1]


def _schedule_browser_open(url: str, *, updated: bool) -> None:
    """Open the dashboard tab after startup — unless this is an update relaunch.

    Normal launch: open after 1.5s. Update relaunch (--updated): the existing tab reconnects on
    its own, so suppress the new tab; as a safeguard open one after 5s only if no WebSocket
    client has connected (the old tab was closed, or _find_free_port picked a different port)."""
    import threading
    import webbrowser

    if not updated:
        threading.Timer(1.5, webbrowser.open, args=[url]).start()
        return

    def _open_if_no_client() -> None:
        from app.api.websocket import manager

        if not manager.active_connections:
            webbrowser.open(url)

    threading.Timer(5.0, _open_if_no_client).start()


def main() -> None:
    try:
        import uvicorn
        from loguru import logger

        from app import __version__
        from app.config import is_frozen
        from app.core.network import ALL_INTERFACES, resolve_startup_host
        from app.main import app, settings

        frozen = is_frozen()
        logger.info(
            f"Starting engram {__version__} — is_frozen()={frozen} "
            f"(sys.frozen={getattr(sys, 'frozen', False)!r}, "
            f"_MEIPASS={'set' if hasattr(sys, '_MEIPASS') else 'unset'})"
        )
        host = resolve_startup_host(settings.host)
        port = _find_free_port(host, settings.port) if frozen else settings.port

        if port != settings.port:
            print(f"Port {settings.port} in use, using {port} instead")

        # Record what we actually bound so the dashboard can report the LAN URL.
        app.state.bound_host = host
        app.state.bound_port = port

        if frozen:
            # Open browser after a short delay to let the server bind the port.
            # When bound to all interfaces, open localhost (http://0.0.0.0 is not
            # a valid client address); LAN clients use the address shown in the UI.
            browser_host = "localhost" if host == ALL_INTERFACES else host
            url = f"http://{browser_host}:{port}"
            _schedule_browser_open(url, updated="--updated" in sys.argv[1:])

        uvicorn.run(
            app,
            host=host,
            port=port,
            # reload is incompatible with frozen PyInstaller bundles
            reload=False if frozen else settings.debug,
            factory=False,
        )
    except KeyboardInterrupt:
        pass  # Normal Ctrl+C shutdown
    except SystemExit as exc:
        sys.exit(exc.code)  # Preserve original exit code
    except Exception as exc:
        # KeyboardInterrupt / SystemExit are handled above; everything else
        # (including module-level import errors) lands here so a frozen build
        # keeps the console open with a visible traceback instead of vanishing.
        traceback.print_exc()
        # Inline the frozen check (not app.config.is_frozen) so the console still
        # pauses even when the crash was app.config failing to import.
        if getattr(sys, "frozen", False) or hasattr(sys, "_MEIPASS"):
            print(f"\nFatal error: {exc}")
            print("Check ~/.engram/engram.log for details")
            input("Press Enter to exit...")
        sys.exit(1)


if __name__ == "__main__":
    # Must run before any multiprocessing work and before main() so that
    # spawn-relaunched worker processes exit here instead of re-running startup.
    multiprocessing.freeze_support()
    if "--selftest" in sys.argv[1:]:
        # CI build guard: prove the frozen bundle can do TLS, then exit without
        # starting the server.
        sys.exit(_selftest())
    main()
