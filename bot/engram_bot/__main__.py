"""Entry point: ``python -m engram_bot``."""

from __future__ import annotations

import asyncio
import logging
import os

import anthropic
import httpx
import jwt

from .agent import Agent, Prices
from .config import Settings
from .discord_app import EngramBot
from .github import GitHub, GitHubError
from .knowledge import KnowledgeBase
from .prompts import system_prompt
from .store import Store

log = logging.getLogger("engram_bot")


def build(settings: Settings) -> EngramBot:
    github = GitHub(
        settings.github_repo,
        token=None if settings.github_app_id else settings.github_token,
        app_id=settings.github_app_id,
        private_key=settings.github_app_private_key,
    )
    agent = Agent(
        anthropic.AsyncAnthropic(max_retries=3, timeout=120.0),
        github,
        model=settings.model,
        effort=settings.effort,
        max_tokens=settings.max_tokens,
        system_prompt=system_prompt(settings.maintainer_name),
        allowed_labels=settings.allowed_labels,
        prices=Prices(
            input=settings.price_input,
            output=settings.price_output,
            cache_read=settings.price_cache_read,
            cache_write=settings.price_cache_write,
            long_prompt_multiplier=settings.long_prompt_multiplier,
        ),
    )
    docs = github.for_repo(settings.docs_repo)
    knowledge = KnowledgeBase(
        docs,
        include=settings.docs_include,
        site_url=settings.docs_site_url,
        changelog_sections=settings.changelog_sections,
        docs_dir=settings.docs_dir,
    )
    return EngramBot(settings, Store(settings.db_path), agent, knowledge, github, releases=docs)


FATAL_STATUSES = (401, 403, 404)


async def credential_problem(github: GitHub, settings: Settings) -> str | None:
    """One clear sentence if GitHub rejects the configured credentials, else None.

    Without this check a wrong token (a Discord token pasted into GITHUB_TOKEN, say) let the
    bot run with a 401 traceback on every poll. Network trouble is not treated as fatal.
    """
    repo = settings.github_repo
    if settings.github_app_id:
        fix = (
            f"Check GITHUB_APP_ID and GITHUB_APP_PRIVATE_KEY(_FILE), and that the app is "
            f"installed on {repo} with Issues: Read and write"
        )
    else:
        fix = f"Check GITHUB_TOKEN: it needs Issues: Read and write on {repo}"
    fix += " (plus Contents: Read-only if the repo is private)."
    try:
        await github.check_access()
    except GitHubError as e:
        if e.status in FATAL_STATUSES:
            return f"GitHub rejected the bot's credentials for {repo} ({e}). {fix}"
        log.warning("could not verify GitHub access at startup: %s", e)
    except (ValueError, jwt.PyJWTError) as e:
        return f"The GitHub App private key could not be used ({e}). {fix}"
    except httpx.HTTPError as e:
        log.warning("could not reach GitHub at startup: %s", e)
    return None


async def main() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("discord.gateway").setLevel(logging.WARNING)
    settings = Settings.from_env()
    bot = build(settings)
    problem = await credential_problem(bot.gh, settings)
    if problem:
        log.error(problem)
        await bot.gh.aclose()
        raise SystemExit(1)
    try:
        async with bot:
            await bot.start(settings.discord_token)
    finally:
        await bot.gh.aclose()
        bot.store.close()


if __name__ == "__main__":
    asyncio.run(main())
