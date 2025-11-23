import os
from pathlib import Path

from dotenv import load_dotenv as loadenv

# Load environment variables from secrets.env (with legacy .env fallback)
_SECRETS_PATH = Path(__file__).resolve().parents[1] / "secrets.env"
if not loadenv(_SECRETS_PATH):
    loadenv()

APP_PASSWORD = "kkawagoe"

AGENT_ROLE_VALUE = "raspberrypi-agent"
AGENT_CAPABILITY_NAME = "agent_instruction"
AGENT_COMMAND_NAME = "agent_instruction"

MAX_COMPLETED_JOBS = int(os.getenv("MAX_COMPLETED_JOBS", "200"))
DEVICE_RESULT_TIMEOUT = float(os.getenv("DEVICE_RESULT_TIMEOUT", "120"))

SECRET_KEY = os.getenv("FLASK_SECRET_KEY", "change-this-secret")
