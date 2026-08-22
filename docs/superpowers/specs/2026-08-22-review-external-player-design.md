# External Player Handoff for Manual Review

**Date:** 2026-08-22
**Status:** Approved design, ready for implementation planning

## Problem

When a ripped track lands in manual review, the operator has to decide which episode it is
using only indirect evidence: duration, chapter count, ASR vote counts, subtitle coverage,
and LLM suggestions. When that evidence is ambiguous, the only way to settle it is to watch
the file, which today means leaving the dashboard, finding the staging directory in a file
manager, and locating the track by its MakeMKV output name.

The goal is to make "look at the actual video" a one-click action from the review UI.

## Why not an embedded player

MakeMKV is a lossless remuxer; it never re-encodes. A staged file therefore carries exactly
what the disc held:

| Source | Video | Audio |
|---|---|---|
| DVD | MPEG-2 | AC3 |
| Blu-ray | H.264 or HEVC | DTS, TrueHD, AC3 |

Browsers reject this on three independent grounds: MKV is not a supported container in
Chrome, Firefox, or Safari; MPEG-2 and DTS/TrueHD/AC3 are not supported codecs; and HEVC
support is hardware-conditional. A `<video src="...mkv">` element fails on effectively all
real Engram content.

Embedding VLC itself is not available either. The VLC Web Plugin was NPAPI and ActiveX;
Chrome removed NPAPI in 2015 and Firefox in 2017, and VideoLAN discontinued the plugin with
no replacement. Browser extensions cannot register a decoder with a `<video>` element.
WebCodecs exposes only codecs the browser already ships, so it adds control but never new
formats. A WebAssembly decoder (ffmpeg.wasm) is the sole technical path to new codecs
in-browser, and it means a ~30 MB download performing software-only decode of
Blu-ray-bitrate video with no hardware acceleration.

That leaves two viable designs: transcode server-side with ffmpeg, or hand the file to a
native player. This design chooses the handoff.

### Why the handoff over transcoding

- **No new dependency.** ffmpeg is a configured, auto-detected tool (`AppConfig.ffmpeg_path`,
  `detect_ffmpeg()` in `app/api/validation.py`) but it is **not bundled**:
  `backend/engram.spec` ships `fpcalc` only. A transcoding feature would be unavailable to
  every user without a system ffmpeg, or would require bundling roughly 80 MB.
- **No process lifecycle.** No subprocess spawning, reaping, temp segments, or cleanup.
- **Full fidelity.** The native player gets every audio track, subtitle track, and chapter
  marker, with hardware decoding.
- **Deployment-neutral.** The player fetches over HTTP, so it works identically whether the
  browser is on the backend host or another machine on the LAN. A design that spawned VLC
  server-side via subprocess could never serve the remote case, and would have no display at
  all under the `docker/` deployment.

Nothing here is wasted if inline playback is ever wanted: a byte-range media endpoint is
exactly the foundation a transcode path would sit on.

## Design

### Backend

Two endpoints, following the established `/jobs/{job_id}/titles/{title_id}/<action>` route
convention in `app/api/routes.py` and reusing the `get_job_or_404` dependency.

**`GET /api/jobs/{job_id}/titles/{title_id}/media`**

Streams the raw MKV with HTTP range support. Starlette 1.3.1's `FileResponse` already
implements range parsing, `206 Partial Content`, and `416 Range Not Satisfiable`
(`starlette/responses.py`), so seeking works without custom code. The handler resolves the
path, validates it, and returns `FileResponse(path, media_type="video/x-matroska")`.

**`GET /api/jobs/{job_id}/titles/{title_id}/playlist.m3u`**

Returns a minimal playlist whose single entry is an absolute URL to the `media` endpoint,
served as `audio/x-mpegurl` with `Content-Disposition: attachment` and a human-meaningful
filename (for example `job-12-title-3.m3u`).

```
#EXTM3U
#EXTINF:-1,<display name>
http://<host>/api/jobs/12/titles/3/media
```

**Access gate.** Both endpoints depend on `require_localhost_or_lan` from `app/api/guards.py`,
the same gate the manual import endpoints use. Loopback is always allowed; a LAN peer is
allowed only when the user has explicitly set `allow_lan_access`. This matches the deployment
story exactly: the remote case this design is built to serve is precisely the case the user
has already opted into.

