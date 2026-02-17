"""Unit tests for API routes.

Tests the REST API endpoints including job management, configuration,
and validation. Verifies API key redaction in config endpoints.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

from app.database import get_session
from app.main import app
from app.models import AppConfig, DiscJob, DiscTitle
from app.models.disc_job import ContentType, JobState, TitleState


# Test database setup
SQLALCHEMY_DATABASE_URL = "sqlite:///:memory:"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@pytest.fixture(scope="function")
def test_db():
    """Create a fresh test database for each test."""
    SQLModel.metadata.create_all(bind=engine)
    yield
    SQLModel.metadata.drop_all(bind=engine)


@pytest.fixture
def db_session(test_db):
    """Provide a database session for tests."""
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client(db_session):
    """Provide a test client with overridden database dependency."""

    def override_get_session():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_session] = override_get_session
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def sample_config(db_session):
    """Create a sample configuration in the database."""
    config = AppConfig(
        makemkv_path="/usr/bin/makemkvcon",
        makemkv_key="T-test-key-1234567890",
        staging_path="/tmp/staging",
        library_movies_path="/media/movies",
        library_tv_path="/media/tv",
        transcoding_enabled=False,
        tmdb_api_key="eyJhbGciOiJIUzI1NiJ9.test_jwt_token",
        max_concurrent_matches=4,
        ffmpeg_path="/usr/bin/ffmpeg",
        conflict_resolution_default="rename",
    )
    db_session.add(config)
    db_session.commit()
    return config


@pytest.fixture
def sample_job(db_session):
    """Create a sample job in the database."""
    job = DiscJob(
        drive_id="D:",
        volume_label="TEST_DISC",
        content_type=ContentType.TV,
        state=JobState.IDLE,
        detected_title="Test Show",
        detected_season=1,
        staging_path="/tmp/staging/job_123",
    )
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    return job


@pytest.fixture
def sample_titles(db_session, sample_job):
    """Create sample titles for a job."""
    titles = []
    for i in range(3):
        title = DiscTitle(
            job_id=sample_job.id,
            title_index=i,
            duration_seconds=2400 + i * 60,
            file_size_bytes=1024 * 1024 * 1024,  # 1GB
            state=TitleState.PENDING,
        )
        db_session.add(title)
        titles.append(title)
    db_session.commit()
    return titles


class TestJobEndpoints:
    """Test job-related API endpoints."""

    def test_list_jobs_empty(self, client):
        """Test listing jobs when database is empty."""
        response = client.get("/api/jobs")
        assert response.status_code == 200
        assert response.json() == []

    def test_list_jobs_with_data(self, client, sample_job):
        """Test listing jobs returns correct data."""
        response = client.get("/api/jobs")
        assert response.status_code == 200
        jobs = response.json()
        assert len(jobs) == 1
        assert jobs[0]["id"] == sample_job.id
        assert jobs[0]["volume_label"] == "TEST_DISC"
        assert jobs[0]["state"] == "idle"

    def test_get_job_by_id(self, client, sample_job):
        """Test retrieving a specific job by ID."""
        response = client.get(f"/api/jobs/{sample_job.id}")
        assert response.status_code == 200
        job = response.json()
        assert job["id"] == sample_job.id
        assert job["detected_title"] == "Test Show"
        assert job["detected_season"] == 1

    def test_get_job_not_found(self, client):
        """Test retrieving non-existent job returns 404."""
        response = client.get("/api/jobs/999")
        assert response.status_code == 404

    def test_get_job_titles(self, client, sample_job, sample_titles):
        """Test retrieving titles for a job."""
        response = client.get(f"/api/jobs/{sample_job.id}/titles")
        assert response.status_code == 200
        titles = response.json()
        assert len(titles) == 3
        assert titles[0]["title_index"] == 0
        assert titles[0]["state"] == "pending"

    def test_start_job_not_found(self, client):
        """Test starting non-existent job returns 404."""
        response = client.post("/api/jobs/999/start")
        assert response.status_code == 404

    def test_cancel_job_not_found(self, client):
        """Test canceling non-existent job returns 404."""
        response = client.post("/api/jobs/999/cancel")
        assert response.status_code == 404


class TestConfigEndpoints:
    """Test configuration API endpoints."""

    def test_get_config_redacts_api_keys(self, client, sample_config):
        """Test that API keys are redacted in GET /api/config response."""
        response = client.get("/api/config")
        assert response.status_code == 200
        config = response.json()

        # Verify API keys are redacted
        assert config["makemkv_key"] == "***"
        assert config["tmdb_api_key"] == "***"

        # Verify other fields are not redacted
        assert config["makemkv_path"] == "/usr/bin/makemkvcon"
        assert config["staging_path"] == "/tmp/staging"
        assert config["library_movies_path"] == "/media/movies"
        assert config["transcoding_enabled"] is False

    def test_get_config_no_config_exists(self, client):
        """Test GET /api/config when no configuration exists."""
        response = client.get("/api/config")
        # Should return default configuration or 404
        assert response.status_code in [200, 404]

    def test_update_config(self, client, sample_config):
        """Test updating configuration via PUT /api/config."""
        update_data = {
            "staging_path": "/new/staging/path",
            "transcoding_enabled": True,
            "max_concurrent_matches": 8,
        }
        response = client.put("/api/config", json=update_data)
        assert response.status_code == 200

        # Verify changes were applied
        verify_response = client.get("/api/config")
        config = verify_response.json()
        assert config["staging_path"] == "/new/staging/path"
        assert config["transcoding_enabled"] is True
        assert config["max_concurrent_matches"] == 8

    def test_update_config_with_new_api_keys(self, client, sample_config):
        """Test updating API keys."""
        update_data = {
            "makemkv_key": "T-new-key-0987654321",
            "tmdb_api_key": "eyJhbGciOiJIUzI1NiJ9.new_token",
        }
        response = client.put("/api/config", json=update_data)
        assert response.status_code == 200

        # Note: Keys should be updated but still redacted in GET response
        verify_response = client.get("/api/config")
        config = verify_response.json()
        assert config["makemkv_key"] == "***"
        assert config["tmdb_api_key"] == "***"


class TestValidation:
    """Test API request validation."""

    def test_invalid_job_id_type(self, client):
        """Test that non-integer job IDs are rejected."""
        response = client.get("/api/jobs/invalid")
        assert response.status_code == 422  # Validation error

    def test_invalid_config_values(self, client, sample_config):
        """Test that invalid configuration values are rejected."""
        invalid_data = {
            "max_concurrent_matches": -1,  # Negative value should be invalid
        }
        response = client.put("/api/config", json=invalid_data)
        # Should reject or sanitize invalid values
        assert response.status_code in [400, 422]


class TestErrorHandling:
    """Test error handling in API endpoints."""

    def test_malformed_json(self, client):
        """Test that malformed JSON is handled gracefully."""
        response = client.put(
            "/api/config",
            data="{invalid json",
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 422

    def test_missing_required_fields(self, client):
        """Test that missing required fields are caught."""
        # This depends on what fields are actually required
        # Add specific tests based on your API schema
        pass


@pytest.mark.skipif(
    True, reason="Simulation endpoints only available in DEBUG mode"
)
class TestSimulationEndpoints:
    """Test simulation endpoints (DEBUG mode only)."""

    def test_simulate_insert_disc(self, client):
        """Test simulating disc insertion."""
        payload = {
            "volume_label": "TEST_TV_S1D1",
            "content_type": "tv",
            "simulate_ripping": True,
        }
        response = client.post("/api/simulate/insert-disc", json=payload)
        # Should create a new job
        assert response.status_code in [200, 201]

    def test_simulate_remove_disc(self, client):
        """Test simulating disc removal."""
        response = client.post("/api/simulate/remove-disc?drive_id=E:")
        assert response.status_code == 200
