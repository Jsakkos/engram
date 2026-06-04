# Changelog

All notable changes to Engram will be documented in this file.

## [Unreleased]

## [0.15.4] - 2026-06-04

_Highlights: a fixes-only release — large multi-season imports no longer dump episodes into "Needs Review" while they wait for a match slot, the review page's "Re-match" is now visible and advisory (it never silently files a track), and every ripped track now shows consistently how its episode was identified._

### Fixed

- **Large multi-season imports no longer dump most episodes into "Needs Review"** — importing a folder with many seasons at once (e.g. a 7-season **Seinfeld** collection, ~175 episodes) created one job per season and queued every episode for matching, but Engram only matches a couple at a time. Episodes waiting their turn were shown as actively "working," and after 30 minutes the stale-job watchdog mistook the whole patiently-waiting queue for a stuck job and force-advanced ~126 of them straight to **Needs Review** — even though nothing was actually wrong. Tracks now show a distinct **QUEUED** state ("waiting for a match slot") while they wait, flip to **MATCHING** only when work actually starts, and are never sent to review just for waiting in line. A single match that genuinely hangs is still recovered on its own (and frees its slot so the rest of the queue keeps draining), so big imports now finish on their own instead of needing manual cleanup. (#334)
- **"Re-match" on the review page no longer runs invisibly or files a track behind your back** — clicking **Re-match** on a track in the review inspector gave no sign it was working (the spinner flashed for a fraction of a second and cleared while matching was still running in the background), and when the deep match landed on an episode that already existed in your library it quietly tried to organize the file, hit a "file already exists" conflict, and surfaced *nothing* — leaving you staring at an unchanged screen (seen re-matching a bonus track on a **Gilmore Girls** disc). A manual re-match is now **advisory**: it shows a live "Re-matching…" indicator on the track for the whole match, then surfaces the candidate in review for you to confirm or mark as an Extra — it never silently organizes anything. If the suggested episode is already present, you now get a clear "File exists — likely a duplicate or extra" warning instead of a silent no-op. (The underlying conflict that was being swallowed is now reported everywhere it can occur, not just on this path.) (#327)
- **Each ripped track now shows consistently how its episode was identified** — inside a disc card, the per-track details rendered unevenly: some tracks showed a confidence percentage and a vote tally, while others showed only the matched episode with no confidence and no source chip at all, so a perfectly good match looked broken or empty (seen ripping a **Gilmore Girls** season). Tracks identified by Engram's whole-file fallback have no per-chunk "votes" by design, and the card was mistaking "no votes" for "no confidence." Every matched track now shows a normalized confidence and a provider chip — an Engram mark for audio matching (with a distinct fingerprint variant), or a DiscDB/AI/Manual chip for those sources — with the vote tally shown only when votes actually exist and a small "full-file" tag explaining the matches that have none. (#333)

## [0.15.3] - 2026-06-04

_Highlights: a fixes-only release — the Windows "restart to update" swap is now crash-safe with automatic rollback, a disc loaded while the previous one is still matching is picked up right away, and a season's longest episode is no longer misfiled as an extra._

### Fixed

- **The README download-count badges now show accurate cumulative per-OS totals** — the badges counted release assets by bare file extension, so the Linux badge swept in the macOS builds *and* the rolling subtitle-cache data pack (a `.tar.gz` whose GitHub download counter resets to zero every time the cache is rebuilt). That made the Linux number read roughly 15× too high and visibly fluctuate downward as the cache was re-published. Downloads are now tallied by exact per-OS binary name, and a separate macOS badge was added. (#324)
- **A disc's longest episode could be misfiled as an "extra"** — before matching a TV track, Engram checks its length against the episode runtimes TMDB lists for the season, and a track that's too far off is set aside as bonus content without ever being matched. That length window was symmetric (±5 minutes), but DVD/Blu-ray episodes run *longer* than TMDB's listed runtime — the disc includes the "previously on" recap, full end credits, and "next time" preview — so a season's longest episode could overshoot the window and be dropped into Extras even though its shorter siblings on the same disc matched fine (seen with **Gilmore Girls** S1, where the ~50-minute "Rory's Dance" was filed as an extra next to its ~46–48-minute neighbors). The window is now lenient on the long side (up to 5 minutes short, 10 minutes over), so a long-but-real episode is still transcribed and matched. (#321)
- **"Restart to update" no longer risks breaking your install on Windows** — the updater downloaded and verified the new version correctly, but the final swap copied the new files *in place over your running install* with no safety net. If Windows still held a lock on any file the instant the old app exited (antivirus or Search Indexer commonly do, for a second or two), the copy half-finished and left a mix of old and new files that wouldn't start — with no way back, so the only recovery was to download and unzip the release by hand. The Windows updater now copies the new build to a separate folder first, verifies it's complete, swaps it into place with two instant renames, and **automatically rolls back to your previous version if anything goes wrong** — and writes a step-by-step log to `~/.engram/update_helper.log` so any future failure is diagnosable. Cleanup of already-installed staged updates is also now crash-safe. (#322)
- **A disc loaded while Engram was still matching the previous one wasn't picked up** — after a disc finished ripping, Engram ejected it but kept matching its episodes in the background for several minutes. If you loaded the next disc during that window, it was silently ignored — no job started — until the previous job finished and you ejected and reinserted it (and reinserting too soon, before the previous job was done, was ignored too). Engram was treating the still-matching job as if it still occupied the drive, even though the disc had already been ejected. It now recognizes that a job past the ripping stage no longer holds the drive and starts a fresh job for the new disc right away; reloading the *same* disc is still ignored, so nothing gets duplicated. (Seen binge-ripping a **Gilmore Girls** season.) (#323)
## [0.15.2] - 2026-06-04

_Highlights: the community fingerprint network moved to a stable custom domain (`api.engramfp.com`); existing installs migrate automatically on update with no interruption._

### Changed

- **The fingerprint network moved to a stable custom domain** — Engram now contributes and identifies against `https://api.engramfp.com` by default, instead of the old `*.workers.dev` address. Existing installs pick up the new address automatically on update (unless you've set a custom server URL in Settings → Data Sharing); the old address keeps serving during the transition, so nothing breaks mid-migration. (#319)

## [0.15.1] - 2026-06-03

_Highlights: a data-loss fix — importing from a watch folder no longer deletes your source folder — plus faster, more accurate episode matching, several import-reliability fixes, and a disc loaded right after an eject is now reliably picked up._

### Fixed

- **An import could delete the source folder you imported from** — when importing pre-ripped files from a watch folder, Engram treated that folder as disposable "staging" and, on a successful job, deleted it wholesale once the matched files had been moved into your library (with the `on_success`/`on_completion` cleanup policy). For a disc rip that staging folder is a throwaway temp directory, so deleting it is harmless — but for an import it is **your own source folder**, so anything still inside it was permanently removed (not sent to the Recycle Bin). This was especially destructive when the folder also held content Engram never imported — for example loose files at the top level shadowing the `Season NN` subfolders beneath them — because the un-imported episodes were deleted along with the folder. Import sources are now never deleted by staging cleanup; only disc-rip staging directories are. (#317)
- **A multi-season import folder could skip its Season subfolders entirely** — when an import watch folder contained both loose top-level `.mkv` files and `Season NN` subfolders, Engram could latch onto the loose files, treat the whole folder as a single season-less "flat" import, and never scan the season subfolders at all (which file it noticed first was effectively random). Those seasons were then left un-imported — and, with the bug above, deleted. The scanner now recognizes that a folder containing season/disc subfolders is a container: it imports each season, and leaves the ambiguous loose top-level files in place (logged) rather than letting them shadow the real content. (#317)
- **Correct episodes were being re-transcribed (slowly) instead of accepted** — when matching ripped or imported episodes, a confident, decisive match could still be thrown away and re-run through a much slower full-file transcription, which sometimes turned a correct match into a wrong one or a manual-review prompt (seen with some **True Detective** episodes). The matcher judged matches by a raw overlap score that is naturally tiny for speech-vs-subtitle comparisons, ignoring its own calibrated 0–100% confidence. It now trusts that calibrated confidence: a decisive, high-confidence match is accepted directly and filed automatically, so matching is both faster and more accurate. (#316)
- **Import folders now reliably show "matching" in the dashboard** — an imported folder could be busy matching episodes in the background while its card stayed stuck on the scanning animation. The job now flips to the matching view the moment real matching begins, hardening the earlier fix so a missed status update can't strand the card. (#316)
- **The import watch folder stopped re-importing a folder after a single failed attempt** — once any job had been created for a watched folder, that exact folder was blocked from ever being imported again, even if the job had failed (cancelled, or auto-failed when the server restarted mid-job). Because the watch folder is re-scanned on every poll and on every restart, a one-time failure silently wedged the folder: the watcher kept detecting it and immediately skipped it ("Job already exists for staging path …"), so nothing imported. Engram now dedups only against an active or review-pending job for the path, so a previously failed import is retried on the next scan instead of being stuck forever. (#311)
- **A disc loaded right after Engram ejected the previous one could be ignored** — when a job finished, Engram ejected the disc but didn't tell its drive monitor the drive was now empty. If you inserted the next disc before the monitor's next poll noticed the eject, it saw "disc present" both before and after and fired no "inserted" event — so no job was created and the new disc sat unprocessed until you ejected and reinserted it (or restarted Engram). Engram now marks the drive empty the instant it ejects, so the next disc always starts a fresh job. (#289, thanks @katelovescode!)
- **Clearer disc card when two shows share a name** — a disc that matches more than one same-name show (for example the 1993 vs 2023 **Frasier**) is flagged for review before ripping, but its card showed a "Review needed" button that opened an empty review screen — there's nothing to review until the disc is ripped — right next to an identical "Review needed" status badge. The card now hides that dead-end button until the disc actually has ripped tracks, emphasizes the **Wrong title?** action as the thing to click, and adds a short banner explaining the same-name ambiguity and how to resolve it. (#308)

## [0.15.0] - 2026-06-03

_Highlights: same-name shows (for example the 2023 **Frasier** vs the 1993 original) can now coexist in your TV library, each in its own year/TMDB-tagged folder; the dashboard now warns you up front when no TMDB key is configured; and FFmpeg is now a documented prerequisite with broader Windows auto-detection and inline path validation in the Config Wizard._

### Added

- **Same-name TV shows can now coexist in the library** — TV episodes were filed under the bare show name (`Frasier/Season 01/…`), so ripping both the 1993 **Frasier** and the 2023 revival collided in one folder with identical filenames, and the second rip was skipped, overwritten, or bounced to Review. A new optional **Show Folder Format** setting disambiguates the show folder with the first-air year and TMDB id, matching the layout Plex (`Frasier (1993) {tmdb-3452}`) and Jellyfin (`Frasier (1993) [tmdbid-3452]`) parse for reliable matching. It is opt-in and defaults to the current bare-name layout, so existing libraries are untouched — set a format containing `{year}`/`{tmdb_id}` (and optionally add them to the episode filename format) to enable it. (#297)
- **The dashboard now warns you when TMDB isn't configured** — without a TMDB Read Access Token, discs can't be identified, but previously the only symptom was matches quietly failing. The dashboard now shows a dismissible banner — with a one-click link to open Settings — whenever no TMDB key is set, plus an inline notice on each active job card. Both clear automatically the moment a token is saved, with no page reload. (#294, thanks @katelovescode!)
- FFmpeg is now documented as a prerequisite, with per-platform install steps (including `winget install Gyan.FFmpeg` on Windows) and a dedicated [Troubleshooting](https://jsakkos.github.io/engram/troubleshooting/) page led by the common "FFmpeg not detected" case.
- The Config Wizard now validates a manually-entered MakeMKV or FFmpeg path against the backend and shows the detected version inline (or the specific error), so a hand-typed override is no longer saved blind. The FFmpeg "not found" card also links to the download page.

### Changed

- Windows FFmpeg auto-detection now also searches the Chocolatey, scoop, winget (`Gyan.FFmpeg`), and user-home install locations, so a freshly-installed FFmpeg is found even when it isn't yet on the running process's `PATH`. The in-app install hint now names the exact winget package.

### Fixed

- **A TV disc named like "Show Season 11 Disc 2" could match the wrong episodes for hours, then fail** — when a disc had no readable volume label, Engram fell back to the drive's display name (e.g. `Supernatural Season 11 Disc 2`), but the parser only recognized a season when the disc number was in parentheses (`(Disc 2)`). A space-separated `Disc 2` left the season undetected, so no subtitles were downloaded for that season and matching fell back to brute-forcing every previously-seen season's subtitles with speech recognition — a run that could churn for many hours scoring the audio against the wrong seasons before failing. Engram now reads the season from these names (with or without parentheses or a dash), so the correct season is detected, its subtitles download, and episodes match on the first pass. (#303)
- **Matching a disc whose season couldn't be determined was needlessly slow** — when a TV disc's season is unknown, Engram matches the file against every candidate season in turn. Each attempt re-ran speech recognition over the *same* audio from scratch, so a show with many seasons could spend hours re-transcribing identical audio before giving up. Transcriptions are now cached and reused across season attempts, so only the first attempt does the expensive transcription work and the rest are near-instant. (#303)
- **Import watch-folder jobs didn't show their tracks or matching progress** — a job created from the import watch folder (pre-ripped MKVs in a watched directory) ran to completion on the backend, but the dashboard stayed frozen on the "scanning" radar for the entire matching phase and then jumped straight to organizing/completed, never showing the track grid or live per-track matching. Because these jobs skip ripping, they advance `identifying → matching` directly — a transition the job state machine rejected, so the card never learned it had left identifying. Engram now allows that shortcut (and the movie equivalent), broadcasts each track's matching state immediately, and routes the movie import branch through the state machine, so import jobs show their tracks and matching progress just like a disc rip. (#307)

## [0.14.1] - 2026-06-02

_Highlights: a hardening fix for the in-app auto-updater — it can no longer install an incomplete or corrupted download over your working copy, and builds now always include the TLS certificate bundle whose absence silently broke all networking in some 0.14.0 installs._

### Fixed

- **The auto-updater could stage and apply an incomplete build, breaking the app** — if an update's extraction was interrupted (or files were removed afterward, e.g. by antivirus), Engram could leave a half-unpacked build that still looked "ready to install": the integrity check only validated the downloaded archive, never the unpacked files. Applying it would copy a broken build over your working install — in one case a build missing its TLS certificate bundle, which silently breaks every network request (update checks, TMDB, subtitle downloads). The updater now unpacks to a temporary location and only swaps it into place once the build is verified complete (against a per-release file manifest plus required-file sentinels), then re-checks completeness one more time immediately before applying — and if that final check fails it drops the staged update instead of leaving a dead "ready to install" offer. As extra safeguards the TLS certificate bundle is now always bundled, the build toolchain is pinned, and the release smoke test fails if a build can't complete an HTTPS request. (#296, #298)

## [0.14.0] - 2026-06-02

_Highlights: a one-click "Did you mean?" candidate picker for discs that share a name with another show (for example the 2023 **Frasier** vs the 1993 original) — pick the right show in the Re-Identify dialog without re-typing a TMDB search — plus matching fixes so a re-identified revival's episodes match and file correctly instead of being shunted to Extras or matched against the wrong show's subtitles._

### Added

- **One-click "Did you mean?" candidate picker when re-identifying a same-name disc** — when a disc is flagged for a same-name collision (for example a 2023 **Frasier** disc that was identified as the 1993 original), the Re-Identify dialog now shows the matching shows as quick-pick buttons. One click on _Frasier (2023)_ re-identifies the disc with the correct show, instead of having to re-type a TMDB search to find it. The free-text search remains as a fallback. (#291)

### Fixed

- **Same-name shows could be silently identified as the wrong one** — a disc whose label has no year (e.g. `FRASIER_S1D1`) was matched to the more popular same-named show on TMDB, so a 2023 revival disc was treated as the 1993 original and every episode matched the wrong subtitles at random, landing in Review with an unhelpful "assign episodes manually" message. Engram now (1) flags a no-year disc that has a real same-name twin for review *before* ripping, suggesting which show to pick, and (2) as a backstop, when a whole TV disc matches no episodes at all and a same-name twin exists, surfaces a clear "this doesn't resemble *Show (year)* — did you mean *Show (other year)*? Re-identify to fix" review instead of the generic message — and it now reaches that review after a single full-coverage confirming match pass, instead of re-transcribing the disc three times over against the wrong show's subtitles first. Re-identifying to the correct show now reliably downloads that show's subtitles. (#287, #290)
- **Re-identifying a same-name revival could still misfile its episodes as "extras"** — after correcting a no-year disc to the right show in Review (e.g. the 2023 **Frasier** revival), the length check that separates real episodes from bonus features still looked up expected episode runtimes for the *original* same-named show. The revival's episodes didn't match any of the wrong show's runtimes, so they were treated as bonus material, filed into `Extras/`, and never episode-matched. That runtime check now uses the show you re-identified to, so the correct episodes are matched instead of being shunted to `Extras/`. (#292)
- **Two same-name shows shared one subtitle cache folder** — downloaded reference subtitles were stored on disk by show *name* (`…/cache/data/Frasier/`), so if both a 1993 and a 2023 *Frasier* were ever processed their episodes landed in the same folder and the matcher could read one show's subtitles while identifying the other. The runtime subtitle cache is now keyed by the show's TMDB id (`…/cache/data/3452/` vs `…/cache/data/195241/`), completing the same-name isolation already applied to the shipped reference cache — the two shows can no longer cross-contaminate. Existing name-keyed caches still work (a show with no resolved id falls back to its name) and no cache rebuild is required. (#288, #293)

## [0.13.2] - 2026-06-01

_Highlights: Engram can now tell apart two TV shows that share a name — for example **Frasier** (1993) and the 2023 revival. An ambiguous disc is sent to Review with both candidates to choose from, and once you pick one, that exact show drives subtitle download and episode matching instead of whichever same-named show happened to rank first._

### Fixed

- **A disc for a show that shares its name with another show could be mis-identified and silently fail to match** — Engram identified shows by *name*, so two different TMDB shows with the same title (for example **Frasier** from 1993 and the 2023 revival) were indistinguishable: it downloaded subtitles for, and matched against, whichever one ranked first, and a disc for the other one would score at the noise floor and land in Review with no clear reason. The resolved TMDB id is now carried as the authoritative show identity through subtitle download, episode matching, and the reference-corpus lookup. When a disc is genuinely ambiguous between two substantial same-name shows, it is routed to Review with both candidates so you can choose; your choice then drives subtitle re-download and matching, and a guard refuses a precomputed reference set that actually belongs to the other same-named show. No library, database, or cache rebuild is required. (#278)
- **The fingerprint and AI episode-matching paths could still pick a same-name show by name** — the fingerprint-identification cascade and the AI (LLM) episode-matching fallback each looked the show up by name independently, so for a same-name collision they could fetch the wrong show's fingerprint data or AI context when those features are enabled. Both now use the known TMDB id when it is available, falling back to name lookup only when it is not. (#282)

## [0.13.1] - 2026-06-01

_Highlights: the in-app auto-update is fixed end to end on Windows — installed builds now actually show the **Restart now** button (it was hidden by a status flag that never reached the UI), and clicking it reliably swaps in the new version and relaunches instead of silently shutting Engram down._

### Fixed

- **The in-app "Restart now" update button never appeared on installed builds** — when a new version finished downloading in the background, the banner showed "ready to install — dev mode, manual download required" and hid the one-click restart button, even on real (frozen) installs that already had the update staged and ready. The build-type flag the button gates on was dropped from the live status push (it rode only the REST endpoint, not the WebSocket message), so the UI always read it as "dev mode." Installed builds now correctly show **Restart now**. The banner is also seeded from the authoritative status endpoint so it appears even if you open the dashboard after the update finished downloading, and stale downloaded versions are now pruned from `~/.engram/update/` instead of accumulating.
- **Restarting to apply an update could shut Engram down without installing it (Windows)** — the helper that swaps in the new files after Engram exits was launched in a way that let Windows terminate it together with the closing app when Engram was running inside a process Job Object, so the app went down and the update was never applied. The helper now breaks away from the job (and uses a console-independent wait), so the restart reliably swaps in the new version and relaunches.

## [0.13.0] - 2026-06-01

_Highlights: a new opt-in AI episode-matching fallback for discs that have no reference subtitles — Engram transcribes the rip and matches it against the TMDB synopsis to suggest an episode in Review; the AI key and "AI-Powered Episode Matching" toggle now save and persist; and queued fingerprint contributions survive a sustained server outage instead of being permanently dropped._

### Added

- **AI episode-matching fallback when no subtitles are found** — for discs where no reference subtitles can be downloaded, the normal subtitle-based matcher has nothing to compare against. Engram can now transcribe the ripped file with on-device speech recognition (ASR) and match that transcript against each candidate episode's TMDB synopsis, surfacing a best-guess episode in the Review queue. It is opt-in via the **"AI-Powered Episode Matching"** setting and never auto-organizes — the suggestion is always presented for you to confirm. (#283)
- **Force-delete a stuck fingerprint contribution** — a contribution retrying against a permanently unreachable server could stay queued indefinitely, and its in-flight guard blocked removal. The single-contribution delete now accepts a `force=true` option to retract such a row. It still refuses to delete anything already uploaded — use **Forget me** to recall data that has left your machine. (#280)

### Fixed

- **The AI API key and "AI-Powered Episode Matching" toggle didn't persist** — the AI/Gemini API key field always rendered blank with no sign a key was saved, and the episode-matching toggle could not be enabled at all, because the underlying config field was missing from the settings API models and was silently dropped on save. Settings now shows a **"Key saved"** indicator once a key is stored, and the toggle persists across restarts. (#283)
- **Queued fingerprint contributions were permanently dropped during a sustained server outage** — the uploader's retry cap was a lifetime cap, so a prolonged upstream outage (for example 503s during a bulk library bootstrap) could exhaust a contribution's attempts in a single drain and mark it permanently failed, with no automatic recovery. Transient errors (5xx, network, and rate-limit 429) now keep the contribution queued and retry it on later drains; only genuine permanent failures (4xx or undecodable data) are marked failed. (#279)

## [0.12.1] - 2026-05-31

_Highlights: fingerprint-network contributions now upload far faster — a backlog drains in back-to-back batches instead of trickling out an hour at a time, and rate-limited uploads are retried instead of dropped. Plus a LAN-access fix so a dual-stack host no longer rejects its own requests._

### Changed

- **Faster fingerprint-contribution uploads** — the contribution uploader now drains its queue in back-to-back batches instead of sending one batch and then sleeping an hour, so a backlog (for example right after a bulk library bootstrap) clears promptly rather than over many hours. Rate-limit responses (HTTP 429) are now treated as transient and retried — honoring the server's `Retry-After`, capped so a misbehaving server can't stall uploads — instead of silently dropping the contribution. The steady-state idle poll interval also dropped from 60 to 15 minutes for quicker pickup of newly ripped episodes. (#276)

### Fixed

- **LAN access could reject the host's own requests on dual-stack (IPv6) binds** — with LAN access bound to all interfaces, the host's own loopback connection can arrive as the IPv4-mapped IPv6 address `::ffff:127.0.0.1`, which the localhost-only guard wrongly rejected with HTTP 403. Loopback is now classified via `ipaddress` (with an explicit IPv4-mapped fallback for Python < 3.13), so local requests are recognized correctly. (#273)

## [0.12.0] - 2026-05-30

_Highlights: a wider setup wizard with a dedicated Data Sharing tab and guided TMDB onboarding; the Import Watch Folder now handles libraries pointed straight at a show and flat folders with no season; and an ASR matcher fix that restores episode matches the new fingerprint-vector scale had started rejecting._

### Added

- **Data Sharing settings tab** — a dedicated tab in the setup/settings wizard now groups everything that sends data off your machine (the fingerprint network, AI assistance, and the gated TheDiscDB integration) in one place, separate from local Preferences. (#263)
- **Guided TMDB onboarding** — the TMDB token field shows instructional text instead of a token-shaped placeholder, validates automatically when you leave the field (with inline ✓/✗ feedback), and first-run setup no longer advances past the TMDB step until you've entered a valid token or explicitly chosen to continue without one. (#243, #263)

### Changed

- **Wider, collapsible config wizard** — the setup/settings modal is wider (800 → 1040px) and Preferences plus the new Data Sharing tab are grouped into collapsible sections, so the page starts compact and you expand only what you need. Inline action buttons that previously rendered as bare text ("Forget me", "Contribute from existing library") are fixed. (#263)

### Fixed

- **Import Watch Folder missed shows it was pointed at directly** — pointing the watch folder straight at a single show's folder broke ingestion: `Season NN` subfolders sat one nesting level too shallow to be detected (jobs were mislabeled with no show or season), and flat folders with no season matched nothing because the matcher requires a season. Engram now recognizes `Season N` / `Season 01` folders when the watch root *is* the show, and searches every candidate season for flat imports so they match across all seasons. (#264)
- **ASR episode matching rejected correct matches on known seasons** — a 30-second speech-recognition chunk scores a structurally low similarity against a full-episode reference vector, and the recent precomputed-vector migration lowered that scale further, so the old fixed similarity threshold rejected most correct chunks and returned no episode. Matching now uses a rank-and-margin vote (the top candidate must clear a low floor *and* lead the runner-up by a wide margin) and falls through to a full-file comparison when no chunk votes, restoring matches while still abstaining on out-of-corpus content. (#269)

## [0.11.0] - 2026-05-29

### Added

- **Bootstrap Library (bulk fingerprint upload)** — a one-pass tool that walks your existing organized TV library, extracts a Chromaprint acoustic fingerprint from every episode, and contributes them to the shared fingerprint network in bulk. Previously the network only grew as you ripped new discs; now shows you already own can seed matching immediately. Respects the same privacy model and opt-out as the per-rip contribution flow. (#253)
- **Bundled `fpcalc`** — the Chromaprint `fpcalc` binary is now shipped inside the Windows, Linux, and macOS builds, so audio fingerprinting works out of the box with no manual Chromaprint install. Development builds fetch it on demand. (#260)
- **Broadcast vs. DVD/streaming episode reordering** — episode organization now reconciles aired order with DVD/streaming order using TMDB episode groups, so shows that shipped in a different order than they aired land in the correct files. (#200, #254)
- **Global episode-ordering default in the Config UI** — pick DVD or aired ordering as the library-wide default directly from Settings, instead of per-show only. (#255, #259)

### Fixed

- **Startup crash `no such column: app_config.episode_ordering_preference`** — the pre-init LAN-address read queried `app_config` before the schema reconcilers ran, so a freshly migrated database crashed on launch. The read now tolerates schema drift. (#261)
- **Bulk fingerprint upload silently skipped episodes with certain codecs** — the bundled Chromaprint 1.5.1 `fpcalc` can't decode DTS, TrueHD, FLAC, or E-AC-3 audio, so ~128 library episodes failed fingerprinting with no warning. Engram now falls back to an `ffmpeg` pre-decode so every track can be fingerprinted. (#261)
- **"Queued contribution for title None" in the logs** — the show title is now persisted and logged, so bulk-upload progress is attributable per show. (#261)
- **Dashboard not refreshing after an in-app update, and incorrect frozen-build detection** — the UI now reloads after applying an update and correctly identifies packaged builds. (#258)
- **Long filenames and paths truncated in job history** — they now wrap instead of being cut off. (#256)
- **Cramped MANUAL row in the review inspector** — split into two rows so the manual-assignment controls are readable. (#257)

## [0.10.0] - 2026-05-28

### Added

- **Audio fingerprint contribution network** — Engram now extracts a Chromaprint acoustic fingerprint from every confidently matched episode and (opt-out) contributes it to a shared fingerprint server so episode matching improves for everyone. Privacy-first by design: contributions are gated behind a one-time consent disclosure, identified only by a regenerable per-install pseudonym (no filenames, paths, or IP addresses are sent), and a **"Forget me"** action wipes both the remote record and the local queue. A localhost-only audit endpoint plus a `~/.engram/cache/contribution_log.jsonl` log show exactly what left the machine. Configure under Settings → Fingerprint; requires the `fpcalc` binary (auto-detected, with a validation endpoint). (#242, #244, #248)
- **Bulk actions in the review queue** — select multiple review titles with checkboxes (with a select-all header and shift-click range selection) and apply one decision to all of them — **Mark as Extra · Discard · Skip · Re-Match** — then commit everything in a single Save. Built for box sets with dozens of unclassified extras that previously had to be cleared one click at a time. The batch commits in one organization pass, which also avoids the `FILE_EXISTS` collisions repeated single-title saves could hit. (#249)

### Fixed

- **`/review` page rendered as a solid black screen in Safari and Firefox** — two compounding causes: the REVIEW nav tab linked to a bare `/review` route that didn't exist (React Router rendered nothing, exposing the near-black body background), and Safari composited the page to black where `mix-blend-mode` atmosphere layers bled through their stacking context. The nav now deep-links to the first review job (with a `/review` → dashboard safety redirect), an `isolation: isolate` boundary contains the blend layers, and **Firefox + WebKit are now part of the Playwright matrix** to catch engine-specific regressions. (#247)
- **Movie extras showed the main feature's filename in job history** — bonus features were organized correctly on disk, but the history detail panel displayed the main movie's path for every extra. `organize_movie()` now returns a source→destination mapping so each extra shows its real `Extras/Extra N.mkv` path. (#245)
- **Movie review decisions didn't record the organized path** — after confirming a movie in the review queue, `organized_from`/`organized_to` were left unset on the title (the same class of bug as the extras path issue, but in the human-review path), so job history showed no destination. Both fields are now set and broadcast on a successful review organize. (#246)
- **Auto-flow finalize could mis-handle TV "extra" titles** — the automatic (non-review) TV finalize path passed the synthetic `"extra"` episode code straight to the episode organizer (which rejects it) and always cleared the `is_extra` flag, diverging from the review path. It now routes extras into the season's `Extras/` folder and preserves the flag, closing a latent inconsistency. (#250)

## [0.9.1] - 2026-05-27

### Fixed

- **Episodic TV with consistent audio never auto-organizing** — shows like *The Gilded Age* where every episode shares vocabulary (recurring characters, settings) produce a tight cosine-similarity band, so the old separation-based confidence formula scored 103/119 chunk votes at only 0.20 even though the winner was dominant. A new vote-ratio path fires when the winning episode has ≥ 3× the runner-up's chunk count and ≥ 50% consensus; it scores the match from the vote ratio directly, and the final confidence is `max(path1, path2)`. Gilded Age S03 now auto-organizes at 0.95+. (#239)
- **TrackGrid showed "no confident match" even when a strong best-guess existed** — REVIEW-state tracks displayed the static string *"no confident match — assign in review queue"* regardless of whether the matcher had a high-confidence episode stored in `matched_episode`. The display is now conditional: if a best guess is available it shows *"best guess S02E04 — 56% confidence (10/25 votes) — confirm in review queue"*; otherwise the no-match message is preserved. (#239)
- **Premature title-complete callback during ripping write pauses** — a brief MakeMKV write pause (buffering or disc seek) could hold a file's size constant for one 3-second polling interval, causing `_check_for_completed_files()` to fire at e.g. 96 MB of a 2 193 MB file. The title was immediately sent to MATCHING state while still being written. Now requires 3 consecutive stable polls (~9 s) before declaring a file complete mid-run; the force-complete path (called after `process.wait()`) bypasses the counter and fires once, at full size. (#237)
- **Windows `.exe` shipped with PyInstaller's default feather icon** — the multi-resolution Synapse v2 `engram.ico` (16–256 px) was already generated by `npm run brand:export` and committed, but the PyInstaller spec was never updated to reference it. The spec now points at `frontend/public/brand/app-icons/windows/engram.ico` so the packaged binary carries the correct brand mark. (#238)

## [0.9.0] - 2026-05-26

### Added
- **Import Watch Folder** — point Engram at any folder of pre-ripped MKV files (ARM output, NAS share, etc.) and it ingests them through the same identification → matching → organizing pipeline as a freshly ripped disc. Configured in Settings → Import Watch Folder; the watcher polls on a configurable interval and skips files still being written. (#233)
- **Auto-update** — Engram now checks for new releases at startup, silently downloads the update in the background, verifies the SHA256 checksum, and prompts with a banner to "Restart to apply." Updates can be skipped per-version. Works for frozen (packaged) builds on Windows, macOS, and Linux; no-ops in development mode. (#235)

### Fixed
- **TVsubtitles show mislabeling** — the resolver previously took the first search hit without verifying the show name, so e.g. "2 Broke Girls" could be matched to Gilmore Girls, corrupting the cache. Results are now filtered by title similarity before accepting a match. Adds `audit_subtitle_cache.py` and `verify_cache_content.py` scripts for cache health checks. (#202)
- **Docker image not published on release** — the `release.yml` dispatch in `tag-release.yml` was missing the `docker.yml` dispatch step, so Docker images were never pushed to GHCR on tagged releases. Also adds a manual `tag` input to `docker.yml` so a specific release can be re-published without re-running the full pipeline. (#231, #232)
- **CI permission error on `tag-release.yml`** — the `actions: write` permission required to dispatch `release.yml` and `docker.yml` was missing, causing workflow dispatch to fail with a 403. (#230)

## [0.8.1] - 2026-05-26

### Fixed
- **Docker MakeMKV install failing on every container start** — the version-detection script scraped the MakeMKV download page for a Linux tarball link (`makemkv-bin-*.tar.gz`) that is no longer listed there; switched to the hash-file link (`makemkv-sha-*.txt`), which is present on every release and uses the same bare version format. Adds a `MAKEMKV_DETECT_ONLY=1` mode for CI verification and a nightly full-compile check workflow to catch future regressions. (#226)

## [0.8.0] - 2026-05-26

### Added
- **LLM episode matching (opt-in)** — when audio fingerprint matching can't confidently identify a TV episode, an LLM compares the cleaned transcript against the season's TMDB synopses and suggests an episode through the review queue. Supports Gemini, Anthropic, OpenAI, and OpenRouter providers (Gemini Flash-Lite recommended); shares the existing `ai_provider`/`ai_api_key` settings. Never auto-organizes — always requires user confirmation. (#109)
- **Google Gemini provider** added to the AI provider list, usable by both AI title resolution and the new LLM episode matcher.
- **Docker / Linux container support** — official Docker image with a single-volume design (`/config` holds the database, logs, caches, and HF models). MakeMKV is compiled from source on first start to avoid redistribution restrictions; the stored MakeMKV license key is automatically seeded into MakeMKV's `settings.conf`. `docker-compose.yml` and full documentation included. (#193)
- **LAN access toggle** — opt-in setting in Preferences that binds the server to `0.0.0.0` so the dashboard is reachable from other devices on the local network. Settings panel shows the LAN URL, a copy button, and a QR code; a "restart to apply" notice appears until the socket is rebound. The `HOST` environment variable still takes precedence for Docker / headless deployments. (#211)

### Fixed
- **TV extras tagging after organize failure** — if organizing an extras track failed (e.g. destination already exists), the `is_extra` flag was silently dropped and the UI showed the track as an ordinary completed episode instead of marking it with an EXTRA chip. (#224)
- **Per-track deep re-match missing from inspector** — the v0.7 inspector redesign removed the per-track "Deep re-match" button for low-confidence titles; only the disc-level conflict re-match was preserved. Restored the per-track action and wired a `deep` flag through `RematchRequest` → `rematch_single_title` → matcher (stricter scan points and vote thresholds). (#224)
- **Auto-escalation never fired for needs-review titles** — `_maybe_escalate_conflicts` only escalated episode collisions, not titles routed to REVIEW. Added `_maybe_escalate_reviews` on the same 10 → 25 → full-coverage ladder; separate pass counters for conflicts vs. reviews prevent them from clearing each other (which previously pinned the ladder at pass 1). (#224)
- **Race condition on shared matcher temp files** — concurrent title threads writing `chunk_{start}_{dur}.wav` and `preprocessed_{stem}.wav` without a source-file disambiguator caused PyAV `InvalidDataError` when two threads sampled the same offset. Chunk and preprocessed paths now hash the canonical source path to keep them per-source. (#216)
- **Stale precomputed-cache manifest entries** — when `manifest.json` claimed coverage for a show/season whose `.npz` was missing, the fallback warning fired once per title and the in-memory manifest was never corrected, causing repeated spurious warnings. Stale entries are now pruned from the manifest on detection. (#216)
- **TF-IDF matcher reference-set reuse** — `TfidfMatcher` reused across calls with a different reference set (precomputed episode codes vs. scraped SRT paths) caused silent `KeyError` swallows that rejected every chunk. The matcher is rebuilt when its `reference_signature()` changes. (#216)
- **Config dropdowns unreadable on Windows** — native `<select>` elements render with the OS-controlled light background on Windows, making options invisible against the dark Synapse v2 theme. Replaced all 7 config `<select>` elements with a new `EngramSelect` component built on Radix UI, which renders the popup as React DOM and respects theme tokens.

## [0.7.3] - 2026-05-25

### Fixed
- **Ripping progress detection**: MakeMKV robot-mode output (`PRGC`/`PRGV`) was misread — the leading field is a message code (not a title index) and progress is `value/65536` (not `current/total`) — producing phantom "title 5018 is ripping" states and >100% per-title progress bars. The filesystem monitor (output-file sizes) is now the single source of per-title and overall progress; the stall-watchdog heartbeat is also fed from it (#209).
- **Redundant disc re-scans during ripping**: each title previously triggered its own `makemkvcon` invocation, re-opening and re-scanning the whole disc every time. Ripping now issues one `makemkvcon … all` pass for the full disc selection, falling back to individual re-rips only for any titles missing from that pass (#209).

### Changed
- **macOS Intel build dropped**: `llvmlite` 0.46.0 (December 2025) no longer ships macOS x86_64 wheels; building from source fails on Python 3.13. The `engram-macos-x64` release artifact is removed. Intel Mac users should use `engram-macos-arm64.tar.gz`, which runs transparently on Intel Macs via Rosetta 2.
- **Subtitle cache build speed**: seasons already covered on disk are skipped on each daily run — no TMDB, OpenSubtitles, or scraper calls — until a configurable freshness window (default 30 days) expires or `--refresh` forces a full re-harvest. Previously every season was re-attempted on each run regardless of prior coverage (#204).

## [0.7.2] - 2026-05-25

### Fixed
- **macOS frozen-build launch crash**: the packaged app now calls `multiprocessing.freeze_support()`
  before spawning workers, preventing an infinite fork-bomb on macOS where the spawn start method
  caused worker processes to re-execute the frozen entry point — opening endless browser windows and
  crashing immediately (#206).
- **macOS Intel binary mislabeled as x64**: `macos-latest` GitHub Actions runner is Apple Silicon,
  so prior releases shipped an arm64 binary as `engram-macos-x64.tar.gz` (Intel Macs received
  "bad CPU type"). CI now builds on `macos-13` (x64) and `macos-14` (arm64) separately (#206).
- **Python 3.14 incompatibility**: `requires-python` capped to `<3.14` as `onnxruntime` (via
  `faster-whisper`) has no cp314 wheel; backend Python pinned to 3.13 (#206).

### Added
- **macOS Apple Silicon download**: `engram-macos-arm64.tar.gz` is now published as a dedicated
  release artifact for M1/M2/M3/M4 Macs (#206).

### Changed
- **Hardened cross-platform smoke tests**: release builds assert binary architecture with `file`
  and a process-count guard (≤ 2 processes) catches re-spawn bugs headlessly; CI runs `uv sync`
  resolution across Python 3.11–3.13 on Ubuntu and macOS arm64 (#206).

## [0.7.1] - 2026-05-23

### Fixed
- **Frozen-build database upgrades**: the packaged app now drops columns removed from the model on startup, fixing a crash when inserting a disc (`NOT NULL constraint failed: disc_jobs.is_transcoding_enabled`) for users upgrading from a build that still had the removed "Enable transcoding" setting (#190).

## [0.7.0] - 2026-05-23

### Added
- **Pre-built subtitle cache**: ships a precomputed subtitle-vector cache so episode matching can run without scraping subtitle sites on every disc, falling back to live scraping only when a season isn't covered (#140). Cache builds are now resumable and log API status (#149), and the builder accepts a `--show-list` to target specific shows.
- **Smarter episode matcher**: persistent on-disk caches plus a threaded provider scheduler and reworked subtitle providers (#155), a per-provider circuit breaker so a failing source no longer stalls a run, interpretable 0–1 confidence scores (#169), and automatic deep re-matching when episodes conflict (#171). Match results now surface which subtitle provider contributed (#158).
- **Redesigned TV disc review**: an inspector-style layout with disc-level conflict detection, making it clearer which episodes clash before you commit (#165).
- **Diagnostics improvements**: bug reports can be previewed before sending and now report real installed tool versions (#174).
- **Resilient frontend**: API and WebSocket errors are handled gracefully with reconnection instead of breaking the dashboard (#180).
- **Brand refresh**: the canonical Synapse v2 brand system (#156), plus an ambient ripping animation and a bottom-anchored status bar (#137).

### Fixed
- **Ripping reliability**: the long-held database session in `_run_ripping` is now tightly scoped to avoid blocking other work (#185), and MakeMKV subprocesses are drained on shutdown alongside matching-lifecycle fixes (#181).
- **Movies**: long bonus tracks are no longer incorrectly flagged as needing review (#175).
- **Review flow**: the Process action returns to the dashboard instead of erroring (#173), and re-running a match re-matches all titles with live progress (#164).
- **MakeMKV validation**: the real installed version is detected from the robot-mode banner (#177).
- **Subtitle matching**: subtitle download is skipped when the precomputed cache already covers a season (#163); tvsubtitles episode resolution and candidate parsing were corrected (#159); UTF-16-encoded SRTs are now accepted; OpenSubtitles quota is reported accurately and skipped when exhausted.
- **Logging**: corrected log-source attribution and now surfaces disc-event errors that were previously silent (#168).
- **Security**: hardened SSRF and path-traversal sinks flagged by CodeQL (#147).

### Changed
- **Subtitle cache format v2**: ~85% smaller on disk via a compact `uint16` encoding (#154).
- **Documentation**: README reworked to be end-user-first with supporting docs consolidated (#162).
- Codebase-wide simplification sweep for maintainability (#143).

### Removed
- The unimplemented "Enable transcoding" setting (#138).
- The obsolete skyline-silhouette atmosphere layer (#139).

## [0.6.0] - 2026-05-02

### Added
- **OpenSubtitles.com REST API**: subtitle downloads now use the official `opensubtitlescom` REST API as the primary path (batch-downloads a whole season in one search call). Addic7ed and OpenSubtitles.org web scrapers remain as per-episode fallbacks. Configure API key, username, and password in Settings → TMDB & Subtitles.
- **Disc name identification via MakeMKV CINFO codes**: extractor now captures the disc display name from `CINFO:2` (e.g. `"Star Trek: Strange New Worlds - Season 3 (Disc 1)"`). When the volume label produces a failed TMDB lookup, the disc name is parsed and tried as a second-chance TMDB query — silently resolving merged-word labels like `STRANGENEWWORLDS_SEASON3` without any user prompt.
- **TMDB-failure review gate**: if both the volume label and disc name fail TMDB lookup for a TV show, the job now enters `REVIEW_NEEDED` state with the garbled name pre-filled in the correction modal (previously the job would silently start ripping with a wrong title).
- **NamePromptModal pre-fill**: when a job enters review due to an unreadable or merged-word label, the modal opens with `detected_title`, content type, and season number pre-populated — the user only needs to correct the show name.
- **Disc analyst static method** `_parse_disc_name()`: parses `"Show Title - Season N (Disc N)"` MakeMKV format into `(title, season)` tuple.
- 14 new unit tests in `tests/unit/test_disc_name_identification.py` covering extractor CINFO parsing, analyst disc-name parsing, identification coordinator fallback logic, and review gate behavior.

### Fixed
- **CINFO vs DINFO**: extractor was reading `DINFO:6` (which doesn't exist in MakeMKV robot-mode output) instead of `CINFO:2`. This meant the disc display name was never captured, so the TMDB disc-name fallback never fired for any disc.
- **Scraper timeouts**: Addic7ed and OpenSubtitles.org request timeouts reduced from 30 s to 8 s so failures are fast when those sites block requests.
- **Simulation service**: `insert_disc_from_staging` no longer crashes when `staging_path` contains paths with non-standard separators.

### Changed
- Subtitle download strategy: OpenSubtitles.com REST API is tried first (entire season at once); only falls back to per-episode scraping if credentials are absent or the API call fails.
- `SRT` validation (`is_valid_srt_file`) now deletes and re-downloads cached files that contain HTML (Cloudflare challenge pages) rather than surfacing them as valid subtitles.
- `opensubtitlescom>=0.1.0` added to backend dependencies.

## [0.5.0] - 2026-04-05

### Changed
- **JobManager decomposition**: broke up the 4,295-line `JobManager` (52 methods) into 5 focused coordinators + thin orchestrator (#58)
  - `IdentificationCoordinator` — disc scanning, DiscDB/TMDB/AI classification
  - `MatchingCoordinator` — episode matching, subtitles, file readiness
  - `FinalizationCoordinator` — conflict resolution, organization, review workflow
  - `CleanupService` — staging cleanup, timed cleanup, DiscDB export
  - `SimulationService` — all simulation methods for E2E testing
  - `JobManager` reduced from 4,295 to 1,166 lines
- **Alembic for database migrations**: replaced custom `_migrate_schema()` with Alembic for versioned, reversible migrations; existing databases auto-stamped on first startup (#58)
- **CORS origins configurable**: read from `CORS_ORIGINS` env var (via `Settings` model) instead of hardcoded localhost (#58)

### Added
- **WebSocket heartbeat**: server sends ping every 30s to detect and clean up stale connections (#58)
- **Accessibility improvements**: ARIA attributes and keyboard handlers on DiscCard, ReviewQueue, ConfigWizard, NamePromptModal (#58)

### Fixed
- **Memory leak**: `_episode_runtimes` and `_discdb_mappings` per-job caches now cleared on job completion/failure (#58)
- **Blocking event loop**: `DiscAnalyst` config loading switched from sync DB call to async preloading in async contexts (#58)
- **Sync engine churn**: `get_config_sync()` now caches the sync SQLAlchemy engine instead of creating one per call (#58)
- **O(n²) loop**: `has_selection` check in `_run_ripping` hoisted out of inner loop (#58)
- **Heartbeat deadlock risk**: heartbeat closes socket directly instead of calling `disconnect()` to avoid lock contention with `broadcast()` (#58)

### Removed
- Unused frontend dependencies: `@mui/material`, `@mui/icons-material`, `@emotion/react`, `@emotion/styled`, `react-router` v7 (#58)

## [0.4.5] - 2026-04-04

### Fixed
- **Multi-drive cancel isolation**: canceling one drive's rip no longer kills another drive's rip — `MakeMKVExtractor` now tracks processes per job (#64)
- **Elapsed time 1-hour offset**: replaced deprecated `datetime.utcnow()` with `datetime.now(UTC)` across all backend files; frontend appends `Z` suffix to naive timestamps (#61)
- **Catalog-number volume labels**: labels like `BBCDVD1550` are now detected as publisher catalog codes and trigger the name prompt when TMDB/DiscDB lookups fail (#62)

### Added
- **Season selector in episode review**: users can now pick season S01–S20 in the TV review UI instead of being locked to the auto-detected season (#63)
- 5 new multi-drive integration tests: concurrent ripping, cancel isolation, drive removal isolation, mixed content, dual identification (#65)
- Catalog number detection unit tests

### Changed
- Bumped GitHub Actions: `actions/setup-node` v4→v6, `astral-sh/setup-uv` v4→v7, `actions/setup-python` v5→v6

## [0.1.9] - 2026-02-22

### Fixed
- Discs with generic Windows volume labels (e.g. `LOGICAL_VOLUME_ID`, `VIDEO_TS`, `BDMV`) no longer produce spurious TMDB search results and wrong detected titles
- TMDB name overrides are now guarded by a Jaccard word-token similarity check (≥ 35%); completely unrelated TMDB matches are discarded and the parsed disc name is preserved
- Jobs where the disc name cannot be detected now enter `REVIEW_NEEDED` state instead of attempting to rip with an unknown title

### Added
- **Name Prompt Modal**: when a disc label is unreadable, a cyberpunk-styled modal prompts the user to enter the title, media type (TV/Movie), and season number before ripping begins
- `POST /api/jobs/{job_id}/set-name` endpoint to resume a stalled job after the user provides a name and content type
- `review_reason` field on `DiscJob` model to communicate why a job entered review state (SQLite migration: `ALTER TABLE disc_jobs ADD COLUMN review_reason TEXT`)
- `backend/scripts/migrate_db.py` utility script for applying future schema migrations to an existing database
- 9 new unit tests covering generic label detection and TMDB similarity guard

## [0.1.8] - 2026-02-22

### Fixed
- CI/CD failures: formatting, lock file sync, and cross-platform test compatibility

## [0.1.7] - 2026-02-22

### Fixed
- TMDB classifier bug causing incorrect content type detection

## [0.1.6] - 2026-02-22

### Fixed
- Multiple tracks showing RIPPING state simultaneously
- Per-track ripping progress stuck at 0% during real disc rips
- Movie review workflow, config wizard key visibility, and review page overhaul
