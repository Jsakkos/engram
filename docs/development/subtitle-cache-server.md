# Subtitle cache: server deployment

The nightly subtitle-cache harvest runs on the Ubuntu host `pve-docker`
(`jsakkos@192.168.1.122`) under a systemd **user** timer, and publishes to the
rolling `subtitle-cache-latest` GitHub release.

## Why exactly one harvester

The OpenSubtitles daily download quota is **per account**, not per machine, and
each machine keeps its own `subtitle_coverage` table. Two harvesters race for
the same 1,000/day bucket, and the loser records zero-coverage rows for seasons
the winner already holds, skip-listing content that exists. That is the same
defect the 2026-08-31 harvester repair fixed, reintroduced across machines. The
laptop keeps its corpus as a backup but must not run `build_subtitle_cache.py`
again.

## Install

Before starting, make sure `gh auth login` has already been run for the
`jsakkos` account on this host. The harvest's preflight check fails fast if
it hasn't (see "Preflight failed" under Recovery, below), without touching
quota, but there is no reason to discover that at 02:00 with nobody watching
instead of now.

```bash
# 1. uv (absent by default on this host)
curl -LsSf https://astral.sh/uv/install.sh | sh
source ~/.local/bin/env
uv --version
```

The installer updates shell rc files for future shells but does not put `uv`
on `PATH` in the shell you just ran it in. If `uv --version` still fails after
sourcing `~/.local/bin/env`, open a new shell and try again.

```bash
# 2. Bring the checkout to current main
cd ~/engram && git fetch origin && git checkout main && git pull --ff-only
cd backend && uv sync --no-install-project
git -C ~/engram log -1 --oneline
```

Expected: a recent commit on `main`, not the stale `v0.8.1` tag the host
started on.

```bash
# 3. Secrets (YOU write this file; copy the template from the repo)
install -d -m 700 ~/.config/engram
install -m 600 ~/engram/deploy/subtitle-cache/engram-subtitle-cache.env.example \
  ~/.config/engram/subtitle-cache.env
${EDITOR:-nano} ~/.config/engram/subtitle-cache.env
```

Fill in `TMDB_API_KEY`, `OPENSUBTITLES_API_KEY`, `OPENSUBTITLES_USERNAME`, and
`OPENSUBTITLES_PASSWORD`. Use LF line endings and leave no trailing whitespace
after a value: `systemd`'s `EnvironmentFile` parser is `KEY=value` with no
shell quoting or expansion, and a stray `\r` from a file edited on Windows or
a trailing space is silently appended to the token or password. That surfaces
as an opaque auth failure at 02:00 with nobody watching; the file's own
comments cover verifying with `cat -A`. The file is never committed and its
contents are never pasted into a terminal transcript, an issue, or a chat
session.

Verify the file has the four keys and non-empty values, without printing the
values themselves:

```bash
awk -F= '/^[A-Z]/ {printf "%s len=%d\n", $1, length($2)}' ~/.config/engram/subtitle-cache.env
```

Expected: four lines, `TMDB_API_KEY`, `OPENSUBTITLES_API_KEY`,
`OPENSUBTITLES_USERNAME`, `OPENSUBTITLES_PASSWORD`, each with `len=` greater
than zero. A `TMDB_API_KEY len=` near 32 means the short v3 API key was
pasted instead of the long v4 Read Access Token.

```bash
# 4. Units
install -d -m 755 ~/.config/systemd/user
install -m 644 ~/engram/deploy/subtitle-cache/engram-subtitle-cache.service ~/.config/systemd/user/
install -m 644 ~/engram/deploy/subtitle-cache/engram-subtitle-cache.timer ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable engram-subtitle-cache.timer
systemctl --user list-unit-files | grep engram-subtitle-cache
```

Expected: `engram-subtitle-cache.timer` listed as `enabled`, and
`engram-subtitle-cache.service` listed as `static` (never `enabled`; see
below).

