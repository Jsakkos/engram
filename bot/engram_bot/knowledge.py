"""The docs the bot answers from, assembled into one cacheable system-prompt block.

The user-facing docs are ~50K tokens, small enough to send whole on every request: with
prompt caching that is cheaper and more reliable than retrieval, and it stays under Claude
Haiku 5.5's 100K-token rate-card threshold. The block only changes when ``main`` does,
so it stays cached between refreshes.
"""

from __future__ import annotations

import asyncio
import fnmatch
import logging
import re
from dataclasses import dataclass
from pathlib import Path

from .github import GitHub

log = logging.getLogger(__name__)

CHANGELOG = "CHANGELOG.md"
# ~3.5 chars/token: past this the prompt nears the 100K-token rate-card threshold.
WARN_CHARS = 320_000


@dataclass(frozen=True)
class Snapshot:
    key: str  # commit sha (GitHub) or mtime fingerprint (local)
    files: dict[str, str]
    latest_release: dict | None


def select(paths: list[str], include: tuple[str, ...]) -> list[str]:
    return sorted(p for p in paths if any(fnmatch.fnmatch(p, pat) for pat in include))


def changelog_excerpt(text: str, sections: int) -> str:
    """The newest ``sections`` released versions; ``[Unreleased]`` is left out on purpose
    so the bot never tells a user a feature exists before it ships."""
    heads = [m for m in re.finditer(r"^## \[(.+?)\].*$", text, re.M)]
    released = [m for m in heads if m.group(1).lower() != "unreleased"][:sections]
    if not released:
        return ""
    chunks = []
    for m in released:
        later = [h.start() for h in heads if h.start() > m.start()]
        chunks.append(text[m.start() : later[0] if later else len(text)].strip())
    return "\n\n".join(chunks)


def doc_url(path: str, site_url: str, repo: str) -> str:
    if path.startswith("docs/") and path.endswith(".md"):
        page = path[len("docs/") : -len(".md")]
        if page == "index":
            return site_url
        if page.endswith("/index"):
            page = page[: -len("index")]
        return f"{site_url.rstrip('/')}/{page.rstrip('/')}/"
    return f"https://github.com/{repo}/blob/main/{path}"


def render(snapshot: Snapshot, site_url: str, repo: str, changelog_sections: int) -> str:
    parts = ["# Engram knowledge base", ""]
    rel = snapshot.latest_release
    if rel:
        published = (rel.get("published_at") or "")[:10]
        parts.append(f"Latest release: {rel['tag']} ({published}) — {rel['url']}")
        parts.append("")
    for path, text in sorted(snapshot.files.items()):
        if path == CHANGELOG:
            continue
        url = doc_url(path, site_url, repo)
        parts.append(f'<doc path="{path}" url="{url}">\n{text.strip()}\n</doc>')
    if CHANGELOG in snapshot.files:
        excerpt = changelog_excerpt(snapshot.files[CHANGELOG], changelog_sections)
        if excerpt:
            url = f"https://github.com/{repo}/blob/main/CHANGELOG.md"
            parts.append(f'<doc path="CHANGELOG.md" url="{url}">\n{excerpt}\n</doc>')
    return "\n\n".join(parts)


class KnowledgeBase:
    def __init__(
        self,
        github: GitHub,
        *,
        include: tuple[str, ...],
        site_url: str,
        changelog_sections: int,
        docs_dir: Path | None = None,
        branch: str = "main",
    ) -> None:
        self._gh = github
        self._include = (*include, CHANGELOG)
        self._site_url = site_url
        self._changelog_sections = changelog_sections
        self._docs_dir = docs_dir
        self._branch = branch
        self._key: str | None = None
        self.text = ""
        self.latest_release: dict | None = None

    async def refresh(self) -> bool:
        """Re-read the docs. Returns True when the text changed. Keeps the old text on error."""
        try:
            release = await self._gh.latest_release()
        except Exception:
            log.warning("could not read the latest release", exc_info=True)
            release = self.latest_release
        try:
            snapshot = await (self._local(release) if self._docs_dir else self._remote(release))
        except Exception:
            log.warning("knowledge refresh failed; keeping the previous docs", exc_info=True)
            return False
        if snapshot is None:
            return False
        self._key = snapshot.key
        self.latest_release = release
        self.text = render(snapshot, self._site_url, self._gh.repo, self._changelog_sections)
        if len(self.text) > WARN_CHARS:
            log.warning(
                "knowledge is %d chars, likely over Haiku's 100K-token rate-card threshold; "
                "narrow BOT_DOCS_INCLUDE",
                len(self.text),
            )
        log.info(
            "knowledge loaded: %d files, %d chars, release %s",
            len(snapshot.files),
            len(self.text),
            release and release["tag"],
        )
        return True

    async def _remote(self, release: dict | None) -> Snapshot | None:
        commit = await self._gh.head_commit(self._branch)
        key = f"{commit}:{release and release['tag']}"
        if key == self._key:
            return None
        paths = select(await self._gh.tree_paths(commit), self._include)
        texts = await asyncio.gather(*(self._gh.file_text(p, commit) for p in paths))
        return Snapshot(key=key, files=dict(zip(paths, texts, strict=True)), latest_release=release)

    async def _local(self, release: dict | None) -> Snapshot | None:
        root = self._docs_dir
        assert root is not None
        # Glob per pattern rather than walking the tree (node_modules, .venv).
        found = {p.relative_to(root).as_posix() for pat in self._include for p in root.glob(pat)}
        paths = select(sorted(found), self._include)
        files = {p: (root / p).read_text(encoding="utf-8") for p in paths}
        stamp = max(((root / p).stat().st_mtime for p in paths), default=0)
        key = f"local:{stamp}:{release and release['tag']}"
        if key == self._key:
            return None
        return Snapshot(key=key, files=files, latest_release=release)
