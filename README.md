# PetKit Temporal

A web app for controlling PetKit automatic pet feeders. Provides a dashboard to monitor feeder status, trigger manual feedings, view feeding history, and set up custom feeding schedules — all through a local web UI backed by the PetKit cloud API.

Note: due to PetKit's janky authentication supporting only once device at a time, using this app will log you out of PetKit's mobile app.

## Stack

- **Backend:** Python / FastAPI, using [pypetkitapi](https://github.com/Jezza34000/py-petkit-api) for PetKit cloud communication. Temporal for managing application state and async workflows.
- **Frontend:** React + TypeScript + Vite, styled with Tailwind CSS

## Setup

- Configure a PetKit automated feeder as per instructions that come with the device. This app has been developed using the D4 Fresh Element Solo.

- Create a Python virtual environment:

```bash
python3 -m venv .venv
```

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
TEMPORAL_HOST="localhost:7233"
TEMPORAL_NAMESPACE="default"
TEMPORAL_TASK_QUEUE="petkit-tasks"
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

The Temporal web UI is available at `http://localhost:8233` for inspecting workflows.

The frontend runs at `http://localhost:5173` and proxies API requests to the backend at `http://localhost:8000`.

## Tests

```bash
uv run pytest tests/ -v
```

## Project structure

```
backend/
  main.py            # FastAPI app setup, lifespan, Temporal worker startup
  client.py          # PetKit API client singleton
  config.py          # Environment variable config
  routers/
    feeders.py       # All /api/feeders endpoints
  temporal/
    client.py        # Temporal client singleton
    worker.py        # Standalone worker entrypoint
    workflows/
      feeder_workflows.py  # DailyScheduledFeedingWorkflow
    activities/
      feeder_activities.py # manual_feed, get_feeder_status
frontend/
  src/
    App.tsx          # Root component
    api.ts           # Axios API client
    components/      # React components (dashboard, feed controls, etc.)
tests/
  test_workflows.py  # Activity and workflow unit tests
  test_router.py     # Router/endpoint integration tests
  test_retry.py      # Retry logic tests
```
