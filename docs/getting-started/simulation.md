# Simulation

Engram includes a full simulation mode for development and testing without a physical optical drive. This is essential for E2E testing and for running on Linux/macOS where automatic drive detection is not available.

## Enabling Simulation Mode

Set the `DEBUG` environment variable to `true` before starting the backend:

=== "Environment variable"

    ```bash
    DEBUG=true uv run uvicorn app.main:app --reload
    ```

=== ".env file"

    Create or edit `backend/.env`:

    ```ini
    DEBUG=true
    ```

    Then start normally:

    ```bash
    uv run uvicorn app.main:app --reload
    ```

!!! warning
    Simulation endpoints are **only available** when `DEBUG=true`. In production mode, these endpoints return 404.

## Simulation Endpoints

All simulation endpoints are under `/api/simulate/`.

### Insert a TV Disc

Simulates inserting a disc with TV show content. With `simulate_ripping: true`, the job will automatically progress through ripping with fake progress updates.

```bash
curl -X POST localhost:8000/api/simulate/insert-disc \
  -H "Content-Type: application/json" \
  -d '{"volume_label":"ARRESTED_DEVELOPMENT_S1D1","content_type":"tv","simulate_ripping":true}'
```

### Insert a Movie Disc

```bash
curl -X POST localhost:8000/api/simulate/insert-disc \
  -H "Content-Type: application/json" \
  -d '{"volume_label":"INCEPTION_2010","content_type":"movie","simulate_ripping":true}'
```

### Remove a Disc

Simulates ejecting a disc from a drive. The `drive_id` parameter is URL-encoded (e.g., `E:` becomes `E%3A`).

```bash
curl -X POST "localhost:8000/api/simulate/remove-disc?drive_id=E%3A"
```

### Advance a Job

Manually pushes a job to its next state in the state machine. Useful for stepping through the workflow one stage at a time.

```bash
curl -X POST localhost:8000/api/simulate/advance-job/1
```

The state machine progression is:

```
IDLE -> IDENTIFYING -> RIPPING -> MATCHING -> ORGANIZING -> COMPLETED
```

With branching to `REVIEW_NEEDED` or `FAILED` at certain stages.

### Reset All Jobs

Deletes all jobs and titles from the database. Useful for cleaning up between test runs.

```bash
curl -X DELETE localhost:8000/api/simulate/reset-all-jobs
```

### Insert Disc from Staging

Creates a job from files already present in the staging directory, rather than simulating a physical disc. Useful when you have pre-ripped MKV files you want to process.

```bash
curl -X POST localhost:8000/api/simulate/insert-disc-from-staging
```

## Typical Development Workflow

1. Start the backend with `DEBUG=true` and the frontend dev server.
2. Open the dashboard at [http://localhost:5173](http://localhost:5173).
3. Insert a simulated disc:

    ```bash
    curl -X POST localhost:8000/api/simulate/insert-disc \
      -H "Content-Type: application/json" \
      -d '{"volume_label":"BREAKING_BAD_S1D1","content_type":"tv","simulate_ripping":true}'
    ```

4. Watch the dashboard update in real-time via WebSocket as the job progresses through each state.
5. If the job enters `REVIEW_NEEDED`, use the Review Queue in the dashboard to resolve it.
6. To test specific states, use `advance-job` to step through manually:

    ```bash
    # Create a job without auto-ripping
    curl -X POST localhost:8000/api/simulate/insert-disc \
      -H "Content-Type: application/json" \
      -d '{"volume_label":"THE_OFFICE_S3D2","content_type":"tv","simulate_ripping":false}'

    # Step through states one at a time
    curl -X POST localhost:8000/api/simulate/advance-job/1
    curl -X POST localhost:8000/api/simulate/advance-job/1
    ```

7. Clean up when done:

    ```bash
    curl -X DELETE localhost:8000/api/simulate/reset-all-jobs
    ```

## E2E Tests

The Playwright E2E test suite uses these simulation endpoints to test the full UI workflow. To run the tests:

```bash
cd frontend
npx playwright install   # first time only
npm run test:e2e         # headless mode
npm run test:e2e:ui      # interactive mode with browser UI
```

!!! note
    The backend must be running with `DEBUG=true` for E2E tests to work.
