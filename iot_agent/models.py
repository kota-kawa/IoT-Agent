import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, List, Optional


@dataclass
class DeviceState:
    # メモリ上に保持するエッジデバイスの状態情報

    # デバイス識別子（例: シリアル番号）
    device_id: str
    # サーバーに登録された機能一覧
    capabilities: List[Dict[str, Any]]
    # 任意メタデータ（表示名や説明など）
    meta: Dict[str, Any]
    # エッジデバイスが取得するジョブの待ち行列
    job_queue: Deque[Dict[str, Any]] = field(default_factory=deque)
    # 完了したジョブ結果を job_id ごとに保持
    job_results: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    # 最後にポーリングされた時刻（UNIX 時刻）
    last_seen: float = field(default_factory=time.time)
    # 直近のジョブ結果
    last_result: Optional[Dict[str, Any]] = None
    # 登録時刻（UNIX 時刻）
    registered_at: float = field(default_factory=time.time)
    # 管理者承認済みかどうか
    approved: bool = False


@dataclass
class _CommandExecutionSummary:
    # コマンド実行結果をテンポラリにまとめる内部用データ構造

    device_id: Optional[str]
    command_name: str
    job_id: Optional[str] = None
    args: Dict[str, Any] = field(default_factory=dict)
    manual_reply: str = ""
    result: Optional[Dict[str, Any]] = None
    instruction: Optional[str] = None
    is_agent: bool = False
    status: int = 200
    error_text: Optional[str] = None
