"""Configuration and capability definitions for the Jetson edge agent."""

import os
from pathlib import Path
from typing import Any, Dict, List

MODEL_PATH = os.getenv("LLAMA_MODEL_PATH", "TinySwallow-1.5B-Instruct-Q5_K_S.gguf")
LLAMA_THREADS = int(os.getenv("LLAMA_THREADS", "4"))
LLAMA_CONTEXT = int(os.getenv("LLAMA_CONTEXT", "1024"))
LLAMA_BATCH = int(os.getenv("LLAMA_BATCH", "32"))
# GPU推論を使用する場合 (CPUを使用する場合はコメントアウトしてください)
LLAMA_GPU_LAYERS = int(os.getenv("LLAMA_GPU_LAYERS", "-1"))

# CPU推論を使用する場合 (有効にするにはコメントアウトを外してください)
# LLAMA_GPU_LAYERS = 0
LLAMA_TEMPERATURE = float(os.getenv("LLAMA_TEMPERATURE", "0.2"))
LLAMA_SEED = int(os.getenv("LLAMA_SEED", "42"))

SERVER_BASE_URL = os.getenv(
    "IOT_SERVER_URL", "https://iot-agent.project-kk.com"
).rstrip("/")
REQUEST_TIMEOUT = float(os.getenv("IOT_AGENT_HTTP_TIMEOUT", "60"))
POLL_INTERVAL = float(os.getenv("IOT_AGENT_POLL_INTERVAL", "2.0"))

DEVICE_ID_ENV = os.getenv("IOT_AGENT_DEVICE_ID")
DEVICE_ID_PATH = Path(
    os.getenv(
        "IOT_AGENT_DEVICE_ID_PATH",
        str(Path(__file__).resolve().parent / "device_id.txt"),
    )
)

DISPLAY_NAME = os.getenv("IOT_AGENT_DISPLAY_NAME", "Jetson Orin Agent")
LOCATION = os.getenv("IOT_AGENT_LOCATION", "Lab")
_AUTO_APPROVE_RAW = os.getenv("IOT_AGENT_AUTO_APPROVE", "1")
AUTO_APPROVE = (
    (_AUTO_APPROVE_RAW or "").strip().lower()
    in {
        "1",
        "true",
        "yes",
        "on",
    }
)

REGISTER_PATH = "/api/devices/register"
NEXT_PATH = "/api/devices/{device_id}/jobs/next"
RESULT_PATH = "/api/devices/{device_id}/jobs/result"

AGENT_ROLE_VALUE = "jetson-agent"
AGENT_COMMAND_NAME = "agent_instruction"

SUPPORTED_ACTIONS: Dict[str, Dict[str, Any]] = {
    "get_current_time": {
        "description": "Return the current local time in ISO 8601 format.",
        "params": [],
    },
    "run_motor_test": {
        "description": "Use the L293D wiring on BCM24/23/27/22 to drive both motors forward and reverse.",
        "params": [
            {
                "name": "forward_seconds",
                "type": "number",
                "required": False,
                "description": "Duration in seconds to run forward (default: 3).",
            },
            {
                "name": "reverse_seconds",
                "type": "number",
                "required": False,
                "description": "Duration in seconds to run reverse (default: 3).",
            },
            {
                "name": "brake_seconds",
                "type": "number",
                "required": False,
                "description": "Brake hold time in seconds (default: 1).",
            },
        ],
    },
    "control_motor": {
        "description": "Control the robot motors to move in a specific direction for a set duration.",
        "params": [
            {
                "name": "direction",
                "type": "string",
                "required": True,
                "description": "Movement direction: 'forward', 'backward', 'left', 'right'.",
            },
            {
                "name": "duration",
                "type": "number",
                "required": False,
                "description": "Duration in seconds (default: 1.0).",
            },
        ],
    },
    "show_text_on_oled": {
        "description": "Display text on the SH1107 OLED screen.",
        "params": [
            {
                "name": "text",
                "type": "string",
                "required": True,
                "description": "The text string to display.",
            },
            {
                "name": "duration",
                "type": "number",
                "required": False,
                "description": "How long to keep the text displayed (default: 10).",
            }
        ],
    },
    "measure_distance_cm": {
        "description": "Measure distance once via HC-SR04 on BCM5 (TRIG) / BCM6 (ECHO).",
        "params": [],
    },
    "monitor_motion": {
        "description": "Monitor the SR501 PIR on BCM26 for a short period and report detections.",
        "params": [
            {
                "name": "duration",
                "type": "number",
                "required": False,
                "description": "Number of seconds to watch for motion (default: 20).",
            }
        ],
    },
    "no_action": {
        "description": "Used when the request should not trigger a device operation.",
        "params": [
            {"name": "message", "type": "string", "required": False},
        ],
    },
}

