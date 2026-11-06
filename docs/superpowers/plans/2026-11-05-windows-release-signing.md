# Windows release signing: decision brief and implementation plan

**Date:** 2026-11-05
**Issue:** #729 (Sign Windows release builds), part of stable-release epic #734 (Tier 3, December 2026 freeze)
**Status:** **Deferred by the maintainer (2026-11-05) until Engram has at least 1,000 users.** Free, open source project; no spending on signing for now. Nothing is bought, enrolled or changed in `release.yml` by this document. The plan below is kept ready to execute when the trigger is met.

**Revisit trigger:** about 1,000 users (for example, the GitHub release download count for `engram-win-x64.zip`). On revisit, re-verify pricing, eligibility and the SignPath lead time, since the figures below date from 2026-11-05. The December freeze dates in section 4 no longer apply; re-base the timeline from the new start date, keeping the 1 to 20 business day validation window.

**Zero-cost option if the maintainer wants signing sooner:** Route B (SignPath Foundation) is free. It was ranked as the hedge, not the lead, only because of its manual per-release approval and unpublished review time.
**Builds on:** `docs/superpowers/reviews/2026-06-03-windows-code-signing.md` (which asked for Azure to be priced first)

## 1. TL;DR and recommendation

**Start Azure Artifact Signing identity validation by Monday 2026-11-09, as an individual developer, if the maintainer's legal address and government ID are in the US or Canada.** It is the only route that is cheap (about $10/month), has no hardware, authenticates from GitHub Actions with OIDC (no long-lived secret), and needs no per-release human click. Its one real risk is lead time: Microsoft says identity validation takes **1 to 20 business days**, so a worst case from 11-09 lands on 2026-12-07, which is inside the freeze window. That is why the decision is needed now, not in December.

If the maintainer is **not** in the US or Canada as an individual, or validation stalls past **2026-11-25**, fall back to **SignPath Foundation** (free for OSI-licensed projects; Engram is AGPL-3.0, which qualifies). Its cost is paperwork and a manual approval click per release, and its review time is not published.

**Do not buy an EV certificate.** Since March 2024 EV no longer gives instant SmartScreen reputation, so its price premium buys nothing here. A classic OV certificate with a cloud HSM is the third choice: dearer, slower to set up, and no better on SmartScreen than the two above.

**No route removes the SmartScreen prompt on day one.** Signing replaces "Unknown publisher" with the verified publisher name and lets reputation accumulate against that identity. Expect weeks of downloads before the warning fades, on every option. State that in the release notes so users are not surprised.

Two facts from the repo shrink the work and the cost:

1. **The auto-updater is unaffected by SmartScreen.** It downloads with `httpx` (`updater.py:_download`), so the files carry no Mark-of-the-Web. SmartScreen only bites on the first manual download of the zip. Signing is a first-install and Defender-reputation fix, not an update fix.
2. **Only `engram.exe` and `fpcalc.exe` need signing.** SmartScreen evaluates the launched executable, not the `.pyd`/`.dll` files inside `_internal/`. Signing the several hundred other PE files would burn signature quota (Azure) and, under SignPath, is not even allowed for third-party binaries. Issue #729 says "and the bundled native DLLs"; this plan deliberately narrows that (see decision D3).

## 2. What exists today (verified in the repo)

