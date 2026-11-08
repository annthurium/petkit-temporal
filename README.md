# PetKit

A web app for controlling PetKit automatic pet feeders. Provides a dashboard to monitor feeder status, trigger manual feedings, view feeding history, and set up custom feeding schedules — all through a local web UI backed by the PetKit cloud API.

Note: due to PetKit's janky authentication supporting only once device at a time, using this app will log you out of PetKit's mobile app.

## Stack

- **Backend:** Python / FastAPI, using [pypetkitapi](https://github.com/Jezza34000/py-petkit-api) for PetKit cloud communication.
- [Temporal](https://docs.temporal.io/develop/python/) for state management and retries
- **Frontend:** React + TypeScript + Vite, styled with Tailwind CSS

## Setup

- Configure a PetKit automated feeder as per instructions that come with the device. This app has been developed using the D4 Fresh Element Solo.

### Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) (Python package manager)
- Node.js 18+
- [Temporal](https://docs.temporal.io/cli#install) (workflow engine)

### Configuration

Copy `.env.example` or create a `.env` file in the project root:

```
PETKIT_USERNAME="your-petkit-email"
PETKIT_PASSWORD="your-petkit-password"
PETKIT_REGION="US"
PETKIT_TIMEZONE="America/Los_Angeles"
TEMPORAL_ADDRESS="localhost:7233"
TEMPORAL_NAMESPACE="default"
TEMPORAL_TASK_QUEUE="petkit-tasks"
# Optional: skip a scheduled feed if the feeder dispensed food within this many hours
FEED_SKIP_WINDOW_HOURS="12"
```

### Google Cloud / Firestore

The app stores device snapshots in Firestore. One-time setup:

```bash
gcloud auth login
gcloud config set project YOUR_PROJECT_ID
gcloud auth application-default login   # credentials for the Python client
gcloud services enable firestore.googleapis.com
gcloud firestore databases create --database=petkit --location=us-west1
```

Then set `GOOGLE_CLOUD_PROJECT` and `FIRESTORE_DATABASE="petkit"` in `.env`. The database lives in `us-west1`, the same region as Temporal Cloud on GCP and the Cloud Run services.

Firestore security rules live in `firestore.rules` and deny all client access; only the backend reads Firestore (see `docs/adr/0007`). To publish them:

```bash
npx firebase-tools deploy --only firestore:rules --project YOUR_PROJECT_ID
```

### Install dependencies

```bash
# Backend
uv sync

# Frontend
cd frontend && npm install
```

## Running

Start the Temporal server, backend, and frontend in separate terminals:

```bash
# Temporal dev server
temporal server start-dev

# Backend (from project root)
uv run uvicorn backend.main:app --reload

# Frontend (from frontend/)
cd frontend && npm run dev
```

The backend automatically starts a Temporal worker as a background task when the server boots. You can also run the worker standalone:

```bash
uv run python -m backend.temporal.worker
```

The Temporal web UI is available at `http://localhost:8233` for inspecting workflows and schedules.

## How feeding works

- **Daily schedules** are [Temporal Schedules](https://docs.temporal.io/schedule) (`feeding-schedule-{device_id}`). Temporal handles the timezone and DST.
- **Each feed**, scheduled or manual, is a short-lived `FeedWorkflow`:
  1. *(scheduled only)* skip if the feeder dispensed food in the last `FEED_SKIP_WINDOW_HOURS`
  2. send the feed command (never retried automatically, since a retry could dispense twice)
  3. poll the device's feed log until the feed shows up (Temporal activity retries do the polling)
  4. if it isn't confirmed, raise an alert; never send it again, because PetKit's feed log can miss real feeds (ADR 0009)
  5. the only re-send: if PetKit explicitly *rejected* the command (e.g. device offline), wait 30s and try once more
- **Manual feeds** use a fixed workflow ID per device, so a double-click can't start two feeds at once.
- **Device commands** (settings, desiccant reset, food replenished) run as a `DeviceCommandWorkflow` with normal retries. The API waits up to 25s for the result.
- **Device data** (status, settings, feed history) is read from Firestore snapshots, never from PetKit directly. The worker writes a snapshot after every feed and command, on Refresh, and every 30 minutes (`feeder-snapshot-refresh` schedule). Only Temporal activities talk to PetKit.

Design decisions are recorded in [docs/adr/](docs/adr/).

### Migrating from the old long-running workflow

Earlier versions used a long-running `DailyScheduledFeedingWorkflow` per feeder. To convert any that are still running into Schedules:

```bash
uv run python -m backend.temporal.migrate_to_schedules          # dry run
uv run python -m backend.temporal.migrate_to_schedules --apply
```

The frontend runs at `http://localhost:5173` and proxies API requests to the backend at `http://localhost:8000`.

## Deploying to Cloud Run

See [docs/deploy.md](docs/deploy.md). In short: one container image; the API runs as a Cloud Run service behind Identity-Aware Proxy (Google sign-in), and the Temporal worker as a Cloud Run worker pool that Temporal Cloud scales from zero (Serverless Workers).

## Tests

```bash
uv run pytest tests/ -v
```

## Project structure

```
backend/
  main.py            # FastAPI app setup, lifespan, Temporal worker startup
  client.py          # PetKit API client singleton (used only by activities)
  serializers.py     # PetKit objects -> snapshot dicts
  snapshots.py       # Firestore snapshot read/write
  config.py          # Environment variable config
  routers/
    feeders.py       # All /api/feeders endpoints
  temporal/
    client.py        # Temporal client singleton
    worker.py        # Worker factory + standalone worker entrypoint
    schedules.py     # Temporal Schedule CRUD + latest feed result lookup
    migrate_to_schedules.py  # One-time migration from the old long-running workflow
    workflows/
      feeder_workflows.py  # FeedWorkflow
    activities/
      feeder_activities.py # trigger_feed, verify_feed, check_recent_feed, alert_feed_failure
frontend/
  src/
    App.tsx          # Root component
    api.ts           # Axios API client
    components/      # React components (dashboard, feed controls, etc.)
tests/
  test_workflows.py  # Activity and workflow unit tests
  test_schedules.py  # Schedule helper tests
  test_router.py     # Router/endpoint integration tests
```
