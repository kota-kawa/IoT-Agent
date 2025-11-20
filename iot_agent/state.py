from collections import deque
from typing import Any, Deque, Dict

from .models import DeviceState

# メモリ上でデバイス情報と進行中ジョブを管理する辞書
_DEVICES: Dict[str, DeviceState] = {}
_PENDING_JOBS: Dict[str, str] = {}
_JOB_METADATA: Dict[str, Dict[str, Any]] = {}
_COMPLETED_JOBS: Dict[str, Dict[str, Any]] = {}
_COMPLETED_JOB_ORDER: Deque[str] = deque()