**Enable only the timer, never the service.** The `.service` unit
deliberately carries no `[Install]` section: `systemctl --user enable
engram-subtitle-cache.service` would wire the harvest to `default.target` and
fire it on every user-manager start (every login, every reboot), spending the
metered OpenSubtitles quota far more than once a day. Only the `.timer` has an
`[Install]` section, and it activates the service directly at its scheduled
time.

**This deliberately enables the timer without starting it (no `--now`).**
Enabling wires it to start at boot; it does not run it right now. Do not
start the timer yet: the cache on this host is still empty, and a timer that
fires against an empty `~/.engram/cache` re-harvests everything the laptop
already holds, burning weeks of OpenSubtitles quota on content that already
exists. Do the cutover below first, prove a supervised run publishes cleanly
from the moved corpus, and only then start the timer, in "Start the timer"
further down.

**Do not add `ProtectHome` when hardening this unit further.** It would
break the job outright: `harvest.sh` needs to read and write the checkout
(`~/engram`), `~/.engram/cache`, and `~/.engram/harvest`, and `gh` rewrites
`~/.config/gh` on token refresh, all of which live under `$HOME`. The
service file's own comments carry this same warning next to `ProtectSystem`.

**Lingering is required** and needs root:

```bash
sudo loginctl enable-linger jsakkos
```

Without it the user manager is torn down at logout and the timer never fires
while nobody is logged in. Verify with `loginctl show-user jsakkos -p Linger`,
which must print `Linger=yes`.

## Cutover from the laptop

Order matters, and it matters more than the mechanical order the install
commands happen to appear in above: the timer must not be started until the
corpus and its coverage database are in place. The coverage database is what
stops the server re-harvesting 36,000 episodes it already has on disk, so it
moves with the corpus, and only one machine may harvest at a time, so this is
also the point where the laptop stops running `build_subtitle_cache.py`.

`~/.engram/cache` does not exist yet on a fresh host. Create the directory
levels first: plain `rsync` creates only the final path component, not the
parents.

```bash
ssh jsakkos@192.168.1.122 'install -d -m 755 ~/.engram/cache/data'
```

```bash
# From the laptop (Git Bash), corpus first, then coverage:
rsync -av --partial --progress ~/.engram/cache/data/ jsakkos@192.168.1.122:~/.engram/cache/data/
rsync -av --partial --progress ~/.engram/cache/tmdb_cache.sqlite jsakkos@192.168.1.122:~/.engram/cache/
```

Verify the transfer landed completely before moving on:

```bash
# SRT file counts must match between laptop and server:
find ~/.engram/cache/data -name "*.srt" | wc -l
ssh jsakkos@192.168.1.122 'find ~/.engram/cache/data -name "*.srt" | wc -l'

# subtitle_coverage row counts must also match:
python3 -c "import sqlite3; c=sqlite3.connect('$HOME/.engram/cache/tmdb_cache.sqlite'); print(c.execute('select count(*) from subtitle_coverage').fetchone()[0])"
ssh jsakkos@192.168.1.122 'python3 -c "import sqlite3; c=sqlite3.connect(\"/home/jsakkos/.engram/cache/tmdb_cache.sqlite\"); print(c.execute(\"select count(*) from subtitle_coverage\").fetchone()[0])"'
```

Expected: both pairs of counts match. A materially lower count on the server
means an interrupted transfer; re-run the `rsync` (`--partial` makes it
resumable) rather than proceeding.

Then prove the server ships from disk at zero quota cost with a supervised
run, before starting the timer. See "First run" below.

## First run (supervised)

```bash
systemctl --user start engram-subtitle-cache.service
journalctl --user -u engram-subtitle-cache.service -f
```

What a healthy first run looks like:

