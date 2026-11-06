from engram_bot import knowledge
from engram_bot.knowledge import KnowledgeBase

CHANGELOG = """# Changelog

## [Unreleased]
- secret future thing

## [0.39.0] - 2026-10-09
_Highlights: TVDB._

## [0.38.1] - 2026-10-03
- fix

## [0.38.0] - 2026-10-01
- old
"""


def test_changelog_excerpt_skips_unreleased():
    out = knowledge.changelog_excerpt(CHANGELOG, 2)
    assert "secret future thing" not in out
    assert "[0.39.0]" in out and "[0.38.1]" in out and "[0.38.0]" not in out


def test_doc_urls():
    site = "https://jsakkos.github.io/engram/"
    assert knowledge.doc_url("docs/index.md", site, "o/r") == site
    assert knowledge.doc_url("docs/guide/history.md", site, "o/r") == site + "guide/history/"
    assert (
        knowledge.doc_url("README.md", site, "o/r") == "https://github.com/o/r/blob/main/README.md"
    )


def test_select():
    paths = ["docs/guide/a.md", "docs/development/b.md", "README.md", "backend/x.py"]
    assert knowledge.select(paths, ("README.md", "docs/guide/*.md")) == [
        "README.md",
        "docs/guide/a.md",
    ]


class FakeGitHub:
    repo = "o/r"

    async def latest_release(self):
        return {"tag": "v0.39.0", "published_at": "2026-10-09T00:00:00Z", "url": "https://rel"}


async def test_local_refresh(tmp_path):
    (tmp_path / "docs" / "guide").mkdir(parents=True)
    (tmp_path / "docs" / "development").mkdir()
    (tmp_path / "docs" / "guide" / "review.md").write_text("# Review queue\nUse it.")
    (tmp_path / "docs" / "development" / "dev.md").write_text("# Internal")
    (tmp_path / "CHANGELOG.md").write_text(CHANGELOG)
    kb = KnowledgeBase(
        FakeGitHub(),
        include=("docs/guide/*.md",),
        site_url="https://site/",
        changelog_sections=1,
        docs_dir=tmp_path,
    )
    assert await kb.refresh()
    assert "Latest release: v0.39.0 (2026-10-09)" in kb.text
    assert 'url="https://site/guide/review/"' in kb.text
    assert "# Internal" not in kb.text
    assert "[0.39.0]" in kb.text and "[0.38.1]" not in kb.text
    assert not await kb.refresh()  # unchanged: the cached prompt stays byte-identical


async def test_refresh_failure_keeps_previous_text(tmp_path):
    class Broken(FakeGitHub):
        async def head_commit(self, branch):
            raise RuntimeError("network down")

    kb = KnowledgeBase(Broken(), include=(), site_url="s", changelog_sections=1)
    kb.text = "previous"
    assert not await kb.refresh()
    assert kb.text == "previous"
