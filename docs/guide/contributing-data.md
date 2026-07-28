# Contributing Data

Identifying an episode has always had the same weak spot: it depends on finding a reference
for the show you happen to be ripping. Popular shows have plentiful subtitles and metadata;
obscure ones have almost none, and that is exactly where matching gets slow and uncertain.

Engram participates in two community data networks that attack that weakness from different
directions. Both work on the same principle: a disc that one person identifies correctly is
a disc nobody else has to identify again. This page explains what each one shares, what it
does not, and how to control both.

## Two networks, two questions

The two networks are easy to confuse because both involve "identifying a disc", but they
answer different questions and behave differently out of the box. TheDiscDB is a public
catalog of physical disc layouts, run by a third party, and Engram only contributes to it if
you ask it to. The Engram fingerprint network is Engram's own catalog of episode audio
signatures; contributions are on by default, gated behind a disclosure you accept before
anything is uploaded.

| Network | Question it answers | What it identifies | Contributions by default |
|---------|---------------------|--------------------|--------------------------|
| [TheDiscDB](https://thediscdb.com) | "Which disc is this?" | A specific factory pressing, and how its tracks map to episodes | **Off.** Opt in from Settings |
| Engram fingerprint network | "Which episode is this track?" | A single ripped title, from a signature of its audio | **On**, and gated behind a disclosure dialog you accept before the first upload |

## TheDiscDB: sharing disc layouts

TheDiscDB is a community database of Blu-ray and DVD releases: which titles are on each
disc, how long they run, and which episode each one is. When Engram scans a disc it looks
the disc up there, and if the release is already catalogued it knows the track layout before
ripping starts.

Those lookups are on for everyone and have been for some time. They are read-only, they need
no account, and they are entirely separate from contributing. Contributing is the other half:
it is what puts a disc into the catalog for the next person, and it is off until you turn it
on.

### What a contribution contains

A contribution describes the disc, not you or your machine:

- **The disc content hash.** An MD5 of the Int64 file sizes of the disc's
  `BDMV/STREAM/*.m2ts` files, sorted by filename. It is computed from sizes, not contents,
  and it identifies a factory pressing. Every copy of the same pressing hashes identically,
  so it says nothing about who did the ripping.
- **The title to episode mapping.** Which track on the disc is which episode, plus track
  durations and sizes.
- **The MakeMKV scan log** for the disc, which is what lets TheDiscDB reconstruct the full
  title structure.
- **UPC, ASIN, and cover art**, but only at the Full sharing tier, and only for the values
  you enter yourself.

### Turning it on

In the dashboard, open **Settings**, go to the **Data Sharing** step, expand **TheDiscDB**,
and tick **Enable TheDiscDB Contributions**. A **Contribution Level** dropdown appears below
it; leave it on Automatic unless you want the Full tier. Save, and completed jobs start
queuing up on the Contribute page.

!!! note "No account, no API key"
    You do not need a TheDiscDB account, and you do not need to fill in the **TheDiscDB API
    Key** field. Submissions from Engram are accepted unauthenticated. The field exists for
    contributors who have been issued a key; an empty box is the normal state and does not
    block anything.

### Sharing tiers

`discdb_contribution_tier` controls how much a contribution carries. The dropdown offers the
two sharing levels; leaving the contributions checkbox unticked is the "do not share" state
(tier 1).

| Tier | Name | What it shares |
|------|------|----------------|
| 1 | Do not share | Nothing leaves the machine. Equivalent to leaving contributions off |
| 2 | Automatic *(default)* | Everything Engram already collected during the rip: content hash, track layout, episode mapping, scan log |
| 3 | Full | The above, plus a prompt to add the release's UPC or ASIN and cover images |

### The Contribute page

With contributions enabled, a **CONTRIBUTE** tab appears in the navigation with a badge
showing how many discs are waiting. Each completed job moves through four steps:

1. **Export.** Engram writes the disc's metadata and scan log into an export bundle. Use
   **Export all pending** to do the whole queue at once.
2. **Group.** If a season spans several discs, select them and use **Group selected** to link
   them as one multi-disc set before submitting. A grouped set can be submitted in one go.
3. **Enhance** *(optional)*. Add the UPC or ASIN, release date, cover art, and extras
   information. This is the tier 3 material, and it is the part only a human with the case in
   hand can supply.
4. **Submit.** Send the bundle to TheDiscDB. Engram then offers a **Continue on TheDiscDB**
   link to the release's contribution page, where you can review the submission on the site
   itself.

Nothing is submitted automatically. Export happens in the background, but the submit step is
always a button you press.

## The Engram fingerprint network: sharing episode fingerprints

### This is not the same as episode matching

Engram's episode matcher transcribes a ripped title with speech recognition and compares that
transcript against reference subtitles for the show. That is the process older documentation
sometimes called "audio fingerprint matching", which was a poor name: it is transcript
comparison, it runs locally, and it uploads nothing.

The fingerprint network is a different mechanism that happens to share a word. A
*chromaprint* fingerprint is a compact numeric signature of acoustic patterns, the same family
of technology music identification apps use. It does not involve speech, transcripts, or
subtitles at all. Two rips of the same episode produce near-identical chromaprints even
though the audio files differ, which is what makes the signature useful as a shared lookup
key.

The practical difference: transcript matching needs subtitles to exist for your show.
Chromaprint matching only needs somebody else to have ripped the same episode.

!!! note "Looking up is separate from contributing"
    Engram also *queries* the network during identification. A query sends only the
    fingerprint being looked up: no pseudonym, no TMDB ID, no episode guess. If the network
    does not recognise it, identification falls back to the usual TMDB, AI, and heuristic
    paths, so a miss costs nothing. Turning contributions off does not turn lookups off, and
    lookups do not upload anything.

### What a contribution contains

Each contribution is one JSON object. These are all of its fields:

| Field | What it is |
|-------|------------|
| `wire_format_version` | Protocol version number, so the server can reject payloads it does not understand |
| `pseudonym` | A random UUID generated once per install. Not an account, not derived from anything about you or your hardware |
| `tmdb_id`, `season`, `episode` | Which episode the fingerprint is claimed to be. Season and episode are empty for movies |
| `fingerprint_b64` | The chromaprint signature itself |
| `fingerprint_sha256_b64` | A checksum of the signature, so the server can spot exact duplicates without storing them twice |
| `disc_content_hash_b64` | The disc release the rip came from (the same pressing hash TheDiscDB uses). This is what lets the server tell distinct releases apart |
| `match_confidence` | How confident the local matcher was. Low-confidence contributions are filtered out before they can influence anything |
| `match_source` | How the episode was identified locally, for example by speech recognition or by your own confirmation in the review queue |
| `client_version` | The Engram version that produced it, so buggy releases can be quarantined |

The fingerprint itself is built from 30 second windows of 16 kHz mono audio, reduced by
chromaprint to a stream of 32-bit integers, then compressed and base64 encoded. It is roughly
7 KB per episode. **No audio or video is transmitted, and audio cannot be reconstructed from
the fingerprint.** It is a numeric signature, not a recording, not a transcript.

Engram also contributes a disc-layout record alongside the fingerprints: the pressing hash
plus how that disc's titles map to episodes. Like TheDiscDB's version, it describes a
factory-pressed disc that is identical for everyone who owns it.

### What is never sent

The server's schema has no field for any of the following, so it would reject them even from
a modified client:

- Filenames, staging paths, library paths, or any part of your directory structure
- Audio or video content
- Transcripts or subtitles, even when speech recognition made the match
- Any indication of what else is in your library
- Email, account, hardware identifier, or any other identity
- Your IP address is not logged or stored alongside contributions

### How a fingerprint becomes canonical

A single contribution does not become the network's answer for an episode. The server
promotes a fingerprint to canonical tier only once it has been contributed from **at least
three distinct disc releases**, which is what the disc content hash is for.

That threshold is the anti-poisoning mechanism. One person's mislabelled rip, or one badly
tuned match, cannot become the catalog's answer, because no single contributor can supply
three independent pressings' worth of agreement.

### Turning it off, and deleting what you sent

Contributions are on by default, but nothing is uploaded until you accept the disclosure
dialog. That dialog appears the first time contributions are actually queued for upload, not
during first-run setup, so you may not have seen it yet. Until it is accepted, uploads are
blocked and the queue simply sits on your machine.

To stop contributing, open **Settings** → **Data Sharing** → **Fingerprint network** and
untick **Contribute audio fingerprints**. The uploader checks that flag before every batch,
so nothing further leaves the machine.

The same panel shows your anonymous contribution ID and offers **Forget me on the server**.
That sends your pseudonym to the network's deletion endpoint, which removes every raw
contribution recorded under it, clears the local upload queue, and rotates you to a fresh
ID. You can also rotate the ID on its own, which unlinks future contributions from past ones.

!!! warning "Promoted data cannot be un-aggregated"
    Deletion removes your raw contributions. If some of them had already been promoted into a
    canonical fingerprint, that consensus fingerprint is not rebuilt: once contributions from
    several people are merged, no individual's share can be separated back out. This is
    stated plainly rather than glossed over, because the deletion endpoint reports it as such.

You can also see exactly what was uploaded and when. Every successful upload appends a line
to `~/.engram/cache/contribution_log.jsonl` recording the episode coordinate and a timestamp.

## Frequently asked questions

### Does Engram upload my media?

No. Neither network transmits audio, video, or any file from your library. TheDiscDB receives
disc structure metadata and the MakeMKV scan log. The fingerprint network receives a numeric
acoustic signature that cannot be turned back into audio. No filenames or paths are sent by
either.

### Do I need an account for either of these?

No. TheDiscDB accepts contributions from Engram without an account or an API key. The
fingerprint network has no accounts at all, only a random per-install identifier.

### Can I turn contributions off?

Yes, both independently. TheDiscDB contributions are off until you turn them on, in
**Settings** → **Data Sharing** → **TheDiscDB**. Fingerprint contributions are on by default
and are unticked in the **Fingerprint network** group of the same step. Turning either off
does not affect disc lookups or network identification, which are read-only.

### Can I delete data I have already contributed?

For the fingerprint network, yes: **Forget me on the server** deletes every raw contribution
recorded under your pseudonym, with the caveat above about already-promoted consensus data.

TheDiscDB is a separate project and Engram has no deletion path into it. A submission there
becomes part of the public catalog entry for that disc release, describing the pressing rather
than you; if an entry needs correcting, take it up with
[TheDiscDB](https://thediscdb.com) directly.

### Does contributing slow down ripping?

No. Fingerprint extraction runs after a title is ripped and matched, and uploads are batched
and sent in the background on a timer. TheDiscDB exports are written after a job completes,
and submitting is a button you press when you feel like it. Neither sits in the path of the
rip.

## Further reading

- [Fingerprint Network Privacy](../development/fingerprint-privacy.md): the field-by-field
  wire format, the exact contents of both contribution types, the pseudonym's properties, and
  how to verify what is transmitted yourself.
- [Review Queue](review-queue.md): confirming a low-confidence match, which is recorded as
  human-confirmed evidence if that title is later contributed.
- [Job History](history.md): per-job detail, including the disc content hash and whether the
  disc was found in TheDiscDB.
- [TheDiscDB](https://thediscdb.com): the catalog itself.
