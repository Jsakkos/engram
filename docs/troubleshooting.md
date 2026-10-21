# Troubleshooting

Common setup and runtime issues, and how to resolve them. If something here doesn't cover your problem, ask **Trace**, the support bot in the [Engram Discord](https://discord.gg/G8fjjGswdc) (post in the `#ask-engram` forum, mention `@Trace`, or run `/bug`), or open an [issue](https://github.com/Jsakkos/engram/issues). A [diagnostics bundle](#diagnostics-bundle) makes it much easier to help. See [Getting Help & Reporting Issues](getting-help.md).

## FFmpeg not detected (Windows)

Engram needs FFmpeg for episode matching (it extracts and decodes the audio that speech recognition transcribes). If the Config Wizard's **Tools** step shows *"FFmpeg not found"* even though you installed it, it's almost always because FFmpeg isn't on your `PATH` — **not** because of the version. Engram doesn't care which FFmpeg version you have; it only needs to be able to run it.

Work through these in order:

**1. Confirm FFmpeg actually runs.** Open a **new** PowerShell or Command Prompt window and run:

```powershell
ffmpeg -version
```

- If you see version output, FFmpeg is on your `PATH` — skip to step 2.
- If you see *"'ffmpeg' is not recognized…"*, FFmpeg is installed but not on your `PATH`. Go to step 3.

**2. Restart Engram.** A running program keeps the `PATH` it was launched with. If you installed FFmpeg (or edited your `PATH`) while Engram was open, **fully close and reopen Engram** so it sees the change, then click **Re-scan** in the Config Wizard.

**3. Put FFmpeg where Engram can find it.** Pick whichever is easiest:

- **Install with winget** (recommended) and restart Engram:

  ```powershell
  winget install Gyan.FFmpeg
  ```

- **Add it to your PATH:** extract your FFmpeg download, then add the `...\bin` folder (the one containing `ffmpeg.exe`) to your system `PATH` via *Settings → Edit the system environment variables → Environment Variables*. Restart Engram afterward.

- **Drop it in a scanned location:** copy `ffmpeg.exe` to `C:\ffmpeg\bin\ffmpeg.exe`. Engram scans this path (and the Chocolatey / scoop / winget install locations) automatically — no `PATH` change needed.

- **Point Engram at it directly:** in the Config Wizard (or **Settings → Tools**), click **Override path manually** and enter the **full path to `ffmpeg.exe`** — for example `C:\Users\You\Downloads\ffmpeg\bin\ffmpeg.exe`. Enter the path to the **`.exe` file itself**, not the folder it lives in. The path is validated immediately and shows the detected version on success.

