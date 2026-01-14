from __future__ import annotations

import sys
from pathlib import Path
import os
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("IOT_AGENT_STORAGE_BACKEND", "memory")


@pytest.fixture(autouse=True)
def reset_model_override():
    from model_selection import update_override

    update_override(None)
    yield
    update_override(None)