**Path resolution.** Both endpoints derive the path from the database, never from a client
parameter. Resolution order:

1. `DiscTitle.output_filename`: an absolute path, set at rip time.
2. `DiscTitle.organized_to`: the library destination, used when the track has already been
   organized and the staging copy is gone.

If neither is set, or the resolved file does not exist on disk, return `404` with a message
distinguishing "not yet ripped" from "file missing".

**Path validation.** The resolved path must be contained within one of the configured roots:
`staging_path`, `library_tv_path`, or `library_movies_path`. Compare using `Path.resolve()`
on both sides and `os.path.commonpath`, so symlinks and `..` cannot escape. A path outside
every configured root returns `403`, not `404`, because a path that resolves but is out of bounds is
a configuration or data problem worth surfacing distinctly. This containment check belongs in
`app/core/security.py` alongside the existing guards, as a reusable
`is_within_configured_roots(path, roots)` helper.

**Host derivation.** The playlist URL must be built from the incoming request's `Host` header
via `request.url_for(...)` or equivalent, **not** a hardcoded `localhost` and not
`settings.host`. A playlist containing `http://localhost:8000/...` opens correctly on the
backend host and fails on every other machine, and passes local testing. This is the single
easiest thing to get wrong in this feature, and it warrants an explicit test.

### Frontend

A per-track control in the review UI. `ReviewQueue/Inspector.tsx` is the focused decision
panel for one title and is the right home: the operator is already looking at the evidence
for that specific track when the question "what is this?" arises.

The control is a small cluster rendered near the track's metadata line, built from the
existing `SvActionButton` synapse primitive:

- **"Open in player"**: navigates to the `playlist.m3u` URL, triggering the browser's
  download-and-open flow.
- **"Copy stream URL"**: copies the absolute `media` URL to the clipboard, with a `sonner`
  toast confirming.

Both are hidden when the title has no resolvable file (no `output_filename` and no
`organized_to`), which is already visible in the `DiscTitle` the Inspector receives, so the
frontend needs no extra request to decide whether to render them.

**Both controls ship as peers; the copy button is not a fallback.** Its job is not to cover
an edge case, it is the answer to the player-detection problem below: it is the one action
that cannot silently fail. On Windows the `.m3u` extension is frequently owned by Films & TV
or by nothing at all rather than VLC, so the download-and-open path fails silently for a
meaningful share of users. Reviewing eight tracks also means eight downloads accumulating in
the browser's download bar. "Copy URL, then VLC, then Open Network Stream" is
handler-independent and works with any player on any OS.

A short standing hint sits beside the two buttons, always visible rather than appearing after
a failure, since a failure produces no signal to react to:

> Opens in your system's default player. Nothing happened? Copy the URL and use your player's
> "Open Network Stream".

### Player detection: why there is none

Engram cannot tell whether a working player is installed, and the UI never claims otherwise.
There is no "VLC detected" badge and no gating of the buttons on a probe. Two independent
reasons:

**Detection would run on the wrong machine.** Any probe executes on the backend, while the
player runs on whatever machine holds the browser. Once `allow_lan_access` is enabled those
are different machines, and a backend-side probe is wrong in both directions: it reports a
player present on a headless Docker host that has no display, or reports none while the
operator's laptop has VLC installed.

**The browser cannot see handlers.** No web API enumerates file-type or protocol handlers,
and navigating to a `.m3u` returns no success or failure signal to the page. "The player
opened" and "the file landed in Downloads and nothing happened" are indistinguishable from
JavaScript. Probing a custom `vlc://` scheme does not rescue this, because VLC does not
register one on Windows by default.

Presence and function also come apart badly. Finding `vlc.exe` on disk would not establish
that it owns the `.m3u` association, that it can reach the backend through the host firewall,
or that it can authenticate (see below). A probe would therefore produce confident answers to
a question it cannot actually settle.

The one case where a probe would be truthful is `is_loopback(peer)`, which proves the browser
is on the backend host. It was considered and rejected for v1: it buys a cosmetic hint in
exchange for an OS-specific detection matrix (`winreg` for the `.m3u` handler association on
Windows, `xdg-mime` on Linux, an app-bundle check on macOS, with no `winreg` usage anywhere in
the backend today), and it would be silently absent for exactly the LAN users who most need
guidance.

