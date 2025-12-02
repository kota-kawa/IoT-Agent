"""
共通設定値と secrets.py ロードを切り出したモジュール。
"""

# Flask サーバーへの接続先 URL と API パス
BASE_URL = "https://iot-agent.project-kk.com"
REGISTER_PATH = "/api/devices/register"
NEXT_PATH = "/api/devices/{device_id}/jobs/next"
RESULT_PATH = "/api/devices/{device_id}/jobs/result"

# Wi-Fi 認証情報は secrets.py から読み込み（無ければ未設定扱い）
WIFI_SSID = ""
WIFI_PASSWORD = ""
try:
    from secrets import WIFI_SSID as _SSID, WIFI_PASSWORD as _PW  # type: ignore

    WIFI_SSID = _SSID
    WIFI_PASSWORD = _PW
except Exception:
    pass

DEVICE_LABEL = ""
DEVICE_LOCATION = ""
try:
    from secrets import DEVICE_LABEL as _DEVICE_LABEL  # type: ignore

    DEVICE_LABEL = _DEVICE_LABEL
except Exception:
    pass
try:
    from secrets import DEVICE_LOCATION as _DEVICE_LOCATION  # type: ignore

    DEVICE_LOCATION = _DEVICE_LOCATION
except Exception:
    pass

# ポーリングや登録関連の挙動を制御するパラメータ
POLL_INTERVAL_SEC = 1  # 1秒間隔でサーバーをポーリング
AUTO_REGISTER_ON_BOOT = True  # True にすると起動時に自動登録
AUTO_APPROVE = True  # True なら登録時に approved フラグを付与
CAPABILITY_SYNC_ENABLED = True  # 手動登録後でも機能一覧をサーバーへ同期する
CAPABILITY_RESYNC_INTERVAL_SEC = 30  # 同期失敗時の再試行間隔（秒）

try:
    from secrets import AUTO_APPROVE as _AUTO_APPROVE  # type: ignore

    AUTO_APPROVE = bool(_AUTO_APPROVE)
except Exception:
    pass

USER_AGENT = "MicroPython-IoT-Edge-Agent/1.1"
HTTP_BODY_PREVIEW_LEN = 512
RETURN_TEXT_LIMIT = 3000
RETURN_TEXT_KEEP = 1500
HTTP_TIMEOUT_SEC = 15
_RECV_CHUNK = 1024
RESULT_MAX_ATTEMPTS = 4
RESULT_RETRY_BASE_DELAY = 2

__all__ = [
    "BASE_URL",
    "REGISTER_PATH",
    "NEXT_PATH",
    "RESULT_PATH",
    "WIFI_SSID",
    "WIFI_PASSWORD",
    "DEVICE_LABEL",
    "DEVICE_LOCATION",
    "POLL_INTERVAL_SEC",
    "AUTO_REGISTER_ON_BOOT",
    "AUTO_APPROVE",
    "CAPABILITY_SYNC_ENABLED",
    "CAPABILITY_RESYNC_INTERVAL_SEC",
    "USER_AGENT",
    "HTTP_BODY_PREVIEW_LEN",
    "RETURN_TEXT_LIMIT",
    "RETURN_TEXT_KEEP",
    "HTTP_TIMEOUT_SEC",
    "_RECV_CHUNK",
    "RESULT_MAX_ATTEMPTS",
    "RESULT_RETRY_BASE_DELAY",
]
