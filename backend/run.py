"""Engram entry point for PyInstaller frozen builds.

Wraps the entire import and startup sequence in error handling so that
any crash — including module-level import errors — keeps the console
window open with a visible traceback.
"""

import sys
import traceback

try:
    import threading
    import webbrowser

    import uvicorn

    from app.main import app, settings

    is_frozen = getattr(sys, "frozen", False)

    if is_frozen:
        # Open browser after a short delay to let the server bind the port
        url = f"http://{settings.host}:{settings.port}"
        threading.Timer(1.5, webbrowser.open, args=[url]).start()

    uvicorn.run(
        app,
        host=settings.host,
        port=settings.port,
        # reload is incompatible with frozen PyInstaller bundles
        reload=False if is_frozen else settings.debug,
        factory=False,
    )
except KeyboardInterrupt:
    pass  # Normal Ctrl+C shutdown
except SystemExit as exc:
    sys.exit(exc.code)  # Preserve original exit code
except BaseException as exc:
    traceback.print_exc()
    if getattr(sys, "frozen", False):
        print(f"\nFatal error: {exc}")
        print("Check ~/.engram/engram.log for details")
        input("Press Enter to exit...")
    sys.exit(1)
