# Troubleshooting

Common setup and runtime issues, and how to resolve them. If something here doesn't cover your problem, ask on [Discord](https://discord.gg/G8fjjGswdc) or open an [issue](https://github.com/Jsakkos/engram/issues) — a [diagnostics bundle](#diagnostics-bundle) makes it much easier to help.

## FFmpeg not detected (Windows)

Engram needs FFmpeg for episode matching (audio fingerprinting and the speech-recognition fallback). If the Config Wizard's **Tools** step shows *"FFmpeg not found"* even though you installed it, it's almost always because FFmpeg isn't on your `PATH` — **not** because of the version. Engram doesn't care which FFmpeg version you have; it only needs to be able to run it.

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

## Diagnostics bundle

When reporting a problem, attach a diagnostics bundle so the logs and environment come with it. From the dashboard, open a job's detail view and download its diagnostic `.zip`, or fetch the overall report from `GET /api/diagnostics/report`. Secrets (API keys, tokens) are redacted automatically.
