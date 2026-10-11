# Engram Discord support bot

A Discord bot for the Engram community server. It answers questions from Engram's docs,
helps troubleshoot setups, and turns bug reports and feature requests into GitHub issues.
It runs on Claude Haiku 5.5 through the Anthropic API.

Design and rationale: [`docs/superpowers/specs/2026-10-11-discord-support-bot-design.md`](../docs/superpowers/specs/2026-10-11-discord-support-bot-design.md).

## What it does

| Where | Behaviour |
|---|---|
| Help forum | Answers every new post. Keeps replying to the post's author; anyone else must @mention it. |
| `@Engram Bot` in a text channel | Opens a thread from that message and answers there. |
| `/ask` | Private, one-off answer (ephemeral). No issue filing. |
| `/bug`, `/feature` | Short form, then a forum post where the bot gathers details and drafts the issue. |
| `/issue <n>` | Shows an issue's title, state and labels. |
| `/quiet` | The thread's author or a maintainer turns the bot off (or back on) in that thread. |

A maintainer posting in a thread makes the bot step back until someone mentions it.

**After an issue is filed, the bot keeps the thread informed.** It polls GitHub every 10
minutes, so it works without a public webhook URL, and posts in the originating thread
when:
- the issue is closed as completed, closed as not planned or as a duplicate, or reopened;
- a maintainer (owner, member or collaborator) comments on it. The comment is quoted with a
  **Reply on GitHub** button. Only the reporter or a maintainer can use it; it opens a form
  whose text is posted to the issue, sanitized and credited to the reporter's Discord name;
