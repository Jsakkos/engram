# Acoustic Fingerprint Network — Privacy Disclosure

This document enumerates exactly what Engram transmits when the fingerprint contribution
feature is active, why each field exists, and what privacy controls are available to the
user. It is the canonical reference for the contribution wire format as defined in
`engram-fingerprint-server/src/schemas.ts` (`ContributionRequestSchema` and
`ContributeDiscRequestSchema`) and implemented in
`backend/app/services/contribution_uploader.py`.

Engram contributes two kinds of record, both gated behind the **same** consent toggle
(`enable_fingerprint_contributions`) and the **same** disclosure acceptance
(`fingerprint_disclosure_accepted`), and both covered by the same pseudonym and the same
`/v1/forget` deletion path:

1. **Audio fingerprints** — a short perceptual fingerprint of an episode's audio, sent to
   `/v1/contribute` (§1).
2. **Disc-layout identity records** — a pressed disc's content hash plus how its titles
   map to episodes, sent to `/v1/contribute-disc` (§1.5). This is **release-level**
   (factory-pressing) data, not personal: it describes the disc, not you.

---

## 1. What Is Sent — Field-by-Field (Audio Fingerprint)

Every audio-fingerprint contribution is a single JSON object `POST`-ed to `/v1/contribute`. The server
validates it against `ContributionRequestSchema` and rejects anything that does not
conform. The following table covers every field in that schema — nothing is omitted and
nothing extra is sent.

| Field | Type / Format | What it is | Why it exists |
|---|---|---|---|
| `wire_format_version` | `integer`, always `1` | Protocol version literal | Allows the server to reject or migrate payloads from old or future clients without ambiguity |
| `pseudonym` | `string`, UUIDv4 | Per-install random identifier (see §3) | Groups contributions from the same install for rate-limiting, anti-poison cross-checking, and the `/v1/forget` deletion path — not tied to any user identity |
| `tmdb_id` | `integer` | TMDB numeric ID of the show or movie | Tells the server which episode row to associate the fingerprint with |
| `season` | `integer ≥ 0` or `null` | Season number | Part of the episode coordinate — `null` for movies |
| `episode` | `integer ≥ 0` or `null` | Episode number within the season | Part of the episode coordinate — `null` for movies |
| `fingerprint_b64` | `string`, standard base64 | A perceptual chromaprint hash sequence, zstd-compressed then base64-encoded (see §1.1) | The acoustic fingerprint the server stores and uses for future identification lookups |
| `fingerprint_sha256_b64` | `string`, standard base64 | SHA-256 of the **uncompressed** LEB128 varint stream (before zstd), base64-encoded | Server-side exact-duplicate detection — if two clients rip the same disc the SHA-256 matches and the server can deduplicate without storing the fingerprint twice |
| `disc_content_hash_b64` | `string`, standard base64, or `null` | TheDiscDB content hash (see §2) | Identifies the disc release a contribution came from; used by the server's promotion algorithm to require contributions from at least 3 **distinct disc releases** before a fingerprint reaches canonical tier |
| `match_confidence` | `float`, 0.0–1.0 | Confidence score from the local matcher | Weights the contribution in tier-promotion; low-confidence contributions are filtered before they can influence canonical fingerprints |
| `match_source` | `string enum` | How the episode was identified locally: `engram_asr`, `engram_discdb`, `bootstrap`, `user_review`, or `engram_chromaprint_corroboration` | Lets the server understand the evidence chain behind each fingerprint; corroboration contributions (chromaprint confirmed by ASR) are stronger signals than ASR-only |
| `client_version` | `string`, 1–100 chars | Engram version string (from `app.__version__`) | Lets the server detect and quarantine contributions from known-buggy client versions |

### 1.1 What the fingerprint actually is

`fingerprint_b64` is a perceptual chromaprint hash stream. Chromaprint produces a
sequence of 32-bit unsigned integers that encode spectral characteristics of short audio
windows. The client:

1. Extracts 30-second audio windows from the ripped MKV via FFmpeg (16 kHz mono PCM).
2. Runs `libchromaprint` over each window, producing ~240 32-bit hashes per window.
3. Encodes the hash array as LEB128 unsigned varints (the same wire primitive used by
   Protocol Buffers).
4. Compresses the varint stream with zstd at level 11.
5. Base64-encodes the compressed bytes.

The result is approximately 7 KB per episode. **It is not audio. It cannot be decoded
back into audio.** It is also not a subtitle, a transcript, or any kind of text. It is a
compact numeric signature of acoustic patterns — useful only for matching against other
chromaprint signatures.

---

## 1.5 Disc-Layout Identity Records (`/v1/contribute-disc`)

In addition to per-episode audio fingerprints, Engram contributes a **disc-layout identity
record** when a disc job completes. This is a single JSON object `POST`-ed to
`/v1/contribute-disc`, validated against `ContributeDiscRequestSchema`. Its purpose: once
enough independent users contribute the **same** pressed disc with the **same**
title→episode mapping, the network can recognize that disc instantly on future inserts and
skip audio matching entirely.

