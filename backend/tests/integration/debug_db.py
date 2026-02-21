import os
import sys

sys.path.insert(0, os.getcwd())
import asyncio

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlmodel import SQLModel

from app.models.disc_job import ContentType, DiscJob, DiscTitle, JobState, TitleState


async def main():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=True)
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)

    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with async_session() as session:
        print("Creating Job...")
        job = DiscJob(
            drive_id="TEST_DRIVE",
            volume_label="LORD_OF_THE_RINGS",
            content_type=ContentType.MOVIE,
            state=JobState.REVIEW_NEEDED,
            detected_title="The Lord of the Rings",
            # staging_path="/tmp/staging"
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)
        print(f"Job created: {job.id}")

        print("Creating Titles...")
        title1 = DiscTitle(
            job_id=job.id,
            title_index=1,
            duration_seconds=12000,
            file_size_bytes=50000000000,
            video_resolution="4K",
            output_filename="/tmp/staging/title_01.mkv",
            state=TitleState.COMPLETED,
        )
        session.add(title1)
        await session.commit()
        print("Title 1 created")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        print(f"\nERROR: {e}")