- `preflight ok (uv, gh, flock, show list, ...KB free)`
- `OpenSubtitles API: ACTIVE` and a real remaining-quota number near 1000
- a long run of seasons shipping from disk without downloads
- `publish-guard: growth: ...` (or `within-tolerance`) with a show/episode
  count close to the published baseline. `within-tolerance` covers a candidate
  up to 2% below the baseline by design, so a small dip there is the guard
  working as intended, not a warning sign; only a `blocked` or `undecided`
  verdict needs attention.
- `verified release assets: ... bytes, ... bytes`
- either `harvest completed the full corpus` (exit 0) or, more commonly,
  `UPLOAD SUCCEEDED. The harvest stopped early only because it hit its
  download budget, which is the normal nightly outcome...` (exit 10)

## Start the timer

Only after the supervised first run above has published successfully (exit 0
or 10, both mean the release updated) does the timer take over the nightly
schedule:

```bash
systemctl --user start engram-subtitle-cache.timer
systemctl --user list-timers engram-subtitle-cache.timer
```

Expected: a `NEXT` column showing tomorrow at roughly 02:00 UTC, plus the
timer's randomized delay.

## Monitoring

| Question | Command |
|---|---|
| Did last night run, and did it fail? | `systemctl --user status engram-subtitle-cache.service` |
| Did it publish, and was it a full corpus or a quota halt? | `journalctl --user -u engram-subtitle-cache.service --since yesterday \| grep 'harvest:'` (see the exit-code note below: a quota halt reports as success with status 0) |
| When does it run next? | `systemctl --user list-timers engram-subtitle-cache.timer` |
| What happened? | `journalctl --user -u engram-subtitle-cache.service --since yesterday` |
| Is the release fresh? | `gh release view subtitle-cache-latest --repo Jsakkos/engram` |

## Reading the exit codes

The service's exit status is `harvest.sh`'s own exit code, not
`build_subtitle_cache.py`'s. Several of these codes mean the run published
successfully even though systemd would otherwise call the unit failed; that
is why the service unit carries `SuccessExitStatus=10` (see below).

**`systemctl` will not show you exit 10.** A code listed in
`SuccessExitStatus` is normalised away: systemd reports `Result=success` and
`ExecMainStatus=0`, so a quota-halted night and a full-corpus night look
identical in `systemctl --user status`. Verified on systemd 255. To tell them
apart, read the journal: a quota halt logs `harvest exit was 2` and the
`UPLOAD SUCCEEDED ... DO NOT re-run` banner, while a full corpus logs
`harvest completed the full corpus`. Every other code in the table is a real
failure and does show up in `systemctl --user status` as
`Result=exit-code` with the true `ExecMainStatus`.

