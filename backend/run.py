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
import socket
import sys
import traceback


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


def main() -> None:
    try:
        import threading
        import webbrowser

        import uvicorn

        from app.main import app, settings

        is_frozen = getattr(sys, "frozen", False)
        port = _find_free_port(settings.host, settings.port) if is_frozen else settings.port

        if port != settings.port:
            print(f"Port {settings.port} in use, using {port} instead")

        if is_frozen:
            # Open browser after a short delay to let the server bind the port
            url = f"http://{settings.host}:{port}"
            threading.Timer(1.5, webbrowser.open, args=[url]).start()

        uvicorn.run(
            app,
            host=settings.host,
            port=port,
            # reload is incompatible with frozen PyInstaller bundles
            reload=False if is_frozen else settings.debug,
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
        if getattr(sys, "frozen", False):
            print(f"\nFatal error: {exc}")
            print("Check ~/.engram/engram.log for details")
            input("Press Enter to exit...")
        sys.exit(1)


if __name__ == "__main__":
    # Must run before any multiprocessing work and before main() so that
    # spawn-relaunched worker processes exit here instead of re-running startup.
    multiprocessing.freeze_support()
    main()
