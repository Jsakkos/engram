# Changelog

All notable changes to Engram will be documented in this file.

## [Unreleased]

### Fixed

- **Disc auto-detection no longer breaks after the first rip on containerized installs (TrueNAS, Docker).** In some container environments `/sys/block/sr0/size` freezes at the previous disc's block count after ejection and never drops to zero. This caused two compounding bugs: a ghost FAILED job fired on every cycle (spurious "inserted" event from the frozen sysfs), and the next real disc insertion was silently ignored until the container restarted. The sentinel now verifies disc presence via `ioctl(CDROM_DRIVE_STATUS)` when sysfs reports a non-zero size, querying drive firmware directly. (#477, thanks @katelovescode!)

## [0.22.1] - 2026-06-29

_Highlights: two UI polish fixes — the bug-report icon no longer causes a layout shift on hover, and selecting a `Season NN` folder in the import picker is now identified correctly._

### Fixed

- **Bug report icon no longer causes a layout shift on hover.** The icon was rendered inside `ActionButtons`, which appears after `StateIndicator` in the flex row, so revealing it on hover pushed all buttons rightward. It now lives before `StateIndicator` in the card, with reserved space via an opacity toggle, so the layout stays stable. (#465, thanks @katelovescode!)
- **Manual import now identifies a picked `Season NN` folder correctly.** Selecting a season folder directly (e.g. `…\Seinfeld\Season 4`) created a job titled "Season 4" with no season and content type "unknown", because the scanner only handled picking a show folder or a parent-of-shows folder. It now recognizes a `Season NN` pick, taking the show name from the parent folder and the season from the folder's own name (and organizes in place beside the original show folder rather than nesting a second copy inside the season). (#474)

### Changed

- **Frontend dependency updates** — @radix-ui/react-scroll-area 1.2.3→1.2.12, @radix-ui/react-collapsible 1.1.3→1.1.14, @radix-ui/react-slot 1.1.2→1.3.0, @playwright/test 1.60.0→1.61.1. (#468, #470, #471, #472)

## [0.22.0] - 2026-06-28

_Highlights: manual media import replaces the background watch folder with a user-triggered folder picker, headless Linux server variants for NAS and Raspberry Pi, Docker multi-arch (amd64 + arm64) with properly versioned GHCR tags, and a new auto-eject toggle._

### Added

- **Manual media import replaces the background watch folder.** Click **IMPORT** in the top bar, browse to a folder on the server, preview the discovered seasons and their file counts, then start one import job per (show, season). The new scanner walks `Disc/` subfolders transparently and infers the season from any `Season NN` path segment, fixing two reported bugs: `Show / Season / Disc / *.mkv` trees that previously imported nothing, and partial imports that required an app restart to re-scan. The import watch path and destination mode are repurposed as last-used defaults for the picker. (#463)
- **Settings toggle to disable automatic disc ejection.** A new "Auto-Eject Disc When Ripping Completes" checkbox in Settings → Preferences → Maintenance & watchdog controls whether Engram ejects the disc when ripping finishes. Enabled by default (preserving existing behavior); disable to keep the disc loaded for manual verification or re-ripping without re-inserting. (#457)
- Headless server build variants for Linux: `engram-linux-x64-headless` and `engram-linux-arm64-headless`. These skip browser auto-launch and default `allow_lan_access` to `True` on first run so the dashboard is reachable from another machine without any manual configuration. Intended for NAS, Raspberry Pi, Jellyfin server, and other headless deployments. (#460)
- Docker image now publishes both `linux/amd64` and `linux/arm64` layers, and GHCR releases now carry four versioned tags (`v0.22.0`, `0.22.0`, `0.22`, and `latest`) for Renovate Bot pinning. (#460, #462)

## [0.21.12] - 2026-06-23

_Highlights: two small fixes — auto-detected extras stay visible in review so you can reassign them before they are filed, and the settings button now uses the universally-recognized gear icon instead of a sun/starburst._

### Fixed

- **Auto-detected extras are no longer filed away mid-rip and frozen in the review panel.** They now stay with the rest of the disc's tracks and are organized at the end, so when a disc goes to review you can reassign a misdetected extra (e.g. a dual-episode track) to an episode before it is filed. (#449)
- **Settings button now shows a gear icon instead of a sun/starburst.** The previous `IcoSettings` icon drew evenly-spaced radial spokes — the universal symbol for brightness controls. It has been replaced with the standard Lucide gear (rounded 8-tab outer shell with a center bore), which is universally recognized as a settings trigger. (#451)

## [0.21.11] - 2026-06-23

_Highlights: disc-hash fingerprint identification is now on by default for all installs, and the Reassign Track dropdown correctly shows the disc's actual season._

### Added

- **Disc-hash fingerprint identification is now enabled by default** — the fingerprint network's disc-hash identification path (`GET /v1/identify-disc`) now runs automatically for every install, including existing ones (migrated via Alembic). A `canonical` or `confirmed` match pre-assigns episodes with high confidence; `candidate`-tier hits are not applied as an override. Previously this path was gated off while the shared catalog was being seeded. The catalog is now large enough to be useful. (#445)

### Fixed

- **Reassign Track episode dropdown now uses the disc's actual season.** When reassigning a track on a completed multi-season disc from the History detail panel, the episode picker was pinned to Season 1 (`S01E01`, `S01E02`, …) because it derived the season from the track's current match instead of the job's identified season. It now uses the job's detected season, so a Season 2+ disc correctly offers `S02E…` episodes. (#447)

## [0.21.10] - 2026-06-22

_Highlights: TheDiscDB ContentHash lookups are fixed after a server-side schema change that was silently causing every hash query to fail, plus dependency maintenance and subtitle-cache refresh._

### Fixed

- **TheDiscDB ContentHash lookups no longer fail with "Unexpected Execution Error"** — TheDiscDB restructured their GraphQL schema to insert a `ReleaseDisc` junction between `Release` and `Disc`. Our filter walked `releases.some.discs.some.contentHash`, which still passed schema validation but threw an execution error because the junction's `contentHash` property is not mapped to a SQL column. The filter now traverses the junction correctly (`disc { contentHash }`). In production this caused every hash-based lookup to fall through to name/TMDB matching, which could misfire badly (an Avatar: The Last Airbender disc matched Green Book, for example). (#444)

### Changed

- **Subtitle seed list pruned and refreshed** — removed 159 non-English and zero-disc shows that were consuming harvest quota with no benefit to English-language matching, and added 8 US/UK classics. (#437)
- **Frontend dependency updates** — react-hook-form 7.55→7.80, react-router-dom 7.17→7.18, @radix-ui/react-checkbox 1.1→1.3, @radix-ui/react-select 2.1→2.3, @radix-ui/react-switch 1.1→1.3. (#439, #440, #441, #442, #443)

## [0.21.9] - 2026-06-22

_Highlights: completed jobs are no longer locked — you can now reassign a misfiled track to the correct episode, mark it as an extra, or discard it directly from the History detail panel, and the fingerprint network self-corrects when a contributed fingerprint is retracted and re-submitted._

### Added

- **Reassign a track on a completed job from the History detail panel.** Open any completed job in History and reassign a misfiled track to a different episode, mark it as an extra, or discard it. Engram moves the organized library file to its new home (extras and discards both land in the show's `Extras/` folder) and, when the track's audio fingerprint was already contributed to the shared network, retracts that erroneous fingerprint and re-submits the corrected one as a user-verified match. The fingerprint network heals by re-deriving the episode's consensus from the remaining contributions. Previously a completed job's assignments were locked, so a single mismatched track could block a later disc with a duplicate-file conflict, and there was no way to correct a contributed fingerprint after the fact. (#435)

## [0.21.8] - 2026-06-21

_Highlights: Engram now ships a native ARM64 build for NVIDIA Jetson and other aarch64 Linux devices, with the auto-updater delivering the correct binary per architecture._

### Added

- **ARM64 (Linux aarch64) build for NVIDIA Jetson and other aarch64 Linux devices** — releases now include `engram-linux-arm64.tar.gz`, built natively on a GitHub-hosted arm64 runner and validated by the same smoke test, TLS self-test, and bundled-`fpcalc` checks as the x86_64 build. ASR runs on the CPU out of the box (the PyPI `ctranslate2` aarch64 wheel has no CUDA build); GPU-accelerated transcription on a Jetson is an opt-in on-device step that compiles CTranslate2 against the device's JetPack CUDA toolkit (`backend/scripts/jetson_gpu_setup.sh`, documented in `docs/development/jetson.md`). The auto-updater now distinguishes x86_64 from aarch64 so each host downloads the matching bundle. (#433)

## [0.21.7] - 2026-06-21

_Highlights: TheDiscDB lookup is now on by default — Engram checks the community disc database during identification and uses its episode mappings as a low-confidence fallback, while audio matching (ASR) remains the primary and preferred signal._

### Added

- **TheDiscDB lookup is now enabled by default** — when a disc's content hash matches an entry in TheDiscDB, Engram now uses the community-contributed title-to-episode mapping as a fallback when audio-recognition confidence is low (below 0.5). ASR always runs first and wins when confident; a DiscDB mapping steps in only when the audio match is weak. Previously, a DiscDB hit would short-circuit ASR entirely and stamp 0.99 confidence using disc-order numbering — which frequently disagreed with aired order and produced worse results than the audio match. Contribution of new disc layouts remains gated off by default. (#430)

## [0.21.6] - 2026-06-19

_Highlights: two disc-handling fixes — the Identify prompt no longer pops a blocking modal over a disc that is already ripping, and box-set TV discs now pin their TMDB lookup to the TV namespace instead of picking a fuzzy movie match._

### Fixed

- **The "Identify Disc" prompt no longer pops a blocking modal over a disc that is already ripping** — when a disc rips first with an open identity question (an unreadable label, or a title TMDB couldn't match), the prompt used to auto-open as a full-screen modal whose "Start Ripping →" button and "Awaiting Input" status made it look like the rip was blocked — so it was easy to hit **Cancel Job** and kill a healthy rip. The rip now stays visible with a **Name this disc** button on the card instead; the prompt only auto-opens once the rip finishes and the job is waiting for you. When the prompt is opened, its wording now reflects reality — it saves the title rather than "starting" a rip that's already underway, shows the actual reason TMDB couldn't match (instead of always claiming the label is unreadable), and reads "Ripping" while the disc is still ripping. (#424)
- **Box-set TV discs now resolve to the TV series instead of a look-alike movie** — a disc whose title carries a season or box-set subtitle (for example "Avatar: The Last Airbender Book One: Water") strips down to the series name for its TMDB lookup, but that stripped name often matches *both* the real series and a fuzzy, sometimes more-popular movie. Engram used to pick the movie, then discard its id as cross-namespace noise — leaving the disc with no `tmdb_id`, forcing an Identify prompt, and failing subtitle download. For a disc the label already says is TV, Engram now pins the lookup to the TV namespace and scores name similarity against the title it actually matched, so the canonical series wins (e.g. Avatar resolves to TMDB TV id 246), subtitles prefetch, and no prompt appears. The guard that protects against a genuine wrong-namespace movie id (the Mad Men "Two Madmen" case) is unchanged. (#425)

## [0.21.5] - 2026-06-18

_Highlights: the in-app update restart button no longer incorrectly blocks when a job is parked in Needs Review or when dismissed cards are still in the database._

### Fixed

- **Update restart no longer false-fires "disc operation in progress" for parked or dismissed jobs** — two bugs caused the restart button to refuse even when no disc I/O was happening: `REVIEW_NEEDED` was counted as a blocking state even though the disc is already out and the job is waiting for user input, and jobs dismissed from the dashboard (`cleared_at` set) were not excluded from the active-job check, so an invisible ghost card could permanently block the button. Both are now fixed. (#422)

## [0.21.4] - 2026-06-18

_Highlights: disc titles that carry a season or box-set subtitle in their AI-guessed name now resolve on TMDB correctly instead of stalling on an identity prompt._

### Fixed

- **Disc titles carrying a season or box-set subtitle now find their TMDB match** — when the AI resolved a title like "Avatar: The Last Airbender Book One: Water", the over-specified name matched nothing on TMDB, leaving the job without a `tmdb_id` and forcing an identity prompt while subtitle download failed. Engram now strips the trailing Book/Volume/Part/Season qualifier before the TMDB lookup, so the disc identifies cleanly and episode matching proceeds without interruption. (#420)

## [0.21.3] - 2026-06-16

_Highlights: two disc-handling fixes — a disc whose label Engram can't quite confirm now rips immediately instead of blocking, and re-ripping a stalled track no longer misattributes a finished track as a duplicate._

### Fixed

- **A disc Engram can't quite confirm now rips first instead of waiting on you** — when TMDB finds the show but nothing on the disc corroborates the name (for example a label like `DS9S3D2 ok`, where a stray annotation broke the match), the disc used to stop and ask you to confirm the title *before* ripping anything. It now rips immediately and carries a **Confirm title** prompt you can answer any time — or it pools into the single Needs Review visit at the end — restoring the walk-away workflow for this case. Engram also strips common trailing rip annotations (`ok`, `done`, `rip`, …) from disc labels so these discs usually identify cleanly with no prompt at all. (#414)
- **Re-ripping a stalled track no longer duplicates another episode or hides the re-ripped track** — when a track stalled and you reinserted the disc (or used manual re-rip), the re-ripped track could be stamped with a *different* already-ripped track's filename. It then matched that other track's episode, conflict resolution flagged the duplicate, and the original track was bounced to Needs Review — so the dashboard showed a duplicate of the first track and the re-ripped one seemed to vanish. The re-rip now ignores the disc's other finished files sitting in the staging folder (rather than re-detecting them as freshly ripped and mis-assigning them), and a file is only ever matched to the track it actually belongs to. This also closes a latent risk where a *second* stall during a re-rip could delete the episodes you'd already ripped. (#415)

## [0.21.2] - 2026-06-15

_Highlights: subtitle-cache harvesting is now hardened against two real-world OpenSubtitles data quality problems that caused entire TV seasons to match at "no clear vote" confidence and route to Needs Review._

### Fixed

- **Subtitle providers that return one episode's dialogue under a different episode's code no longer corrupt the cache** — some providers re-time an episode's subtitle and save it under a different episode slot (e.g. DS9 S02E05 was a re-timed copy of S01E05 "Babel"). Two episodes then held byte-identical dialogue in the TF-IDF index, the matching scores scattered, and both episodes ended up in Needs Review. The harvester now hashes each downloaded subtitle's cleaned dialogue, detects cross-episode duplicates before they enter the cache, removes the contaminant file, and marks its slot as not-found so it can be re-fetched cleanly. On the affected DS9 Season 2, exactly the 12 contaminated episodes were flagged and cleared. (#407)
- **OpenSubtitles results mislabeled to the wrong episode are now rejected before they reach the cache** — OpenSubtitles returns multiple candidates per episode number, and for some shows the metadata is wrong (e.g. DS9 Season 2 subtitles are tagged `season_number=1`, and within-season numbering is offset by the Emissary two-part pilot so "Episode 10 - Move Along Home" was returned for a request whose S01E10 is "The Nagus"). The harvester took the first candidate, saving the wrong episode's dialogue under the right code. Each candidate is now cross-checked against the requested episode's TMDB title (and episode numbers outside the season's real range are rejected outright); the harvester saves the first *valid* candidate per episode, falling through to the scraper sources when no valid OS result exists. On live DS9 data, the real Season 1 subtitles now harvest correctly — The Passenger at S01E08, Move Along Home at S01E09 — and that episode's audio now matches at 0.347 vs 0.058 runner-up where it previously scored ~0.067 scattered and went to review. (#407)

## [0.21.1] - 2026-06-15

_Highlights: a fixes-focused release that sharpens TV disc identification — fan-style abbreviated labels (e.g. `DS9`) now resolve to the full show name, a legitimately feature-length episode is no longer mistaken for a "Play All" track and discarded, and a disc whose identity can't be corroborated goes to Needs Review instead of completing under a guessed name._

### Changed

- **Multi-disc box sets identify a little faster** — ripping several discs of the same season no longer re-fetches identical episode-runtime data from TMDB once per disc; the runtime lookup is now cached for the life of the app, trimming redundant network calls during identification. (#404)

### Fixed

- **Abbreviated TV disc labels now resolve to the right show name** — a disc whose label is a fan-style abbreviation (e.g. `DS9` for *Star Trek: Deep Space Nine*) used to keep the raw label as the show name even when TMDB had identified the series correctly, filing episodes under a name like `DS9S1D1`. Engram now matches the abbreviation against the TMDB title — including number-words, so `DS9` corroborates "Deep Space **Nine**" — and adopts the proper show name. (#403)
- **Feature-length episodes are no longer discarded as a "Play All" track** — a double-length episode such as a 90-minute pilot whose runtime happened to equal the combined length of the other episodes on the disc could be mistaken for a "Play All" concatenation and skipped. Engram now checks the expected episode runtimes from TMDB before flagging a long title, so a legitimate long episode is kept and filed (and the disc still classifies as TV rather than a movie). (#403)
- **Discs whose identity can't be confirmed now go to review instead of completing under a guessed name** — when TMDB's show name can't be corroborated by anything on the disc, the job is sent to Needs Review with the suggested title to confirm or correct, rather than silently organizing files under the raw disc label. (#403)

## [0.21.0] - 2026-06-13

_Highlights: the walk-away workflow — drop a disc and walk away. Ripping now starts immediately even when Engram has a question about the disc (an unreadable label, an unknown season, or two same-named shows): the question rides along on the job card as a button you can answer any time, or pools into a single Needs Review visit at the end, instead of stopping the disc before it rips. Re-matching is dramatically faster thanks to a persistent on-disk transcript cache and background pre-transcription while a disc waits in review — and, with the opt-in fingerprint network, a disc the community has already mapped is recognized instantly by its content hash, pre-assigning every episode and skipping audio matching entirely._

### Added

- **Drop a disc and walk away — ripping now starts immediately even when Engram has a question about the disc** — an unreadable label, a box-set disc that doesn't reveal its season, or two same-named shows used to stop the disc in review *before* ripping, so a headless-server user visited the dashboard twice: once at insert to answer, and again at the end for episode review (the exact friction raised in the follow-up discussion on #370). The disc now rips first, and the open question rides along on the job card as a button — **Name this disc**, **Select season**, or **Confirm title** — you can answer any time. Answer during the rip and matching picks up seamlessly with zero stops; ignore it and the disc finishes ripping, then pools the question into one Needs Review visit at the end. The season question usually answers itself: matching searches across seasons automatically, and once two episodes agree on a season the disc is pinned to it and the prompt quietly disappears. (#370)

- **Episode re-matching no longer re-runs speech recognition** — Whisper transcripts are now kept in a persistent on-disk cache keyed by the exact file, audio offset, and speech-recognition model, so re-matching a track — after a review decision, a "Wrong title?" re-identification, a "Re-match", or even a full app restart — reuses the transcription work it already did instead of grinding through it again. Re-matches that used to take minutes now take seconds. The cache prunes itself, and a re-ripped file simply gets fresh entries, so stale transcripts are never served. (#397)
- **Background pre-transcription while a disc waits in review** — while a job sits in Needs Review, Engram now quietly transcribes its unresolved tracks in the background (politely yielding to any live matching, and stopping the moment you act on the job), so by the time you pick an episode or fix the show identity the re-match is near-instant. On by default with a **Background Pre-Transcription** toggle in Settings → Preferences, plus an optional **Pre-Transcribe Entire Files** mode for setups that often hit the expensive full-file fallback. (#397)
- **Discs the community has already mapped are recognized instantly — no audio matching needed** — when the optional fingerprint network is enabled, Engram now looks a disc up by its content hash before ripping. If enough people have already contributed the same pressed disc with the same title→episode mapping, Engram identifies it and pre-assigns every episode straight away, so the whole disc skips speech-recognition matching entirely (a network-confirmed disc still verifies its episodes by audio as usual). And, with the same opt-in consent that already covers audio-fingerprint sharing, completed discs contribute their own layout record (the disc's content hash plus how its titles map to episodes — release-level data, never filenames or anything personal, tied only to your rotating pseudonym and erasable via the existing forget control) so the next person to insert that disc gets it for free. A disc that Engram itself identified from the network is never re-contributed, so the catalog can't confirm itself. (#399)

### Changed

- **Deep re-match passes now build on each other instead of starting over** — the escalating "deep re-match" scan depths are aligned to a nested grid of audio offsets, so a deeper pass re-visits exactly the chunks a shallower pass already transcribed plus new ones in between; combined with the persistent transcript cache, each escalation only pays for the new offsets. A requested scan depth now equals the realized depth, and the final escalation tier no longer runs a redundant deeper pass on typical-length episodes. (#397)

### Fixed

- **Disabling GPU acceleration now fully applies to episode matching** — the matcher could still select CUDA for its transcription model after GPU ASR was turned off (or when the CUDA libraries weren't usable), because it probed the GPU hardware directly instead of honoring the device resolved at startup. It now follows the same startup-pinned device as the rest of the app — including the cached-transcript identity — so "GPU off" means matching genuinely runs on the CPU. (#397)

## [0.20.0] - 2026-06-11

_Highlights: completed disc cards now summarize their contents at a glance — TV cards show the season and matched episode range, movie cards show the year — instead of repeating the raw volume label; plus a fix for a ripping card that could visibly flicker between "ripping" and "matched" on slow or dirty discs._

### Added

- **Completed disc cards now summarize what's on the disc instead of repeating the raw volume label** — a finished card's subtitle used to show the unprocessed disc label (e.g. `TV • ARRESTED_DEVELOPMENT_S1D1`), which is hard to scan in the Done list. TV cards now show the season and matched episode range (e.g. `TV · S01 E01–E08`), listing each season separately for multi-disc sets that span seasons and appending a count when some episodes are missing from the library; movie cards show `Movie · <year>`. The raw label still appears on its own line for traceability, and active cards (ripping, matching) are unchanged. (#382)

### Fixed

- **A ripping track no longer flickers between "ripping" and "matched/idle"** — when MakeMKV paused mid-track for a few seconds (common on slow or dirty discs), Engram mistook the brief lull in file growth for the track being finished, handed the still-ripping file to the matcher, and then the progress monitor kept yanking it back to "ripping" as the rip resumed — so the card visibly bounced between the red ripping state and the green matched/idle state for the rest of the rip. A track is now only treated as finished once MakeMKV has provably moved on to the next title (or the rip process exits), so a mid-track write pause can no longer be misread as completion. (#381)

## [0.19.0] - 2026-06-11

_Highlights: Settings becomes a jump-anywhere section list with deep-linkable controls instead of a replay of the first-run wizard, alongside a broad UI/UX polish pass — full-width layouts on large screens, AA-legible secondary text, fixed transparent dropdowns, and safer modal dismissal — plus the shipped subtitle cache now heals itself when a reference episode is missing instead of silently sending the track to Needs Review._

### Changed

- **Settings is a jump-anywhere section list instead of a replay of the setup wizard** — opening Settings from the gear used to reopen the first-run wizard verbatim (titled "Setup Wizard", a linear five-step stepper that always started on step 1 with no hint the steps were clickable), so changing one preference meant knowing which step and collapsible hid it. It now opens as **Settings** with a section sidebar you can move through freely, and individual settings are deep-linkable: the "GPU available →" status badge now opens Settings directly on the GPU acceleration control — it previously dropped you on the unrelated Library Paths step — and the "TMDB not configured" warnings open straight to the TMDB token field. First-run onboarding is unchanged. (#388)

### Removed

- **The Library tab has been removed** — it was a poster-grid view over the same completed-jobs data the History page already shows, so it only added navigation clutter with no unique functionality. History remains the single place to browse your finished discs, and any bookmarked `/library` link now redirects there. (#374)

### Fixed

- **A TV box-set disc whose label happens to match an obscure movie is no longer misidentified — and the phantom show name is gone** — a disc labeled like `MADMEN3` fuzzy-matched an unrelated TMDB *movie* ("Two Madmen"), and because a movie's id points at a completely different show in TMDB's TV namespace, Engram both sent the disc to review every time *and* showed a baffling "No episodes found for O Hristos xanastavronetai Season 3 on TMDB" banner — which survived even after you corrected the title to Mad Men. Engram now re-resolves identity from the cleaner disc title MakeMKV reports ("Mad Men Season 3") whenever a season-labeled disc matches a movie, so these discs auto-identify with no review; it never carries a movie's id onto a TV job (a cross-namespace id that poisoned the subtitle and roster lookups); and subtitle-download errors are now recorded on a clearable field, so they no longer linger as a stale banner after a successful re-download. (#392)
- **A missing reference subtitle no longer silently sends an episode to Needs Review** — when the shipped subtitle-vector cache was missing a single episode (e.g. Mad Men S02E05), Engram skipped the subtitle download *and* matched against the incomplete set without ever noticing the gap, so the affected episode dead-ended in review with no match and an unreliable AI guess. Engram now compares the cache against the canonical episode list, fetches only the missing episode(s) when they can be obtained, and grafts a fetched subtitle into matching at runtime so it actually identifies the track. Any episode it still can't get a reference for is now flagged on the Review page's season roster (a warning glyph + hatch on the slot, plus a summary line) instead of failing silently — because subtitles are the source of truth for matching. Dropping the missing `.srt` into the subtitle cache also now makes the episode matchable on the next run.

- **The dashboard and Review page now actually use your screen** — a flex-layout subtlety made every page's content shrink-wrap to its cards instead of filling the intended width, leaving a narrow centered column with huge dead margins on large monitors (the Review page left the entire right half of a 1920px screen empty). Content now fills out to the designed maximum, with the recovered space going to the track grid and the review Inspector — which also takes the larger share of the review layout, since it's the page's main workspace. Below ~1100px (e.g. a window snapped to half of a 1920px monitor) the dashboard's side rail now folds away instead of crushing the job cards until titles truncated to a single letter.
- **A disc already in the drive no longer auto-rips during first-run setup** — on a fresh install with a disc inserted, the pipeline fired the moment the backend started, scanning and ripping into the default (unconfirmed) staging path while the setup wizard was still on screen. The disc is now parked until setup completes: the dashboard shows a "Disc detected — finish setup to start ripping" banner, and finishing the wizard picks the disc up automatically — no eject/reinsert needed. Already-configured installs are unaffected.
- **Settings dropdowns are no longer transparent** — every styled dropdown (conflict resolution, episode ordering, staging cleanup, watchdog) rendered its open menu with no background, so the options were unreadable over the text behind them. The whole Synapse color-token set was silently failing to reach hand-written CSS (a Tailwind `@theme inline` subtlety), which also left the settings modal with a white frame instead of the brand cyan; both are fixed at the token layer so all 51 affected style rules recover at once.
- **Pressing Escape (or clicking outside) the "Identify Disc" prompt no longer cancels the job** — the universal dismiss gestures were wired to job cancellation, so an absent-minded Escape or a misclick on the backdrop destroyed the job (the same applied to the season prompt). Dismissing now just closes the prompt and parks the disc in review — the card's actions and the Review page remain available — and cancelling requires the explicit button, now labeled "Cancel job". A dismissed prompt also stays dismissed instead of re-opening on the next status refresh.
- **The "Identify Disc" prompt no longer pops over the dashboard while you're busy** — a disc with an unreadable label (or an unknown season) used to auto-open a blocking modal the instant the status poll noticed it, stealing focus from whatever you were doing, such as watching another disc rip. The prompt now only opens on its own when that disc is the *only* active job — so inserting one disc and walking away still leaves the prompt waiting for you — and stale completed cards don't count against that. When something else is in progress, the disc's card instead shows a prominent "Name this disc…" / "Select season…" button that opens the prompt on demand (also the way back in after dismissing it).
- **The REVIEW tab is disabled when nothing needs review** — it previously linked to the dashboard, silently navigating you away (and triggering React duplicate-key warnings in the nav). It now sits inert with a "No jobs awaiting review" tooltip until a review exists.
- **The "TMDB not configured" warning appears once, not on every card** — the dashboard banner and an identical per-card alert rendered simultaneously (four copies with three discs on screen). Cards now only warn about job-specific TMDB degradation, such as a rejected key.
- **Compact view cleanup** — rows showed raw internal state names (`review_needed`) instead of the formatted labels used everywhere else, truncated "REVIEW NEEDED", and offered no way to resolve an identity review (only Cancel). States are now formatted from the same source as the badges, and identity-review rows get a "Fix title" action.
- **Setup wizard polish** — the Next button no longer jumps from bottom-left (step 1) to bottom-right (steps 2+); detected tool versions read "MakeMKV v1.18.3" / "ffmpeg 7.1" instead of raw probe output like the full FFmpeg copyright banner or "(version probe timed out)"; and the ORGANIZING dashboard badge has its own library icon instead of reusing the matching glyph.
- **TMDB token onboarding no longer fails silently** — the setup wizard's TMDB field showed `eyJhbGci…` as placeholder text, which is the literal opening of *every* TMDB Read Access Token, so an empty field looked already-filled and users could finish setup having never entered a token — then spend ages debugging silent matching failures. The placeholder is now plain instructions, the token auto-validates as soon as you leave the field (inline ✓/✗ — no "Test Token" hunt), and first-run setup won't pass the TMDB step until the token validates or you explicitly choose to continue without it. The dashboard also shows a dismissible warning while no TMDB token is configured, and an active disc whose classification fell back to heuristics because the key is missing or rejected now says so on its card ("TMDB API key not configured — classification ran in heuristic-only mode") instead of silently degrading. Token validation also now tells "couldn't reach the validation service" apart from "token rejected", so a backend hiccup no longer reads as a bad token. (#243)
- **Identifying a disc no longer briefly freezes the dashboard and other jobs** — disc identification (and the "Wrong title?" re-identify) made blocking TMDB network calls directly on the async event loop, so while one disc was being looked up, live progress updates, other in-flight jobs, and drive detection all stalled until the lookup returned. Those lookups now run off the event loop, keeping the UI and concurrent jobs responsive during identification. (#377, #379)
- **A disc with a bad sector that keeps writing slowly no longer wedges the job** — when MakeMKV crawls through damaged sectors, writing small bursts every 30–60 seconds, the "incomplete rip" detector could never accumulate 90 consecutive seconds of file-size stability (each burst reset the counter), so the job stayed stuck until the hours-long outer timeout fired. The grace window is now measured from the *last time the file grew* rather than from consecutive stable polls, so intermittent slow writes no longer restart the clock — detection fires within 90 seconds of MakeMKV's final write, regardless of how erratically it wrote before.
- **Dim secondary text is now legible, and the magenta accent is back to meaning "action"** — small grey labels and values that carry real information (timestamps, file paths, per-track details, table cells, form help and status text) used a tertiary grey that fell below the WCAG AA contrast minimum; they're now a brighter grey that passes AA, while purely decorative "telemetry" text keeps its ambient dimness. Separately, magenta — reserved for actively-ripping things and the primary action of a flow — was trimmed where it had crept in as decoration: the History "TV seasons" distribution bar and the disc-identify modals' Movie/TV toggle now use the brand cyan/amber palette, and those modals' Cancel buttons are now neutral so the primary action carries the weight. (#384)

## [0.18.0] - 2026-06-10

_Highlights: a damaged track can now be recovered without re-ripping the whole disc — clean the disc, reinsert it, and Engram re-rips just that title — and box-set discs that don't reveal their season now prompt you to pick one up front instead of dead-ending every episode in Needs Review._

### Added

- Recover a single damaged track without re-ripping the whole disc. When a title fails at the rip level (a scratch/bad-sector truncation or a ripping stall), Engram now holds it in review with a "clean the disc and reinsert to re-rip this title" prompt. Reinserting the **same** disc (verified by its content fingerprint) automatically re-rips just that track, re-matches it, and finishes the job — with a manual "Re-rip this title" button and a bounded automatic-retry cap as fallbacks. (#371)

### Changed

- A disc with an unrecoverable track no longer auto-completes with that track silently failed; it now waits in review until the track is re-ripped or explicitly skipped, so a "completed" job means every title succeeded. (#371)

### Fixed

- **Discs that don't reveal their season no longer dead-end in review** — box-set discs labeled by disc number only (e.g. `Eureka D3`) identified the show but not the season, and Engram then silently skipped downloading the reference subtitles that episode matching depends on. Every episode failed at 0% confidence and the whole disc landed in Needs Review. Engram now asks up front: as soon as the disc is identified, a prompt appears to pick the season — or choose "All Seasons" to let matching search every season — and everything downstream (subtitle download, matching, review) works normally. Shows with only one season skip the prompt. (#370)
- **The misleading "different same-named show" suggestion no longer appears when reference subtitles never arrived** — with no subtitles to match against, zero matches says nothing about the show being wrong, but Engram suggested re-identifying as a same-named twin (e.g. *Eureka! (2022)* for a *Eureka (2006)* disc). The review reason now explains the real problem — no reference subtitles were available — and what to do about it, and Engram no longer burns multi-minute deep re-match passes against an empty subtitle library. (#370)
- **The review screen gains a season picker for jobs whose season is still unknown** — manual episode assignment was locked to Season 1 codes (the dropdown in the bug report's screenshot); the picker now loads any season's episode list, with episode names, for assignment. Two smaller gaps closed along the way: naming a disc through the identify prompt now also starts the reference-subtitle download (that path previously never downloaded any), and a subtitle lookup that finds nothing is no longer cached for the rest of the run, so a retried download becomes visible to re-matches. (#370)
- **Discs awaiting a "Wrong title?" correction no longer show a dead-end "Review needed" button** — when a disc matched multiple same-name shows on TMDB (e.g. "The Office"), the card showed a yellow "Review needed" action button next to an identical "REVIEW NEEDED" status badge, but that button only opened an empty episode-review queue because the show identity wasn't settled yet. The episode-review button is now hidden while a disc's identity is unconfirmed, leaving the corrective "Wrong title?" action (and an explanatory banner) as the clear next step. (An earlier attempt only covered discs with no enumerated titles, which never happens for this case since titles are listed at scan time.)
- **The Re-Identify ("Wrong title?") modal now shows the release year and TMDB id** — after you pick a show from the search results (or the "Did you mean?" quick-pick), the modal confirms the exact match as `Name (year) · TMDB #id` so you can tell same-name shows apart, and it now also shows what the disc is *currently* identified as for comparison.
- **The History detail panel now shows the release year alongside the show/movie name** — the job-detail drill-down in History displayed only the name (or a bare TMDB id) for a disc's committed identification, because the backend exposed `tmdb_year` on the job list/by-id response but not on the detail response. The detail panel now reads `Name (year)`, matching the Re-Identify modal, so same-name titles (e.g. Frasier 1993 vs 2023) are distinguishable in history too. (#358 follow-up)
- **Processing many jobs at once no longer crashes with a database timeout** — importing several seasons while another disc is ripping could exhaust Engram's database connection pool (`QueuePool limit of size 5 overflow 10 reached, connection timed out`), which stalled episode matching and the dashboard. The connection pool is now sized for that concurrency (and waits politely for SQLite's single-writer lock instead of failing with "database is locked"), and episode matching no longer keeps a database connection open while it looks up episode runtimes over the network. The same pool sizing and polite-wait now also cover the separate synchronous database connections that background worker threads use to read settings (during matching, organizing, and subtitle downloads), so those can't exhaust their own pool under the same import load. Takes effect after a backend restart.
- **The dashboard now shows "Organizing" while files are moved to your library, instead of staying stuck on "Matching"** — when all of a TV disc's episodes matched automatically, Engram moved the files from the staging drive into the library (which can take a while over a network share) while the card still read "Matching", with no sign of what was happening. The card now switches to an **Organizing** state with an "Organizing N of M" progress bar as each episode lands; a single-file movie shows an indeterminate "Organizing to library…" indicator. (Movies and the post-review path already did this — this closes the gap on the common auto-match path.) As part of the fix, the long file-move no longer holds a database connection open, the stall watchdog no longer force-advances a job mid-move, and a job interrupted by a restart while organizing is now resumed on the next launch instead of being marked failed. (#359)
- **A disc with a bad sector no longer leaves the whole job stuck for hours** — when MakeMKV aborts a title partway through because of an unreadable spot on the disc (an "uncorrectable" read error, e.g. a scratch), the partly-ripped file sits on disk far smaller than expected. Engram kept waiting for that file to "finish", leaving the job stuck on **Matching** for hours before finally giving up. Engram now recognizes a ripped file that has stopped growing well below its scanned size as an incomplete rip within about 90 seconds, sends just that title to review with a clear "incomplete rip — clean the disc and re-rip" explanation, and lets the rest of the disc's episodes finish normally. While Engram waits for a freshly-ripped file to finalize, the track now shows as **Queued** instead of a misleading spinning **Ripping** badge.
- **Discs with run-together volume labels are now filed under the correct name and show their cover art** — a disc whose volume label has no separators between words (e.g. `BREAKINGBADS2`) was organized into a folder like `Breakingbad` and showed no poster, even though Engram had already identified it as *Breaking Bad* on TMDB. The matched TMDB title is now treated as the authoritative name whenever it's corroborated by something on the disc itself — the volume label (compared ignoring spaces and punctuation) or MakeMKV's human-readable disc name (e.g. `Breaking Bad: Season 2: Disc 1`), which is now always consulted rather than only as a last resort — so the show is filed under its proper name and season. The dashboard's cover art is also fetched by the resolved TMDB id instead of re-searching by the (sometimes garbled) name, so the poster now appears reliably. Re-rip the disc to apply the corrected naming.
- **Inserting the next disc of a season is no longer rejected as a duplicate** — every disc in a season set shares one volume label (e.g. all of Breaking Bad Season 2 read as `BREAKINGBADS2`), so inserting Disc 2 while Disc 1 was still matching made Engram treat it as the same disc lingering in the drive and silently drop it. Engram now fingerprints each disc by its content hash, so two discs that share a label are told apart: a genuinely different disc is picked up right away, while a re-read of the same disc is still correctly ignored.

## [0.17.0] - 2026-06-08

_Highlights: episode matching can now run on an NVIDIA GPU (opt-in, with the CUDA runtime downloaded on demand) for dramatically faster transcription — and the dashboard's ASR badge now reports the device transcription actually runs on instead of claiming CUDA while silently falling back to the CPU._

### Added

- **GPU acceleration for episode matching (NVIDIA, opt-in)** — Engram can now run Whisper transcription on an NVIDIA GPU instead of the CPU, which is dramatically faster for episode matching. Because the required CUDA libraries (cuDNN + cuBLAS) are ~1.2 GB, they are not bundled into the download; instead a new **GPU Acceleration** control in Settings → Matching detects your GPU and downloads the runtime on demand (one time, into `~/.engram/` so it survives app updates). Supported on Windows and Linux with an NVIDIA GPU; macOS and AMD GPUs continue to run on the CPU (the engine has no GPU path there). Takes effect after a backend restart.

### Fixed

- **The ASR status badge no longer claims "CUDA" when it's actually using the CPU** — on a machine with an NVIDIA GPU, the dashboard badge showed `ASR: CUDA …` based purely on the GPU being *present*, even though the compiled build had no CUDA math libraries and silently fell back to the CPU for every match. The badge now reports the device transcription actually runs on, and when a GPU is available but unused it shows an actionable `CPU · GPU available →` chip that opens the new GPU setting.

## [0.16.3] - 2026-06-07

_Highlights: a fixes-only release — the Windows "Restart to update" now runs to completion and actually installs the update, and the review panel's AI/LLM episode-matching button works again and clearly reports what it found._

### Changed

- The review Inspector's "Try LLM match" endpoint now returns a differentiated `reason` (e.g. `ai_disabled`, `not_configured`, `no_season`, `show_not_found`, `transcription_failed`, `no_match`, `llm_error`) and uses HTTP 503 for retryable operational failures (matcher/transcription/LLM-provider errors) and 500 for unexpected errors, instead of reporting every failure as a 200. This lets the UI tell "AI matching is off / couldn't find a match" apart from "the LLM provider failed, retry". (#347 follow-up)

### Fixed

- **The review panel's episode-ordering picker now offers only Aired and DVD order** — the per-show ordering selector in the review queue could show extra buttons (digital, story-arc, production, TV) whenever a show happened to have those alternative orderings on TMDB, even though the global Episode Ordering setting only ever offered Aired and DVD. The two surfaces now agree: both offer just Aired and DVD. The other orderings are still recognized internally but no longer selectable, and matching, history, and the fingerprint network remain on canonical aired numbering as before. (#348)
- **The "Try LLM match" button in the review Inspector now works** — clicking it on a track in review did nothing useful: the backend failed instantly on a wrong internal import (it looked for an `episode_curator` that doesn't exist), caught the error, and returned a silent success, so the button appeared to do nothing and the failure only showed up in the logs. AI-assisted single-track matching from the review panel now runs the transcription and returns a suggestion as intended. (#347)
- **The review screen's "Try AI match" button now tells you what happened** — clicking *Try AI match* on a title in review runs AI episode matching in the background (often 1–3 minutes), but the button gave no signal: while it ran nothing changed on screen, and if the AI found no confident match or hit an error the screen silently refreshed and showed nothing. The button now shows a "Matching…" state while it runs, and reports the outcome right in the Inspector — the suggested episode on success, or a clear notice otherwise ("No confident AI match found." or "AI match failed — check the server log."). (#349)
- **"Restart to update" on Windows now runs to completion — for real this time** — even after the 0.16.1 fix, the in-app updater could still leave you on the old version with the app simply vanishing. The helper that swaps in the new build waits for the app to close using a command pipeline that *silently hangs forever* when run without a console window — which is exactly how the helper was launched — so it never reached the step that copies in the update. The helper now runs with a hidden console so that wait works, gives up after ~10 seconds instead of hanging indefinitely, and on any failure rolls back to your working version and tells you in the app (with a "download manually" link) rather than disappearing. On a successful update you get a brief "you're now on vX" confirmation, and restarting no longer opens a second browser tab. (#351)

## [0.16.2] - 2026-06-05

_Highlights: a fixes-only release — AI episode matching works again on Google Gemini, multi-season imports now match every episode correctly instead of dropping them into review, and tool detection no longer stalls the Settings screen on a busy optical drive._

### Fixed

- **Tool detection no longer hangs the Settings/first-run screen on a busy optical drive** — the "Detect tools" check (which runs on the dashboard and in the Config Wizard) read MakeMKV's version by briefly spinning up the drive, and on a slow or busy drive that probe could block the whole check for up to ~20 seconds — so FFmpeg and fpcalc, which were already detected, sat waiting on it before anything showed up. MakeMKV is now reported as found (with its path) without waiting on the slow version read, the version probe is given a short budget and degrades to "version probe timed out" instead of stalling, and a hard per-tool deadline guarantees one slow tool can never gate the others. Server startup and the diagnostics report get the same short budget, so neither stalls on a busy drive either. (#343)
- **AI episode matching now works with Google Gemini** — when the AI provider was set to **Gemini**, AI-assisted episode matching silently never produced a suggestion: every request to Gemini was rejected because the structured-output schema Engram sent used a format Gemini's API doesn't accept (a field allowed to be "an object or nothing" was encoded in a way only some providers understand). Gemini answered every call with a "bad request" error, which Engram caught and treated as "no AI result," so affected tracks fell through to manual review with nothing to show for it — making AI matching look completely broken on Gemini. Engram now translates the schema into the form Gemini expects before sending, so Gemini returns real episode suggestions again. Other providers (Anthropic, OpenAI, OpenRouter) were unaffected. (#344)
- **Releases no longer fail on a transient tool-detection timeout** — the release smoke test probed the bundled-tool detection endpoint with a single 5-second request, and on a cold Windows runner the first execution of the freshly-extracted `fpcalc`/`ffmpeg` binaries (antivirus scan + cold process start) could exceed that and fail the whole release even though the build was fine — this is what stopped the v0.16.1 binaries from publishing. The smoke test now retries the probe with a warmup and verifies the bundled `fpcalc` is present in the build tree directly, so a transient timing hiccup can no longer sink a release. (#342)
- **Parallel episode matching no longer matches tracks against the wrong season's references** — the parallel transcription added in 0.16.0 let several tracks match at once, but they all shared a single matcher that held just one "current season" set of reference subtitles. When two tracks from different seasons matched concurrently — e.g. importing a multi-season **Seinfeld** collection, where each season is its own job and they run side by side — one track's references were swapped out from under it mid-match, so it compared its audio against the *other* season's episodes, found nothing, and fell back to a slow whole-file pass that also failed, dropping the track into **Needs Review** with no match and no voting feedback. The matcher now keeps a separate, reusable reference set per season, so concurrent matches can't clobber each other. In a real 7-season Seinfeld run this took episodes that were landing in review at 0 votes back to confident, correct matches (10/10 votes) — with full per-track voting restored and no needless whole-file fallback. (#345)

## [0.16.1] - 2026-06-05

_Highlights: a fixes-only release — "Restart to update" on Windows now runs to completion and actually installs the update, and importing a TV-season folder with a generic label (like `Season 3`) now resolves the show and matches its episodes instead of dropping the whole season into Needs Review._

### Fixed

- **"Restart to update" now actually applies the update on Windows** — the crash-safe swap added in 0.15.3 (copy aside, verify, atomic rename, roll back) was correct, but it never ran to completion: the helper that performs the swap was launched with your install folder as its working directory, and Windows won't rename a folder while it's any program's current directory. So the very first rename failed, the updater rolled back, and you were left on the old version with no visible error — every time. The helper now moves to a neutral working directory before the swap (and is spawned from one), so the rename succeeds and the new build is installed. The `~/.engram/update_helper.log` now also records the helper's working directory and each step's exit code, so any remaining edge case is diagnosable from a single log. (#338)
- **Imported TV seasons with generic folder labels now match instead of sending every episode to review** — when you import a pre-ripped folder like `…\Seinfeld\Season 3\`, Engram reads the show and season from the folder names, but the import's disc "label" is just `SEASON_3`, so the show was never looked up on TMDB and the job had no TMDB id. Since reference subtitles are now stored per-TMDB-id, a missing id sent the matcher hunting for references in a folder that didn't exist (`…/cache/data/Seinfeld` instead of `…/cache/data/1400`), found none, and dropped every track into **Needs Review** with no real match — and, for the same reason, the review panel showed no episode list and bonus features weren't sorted as Extras. Imports (and discs you name by hand) now resolve the show's TMDB id from its name *before* matching, so references are found, episodes match, extras are tagged, and the review panel shows the full season. Genuinely ambiguous same-name shows — e.g. *Frasier* (1993) vs the 2023 revival, where the folder has no year to tell them apart — still pause for you to pick the right one. (#340)

## [0.16.0] - 2026-06-05

_Highlights: episode matching now transcribes multiple tracks in parallel — the **Max Concurrent Matches** setting finally drives real concurrency (auto-clamped to your CPU or GPU), with a live ASR backend badge on the dashboard, and the "Matching" count is now honest about which tracks are genuinely transcribing._

### Added

- **Episode matching now transcribes multiple tracks in parallel** — the dashboard could show several tracks as "Matching" at once while only one actually made progress and the CPU sat mostly idle. The speech-recognition model was running one transcription at a time no matter what, so the **Max Concurrent Matches** setting didn't really do anything. That setting now drives how many episodes are transcribed simultaneously (on CPU or GPU), automatically clamped to your hardware (CPU cores, or a GPU limit) so it can't oversubscribe and slow things down. A small **ASR badge** on the dashboard shows the active backend — e.g. `ASR: CUDA · float16 · 4w` or `ASR: CPU · int8 · 8w`. (#336)

### Changed

- **The dashboard's "Matching" count is now honest** — tracks waiting for a transcription slot now show **QUEUED** and flip to **MATCHING** only when a real worker actually starts on them, so the number of tracks shown as "Matching" reflects what's genuinely transcribing rather than overstating progress. The **Max Concurrent Matches** setting takes effect after a backend restart (noted in Settings). (#336)

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
