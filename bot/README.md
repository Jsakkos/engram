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

**Issues are only filed when a person presses a button.** The model can *propose* a draft
(the `draft_issue` tool). The bot shows the draft as an embed with **File issue / Edit /
Cancel** buttons, and only the reporter or a maintainer can press them. Before drafting,
the model searches existing issues. If it finds the same problem, the button reads **Add
to issue** and posts the report as a comment on that issue instead.

Every issue gets the `from-discord` and `needs-triage` labels, plus `bug`,
`enhancement` or `documentation`, and any area labels the model picked from
`BOT_ALLOWED_LABELS` that exist in the repo. The bot renders the body itself from fixed
fields. All user text passes through `sanitize.py`, which strips mentions (including
`@claude`), escapes HTML and caps lengths. The footer credits the reporter and links back
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
| `discord_app.py` | Events, slash commands, persistent draft buttons, turn orchestration. |
| `agent.py` | Claude call and tool loop (`search_issues`, `get_issue`, `draft_issue`), usage and cost. |
| `prompts.py` | Standing instructions (cached) and the per-turn context note. |
| `knowledge.py` | Docs to system-prompt block, from GitHub or a local checkout. |
| `history.py` | Discord thread to Messages API history. |
| `intake.py` | Draft schema, validation, issue/comment rendering, embed fields. |
| `sanitize.py` | Mention, HTML and length safety for anything published to GitHub. |
| `github.py` | Minimal REST client with GitHub App or token auth. |
| `store.py` | SQLite: threads, drafts, usage. |
