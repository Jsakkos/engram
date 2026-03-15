# Testing

Engram has a comprehensive test suite spanning unit tests, integration tests, pipeline snapshot tests, and Playwright E2E tests.

## Quick Reference

```bash
# Backend
cd backend
uv run pytest                         # All tests
uv run pytest tests/unit/             # Unit tests only (~8s)
uv run pytest tests/integration/      # Integration tests (~80s)
uv run pytest tests/pipeline/         # Pipeline tests (~0.4s)
uv run pytest -k test_name            # Specific test
uv run pytest --cov=app               # With coverage

# Frontend E2E
cd frontend
npm run test:e2e                      # Headless
npm run test:e2e:ui                   # Interactive UI
```

## Test Categories

### Unit Tests (`tests/unit/`)

Test individual modules in isolation with mocked dependencies. Fast execution (< 1 second per test).

Covers: Analyst, Extractor, Curator, StateMachine, EventBroadcaster, ConfigService, TMDB classifier, DiscDB classifier, Organizer, validation endpoints.

### Integration Tests (`tests/integration/`)

Test complete workflows from disc insertion through completion. Use simulation endpoints to avoid physical disc requirements. Validate WebSocket message broadcasting end-to-end.

Covers: full workflow, simulation API, WebSocket contracts, error recovery, movie edition workflow, subtitle workflow.

!!! note
    Integration tests have caught 2 production bugs (WebSocket parameter mismatches).

### Pipeline Tests (`tests/pipeline/`)

Snapshot-based tests for classification, organization, and flow scenarios.

Covers: content classification, play-all detection, generic label flow, concurrent jobs, ambiguous movie flow, organization paths, TV episode pipeline.

### Real Data Tests (`tests/real_data/`)

Tests requiring actual MKV files. Auto-skipped if test files don't exist.

### E2E Tests (`frontend/e2e/`)

Full UI workflow testing using Playwright (10 spec files). Requires backend running with `DEBUG=true`.

| Spec File | Tests | Description |
|-----------|-------|-------------|
| `basic-ui-verification` | 11 | Header, filters, empty state, styling |
| `disc-flow` | 6 | TV and movie disc lifecycle |
| `error-recovery` | 4 | Failed jobs, WebSocket reconnect, cancel |
| `movie-track-progress` | 8 | Multi-track movie progress |
| `progress-display` | 9 | Ripping progress, speed/ETA, matching |
| `realistic-disc-flow` | 5 | Generic labels, multi-disc, extras |
| `review-flow` | 5 | Review queue interactions |
| `screenshot-workflow` | 2 | Captures every major UI state |
| `visual-verification` | 14 | Visual regression checks |
| `real-data-simulation` | 1 | Real MKV file testing |

## Test Fixtures

### Backend

```python
@pytest.fixture
async def client():
    """AsyncClient with ASGITransport for FastAPI testing."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

@pytest.fixture(autouse=True)
async def setup_db():
    """Clean database between tests."""
    await init_db()
    async with async_session() as session:
        await session.execute(text("DELETE FROM disc_titles"))
        await session.execute(text("DELETE FROM disc_jobs"))
        await session.commit()
```

### Frontend E2E

Tests use simulation API helpers defined in `e2e/fixtures/api-helpers.ts`:

- `simulateInsertDisc()` — trigger disc insertion
- `resetAllJobs()` — clean slate between tests
- `advanceJob()` — manually progress job state

Disc scenarios are defined in `e2e/fixtures/disc-scenarios.ts` with pre-configured TV and movie disc parameters.

## CI

Tests run automatically on every PR and push to `main` via GitHub Actions:

- **Backend Lint**: `ruff check` + `ruff format --check`
- **Backend Tests**: Full pytest suite
- **Frontend Lint & Build**: TypeScript check + Vite build
- **E2E Tests**: Playwright with Chromium (cached for speed)
