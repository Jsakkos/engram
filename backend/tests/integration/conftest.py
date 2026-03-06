"""Shared fixtures and configuration for integration tests."""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

from app.config import settings
from app.database import get_session
from app.main import app
from app.models import AppConfig


@pytest.fixture(autouse=True, scope="session")
def enable_debug_mode():
    """Enable debug mode for all integration tests.

    Simulation endpoints require DEBUG=true. Rather than relying on a .env file
    (which varies between dev machines and worktrees), integration tests should
    be self-contained and explicitly enable debug mode.
    """
    original = settings.debug
    settings.debug = True
    yield
    settings.debug = original

# Test database URL for integration tests
INTEGRATION_DB_URL = "sqlite+aiosqlite:///:memory:"


@pytest.fixture(scope="session")
def async_engine():
    """Create async engine for integration tests."""
    engine = create_async_engine(
        INTEGRATION_DB_URL,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    return engine


@pytest.fixture(scope="function")
async def async_session_maker(async_engine):
    """Create async session maker."""
    async with async_engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)

    AsyncSessionLocal = sessionmaker(async_engine, class_=AsyncSession, expire_on_commit=False)

    yield AsyncSessionLocal

    async with async_engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.drop_all)


@pytest.fixture
async def async_session(async_session_maker):
    """Provide async database session."""
    async with async_session_maker() as session:
        yield session


@pytest.fixture
async def integration_client(async_session):
    """Provide async HTTP client for integration tests."""

    async def override_get_session():
        yield async_session

    app.dependency_overrides[get_session] = override_get_session

    async with AsyncClient(app=app, base_url="http://test") as client:
        yield client

    app.dependency_overrides.clear()


@pytest.fixture
async def integration_config(async_session):
    """Create test configuration for integration tests."""
    config = AppConfig(
        makemkv_path="/usr/bin/makemkvcon",
        makemkv_key="T-integration-test-key",
        staging_path="/tmp/integration-staging",
        library_movies_path="/tmp/integration-movies",
        library_tv_path="/tmp/integration-tv",
        transcoding_enabled=False,
        tmdb_api_key="eyJhbGciOiJIUzI1NiJ9.integration_test_token",
        max_concurrent_matches=2,
        ffmpeg_path="/usr/bin/ffmpeg",
        conflict_resolution_default="rename",
        # Fast polling for tests
        ripping_file_poll_interval=0.5,
        ripping_stability_checks=2,
        ripping_file_ready_timeout=60.0,
        sentinel_poll_interval=0.5,
    )
    async_session.add(config)
    await async_session.commit()
    await async_session.refresh(config)
    return config
