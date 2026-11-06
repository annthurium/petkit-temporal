import os
from dotenv import load_dotenv

load_dotenv()

PETKIT_USERNAME = os.getenv("PETKIT_USERNAME", "")
PETKIT_PASSWORD = os.getenv("PETKIT_PASSWORD", "")
PETKIT_REGION = os.getenv("PETKIT_REGION", "US")
PETKIT_TIMEZONE = os.getenv("PETKIT_TIMEZONE", "America/Los_Angeles")

# Temporal configuration
TEMPORAL_HOST = os.getenv("TEMPORAL_HOST", "localhost:7233")
TEMPORAL_NAMESPACE = os.getenv("TEMPORAL_NAMESPACE", "default")
TEMPORAL_TASK_QUEUE = os.getenv("TEMPORAL_TASK_QUEUE", "petkit-tasks")

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
