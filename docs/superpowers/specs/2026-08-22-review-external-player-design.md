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

**Both controls ship; the copy button is not a nice-to-have.** On Windows the `.m3u`
extension is frequently owned by Films & TV or by nothing at all rather than VLC, so the
download-and-open path silently fails for a meaningful share of users. Reviewing eight tracks
also means eight downloads accumulating in the browser's download bar. "Copy URL, then VLC →
Open Network Stream" is handler-independent and works with any player on any OS.

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

The API has no authentication today, and CORS is the only gate. These endpoints serve full
media file bytes over HTTP, so anyone who can reach the dashboard on the LAN can pull raw
rips. Relative to the existing exposure (`/api/config`, the diagnostics bundle, the whole
job API), the delta is modest, but it is a real widening and is accepted knowingly rather
than overlooked.

The mitigations that do apply:

- Path comes from the database keyed by `(job_id, title_id)`; no client-supplied path is ever
  accepted. `TitleResponse.output_filename` already goes out to the client, but it must never
  come back in.
- Containment check against configured roots, so a corrupted or hand-edited `output_filename`
  cannot be used to read arbitrary files.
- `403` for out-of-bounds paths, `404` for missing ones.

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

**Frontend unit** (`Inspector.test.tsx`, vitest + RTL):

- Controls render when the title has a resolvable file.
- Controls are absent when it has neither path field.
- Copy writes the expected absolute URL to the clipboard.

No E2E coverage. The value of the feature is what a native application does after the browser
hands off, which Playwright cannot observe.

## Out of scope

- Server-side transcoding or inline `<video>` playback.
- A "try inline first, fall back to external" hybrid, since it adds a codec probe and a failure
  path for content that essentially never plays natively from disc.
- Contact-sheet thumbnail generation. It was considered and is genuinely useful for "which
  episode is this", but it requires ffmpeg, which this design deliberately avoids depending
  on.
- Authentication for media endpoints, which would need an app-wide auth model that does not
  exist yet.
- Playback position, resume, or any player state round-tripping back into Engram.
