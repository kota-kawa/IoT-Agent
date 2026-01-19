import sys
import os
from pathlib import Path

# Mocking the path logic from iot_agent/config.py
_SECRETS_PATH = Path("iot_agent/config.py").resolve().parents[1] / "secrets.env"
print(f"Calculated secrets path: {_SECRETS_PATH}")
print(f"Exists: {_SECRETS_PATH.exists()}")

from iot_agent.config import APP_PASSWORD
print(f"APP_PASSWORD from config: '{APP_PASSWORD}'")
