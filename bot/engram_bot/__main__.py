"""Entry point: ``python -m engram_bot``."""

from __future__ import annotations

import asyncio
import logging
import os

import anthropic

from .agent import Agent, Prices
from .config import Settings
from .discord_app import EngramBot
from .github import GitHub
from .knowledge import KnowledgeBase
from .prompts import system_prompt
from .store import Store


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
    knowledge = KnowledgeBase(
        github,
        include=settings.docs_include,
        site_url=settings.docs_site_url,
        changelog_sections=settings.changelog_sections,
        docs_dir=settings.docs_dir,
    )
    return EngramBot(settings, Store(settings.db_path), agent, knowledge, github)


async def main() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("discord.gateway").setLevel(logging.WARNING)
    settings = Settings.from_env()
    bot = build(settings)
    try:
        async with bot:
            await bot.start(settings.discord_token)
    finally:
        await bot.gh.aclose()
        bot.store.close()


if __name__ == "__main__":
    asyncio.run(main())
