# Discord support bot: design

**Status:** Phase 1 implemented in `bot/`. **Date:** 2026-10-11.

## Goal

Take routine work off the maintainer on the Engram Discord:
- answer how-to and setup questions from the docs;
- troubleshoot before something gets called a bug;
- turn real bugs, feature requests and doc gaps into well-formed GitHub issues, with a
  link back to the conversation.

The bot uses Claude Haiku 5.5 through the Anthropic API.

## Decisions

| Question | Decision |
|---|---|
| Hosting | Docker on the maintainer's server (`deploy/discord-bot/docker-compose.yml`). A long-lived gateway connection rules out serverless or Actions. |
| Repo | Monorepo `bot/`, a separate `uv` project, so the docs it answers from and the code they describe move together. It never imports `backend/`. |
| Who files | Anyone, but only by pressing **File issue** on a draft they've seen. Discord accounts younger than 7 days, and users past 3 filed issues a day, can chat but not file. Maintainers are exempt. |
| Home | A help **forum** channel: one post is one conversation and at most one issue. @mention in a text channel opens a thread. |
| Labels | Applied directly, but only from an allowlist intersected with the repo's real labels, plus `from-discord` and `needs-triage` always. |
| Diagnostics/logs | May go into public issues, in a collapsed `<details>` block. The prompt makes the bot warn that issues are public. Bundles are parsed by the bot (phase 2, below). |
| Transcripts | Not stored. Discord already holds them and the bot rebuilds history from the thread each turn. The DB keeps only thread ownership, drafts and usage. |
| DMs | Not answered. `/ask` gives a private answer without leaving the server. |
| Persona | Says it is an AI, names the maintainer as the human who reads issues, promises no timelines. |
| Duplicates | The model searches first. A match turns the draft into **Add to issue**, which posts a comment on the existing issue after the user confirms. |
| Budget | `BOT_DAILY_BUDGET_USD` (default $2) over a rolling 24 hours, estimated from `usage`. Sonnet escalation deferred until real usage shows it's needed. |

## Architecture

```
Discord ──▶ discord_app.EngramBot ──▶ agent.Agent ──▶ Anthropic Messages API (Haiku 5.5)
              │  thread routing,           │  tools: search_issues, get_issue (read-only)
              │  debounce, limits          │         draft_issue (proposal only)
              │                            ▼
              │                       github.GitHub ◀── GitHub App installation token
              ├─ store.Store (SQLite: threads, drafts, usage)
              └─ knowledge.KnowledgeBase (docs from main, refreshed every 6h)
```

### A turn

1. A message arrives in a thread the bot owns, from the reporter or from someone who
   mentions the bot. A 2-second debounce folds multi-message thoughts into one reply.
2. Budget, rate and filing-eligibility checks run.
3. History is rebuilt from the Discord thread as text only:
   - user messages are prefixed with the speaker's name;
   - text attachments are inlined;
   - past drafts appear as a one-line note.

   No thinking blocks are ever replayed, so the request is append-only within a turn and
   immune to edits or deletions in the thread.
4. Request shape:
   - `system` = standing instructions, then the docs block with `cache_control` (1-hour
     TTL);
   - `messages` = history, plus a trailing mid-conversation `system` message for per-turn
     facts (reporter name, `/bug` vs `/feature`, whether they may file). That keeps those
     facts out of the cached prefix.
   - `output_config.effort` comes from config (default `medium`); thinking is left
     adaptive.
   - Temperature and other sampling parameters are not sent; Haiku 5.5 rejects them.
5. Tool loop, at most 6 calls:
   - `search_issues` and `get_issue` are read-only;
   - a valid `draft_issue` ends the turn without another round trip;
   - an invalid one returns an error `tool_result` the model can fix;
   - `refusal` and API errors degrade to a canned reply.
6. Reply text is chunked at 2000 characters. A draft is posted as an embed with
   persistent buttons. Their custom ids carry the draft id, so they work across
   restarts. A new draft supersedes the thread's pending one.

### Filing

Filing happens in bot code, on the button press, never as a model action:

1. Check that the presser is the reporter or a maintainer.
2. Re-check that the reporter may file.
3. `claim_draft` atomically moves the draft from `pending` to `filing`, so a double click
   files once.
4. Render the body from the draft's fields (`intake.render_body`), sanitize it, create
   the issue or comment, record the number, and close the buttons.
5. If GitHub fails, the draft returns to `pending`.

### Safety

- **Prompt injection:** user text can at most shape a draft that the user then sees and
  confirms. The model has no write tool.
- **Mentions and Discord:** `allowed_mentions` is none client-wide. The only exception is
  the `/bug` starter post, which pings its own author.
