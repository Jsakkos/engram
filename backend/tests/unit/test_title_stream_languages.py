"""Scan-time stream languages persist on DiscTitle (issue #241)."""

import json

import pytest
from sqlalchemy import text

from app.core.analyst import TitleInfo
from app.models import ContentType
from app.models.disc_job import DiscTitle
from tests.unit.test_identify_rip_first_gates import (
    _bare_coord,
    _job_titles,
    _make_analysis,
    _seed_identifying_job,
    gate_env,  # noqa: F401 (pytest fixture)
)


@pytest.mark.unit
class TestIdentificationStoresStreamLanguages:
    async def test_scan_languages_saved_as_json(self, gate_env):  # noqa: F811
        titles = [
            TitleInfo(
                index=0,
                duration_seconds=1500,
                size_bytes=2_000_000_000,
                chapter_count=7,
                audio_languages=["ja", None],
                subtitle_languages=["en"],
            ),
            TitleInfo(index=1, duration_seconds=1500, size_bytes=2_000_000_000, chapter_count=7),
        ]
        analysis = _make_analysis(ContentType.TV, "Some Show", season=1, tmdb_id=42)
        job_id = await _seed_identifying_job("SOME_SHOW_S1D1")
        coord = _bare_coord(analysis, titles, "SOME_SHOW_S1D1")

        await coord.identify_disc(job_id)

        rows = {t.title_index: t for t in await _job_titles(job_id)}
        assert json.loads(rows[0].audio_languages) == ["ja", None]
        assert json.loads(rows[0].subtitle_languages) == ["en"]
        # No SINFO for the title: unknown, not an empty list.
        assert rows[1].audio_languages is None
        assert rows[1].subtitle_languages is None


@pytest.mark.unit
class TestAudioLanguageList:
    def test_parses_json(self):
        t = DiscTitle(job_id=1, title_index=0, duration_seconds=1, audio_languages='["ja", null]')
        assert t.audio_language_list() == ["ja", None]

    @pytest.mark.parametrize("raw", [None, "", "not json", '{"a": 1}', "[1, 2]"])
    def test_unknown_or_malformed_is_none(self, raw):
        t = DiscTitle(job_id=1, title_index=0, duration_seconds=1, audio_languages=raw)
        assert t.audio_language_list() is None


@pytest.mark.unit
async def test_reconciler_adds_language_columns_to_old_disc_titles(tmp_path):
    """Frozen builds skip Alembic: _add_missing_columns must add both columns."""
    from sqlalchemy.ext.asyncio import create_async_engine

    import app.database as db_mod

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'old.db'}")
    original_engine = db_mod.engine
    db_mod.engine = engine
    try:
        async with engine.begin() as conn:
            await conn.run_sync(lambda c: DiscTitle.__table__.create(c, checkfirst=True))
            await conn.execute(text("ALTER TABLE disc_titles DROP COLUMN audio_languages"))
            await conn.execute(text("ALTER TABLE disc_titles DROP COLUMN subtitle_languages"))

        await db_mod._add_missing_columns()

        async with engine.connect() as conn:
            actual = await db_mod._get_actual_columns(conn, "disc_titles")
        assert {"audio_languages", "subtitle_languages"} <= actual
    finally:
        db_mod.engine = original_engine
        await engine.dispose()
