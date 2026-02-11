import os
from pathlib import Path

from dotenv import load_dotenv as loadenv

# Load environment variables from secrets.env
_SECRETS_PATH = Path(__file__).resolve().parents[1] / "secrets.env"
loadenv(_SECRETS_PATH)

def _parse_int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default

AGENT_ROLE_VALUE = "raspberrypi-agent"
AGENT_CAPABILITY_NAME = "agent_instruction"
AGENT_COMMAND_NAME = "agent_instruction"

MAX_COMPLETED_JOBS = int(os.getenv("MAX_COMPLETED_JOBS", "200"))
DEVICE_RESULT_TIMEOUT = float(os.getenv("DEVICE_RESULT_TIMEOUT", "300.0"))
LLM_DAILY_API_LIMIT = max(0, _parse_int_env("LLM_DAILY_API_LIMIT", 0))

SECRET_KEY = os.getenv("FLASK_SECRET_KEY", "")

PROMPT_GUARD_ENABLED = os.getenv("PROMPT_GUARD_ENABLED", "true")
PROMPT_GUARD_MODEL = os.getenv("PROMPT_GUARD_MODEL", "openai/gpt-oss-safeguard-20b")
PROMPT_GUARD_BLOCK_MESSAGE = os.getenv(
    "PROMPT_GUARD_BLOCK_MESSAGE",
    "申し訳ありませんが、その依頼には対応できません。"
    "システムの指示や安全設定を変更しようとする内容が含まれている可能性があります。"
    "デバイス操作の目的を具体的に教えてください。",
)


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