| Field | Type / Format | What it is | Why it exists |
|---|---|---|---|
| `wire_format_version` | `integer`, always `1` | Protocol version literal | Lets the server reject or migrate old/future clients |
| `pseudonym` | `string`, UUIDv4 | The same per-install random identifier as §3 | Groups contributions for rate-limiting and the `/v1/forget` path |
| `disc_content_hash_b64` | `string`, standard base64 (**required**) | TheDiscDB content hash of the disc (see §2) | The disc-release key the network promotes against |
| `tmdb_id` | `integer` | TMDB numeric ID of the show or movie | Which title the disc belongs to |
| `content_type` | `"tv"` or `"movie"` | Whether the disc is a TV-season disc or a movie | Shapes how the layout is interpreted |
| `season` | `integer ≥ 0` or `null` | Season number (`null` for movies) | Part of the disc coordinate |
| `titles` | `array` of title rows | The disc's full title layout (see below) | The mapping the network promotes once corroborated |
| `client_version` | `string`, 1–100 chars | Engram version string (`app.__version__`) | Lets the server quarantine known-buggy clients |

Each row in `titles` describes one track on the disc: its `title_index`,
`duration_seconds`, `size_bytes`, an `assignment` (`episode` / `main_movie` / `extra` /
`discarded`), the `season`/`episode` it maps to (when applicable), the local
`match_confidence`, and the `match_source`. **No filenames, paths, or file contents are in
these rows** — only the disc's structural layout (durations and byte sizes come from the
disc's own directory metadata) and which episode each title was identified as.

This record is **release-level, not personal**: it describes a factory-pressed disc that is
identical for everyone who owns that pressing, exactly like the disc content hash itself
(§2). A disc whose layout was handed to the client *by* the network (i.e. every identified
title came from `network_disc`) is **not** re-contributed, to avoid a feedback loop.

---

## 2. The Disc Content Hash — Explicit Clarification

`disc_content_hash_b64` is the **TheDiscDB ContentHash**: an MD5 digest computed from
the little-endian Int64 byte-sizes of all `BDMV/STREAM/*.m2ts` files on the BluRay disc,
sorted by filename, concatenated, and hashed.

What this means in practice:

- It identifies a **specific disc release** (e.g. "the US retail Blu-ray of Season 1").
  Different regional pressings or remaster editions hash differently; the same pressing
  always hashes the same.
- It is derived from **file sizes, not file contents**. It cannot be reversed into
  anything the user owns and is not useful to anyone who does not already have the same
  disc.
- It is **not a hash of any file on the user's hard drive**. It comes from the optical
  disc structure at rip time.
- It is **nullable** (`disc_content_hash_b64: Base64.nullable()` in the schema). If the
  disc content hash is not available for a job (e.g. a simulated rip), the field is
  `null` and the server omits the disc-level deduplication for that contribution.

---

## 3. The Pseudonym

`contribution_pseudonym` is a UUIDv4 string generated once on first run by
`app/services/contribution_pseudonym.py` using Python's `uuid.uuid4()`. It is stored in
the `app_config` database table.

Properties:

- **Not tied to any user identity.** There is no account, no login, no email. The UUID
  is entirely random and carries no information about who generated it.
- **Not logged server-side with an IP.** The server stores the pseudonym in the
  `contributor` table alongside a contribution count and first/last-seen timestamps; it
  does not log the IP address of the contributing client.
- **Rotatable at any time.** The user can issue `POST /api/fingerprint/contributions/rotate-pseudonym`.
  The endpoint generates a fresh UUIDv4, re-tags all pending (not-yet-uploaded)
  contributions with the new pseudonym, and saves it to config. Past uploaded
  contributions on the server retain the old pseudonym and become effectively
  unlinked from the new identity.
- **Scoped to one install.** Nothing in the system links pseudonyms across machines.

---

## 4. What Is NOT Sent

The following data is explicitly **absent** from `ContributionRequestSchema` **and**
`ContributeDiscRequestSchema` and is therefore never transmitted by either path. If a field
is not in those schemas, the server will not accept it even if a modified client tried to
send it. (The disc-layout record's `titles` rows carry only structural metadata — track
index, duration, byte size, assignment, and the matched episode — never filenames or
paths.)

- **File names** — the MKV filename, the staging path, the library path, or any part
  of the user's directory structure.
- **File paths** — no path of any kind from the client machine.
- **IP address** — the server receives the IP as part of TCP connection metadata but
  does not log or store it. The schema has no IP field and the server's
  `handleForget` implementation deletes rows purely by pseudonym.
- **User identity** — no email, no account, no hardware identifier, no MAC address.
- **Library structure** — no indication of what other shows or movies the user owns.
- **Audio content** — the fingerprint is irreversible; raw audio is never transmitted.
- **Subtitles or transcripts** — even when the match was made via ASR, only the
  resulting episode tuple and confidence are sent, not the transcript.
