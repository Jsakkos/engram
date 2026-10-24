# Decision brief: ship a built-in TMDB key? (#732)

Date: 2026-10-24. Status: recommendation for the owner; nothing implemented.
Related: #732, epic #734 (1.0 targeted end of 2026), #243 (TMDB onboarding, closed).

## Recommendation

**Adopt option 2 (a built-in project key with a user override), shipped the way the TheTVDB key already is, and keep option 1 as the explicit fallback.** Do not build option 3 for 1.0.

Why, in short:

1. Every comparable desktop or self-hosted media app we could verify ships a default TMDB key and lets users replace it (Jellyfin, tinyMediaManager, Kodi). Radarr avoids the question with its own metadata server. None of them require each user to register.
2. TMDB's terms do not forbid it. The Terms of Use say nothing about embedding or sharing keys; the binding constraints are non-commercial use, attribution, a 6-month cache cap, and "no excessive use".
3. The risk is revocation, and Engram can survive it: the override path stays, the current "Continue without TMDB" degradation already works, and a rotated key can reach existing installs without a release if we add a small fetched-key fallback (step 6 below, optional).
4. Option 3 buys control we do not need yet, at the price of an always-on service whose outage would break identification for every user. Keep it as the escalation if the shared key is ever throttled or revoked.

Two things the owner must do regardless of option: register the project key under TMDB's non-commercial tier, and add the TMDB attribution notice, which **Engram does not currently display anywhere** (a grep for the required wording finds nothing in the repo).

## 1. What TMDB allows

