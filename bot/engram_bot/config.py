"""Bot settings, read once from the environment.

Every knob has a default except the three secrets (Discord token, Anthropic key, GitHub
credentials), so a deployment's ``.env`` only needs to name the server-specific IDs.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _int(name: str, default: int | None = None) -> int | None:
    raw = os.environ.get(name, "").strip()
    return int(raw) if raw else default


def _float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    return float(raw) if raw else default


def _list(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return tuple(part.strip() for part in raw.split(",") if part.strip())


@dataclass(frozen=True)
class Settings:
    discord_token: str
    github_repo: str = "Jsakkos/engram"

    # GitHub auth: a GitHub App (preferred, issues show as the app's bot account) or a
    # fine-grained token scoped to Issues read/write on the one repository.
    github_app_id: str | None = None
    github_app_private_key: str | None = None
    github_token: str | None = None

    # Discord wiring. The forum is the bot's home; the others are optional.
    guild_id: int | None = None
    help_forum_id: int | None = None
    maintainer_role_id: int | None = None
    maintainer_log_channel_id: int | None = None
    maintainer_name: str = "the maintainer"

    # Model.
    model: str = "claude-haiku-5-5"
    effort: str = "medium"
    max_tokens: int = 8000
    history_limit: int = 40

    # Knowledge: a local checkout (dev) or the repo's default branch via the GitHub API.
    docs_dir: Path | None = None
    docs_include: tuple[str, ...] = (
        "README.md",
        "docs/index.md",
        "docs/troubleshooting.md",
        "docs/getting-started/*.md",
        "docs/guide/*.md",
        "docs/deployment/*.md",
        "docs/api/rest.md",
        "docs/architecture/overview.md",
    )
    docs_site_url: str = "https://jsakkos.github.io/engram/"
    changelog_sections: int = 3
    knowledge_refresh_hours: float = 6.0
    github_poll_minutes: float = 10.0
    escalation_model: str = ""  # e.g. claude-sonnet-5-5; empty = never escalate
    escalation_after_turns: int = 6
    digest_weekday: int = 0  # Monday; digest posts to the maintainer log channel
    digest_hour_utc: int = 9

    # Labels the model may apply, on top of the two the bot always applies.
    allowed_labels: tuple[str, ...] = (
        "bug",
        "enhancement",
        "documentation",
        "ux",
        "question",
        "linux",
        "docker",
    )
    always_labels: tuple[str, ...] = ("from-discord", "needs-triage")

    # Abuse and spend limits.
    min_account_age_days: int = 7
    max_issues_per_user_per_day: int = 3
    max_turns_per_user_per_hour: int = 30
    daily_budget_usd: float = 2.0

    # Per-MTok prices used for the spend estimate (Claude Haiku 5.5, prompts <= 100K).
    price_input: float = 0.10
    price_output: float = 0.50
    price_cache_read: float = 0.01
    price_cache_write: float = 0.20  # 1-hour TTL writes bill at 2x input
    long_prompt_multiplier: float = 5.0  # > 100K-token prompts use the higher rate card

    db_path: Path = field(default_factory=lambda: Path("data/engram-bot.db"))

    @property
    def repo_owner(self) -> str:
        return self.github_repo.split("/", 1)[0]

    @property
    def repo_name(self) -> str:
        return self.github_repo.split("/", 1)[1]

    @classmethod
    def from_env(cls) -> Settings:
        token = os.environ.get("DISCORD_TOKEN", "").strip()
        if not token:
            raise SystemExit("DISCORD_TOKEN is required")
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise SystemExit("ANTHROPIC_API_KEY is required")

        app_id = os.environ.get("GITHUB_APP_ID") or None
        private_key = os.environ.get("GITHUB_APP_PRIVATE_KEY")
        key_file = os.environ.get("GITHUB_APP_PRIVATE_KEY_FILE")
        # The compose file always mounts the key path; with a token setup it is absent.
        if app_id and not private_key and key_file and Path(key_file).is_file():
            private_key = Path(key_file).read_text()
        gh_token = os.environ.get("GITHUB_TOKEN") or None
        if not ((app_id and private_key) or gh_token):
            raise SystemExit(
                "GitHub credentials are required: GITHUB_APP_ID + GITHUB_APP_PRIVATE_KEY(_FILE), "
                "or GITHUB_TOKEN"
            )

        defaults = cls(discord_token="")
        docs_dir = os.environ.get("ENGRAM_DOCS_DIR")
        return cls(
            discord_token=token,
            github_repo=os.environ.get("GITHUB_REPO", defaults.github_repo),
            github_app_id=app_id,
            github_app_private_key=private_key,
            github_token=gh_token,
            guild_id=_int("DISCORD_GUILD_ID"),
            help_forum_id=_int("DISCORD_HELP_FORUM_ID"),
            maintainer_role_id=_int("DISCORD_MAINTAINER_ROLE_ID"),
            maintainer_log_channel_id=_int("DISCORD_MAINTAINER_LOG_CHANNEL_ID"),
            maintainer_name=os.environ.get("MAINTAINER_NAME", defaults.maintainer_name),
            model=os.environ.get("BOT_MODEL", defaults.model),
            effort=os.environ.get("BOT_EFFORT", defaults.effort),
            max_tokens=_int("BOT_MAX_TOKENS", defaults.max_tokens),
            history_limit=_int("BOT_HISTORY_LIMIT", defaults.history_limit),
            docs_dir=Path(docs_dir) if docs_dir else None,
            docs_include=_list("BOT_DOCS_INCLUDE", defaults.docs_include),
            docs_site_url=os.environ.get("DOCS_SITE_URL", defaults.docs_site_url),
            changelog_sections=_int("BOT_CHANGELOG_SECTIONS", defaults.changelog_sections),
            knowledge_refresh_hours=_float(
                "BOT_KNOWLEDGE_REFRESH_HOURS", defaults.knowledge_refresh_hours
            ),
            github_poll_minutes=_float("BOT_GITHUB_POLL_MINUTES", defaults.github_poll_minutes),
            escalation_model=os.environ.get("BOT_ESCALATION_MODEL", defaults.escalation_model),
            escalation_after_turns=_int(
                "BOT_ESCALATION_AFTER_TURNS", defaults.escalation_after_turns
            ),
            digest_weekday=_int("BOT_DIGEST_WEEKDAY", defaults.digest_weekday),
            digest_hour_utc=_int("BOT_DIGEST_HOUR_UTC", defaults.digest_hour_utc),
            allowed_labels=_list("BOT_ALLOWED_LABELS", defaults.allowed_labels),
            always_labels=_list("BOT_ALWAYS_LABELS", defaults.always_labels),
            min_account_age_days=_int("BOT_MIN_ACCOUNT_AGE_DAYS", defaults.min_account_age_days),
            max_issues_per_user_per_day=_int(
                "BOT_MAX_ISSUES_PER_USER_PER_DAY", defaults.max_issues_per_user_per_day
            ),
            max_turns_per_user_per_hour=_int(
                "BOT_MAX_TURNS_PER_USER_PER_HOUR", defaults.max_turns_per_user_per_hour
            ),
            daily_budget_usd=_float("BOT_DAILY_BUDGET_USD", defaults.daily_budget_usd),
            db_path=Path(os.environ.get("BOT_DB_PATH", str(defaults.db_path))),
        )