**Forward-looking constraint.** An external player sends none of the browser's cookies or
headers. The endpoints work today only because the API has no authentication. If auth is ever
added, these two endpoints break for external players and would need a signed, expiring token
embedded in the URL rather than session credentials. Worth knowing before an auth model is
designed, so this case is accounted for rather than discovered.

The URLs are constructed inline as plain strings rather than through `api/client.ts`, whose
`apiFetch` helpers exist to parse JSON responses and throw `ApiError`, and neither applies to a
link the browser or clipboard consumes directly.

### Scope boundary

This serves **post-rip review only**. `identification_coordinator` parks jobs in
`REVIEW_NEEDED` before ripping begins; those jobs have no file on disk and the controls will
correctly not render. This is inherent to the feature, not a gap to close later: there is
nothing to play before a rip. Post-rip matching review is where episode disambiguation
actually happens, and rip-first deferral routes more reviews into that phase.

## Security

The API has no authentication today. These endpoints serve full media file bytes over HTTP,
which is a genuine widening of what the API exposes, so the reach is bounded by the same
opt-in the import endpoints already use rather than left open.

- **Origin gate.** `require_localhost_or_lan`. Loopback always; LAN peers only when the user
  has set `allow_lan_access`. On a default install these endpoints are unreachable from the
  network, and a user who enables LAN access has already accepted filesystem browsing and
  manual import over the same boundary. Serving media to that peer is consistent with the
  decision they made, not an escalation beyond it.
- **No client-supplied paths.** The path comes from the database keyed by
  `(job_id, title_id)`. `TitleResponse.output_filename` already goes out to the client, but it
  must never come back in.
- **Containment check** against the configured roots, so a corrupted or hand-edited
  `output_filename` cannot be turned into an arbitrary file read.
- `403` for out-of-bounds paths, `404` for missing ones.

The residual risk is accepted knowingly: a LAN peer permitted by `allow_lan_access` can
enumerate job and title ids and pull any ripped file. That peer can already browse the
filesystem and drive imports through the existing gated endpoints, so this adds a new data
egress path rather than a new trust boundary.

## Testing

**Backend unit** (`tests/unit/`):

- Range request returns `206` with correct `Content-Range`; unsatisfiable range returns `416`.
- Full request returns `200` with the complete body.
- Title with neither `output_filename` nor `organized_to` returns `404`.
- Title whose recorded path does not exist on disk returns `404`.
- Title whose path resolves outside every configured root returns `403`, including a `..`
  traversal attempt and a symlink escape.
- Falls back to `organized_to` when `output_filename` is absent.
- Playlist body contains an absolute URL built from the request `Host` header; assert
  explicitly that a non-localhost `Host` produces a non-localhost URL.
- Playlist carries `Content-Disposition: attachment` and the `audio/x-mpegurl` type.
- Both endpoints reject a non-loopback peer with `403` when `allow_lan_access` is false, and
  serve it when true. Tests override the dependency rather than spoofing peer addresses, per
  the note in `guards.py`. Note that `ASGITransport` defaults the peer to loopback, so the
  403 branch is only reachable by passing an explicit non-loopback `client=` to the transport.

**Frontend unit** (`Inspector.test.tsx`, vitest + RTL):

- Controls render when the title has a resolvable file.
- Controls are absent when it has neither path field.
- Copy writes the expected absolute URL to the clipboard.
- The standing hint renders whenever the controls do, since it is the only guidance a user
  gets when the handoff fails silently.

No E2E coverage. The value of the feature is what a native application does after the browser
hands off, which Playwright cannot observe.

## Out of scope

- Server-side transcoding or inline `<video>` playback.
- A "try inline first, fall back to external" hybrid, since it adds a codec probe and a failure
  path for content that essentially never plays natively from disc.
- Contact-sheet thumbnail generation. It was considered and is genuinely useful for "which
  episode is this", but it requires ffmpeg, which this design deliberately avoids depending
  on.
- Player detection or an install-help prompt, for the reasons set out above. Not deferred
  pending effort; it is a question the platform cannot answer honestly.
- Authentication for media endpoints, which would need an app-wide auth model that does not
  exist yet. The token constraint this creates is recorded above so a future auth design
  accounts for it.
- Playback position, resume, or any player state round-tripping back into Engram.