| Fact | Where |
| --- | --- |
| Windows job builds with PyInstaller 6.20.0, then smoke test, CA-bundle and fpcalc checks, `--selftest`, **then** manifest, **then** zip | `.github/workflows/release.yml` job `build-windows` |
| Manifest is `sha256  relpath` for every file under `backend/dist/engram`; it is generated from the built tree | step "Generate file manifest" |
| `sha256sums.txt` (zip hash) is computed in `create-release` from the downloaded artifact | `create-release` |
| Asset is named `engram-win-x64.zip` and must never contain "windows" (#825) | comment above "Package" |
| `engram.exe` is the onedir launcher with `upx=True`; `fpcalc.exe` is bundled to `_internal/bin/` | `backend/engram.spec` |
| `fpcalc` is Chromaprint (LGPL-2.1+), downloaded and SHA-pinned by `fetch_fpcalc.py` | `backend/scripts/fetch_fpcalc.py` |
| Updater: download, check zip against `sha256sums.txt`, extract to `.incoming`, `_verify_extracted` (sentinels + every manifest path exists), atomic promote, re-verify before swap, `.bat` robocopy + rename swap with rollback | `backend/app/core/updater.py` (`_download`, `_verify_extracted`, `apply_update`, `_restart_windows`) |
| The updater checks **existence**, not hashes, of manifest entries, and does no Authenticode check | `_verify_extracted` |
| Triggers: tag push `v*` and `workflow_dispatch` with a `draft` input (hidden from `/releases/latest`, which the updater polls) | top of `release.yml` |

### Why signing fits the update flow without updater changes

Signing changes the bytes of `engram.exe` and `fpcalc.exe`, so it must run **before** the manifest and zip steps. If it does, everything downstream is correct by construction: the manifest records signed hashes, the zip contains signed files, `create-release` hashes the signed zip, and `_verify_extracted` (existence check) is indifferent to hashes. The current step order already has the right shape; the sign step slots in right after "Build executable" and before the smoke test, so the smoke test also exercises the signed binary.

Things that could still bite, and the guard for each:

- **PyInstaller archive and appended signature.** The launcher carries its archive appended to the PE file. PyInstaller supports signing the finished executable (its bootloader accounts for the Authenticode blob), but this is the single most important thing to prove on the first dry run. The post-sign smoke test (server boots, `/api/detect-tools` finds fpcalc, `--selftest`) is the proof. Do not sign before PyInstaller finishes, and do not run UPX after signing.
- **UPX.** `engram.spec` sets `upx=True`. If UPX is on the build PATH it packs binaries, which both raises AV false positives and would invalidate any signature applied earlier. The GitHub runner has no UPX by default, so this is probably inert today, but the implementation PR should confirm that and set `upx=False` explicitly for the Windows build so it cannot change silently.
- **Timestamping is mandatory.** Azure certificates live about 72 hours (community-confirmed, not in official docs), so a signature without an RFC 3161 timestamp stops validating within days. The timestamp is what keeps old releases valid.
- **Defender locks during the swap.** Unrelated to signing, but signed binaries have better odds of skipping the heavy first-scan hold that motivated the atomic-swap work. No code change needed.

## 3. Options compared

All dollar figures are indicative and must be re-checked at purchase time. The Azure pricing page did not render dollar amounts when fetched; the $9.99 and $99.99 figures come from Microsoft's own code-signing-options page as relayed by secondary sources.

| | **A. Azure Artifact Signing** (formerly Trusted Signing) | **B. SignPath Foundation** | **C. OV cert + cloud HSM** |
| --- | --- | --- | --- |
| **Who qualifies** | Organizations in US, Canada, EU, UK, AU, NZ, JP, KR, SG, CH, NO, IL. **Individuals only if located in the US or Canada.** Azure billing account must be type Individual, and its legal name and sold-to address must match the government ID. Needs a paid subscription (no free or sponsored). | Open-source projects with an OSI license and no commercial dual licensing (AGPL-3.0 qualifies). Project must be released, maintained, have verifiable reputation, public "Code signing policy" section, MFA for team, defined Author/Reviewer/Approver roles. | Anyone a CA will validate. Some CAs issue OV to individuals; many want a registered business. |
| **Cost** | Basic about $9.99/month (5,000 signatures); Premium about $99.99/month (100,000). Billing starts when the account is created, even if validation fails. Realistic cost for Engram: Basic, about $120/year. | Free. | Cert about $129 to $530/year (one year OV list prices) plus key storage: Azure Key Vault Premium HSM or Google Cloud KMS at a few dollars/month, or a CA-hosted signing service (SSL.com eSigner, priced by plan). Realistic: $200 to $400/year. EV is $349 to $669/year, not worth it. |
| **Lead time** | Account is minutes. Identity validation **1 to 20 business days**, plus the AU10TIX photo-ID and Authenticator step done by the maintainer. | Not published. Unofficial reports guess 2 to 4 weeks. They also expect traction first and a download page that credits SignPath. | CA validation typically days to about 2 weeks; then HSM or eSigner setup. |
| **SmartScreen** | Reputation attaches to the **verified identity**, not the cert, so rotation is fine, but Microsoft states there is **no instant trust**: new publishers see warnings for weeks and hundreds of clean installs. Each new build hash also starts cold. | Same mechanics. Publisher shown is SignPath Foundation, shared across many OSS projects, which may help or may not; unverified. | OV: reputation builds per certificate and publisher with download volume. A renewal that reuses the same identity builds faster. EV no longer differs. |
| **CI integration** | Best of the three. `azure/login@v3` with OIDC (no secret) then `azure/artifact-signing-action@v2`, Windows runner only. Fits `build-windows` as is. | Heavier. Unsigned artifact is uploaded with `actions/upload-artifact`, then `signpath/github-action-submit-signing-request@v3` submits it, and **an Approver clicks approve for every release**. Policy allows GitHub-hosted runners only. Needs a "GitHub.com" Trusted Build System set up in SignPath, a project, signing policy and artifact configuration. | Moderate to heavy. Typically AzureSignTool against Key Vault, or CA's eSigner client; long-lived credentials in GitHub secrets (conflicts with acceptance criterion "credentials only in encrypted secrets" in spirit if they are broad). |
| **What can be signed** | Any PE file, any count within quota. | **Only your own binaries.** `engram.exe` yes; third-party `fpcalc.exe` (Chromaprint) and bundled DLLs no (unsigned upstream binaries may ride along). Zip signing is by signing inner files, not the zip. | Any file. |
| **Per-release friction** | None (tag push signs automatically). | Manual approval per release adds a human step to the tag-to-release chain (`tag-release.yml` then `release.yml`); a slow approver stalls releases. | None. |
| **Main risk** | Eligibility (US/CA individual) and a validation that can stall on mismatched name or address. | Unpublished approval time; strict rules; fpcalc stays unsigned. | Cost and key-custody complexity for no SmartScreen gain. |

### Reading of the table

- **A** wins on cost, setup and automation, and loses only on the geographic and legal-identity gate and on a validation that Microsoft itself says can take four weeks.
- **B** is the right hedge, not the lead: free, but its manual approval and unpublished lead time cut against a December freeze. Its approval step is arguably a feature for supply-chain hygiene, but a solo maintainer approving their own release adds little.
- **C** is only worth choosing if A and B are both unavailable.

## 4. Decisions the maintainer must make

| # | Decision | Recommended answer | Needed by |
| --- | --- | --- | --- |
| **D1** | Which route to start. | A (Azure), with B as a parallel hedge only if D2 says the maintainer is ineligible. | **2026-11-09** |
| **D2** | Eligibility facts: is the maintainer's legal address and government ID in the US or Canada? Sign as an individual (name on the certificate is the legal name, with city and state) or via a registered entity? | Individual if eligible. The legal name will be public on every release. Confirm they are comfortable with that. | **2026-11-09** (blocks everything) |
| **D3** | Signing scope. | `engram.exe` + bundled `fpcalc.exe` only. Do not sign `_internal` DLL/`.pyd` files. | 2026-11-13 (before implementation PR; does not gate the 11-09 validation start) |
| **D4** | Budget approval for about $10/month (about $120/year), billed from the day the account is created. | Approve. | **2026-11-09** |
| **D5** | Failure policy: what happens if signing fails on a tag. | Fail the release hard, no silent unsigned fallback. Keep `workflow_dispatch` `draft=true` for dry runs. Hotfix path: re-run the workflow, do not add a skip flag. | 2026-11-13 |
| **D6** | Should the updater verify the Authenticode publisher of the staged `engram.exe` before swapping (section 6, optional phase)? | Yes, but as a later separate PR, warn-only for the first signed release, enforcing after. | Before the stable tag |
| **D7** | Who is the SignPath Approver, and are they willing to be named publicly (only if B is used)? | Only decide if B is activated. | On fallback |

### Timeline against the December freeze

| Date | Step |
| --- | --- |
| 2026-11-09 | D1, D2, D4 decided. Create Azure subscription (billing account type Individual, name and address matching ID), register `Microsoft.CodeSigning`, create account, submit identity validation, complete the ID check on the phone. |
| 2026-11-13 | D3, D5 decided. Implementation PR can be written while validation is pending (the pipeline can be built and reviewed without a live profile; it only needs the profile for the dry run). |
| **2026-11-25** | **Tripwire.** If validation is not Completed, open the SignPath application (needs the "Code signing policy" text on the README and download page first, see section 5) and decide whether a plan C quote is worth the effort. 11-09 plus 12 business days is 11-25. |
| 2026-12-07 | Latest date Microsoft's own 20-business-day bound reaches from an 11-09 start. |
| By freeze | First dry run on a draft release; first signed stable-line release candidate. If nothing is validated by then, ship the RC unsigned and add the "unsigned, here is why" note to the release body. |

Starting the Azure validation also costs nothing extra beyond the first $9.99, so the hedge is cheap. Applying to SignPath at the same time is allowed but costs real effort (policy text, role assignments) and the two identities would then be published under different publisher names, which fragments SmartScreen reputation. Do not run both unless the tripwire fires.

## 5. One-time setup the maintainer does (not automatable by an agent)

Do not delegate these to a coding session: they need the maintainer's government ID, phone and billing details.

### Route A (Azure)

1. Confirm Azure billing account type is **Individual** and that its legal name and sold-to address exactly match the ID. Fix the billing account first; a mismatch forces a new validation request.
2. Create a pay-as-you-go subscription (trial and sponsored subscriptions are not supported).
3. Register the `Microsoft.CodeSigning` resource provider.
4. Create an Artifact Signing account (**Basic** SKU is enough; pick a region and note its endpoint, for example `https://eus.codesigning.azure.net/`). Account names are 3 to 24 alphanumeric characters, globally unique.
5. Assign yourself the *Artifact Signing Identity Verifier* role, then create an **Individual, Public** identity validation. Complete the AU10TIX ID flow with the phone and Microsoft Authenticator. Watch the portal for "Action Required".
6. When Completed, create a **Public Trust** certificate profile (and optionally a *Public Trust Test* profile for the first dry run). Choose whether street address and postal code appear in the subject; leaving them off is the privacy-friendly default.
7. Create an Entra app registration for GitHub, add a **federated credential** for `repo:Jsakkos/engram:environment:release-signing`, and assign it the *Artifact Signing Certificate Profile Signer* role on the account.
8. In the GitHub repo create an **Environment** named `release-signing` (optionally with a required reviewer if a manual gate on signing is wanted). Add these as environment **variables**, not secrets, since OIDC makes them non-secret identifiers: `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`, `SIGNING_ENDPOINT`, `SIGNING_ACCOUNT`, `SIGNING_PROFILE`.

This satisfies the issue's acceptance criterion "credentials live only in GitHub encrypted secrets" in a stronger form: there is no stored credential at all.

### Route B (SignPath), only if activated

Add a "Code signing policy" section to `README.md` and the download docs that credits SignPath.io and SignPath Foundation, lists Authors/Reviewers/Approvers and states the privacy policy; make sure the download page exists in its release form; apply at signpath.org/apply; once approved, add the GitHub.com Trusted Build System and a project/policy/artifact configuration (a `<zip-file>` root containing `engram.exe`), plus `SIGNPATH_API_TOKEN` as a secret. Third-party `fpcalc.exe` remains unsigned. Because this route adds a per-release approval, set the `submit-signing-request` timeouts well above the 600 s default or the release job will fail while waiting for a human.

## 6. CI changes, step by step (for the implementation session)

Land these as separate small PRs. None is merged until D1 to D5 are answered and the maintainer reports validation Completed.

### PR 1: signing step in `build-windows` (Route A)

File: `.github/workflows/release.yml`, job `build-windows` only. Linux and macOS jobs are untouched.

1. **Job permissions.** The workflow sets `permissions: contents: write`. A job-level `permissions` block replaces, not merges, so `build-windows` needs both `contents: read` (for checkout) and `id-token: write` (for OIDC). Declare them on the job, leaving `create-release` on the workflow default.
2. **Environment.** Add `environment: release-signing` to the job so the federated credential subject matches (it works for both tag and `workflow_dispatch` runs). A required reviewer can be added later, but then every tag release waits on a click, which gives up the "no per-release human click" advantage in section 3. Leave it off unless wanted. `build-windows` needs only `contents: read`, not `contents: write`; only `create-release` uploads assets.
3. **Insert after "Build executable" and before "Smoke test built executable":**

   ```yaml
   - name: Azure login (OIDC)
     uses: azure/login@<full-sha>   # v3; pin to a full commit SHA, not the floating tag
     with:
       client-id: ${{ vars.AZURE_CLIENT_ID }}
       tenant-id: ${{ vars.AZURE_TENANT_ID }}
       subscription-id: ${{ vars.AZURE_SUBSCRIPTION_ID }}

   - name: Stage binaries to sign
     shell: pwsh
     run: |
       New-Item -ItemType Directory -Force sign-staging | Out-Null
       Copy-Item backend/dist/engram/engram.exe sign-staging/
       Copy-Item backend/dist/engram/_internal/bin/fpcalc.exe sign-staging/

   - name: Sign engram.exe and fpcalc.exe
     uses: azure/artifact-signing-action@<full-sha>   # v2; pin to a full commit SHA, not the floating tag
     with:
       endpoint: ${{ vars.SIGNING_ENDPOINT }}
       signing-account-name: ${{ vars.SIGNING_ACCOUNT }}
       certificate-profile-name: ${{ vars.SIGNING_PROFILE }}
       files-folder: sign-staging
       files-folder-filter: exe
       file-digest: SHA256
       timestamp-rfc3161: http://timestamp.acs.microsoft.com
       timestamp-digest: SHA256

   - name: Put signed binaries back and verify
     shell: pwsh
     run: |
       Copy-Item sign-staging/engram.exe backend/dist/engram/engram.exe -Force
       Copy-Item sign-staging/fpcalc.exe backend/dist/engram/_internal/bin/fpcalc.exe -Force
       foreach ($f in "backend/dist/engram/engram.exe", "backend/dist/engram/_internal/bin/fpcalc.exe") {
         $sig = Get-AuthenticodeSignature $f
         if ($sig.Status -ne "Valid") { throw "$f signature status: $($sig.Status)" }
         if (-not $sig.TimeStamperCertificate) { throw "$f has no RFC 3161 timestamp" }
         Write-Host "$f signed by: $($sig.SignerCertificate.Subject)"
       }
   ```

   Notes for the implementer:
   - Before signing, assert `sign-staging` contains exactly the two expected files, so a changed action filter cannot sign or miss anything silently.
   - Staging into a temp folder makes the action's folder recursion semantics irrelevant and keeps the signature count at exactly two per release, far under the 5,000/month quota even with many dry runs.
   - Verify the action's exact input names against its README at implementation time (the names above are from its current documentation; the README had no tagged release list when checked).
   - Use `Get-AuthenticodeSignature` rather than locating `signtool.exe` in the SDK path. It is a built-in and gives status, signer and timestamp in one call. Treat `Valid` plus a timestamp as the gate. A stricter version also asserts `$sig.SignerCertificate.Subject` contains the expected publisher name (store that string as an environment variable `SIGNING_EXPECTED_SUBJECT` so a mis-pointed profile is caught).
4. **Leave unchanged:** the smoke test, CA/fpcalc checks, `--selftest`, manifest and zip steps. They now run against signed files, which is the point.
5. **Add a new step between "Generate file manifest" and "Package"** (not inside the earlier "Verify CA bundle" step, which runs before the manifest): re-run `Get-AuthenticodeSignature` on both `backend/dist/engram/engram.exe` and `backend/dist/engram/_internal/bin/fpcalc.exe` to prove nothing modified the signed files before they were zipped.
6. **Spec hardening. Precondition for the dry run, because UPX would corrupt the appended PyInstaller archive plus signature and make the dry run untrustworthy** (same PR or an adjacent one): in `backend/engram.spec`, set `upx=False` for both `EXE` and `COLLECT` (or gate on `os.name != "nt"`). Check the runner's PATH for `upx` first so the change is understood rather than assumed.
7. **Dry run.** Trigger `workflow_dispatch` with `draft: true` on the PR branch. Check: all three Windows verify steps pass; the smoke test passes against the signed exe (this is the PyInstaller-appended-signature proof); download the draft zip in a browser on a clean Windows machine and note the SmartScreen dialog and the Digital Signatures tab on `engram.exe`. Record the result in the PR.

### PR 2: docs and release notes

- `README.md` / user docs: one paragraph on the first-run SmartScreen prompt ("Engram is signed; Windows still shows a prompt until the publisher builds reputation. Click More info, then Run anyway"), and that auto-updates are unaffected.
- `CHANGELOG.md` under `[Unreleased]`: user-facing line, referencing #729.
- `CLAUDE.md` Release section: one bullet saying the Windows job signs `engram.exe` and `fpcalc.exe` before the manifest and zip, and that the order must be preserved.
- Update the 2026-06-03 review doc's status line to point at this plan (optional).

### PR 3 (optional, decision D6): updater verifies the publisher

Goal: defend against a tampered release asset, which the current same-origin SHA-256 checks cannot catch because the attacker who replaces the zip can replace `sha256sums.txt` and the manifest too.

- Add `_verify_signature(engram_exe: Path)` in `updater.py`, Windows only, invoked from `_download` after `_verify_extracted` and again in `apply_update` next to the existing re-verify. Implement via `WinVerifyTrust` through `ctypes`, or by shelling out to PowerShell `Get-AuthenticodeSignature` (slower and subject to Defender latency; use a generous timeout).
- **Pin the publisher subject (CN), never the thumbprint.** Azure certificates rotate about every 72 hours, so a thumbprint pin would break every release.
- **Trust on first signed:** enforce only if the currently running `engram.exe` is itself validly signed. Users updating from the last unsigned release to the first signed one must not be blocked. Ship the first signed release in warn-only mode (log and surface in diagnostics), then flip to enforcing.
- Keep it out of the `.bat` helper; do it in Python before the swap so a failure leaves state in `ERROR` with the live install untouched, like the other verifier failures.
- Tests: pure-function tests with the PowerShell/`ctypes` call mocked (valid, invalid, unsigned, wrong subject, current-build-unsigned skip); no real signing needed. The existing updater tests that call `_verify_extracted` must still pass untouched.

### PR 1 variant for Route B (SignPath)

Only if the tripwire fires. In `build-windows`, after "Build executable": copy `engram.exe` into a clean folder, upload it with `actions/upload-artifact` (`id: unsigned`), run `signpath/github-action-submit-signing-request@v3` with `github-artifact-id: ${{ steps.unsigned.outputs.artifact-id }}`, `wait-for-completion: true` and a long timeout, `output-artifact-directory: signed`, then copy the signed `engram.exe` back into `backend/dist/engram/`. Skip `fpcalc.exe`. Everything after (verify, smoke test, manifest, zip) is identical. Document the publisher as SignPath Foundation in the verification step instead of the individual's name.

## 7. Risks and open questions

- **Identity validation can stall.** Microsoft allows three document-upload attempts and requires matching documents. Mitigation: fix billing account name and address before submitting; use a clean ID photo per the quickstart rules.
- **SmartScreen will still warn at first.** Set expectations in the release notes and README (PR 2). The first Windows users to download after signing carry the reputation cost.
- **Public legal name on every release.** An individual certificate shows the legal name plus city and state. This is a privacy decision (D2), not a technical one.
- **Account billing starts at creation.** Create the account and submit validation the same day to avoid paying for idle months.
- **Azure region and service name drift.** The product was renamed from Trusted Signing to Artifact Signing (CLI extension `artifact-signing`, action `azure/artifact-signing-action`); older blog posts and the June review use the old names and a different action. Use the current docs.
- **Action pinning.** The repo's other workflows use major-version tags; for a signing action with access to a code-signing identity, pin to a full commit SHA.
- **Unverified claims.** Pricing dollar amounts (Azure Basic/Premium), the 72-hour certificate lifetime, the SignPath 2 to 4 week review estimate and the OV/HSM price ranges come from secondary sources. Re-check each before spending money or setting dates.
- **EV claim conflict.** One vendor blog claims EV still gives instant reputation. Microsoft-aligned and multiple other sources say it does not since March 2024. This plan follows the majority and the Microsoft guidance.

## 8. Acceptance mapping (issue #729)

| Criterion | Delivered by |
| --- | --- |
| Windows release artifacts are Authenticode-signed and timestamped | PR 1: `engram.exe` and `fpcalc.exe`, RFC 3161 via `timestamp.acs.microsoft.com` |
| CI verifies the signatures before publishing | PR 1: `Get-AuthenticodeSignature` gate (status Valid, timestamp present, optional subject check) before the smoke test and again before packaging |
| Signing credentials live only in GitHub encrypted secrets | PR 1 setup: OIDC federation, so no stored signing credential; identifiers live in a protected Environment |
| (Narrowed) "and the bundled native DLLs" | Deferred by decision D3: not needed for SmartScreen, costs quota, not permitted under SignPath |

## Sources

- [Quickstart: Set up Artifact Signing (Microsoft Learn)](https://learn.microsoft.com/en-us/azure/artifact-signing/quickstart)
- [Code signing options for Windows app developers (Microsoft Learn)](https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/code-signing-options)
- [Artifact Signing GitHub Action](https://github.com/Azure/artifact-signing-action)
- [Artifact Signing pricing (Azure)](https://azure.microsoft.com/en-us/pricing/details/artifact-signing/)
- [SmartScreen reputation for Windows apps (Microsoft Learn)](https://learn.microsoft.com/windows/apps/package-and-deploy/smartscreen-reputation)
- [SignPath Foundation conditions for open source projects](https://signpath.org/terms.html)
- [SignPath GitHub trusted build system docs](https://docs.signpath.io/trusted-build-systems/github)
- [Azure Artifact Signing review and pricing (itechguides)](https://www.itechguides.com/best/code-signing-software/azure-artifact-signing/)
- [Code signing certificate prices, OV vs EV (my-ssl.com)](https://my-ssl.com/learn/code-signing-certificate-price)
- [SSL.com OV code signing](https://www.ssl.com/products/software-integrity/code-signing/ov/) and [eSigner](https://www.ssl.com/products/software-integrity/signing-service/)
- [EV certificates no longer grant immediate reputation (ToDesktop)](https://www.todesktop.com/blog/posts/windows-apps-psa-ev-certs-do-not-grant-immediate-reputation-anymore)