- **Mentions and GitHub:** every `@` handle in published text gets a zero-width space,
  including inside code fences. Repo workflows match on raw body text
  (`contains(..., '@claude')`), and `claude.yml` additionally requires
  `sender == Jsakkos`.
- **HTML and limits:** HTML is escaped, every field is length-capped, and logs go into a
  fence longer than any backtick run they contain.
- **Least privilege:** the GitHub App has Issues read/write on one repository and nothing
  else.

### Cost

The knowledge block is about 145K characters, roughly 40–50K tokens with the Haiku 5.5
tokenizer. That is under the 100K-token threshold where Haiku's higher rate card starts.
`knowledge.WARN_CHARS` logs a warning before the docs drift past it. The block only
changes when `main` or the latest release changes, so most requests read it from cache.

## Phases

1. **Done (this change):**
   - Q&A in the forum, mentions and `/ask`;
   - `/bug` and `/feature` intake;
   - search, draft, confirm, file or comment;
   - labels;
   - per-user and global limits;
   - the maintainer step-back and `/quiet`;
   - Docker deploy.
2. **Phase 2 (done):** diagnostics `.zip` parsing (2a) and GitHub → Discord
   follow-through (2b).
3. **Phase 3 (done):** 👍/👎 feedback, a weekly digest, and optional escalation to a
   stronger model (3, below).

## Phase 2a: diagnostics bundles

The per-job bundle from `GET /api/diagnostics/report/{job_id}/bundle` contains:
- `report.md`;
- `job-detail.json`;
- `job-logs.txt`;
- `scan.log` / `rip.log`, when MakeMKV wrote them.

When a user attaches one, `diagnostics.parse` reads only those five names, and produces two
things.

**For the model** (`summary_for_model`), under about 6K characters and placed where the
attachment was in the history:
- Engram, OS and MakeMKV versions, parsed from `report.md`;
- the Job Context fields;
- a digest of `job-detail.json`: review reason, error, classification, TMDB match, and
  per-state track counts;
- the last 25 ERROR/WARNING/traceback lines from the job log;
- MakeMKV log lines that mention errors or failures.

So the model stops asking for what the bundle already answers, and fills the draft's
environment fields itself.

**For the issue** (`issue_section`): a collapsed `<details>` block with `report.md` (capped
at 8K), the error/warning log excerpt (10K) and the MakeMKV problems (4K).
- The bot attaches it to the draft from the reporter's *latest* bundle in the thread.
  Bundles other people post are read for the model but never published under the
  reporter's issue.
- The model only controls `include_diagnostics`, so the reporter can say "leave the logs
  out"; it cannot rewrite the evidence.

**Threat model.** The archive comes from the internet.
- The zip is capped at 8 MB and at 64 members.
- Unknown names, including path-traversal names, are ignored.
- Each member read stops at 2 MB decompressed, whatever the header claims, which stops zip
  bombs.
- Encrypted and unsupported members are reported as "not a bundle".
- Parsing runs in a worker thread.

**Redaction.** Redaction moved into `sanitize.redact` and now applies to *everything*
published to GitHub, not just bundles. It covers:
- Discord webhooks;
- MakeMKV `T-` keys;
- JWTs;
- `sk-ant-` and `gh*_` tokens;
- `key=`, `token=` and `password=` values;
- `C:\Users\<name>`, `/home/<name>` and `/Users/<name>` paths.

The backend's own pass only knows JWTs, `key=` / `token=` values and the server's own home
directory.

Raw text attachments (an `engram.log`, say) are redacted before the model sees them, so the
model can't copy a secret into the draft's logs field.

**Body size.** GitHub rejects issue bodies over 65,536 characters. `render_body` keeps the
body under 60K by first replacing the model's pasted logs, then the diagnostics block, with
a pointer to the Discord thread.

**Format coupling.** The parser keys on `report.md`'s `**Engram version**:` lines and the
`- **ID**:` entries under `### Job Context`, both from `_build_markdown_summary`. If those
change, the parser degrades: it still reads the logs and detail, but loses the version
fields. Check `bot/tests/test_diagnostics.py`, which mirrors the format.

## Phase 2b: GitHub → Discord follow-through

**Polling, not webhooks.** The bot runs on a home server with no public URL. Each
`BOT_GITHUB_POLL_MINUTES` (default 10) it makes three requests, whatever the number of
followed issues:
- issues updated since the cursor (`state=all&since=`);
- repo comments updated since the cursor (`/issues/comments?since=`);
- `releases/latest`.

**State.**
- `follows` maps each issue to each thread that reported it: one row per (issue, thread),
  so duplicates added as comments are followed too. A row is written when the issue or
  comment is filed, with the issue's state at that moment.
- `meta.poll_since` is the cursor. Each window overlaps the last by 2 minutes, so a clock
  difference can't skip an update. Repeats are harmless: notices fire only on a state
  change, and `relays` is unique per (comment, thread).
