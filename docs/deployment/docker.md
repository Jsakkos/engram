# Docker deployment (Linux)

Run Engram as a container on a Linux host with an optical drive. The image
serves the API and dashboard from a single process on port `8000`.

> **MakeMKV is not bundled.** It is proprietary and license-keyed, so the image
> compiles the official MakeMKV source into a persistent volume the first time
> the container starts. You supply your own license (or the free beta key).

## Requirements

- A Linux host with Docker and an optical drive (e.g. `/dev/sr0`).
- A MakeMKV license, or the current free beta key from the
  [MakeMKV forum](https://www.makemkv.com/forum/viewtopic.php?t=1053).
- A TMDB Read Access Token (entered later in the setup wizard).

> Docker Desktop on Windows/macOS (WSL2) **cannot** pass an optical drive into a
> container. You can run the dashboard there to try the UI, but ripping requires
> a native Linux host.

## Quick start

```bash
git clone https://github.com/Jsakkos/engram.git
cd engram
# Edit docker-compose.yml: set MAKEMKV_APP_KEY, PUID/PGID, TZ, and the drive path.
docker compose up -d
```

Open <http://localhost:8000> and complete the setup wizard. In the wizard set the
library and staging paths to the in-container mount points:

- Movies library → `/media/movies`
- TV library → `/media/tv`
- Staging → `/staging`

> **First start is slow.** The container compiles MakeMKV (a few minutes) and the
> first episode match downloads the speech-recognition model (~465 MB). Both are
> cached in the `config` volume and reused on every subsequent start.

## Using the published image

Instead of building locally, pull the prebuilt image:

```yaml
services:
  engram:
    image: ghcr.io/jsakkos/engram:latest
```

Tags: `latest`, `MAJOR.MINOR`, and exact versions (e.g. `0.7.1`) are published to
GHCR on each release.

## Configuration

### Environment variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `PUID` / `PGID` | `1000` | User/group the server runs as. Match your host user (`id -u` / `id -g`) so library files aren't root-owned. |
| `TZ` | `Etc/UTC` | Container timezone. |
| `MAKEMKV_APP_KEY` | _(empty)_ | MakeMKV license / beta key. Written to MakeMKV's `settings.conf` on start. Can also be set later in the wizard. |
| `MAKEMKV_VERSION` | `latest` | MakeMKV version to compile. Pin a number (e.g. `1.18.1`) for reproducibility. |
| `MAKEMKV_SKIP_INSTALL` | _(unset)_ | Set to `1` to skip the MakeMKV compile (used for CI/smoke tests; ripping is unavailable). |

The server also honors `DATABASE_URL`, `HOST`, `PORT`, and `DEBUG` — these are
preset in the image and rarely need changing.

### Volumes

| Mount | Holds |
|-------|-------|
| `/config` | Database, caches, logs, the compiled MakeMKV, and MakeMKV's `settings.conf`. **Back this up.** |
| `/media/movies`, `/media/tv` | Organized library output. |
| `/staging` | Work area for in-progress rips and the staging auto-import workflow. |

### Optical drive passthrough

Pass each drive through with `devices:`. Find yours with `ls -l /dev/sr*`:

```yaml
    devices:
      - "/dev/sr0:/dev/sr0"
```

The entrypoint detects the group that owns the device on the host and adds the
runtime user to it, but the shipped compose file also sets `privileged: true`:
plain `devices:` passthrough isn't always enough for full drive control
(eject/tray), and some hosts have gotten stuck unable to stop/restart the
container without it ([#459](https://github.com/Jsakkos/engram/issues/459)).

**Know what that costs:** `privileged: true` disables the container's
seccomp/AppArmor confinement and gives it access to *all* host devices, not
just the optical drive — a meaningful widening of the isolation boundary if
you're running other containers on the same host. If you'd rather not accept
that, try removing the line and using a narrower `cap_add`/`device_cgroup_rules`
grant instead; it just isn't what's verified against the drive-control issue
above.

## MakeMKV licensing

- The **free beta key** rotates roughly monthly. When ripping starts failing with
  a registration error, grab the new key from the forum and update
  `MAKEMKV_APP_KEY` (then `docker compose up -d`) or paste it into the wizard.
- A **purchased license** is stable and does not need refreshing.
- The key is stored in `/config/.MakeMKV/settings.conf`. You may bind-mount your
  own `settings.conf` there instead of using the env var.

## GPU acceleration

The image is CPU-only. Episode matching runs whisper with int8 quantization,
which is slower but needs no GPU. A CUDA image variant may be offered later.

## Troubleshooting

- **MakeMKV compile failed on first start** — check `docker compose logs`. The
  most common cause is a `MAKEMKV_VERSION` whose tarball is no longer on
  makemkv.com; pin a current version. You can also mount a host-installed
  `makemkvcon` and symlink it onto the container `PATH` as a fallback.
- **Ripping can't access the drive** — confirm the `devices:` path matches a real
  `/dev/sr*` and that the host can read the disc (`blkid /dev/sr0`).
- **Library files are root-owned** — set `PUID`/`PGID` to your host user.
- **The dashboard loads but nothing rips on Windows/macOS** — expected; optical
  passthrough is Linux-only.