Sources: [API Terms of Use](https://www.themoviedb.org/api-terms-of-use), [API FAQ](https://developer.themoviedb.org/docs/faq), [rate limiting](https://developer.themoviedb.org/docs/rate-limiting). Read 2026-10-24.

| Question | Finding |
|---|---|
| Embedding a key in a distributed app | The Terms contain no rule on sharing or embedding keys. The FAQ does not cover it either. So it is neither granted nor prohibited; it rests on TMDB's discretion. |
| Commercial use | Free for non-commercial use. "Commercial" means the primary purpose is to create revenue; that needs a written agreement. TMDB decides this at its sole discretion. Engram is free and open source, so it qualifies, but **any future paid tier, hosted Engram, or donation-gated feature should be re-checked.** |
| Attribution | Show the TMDB logo (less prominent than your own branding) and the notice that the product uses the TMDB API but is not endorsed or certified by TMDB. Required whichever option we pick, and today missing. |
| Caching | TMDB data may not be cached beyond 6 months. Engram keeps a persistent TMDB cache at `~/.engram/cache/tmdb_cache.sqlite`; confirm its TTL is under 6 months before 1.0. This applies under every option. |
| Rate limits | No fixed numbers in the Terms. The rate-limit page says upper limits "sit somewhere in the 40 requests per second range", may change, and that clients must respect HTTP 429. It does not say whether the limit is per key or per IP. The old 40 per 10 seconds limit was disabled in 2019. |
| Shared-key implication | If limits are per IP (which the docs do not rule out), every home install has its own address and one shared key costs nothing extra. If per key, ~40 req/s is a very large budget for a tool that makes a handful of lookups per disc; Engram retries with backoff and caches (`lru_cache` plus the persistent cache). The unknown is TMDB's "excessive use" judgment, not a published number. |
| Revocation | TMDB may terminate the license at any time for any reason and may change or restrict access without notice. On termination you must stop using the keys and delete cached content. A leaked or abused shared key can be disabled and nothing in the Terms limits that. |

Precedent for the revocation risk: a 2017 [Kodi forum thread](https://forum.kodi.tv/printthread.php?tid=311689) reports the bundled scraper key being revoked over misuse by piracy sites, then reissued and rebuilt into the scrapers (I could not re-fetch the thread, which returned 403; the claim comes from the search excerpt, so treat the details as unverified).

## 2. What comparable apps do

| App | Approach | Evidence |
|---|---|---|
| Jellyfin | Ships a hardcoded default key; the plugin config has an optional override field that is "intentionally excluded from the settings page", falling back to the built-in constant when empty. | [`TmdbUtils.cs` line 35](https://github.com/jellyfin/jellyfin/blob/master/MediaBrowser.Providers/Plugins/Tmdb/TmdbUtils.cs), [`PluginConfiguration.cs`](https://github.com/jellyfin/jellyfin/blob/master/MediaBrowser.Providers/Plugins/Tmdb/Configuration/PluginConfiguration.cs), [`TmdbClientManager.cs` line 51](https://github.com/jellyfin/jellyfin/blob/master/MediaBrowser.Providers/Plugins/Tmdb/TmdbClientManager.cs). Verified by reading source. |
| tinyMediaManager | Built-in key by default; an API key field lets users enter their own. | [tMM scraper docs](https://www.tinymediamanager.org/docs/scrapers). |
| Kodi scrapers | Built-in keys in the scrapers (and the revocation history above). | [Kodi forum](https://forum.kodi.tv/printthread.php?tid=311689), via search excerpt. |
| Radarr | No TMDB key on the client at all: requests go to its own metadata server, `https://api.radarr.video/v1/{route}` (option 3). | [`RadarrCloudRequestBuilder.cs`](https://raw.githubusercontent.com/Radarr/Radarr/develop/src/NzbDrone.Common/Cloud/RadarrCloudRequestBuilder.cs). Sonarr uses the same SkyHook pattern (not separately verified). |
| FileBot | Client code takes an API key and throttles itself to 35 requests per 10 seconds (older limit). The maintainer has said per-user keys are not worth a config dialog while the major sources do not require them, implying a bundled key. Current behavior not confirmed. | [FileBot forum](https://www.filebot.net/forums/viewtopic.php?p=32197), [source mirror commit](https://code.moparisthebest.com/moparisthebest/filebot/commit/23df26fad4bef15a77e808e7ea7d5c97a9ef7eef). |

Pattern: local-first apps ship a default key with an override (option 2); apps with a large central service run a proxy (option 3). Nobody in the set makes users register as the only path. Note that the Jellyfin key is committed in public source, which tells us TMDB has tolerated an openly extractable key for years. Engram would not need to commit its key (see below).

## 3. Engram today

- **Storage and read path.** `AppConfig.tmdb_api_key` (`backend/app/models/app_config.py:63`), edited through `ConfigUpdate` and `ConfigWizard`, redacted as `"***"` in `GET /api/config`, with a separate `tmdb_configured` boolean for the banner (#243).
- **Many direct readers.** About 15 backend sites read `config.tmdb_api_key` directly: `matcher/tmdb_client.py` (most functions, each guarded by `if not ...tmdb_api_key`), `identification_coordinator.py` (lines ~1179, 1767, 1929, 1950, 2097, 2125), `job_manager.py:4759`, `organizer.py:904`, `tmdb_poster.py:28`, `episode_ordering_service.py:40`, `episode_namespace_service.py`, `curator.py:699`, `routes.py` (several, including `/tmdb/search` which 400s when unset), and `matcher/testing_service.py`. Any option 2 implementation must resolve the effective key in one place, not patch each site.
- **Token type.** The setting takes a v4 Read Access Token (long `eyJ...` JWT). `_tmdb_auth` sends it as a Bearer header when longer than `_V4_TOKEN_MIN_LEN`, else as a v3 `api_key` param. A built-in key can be either; the Bearer token is the one TMDB's current docs lead with.
- **Degradation without a key.** `identification_coordinator.py` pre-sets `tmdb_degraded_reason = TMDB_DEGRADED_NOT_CONFIGURED` when no key is stored and flips to `TMDB_DEGRADED_AUTH_FAILED` when a lookup is rejected (`TmdbAuthError`). Classification falls back to the Analyst heuristics. No `tmdb_id` means the frontend derives `identityReview` true (`types/adapters.ts`, `isIdentityUnresolved`), so every review job becomes an identity review and the dashboard shows the permanent warning. The wizard's "Continue without TMDB" button (`ConfigWizard.tsx` ~line 1338) sets `tmdbContinueAnyway`. This is the path a revoked built-in key would fall into, so it is already the right failure mode.
- **Precedent in this repo.** `TVDB_API_KEY` is injected at build time: `release.yml` passes `${{ secrets.TVDB_API_KEY }}` to every `pyinstaller engram.spec` step (6 build jobs), `engram.spec` (~lines 147 to 158) writes a gitignored `backend/hooks/rthook_tvdb_generated.py` that does `os.environ.setdefault('TVDB_API_KEY', ...)`, and `tvdb_client.resolve_api_key` prefers a `tvdb_api_key` config override, else the env var. Absent locally, the feature is simply off.

## 4. Option 2 in detail

**Shipping the key.** Copy the TVDB mechanism for a `TMDB_API_KEY` secret: same runtime hook (generalize `rthook_tvdb_generated.py` into one generated hook carrying both), the Docker image gets it as a build arg or secret baked into an env default at build time, never committed. Dev checkouts and forks without the secret behave exactly like today (option 1).

**Precedence.** Add `resolve_tmdb_key(config)` mirroring `resolve_api_key` for TVDB: stored user token if non-empty, else `TMDB_API_KEY` env. Route every reader through it (ideally have `get_config()` consumers use one accessor so the ~15 sites change mechanically). User override always wins, so a power user can opt out of the shared key.

**Keep the secret out of the UI and exports.** `GET /api/config` must not echo the built-in key; `tmdb_configured` should become true when either source exists, and the settings page should say "Using Engram's built-in key" with a field to override. Check `config_transfer` (settings export) so the built-in key is never written to an export file.

**Rotation without a release.** The embedded value is fixed at build time, so rotation by default means a new release. Cheaper option: on revocation (`TmdbAuthError` from the built-in key), fetch a replacement from a small static file the project already knows how to host (for example a `badges`-style branch artifact or the existing subtitle-cache release channel), signed or at least pinned to a host allowlist like `is_safe_remote_url`. This is optional and only needed if a revocation actually happens; it is not a 1.0 blocker.

**How much of a secret.** None. Any binary or image can be unpacked and the key read from the process environment or a `strings` pass; PyInstaller bundles are not obfuscation. Treat it as a project identifier, as Jellyfin and tMM do. The build-secret route only avoids committing it to git history and keeps forks from inheriting it. The practical consequences: expect someone to reuse it eventually, keep the key's registered use description honest (non-commercial, open source), and be ready to rotate.

**Failure behavior if disabled.** Built-in key revoked: `TmdbAuthError` already maps to `TMDB_DEGRADED_AUTH_FAILED`; update that message to say the built-in key was rejected and point to the override. No data loss, same as today's no-token path.

## 5. Option 3 (Engram-run proxy) cost

The owner already runs the fingerprint server and a subtitle-cache server on 192.168.1.122 (nightly publishing timer), so the building blocks exist. Still, a TMDB proxy differs in kind from those:

- **Always on, in the critical path.** Fingerprint and subtitle caches are optional accelerators with graceful fallbacks. Identification without TMDB is the degraded mode, so the proxy becomes a single point of failure for first-run classification. A home-LAN host (192.168.1.x) would need public ingress, TLS, DDNS or a tunnel, and uptime commitments.
- **Abuse surface.** An unauthenticated public endpoint that forwards to TMDB needs rate limiting per client, request-shape allowlisting, and logging decisions with privacy implications (see `docs/development/fingerprint-privacy.md` for the project's current stance).
- **Terms exposure.** A proxy that serves TMDB data to many clients starts to look like redistributing the API ("do not sell, lease, or sublicense API access or content") and concentrates all traffic under one key and IP, raising the "excessive use" risk that option 2 spreads across user networks. It would also need a cache that honors the 6-month cap.
- **Ongoing work.** Monitoring, a second deploy pipeline (the fingerprint server's `main` auto-deploys), and being the on-call for a service the product depends on.

Rough effort: 2 to 4 days to a working proxy, then permanent ops overhead. Not justified before 1.0 when option 2 reaches nearly the same user outcome.

## 6. Implementation steps if adopted

1. Register a dedicated TMDB application for Engram (non-commercial, open source description); store its Read Access Token as the `TMDB_API_KEY` Actions secret. Owner action.
2. Add `resolve_tmdb_key(config)` (user token, else `TMDB_API_KEY` env) and route all ~15 direct readers through it; unit-test precedence and the "neither set" path.
3. Generalize the PyInstaller runtime hook in `engram.spec` to inject both keys; pass `TMDB_API_KEY` in all six `release.yml` build steps and the Docker build; confirm `.gitignore` covers the generated hook.
4. `GET /api/config`: report `tmdb_configured` true for either source, add `tmdb_key_source: "user" | "builtin" | "none"`, never return the built-in value; verify config export/import skips it (tests under `test_config_transfer*.py`).
5. Wizard and settings: drop TMDB as a required onboarding step when a built-in key exists (show "Using Engram's built-in key", optional override field). Keep the validation flow and the "Continue without TMDB" path for builds without a key.
6. Attribution: add the TMDB logo and the required "not endorsed or certified" notice (About/Settings and docs), mirroring `TvdbAttribution.tsx`.
7. Confirm the persistent TMDB cache TTL is under 6 months.
8. Update `TMDB_DEGRADED_AUTH_FAILED` copy and docs (`docs/troubleshooting.md`, `docs/deployment/docker.md`) and the CLAUDE.md "TMDB Configuration" section.
9. Optional, only on a real revocation: signed fetched-key fallback for rotation without a release.
10. Contact TMDB support once to confirm the embedded-key model is acceptable and ask whether limits are per key or per IP. The Terms are silent, so a written answer is the cheapest way to de-risk 1.0.

## Open questions for the owner

- Are donations or any paid feature planned that could make TMDB consider Engram commercial?
- Is a one-email check with TMDB (step 10) worth doing before committing?
- Is rotation-without-release (step 6/9) wanted up front, or only after a revocation?

## Caveats on this research

TMDB's key-sharing and per-key versus per-IP behavior are not documented, so those points are inferences. Radarr's metadata host and Jellyfin's constant were read from source; the FileBot and Kodi details come from search excerpts and are weaker. Sonarr was not verified directly.