- a new release comes out after an issue was closed as completed ("Engram vX.Y.Z is out
  and should include the fix").

Duplicate reports that were added to an existing issue get the same updates. The bot never
relays its own comments back.

**Feedback and the weekly digest.** 👍 / 👎 reactions on the bot's answers are recorded. If
`DISCORD_MAINTAINER_LOG_CHANNEL_ID` is set, the bot posts a weekly digest there (Mondays,
09:00 UTC by default). It covers new threads, answers, spend, drafts filed or cancelled,
relayed comments, feedback counts, and links to answers people marked unhelpful, which
usually point at a gap in the docs.

**Escalation (off by default).** Set `BOT_ESCALATION_MODEL` (e.g. `claude-sonnet-5-5`) to
answer with a stronger model in long threads (from the 6th user message) or once any answer
in the thread got a 👎. Escalated calls use Anthropic's server-side refusal fallback, and
their spend counts against the same daily budget at that model's prices.

**Issues are only filed when a person presses a button.** The model can *propose* a draft
(the `draft_issue` tool). The bot shows the draft as an embed with **File issue / Edit /
Cancel** buttons, and only the reporter or a maintainer can press them. Before drafting,
the model searches existing issues. If it finds the same problem, the button reads **Add
to issue** and posts the report as a comment on that issue instead.

**Diagnostics bundles.** Users can attach the per-job diagnostic `.zip` from Engram's
dashboard. The bot reads it (`diagnostics.py`): versions, job state, the errors and warnings
from the job log, and problems in the MakeMKV logs. The model gets a short summary, so it
can troubleshoot without asking for things the bundle already says. When the reporter's
thread becomes an issue, the bot appends their latest bundle (the report plus a log
excerpt) to the issue as a collapsed section, so the evidence reaches GitHub word for word
rather than paraphrased by the model.

The archive is treated as hostile input:
- only the five known file names are read, ignoring any others or any paths;
- each file is capped at 2 MB decompressed, whatever the zip claims, which stops zip bombs;
- the zip is capped at 8 MB;
- everything goes through the bot's own secret redaction as well as the backend's.

The reporter can ask for the bundle to be left out of the issue.

Every issue gets the `from-discord` and `needs-triage` labels, plus `bug`,
`enhancement` or `documentation`, and any area labels the model picked from
`BOT_ALLOWED_LABELS` that exist in the repo. The bot renders the body itself from fixed
fields. All user text passes through `sanitize.py`, which strips mentions (including
`@claude`), redacts secrets and home paths (webhook URLs, MakeMKV keys, API tokens,
`key=` values), escapes HTML and caps lengths. Bodies are kept under GitHub's 65,536-character
limit by dropping the bulkiest optional sections first. The footer credits the reporter and links back
to the Discord thread.

## Setup

### 1. Discord application

1. Create an application at <https://discord.com/developers/applications>, then add a bot.
2. Under **Bot → Privileged Gateway Intents**, enable **Message Content Intent**. Leave
   Server Members off; the bot doesn't need it.
3. Invite it with the `bot` and `applications.commands` scopes and these permissions:
   View Channels, Send Messages, Send Messages in Threads, Create Public Threads, Embed
   Links, Read Message History, Attach Files.
4. Create a **forum** channel for help (e.g. `#help`). If it has tags named `Bug` and
   `Feature`, `/bug` and `/feature` posts get tagged automatically.
5. Create a maintainer role (or reuse one) and give it to yourself.
6. Turn on Developer Mode in Discord, then copy the server ID, the forum's channel ID and
   the maintainer role ID.

### 2. GitHub App (recommended)

Use a GitHub App rather than a personal token, so issues show up as the app's bot account
rather than as you.

1. Go to **Settings → Developer settings → GitHub Apps → New GitHub App**. Turn off the
   webhook.
2. Repository permissions: **Issues: Read and write**. Metadata (read) is selected
   automatically. Nothing else.
3. Install it on `Jsakkos/engram` only.
4. Note the App ID and generate a private key (`.pem`).

The simpler alternative is a fine-grained personal access token with **Issues: Read and
write** on the one repository. Set it as `GITHUB_TOKEN` and remove the `.pem` volume line
from the compose file. Issues will then show up as authored by you.

### 3. Anthropic API key

Create a key at <https://console.anthropic.com/>. A spend limit on the key's workspace is a
good backstop on top of the bot's own `BOT_DAILY_BUDGET_USD`.

### 4. Run it (Docker)

```bash
cd deploy/discord-bot
cp engram-discord-bot.env.example .env     # fill in the IDs and secrets
cp ~/Downloads/your-app.private-key.pem github-app.pem
docker compose up -d --build
docker compose logs -f
```

On startup the bot:
- creates its two labels if they're missing;
- loads the docs from `main`, and reloads them every 6 hours;
- registers the slash commands on your server.

SQLite state (threads, drafts, spend) lives in the `bot-data` volume.

To update: `git pull && docker compose up -d --build`.

## Configuration

All settings come from environment variables; see
[`deploy/discord-bot/engram-discord-bot.env.example`](../deploy/discord-bot/engram-discord-bot.env.example).
The ones worth knowing:

| Variable | Default | |
|---|---|---|
| `BOT_MODEL` | `claude-haiku-5-5` | |
| `BOT_EFFORT` | `medium` | `low` is cheaper and terser; `high` reasons more. |
| `BOT_DAILY_BUDGET_USD` | `2.0` | Estimated spend over the last 24 hours. Past it, the bot posts one "back tomorrow" note per thread. |
| `BOT_MAX_ISSUES_PER_USER_PER_DAY` | `3` | Maintainers are exempt. |
| `BOT_MIN_ACCOUNT_AGE_DAYS` | `7` | Newer Discord accounts can chat but not file. |
| `BOT_MAX_TURNS_PER_USER_PER_HOUR` | `30` | |
| `BOT_GITHUB_POLL_MINUTES` | `10` | How often to check GitHub for issue updates, comments and releases. |
| `BOT_ESCALATION_MODEL` | unset | Stronger model for long or 👎 threads, e.g. `claude-sonnet-5-5`. |
| `BOT_ESCALATION_AFTER_TURNS` | `6` | User messages in a thread before escalating. |
| `BOT_DIGEST_WEEKDAY` / `BOT_DIGEST_HOUR_UTC` | `0` / `9` | When the weekly digest posts (0 = Monday). |
| `BOT_DOCS_INCLUDE` | user-facing docs | Comma-separated globs of repo paths to load. |
| `ENGRAM_DOCS_DIR` | unset | Read docs from a local checkout instead of GitHub (development). |

### What it knows

The bot sends the whole user-facing documentation to the model on every request:
`README.md`, `docs/` minus contributor and design material, and the three newest released
`CHANGELOG.md` sections, plus the latest release tag. That's about 145K characters,
roughly 40–50K tokens. It is sent as one cached system block (1-hour TTL), so repeat
requests mostly pay cache-read rates.

It stays under Claude Haiku 5.5's 100K-token threshold, above which the higher rate card
applies. The bot logs a warning if the docs grow past that.

`[Unreleased]` changelog entries are left out on purpose, so the bot never promises a
feature before it ships.

## Development

```bash
cd bot
uv sync
uv run pytest
uv run ruff check . && uv run ruff format --check .
```

To run it locally against a test server, export the variables from the env example, then
run `ENGRAM_DOCS_DIR=.. uv run python -m engram_bot`.

### Layout

| Module | |
|---|---|
| `discord_app.py` | Events, slash commands, persistent buttons, turn orchestration, feedback, digest. |
| `followup.py` | GitHub → Discord follow-through (state changes, maintainer comments, releases). |
| `agent.py` | Claude call and tool loop (`search_issues`, `get_issue`, `draft_issue`), usage and cost. |
| `prompts.py` | Standing instructions (cached) and the per-turn context note. |
| `knowledge.py` | Docs to system-prompt block, from GitHub or a local checkout. |
| `history.py` | Discord thread to Messages API history. |
| `intake.py` | Draft schema, validation, issue/comment rendering, embed fields. |
| `diagnostics.py` | Engram diagnostics `.zip` to a model summary and an issue section. |
| `sanitize.py` | Secret redaction, mention, HTML and length safety for anything published to GitHub. |
| `github.py` | Minimal REST client with GitHub App or token auth. |
| `store.py` | SQLite: threads, drafts, usage, follows, relays, feedback, meta. |
