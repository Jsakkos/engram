# Follow-up: Authenticode code-signing for Windows builds

**Date:** 2026-06-03
**Status:** Proposed (not implemented — requires purchasing a certificate)
**Related:** the atomic Windows update-swap fix (`backend/app/core/updater.py` `_restart_windows` / `_render_update_bat`)

## Why this is on the radar

`engram.exe` ships **unsigned** (`Get-AuthenticodeSignature` → `NotSigned`). End users run it as an
unsigned PyInstaller onedir build, frequently straight out of their `Downloads` folder. That combination
produces real friction and was a contributing factor in the "restart to update keeps breaking" reports:

- **SmartScreen "Windows protected your PC"** on first launch of each new download — users must click
  *More info → Run anyway*. Re-downloading on every failed update (we saw `engram-windows-x64(8)`) means
  hitting this repeatedly.
- **Defender heuristic scrutiny.** Unsigned, non-reputation binaries that write/execute native `.pyd`/`.dll`
  files get heavier on-access scanning and can be held with transient file locks just after a process exits —
  the exact window that broke the old in-place `xcopy` swap.
- **No publisher identity** in UAC / install prompts.

The atomic-swap + rollback fix makes the updater *resilient* to these conditions. Code-signing attacks the
*root* of the friction — it is **complementary, not a substitute**. Ship the swap fix first; signing is the
longer-lead trust improvement.

## What signing would and wouldn't fix

| Symptom | Atomic-swap fix | Code-signing |
| --- | --- | --- |
| Half-copied / bricked install on a locked file | ✅ rollback + retry | ➖ (reduces lock pressure, not a guarantee) |
| SmartScreen first-run warning | ➖ | ✅ (EV: immediate; OV: after reputation builds) |
| Defender heuristic flag / transient locks | ➖ (tolerated) | ✅ reduces likelihood |
| Publisher shown in UAC/prompts | ➖ | ✅ |

## Options

1. **OV (Organization Validation) certificate** — cheaper (~$200–400/yr). Signs binaries, but SmartScreen
   reputation must still accumulate per-publisher before warnings disappear (weeks of downloads).
2. **EV (Extended Validation) certificate** — pricier (~$300–700/yr), historically required a hardware token
   (now often a cloud HSM / FIPS keystore). Grants **immediate** SmartScreen reputation. Best UX, more setup.
3. **Azure Trusted Signing** (Microsoft's managed signing service) — pay-as-you-go, no hardware token,
   integrates with GitHub Actions; the lowest-friction modern path. Worth pricing first.

## Implementation sketch (when a cert exists)

- Sign **both** `engram.exe` and the bundled native DLLs in `backend/dist/engram/` after `pyinstaller`
  runs and **before** zipping, in `.github/workflows/release.yml`:
  ```
  signtool sign /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 /a <files...>
  ```
  (or the Azure Trusted Signing action). Timestamping (`/tr`) is mandatory so signatures stay valid after the
  cert expires.
- Store the cert/credentials as GitHub encrypted secrets; never commit them.
- Add a CI verification step (`signtool verify /pa /all`) mirroring the existing CA-bundle size check.
- Keep the `engram-windows-x64.manifest.sha256` integrity flow as-is — signing and the content manifest are
  orthogonal trust layers.

## Recommendation

Defer until there's a budget owner for the certificate. Price **Azure Trusted Signing** first (no token, CI-native).
Track as a standalone issue; it does not block the atomic-swap fix, which is the actual reliability bug.