ACTION_CATALOG: List[Dict[str, Any]] = [
    {
        "name": action,
        "description": spec["description"],
        "params": spec.get("params", []),
    }
    for action, spec in SUPPORTED_ACTIONS.items()
    if action != "no_action"
]

CAPABILITIES: List[Dict[str, Any]] = [
    {
        "name": AGENT_COMMAND_NAME,
        "description": "Execute Jetson automation tasks derived from simple English instructions.",
        "params": [
            {"name": "instruction", "type": "string", "required": True},
        ],
    },
    *ACTION_CATALOG,
]

LLM_SYSTEM_PROMPT = (
    "あなたはJetsonハードウェアエージェント用のコマンド変換AIです。\n"
    "ユーザーの指示を以下のJSON形式に変換してください：\n"
    '{"action": "<アクション名>", "parameters": { ... }, "message": "<任意のメッセージ>"}\n'
    "JSONのみを出力し、説明やコードフェンスは含めないでください。\n\n"
    "【利用可能なアクション】\n"
    "- get_current_time: 現在時刻を取得（パラメータなし）\n"
    "- control_motor: モーター制御\n"
    "  - direction (必須): 'forward', 'backward', 'left', 'right'\n"
    "  - duration (任意): 秒数（デフォルト: 1.0）\n"
    "- run_motor_test: モーターテスト（前進→ブレーキ→後退）\n"
    "  - forward_seconds (任意): 前進秒数（デフォルト: 3）\n"
    "  - reverse_seconds (任意): 後退秒数（デフォルト: 3）\n"
    "  - brake_seconds (任意): ブレーキ秒数（デフォルト: 1）\n"
    "- show_text_on_oled: OLEDにテキスト表示\n"
    "  - text (必須): 表示する文字列\n"
    "  - duration (任意): 表示秒数（デフォルト: 10）\n"
    "- measure_distance_cm: 超音波センサーで距離測定（パラメータなし）\n"
    "- monitor_motion: PIRセンサーで動体検知\n"
    "  - duration (任意): 監視秒数（デフォルト: 20）\n"
    "- no_action: 操作不要な場合\n\n"
    "【例】\n"
    "指示: 今何時？\n"
    '{"action": "get_current_time", "parameters": {}}\n'
    "指示: 前に2秒進んで\n"
    '{"action": "control_motor", "parameters": {"direction": "forward", "duration": 2.0}}\n'
    "指示: 画面にこんにちはと表示して\n"
    '{"action": "show_text_on_oled", "parameters": {"text": "こんにちは"}}\n'
    "指示: 距離を測って\n"
    '{"action": "measure_distance_cm", "parameters": {}}\n'
    "指示: 10秒間動きを監視して\n"
    '{"action": "monitor_motion", "parameters": {"duration": 10}}\n'
    "指示: ありがとう\n"
    '{"action": "no_action", "parameters": {}, "message": "操作は不要です"}\n'
)

_RETURN_TEXT_LIMIT = 3000
_RETURN_TEXT_KEEP = 1500
