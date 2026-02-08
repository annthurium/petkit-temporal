# PetKit Temporal

A web app for controlling PetKit automatic pet feeders. Provides a dashboard to monitor feeder status, trigger manual feedings, view feeding history, and set up custom feeding schedules — all through a local web UI backed by the PetKit cloud API.

Note: due to PetKit's janky authentication supporting only once device at a time, using this app will log you out of PetKit's mobile app.

## Stack

- **Backend:** Python / FastAPI, using [pypetkitapi](https://github.com/Jezza34000/py-petkit-api) for PetKit cloud communication
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

### Configuration

Copy `.env.example` or create a `.env` file in the project root:

```
PETKIT_USERNAME="your-petkit-email"
PETKIT_PASSWORD="your-petkit-password"
PETKIT_REGION="US"
PETKIT_TIMEZONE="America/Los_Angeles"
```

### Install dependencies

```bash
# Backend
uv sync

# Frontend
cd frontend && npm install
```

## Running

Start the backend and frontend in separate terminals:

```bash
# Backend (from project root)
uv run uvicorn backend.main:app --reload

# Frontend (from frontend/)
cd frontend && npm run dev
```

The frontend runs at `http://localhost:5173` and proxies API requests to the backend at `http://localhost:8000`.

## Tests

```bash
uv run pytest tests/ -v
```

## Project structure

```
backend/
  main.py            # FastAPI app setup and lifespan
  client.py          # PetKit API client singleton
  config.py          # Environment variable config
  scheduler.py       # Scheduled feeding loop
  routers/
    feeders.py       # All /api/feeders endpoints
frontend/
  src/
    App.tsx          # Root component
    api.ts           # Axios API client
    components/      # React components (dashboard, feed controls, etc.)
tests/
  test_scheduler.py  # Scheduler unit tests
  test_router.py     # Router/endpoint integration tests
```
