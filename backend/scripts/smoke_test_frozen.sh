#!/usr/bin/env bash
#
# smoke_test_frozen.sh - boot a PyInstaller-built engram bundle and assert it works.
#
# Shared by release.yml (every non-Windows build job) and ci.yml's frozen-build
# gate, so PR-time and release-time smoke assertions cannot drift apart. The
# Windows job keeps its own PowerShell translation of this logic.
#
# Why this exists at all: `pyinstaller` exiting 0 proves nothing about the
# bundle. Unresolved dynamic imports are reported as non-fatal WARNINGs, so a
# dependency bump can produce a green build whose binary dies on startup. Three
# releases (v0.29.0 - v0.31.0) shipped that way. Only booting the thing catches it.
#
# Usage:
#   backend/scripts/smoke_test_frozen.sh <exe-path> [--expect-lan-access]
#
# Options:
#   --expect-lan-access  additionally assert the build seeded allow_lan_access=True
#                        (the headless server variants).
#
# Environment:
#   PORT  port the executable listens on (default 8765). The caller exports this
#         to the executable too, so both sides must agree.
#
# Exit codes: 0 pass, 1 smoke failure, 2 usage error.

set -uo pipefail

EXE="${1:-}"
if [ -z "$EXE" ]; then
  echo "usage: $0 <exe-path> [--expect-lan-access]" >&2
  exit 2
fi
shift

EXPECT_LAN=0
while [ $# -gt 0 ]; do
  case "$1" in
    --expect-lan-access) EXPECT_LAN=1 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done

if [ ! -x "$EXE" ]; then
  echo "executable not found or not executable: $EXE" >&2
  exit 1
fi

PORT="${PORT:-8765}"
BASE="http://127.0.0.1:${PORT}"
echo "smoke testing $EXE against $BASE"

"$EXE" > smoke-stdout.log 2> smoke-stderr.log &
BACKEND_PID=$!
trap 'kill "$BACKEND_PID" 2>/dev/null || true' EXIT

dump_logs() {
  echo "===== stdout ====="
  cat smoke-stdout.log || true
  echo "===== stderr ====="
  cat smoke-stderr.log || true
}

ready=0
for _ in $(seq 1 30); do
  if curl -fsS --max-time 2 "${BASE}/api/jobs" > /dev/null; then
    ready=1
    break
  fi
  sleep 1
done
if [ "$ready" != "1" ]; then
  dump_logs
  echo "executable never became ready"
  exit 1
fi
echo "backend responded on /api/jobs"

# Fork-bomb regression guard: a healthy onedir server is ~1 process; a spawn
# fork-bomb produces 10+ within seconds, so >2 is a safe trigger. The pattern is
# derived from the path we launched, so it cannot drift from the real layout.
# Exclude our own PID: this script was invoked WITH the exe path as an argument,
# so `pgrep -f` matches this shell too. Counting it would silently eat the
# guard's headroom and make a genuine 2-process bundle look like a fork-bomb.
count=$(pgrep -f "$EXE" | grep -cvx "$$" | tr -d ' ')
echo "engram process count: $count"
if [ "$count" -gt 2 ]; then
  dump_logs
  echo "too many engram processes ($count) - possible multiprocessing fork-bomb"
  exit 1
fi

# fpcalc must be bundled and detected, the whole point of shipping it.
# Retry with a generous per-attempt timeout: the endpoint shells out to
# ffmpeg/fpcalc subprocesses and the first call can be slow on a cold runner; a
# retry after warmup returns fast. The gate stays strict, it still requires
# found=True, it just tolerates transient first-run latency.
fpcalc_found=""
for i in $(seq 1 4); do
  fpcalc_found=$(curl -fsS --max-time 15 "${BASE}/api/detect-tools" \
    | python3 -c "import sys, json; print(json.load(sys.stdin)['fpcalc']['found'])" 2>/dev/null) || true
  if [ "$fpcalc_found" = "True" ]; then break; fi
  if [ "$i" -lt 4 ]; then
    echo "detect-tools attempt $i: fpcalc_found='$fpcalc_found', retrying in 2s"
    sleep 2
  else
    echo "detect-tools attempt $i: fpcalc_found='$fpcalc_found', no more retries"
  fi
done
echo "fpcalc detected: $fpcalc_found"
if [ "$fpcalc_found" != "True" ]; then
  dump_logs
  echo "bundled fpcalc was not detected"
  exit 1
fi

if [ "$EXPECT_LAN" = "1" ]; then
  lan=$(curl -fsS --max-time 5 "${BASE}/api/config" \
    | python3 -c "import sys, json; print(json.load(sys.stdin)['allow_lan_access'])" 2>/dev/null) || true
  echo "allow_lan_access: $lan"
  if [ "$lan" != "True" ]; then
    dump_logs
    echo "headless build did not seed allow_lan_access=True"
    exit 1
  fi
fi

echo "smoke test passed"