| Exit | Meaning | Published? | Action |
|---|---|---|---|
| 0 | Harvest completed the full corpus; packed and published | Yes | None |
| 10 | Harvest halted on the OpenSubtitles quota guard partway through; packed from disk and published anyway | Yes, from disk | None. This is the **normal** nightly outcome when `ENGRAM_MAX_DOWNLOADS` sits under the account's daily cap. Do not re-run the harvest today: a second run burns the remaining quota and can race the timer's next run. `SuccessExitStatus=10` in the service unit tells systemd to treat this as a clean run, so a healthy quota-halted night does not show up as a failed unit and train the operator to ignore real failures. |
| 20 | The shrink guard blocked the upload | No; live release unchanged | See "The shrink guard blocked the upload" below |
| 21 | The shrink guard could not decide (couldn't reach GitHub, malformed manifest, untrustworthy published baseline) | No; live release unchanged | Same as a guard block: read the journal, fix the underlying problem, re-run manually |
| 1 | Everything else: preflight failure (missing `uv`/`gh`/`flock`, unauthenticated `gh`, missing show list, insufficient disk), lock already held, harvest process failure, pack failure, or an upload/verification failure after retries | Usually no; an upload failure after all retries can leave the release **inconsistent** | Read the journal; see "Recovery" below |
| 143 | Terminated by `SIGTERM` (systemd `TimeoutStartSec`, or a manual `systemctl --user stop`) | No | Check whether the timeout is too tight for a cold run, or whether the stop was intentional |

## Recovery

**A second run started while one was already in progress.** `harvest.sh`
takes an exclusive `flock` on `$WORK_DIR/.harvest.lock` before touching
anything metered. A run that finds the lock held logs `FATAL: another
harvest is already running (lock held); exiting without touching the quota`
and exits 1 immediately, without spending any quota or disturbing the
in-progress run. No action needed beyond confirming only one run is actually
active (`systemctl --user status engram-subtitle-cache.service`); if the
"in-progress" run is actually stuck, investigate that one, not the one that
backed off.

**The shrink guard blocked the upload.** The live release is untouched, which
is the point. Compare by hand:

```bash
cd ~/engram/backend
uv run python scripts/publish_guard.py --candidate ~/.engram/harvest/manifest.json
```

If the shrink is deliberate (a corpus pruning), re-run with `--allow-shrink`,
then upload manually:

```bash
gh release upload subtitle-cache-latest \
  ~/.engram/harvest/engram-subtitle-cache.tar.gz \
  ~/.engram/harvest/manifest.json \
  --clobber --repo Jsakkos/engram
```

If it is not deliberate, do not override it. Find out why the corpus lost
content first.

**An upload failed every retry, or post-upload verification failed.**
`harvest.sh` retries `gh release upload` up to `ENGRAM_UPLOAD_ATTEMPTS`
(default 3) times with backoff, then verifies the two uploaded assets by
re-fetching the release's asset list and comparing sizes against the local
files. Either failure logs the same warning:

```
release subtitle-cache-latest may now be INCONSISTENT (missing asset, or
tarball and manifest.json from different builds); re-upload
/home/jsakkos/.engram/harvest/engram-subtitle-cache.tar.gz and
/home/jsakkos/.engram/harvest/manifest.json by hand
```

This means the release can be left with a missing asset, or with a tarball
and `manifest.json` from different builds (a client compares their embedded
checksums and silently discards the download if they disagree, so installs
quietly stop updating with only a debug-level client log). Re-upload both
files by hand with the exact paths the script names in the log:

```bash
gh release upload subtitle-cache-latest \
  ~/.engram/harvest/engram-subtitle-cache.tar.gz \
  ~/.engram/harvest/manifest.json \
  --clobber --repo Jsakkos/engram
```

Then confirm both assets are present and the right size:

```bash
gh release view subtitle-cache-latest --repo Jsakkos/engram --json assets --jq '.assets[] | "\(.name) \(.size)"'
```

**Preflight failed before any quota was touched.** Every publishing
prerequisite is checked up front so a broken environment never costs a
metered harvest. The journal line names the exact problem:

- `FATAL: required tool 'uv' not found on PATH; install uv to ~/.local/bin and
  check the unit's PATH=` (also raised for `gh` and `flock`, with the fix
  named in the same line)
- `FATAL: gh is not authenticated for github.com (exit N); run 'gh auth login'
  as this user` (run `gh auth login` as `jsakkos`, not as the operator's own
  account)
- `FATAL: show list not found at <path>` (checkout is missing or
  `ENGRAM_SHOW_LIST` points somewhere wrong)
- `FATAL: only <N>KB free on <dir>'s filesystem, need <N>KB` (free up space;
  the default floor is 1 GiB, roughly twice the artifact)

None of these touch the quota; fix the named problem and either wait for the
next scheduled run or start the service manually.

**Quota exhausted every night.** Check the true remaining quota; the login
response's `allowed_downloads` is the daily CAP, not the remainder. A run that
whose end-of-run summary reports `OS quota left:` as `0 downloads today` has
nothing to do but wait ~24h.

**Rolling back a bad publish.** There is no history on a rolling release asset.
Re-pack from a known-good corpus and `--clobber` over it.
