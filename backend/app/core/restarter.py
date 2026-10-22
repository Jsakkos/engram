"""Plain backend restart for settings that only apply at startup (#722).

Distinct from the updater's swap-and-relaunch: nothing is replaced, the same
executable is simply started again once this process has released its port.
Only packaged (frozen) builds can do that for themselves. A source run has no
supervisor to bring it back, and Docker relies on the container's restart
policy, so both get instructions instead of a button.

``RESTART_REQUIRED_FIELDS`` is the one place that says which settings are read
only at startup. ``PUT /api/config`` reports the ones a save changed, so the UI
carries no list of its own that could drift from the backend.
"""

import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath

from app.config import is_frozen
from app.core.updater import update_checker

logger = logging.getLogger(__name__)

# Settings consumed once, in startup code, with no per-operation re-read.
# Verify against the reader before adding: a field the backend re-reads per
# operation must NOT be listed (a needless prompt trains users to ignore it).
RESTART_REQUIRED_FIELDS: dict[str, str] = {
    # job_manager.start(): ASR device resolved once, match semaphore sized once.
    "enable_gpu_acceleration": "GPU acceleration",
    "max_concurrent_matches": "Concurrent matches",
    # job_manager.start(): the timed-cleanup and watchdog tasks are only created
    # when enabled at startup.
    "staging_cleanup_policy": "Staging cleanup",
    "staging_cleanup_days": "Staging cleanup age",
    "watchdog_enabled": "Stuck-job watchdog",
    # run.py: the bind host is chosen once.
    "allow_lan_access": "LAN access",
}


def changed_restart_fields(before: object, update: dict) -> list[str]:
    """Names of restart-required fields in ``update`` whose value differs from ``before``."""
    return [
        name
        for name in RESTART_REQUIRED_FIELDS
        if name in update and getattr(before, name, None) != update[name]
    ]


@dataclass(frozen=True)
class RestartSupport:
    mode: str  # "packaged" | "docker" | "source"
    can_restart: bool
    hint: str


def restart_support() -> RestartSupport:
    if is_frozen():
        return RestartSupport("packaged", True, "")
    if os.path.exists("/.dockerenv"):
        return RestartSupport(
            "docker",
            False,
            "Restart the container (for example: docker restart engram) to apply this.",
        )
    return RestartSupport("source", False, "Stop and start the backend (uvicorn) to apply this.")


def render_restart_bat(*, exe: str, args: list[str], pid: int, log_path: str) -> str:
    """Windows helper: wait for ``pid`` to exit, then start ``exe`` again (pure, testable).

    Waits with ``ping`` (``timeout`` aborts without a console) and filters
    ``tasklist`` on the PID *and* image name so a reused PID cannot wedge it.
    Bounded at about 15s; on timeout it starts anyway, and the new process picks
    the next free port rather than leaving the user with nothing running.
    """
    arg_str = " ".join(f'"{a}"' for a in args)
    image = PureWindowsPath(exe).name
    return "\r\n".join(
        [
            "@echo off",
            "setlocal enableextensions",
            'cd /d "%TEMP%"',
            "set /a TRIES=0",
            f'echo [engram-restart] waiting for pid {pid} > "{log_path}"',
            ":wait",
            f'tasklist /FI "PID eq {pid}" /FI "IMAGENAME eq {image}" 2>nul | find "{pid}" >nul',
            "if errorlevel 1 goto launch",
            "set /a TRIES+=1",
            "if %TRIES% GEQ 15 goto launch",
            "ping -n 2 127.0.0.1 >nul",
            "goto wait",
            ":launch",
            f'echo [engram-restart] launching {exe} >> "{log_path}"',
            f'start "" "{exe}" {arg_str}'.rstrip(),
            "",
        ]
    )


def restart_now() -> None:
    """Relaunch this packaged build. Does not return on success.

    Callers must have checked ``restart_support().can_restart`` and that no job
    is active; this only does the process swap. ``--updated`` keeps the relaunch
    from opening a second browser tab (the open one reconnects on its own).
    """
    args = [a for a in sys.argv[1:] if a != "--updated"] + ["--updated"]
    if sys.platform == "win32":
        import tempfile

        log_path = str(Path.home() / ".engram" / "restart_helper.log")
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)
        bat = Path(tempfile.gettempdir()) / "engram_restart.bat"
        with open(bat, "w", newline="") as f:
            f.write(
                render_restart_bat(
                    exe=sys.executable, args=args, pid=os.getpid(), log_path=log_path
                )
            )
        logger.info("Restarting via helper %s", bat)
        update_checker._spawn_detached_helper(["cmd", "/c", str(bat)])
        os._exit(0)  # Hard exit: avoids asyncio catching SystemExit
    logger.info("Restarting via exec")
    os.execv(sys.executable, [sys.executable, *args])
