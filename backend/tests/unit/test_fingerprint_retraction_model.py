import pytest
from sqlmodel import select

from app.database import async_session, init_db
from app.models.fingerprint import FingerprintRetraction


@pytest.fixture(autouse=True)
async def _db():
    await init_db()


async def test_retraction_row_roundtrips():
    async with async_session() as session:
        row = FingerprintRetraction(
            pseudonym="00000000-0000-4000-8000-000000000000",
            tmdb_id=1396,
            season=3,
            episode=10,
            fingerprint_sha256=b"\x07" * 32,
        )
        session.add(row)
        await session.commit()

        fetched = (await session.execute(select(FingerprintRetraction))).scalars().all()
        assert len(fetched) == 1
        assert fetched[0].upload_status is None
        assert fetched[0].fingerprint_sha256 == b"\x07" * 32
        await session.delete(fetched[0])
        await session.commit()
