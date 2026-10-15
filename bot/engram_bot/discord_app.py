"""Discord wiring: where the bot listens, how a turn runs, and the draft buttons.

Where it answers:
- every post in the help forum (the post's author is the "reporter");
- a thread it opens when someone @mentions it in a text channel;
- threads it opens for /bug and /feature.
In those threads it replies to the reporter, and to anyone else only when mentioned. When
a maintainer joins a thread the bot steps back until someone mentions it again.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import time
from collections import OrderedDict, defaultdict
from datetime import UTC, datetime

import discord
from discord import app_commands

from . import diagnostics, history, intake, sanitize
from .agent import FALLBACK_TEXT, Agent, TurnResult
from .config import Settings
from .followup import FollowUp
from .github import GitHub, GitHubError
from .knowledge import KnowledgeBase
from .prompts import TurnContext
from .store import DAY, Digest, Store, ThreadInfo

log = logging.getLogger(__name__)

DEBOUNCE_SECONDS = 2.0
DISCORD_LIMIT = 2000
TEXT_ATTACHMENT_LIMIT = 50_000
TEXT_SUFFIXES = (".txt", ".log", ".md", ".json", ".yaml", ".yml", ".ini", ".csv")
KIND_COLOR = {"bug": 0xE5484D, "feature": 0x22D3EE, "docs": 0xA78BFA}
LABEL_COLORS = {"from-discord": ("5865F2", "Reported through the Discord support bot")}
DEFAULT_LABEL = ("EDEDED", "Created by the Discord support bot")

STEP_BACK_TEXT = (
    "A maintainer has joined, so I'll step back. Mention me if you'd like me to jump in again."
)
BUDGET_TEXT = (
    "I've reached my usage limit for today, so I can't answer right now. A maintainer will "
    "see this thread, or you can try me again tomorrow."
)
RATE_TEXT = "You're sending me a lot of messages. Give me a little while and try again."


FEEDBACK_EMOJI = {"👍": 1, "👎": -1}


def render_digest(d: Digest, repo: str, guild_id: int | str = "@me") -> str:
    """The weekly maintainer summary. Plain text so it reads in any client."""
    lines = [
        "**Engram support bot: weekly digest**",
        f"- {d.threads} new thread(s), {d.turns} answer(s), ~${d.spend_usd:.2f} spent",
        f"- {d.drafts} draft(s): {len(d.filed)} filed, {d.cancelled} cancelled",
        f"- {d.relays} maintainer comment(s) relayed to Discord",
        f"- Feedback: {d.thumbs_up} 👍 / {d.thumbs_down} 👎",
    ]
    if d.filed:
        lines.append("\n**Filed from Discord**")
        for number, payload, url in d.filed[:25]:
            title = str(payload.get("title", ""))[:100]
            lines.append(
                f"- #{number} {title} <{url or f'https://github.com/{repo}/issues/{number}'}>"
            )
    if d.downvoted:
        lines.append("\n**Answers people marked unhelpful** (often a docs gap)")
        for thread_id, message_id in d.downvoted:
            lines.append(f"- https://discord.com/channels/{guild_id}/{thread_id}/{message_id}")
    if not (d.threads or d.turns or d.filed):
        lines.append("\nA quiet week.")
    return "\n".join(lines)


def chunk(text: str, limit: int = DISCORD_LIMIT) -> list[str]:
    """Split to fit Discord's message cap at the last paragraph, line or word break."""
    chunks: list[str] = []
    text = text.strip()
    while len(text) > limit:
        for sep in ("\n\n", "\n", " "):
            cut = text.rfind(sep, 0, limit)
            if cut > 0:
                break
        else:
            cut = limit
        chunks.append(text[:cut].rstrip())
        text = text[cut:].lstrip()
    if text:
        chunks.append(text)
    return chunks


class DraftButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"engram:draft:(?P<action>file|edit|cancel):(?P<id>\d+)",
):
    """Persistent button: the action and draft id live in the custom_id, so buttons keep
    working after the bot restarts."""

    STYLES = {
        "file": ("File issue", discord.ButtonStyle.success),
        "edit": ("Edit", discord.ButtonStyle.secondary),
        "cancel": ("Cancel", discord.ButtonStyle.danger),
    }

    def __init__(self, action: str, draft_id: int, *, comment: bool = False) -> None:
        label, style = self.STYLES[action]
        if action == "file" and comment:
            label = "Add to issue"
        super().__init__(
            discord.ui.Button(
                label=label, style=style, custom_id=f"engram:draft:{action}:{draft_id}"
            )
        )
        self.action = action
        self.draft_id = draft_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match, /):  # noqa: D102
        return cls(match["action"], int(match["id"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        bot = interaction.client
        assert isinstance(bot, EngramBot)
        await bot.on_draft_button(interaction, self.action, self.draft_id)


def draft_view(draft_id: int, comment: bool) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    for action in ("file", "edit", "cancel"):
        view.add_item(DraftButton(action, draft_id, comment=comment))
    return view


def draft_embed(d: intake.Draft, draft_id: int) -> discord.Embed:
    heading = (
        f"Add to issue #{d.duplicate_of}" if d.is_comment else f"Draft {intake.KIND_TITLE[d.kind]}"
    )
    embed = discord.Embed(
        title=heading,
        description=f"**{d.title[:250]}**\n\n{d.summary[:3500]}",
        color=KIND_COLOR.get(d.kind, 0x888888),
    )
    for name, value in intake.preview_fields(d):
        embed.add_field(name=name, value=value, inline=False)
    labels = ", ".join(d.labels) or "none"
    embed.set_footer(
        text=f"Draft {draft_id} · labels: {labels} · Nothing is filed until you press the "
        "button. Issues are public."
    )
    return embed


class ReplyButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"engram:reply:(?P<id>\d+)",
):
    """Under a relayed maintainer comment: lets the reporter answer on the issue."""

    def __init__(self, relay_id: int) -> None:
        super().__init__(
            discord.ui.Button(
                label="Reply on GitHub",
                style=discord.ButtonStyle.primary,
                custom_id=f"engram:reply:{relay_id}",
            )
        )
        self.relay_id = relay_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match, /):  # noqa: D102
        return cls(int(match["id"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        bot = interaction.client
        assert isinstance(bot, EngramBot)
        await bot.on_reply_button(interaction, self.relay_id)


class ReplyModal(discord.ui.Modal):
    reply = discord.ui.TextInput(
        label="Your reply",
        style=discord.TextStyle.paragraph,
        max_length=3000,
        placeholder="This is posted publicly on the GitHub issue, credited to your Discord name.",
    )

    def __init__(self, bot: EngramBot, relay_id: int, issue_number: int) -> None:
        super().__init__(title=f"Reply on issue #{issue_number}")
        self.bot = bot
        self.relay_id = relay_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.bot.post_reply(interaction, self.relay_id, str(self.reply))


class BugModal(discord.ui.Modal):
    summary = discord.ui.TextInput(label="Short summary", max_length=90)
    details = discord.ui.TextInput(
        label="Details",
        style=discord.TextStyle.paragraph,
        max_length=3500,
        placeholder="What happened, what you expected, Engram version and OS if you know them.",
    )

    def __init__(self, bot: EngramBot, kind: str) -> None:
        super().__init__(title="Report a bug" if kind == "bug" else "Request a feature")
        self.bot = bot
        self.kind = kind
        if kind == "feature":
            self.details.placeholder = "What are you trying to do, and what would help?"

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.bot.start_intake_thread(
            interaction, self.kind, str(self.summary), str(self.details)
        )


class EngramBot(discord.Client):
    def __init__(
        self,
        settings: Settings,
        store: Store,
        agent: Agent,
        knowledge: KnowledgeBase,
        github: GitHub,
        releases: GitHub | None = None,
    ) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents, allowed_mentions=discord.AllowedMentions.none())
        self.settings = settings
        self.store = store
        self.agent = agent
        self.kb = knowledge
        self.gh = github
        self.tree = app_commands.CommandTree(self)
        self.followup = FollowUp(github, store, self.send_to_thread, releases=releases)
        self._locks: defaultdict[int, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._latest_trigger: dict[int, int] = {}
        self._budget_noticed: dict[int, float] = {}
        self._attachment_cache: OrderedDict[int, str] = OrderedDict()
        # attachment id -> parsed bundle, or the reason it couldn't be read
        self._bundle_cache: OrderedDict[int, diagnostics.Diagnostics | str] = OrderedDict()
        self._register_commands()

    # -- lifecycle --------------------------------------------------------------------

    async def setup_hook(self) -> None:
        self.add_dynamic_items(DraftButton, ReplyButton)
        await self.kb.refresh()
        await self._prepare_labels()
        if self.settings.guild_id:
            guild = discord.Object(id=self.settings.guild_id)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()
        self._refresh_task = asyncio.create_task(self._refresh_loop())
        self._poll_task = asyncio.create_task(self._poll_loop())
        self._digest_task = asyncio.create_task(self._digest_loop())

    async def on_ready(self) -> None:
        log.info("connected as %s", self.user)

    async def _poll_loop(self) -> None:
        await self.wait_until_ready()
        while not self.is_closed():
            try:
                await self.followup.poll()
            except Exception:
                log.exception("follow-through poll crashed; retrying next interval")
            await asyncio.sleep(self.settings.github_poll_minutes * 60)

    async def send_to_thread(
        self, thread_id: int, content: str, relay_id: int | None
    ) -> int | None:
        """Post a follow-through notice. Returns the message id; 0 means the thread is gone
        for good (don't retry); None means try again on the next poll."""
        try:
            channel = self.get_channel(thread_id) or await self.fetch_channel(thread_id)
        except (discord.NotFound, discord.Forbidden):
            log.warning("thread %s is gone or hidden; dropping follow-through", thread_id)
            return 0
        except discord.HTTPException:
            log.info("could not fetch thread %s", thread_id, exc_info=True)
            return None
        if not isinstance(channel, discord.Thread):
            return 0
        view = None
        if relay_id is not None:
            view = discord.ui.View(timeout=None)
            view.add_item(ReplyButton(relay_id))
        try:
            message = await channel.send(content[:DISCORD_LIMIT], view=view)
        except discord.Forbidden:
            log.warning("not allowed to post in thread %s", thread_id)
            return 0
        except discord.HTTPException:
            log.info("could not post in thread %s", thread_id, exc_info=True)
            return None
        return message.id

    async def _refresh_loop(self) -> None:
        while not self.is_closed():
            await asyncio.sleep(self.settings.knowledge_refresh_hours * 3600)
            await self.kb.refresh()

    async def _prepare_labels(self) -> None:
        """Create the bot's own labels and limit the model to labels the repo really has."""
        try:
            for name in self.settings.always_labels:
                color, description = LABEL_COLORS.get(name, DEFAULT_LABEL)
                await self.gh.ensure_label(name, color, description)
            existing = await self.gh.list_labels()
        except GitHubError:
            log.warning("could not prepare labels; keeping the configured list", exc_info=True)
            return
        allowed = tuple(lbl for lbl in self.settings.allowed_labels if lbl in existing)
        dropped = set(self.settings.allowed_labels) - set(allowed)
        if dropped:
            log.info("labels not in the repo, not offered to the model: %s", sorted(dropped))
        self.agent.set_allowed_labels(allowed)

    # -- permissions & limits ---------------------------------------------------------

    def is_maintainer(self, user: discord.abc.User) -> bool:
        role_id = self.settings.maintainer_role_id
        if role_id is None or not isinstance(user, discord.Member):
            return False
        return any(role.id == role_id for role in user.roles)

    def filing_block(self, user: discord.abc.User) -> str:
        """Why this user can't file an issue right now, or '' if they can."""
        if self.is_maintainer(user):
            return ""
        age_days = (discord.utils.utcnow() - user.created_at).days
        if age_days < self.settings.min_account_age_days:
            return "their Discord account is too new"
        filed = self.store.issues_filed_since(user.id, time.time() - DAY)
        if filed >= self.settings.max_issues_per_user_per_day:
            return "they've reached today's limit for new issues"
        return ""

    def over_budget(self) -> bool:
        return self.store.spend_since(time.time() - DAY) >= self.settings.daily_budget_usd

    def over_rate(self, user_id: int) -> bool:
        turns = self.store.turns_since(user_id, time.time() - 3600)
        return turns >= self.settings.max_turns_per_user_per_hour

    # -- message routing --------------------------------------------------------------

    def turn_model(self, thread_id: int, conversation: list[dict]) -> str | None:
        """The escalation model for long threads or after a 👎, if one is configured."""
        model = self.settings.escalation_model
        if not model:
            return None
        user_turns = sum(1 for m in conversation if m["role"] == "user")
        if user_turns >= self.settings.escalation_after_turns:
            return model
        if self.store.thread_downvoted(thread_id):
            return model
        return None

    # -- feedback ---------------------------------------------------------------------

    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        await self._on_reaction(payload, added=True)

    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent) -> None:
        await self._on_reaction(payload, added=False)

    async def _on_reaction(self, payload: discord.RawReactionActionEvent, *, added: bool):
        value = FEEDBACK_EMOJI.get(str(payload.emoji))
        if value is None or self.user is None or payload.user_id == self.user.id:
            return
        if not self.store.is_bot_message(payload.message_id):
            return
        self.store.set_feedback(payload.message_id, payload.user_id, value if added else None)

    # -- weekly digest ----------------------------------------------------------------

    async def _digest_loop(self) -> None:
        await self.wait_until_ready()
        while not self.is_closed():
            try:
                await self.maybe_post_digest()
            except Exception:
                log.exception("digest failed; retrying next hour")
            await asyncio.sleep(3600)

    async def maybe_post_digest(self, now: datetime | None = None) -> bool:
        channel_id = self.settings.maintainer_log_channel_id
        if not channel_id:
            return False
        now = now or datetime.now(UTC)
        if now.weekday() != self.settings.digest_weekday:
            return False
        if now.hour < self.settings.digest_hour_utc:
            return False
        last = self.store.get_meta("digest_at")
        if last and now.timestamp() - float(last) < 6 * DAY:
            return False
        channel = self.get_channel(channel_id)
        if not isinstance(channel, discord.abc.Messageable):
            return False
        since = float(last) if last else now.timestamp() - 7 * DAY
        guild = getattr(channel, "guild", None)
        text = render_digest(
            self.store.digest(since), self.settings.github_repo, guild.id if guild else "@me"
        )
        for part in chunk(text):
            await channel.send(part)
        self.store.set_meta("digest_at", str(now.timestamp()))
        return True

    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or message.guild is None or self.user is None:
            return
        channel = message.channel
        mentioned = self.user in message.mentions

        if isinstance(channel, discord.Thread):
            info = self.store.thread(channel.id)
            if info is None and channel.parent_id == self.settings.help_forum_id:
                self.store.add_thread(channel.id, channel.owner_id or message.author.id, "forum")
                info = self.store.thread(channel.id)
            if info is None:
                return
            author = message.author
            if info.muted and not mentioned:
                return
            if self.is_maintainer(author) and author.id != info.reporter_id and not mentioned:
                self.store.set_muted(channel.id, True)
                await channel.send(STEP_BACK_TEXT)
                return
            if author.id != info.reporter_id and not mentioned:
                return
            if info.muted:
                self.store.set_muted(channel.id, False)
            await self.handle_turn(channel, info, author, trigger_id=message.id)
            return

        if mentioned and isinstance(channel, discord.TextChannel):
            name = self._thread_name(message.clean_content) or "Engram help"
            thread = await message.create_thread(name=name, auto_archive_duration=1440)
            self.store.add_thread(thread.id, message.author.id, "mention")
            info = self.store.thread(thread.id)
            assert info is not None
            await self.handle_turn(thread, info, message.author, trigger_id=message.id)

    def _thread_name(self, text: str) -> str:
        assert self.user is not None
        text = text.replace(f"@{self.user.display_name}", "").strip()
        text = " ".join(text.split())
        return text[:80]

    # -- a turn -----------------------------------------------------------------------

    async def handle_turn(
        self,
        thread: discord.Thread,
        info: ThreadInfo,
        author: discord.abc.User,
        *,
        trigger_id: int | None = None,
    ) -> None:
        if trigger_id is not None:
            self._latest_trigger[thread.id] = trigger_id
        async with self._locks[thread.id]:
            if trigger_id is not None:
                # Wait briefly: people often send a thought as several messages.
                await asyncio.sleep(DEBOUNCE_SECONDS)
                if self._latest_trigger.get(thread.id) != trigger_id:
                    return
            if self.over_budget():
                last = self._budget_noticed.get(thread.id, 0)
                if time.time() - last > DAY:
                    self._budget_noticed[thread.id] = time.time()
                    await thread.send(BUDGET_TEXT)
                return
            if self.over_rate(author.id):
                await thread.send(RATE_TEXT)
                return

            reporter = await self._member(thread.guild, info.reporter_id) or author
            block = self.filing_block(reporter)
            context = TurnContext(
                mode="thread",
                reporter_name=reporter.display_name,
                thread_kind=info.kind,
                can_file=not block,
                cannot_file_reason=block,
            )
            try:
                async with thread.typing():
                    items, bundle = await self._history(thread, info)
                    conversation = history.build_messages(items)
                    if not conversation:
                        return
                    result = await self.agent.respond(
                        conversation,
                        context,
                        self.kb.text,
                        model=self.turn_model(thread.id, conversation),
                    )
                if result.draft is not None and bundle is not None:
                    result.draft.attach_diagnostics(bundle.issue_section(), bundle.preview())
                self._record(result, author.id, thread.id)
                await self._deliver(thread, info, result)
            except Exception:
                # Never leave someone talking to a silent bot.
                log.exception("turn failed in thread %s", thread.id)
                with contextlib.suppress(discord.HTTPException):
                    await thread.send(FALLBACK_TEXT)

    def _record(self, result: TurnResult, user_id: int | None, thread_id: int | None) -> None:
        u = result.usage
        self.store.record_usage(
            user_id=user_id,
            thread_id=thread_id,
            input_tokens=u.input_tokens,
            output_tokens=u.output_tokens,
            cache_read=u.cache_read,
            cache_write=u.cache_write,
            cost_usd=u.cost_usd,
        )
        log.info(
            "turn thread=%s tools=%s in=%d out=%d cache_read=%d cache_write=%d cost=$%.4f",
            thread_id,
            result.tools_used,
            u.input_tokens,
            u.output_tokens,
            u.cache_read,
            u.cache_write,
            u.cost_usd,
        )

    async def _deliver(self, thread: discord.Thread, info: ThreadInfo, result: TurnResult):
        for part in chunk(result.text):
            sent = await thread.send(part)
            self.store.add_bot_message(sent.id, thread.id, result.model)
        if result.draft is None:
            return
        for old in self.store.supersede_drafts(thread.id):
            await self._close_draft_message(thread, old.message_id, "Replaced by a newer draft.")
        draft_id = self.store.add_draft(thread.id, info.reporter_id, result.draft.to_json())
        message = await thread.send(
            embed=draft_embed(result.draft, draft_id),
            view=draft_view(draft_id, result.draft.is_comment),
        )
        self.store.set_draft_message(draft_id, message.id)

    async def _close_draft_message(
        self, channel: discord.abc.Messageable, message_id: int | None, footer: str
    ) -> None:
        if message_id is None:
            return
        try:
            message = await channel.fetch_message(message_id)
            embed = message.embeds[0] if message.embeds else None
            if embed is not None:
                embed.set_footer(text=footer)
            await message.edit(embed=embed, view=None)
        except discord.HTTPException:
            log.debug("could not update draft message %s", message_id, exc_info=True)

    async def _member(self, guild: discord.Guild, user_id: int) -> discord.Member | None:
        member = guild.get_member(user_id)
        if member is not None:
            return member
        try:
            return await guild.fetch_member(user_id)
        except discord.HTTPException:
            return None

    async def _history(
        self, thread: discord.Thread, info: ThreadInfo
    ) -> tuple[list[history.Item], diagnostics.Diagnostics | None]:
        """The thread as model history, plus the reporter's latest diagnostics bundle."""
        messages = [m async for m in thread.history(limit=self.settings.history_limit)]
        messages.reverse()
        if info.origin == "mention" and (not messages or messages[0].id != thread.id):
            # A thread opened from a message: the question itself lives in the parent channel.
            try:
                if isinstance(thread.parent, discord.TextChannel):
                    messages.insert(0, await thread.parent.fetch_message(thread.id))
            except discord.HTTPException:
                # Starter deleted or unreadable: answer from the thread's own messages.
                log.info("could not fetch the starter message of thread %s", thread.id)
        drafts = self.store.drafts_by_message([m.id for m in messages if m.author == self.user])
        items: list[history.Item] = []
        bundle: diagnostics.Diagnostics | None = None
        for m in messages:
            if m.author == self.user:
                if m.id == thread.id and info.origin == "command":
                    # The /bug or /feature starter post: the bot wrote it, the user said it.
                    text = re.sub(r"<@!?\d+>", "", m.content)
                    items.append(history.Item("user", text, self._name(m, info)))
                elif m.id in drafts:
                    row = drafts[m.id]
                    draft = intake.Draft.from_json(row.payload)
                    note = intake.describe_for_history(draft, row.id)
                    items.append(history.Item("assistant", f"{note} (status: {row.status})"))
                elif m.content:
                    items.append(history.Item("assistant", m.content))
                continue
            if m.author.bot or m.type not in (
                discord.MessageType.default,
                discord.MessageType.reply,
            ):
                continue
            text = m.content
            if self.user is not None:
                text = text.replace(self.user.mention, "").replace(f"<@!{self.user.id}>", "")
            extra, bundles = await self._attachments_text(m)
            text += extra
            if bundles and m.author.id == info.reporter_id:
                bundle = bundles[-1]
            items.append(history.Item("user", text, m.author.display_name))
        return items, bundle

    def _name(self, m: discord.Message, info: ThreadInfo) -> str:
        member = m.guild.get_member(info.reporter_id) if m.guild else None
        return member.display_name if member else "reporter"

    async def _attachments_text(
        self, m: discord.Message
    ) -> tuple[str, list[diagnostics.Diagnostics]]:
        """Readable attachments as text for the model, plus any diagnostics bundles."""
        parts: list[str] = []
        bundles: list[diagnostics.Diagnostics] = []
        for a in m.attachments:
            name = a.filename.lower()
            if name.endswith(".zip"):
                parsed = await self._read_bundle(a)
                if isinstance(parsed, diagnostics.Diagnostics):
                    bundles.append(parsed)
                    parts.append(parsed.summary_for_model())
                else:
                    parts.append(
                        f"[attached {a.filename}: not a readable Engram diagnostics bundle "
                        f"({parsed}). The bundle is the .zip downloaded from a job's detail "
                        "view in the dashboard.]"
                    )
                continue
            is_text = (a.content_type or "").startswith("text/") or name.endswith(TEXT_SUFFIXES)
            if not is_text:
                continue
            if a.size > TEXT_ATTACHMENT_LIMIT:
                parts.append(f"[attached {a.filename}: too large to read ({a.size} bytes)]")
                continue
            text = self._attachment_cache.get(a.id)
            if text is None:
                try:
                    raw = (await a.read()).decode("utf-8", errors="replace")
                except discord.HTTPException:
                    log.info("could not download attachment %s", a.filename, exc_info=True)
                    continue
                text = sanitize.redact(raw)
                self._remember(self._attachment_cache, a.id, text)
            parts.append(f"[attached {a.filename}]\n```\n{text}\n```")
        return (("\n\n" + "\n\n".join(parts)) if parts else ""), bundles

    async def _read_bundle(self, a: discord.Attachment) -> diagnostics.Diagnostics | str:
        cached = self._bundle_cache.get(a.id)
        if cached is not None:
            return cached
        if a.size > diagnostics.MAX_ZIP_BYTES:
            result: diagnostics.Diagnostics | str = "it's larger than 8 MB"
        else:
            try:
                data = await a.read()
            except discord.HTTPException:
                log.info("could not download attachment %s", a.filename, exc_info=True)
                return "it couldn't be downloaded"  # not cached: worth retrying next turn
            try:
                result = await asyncio.to_thread(diagnostics.parse, data, a.filename)
            except diagnostics.NotABundle as e:
                result = str(e)
        self._remember(self._bundle_cache, a.id, result)
        return result

    @staticmethod
    def _remember(cache: OrderedDict, key: int, value) -> None:
        cache[key] = value
        if len(cache) > 200:
            cache.popitem(last=False)

    # -- draft buttons ----------------------------------------------------------------

    async def on_draft_button(
        self, interaction: discord.Interaction, action: str, draft_id: int
    ) -> None:
        row = self.store.draft(draft_id)
        if row is None:
            await interaction.response.send_message("That draft no longer exists.", ephemeral=True)
            return
        user = interaction.user
        if user.id != row.reporter_id and not self.is_maintainer(user):
            await interaction.response.send_message(
                "Only the person who reported this can file or change it.", ephemeral=True
            )
            return
        if row.status != "pending":
            await interaction.response.send_message(
                f"That draft is already {row.status}.", ephemeral=True
            )
            return

        if action == "cancel":
            self.store.cancel_draft(draft_id)
            await interaction.response.edit_message(view=None)
            await interaction.followup.send("Okay, I won't file it.")
            return
        if action == "edit":
            await interaction.response.send_message(
                "Sure, tell me what to change and I'll redo the draft."
            )
            return

        block = self.filing_block(user)
        if block:
            await interaction.response.send_message(
                f"I can't file this right now: {block}. A maintainer will see this thread.",
                ephemeral=True,
            )
            return
        if not self.store.claim_draft(draft_id):
            await interaction.response.send_message(
                "That draft is already being filed.", ephemeral=True
            )
            return
        await interaction.response.defer(thinking=True)

        draft = intake.Draft.from_json(row.payload)
        reporter_member = (
            await self._member(interaction.guild, row.reporter_id) if interaction.guild else None
        )
        channel = interaction.channel
        thread_url = getattr(channel, "jump_url", "") or ""
        reporter = intake.Reporter(
            display_name=reporter_member.display_name if reporter_member else user.display_name,
            thread_url=thread_url,
        )
        try:
            if draft.is_comment:
                target = await self.gh.get_issue(draft.duplicate_of)
                if target is None:
                    raise GitHubError(f"issue #{draft.duplicate_of} does not exist")
                comment = await self.gh.create_comment(
                    draft.duplicate_of, intake.render_comment(draft, reporter)
                )
                self.store.add_own_comment(comment.id)
                number, url = draft.duplicate_of, comment.url
                self.store.add_follow(
                    number,
                    row.thread_id,
                    row.reporter_id,
                    "comment",
                    target.state,
                    target.state_reason,
                )
                done = f"Added your report to #{number}: <{url}>"
            else:
                issue = await self.gh.create_issue(
                    draft.issue_title(),
                    intake.render_body(draft, reporter),
                    draft.issue_labels(self.settings.always_labels),
                )
                number, url = issue.number, issue.url
                self.store.add_follow(number, row.thread_id, row.reporter_id, "issue")
                done = (
                    f"Filed as #{number}: <{url}>\n"
                    f"Thanks! {self.settings.maintainer_name} will take it from here."
                )
        except GitHubError as e:
            log.error("filing draft %s failed: %s", draft_id, e)
            self.store.release_draft(draft_id)
            await interaction.followup.send(
                "GitHub didn't accept that just now. Press the button again in a minute."
            )
            return

        self.store.finish_draft(draft_id, number, url)
        if interaction.message is not None:
            await self._close_draft_message(channel, interaction.message.id, f"Filed: #{number}")
        await interaction.followup.send(done)
        await self._log_filed(number, url, thread_url, draft)

    async def on_reply_button(self, interaction: discord.Interaction, relay_id: int) -> None:
        relay = self.store.relay(relay_id)
        if relay is None:
            await interaction.response.send_message(
                "I can't find that comment any more.", ephemeral=True
            )
            return
        user = interaction.user
        if user.id != relay.reporter_id and not self.is_maintainer(user):
            await interaction.response.send_message(
                "Only the person who reported this can reply from here. You can comment on "
                f"the issue directly: <{relay.url}>",
                ephemeral=True,
            )
            return
        await interaction.response.send_modal(ReplyModal(self, relay_id, relay.issue_number))

    async def post_reply(self, interaction: discord.Interaction, relay_id: int, text: str):
        relay = self.store.relay(relay_id)
        if relay is None or not text.strip():
            await interaction.response.send_message("Nothing was posted.", ephemeral=True)
            return
        if self.over_rate(interaction.user.id):
            await interaction.response.send_message(RATE_TEXT, ephemeral=True)
            return
        block = self.filing_block(interaction.user)
        if block.startswith("their Discord account"):  # the daily issue cap doesn't apply
            await interaction.response.send_message(
                f"I can't post that for you: {block}. A maintainer will see this thread.",
                ephemeral=True,
            )
            return
        await interaction.response.defer(thinking=True)
        name = sanitize.clean_inline(interaction.user.display_name, 80)
        thread_url = getattr(interaction.channel, "jump_url", "") or ""
        body = (
            f"{sanitize.clean(text, 3000)}\n\n---\n"
            f"_Reply from **{name}** on Discord ([conversation]({thread_url})), "
            f"answering @{sanitize.ZWSP}{sanitize.clean_inline(relay.author, 40)}._"
        )
        try:
            comment = await self.gh.create_comment(relay.issue_number, body)
        except GitHubError as e:
            log.error("posting reply to #%s failed: %s", relay.issue_number, e)
            await interaction.followup.send(
                "GitHub didn't accept that just now. Press the button again in a minute."
            )
            return
        self.store.add_own_comment(comment.id)
        # Counts toward the per-user rate limit like a model turn, at no cost.
        self.store.record_usage(
            user_id=interaction.user.id,
            thread_id=relay.thread_id,
            input_tokens=0,
            output_tokens=0,
            cache_read=0,
            cache_write=0,
            cost_usd=0.0,
        )
        await interaction.followup.send(
            f"Posted your reply on #{relay.issue_number}: <{comment.url}>"
        )

    async def _log_filed(self, number: int, url: str, thread_url: str, d: intake.Draft) -> None:
        channel_id = self.settings.maintainer_log_channel_id
        if not channel_id:
            return
        channel = self.get_channel(channel_id)
        if isinstance(channel, discord.abc.Messageable):
            verb = "Comment on" if d.is_comment else f"New {d.kind}"
            await channel.send(f"{verb} #{number}: {d.title[:200]}\n<{url}> · from {thread_url}")

    # -- slash commands ---------------------------------------------------------------

    def _register_commands(self) -> None:
        tree = self.tree

        @tree.command(name="ask", description="Ask the Engram bot a question privately")
        @app_commands.describe(question="Your question about Engram")
        async def ask(interaction: discord.Interaction, question: app_commands.Range[str, 1, 1500]):
            await interaction.response.defer(ephemeral=True, thinking=True)
            if self.over_budget():
                await interaction.followup.send(BUDGET_TEXT, ephemeral=True)
                return
            if self.over_rate(interaction.user.id):
                await interaction.followup.send(RATE_TEXT, ephemeral=True)
                return
            conversation = history.build_messages(
                [history.Item("user", question, interaction.user.display_name)]
            )
            result = await self.agent.respond(conversation, TurnContext(mode="ask"), self.kb.text)
            self._record(result, interaction.user.id, None)
            for part in chunk(result.text):
                await interaction.followup.send(part, ephemeral=True)

        @tree.command(name="bug", description="Report a bug in Engram")
        async def bug(interaction: discord.Interaction):
            await interaction.response.send_modal(BugModal(self, "bug"))

        @tree.command(name="feature", description="Request a feature for Engram")
        async def feature(interaction: discord.Interaction):
            await interaction.response.send_modal(BugModal(self, "feature"))

        @tree.command(name="issue", description="Show the status of an Engram GitHub issue")
        @app_commands.describe(number="Issue number")
        async def issue(interaction: discord.Interaction, number: app_commands.Range[int, 1]):
            await interaction.response.defer(ephemeral=True)
            try:
                found = await self.gh.get_issue(number)
            except GitHubError:
                await interaction.followup.send("GitHub isn't answering right now.", ephemeral=True)
                return
            if found is None:
                await interaction.followup.send(f"There's no issue #{number}.", ephemeral=True)
                return
            state = found.state + (f" ({found.state_reason})" if found.state_reason else "")
            embed = discord.Embed(title=f"#{found.number} {found.title}"[:256], url=found.url)
            embed.add_field(name="State", value=state)
            if found.labels:
                embed.add_field(name="Labels", value=", ".join(found.labels)[:1000])
            await interaction.followup.send(embed=embed, ephemeral=True)

        @tree.command(name="quiet", description="Stop or resume the bot's replies in this thread")
        async def quiet(interaction: discord.Interaction):
            channel = interaction.channel
            info = self.store.thread(channel.id) if channel else None
            if info is None:
                await interaction.response.send_message(
                    "I'm not following this channel.", ephemeral=True
                )
                return
            if interaction.user.id != info.reporter_id and not self.is_maintainer(interaction.user):
                await interaction.response.send_message(
                    "Only the thread's author can do that.", ephemeral=True
                )
                return
            self.store.set_muted(info.thread_id, not info.muted)
            msg = (
                "I'm back. Ask away."
                if info.muted
                else "Okay, I'll stay quiet here. Mention me if you need me."
            )
            await interaction.response.send_message(msg)

    async def start_intake_thread(
        self, interaction: discord.Interaction, kind: str, summary: str, details: str
    ) -> None:
        forum = self.get_channel(self.settings.help_forum_id or 0)
        if not isinstance(forum, discord.ForumChannel):
            await interaction.response.send_message(
                "The help forum isn't set up yet, so I can't start a report.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        user = interaction.user
        label = "Bug report" if kind == "bug" else "Feature request"
        wanted = {"bug": ("bug",), "feature": ("feature", "feature request", "idea")}[kind]
        tags = [t for t in forum.available_tags if t.name.lower() in wanted][:1]
        created = await forum.create_thread(
            name=summary[:100],
            content=f"**{label} from {user.mention}**\n**{summary}**\n\n{details}",
            applied_tags=tags,
            allowed_mentions=discord.AllowedMentions(users=[user]),
        )
        thread = created.thread
        self.store.add_thread(thread.id, user.id, "command", kind)
        info = self.store.thread(thread.id)
        assert info is not None
        await interaction.followup.send(
            f"Started {thread.mention}. Let's continue there.", ephemeral=True
        )
        await self.handle_turn(thread, info, user)