- On the very first poll the bot records the cursor and the current release and posts
  nothing, so it never replays the repo's history.
- A GitHub error leaves the cursor in place, so the next poll covers the window again.

**What gets posted.**
- **State changes:** closed as completed, not planned or duplicate, and reopened.
- **Maintainer comments:** only from `OWNER`, `MEMBER` or `COLLABORATOR` authors, never from
  bots, never from the bot's own comments (`own_comments`, which matters with token auth,
  where the bot posts as the maintainer), and only comments made after the follow began.
  - The comment is quoted (up to 1,500 characters) with a persistent **Reply on GitHub**
    button (`engram:reply:<relay id>`).
  - Only the relay's reporter or a maintainer can open its form.
  - The reply is sanitized, credited to the reporter's Discord name and the thread, and
    posted as a comment, which is recorded as the bot's own.
  - Posting a reply counts toward the per-user rate limit.
  - The model is told it can't post on GitHub, and to help the user word an answer and
    point them to the button.
- **Releases:** when `releases/latest` changes, each follow closed as completed *before*
  that release was published gets one "vX.Y.Z is out" notice. One closed afterwards waits
  for the next release.

**Delivery.** A failed Discord post returns `None` and is retried next poll: the follow's
state isn't advanced, or the relay row is deleted. A thread that is gone or forbidden
returns `0` and is dropped, so a deleted thread doesn't retry forever.

## Phase 3: feedback, digest, escalation

**Feedback.**
- Every bot reply is recorded in `bot_messages` with the model that wrote it.
- Raw reaction events (no member cache needed) turn 👍 / 👎 on those messages into
  `feedback` rows, one per user per message. Removing the reaction removes the row.
- The bot ignores its own reactions.

**Digest.** Posted to the maintainer log channel once a week, at `BOT_DIGEST_WEEKDAY` /
`BOT_DIGEST_HOUR_UTC`, with at most one post per 6 days; the time is kept in
`meta.digest_at`. It lists:
- threads, answers and spend;
- drafts filed or cancelled, and the filed issues;
- relayed comments;
- feedback counts;
- jump links to answers with a net-negative score.

Downvoted answers are the docs-gap signal: an answer marked unhelpful usually means the
docs didn't cover the question.

**Escalation.** `BOT_ESCALATION_MODEL` is empty by default, so it stays off. When set, a
turn uses that model once the thread has `BOT_ESCALATION_AFTER_TURNS` user messages, or
once any answer in the thread has a net 👎.
- History is text only (see "A turn"), so switching models mid-thread needs no
  thinking-block handling.
- Escalated calls to Claude Sonnet 5.5 or Claude Opus 5.5 go through
  `client.beta.messages` with `fallbacks: "default"` (beta
  `server-side-fallback-2026-07-01`), so a safety-classifier refusal is retried on a
  fallback model server-side. Claude Haiku 5.5 has no server-side fallback, and its calls
  stay on the plain endpoint.
- Spend is priced per model (`agent.KNOWN_PRICES`) and counts against the same daily
  budget.

## Live-test fixes (2026-10-15)

From the first deployment against a test server and a private scratch repo:

- **Reopen notices repeated.** GitHub reports a reopened issue as `("open", "reopened")`,
  but a follow stores no reason while open. The poll compared the raw pair to the stored
  one, so every later poll that saw the issue posted "was reopened" again. It now compares
  the normalized pair it stores.
- **LAN details in published text.** Real bundles carry matcher paths such as
  `\\192.168.x.x\NAS Downloads\Show\Disc 5\t07.mkv`. `sanitize.redact` now rewrites
  network shares to `\\<host>\<share>\...` (keeping the path below the share, which is the
  useful part), handles `//host/share/` and `smb://` forms and repr-doubled separators, and
  replaces private-range IPv4 addresses (RFC 1918 plus 100.64/10) with `<lan-ip>`. A UNC
  prefix must start a token, so a repr'd Windows path is not mistaken for a share.
- **Docs repo split from the issue repo.** `BOT_DOCS_REPO` (default `Jsakkos/engram`)
  supplies docs, the changelog and releases; `GITHUB_REPO` is only where issues go.
  Release notices use `BOT_DOCS_REPO`, because "vX is out" means the app shipped the fix.
  The docs client shares the issue client's credentials, so a GitHub App installed only on
  a scratch repo still reads Engram's public repo.
- **Bad credentials fail fast.** Startup reads the repo and its issues once; a 401, 403 or
  404 (or an unusable App key) logs one error naming the variable and the permissions
  needed, then exits non-zero. Network errors only warn.
- **Release errors no longer hold the cursor.** The release check runs after the cursor
  advances, in its own `try`, so a token that can't read releases (a private repo without
  Contents: read) doesn't stall issue and comment notices.

