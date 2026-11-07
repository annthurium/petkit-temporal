import os
from dotenv import load_dotenv

# ENV_FILE picks the settings file (default .env), e.g. ENV_FILE=.env.cloud.
# Real environment variables always win, which is how Cloud Run configures us.
load_dotenv(os.getenv("ENV_FILE", ".env"))


def _flag(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


PETKIT_USERNAME = os.getenv("PETKIT_USERNAME", "")
PETKIT_PASSWORD = os.getenv("PETKIT_PASSWORD", "")
PETKIT_REGION = os.getenv("PETKIT_REGION", "US")
PETKIT_TIMEZONE = os.getenv("PETKIT_TIMEZONE", "America/Los_Angeles")

# Temporal connection. Names match the Temporal SDK/CLI conventions.
# Defaults point at a local `temporal server start-dev`. TEMPORAL_HOST is the
# old name for TEMPORAL_ADDRESS and still works.
TEMPORAL_ADDRESS = os.getenv("TEMPORAL_ADDRESS") or os.getenv("TEMPORAL_HOST") or "localhost:7233"
TEMPORAL_HOST = TEMPORAL_ADDRESS  # backwards-compatible alias
TEMPORAL_NAMESPACE = os.getenv("TEMPORAL_NAMESPACE", "default")
# Temporal Cloud API key. When set, the connection uses TLS.
TEMPORAL_API_KEY = os.getenv("TEMPORAL_API_KEY") or None
TEMPORAL_TASK_QUEUE = os.getenv("TEMPORAL_TASK_QUEUE", "petkit-tasks")

# Worker Versioning (required for Serverless Workers). Set both to turn it on;
# the deploy script sets them on the Cloud Run worker pool. Leave unset locally.
WORKER_DEPLOYMENT_NAME = os.getenv("WORKER_DEPLOYMENT_NAME") or None
WORKER_BUILD_ID = os.getenv("WORKER_BUILD_ID") or None

# Set the client identity from Cloud Run instance metadata. Only works on
# Cloud Run (the metadata server must be reachable).
TEMPORAL_CLOUD_RUN_IDENTITY = _flag("TEMPORAL_CLOUD_RUN_IDENTITY", False)

# Run a Temporal worker inside the API process. Convenient locally; off on
# Cloud Run, where the worker is a separate worker pool (ADR 0001, 0006).
RUN_EMBEDDED_WORKER = _flag("RUN_EMBEDDED_WORKER", True)

# Directory of the built frontend (npm run build) for the API to serve. Unset
# locally, where Vite serves the frontend and proxies /api.
FRONTEND_DIST_DIR = os.getenv("FRONTEND_DIST_DIR") or None

# Firestore: the worker writes device snapshots, the API reads them (ADR 0006).
GOOGLE_CLOUD_PROJECT = os.getenv("GOOGLE_CLOUD_PROJECT") or None
FIRESTORE_DATABASE = os.getenv("FIRESTORE_DATABASE", "(default)")
FIRESTORE_COLLECTION = os.getenv("FIRESTORE_COLLECTION", "feeders")

# How often the worker refreshes device snapshots from PetKit.
SNAPSHOT_REFRESH_MINUTES = int(os.getenv("SNAPSHOT_REFRESH_MINUTES", "30"))

# A scheduled feed is skipped if the feeder dispensed food within this many hours
# (manual feed, PetKit app, or the button on the device). Keep it under 24h, or
# each daily feed will cause the next one to be skipped. 0 disables skipping.
# Read by the worker at feed time, so changes apply after a restart; schedules
# don't need to be re-saved.
FEED_SKIP_WINDOW_HOURS = float(os.getenv("FEED_SKIP_WINDOW_HOURS", "12"))