- **File content hash** — only the disc-structure size hash (§2); no hash of the user's
  actual files.

---

## 5. Privacy Controls

### 5.1 Opt-out toggle

`enable_fingerprint_contributions` (config field, default `true`) disables all uploads
when set to `false` — **both** audio fingerprints and disc-layout records share this single
toggle. The uploader service checks this flag before every batch of either kind; if it is
`false`, nothing leaves the machine. The toggle is exposed in the ConfigWizard settings UI.

### 5.2 Just-in-time disclosure gate

`fingerprint_disclosure_accepted` (config field, default `false`) is a hard gate before
any upload occurs. The uploader checks both flags:

```
enable_fingerprint_contributions == true
AND fingerprint_disclosure_accepted == true
```

If **either** audio fingerprints **or** disc-layout records are queued but the user has not
yet accepted the disclosure modal, the uploader fires a `fingerprint_disclosure_required`
WebSocket event to trigger the `FingerprintDisclosureModal` in the UI — the pending count
covers both queues. Nothing is uploaded until the user explicitly clicks "Accept and start
contributing." Clicking "Disable contributions" sets `enable_fingerprint_contributions =
false` and no upload occurs.

### 5.3 Local audit log

Every successful upload appends a line to
`~/.engram/cache/contribution_log.jsonl`. Each line is a JSON object tagged with a `kind`
(`"episode"` or `"disc"`). An episode upload logs:

```json
{
  "ts": "2026-05-28T12:34:56.789Z",
  "kind": "episode",
  "contrib_id": 42,
  "tmdb_id": 1399,
  "season": 1,
  "episode": 3,
  "pseudonym_prefix": "a1b2c3d4"
}
```

A disc-layout upload logs the disc coordinate and a title count (never the title rows
themselves):

```json
{
  "ts": "2026-05-28T12:34:56.789Z",
  "kind": "disc",
  "contrib_id": 7,
  "tmdb_id": 1399,
  "content_type": "tv",
  "season": 1,
  "title_count": 6,
  "pseudonym_prefix": "a1b2c3d4"
}
```

Note: only the first 8 characters of the pseudonym are logged locally (enough to
correlate but not enough to reconstruct the full UUID from the log alone). The full
pseudonym is stored in `app_config`.

The audit log is surfaced via `GET /api/fingerprint/contributions` (localhost-only
endpoint). Users can inspect exactly which episodes were contributed and when.

### 5.4 Pseudonym rotation

`POST /api/fingerprint/contributions/rotate-pseudonym` generates a fresh UUIDv4,
re-tags all pending local contributions with the new pseudonym, and saves it to config.
Future uploads will use the new identity. Past uploads on the server retain the old
pseudonym and are no longer linked to the new identity.

### 5.5 Forget (server-side deletion)

`POST /api/fingerprint/forget` sends the current pseudonym to the server's
`POST /v1/forget` endpoint, which executes:

```sql
DELETE FROM contribution WHERE pseudonym = ?;
DELETE FROM contributor WHERE pseudonym = ?;
```

The server response always includes `"canonical_unaffected": true`. This is honest:
**if any of this install's contributions have already been promoted into the
`episode_canonical` table, those canonical fingerprints are not rebuilt.** Promotion is
an aggregation across multiple independent contributors — once a contribution has been
merged into a consensus fingerprint it is indistinguishable from the other contributors'
data and cannot be un-aggregated. Only the raw `contribution` rows (and the `contributor`
registration row) are deleted.

After the server call, `forget_fingerprint_on_server` in `routes.py`:

1. Rotates to a fresh pseudonym and clears `fingerprint_disclosure_accepted`.
2. Deletes all local pending (not-yet-uploaded) contributions from the local DB.

The net effect: the old pseudonym is gone from the server's raw tables, a new identity
starts fresh, and the user must re-accept the disclosure before any future contributions
upload.

---

## 6. Phase 3 Note — Identification

Identification (querying the network to find what episode a fingerprint matches) is
controlled by a separate config flag: `enable_fingerprint_identification`, which defaults
to `false`. It is gated off until the catalog has sufficient coverage to be useful.

When enabled, identification sends **only the query fingerprint** (a zstd-varint
chromaprint) to `GET /v1/identify` with no additional request metadata — no pseudonym, no
TMDB ID, no episode guess. The server returns candidate matches. No IP is logged.
Identification is strictly separate from contribution; disabling contributions does not
affect identification and vice versa.

---

## 7. Verification

To independently verify what is transmitted, capture a contribution upload:

```
# On the machine running Engram, with DEBUG=true:
curl -X POST localhost:8000/debug/fingerprint/seed   # seed one fake contribution
# Then watch the uploader fire (it polls every 3600s by default; lower poll_interval in dev)
# or restart with a short interval
```

Alternatively, inspect the local audit log after a contribution succeeds:

```
cat ~/.engram/cache/contribution_log.jsonl
```

And compare the logged fields against the schema table in §1 above.
