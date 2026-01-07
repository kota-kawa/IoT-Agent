import os
from pathlib import Path

from dotenv import load_dotenv as loadenv

# Load environment variables from secrets.env
_SECRETS_PATH = Path(__file__).resolve().parents[1] / "secrets.env"
loadenv(_SECRETS_PATH)

APP_PASSWORD = os.getenv("APP_PASSWORD")

AGENT_ROLE_VALUE = "raspberrypi-agent"
AGENT_CAPABILITY_NAME = "agent_instruction"
AGENT_COMMAND_NAME = "agent_instruction"

MAX_COMPLETED_JOBS = int(os.getenv("MAX_COMPLETED_JOBS", "200"))
DEVICE_RESULT_TIMEOUT = float(os.getenv("DEVICE_RESULT_TIMEOUT", "300.0"))

SECRET_KEY = os.getenv("FLASK_SECRET_KEY", "change-this-secret")

DATABASE_URL = os.getenv("DATABASE_URL")
REDIS_URL = os.getenv("REDIS_URL")
STORAGE_BACKEND = os.getenv("IOT_AGENT_STORAGE_BACKEND", "auto")
REDIS_PREFIX = os.getenv("IOT_AGENT_REDIS_PREFIX", "iot_agent")
try:
    JOB_RESULT_TTL_SECONDS = int(os.getenv("IOT_AGENT_JOB_RESULT_TTL", "3600"))
except ValueError:
    JOB_RESULT_TTL_SECONDS = 3600
