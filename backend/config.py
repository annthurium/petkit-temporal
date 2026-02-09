import os
from dotenv import load_dotenv

load_dotenv()

PETKIT_USERNAME = os.getenv("PETKIT_USERNAME", "")
PETKIT_PASSWORD = os.getenv("PETKIT_PASSWORD", "")
PETKIT_REGION = os.getenv("PETKIT_REGION", "US")
PETKIT_TIMEZONE = os.getenv("PETKIT_TIMEZONE", "America/Los_Angeles")

NUM_RETRIES = 5
