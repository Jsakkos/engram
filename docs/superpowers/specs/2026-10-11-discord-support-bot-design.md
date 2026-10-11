# Discord support bot — design

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
| Diagnostics/logs | May go into public issues, in a collapsed `<details>` block. The prompt makes the bot warn that issues are public. `.zip` bundles aren't parsed yet; the bot asks for the relevant `report.md` excerpt. |
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
2. **Next:**
   - parse diagnostics `.zip` bundles (`report.md` plus the job-tagged log tail) into the
     draft;
   - GitHub → Discord follow-through: post in the originating thread when its issue
     closes or ships in a release, and relay a maintainer's "needs info" comment to the
     thread. `drafts.issue_number` already maps issues to threads.
3. **Later:**
   - a weekly digest of `from-discord` issues and unanswered questions (which doubles as
     a docs-gap report);
   - 👍/👎 feedback on answers;
   - Sonnet escalation for long or low-confidence threads, if the data shows a need.