See [Installing FFmpeg](getting-started/installation.md#installing-ffmpeg) for download links.

### FFmpeg on Linux / macOS

Install it with your package manager (`sudo apt install ffmpeg`, `sudo dnf install ffmpeg`, or `brew install ffmpeg`). See the [Linux / macOS setup guide](guide/linux-setup.md#ffmpeg-not-found) for details.

## MakeMKV not detected

MakeMKV is required for disc ripping. If the Config Wizard can't find it, install it from [makemkv.com](https://www.makemkv.com/) and, if it still isn't detected, use **Override path manually** to point at `makemkvcon64.exe` (typically under `C:\Program Files (x86)\MakeMKV\`). On Linux, see [Linux / macOS setup](guide/linux-setup.md). Don't forget to enter your MakeMKV license key in the wizard.

## TMDB token not working

The TMDB field expects a **Read Access Token** (v4 auth) — the long string starting with `eyJ…` — not the shorter v3 "API Key". The wizard validates it as you type. See [Configuration](getting-started/configuration.md) for where to find it.

## Discord notifications aren't arriving

Engram posts to a Discord webhook when a disc completes, fails, or parks waiting for you.
Settings live under **Settings, Preferences, Notifications**.

**First, prove the webhook works.** Click **Send test message**. It tests the webhook you
have saved, so if you just pasted a new URL, save before testing. A sample notification
should appear in the channel within a second or two. If it doesn't:

- Read the message next to the button. A red cross means Discord refused the post, which
  usually means the webhook or its channel was deleted: re-create it under **Channel
  Settings, Integrations, Webhooks** and paste the fresh URL. An amber warning means the
  check itself could not run, so the webhook may well be fine and the problem is between
  your browser and Engram.
- Check that the URL starts with `https://discord.com/api/webhooks/`.

**The test works but real discs are silent.** Check the three per-event toggles. Each
event has its own switch, so it's possible to have completions enabled and review
notifications turned off, or the reverse.

**Notifications arrive but don't alert my phone.** Discord does not push notifications for
embed content. Set **Review Mention** to your user ID (`<@123456789012345678>`), a role
(`<@&987654321098765432>`), or `@here`. That value is sent as the message body, which is
the part Discord actually pings on. To find your user ID, enable **Developer Mode** in
Discord's Advanced settings, then right-click your name and choose **Copy User ID**.

**Notifications aren't clickable.** Set **Dashboard URL** to the address you use to reach
Engram from other devices, for example `http://192.168.1.50:5173`. Notifications then link
straight to the job. Leave it blank and notifications still arrive, just without a link.

**Every disc in my box set looks the same.** Each notification carries a **Disc** field
built from the disc's volume label plus its disc number, so `THE_WIRE_S1 (Disc 3)` is
distinct from disc 4. If the field shows only `Disc 1`, the disc reported no volume label
and Engram has no better identity to use; naming the disc during identification fixes it
for that job.

## A track says its only audio is another language

Engram identifies TV episodes by listening to the dialogue and comparing it with English
subtitles. When every audio track in a ripped file is another language (an anime disc
without an English dub, or a foreign-language bonus episode), Engram does not guess: the
track goes to review with a note naming the language, and you assign the episode by hand.

**The note says the disc also has English audio.** MakeMKV only rips the audio tracks its
language settings select, so an English dub on the disc can be missing from the file. In
MakeMKV, open **Preferences > Language** (or adjust the default track selection), add
English, and re-rip the track.

On a disc with several audio tracks, Engram always listens to the English one, whichever
track the disc marks as the default.

## Restoring the database after an upgrade

Engram's database (`engram.db`) holds your settings and API keys (MakeMKV key, TMDB token, AI and OpenSubtitles keys) along with your job history. Each time Engram starts on a new version, or finds that the database's layout needs updating, it first saves a copy of the database next to it, named `engram.db.bak-<previous-version>-<timestamp>` (for example `engram.db.bak-0.41.0-20261020T153012.123456Z`). The first upgrade to a version with this feature names the copy `bak-unknown`, because no earlier version was recorded. Only the three newest copies are kept. The startup log says when a copy is made: `Backed up database to ... before migrating`.

Where the database lives:

| Install | Database |
|---------|----------|
| Packaged app (Windows, Linux, macOS) | `~/.engram/engram.db` (on Windows, `C:\Users\<you>\.engram\engram.db`) |
| Docker | `/config/.engram/engram.db` inside the `/config` volume |
| From source | `backend/engram.db` (or wherever `DATABASE_URL` points) |

If an upgrade leaves Engram unable to start, or your settings look wrong afterwards, restore the copy:

1. Stop Engram completely (quit the app, or stop your Engram container, for example `docker compose stop engram`).
2. In the database folder, move `engram.db` aside, along with `engram.db-wal` and `engram.db-shm` if they exist. Keep them until you're sure the restore worked.
3. Copy the backup you want (usually the newest) to `engram.db`.
4. Delete `engram.db.version` so the next start treats the database as coming from the older version.
5. Start the version of Engram that made the backup (the version is in its name). Starting a newer version will try the same upgrade again, so if the upgrade itself is the problem, stay on the older version and [report it](getting-help.md) with the startup log.

If a backup can't be written (for example, the disk is full), Engram logs a warning and starts anyway.

## Diagnostics bundle

When reporting a problem, attach a diagnostics bundle so the logs and environment come with it. From the dashboard, open a job's detail view and download its diagnostic `.zip`, or fetch the overall report from `GET /api/diagnostics/report`. Secrets (API keys, tokens) are redacted automatically.

In Discord, drop the `.zip` into your thread with Trace: it reads the versions, job state and log errors for you, and attaches a summary to the GitHub issue when your report is filed. See [Attaching a diagnostics bundle](getting-help.md#attaching-a-diagnostics-bundle).
