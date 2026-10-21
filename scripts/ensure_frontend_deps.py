"""Run `npm ci` in frontend/ only when package-lock.json changed since the last install.

Called from the Procfile before the Vite dev server starts.
"""

import filecmp
import shutil
import subprocess
import sys
from pathlib import Path

FRONTEND = Path(__file__).resolve().parent.parent / "frontend"
LOCKFILE = FRONTEND / "package-lock.json"
# Inside node_modules so deleting node_modules also forces a reinstall.
STAMP = FRONTEND / "node_modules" / ".engram-lock-stamp"


def main() -> int:
    if not STAMP.exists():
        print("frontend: no record of a previous install, running npm ci", flush=True)
    elif not filecmp.cmp(LOCKFILE, STAMP, shallow=False):
        print("frontend: package-lock.json changed since last install, running npm ci", flush=True)
    else:
        return 0

    npm = shutil.which("npm")
    if npm is None:
        print("frontend: npm not found on PATH", file=sys.stderr)
        return 1

    result = subprocess.run([npm, "ci"], cwd=FRONTEND)
    if result.returncode != 0:
        return result.returncode

    shutil.copyfile(LOCKFILE, STAMP)
    return 0


if __name__ == "__main__":
    sys.exit(main())
