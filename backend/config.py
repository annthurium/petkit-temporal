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
