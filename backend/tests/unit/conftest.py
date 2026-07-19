"""Shared fixtures for unit tests.

Patches async_session and the cached sync engine so no unit test touches
the real engram.db.
"""

import importlib
import importlib.util
import sys
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, create_engine


def _load_script_module(name: str) -> object:
    """Load a backend/scripts/<name>.py file as an importable module.

    Lives here so multiple test files share a single load (and thus a single
    exec_module run) per session — the validator and build-script modules
    contain top-level imports and a sys.path.insert that would otherwise run
    once per loader call site.
    """
    backend_root = Path(__file__).parent.parent.parent
    script_path = backend_root / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, script_path)
    # spec_from_file_location can return None for unresolvable paths and
    # ModuleSpec.loader is typed Optional — guard both so a missing or
    # renamed script surfaces as a clean ImportError rather than an
    # AttributeError on the next line.
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load script module {name!r}: no loader for {script_path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    # If exec_module raises (missing dep, import error in the script), pop the
    # half-initialised stub so a later test sees a fresh ImportError rather
    # than a zombie module. Matches the pattern in importlib's own internals.
    try:
        spec.loader.exec_module(mod)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return mod


@pytest.fixture(scope="session")
def vsc():
    """The validate_subtitle_cache.py module, loaded once per pytest session."""
    return _load_script_module("validate_subtitle_cache")


@pytest.fixture(scope="session")
def bsc():
    """The build_subtitle_cache.py module, loaded once per pytest session."""
    return _load_script_module("build_subtitle_cache")


@pytest.fixture(scope="session")
def ecl():
    """The extract_changelog.py module, loaded once per pytest session."""
    return _load_script_module("extract_changelog")


@pytest.fixture(scope="session")
def contrib():
    """The contributors.py module, loaded once per pytest session."""
    return _load_script_module("contributors")


@pytest.fixture(scope="session")
def nsc():
    """The normalize_subtitle_cache.py module, loaded once per pytest session."""
    return _load_script_module("normalize_subtitle_cache")


@pytest.fixture(scope="session")
def msc():
    """The migrate_subtitle_cache_keys.py module, loaded once per pytest session."""
    return _load_script_module("migrate_subtitle_cache_keys")


@pytest.fixture(scope="session")
def psc(bsc):
    """The pack_subtitle_cache.py module, loaded once per pytest session.

    Depends on ``bsc`` so ``build_subtitle_cache`` is already in ``sys.modules``
    when pack's module-level ``from build_subtitle_cache import ...`` runs —
    importlib spec-loading doesn't put ``scripts/`` on ``sys.path`` the way
    running the script directly does.
    """
    return _load_script_module("pack_subtitle_cache")


_unit_engine = create_async_engine(
    "sqlite+aiosqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)

_unit_session_factory = sessionmaker(_unit_engine, class_=AsyncSession, expire_on_commit=False)

# Separate sync engine for get_config_sync() callers (organizer, analyst, etc.).
# StaticPool keeps the in-memory database alive across connections within the
# same process.
_unit_sync_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)


@pytest.fixture(autouse=True)
async def isolate_database(monkeypatch):
    """Patch async_session + sync engine so no unit test touches engram.db."""
    async with _unit_engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    SQLModel.metadata.create_all(_unit_sync_engine)

    # Patch via direct module references to avoid name-shadowing in __init__.py
    import app.database as _db_mod

    _config_mod = importlib.import_module("app.services.config_service")
    _jm_mod = importlib.import_module("app.services.job_manager")
    # cleanup_service and finalization_coordinator open DB sessions inside their
    # terminal callbacks — patch so they hit the in-memory engine, not engram.db.
    # matching_coordinator.clear_job_caches is in-memory only; patched defensively
    # in case it gains DB access in the future.
    _cleanup_mod = importlib.import_module("app.services.cleanup_service")
    _final_mod = importlib.import_module("app.services.finalization_coordinator")
    _match_mod = importlib.import_module("app.services.matching_coordinator")
    # transcription_prewarm spawns background tasks off REVIEW_NEEDED transitions
    # (via job_manager's module-level state machine) — patch it too so a prewarm
    # task kicked off by an unrelated unit test never touches engram.db.
    _prewarm_mod = importlib.import_module("app.services.transcription_prewarm")

    monkeypatch.setattr(_db_mod, "async_session", _unit_session_factory)
    monkeypatch.setattr(_config_mod, "async_session", _unit_session_factory)
    monkeypatch.setattr(_jm_mod, "async_session", _unit_session_factory)
    monkeypatch.setattr(_cleanup_mod, "async_session", _unit_session_factory)
    monkeypatch.setattr(_final_mod, "async_session", _unit_session_factory)
    monkeypatch.setattr(_match_mod, "async_session", _unit_session_factory)
    monkeypatch.setattr(_prewarm_mod, "async_session", _unit_session_factory)

    # Redirect the cached sync engine in config_service so get_config_sync()
    # uses the in-memory test database instead of connecting to engram.db.
    monkeypatch.setattr(_config_mod, "_sync_engine", _unit_sync_engine)
    monkeypatch.setattr(_config_mod, "_get_sync_engine", lambda: _unit_sync_engine)

    # Neutralize MakeMKV settings.conf writes so tests that save a makemkv_key
    # never touch the developer's real ~/.MakeMKV/settings.conf. config_service
    # imports this lazily, so patching the source attribute is enough. Tests in
    # test_makemkv_registration.py bind the real function at import and pass
    # explicit tmp paths, so they're unaffected.
    _reg_mod = importlib.import_module("app.core.makemkv_registration")
    monkeypatch.setattr(_reg_mod, "write_makemkv_settings", lambda *a, **k: False)

    # Stub the production prewarmer's kickoff so that unit tests which
    # transition jobs into REVIEW_NEEDED (via the real module-level
    # JobStateMachine) never spawn real background tasks.  Real tasks build an
    # EpisodeMatcher in a thread, call ffprobe on fake files, and hold DB
    # sessions on the StaticPool — when the autouse isolate_database fixture
    # later calls drop_all the task is still alive → OperationalError: database
    # table is locked.  Tests that want to assert on kickoff calls replace
    # _prewarmer entirely with their own MagicMock (via monkeypatch.setattr on
    # jm.job_manager._prewarmer), which takes precedence over this stub; see
    # TestJobManagerWiring.
    _jm_inst = _jm_mod.job_manager
    monkeypatch.setattr(_jm_inst._prewarmer, "kickoff", lambda job_id: None)  # no-op stub

    # Clear the in-flight match-dispatch guard on the singleton: a prior test
    # whose event loop closed before a match task's done callback ran can leak
    # a title id here, and title ids restart at 1 with each fresh in-memory DB
    # — a stale entry would silently skip an unrelated test's dispatch.
    _jm_inst._inflight_match_dispatch.clear()

    # Same hazard for the extractor's per-job skip-set: skip_rip_title registers a
    # job_id -> {title_index} entry that is only cleared at rip end / shutdown
    # (neither runs in unit tests). With job ids restarting at 1 per in-memory DB,
    # a stale entry would make an unrelated test's per-title rip silently skip a
    # title. Clear it so each test starts with an empty skip-set.
    _jm_inst._extractor._skipped_indices.clear()

    # Same hazard again for the cancellation-intent map (#506): reconcile_and_advance
    # records a job id there before cancelling its rip task, and the entry is normally
    # consumed by that task's CancelledError handler. A test whose loop closed first
    # can strand one, and job ids restart at 1 with each fresh in-memory DB — a stale
    # entry would make an unrelated test's genuine user cancel look watchdog-initiated.
    _jm_inst._cancel_reasons.clear()

    yield

    async with _unit_engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.drop_all)
    SQLModel.metadata.drop_all(_unit_sync_engine)
