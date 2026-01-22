import os
from pathlib import Path

from dotenv import load_dotenv as loadenv

# Load environment variables from secrets.env
_SECRETS_PATH = Path(__file__).resolve().parents[1] / "secrets.env"
loadenv(_SECRETS_PATH)

_raw_password = os.getenv("APP_PASSWORD")
if _raw_password is None:
    # Security: Do not default to empty password. Force user to configure it.
    raise ValueError("APP_PASSWORD is missing. Please set it in secrets.env.")

# Avoid CRLF artifacts from secrets.env without stripping intentional spaces.
APP_PASSWORD = _raw_password.rstrip("\r\n")

AGENT_ROLE_VALUE = "raspberrypi-agent"
AGENT_CAPABILITY_NAME = "agent_instruction"
AGENT_COMMAND_NAME = "agent_instruction"

MAX_COMPLETED_JOBS = int(os.getenv("MAX_COMPLETED_JOBS", "200"))
DEVICE_RESULT_TIMEOUT = float(os.getenv("DEVICE_RESULT_TIMEOUT", "300.0"))

SECRET_KEY = os.getenv("FLASK_SECRET_KEY")
if not SECRET_KEY or SECRET_KEY == "change-this-secret":
    raise ValueError("FLASK_SECRET_KEY is missing or insecure. Please set a strong random string in secrets.env.")


DEFAULT_IOT_AGENT_API_BASE_URL = "http://localhost:5006/"
_raw_api_base = os.getenv("IOT_AGENT_API_BASE_URL")
if isinstance(_raw_api_base, str) and _raw_api_base:
    IOT_AGENT_API_BASE_URL = _raw_api_base.rstrip("/")
else:
    IOT_AGENT_API_BASE_URL = DEFAULT_IOT_AGENT_API_BASE_URL.rstrip("/")

DATABASE_URL = os.getenv("DATABASE_URL")
REDIS_URL = os.getenv("REDIS_URL")
STORAGE_BACKEND = os.getenv("IOT_AGENT_STORAGE_BACKEND", "auto")
REDIS_PREFIX = os.getenv("IOT_AGENT_REDIS_PREFIX", "iot_agent")
try:
    JOB_RESULT_TTL_SECONDS = int(os.getenv("IOT_AGENT_JOB_RESULT_TTL", "3600"))
except ValueError:
    JOB_RESULT_TTL_SECONDS = 3600
