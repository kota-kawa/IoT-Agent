#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Raspberry Pi 4 edge agent for the IoT server.

This script connects to the Flask server, receives natural-language
instructions that were simplified by GPT-4.1, converts them into
structured JSON with a local TinyLlama model, executes supported tasks on
the Pi, and reports the results back to the server.

The implementation avoids hardware-specific features so that it runs on a
plain Raspberry Pi 4 without additional peripherals.
"""

import argparse
import base64
import json
import logging
import os
import random
import re
import shlex
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

# HTTP 通信とローカル推論エンジンを扱う外部ライブラリを読み込む
import requests
from dotenv import load_dotenv
from llama_cpp import Llama

# Load environment variables from potential secrets.env locations before reading them.
# secrets.env ファイルを探索する候補パス（レガシー .env もフォールバック）
_ENV_CANDIDATES = [
    Path(__file__).resolve().parent / "secrets.env",
    Path(__file__).resolve().parent.parent / "secrets.env",
    Path.cwd() / "secrets.env",
    Path(__file__).resolve().parent / ".env",
    Path(__file__).resolve().parent.parent / ".env",
    Path.cwd() / ".env",
]
for _env_file in _ENV_CANDIDATES:
    # 各パスに secrets.env/.env があれば読み込んで環境変数を補完
    if _env_file.exists():
        load_dotenv(_env_file, override=False)
# Also respect any other default .env resolution from python-dotenv.
load_dotenv(override=False)

# ==== Configuration ========================================================

# TinyLlama などローカル LLM の推論設定
MODEL_PATH = os.getenv("LLAMA_MODEL_PATH", "Llama-3.2-3B-Instruct-Q3_K_M.gguf")
LLAMA_THREADS = int(os.getenv("LLAMA_THREADS", "4"))
LLAMA_CONTEXT = int(os.getenv("LLAMA_CONTEXT", "1024"))
LLAMA_TEMPERATURE = float(os.getenv("LLAMA_TEMPERATURE", "0.2"))

# NOTE: The IoT server is deployed remotely, so we default to the public
# endpoint. Set IOT_SERVER_URL to override when testing against a different
# environment.
# Flask サーバーのベース URL（デフォルトは公開エンドポイント）
SERVER_BASE_URL = os.getenv(
    "IOT_SERVER_URL", "https://iot-agent.project-kk.com"
).rstrip("/")
# Default to a 3 minute HTTP timeout to accommodate longer-running server
# operations, while still allowing customization through the environment
# variable.
# HTTP タイムアウトやポーリング間隔など通信関連の設定
REQUEST_TIMEOUT = float(os.getenv("IOT_AGENT_HTTP_TIMEOUT", "60"))
POLL_INTERVAL = float(os.getenv("IOT_AGENT_POLL_INTERVAL", "2.0"))

# 自動登録フラグ（ブール文字列を解釈）
_AUTO_REGISTER_RAW = os.getenv("IOT_AGENT_AUTO_REGISTER")
AUTO_REGISTRATION_REQUESTED = (
    (_AUTO_REGISTER_RAW or "").strip().lower()
    in {
        "1",
        "true",
        "yes",
        "on",
    }
)
# Self-approval toggle so the agent can register capabilities without a dashboard
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

# 天気情報取得に使う OpenWeather の資格情報
OPEN_WEATHER_API_KEY = os.getenv("OPEN_WEATHER_API_KEY")
OPEN_WEATHER_BASE_URL = os.getenv(
    "OPEN_WEATHER_BASE_URL", "https://api.openweathermap.org/data/2.5/weather"
)

# デバイス ID は環境変数かファイルから解決
DEVICE_ID_ENV = os.getenv("IOT_AGENT_DEVICE_ID")
DEVICE_ID_PATH = Path(
    os.getenv(
        "IOT_AGENT_DEVICE_ID_PATH",
        str(Path(__file__).resolve().parent / "device_id.txt"),
    )
)

DEVICE_TEST_DIR = Path(__file__).resolve().parent / "device_test"
CAMERA_SAVE_DIR = Path(
    os.getenv("IOT_AGENT_CAMERA_DIR", "/home/kota/iot-agent/test")
).expanduser()
CAMERA_WARMUP_SECONDS = float(os.getenv("IOT_AGENT_CAMERA_WARMUP", "1.2"))

DISPLAY_NAME = os.getenv("IOT_AGENT_DISPLAY_NAME", "Raspberry Pi 4 Agent")
LOCATION = os.getenv("IOT_AGENT_LOCATION", "Lab")

REGISTER_PATH = "/api/devices/register"
NEXT_PATH = "/api/devices/{device_id}/jobs/next"
RESULT_PATH = "/api/devices/{device_id}/jobs/result"

AGENT_ROLE_VALUE = "raspberrypi-agent"
AGENT_COMMAND_NAME = "agent_instruction"

# Pi がネイティブにサポートするアクション定義
SUPPORTED_ACTIONS: Dict[str, Dict[str, Any]] = {
    "play_rock_paper_scissors": {
        "description": "Play a round of rock-paper-scissors against the agent.",
        "params": [
            {
                "name": "player_move",
                "type": "string",
                "required": False,
                "description": "Player's move: rock, paper, or scissors",
            }
        ],
    },
    "get_current_time": {
        "description": "Return the current local time in ISO 8601 format.",
        "params": [],
    },
    "get_weather": {
        "description": "Fetch current weather information for a given location using OpenWeather.",
        "params": [
            {
                "name": "location",
                "type": "string",
                "required": True,
                "description": "City name or query string accepted by OpenWeather (e.g. 'Tokyo,JP').",
            },
            {
                "name": "units",
                "type": "string",
                "required": False,
                "description": "Units system: standard, metric, or imperial (default: metric).",
            },
        ],
    },
    "tell_joke": {
        "description": "Tell one joke chosen from a predefined list.",
        "params": [],
    },
    "run_motor_test": {
        "description": "Execute the built-in dual DC motor diagnostic routine using the configured L293D wiring.",
        "params": [
            {
                "name": "timeout",
                "type": "number",
                "required": False,
                "description": "Optional timeout in seconds before the motor test is stopped.",
            }
        ],
    },
    "run_oled_robot_demo": {
        "description": "Show the ST7735 mono-eye animation (Zaku-style) on the connected display and optionally render text.",
        "params": [
            {
                "name": "timeout",
                "type": "number",
                "required": False,
                "description": "Optional timeout in seconds before stopping the animation demo.",
            },
            {
                "name": "text",
                "type": "string",
                "required": False,
                "description": "Optional English text to draw under the mono-eye.",
            }
        ],
    },
    "run_servo_demo": {
        "description": "Execute the integrated servo control utilities (demo, set, center, off, sweep, info).",
        "params": [
            {
                "name": "command",
                "type": "string",
                "required": False,
                "description": "Raw command string to pass to the servo script (e.g. 'set --angle 90').",
            },
            {
                "name": "mode",
                "type": "string",
                "required": False,
                "description": "Named subcommand to run (set, center, off, sweep, info).",
            },
            {
                "name": "angle",
                "type": "number",
                "required": False,
                "description": "Angle in degrees used with the set subcommand (0-180).",
            },
            {
                "name": "start",
                "type": "number",
                "required": False,
                "description": "Start angle in degrees for sweep operations.",
            },
            {
                "name": "end",
                "type": "number",
                "required": False,
                "description": "End angle in degrees for sweep operations.",
            },
            {
                "name": "step",
                "type": "number",
                "required": False,
                "description": "Step size in degrees for sweep operations.",
            },
            {
                "name": "delay",
                "type": "number",
                "required": False,
                "description": "Delay in seconds between sweep steps.",
            },
            {
                "name": "cycles",
                "type": "integer",
                "required": False,
                "description": "Number of sweep cycles to execute (0 for infinite).",
            },
            {
                "name": "channel",
                "type": "integer",
                "required": False,
                "description": "Servo channel (1-4).",
            },
            {
                "name": "pigpio",
                "type": "boolean",
                "required": False,
                "description": "When true, add the --pigpio flag to use the PiGPIO factory.",
            },
            {
                "name": "hold",
                "type": "number",
                "required": False,
                "description": "Hold duration in seconds after executing the servo command.",
            },
            {
                "name": "timeout",
                "type": "number",
                "required": False,
                "description": "Optional timeout in seconds before stopping the servo script.",
            },
        ],
    },
    "capture_camera_photo": {
        "description": "Capture a still photo using the Picamera2 module and save it to the default test directory.",
        "params": [
            {
                "name": "filename",
                "type": "string",
                "required": False,
                "description": "Optional filename (JPEG) to use instead of the default timestamp-based name.",
            },
            {
                "name": "directory",
                "type": "string",
                "required": False,
                "description": "Optional output directory; defaults to /home/kota/iot-agent/test.",
            },
            {
                "name": "warmup",
                "type": "number",
                "required": False,
                "description": "Warmup time in seconds before capturing the photo.",
            },
        ],
    },
    "run_led_demo": {
        "description": "Run the three-LED chase and blink demo using GPIO2, GPIO3, and GPIO16.",
        "params": [
            {
                "name": "cycles",
                "type": "integer",
                "required": False,
                "description": "Number of pattern cycles to run (0 for continuous until timeout).",
            },
            {
                "name": "timeout",
                "type": "number",
                "required": False,
                "description": "Optional timeout in seconds for the LED routine.",
            },
        ],
    },
    "run_dual_servo_demo": {
        "description": "Run the dual-servo inverse sweep demo on GPIO12 and GPIO19.",
        "params": [
            {
                "name": "command",
                "type": "string",
                "required": False,
                "description": "Subcommand to run: demo, set, off, or info (defaults to demo).",
            },
            {
                "name": "cycles",
                "type": "integer",
                "required": False,
                "description": "Number of sweep cycles to perform (0 for continuous until timeout).",
            },
            {
                "name": "step",
                "type": "number",
                "required": False,
                "description": "Angle step size in degrees for the sweep.",
            },
            {
                "name": "delay",
                "type": "number",
                "required": False,
                "description": "Delay in seconds between servo updates.",
            },
            {
                "name": "pigpio",
                "type": "boolean",
                "required": False,
                "description": "Use the PiGPIO pin factory for more stable PWM (if available).",
            },
            {
                "name": "angle1",
                "type": "number",
                "required": False,
                "description": "Angle for servo1 (GPIO12) when using the set command.",
            },
            {
                "name": "angle2",
                "type": "number",
                "required": False,
                "description": "Angle for servo2 (GPIO19) when using the set command.",
            },
            {
                "name": "hold",
                "type": "number",
                "required": False,
                "description": "Hold duration in seconds after executing set/off (0 for none).",
            },
            {
                "name": "timeout",
                "type": "number",
                "required": False,
                "description": "Optional timeout in seconds for the dual-servo routine.",
            },
        ],
    },
    "no_action": {
        "description": "Used when the request should not trigger a device operation.",
        "params": [
            {"name": "message", "type": "string", "required": False},
        ],
    },
}

# サーバーへ公開するアクションカタログ（no_action を除外）
ACTION_CATALOG = [
    {
        "name": action,
        "description": spec["description"],
        "params": spec.get("params", []),
    }
    for action, spec in SUPPORTED_ACTIONS.items()
    if action != "no_action"
]

# サーバー登録時に伝える capability 情報
CAPABILITIES = [
    {
        "name": AGENT_COMMAND_NAME,
        "description": "Execute Raspberry Pi automation tasks derived from simple English instructions.",
        "params": [
            {"name": "instruction", "type": "string", "required": True},
        ],
    },
    *ACTION_CATALOG,
]


def _console(message: str) -> None:
    """Emit a human-readable status line to the terminal."""

    # 実行中の状態をターミナルへ表示する共通処理
    try:
        print(f"[agent] {message}", flush=True)
    except Exception:  # pragma: no cover - printing should never fail, but stay safe
        pass

LLM_SYSTEM_PROMPT = (
    "You convert simple English instructions into JSON commands for a Raspberry Pi automation agent.\n"
    "Return ONLY a single JSON object that exactly matches the schema:\n"
    '{"action": "<one of the supported actions>", "parameters": { ... }, "message": "<optional string>"}\n'
    "Do not include code fences, explanations, or any trailing text.\n"
    "Valid actions are: "
    + ", ".join(sorted(SUPPORTED_ACTIONS.keys()))
    + ".\n"
    "Always choose the action that best fulfills the instruction.\n"
    "Only respond with 'no_action' when the request is impossible or unrelated to the available actions.\n"
    "Include all required parameters.\n"
    "Examples:\n"
    "Instruction: Let's play rock paper scissors, I choose rock.\n"
    "{\"action\": \"play_rock_paper_scissors\", \"parameters\": {\"player_move\": \"rock\"}}\n"
    "Instruction: What's the weather in Tokyo in metric units?\n"
    "{\"action\": \"get_weather\", \"parameters\": {\"location\": \"Tokyo\", \"units\": \"metric\"}}\n"
    "Instruction: What time is it right now?\n"
    "{\"action\": \"get_current_time\", \"parameters\": {}}\n"
    "Instruction: Tell me a joke.\n"
    "{\"action\": \"tell_joke\", \"parameters\": {}}\n"
    "Instruction: Just saying thank you!\n"
    "{\"action\": \"no_action\", \"parameters\": {}, \"message\": \"No task requested.\"}"
)

# ==== Helpers ==============================================================


def _build_url(path: str) -> str:
    # API パスをベース URL と結合してアクセス先を得る
    return f"{SERVER_BASE_URL}{path}"


_DIGIT_NORMALIZATION = str.maketrans("０１２３４５６７８９", "0123456789")


def _normalize_digits(text: str) -> str:
    """Convert full-width digits to ASCII so heuristics catch Japanese numerals."""

    return text.translate(_DIGIT_NORMALIZATION)


def _load_device_id() -> str:
    # ファイルキャッシュと環境変数を考慮してデバイス ID を解決
    if DEVICE_ID_ENV:
        return DEVICE_ID_ENV.strip()

    try:
        if DEVICE_ID_PATH.exists():
            stored = DEVICE_ID_PATH.read_text(encoding="utf-8").strip()
            if stored:
                return stored
    except Exception as exc:  # pragma: no cover - filesystem edge cases
        logging.warning("Failed to read device id file: %s", exc)

    new_id = f"raspi-agent-{uuid.uuid4().hex[:12]}"
    try:
        DEVICE_ID_PATH.parent.mkdir(parents=True, exist_ok=True)
        DEVICE_ID_PATH.write_text(new_id, encoding="utf-8")
    except Exception as exc:  # pragma: no cover - filesystem edge cases
        logging.warning("Unable to persist device id: %s", exc)
    return new_id


def _create_llm() -> Llama:
    # 指定パスの GGUF モデルを読み込んで推論器インスタンスを生成
    if not Path(MODEL_PATH).exists():
        logging.error("Model file not found: %s", MODEL_PATH)
        sys.exit(1)

    logging.info("Loading model from %s", MODEL_PATH)
    return Llama(
        model_path=MODEL_PATH,
        n_threads=LLAMA_THREADS,
        n_ctx=LLAMA_CONTEXT,
        verbose=False,
    )


def _log_dict(label: str, value: Dict[str, Any], *, level: int = logging.INFO) -> None:
    # 辞書データを JSON 文字列化してログに記録
    try:
        message = json.dumps(value, ensure_ascii=False, sort_keys=True)
    except TypeError:
        message = repr(value)
    logging.log(level, "%s: %s", label, message)


def _register_device(session: requests.Session, device_id: str) -> Tuple[bool, bool]:
    # サーバーへ登録リクエストを送り、成功と手動承認の要否を返す
    payload = {
        "device_id": device_id,
        "capabilities": CAPABILITIES,
        "meta": {
            "display_name": DISPLAY_NAME,
            "role": AGENT_ROLE_VALUE,
            "location": LOCATION,
            "action_catalog": ACTION_CATALOG,
            "note": "TinyLlama-powered Raspberry Pi agent",
            "registered_via": "edge-device",
        },
        "approved": AUTO_APPROVE,
    }

    _console(
        "Attempting to register device '{}' (display='{}', location='{}').".format(
            device_id,
            DISPLAY_NAME,
            LOCATION,
        )
    )
    try:
        resp = session.post(
            _build_url(REGISTER_PATH),
            json=payload,
            timeout=REQUEST_TIMEOUT,
        )
        if resp.status_code == 403:
            logging.warning(
                "Device not yet approved on server. Register the device ID '%s' manually via the dashboard.",
                device_id,
            )
            _console(
                "Registration pending approval for device '{}'. Approve it from the dashboard.".format(
                    device_id
                )
            )
            return False, True

        resp.raise_for_status()
        try:
            data = resp.json()
        except ValueError:
            data = {}
        logging.info("Device registration acknowledged: status=%s", data.get("status", "ok"))
        _log_dict("Server device snapshot", data.get("device") or {})
        status_text = data.get("status") or "ok"
        _console(
            "Device '{}' registration succeeded with status '{}'.".format(
                device_id,
                status_text,
            )
        )
        return True, False
    except Exception as exc:
        logging.error("Registration failed: %s", exc)
        _console("Device '{}' registration failed: {}".format(device_id, exc))
        return False, False


def _poll_next_job(session: requests.Session, device_id: str) -> Optional[Dict[str, Any]]:
    # サーバーから次のジョブを取得し、必要に応じて再登録を試みる
    try:
        resp = session.get(
            _build_url(NEXT_PATH.format(device_id=device_id)),
            timeout=REQUEST_TIMEOUT,
        )
    except Exception as exc:
        logging.error("Failed to poll for job: %s", exc)
        return None

    if resp.status_code == 204:
        return None

    if resp.status_code == 404:
        logging.warning("Device not registered on server. Re-registering...")
        _console(
            "Server returned 404 for device '{}'. Triggering re-registration.".format(
                device_id
            )
        )
        registered, manual_required = _register_device(session, device_id)
        if not registered and manual_required:
            logging.warning(
                "Server still waiting for manual approval of device '%s'.", device_id
            )
            _console(
                "Device '{}' still awaiting manual approval on server.".format(device_id)
            )
        return None

    if resp.status_code != 200:
        logging.error("Unexpected status from job endpoint: %s", resp.status_code)
        _console(
            "Polling jobs failed with status {} for device '{}'.".format(
                resp.status_code,
                device_id,
            )
        )
        return None

    try:
        job = resp.json()
        job_id = job.get("job_id") or job.get("id")
        _console(
            "Received job {} from server.".format(job_id if job_id is not None else "<unknown>")
        )
        return job
    except json.JSONDecodeError:
        logging.error("Job payload is not valid JSON: %s", resp.text[:200])
        _console("Received invalid job payload from server (JSON decode error).")
    return None


def _post_result(
    session: requests.Session,
    payload: Dict[str, Any],
    *,
    max_attempts: int = 3,
    backoff_seconds: float = 2.0,
) -> bool:
    # ジョブ結果を再試行つきでサーバーに送信
    device_id_value = str(payload.get("device_id") or "").strip()
    if not device_id_value:
        logging.error("Result payload is missing device_id")
        return False

    url = _build_url(RESULT_PATH.format(device_id=device_id_value))
    attempt = 0
    while True:
        attempt += 1
        try:
            response = session.post(url, json=payload, timeout=REQUEST_TIMEOUT)
            if 200 <= response.status_code < 300:
                logging.info(
                    "Reported job %s result successfully (status=%s)",
                    payload.get("job_id"),
                    response.status_code,
                )
                _console(
                    "Result for job {} delivered successfully (status {}).".format(
                        payload.get("job_id"),
                        response.status_code,
                    )
                )
                return True

            body_preview = response.text[:200] if response.text else ""
            logging.error(
                "Result post attempt %s failed with status %s. Body preview: %s",
                attempt,
                response.status_code,
                body_preview,
            )
            _console(
                "Attempt {} to send result for job {} failed with status {}.".format(
                    attempt,
                    payload.get("job_id"),
                    response.status_code,
                )
            )
        except Exception as exc:
            logging.error("Result post attempt %s raised error: %s", attempt, exc)
            _console(
                "Attempt {} to send result for job {} raised error: {}.".format(
                    attempt,
                    payload.get("job_id"),
                    exc,
                )
            )

        if attempt >= max_attempts:
            break

        sleep_for = min(backoff_seconds * (2 ** (attempt - 1)), 30.0)
        logging.info("Retrying result post in %.1f seconds", sleep_for)
        _console(
            "Retrying result delivery for job {} in {:.1f} seconds.".format(
                payload.get("job_id"),
                sleep_for,
            )
        )
        time.sleep(sleep_for)

    return False


def _build_result_payload(
    *,
    device_id: str,
    job_id: str,
    ok: bool,
    action: Optional[str],
    parameters: Optional[Dict[str, Any]],
    message: Optional[str],
    result: Any,
    error: Optional[str],
) -> Dict[str, Any]:
    # サーバーが期待するフォーマットに結果をまとめる
    def _truncate_text(value: Any) -> Any:
        if isinstance(value, str) and len(value) > _RETURN_TEXT_LIMIT:
            head = value[:_RETURN_TEXT_KEEP]
            tail = value[-_RETURN_TEXT_KEEP:]
            return f"{head}...[truncated]...{tail}"
        return value

    truncated_message = _truncate_text(message)
    truncated_result = _truncate_text(result) if isinstance(result, str) else result
    truncated_error = _truncate_text(error)

    return {
        "device_id": device_id,
        "job_id": job_id,
        "ok": bool(ok),
        "return_value": {
            "action": action,
            "parameters": parameters or {},
            "message": truncated_message,
            "result": truncated_result,
        },
        "stdout": None,
        "stderr": None,
        "error": truncated_error,
        "ts": time.time(),
    }


# ==== Task execution =======================================================


def _format_for_log(value: Any, *, max_length: int = 500) -> str:
    # ログ出力用に値を文字列化し、長すぎる場合は省略記法にする
    try:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    except TypeError:
        text = repr(value)

    if len(text) > max_length:
        return text[: max_length - 20] + "...<truncated>"
    return text


JOKES = [
    "Why don't scientists trust atoms? Because they make up everything!",
    "I told my computer I needed a break, and it said 'No problem, I'll go to sleep.'",
    "What's a robot's favorite snack? Computer chips!",
    "Why do programmers confuse Halloween with Christmas? Because October 31st is December 25th.",
    "My Raspberry Pi was down so I asked it about it and it said, 'The battery is low.'",
]

_MOVE_ALIASES = {
    "rock": "rock",
    "stone": "rock",
    "gu": "rock",
    "goo": "rock",
    "paper": "paper",
    "paa": "paper",
    "pa": "paper",
    "hand": "paper",
    "scissors": "scissors",
    "choki": "scissors",
    "scissor": "scissors",
}

_VALID_MOVES = ("rock", "paper", "scissors")

_WIN_MAP = {
    "rock": "scissors",
    "scissors": "paper",
    "paper": "rock",
}


def _normalize_move(value: str) -> Optional[str]:
    # じゃんけんの手を多言語表現から正規化
    key = value.strip().lower()
    return _MOVE_ALIASES.get(key)


def _play_rock_paper_scissors(params: Dict[str, Any]) -> Dict[str, Any]:
    # じゃんけん対戦を行い、勝敗とメッセージを返す
    move_value = params.get("player_move") if isinstance(params, dict) else None
    if isinstance(move_value, str) and move_value.strip():
        player_move = _normalize_move(move_value)
        if not player_move:
            raise ValueError("player_move must be rock, paper, scissors")
        provided = True
    else:
        player_move = random.choice(_VALID_MOVES)
        provided = False

    agent_move = random.choice(_VALID_MOVES)
    if player_move == agent_move:
        outcome = "draw"
    elif _WIN_MAP[player_move] == agent_move:
        outcome = "win"
    else:
        outcome = "lose"

    result_message = {
        "win": "You win!",
        "lose": "You lose!",
        "draw": "It's a draw!",
    }[outcome]

    return {
        "player_move": player_move,
        "agent_move": agent_move,
        "outcome": outcome,
        "message": result_message,
        "player_move_was_random": not provided,
    }


def _tell_joke() -> Dict[str, Any]:
    # 事前定義されたジョークをランダムに選択
    joke = random.choice(JOKES)
    return {"joke": joke}


def _get_weather(params: Dict[str, Any]) -> Dict[str, Any]:
    # OpenWeather API を呼び出して現在の天気情報を取得
    if not OPEN_WEATHER_API_KEY:
        raise RuntimeError("OpenWeather API key is not configured in the environment.")

    if not isinstance(params, dict):
        params = {}

    location_value = params.get("location") or params.get("city")
    if not isinstance(location_value, str) or not location_value.strip():
        raise ValueError("location parameter must be provided as a non-empty string.")

    location = location_value.strip()

    units_value = "metric"
    raw_units = params.get("units")
    if isinstance(raw_units, str) and raw_units.strip():
        candidate_units = raw_units.strip().lower()
        if candidate_units not in {"standard", "metric", "imperial"}:
            raise ValueError("units must be one of: standard, metric, imperial.")
        units_value = candidate_units

    query_params = {
        "q": location,
        "appid": OPEN_WEATHER_API_KEY,
        "units": units_value,
    }

    try:
        response = requests.get(
            OPEN_WEATHER_BASE_URL,
            params=query_params,
            timeout=min(REQUEST_TIMEOUT, 30),
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        raise RuntimeError(f"Failed to fetch weather data: {exc}") from exc

    try:
        data = response.json()
    except ValueError as exc:
        raise RuntimeError("Weather service returned invalid JSON.") from exc

    main_data = data.get("main") or {}
    weather_list = data.get("weather") or []
    weather_description = None
    if weather_list and isinstance(weather_list, list):
        first = weather_list[0]
        if isinstance(first, dict):
            weather_description = first.get("description")

    sys_data = data.get("sys") or {}
    wind_data = data.get("wind") or {}

    return {
        "query": location,
        "location": data.get("name") or location,
        "country": sys_data.get("country"),
        "temperature": main_data.get("temp"),
        "feels_like": main_data.get("feels_like"),
        "humidity": main_data.get("humidity"),
        "weather": weather_description,
        "wind_speed": wind_data.get("speed"),
        "units": units_value,
    }


_DEFAULT_MOTOR_TEST_TIMEOUT = 30.0
_DEFAULT_OLED_DEMO_TIMEOUT = 60.0
_DEFAULT_SERVO_TIMEOUT = 60.0
_DEFAULT_LED_TIMEOUT = 45.0
_DEFAULT_DUAL_SERVO_TIMEOUT = 60.0
_OLED_RESULT_RETURN_SECONDS = 3.0
_RETURN_TEXT_LIMIT = 3000
_RETURN_TEXT_KEEP = 1500


def _parse_timeout_parameter(parameters: Any, default: float) -> float:
    """Extract a positive timeout value from action parameters."""

    if not isinstance(parameters, dict):
        return default

    raw_value = parameters.get("timeout")
    if raw_value is None:
        return default

    if isinstance(raw_value, (int, float)):
        timeout_value = float(raw_value)
    elif isinstance(raw_value, str):
        raw_value = raw_value.strip()
        if not raw_value:
            return default
        try:
            timeout_value = float(raw_value)
        except ValueError as exc:  # pragma: no cover - defensive
            raise ValueError("timeout must be a positive number of seconds.") from exc
    else:
        raise ValueError("timeout must be a positive number of seconds.")

    if timeout_value <= 0:
        raise ValueError("timeout must be greater than zero.")

    return timeout_value


class _ActionExecutionContext:
    """Helper for hardware demos that adds logging and timeout handling."""

    def __init__(self, timeout: float):
        self.started = time.monotonic()
        self.deadline = self.started + float(timeout) if timeout else None
        self.timed_out = False
        self.events: List[Dict[str, Any]] = []

    def elapsed(self) -> float:
        return time.monotonic() - self.started

    def remaining(self) -> Optional[float]:
        if self.deadline is None:
            return None
        return max(0.0, self.deadline - time.monotonic())

    def log(self, message: str, **extra: Any) -> None:
        entry: Dict[str, Any] = {"time": round(self.elapsed(), 3), "message": message}
        if extra:
            entry.update(extra)
        self.events.append(entry)

    def sleep(self, seconds: float) -> bool:
        """Sleep while respecting the configured timeout."""

        if seconds <= 0:
            return True

        if self.deadline is None:
            time.sleep(seconds)
            return True

        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            self.timed_out = True
            return False

        actual_sleep = min(seconds, remaining)
        time.sleep(actual_sleep)
        if actual_sleep + 1e-9 < seconds:
            self.timed_out = True
            return False

        return True


_LED_PINS = {"led1": 2, "led2": 3, "led3": 16}


def _capture_camera_photo(parameters: Any) -> Dict[str, Any]:
    """Capture a still photo using Picamera2 with the same defaults as camera_test.py."""

    try:
        from picamera2 import Picamera2
    except ImportError as exc:
        raise RuntimeError(
            "picamera2 is required to capture photos on the Raspberry Pi. Install it before running this action."
        ) from exc

    save_dir = CAMERA_SAVE_DIR
    warmup = CAMERA_WARMUP_SECONDS
    filename = f"rpi_{datetime.now():%Y%m%d_%H%M%S}.jpg"

    if isinstance(parameters, dict):
        dir_value = parameters.get("directory") or parameters.get("dir")
        if isinstance(dir_value, str) and dir_value.strip():
            save_dir = Path(dir_value).expanduser()

        fname_value = parameters.get("filename")
        if isinstance(fname_value, str) and fname_value.strip():
            filename = fname_value.strip()

        warmup_value = parameters.get("warmup")
        if isinstance(warmup_value, (int, float)):
            warmup = float(warmup_value)
        elif isinstance(warmup_value, str) and warmup_value.strip():
            try:
                warmup = float(warmup_value)
            except ValueError as exc:  # pragma: no cover - defensive
                raise ValueError("warmup must be a number of seconds.") from exc

    if warmup < 0:
        raise ValueError("warmup must be zero or a positive number of seconds.")

    save_dir.mkdir(parents=True, exist_ok=True)
    outfile = (save_dir / filename).with_suffix(".jpg")

    picam2 = Picamera2()
    picam2.configure(picam2.create_still_configuration())

    started = time.monotonic()
    picam2.start()
    if warmup:
        time.sleep(warmup)
    picam2.capture_file(str(outfile))
    picam2.stop()
    duration = time.monotonic() - started
    with outfile.open("rb") as image_file:
        image_bytes = image_file.read()
    image_base64 = base64.b64encode(image_bytes).decode("ascii")

    return {
        "saved_path": str(outfile),
        "directory": str(save_dir),
        "filename": outfile.name,
        "warmup_seconds": warmup,
        "duration_seconds": round(duration, 3),
        "image_base64": image_base64,
        "image_mime_type": "image/jpeg",
        "file_size_bytes": len(image_bytes),
    }


def _run_led_demo(parameters: Any) -> Dict[str, Any]:
    """Run the three-LED chase/blink routine using gpiozero, matching led_test.py wiring."""

    timeout = _parse_timeout_parameter(parameters, _DEFAULT_LED_TIMEOUT)
    context = _ActionExecutionContext(timeout)

    try:
        from gpiozero import LED
    except ImportError as exc:
        raise RuntimeError(
            "gpiozero is required to drive the LEDs. Install it on the Raspberry Pi."
        ) from exc

    cycles = 2
    if isinstance(parameters, dict) and "cycles" in parameters:
        try:
            cycles_value = int(parameters["cycles"])
        except (TypeError, ValueError) as exc:
            raise ValueError("cycles must be an integer (0 for continuous).") from exc
        if cycles_value < 0:
            raise ValueError("cycles must be zero or a positive integer.")
        cycles = cycles_value

    led1 = LED(_LED_PINS["led1"])
    led2 = LED(_LED_PINS["led2"])
    led3 = LED(_LED_PINS["led3"])

    def chase(delay: float = 0.15) -> bool:
        led1.off()
        led2.off()
        led3.off()
        if not context.sleep(delay):
            return False
        led1.on()
        if not context.sleep(delay):
            return False
        led1.off()
        led2.on()
        if not context.sleep(delay):
            return False
        led2.off()
        led3.on()
        if not context.sleep(delay):
            return False
        led3.off()
        return not context.timed_out

    def all_on(delay: float = 0.4) -> bool:
        led1.on()
        led2.on()
        led3.on()
        if not context.sleep(delay):
            return False
        led1.off()
        led2.off()
        led3.off()
        return not context.timed_out and context.sleep(delay)

    def blink_all(delay: float = 0.07, times: int = 8) -> bool:
        for _ in range(times):
            led1.on()
            led2.on()
            led3.on()
            if not context.sleep(delay):
                return False
            led1.off()
            led2.off()
            led3.off()
            if not context.sleep(delay):
                return False
        return True

    executed_cycles = 0
    context.log(
        "LED demo start",
        pins=_LED_PINS,
        cycles=cycles if cycles > 0 else "until timeout",
        timeout_seconds=timeout,
    )

    try:
        while not context.timed_out:
            if cycles > 0 and executed_cycles >= cycles:
                break

            for _ in range(4):
                if not chase():
                    break
            if context.timed_out:
                break

            for _ in range(3):
                if not all_on():
                    break
            if context.timed_out:
                break

            if not blink_all():
                break

            executed_cycles += 1
    finally:
        led1.off()
        led2.off()
        led3.off()
        led1.close()
        led2.close()
        led3.close()
        context.log("LED demo finished", cycles_executed=executed_cycles, timed_out=context.timed_out)

    return {
        "events": context.events,
        "timed_out": context.timed_out,
        "duration_seconds": context.elapsed(),
        "cycles_executed": executed_cycles,
        "pins": _LED_PINS,
    }


def _run_motor_test(parameters: Any) -> Dict[str, Any]:
    timeout = _parse_timeout_parameter(parameters, _DEFAULT_MOTOR_TEST_TIMEOUT)
    context = _ActionExecutionContext(timeout)

    try:
        from gpiozero import OutputDevice
    except ImportError as exc:
        raise RuntimeError(
            "gpiozero is required to control the motors. Install it on the Raspberry Pi."
        ) from exc

    motor1 = {"EN": 25, "IN1": 24, "IN2": 23}
    motor2 = {"EN": 17, "IN1": 27, "IN2": 22}

    context.log("Initializing L293D motor outputs", motor1=motor1, motor2=motor2)

    en1 = OutputDevice(motor1["EN"], active_high=True, initial_value=True)
    en2 = OutputDevice(motor2["EN"], active_high=True, initial_value=True)
    in1_1 = OutputDevice(motor1["IN1"])
    in1_2 = OutputDevice(motor1["IN2"])
    in2_1 = OutputDevice(motor2["IN1"])
    in2_2 = OutputDevice(motor2["IN2"])

    def forward() -> None:
        in1_1.on()
        in1_2.off()
        in2_1.on()
        in2_2.off()

    def backward() -> None:
        in1_1.off()
        in1_2.on()
        in2_1.off()
        in2_2.on()

    def coast() -> None:
        en1.off()
        en2.off()
        context.log("Coasting motors (EN pins low)")
        context.sleep(2.0)
        en1.on()
        en2.on()

    try:
        context.log("FORWARD for 5 seconds")
        forward()
        if not context.sleep(5.0):
            context.log("Timeout reached while running forward motion")
            return {
                "events": context.events,
                "timed_out": True,
                "duration_seconds": context.elapsed(),
                "motor_pins": {"motor1": motor1, "motor2": motor2},
            }

        context.log("COAST for 2 seconds")
        coast()
        if context.timed_out:
            context.log("Timeout reached during coasting phase")
            return {
                "events": context.events,
                "timed_out": True,
                "duration_seconds": context.elapsed(),
                "motor_pins": {"motor1": motor1, "motor2": motor2},
            }

        context.log("BACKWARD for 5 seconds")
        backward()
        if not context.sleep(5.0):
            context.log("Timeout reached while running backward motion")
            return {
                "events": context.events,
                "timed_out": True,
                "duration_seconds": context.elapsed(),
                "motor_pins": {"motor1": motor1, "motor2": motor2},
            }

        context.log("Motor diagnostic completed successfully")
        return {
            "events": context.events,
            "timed_out": False,
            "duration_seconds": context.elapsed(),
            "motor_pins": {"motor1": motor1, "motor2": motor2},
        }
    finally:
        en1.off()
        en2.off()
        in1_1.off()
        in1_2.off()
        in2_1.off()
        in2_2.off()


def _run_oled_robot_demo(parameters: Any) -> Dict[str, Any]:
    timeout = _parse_timeout_parameter(parameters, _DEFAULT_OLED_DEMO_TIMEOUT)
    context = _ActionExecutionContext(timeout)
    max_run_seconds = min(timeout, _OLED_RESULT_RETURN_SECONDS)
    text_override: Optional[str] = None

    if isinstance(parameters, dict):
        candidate_text = parameters.get("text") or parameters.get("message")
        if isinstance(candidate_text, list):
            candidate_text = " ".join(str(item) for item in candidate_text if item is not None)
        if isinstance(candidate_text, str) and candidate_text.strip():
            text_override = " ".join(candidate_text.split())

    try:
        import math
        import os
        from gpiozero import PWMLED
        from PIL import ImageDraw, ImageFont
        from luma.core.interface.serial import spi
        from luma.core.render import canvas
        from luma.lcd.device import st7735
    except ImportError as exc:
        raise RuntimeError(
            "The OLED demo requires gpiozero, Pillow, and luma.lcd to be installed on the Raspberry Pi."
        ) from exc

    SPI_PORT = 1
    SPI_DEVICE = 0
    PIN_DC = 26
    PIN_RST = 6
    PIN_BL = 13
    BUS_HZ = 16_000_000

    WIDTH, HEIGHT = 160, 128
    ROTATE = 0
    BGR = False
    H_OFF, V_OFF = 0, 0

    COL_BG = (6, 18, 10)
    COL_HEAD = (20, 44, 26)
    COL_VISOR = (10, 12, 16)
    COL_FRAME = (90, 150, 96)
    COL_TRACK = (24, 36, 30)
    COL_GLOW_SOFT = (82, 20, 44)
    COL_GLOW = (200, 54, 104)
    COL_GLOW_CORE = (255, 148, 196)
    COL_BEAM = (120, 210, 170)
    COL_TEXT = (225, 240, 228)
    COL_TEXT_BG = (12, 26, 16)
    COL_TEXT_BORDER = (60, 120, 74)
    COL_ACCENT = (36, 76, 46)

    FPS = 50
    TEXT_PANEL_HEIGHT = 34
    EYE_SWEEP = 0.48
    TRACK_PADDING = 18

    def ensure_spidev() -> None:
        path = f"/dev/spidev{SPI_PORT}.{SPI_DEVICE}"
        if not os.path.exists(path):
            raise FileNotFoundError(
                f"{path} が見つかりません。/boot(または /boot/firmware)/config.txt に 'dtoverlay=spi1-1cs' を追記して再起動してください。"
            )

    def setup_backlight() -> PWMLED:
        return PWMLED(PIN_BL, frequency=1000, active_high=True, initial_value=1.0)

    def set_backlight_percent(bl: PWMLED, percent: float) -> None:
        value = max(0.0, min(100.0, percent)) / 100.0
        bl.value = value

    def init_device() -> st7735:
        serial_if = spi(
            port=SPI_PORT,
            device=SPI_DEVICE,
            gpio_DC=PIN_DC,
            gpio_RST=PIN_RST,
            bus_speed_hz=BUS_HZ,
        )
        return st7735(
            serial_interface=serial_if,
            width=WIDTH,
            height=HEIGHT,
            rotate=ROTATE,
            bgr=BGR,
            h_offset=H_OFF,
            v_offset=V_OFF,
        )

    font = ImageFont.load_default()

    def _text_width(text: str) -> int:
        if hasattr(font, "getbbox"):
            bbox = font.getbbox(text)
            return bbox[2] - bbox[0]
        return font.getsize(text)[0]

    def wrap_text_lines(text: str, max_width: int) -> List[str]:
        if not text:
            return []
        lines: List[str] = []
        current = ""
        words = text.split()
        for word in words:
            candidate = word if not current else f"{current} {word}"
            if _text_width(candidate) <= max_width:
                current = candidate
                continue
            if current:
                lines.append(current)
            chunk = ""
            for char in word:
                candidate_chunk = f"{chunk}{char}"
                if _text_width(candidate_chunk) <= max_width:
                    chunk = candidate_chunk
                else:
                    if chunk:
                        lines.append(chunk)
                    chunk = char
            current = chunk
        if current:
            lines.append(current)
        return lines[:3]

    text_lines = wrap_text_lines(text_override or "", WIDTH - 18)
    show_text_only = bool(text_lines)
    panel_height = TEXT_PANEL_HEIGHT if show_text_only else 0

    def draw_text_screen(draw: ImageDraw.ImageDraw, lines: List[str], W: int, H: int) -> None:
        draw.rectangle((0, 0, W, H), fill=COL_TEXT_BG, outline=COL_TEXT_BORDER, width=1)
        if hasattr(font, "getbbox"):
            line_height = (font.getbbox("Ag")[3] - font.getbbox("Ag")[1]) + 2
        else:
            line_height = font.getsize("Ag")[1] + 2
        total_height = len(lines) * line_height
        y = max(8, (H - total_height) // 2)
        for line in lines:
            x = max(8, (W - _text_width(line)) // 2)
            draw.text((x, y), line, fill=COL_TEXT, font=font)
            y += line_height

    def draw_mono_eye(draw: ImageDraw.ImageDraw, t: float, W: int, H: int) -> None:
        head_bottom = H - panel_height
        draw.rectangle((0, 0, W, H), fill=COL_BG)
        draw.rectangle((0, 0, W, head_bottom), fill=COL_HEAD)

        visor_left = 10
        visor_right = W - 10
        visor_top = 18
        visor_bottom = head_bottom - 8
        draw.rectangle((visor_left, visor_top, visor_right, visor_bottom), outline=COL_FRAME, fill=COL_VISOR, width=2)

        ridge_height = 8
        ridge_y = visor_top - ridge_height
        draw.rectangle((visor_left + 6, ridge_y, visor_right - 6, visor_top + 2), fill=COL_HEAD, outline=COL_ACCENT, width=1)

        track_left = visor_left + TRACK_PADDING
        track_right = visor_right - TRACK_PADDING
        track_mid_y = (visor_top + visor_bottom) // 2
        track_height = 20
        track_top = track_mid_y - track_height // 2
        track_bottom = track_mid_y + track_height // 2
        draw.rectangle((track_left, track_top, track_right, track_bottom), fill=COL_TRACK, outline=COL_FRAME, width=1)

        beam_phase = (t * 60) % (track_right - track_left)
        beam_x = track_left + beam_phase
        if beam_x < track_right:
            draw.line((beam_x, visor_top + 4, beam_x, visor_bottom - 4), fill=COL_BEAM, width=2)

        eye_band = ((math.sin(t * 1.2) * EYE_SWEEP) + (math.sin(t * 0.37) * 0.22)) * 0.5 + 0.5
        eye_x = int(track_left + 8 + eye_band * max(4, (track_right - track_left - 16)))
        eye_y = track_mid_y + int(math.sin(t * 0.7) * 3)
        glow_r = 16
        eye_r = 10
        core_r = 6

        draw.ellipse((eye_x - glow_r, eye_y - glow_r, eye_x + glow_r, eye_y + glow_r), fill=COL_GLOW_SOFT)
        draw.ellipse((eye_x - eye_r, eye_y - eye_r, eye_x + eye_r, eye_y + eye_r), fill=COL_GLOW, outline=COL_FRAME, width=1)
        draw.ellipse((eye_x - core_r, eye_y - core_r, eye_x + core_r, eye_y + core_r), fill=COL_GLOW_CORE)
        draw.line((eye_x - eye_r - 6, eye_y - core_r // 2, eye_x + eye_r + 6, eye_y + core_r // 2), fill=COL_GLOW_CORE, width=1)

        meter_height = 20
        meter_width = 6
        meter_x = visor_left + 6
        meter_y = visor_bottom - meter_height - 6
        meter_value = int((math.sin(t * 1.5) * 0.5 + 0.5) * (meter_height - 4))
        draw.rectangle((meter_x, meter_y, meter_x + meter_width, meter_y + meter_height), outline=COL_FRAME, width=1)
        draw.rectangle((meter_x + 1, meter_y + meter_height - meter_value, meter_x + meter_width - 1, meter_y + meter_height - 2), fill=COL_ACCENT)

        right_meter_x = visor_right - meter_width - 6
        draw.rectangle((right_meter_x, meter_y, right_meter_x + meter_width, meter_y + meter_height), outline=COL_FRAME, width=1)
        draw.rectangle(
            (right_meter_x + 1, meter_y + meter_height - meter_value, right_meter_x + meter_width - 1, meter_y + meter_height - 2),
            fill=COL_ACCENT,
        )


    ensure_spidev()
    context.log("SPI device is available", device=f"/dev/spidev{SPI_PORT}.{SPI_DEVICE}")
    if text_lines:
        context.log("OLED text prepared", lines=text_lines)

    backlight: Optional[PWMLED] = None
    frames = 0

    try:
        backlight = setup_backlight()
        context.log("Backlight PWM initialized", pin=PIN_BL)
        device = init_device()
        context.log("ST7735 display initialized", resolution=f"{WIDTH}x{HEIGHT}")

        t0 = time.time()
        frame_delay = 1.0 / FPS

        for duty in (20, 40, 60, 80, 100):
            if backlight is not None:
                set_backlight_percent(backlight, duty)
            with canvas(device) as draw:
                if show_text_only:
                    draw_text_screen(draw, text_lines or ["Showing message..."], device.width, device.height)
                else:
                    draw_mono_eye(draw, 0.0, device.width, device.height)
            context.log("Backlight fade-in step", percent=duty, text_mode=show_text_only)
            if not context.sleep(0.05):
                break

        while not context.timed_out:
            remaining = context.remaining()
            if remaining is not None and remaining <= 0:
                context.timed_out = True
                break

            t = time.time() - t0
            with canvas(device) as draw:
                if show_text_only:
                    draw_text_screen(draw, text_lines, device.width, device.height)
                else:
                    draw_mono_eye(draw, t, device.width, device.height)
            frames += 1

            if not context.sleep(frame_delay):
                break

            if context.elapsed() >= max_run_seconds:
                context.log(
                    "Reached OLED result return window; leaving display as-is",
                    elapsed_seconds=round(context.elapsed(), 3),
                    max_run_seconds=max_run_seconds,
                )
                break

        if context.timed_out:
            context.log("OLED demo stopped because the timeout was reached")
        else:
            context.log("OLED demo completed", frames=frames)

        return {
            "events": context.events,
            "timed_out": context.timed_out,
            "duration_seconds": context.elapsed(),
            "frames_rendered": frames,
            "target_fps": FPS,
            "display_left_on": True,
            "max_run_seconds": max_run_seconds,
            "text_lines": text_lines,
        }
    finally:
        if backlight is not None:
            context.log("Leaving OLED backlight on after demo")


def _extract_servo_arguments(parameters: Any) -> Tuple[List[str], bool]:
    """Convert structured parameters into CLI arguments for servo_tesr.py."""

    if not isinstance(parameters, dict):
        return [], False

    command_value = parameters.get("command")
    if isinstance(command_value, str) and command_value.strip():
        args = shlex.split(command_value.strip())
        return args, bool(args)

    if isinstance(command_value, list) and command_value:
        args = [str(item) for item in command_value]
        return args, bool(args)

    args: List[str] = []
    mode_value = parameters.get("mode")
    if isinstance(mode_value, str) and mode_value.strip():
        args.append(mode_value.strip())

    has_command = bool(args)

    if has_command:
        # Boolean flag support
        if parameters.get("pigpio"):
            args.append("--pigpio")

        option_fields = {
            "channel": "--channel",
            "angle": "--angle",
            "hold": "--hold",
            "start": "--start",
            "end": "--end",
            "step": "--step",
            "delay": "--delay",
            "cycles": "--cycles",
            "min_pw": "--min-pw",
            "max_pw": "--max-pw",
        }

        for key, flag in option_fields.items():
            if key in parameters and parameters[key] is not None:
                args.append(flag)
                args.append(str(parameters[key]))

    return args, has_command


_SERVO_USED_BCM: Set[int] = {17, 22, 23, 24, 25, 27, 20, 21, 18, 26, 6, 13}

_SERVO_DEFAULT_CHANNEL_PINS: Dict[int, int] = {
    1: 12,
    2: 19,
    3: 5,
    4: 4,
}


def _servo_validate_default_pins() -> None:
    for channel, pin in _SERVO_DEFAULT_CHANNEL_PINS.items():
        if pin in _SERVO_USED_BCM:
            raise RuntimeError(
                f"デフォルト割当のCH{channel} -> GPIO{pin} が '使用済み' リストと衝突しています。別の空きGPIOに変更してください。"
            )


def _servo_resolve_pin(channel: int) -> int:
    bcm = _SERVO_DEFAULT_CHANNEL_PINS.get(channel)
    if bcm is None:
        raise ValueError(f"不正なチャンネル番号: {channel}")
    return bcm


def _servo_create_servo(
    *,
    bcm_pin: int,
    use_pigpio: bool,
    min_pulse_width: float,
    max_pulse_width: float,
) -> "AngularServo":
    try:
        from gpiozero import AngularServo, Device
    except ImportError as exc:
        raise RuntimeError(
            "gpiozero is required for servo control. Install it on the Raspberry Pi."
        ) from exc

    if bcm_pin in _SERVO_USED_BCM:
        raise ValueError(
            f"指定されたGPIO{bcm_pin}は '使用済み' リストに含まれています。別のGPIOを指定してください。"
        )

    if use_pigpio:
        try:
            from gpiozero.pins.pigpio import PiGPIOFactory
        except Exception as exc:
            raise RuntimeError(
                "pigpio のピンファクトリが利用できません。'python3 -m pip install pigpio' および 'sudo apt-get install pigpio' 後、'sudo systemctl start pigpiod' を実行してください。"
            ) from exc
        Device.pin_factory = PiGPIOFactory()

    return AngularServo(
        bcm_pin,
        min_angle=0.0,
        max_angle=180.0,
        min_pulse_width=min_pulse_width,
        max_pulse_width=max_pulse_width,
        frame_width=0.02,
    )


def _servo_log_wiring(context: _ActionExecutionContext) -> None:
    context.log("=== サーボ配線（色とGPIO/物理ピン） ===")
    context.log(" ⚫ GND（黒/茶） : Raspberry Pi の GND（物理 6/9/14/20/25/30/34/39）")
    context.log(" 🔴 +5V（赤）   : 外部5V推奨（Piの 2/4 でも小型1個なら動作例あり）")
    context.log(
        " 🟠 信号（橙/黄/白）: CH1->GPIO12(物理32), CH2->GPIO19(物理35), CH3->GPIO5(物理29), CH4->GPIO4(物理7)"
    )
    context.log(" ※ 使用済GPIO: 17, 22, 23, 24, 25, 27, 20, 21, 18, 26, 6, 13 は回避済み。")


def _servo_cmd_set(args: argparse.Namespace, context: _ActionExecutionContext) -> None:
    bcm = _servo_resolve_pin(args.channel)
    servo = _servo_create_servo(
        bcm_pin=bcm,
        use_pigpio=args.pigpio,
        min_pulse_width=args.min_pw,
        max_pulse_width=args.max_pw,
    )
    try:
        angle = float(args.angle)
        if not (0.0 <= angle <= 180.0):
            raise ValueError("角度は0〜180の範囲で指定してください。")
        servo.angle = angle
        context.log("[SET] サーボ角度を設定", channel=args.channel, gpio=bcm, angle=round(angle, 1))
        if args.hold > 0 and not context.sleep(args.hold):
            context.log("タイムアウトのため保持を終了")
    finally:
        servo.close()


def _servo_cmd_center(args: argparse.Namespace, context: _ActionExecutionContext) -> None:
    bcm = _servo_resolve_pin(args.channel)
    servo = _servo_create_servo(
        bcm_pin=bcm,
        use_pigpio=args.pigpio,
        min_pulse_width=args.min_pw,
        max_pulse_width=args.max_pw,
    )
    try:
        servo.angle = 90.0
        context.log("[CENTER] サーボをセンターへ移動", channel=args.channel, gpio=bcm)
        if args.hold > 0 and not context.sleep(args.hold):
            context.log("タイムアウトのため保持を終了")
    finally:
        servo.close()


def _servo_cmd_off(args: argparse.Namespace, context: _ActionExecutionContext) -> None:
    bcm = _servo_resolve_pin(args.channel)
    servo = _servo_create_servo(
        bcm_pin=bcm,
        use_pigpio=args.pigpio,
        min_pulse_width=args.min_pw,
        max_pulse_width=args.max_pw,
    )
    try:
        servo.value = None
        context.log("[OFF] PWM停止（デタッチ）", channel=args.channel, gpio=bcm)
        if args.hold > 0 and not context.sleep(args.hold):
            context.log("タイムアウトのため保持を終了")
    finally:
        servo.close()


def _servo_cmd_sweep(args: argparse.Namespace, context: _ActionExecutionContext) -> None:
    start = float(args.start)
    end = float(args.end)
    step = float(args.step)
    delay = float(args.delay)

    if step <= 0:
        raise ValueError("step は正の値にしてください。")
    if not (0.0 <= start <= 180.0 and 0.0 <= end <= 180.0):
        raise ValueError("start / end は 0〜180 の範囲で指定してください。")

    bcm = _servo_resolve_pin(args.channel)
    servo = _servo_create_servo(
        bcm_pin=bcm,
        use_pigpio=args.pigpio,
        min_pulse_width=args.min_pw,
        max_pulse_width=args.max_pw,
    )
    try:
        cycles = int(args.cycles)
        context.log(
            "[SWEEP] サーボスイープを開始",
            channel=args.channel,
            gpio=bcm,
            start=start,
            end=end,
            step=step,
            delay=delay,
            cycles=cycles,
        )
        count = 0
        while True:
            a = start
            while a <= end + 1e-6:
                servo.angle = a
                context.log(" angle更新", angle=round(a, 1))
                if not context.sleep(delay):
                    context.log("タイムアウトのためスイープを終了")
                    return
                a += step

            a = end
            while a >= start - 1e-6:
                servo.angle = a
                context.log(" angle更新", angle=round(a, 1))
                if not context.sleep(delay):
                    context.log("タイムアウトのためスイープを終了")
                    return
                a -= step

            if cycles > 0:
                count += 1
                if count >= cycles:
                    break
    finally:
        servo.close()


def _servo_cmd_info(_: argparse.Namespace, context: _ActionExecutionContext) -> None:
    _servo_log_wiring(context)


def _servo_autorun_demo(context: _ActionExecutionContext) -> None:
    _servo_log_wiring(context)

    channel = 1
    bcm = _SERVO_DEFAULT_CHANNEL_PINS[channel]
    use_pigpio = False
    min_pw = 0.0005
    max_pw = 0.0025
    center_angle = 90.0
    sweep_start = 60.0
    sweep_end = 120.0
    sweep_step = 2.0
    sweep_delay = 0.02
    sweep_cycles = 2

    servo = _servo_create_servo(
        bcm_pin=bcm,
        use_pigpio=use_pigpio,
        min_pulse_width=min_pw,
        max_pulse_width=max_pw,
    )
    try:
        servo.angle = center_angle
        context.log(
            "[DEMO] サーボをセンターへ移動",
            channel=channel,
            gpio=bcm,
            angle=center_angle,
        )
        if not context.sleep(0.5):
            context.log("タイムアウトのためデモを終了")
            return

        context.log(
            "[DEMO] スイープ開始",
            start=sweep_start,
            end=sweep_end,
            step=sweep_step,
            delay=sweep_delay,
            cycles=sweep_cycles,
        )
        count = 0
        while True:
            a = sweep_start
            while a <= sweep_end + 1e-6:
                servo.angle = a
                context.log(" angle更新", angle=round(a, 1))
                if not context.sleep(sweep_delay):
                    context.log("タイムアウトのためデモを終了")
                    return
                a += sweep_step

            a = sweep_end
            while a >= sweep_start - 1e-6:
                servo.angle = a
                context.log(" angle更新", angle=round(a, 1))
                if not context.sleep(sweep_delay):
                    context.log("タイムアウトのためデモを終了")
                    return
                a -= sweep_step

            count += 1
            if count >= sweep_cycles:
                break

        servo.value = None
        context.log("[DEMO] PWM停止（保持解除）")
    finally:
        servo.close()


def _servo_build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Raspberry Pi 4 サーボ制御（gpiozero/AngularServo）。角度は0〜180度で指定。無引数時は非対話デモを自動実行。",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
        add_help=False,
        exit_on_error=False,
    )

    sub = parser.add_subparsers(dest="cmd", required=False)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--channel", type=int, default=1)
    common.add_argument("--pigpio", action="store_true")
    common.add_argument("--min-pw", dest="min_pw", type=float, default=0.0005)
    common.add_argument("--max-pw", dest="max_pw", type=float, default=0.0025)
    common.add_argument("--hold", type=float, default=0.0)

    sp_set = sub.add_parser("set", parents=[common], add_help=False)
    sp_set.add_argument("--angle", type=float, required=True)
    sp_set.set_defaults(_handler=_servo_cmd_set)

    sp_center = sub.add_parser("center", parents=[common], add_help=False)
    sp_center.set_defaults(_handler=_servo_cmd_center)

    sp_off = sub.add_parser("off", parents=[common], add_help=False)
    sp_off.set_defaults(_handler=_servo_cmd_off)

    sp_sweep = sub.add_parser("sweep", parents=[common], add_help=False)
    sp_sweep.add_argument("--start", type=float, default=0.0)
    sp_sweep.add_argument("--end", type=float, default=180.0)
    sp_sweep.add_argument("--step", type=float, default=1.0)
    sp_sweep.add_argument("--delay", type=float, default=0.01)
    sp_sweep.add_argument("--cycles", type=int, default=1)
    sp_sweep.set_defaults(_handler=_servo_cmd_sweep)

    sp_info = sub.add_parser("info", add_help=False)
    sp_info.set_defaults(_handler=_servo_cmd_info)

    return parser


def _run_servo_demo(parameters: Any) -> Dict[str, Any]:
    args, has_command = _extract_servo_arguments(parameters)
    timeout = _parse_timeout_parameter(parameters, _DEFAULT_SERVO_TIMEOUT)
    context = _ActionExecutionContext(timeout)

    _servo_validate_default_pins()
    parser = _servo_build_parser()

    argv = args if has_command else []
    try:
        parsed = parser.parse_args(argv)
    except Exception as exc:
        raise ValueError(f"Invalid servo command arguments: {exc}") from exc

    handler = getattr(parsed, "_handler", None)

    try:
        if handler is None:
            _servo_autorun_demo(context)
        else:
            handler(parsed, context)
    finally:
        if context.timed_out:
            context.log("Servo操作は指定されたタイムアウトで終了しました。")

    command_name = parsed.cmd if getattr(parsed, "cmd", None) else "demo"
    return {
        "events": context.events,
        "timed_out": context.timed_out,
        "duration_seconds": context.elapsed(),
        "command": command_name,
        "arguments": argv,
    }


_DUAL_SERVO_PINS = {"servo1": 12, "servo2": 19}
_DUAL_SERVO_MIN_ANGLE = 0.0
_DUAL_SERVO_MAX_ANGLE = 180.0
_DUAL_SERVO_MIN_PW = 0.0005
_DUAL_SERVO_MAX_PW = 0.0025


def _run_dual_servo_demo(parameters: Any) -> Dict[str, Any]:
    """Dual-servo operations mirroring two_servo_test.py (demo/set/off/info)."""

    timeout = _parse_timeout_parameter(parameters, _DEFAULT_DUAL_SERVO_TIMEOUT)
    context = _ActionExecutionContext(timeout)

    command = "demo"
    cycles = 3
    step = 3.0
    delay = 0.02
    use_pigpio = False
    hold = 0.0
    angle1: Optional[float] = None
    angle2: Optional[float] = None

    if isinstance(parameters, dict):
        if "command" in parameters and parameters["command"] is not None:
            command = str(parameters["command"]).strip().lower()

        if "cycles" in parameters:
            try:
                cycles_value = int(parameters["cycles"])
            except (TypeError, ValueError) as exc:
                raise ValueError("cycles must be an integer.") from exc
            if cycles_value < 0:
                raise ValueError("cycles must be zero or a positive integer.")
            cycles = cycles_value

        if "step" in parameters and parameters["step"] is not None:
            try:
                step = float(parameters["step"])
            except (TypeError, ValueError) as exc:
                raise ValueError("step must be a number.") from exc
            if step <= 0:
                raise ValueError("step must be greater than zero.")

        if "delay" in parameters and parameters["delay"] is not None:
            try:
                delay = float(parameters["delay"])
            except (TypeError, ValueError) as exc:
                raise ValueError("delay must be a number.") from exc
            if delay <= 0:
                raise ValueError("delay must be greater than zero.")

        if "hold" in parameters and parameters["hold"] is not None:
            try:
                hold = float(parameters["hold"])
            except (TypeError, ValueError) as exc:
                raise ValueError("hold must be a number.") from exc
            if hold < 0:
                raise ValueError("hold must be zero or a positive value.")

        if "angle1" in parameters and parameters["angle1"] is not None:
            try:
                angle1 = float(parameters["angle1"])
            except (TypeError, ValueError) as exc:
                raise ValueError("angle1 must be a number.") from exc

        if "angle2" in parameters and parameters["angle2"] is not None:
            try:
                angle2 = float(parameters["angle2"])
            except (TypeError, ValueError) as exc:
                raise ValueError("angle2 must be a number.") from exc

        use_pigpio = bool(parameters.get("pigpio"))

    command = command or "demo"
    if command not in {"demo", "set", "off", "info"}:
        raise ValueError("command must be one of: demo, set, off, info.")

    def _validate_angle(label: str, value: float) -> float:
        if not (_DUAL_SERVO_MIN_ANGLE <= value <= _DUAL_SERVO_MAX_ANGLE):
            raise ValueError(
                f"{label} must be between {_DUAL_SERVO_MIN_ANGLE} and {_DUAL_SERVO_MAX_ANGLE} degrees."
            )
        return value

    if command == "set":
        if angle1 is None or angle2 is None:
            raise ValueError("angle1 and angle2 are required when command is 'set'.")
        angle1 = _validate_angle("angle1", angle1)
        angle2 = _validate_angle("angle2", angle2)

    if command == "info":
        context.log(
            "Dual-servo wiring info",
            pins=_DUAL_SERVO_PINS,
            angle_range=(_DUAL_SERVO_MIN_ANGLE, _DUAL_SERVO_MAX_ANGLE),
        )
        return {
            "events": context.events,
            "timed_out": context.timed_out,
            "duration_seconds": context.elapsed(),
            "command": command,
            "pins": _DUAL_SERVO_PINS,
            "angle_range": (_DUAL_SERVO_MIN_ANGLE, _DUAL_SERVO_MAX_ANGLE),
        }

    try:
        from gpiozero import AngularServo, Device
    except ImportError as exc:
        raise RuntimeError(
            "gpiozero is required for the dual-servo demo. Install it on the Raspberry Pi."
        ) from exc

    try:
        from gpiozero.pins.pigpio import PiGPIOFactory  # type: ignore
    except Exception:
        PiGPIOFactory = None  # type: ignore

    if use_pigpio:
        if PiGPIOFactory is None or Device is None:
            raise RuntimeError(
                "pigpio pin factory is unavailable. Install pigpio and start pigpiod."
            )
        Device.pin_factory = PiGPIOFactory()

    servo1 = AngularServo(
        _DUAL_SERVO_PINS["servo1"],
        min_pulse_width=_DUAL_SERVO_MIN_PW,
        max_pulse_width=_DUAL_SERVO_MAX_PW,
        min_angle=_DUAL_SERVO_MIN_ANGLE,
        max_angle=_DUAL_SERVO_MAX_ANGLE,
        frame_width=0.02,
    )
    servo2 = AngularServo(
        _DUAL_SERVO_PINS["servo2"],
        min_pulse_width=_DUAL_SERVO_MIN_PW,
        max_pulse_width=_DUAL_SERVO_MAX_PW,
        min_angle=_DUAL_SERVO_MIN_ANGLE,
        max_angle=_DUAL_SERVO_MAX_ANGLE,
        frame_width=0.02,
    )

    context.log(
        "Dual-servo routine start",
        pins=_DUAL_SERVO_PINS,
        command=command,
        cycles=cycles if cycles > 0 else "until timeout",
        step=step,
        delay_seconds=delay,
        pigpio=use_pigpio,
    )

    executed_cycles = 0
    try:
        servo1.angle = 90.0
        servo2.angle = 90.0
        context.log("Servos centered", angle1=90.0, angle2=90.0)
        if command == "demo" and (context.timed_out or not context.sleep(0.5)):
            context.log("Timeout reached before starting sweep")
            return {
                "events": context.events,
                "timed_out": context.timed_out,
                "duration_seconds": context.elapsed(),
                "cycles_executed": executed_cycles,
                "pins": _DUAL_SERVO_PINS,
                "step": step,
                "delay": delay,
                "pigpio": use_pigpio,
                "command": command,
            }

        if command == "set":
            servo1.angle = angle1  # type: ignore[arg-type]
            servo2.angle = angle2  # type: ignore[arg-type]
            context.log("Angles set", angle1=angle1, angle2=angle2)
            if hold > 0 and not context.sleep(hold):
                context.log("Timed out while holding angles")

        elif command == "off":
            servo1.value = None
            servo2.value = None
            context.log("PWM stopped for both servos")
            if hold > 0 and not context.sleep(hold):
                context.log("Timed out while holding off state")

        else:  # demo
            while not context.timed_out:
                if cycles > 0 and executed_cycles >= cycles:
                    break

                angle = 0.0
                while angle <= _DUAL_SERVO_MAX_ANGLE + 1e-9:
                    servo1.angle = angle
                    servo2.angle = _DUAL_SERVO_MAX_ANGLE - angle
                    if int(angle) % 15 == 0 or angle in (
                        _DUAL_SERVO_MIN_ANGLE,
                        _DUAL_SERVO_MAX_ANGLE,
                    ):
                        context.log(
                            "Sweep up",
                            angle1=round(angle, 1),
                            angle2=round(_DUAL_SERVO_MAX_ANGLE - angle, 1),
                        )
                    if not context.sleep(delay):
                        break
                    angle += step

                angle = _DUAL_SERVO_MAX_ANGLE
                while angle >= _DUAL_SERVO_MIN_ANGLE - 1e-9 and not context.timed_out:
                    servo1.angle = angle
                    servo2.angle = _DUAL_SERVO_MAX_ANGLE - angle
                    if int(angle) % 15 == 0 or angle in (
                        _DUAL_SERVO_MIN_ANGLE,
                        _DUAL_SERVO_MAX_ANGLE,
                    ):
                        context.log(
                            "Sweep down",
                            angle1=round(angle, 1),
                            angle2=round(_DUAL_SERVO_MAX_ANGLE - angle, 1),
                        )
                    if not context.sleep(delay):
                        break
                    angle -= step

                if context.timed_out:
                    break

                executed_cycles += 1
    finally:
        servo1.value = None
        servo2.value = None
        servo1.close()
        servo2.close()
        context.log(
            "Dual-servo routine finished",
            cycles_executed=executed_cycles,
            timed_out=context.timed_out,
            command=command,
        )

    return {
        "events": context.events,
        "timed_out": context.timed_out,
        "duration_seconds": context.elapsed(),
        "cycles_executed": executed_cycles,
        "pins": _DUAL_SERVO_PINS,
        "step": step,
        "delay": delay,
        "pigpio": use_pigpio,
        "command": command,
        "angle1": angle1,
        "angle2": angle2,
        "hold": hold,
    }


def _execute_action(action: str, parameters: Dict[str, Any]) -> Tuple[bool, Any, Optional[str]]:
    # アクション名に応じてローカル処理を実行し、成功可否と結果を返す
    logging.info(
        "Executing action '%s' with parameters=%s",
        action,
        _format_for_log(parameters or {}),
    )
    try:
        if action == "play_rock_paper_scissors":
            return True, _play_rock_paper_scissors(parameters or {}), None
        if action == "get_current_time":
            now = datetime.now(timezone.utc).astimezone()
            return True, {"current_time": now.isoformat()}, None
        if action == "get_weather":
            return True, _get_weather(parameters or {}), None
        if action == "tell_joke":
            return True, _tell_joke(), None
        if action == "run_motor_test":
            return True, _run_motor_test(parameters or {}), None
        if action == "run_oled_robot_demo":
            return True, _run_oled_robot_demo(parameters or {}), None
        if action == "run_servo_demo":
            return True, _run_servo_demo(parameters or {}), None
        if action == "capture_camera_photo":
            return True, _capture_camera_photo(parameters or {}), None
        if action == "run_led_demo":
            return True, _run_led_demo(parameters or {}), None
        if action == "run_dual_servo_demo":
            return True, _run_dual_servo_demo(parameters or {}), None
        if action == "no_action":
            message = parameters.get("message") if isinstance(parameters, dict) else None
            return True, {"message": message or "No action executed."}, None

        return False, None, f"Unsupported action: {action}"
    except Exception as exc:
        logging.exception("Action '%s' raised an exception", action)
        return False, None, str(exc)


# ==== LLM interaction ======================================================


def _plan_from_instruction(llm: Llama, instruction: str) -> Dict[str, Any]:
    # LLM へ命令文を渡し、JSON 形式のプランを推定
    def _validate_plan(payload: Any) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        if not isinstance(payload, dict):
            return None, "response was not a JSON object."
        action = payload.get("action")
        if not isinstance(action, str) or not action.strip():
            return None, "action must be a non-empty string."
        action = action.strip()
        if action not in SUPPORTED_ACTIONS:
            return None, f"action '{action}' is not supported."
        parameters = payload.get("parameters")
        if parameters is None:
            parameters = {}
        if not isinstance(parameters, dict):
            return None, "parameters must be a JSON object."
        message = payload.get("message")
        if message is not None and not isinstance(message, str):
            return None, "message must be a string when provided."

        normalised: Dict[str, Any] = {"action": action, "parameters": parameters}
        if isinstance(message, str) and message.strip():
            normalised["message"] = message.strip()
        return normalised, None

    retry_instruction: Optional[str] = None
    plan: Dict[str, Any] = {}
    max_attempts = 3

    for attempt in range(1, max_attempts + 1):
        messages: List[Dict[str, str]] = [
            {"role": "system", "content": LLM_SYSTEM_PROMPT},
        ]
        if retry_instruction:
            messages.append({"role": "system", "content": retry_instruction})
        messages.append({"role": "user", "content": instruction})

        logging.debug("LLM request (attempt %s): %s", attempt, instruction)
        response = llm.create_chat_completion(
            messages=messages,
            temperature=LLAMA_TEMPERATURE,
        )

        text = response["choices"][0]["message"]["content"].strip()
        logging.debug("LLM raw response: %s", text)

        candidate = _extract_json(text)
        validated, error = _validate_plan(candidate)
        if validated:
            plan = validated
            break

        if attempt == max_attempts:
            break

        retry_instruction = (
            "The previous reply was invalid JSON. Respond ONLY with a JSON object shaped as "
            '{"action": "<supported action>", "parameters": { ... }, "message": "<optional string>"}. '
            f"Error: {error or 'Unable to parse response.'}"
        )

    if not plan:
        fallback = _keyword_plan(instruction)
        if fallback:
            plan = dict(fallback)

    action = plan.get("action")
    if action not in SUPPORTED_ACTIONS:
        plan["action"] = "no_action"
        plan.setdefault("parameters", {})
        plan.setdefault("message", "Model returned an unsupported action.")
    else:
        plan.setdefault("parameters", {})

    if plan.get("action") == "no_action":
        fallback = _keyword_plan(instruction)
        if fallback:
            plan["action"] = fallback["action"]
            plan["parameters"] = fallback.get("parameters", {})
            plan.pop("message", None)

    logging.info("LLM plan resolved: %s", _format_for_log(plan))
    return plan


def _build_multi_action_plan(llm: Llama, instruction: str) -> List[Dict[str, Any]]:
    # 単一アクションまたはヒューリスティックから多段プランを生成
    heuristic = _heuristic_multi_plan(instruction)
    if heuristic:
        return heuristic

    plan = _plan_from_instruction(llm, instruction)
    if isinstance(plan, dict) and plan:
        return [plan]

    return []


def _execute_plan_sequence(
    plans: List[Dict[str, Any]]
) -> Tuple[bool, Any, Optional[str], Optional[str], str, Dict[str, Any]]:
    # プラン配列を順に実行し、総合結果とメタ情報をまとめる
    if not plans:
        message = "No executable actions resolved from instruction."
        return False, None, message, message, "no_action", {}

    if len(plans) == 1:
        plan = plans[0]
        action = str(plan.get("action") or "no_action")
        parameters = dict(plan.get("parameters") or {})
        message = plan.get("message") if isinstance(plan.get("message"), str) else None
        ok, result, error = _execute_action(action, parameters)
        return ok, result, message, error, action, parameters

    executed_steps: List[Dict[str, Any]] = []
    status_parts: List[str] = []
    plan_messages: List[str] = []
    error_messages: List[str] = []

    for index, plan in enumerate(plans, start=1):
        action = str(plan.get("action") or "no_action")
        parameters = dict(plan.get("parameters") or {})
        message = plan.get("message") if isinstance(plan.get("message"), str) else None

        ok, result, error = _execute_action(action, parameters)

        step_record: Dict[str, Any] = {
            "step": index,
            "action": action,
            "ok": ok,
            "parameters": parameters,
        }

        if result is not None:
            step_record["result"] = result

        if message:
            plan_messages.append(message)
            step_record["plan_message"] = message

        if error:
            error_entry = f"{action}: {error}"
            error_messages.append(error_entry)
            step_record["error"] = error

        status_parts.append(f"{action}: {'成功' if ok else '失敗'}")
        executed_steps.append(step_record)

    overall_ok = all(step["ok"] for step in executed_steps)

    summary: Dict[str, Any] = {
        "actions": [step["action"] for step in executed_steps],
        "total_steps": len(executed_steps),
        "successful_steps": sum(1 for step in executed_steps if step["ok"]),
        "success": overall_ok,
    }

    if not overall_ok:
        summary["failed_steps"] = [step["step"] for step in executed_steps if not step["ok"]]

    message_parts: List[str] = list(dict.fromkeys(status_parts))
    if plan_messages:
        message_parts.extend(part for part in plan_messages if part)

    message_text = " / ".join(part for part in message_parts if part) or None

    error_text = " / ".join(dict.fromkeys(error_messages)) or None
    if error_text:
        message_text = (message_text + " / " if message_text else "") + f"エラー: {error_text}"

    result_value: Dict[str, Any] = {
        "summary": summary,
        "steps": executed_steps,
    }

    logging.info("Multi-action plan summary: %s", _format_for_log(summary))
    for step in executed_steps:
        logging.info(
            "Step %s/%s '%s' -> %s",
            step["step"],
            summary["total_steps"],
            step["action"],
            "success" if step["ok"] else "failure",
        )

    return overall_ok, result_value, message_text, error_text, "multi_action_sequence", summary


def _extract_json(text: str) -> Optional[Any]:
    # LLM の生テキストから JSON を抽出
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        candidate = text[start : end + 1]
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            return None
    return None


def _infer_units_from_instruction(instruction: str) -> Optional[str]:
    # 命令文に含まれる単位指定を推定
    text = instruction.lower()
    if "fahrenheit" in text or "imperial" in text:
        return "imperial"
    if "celsius" in text or "metric" in text:
        return "metric"
    if "kelvin" in text or "standard" in text:
        return "standard"
    return None


def _extract_weather_location(instruction: str) -> Optional[str]:
    # 天気要求から地名を抽出（英語・日本語どちらにも対応）
    patterns = [
        r"\bweather\s+(?:in|for)\s+([A-Za-z0-9 ,'-]+)",
        r"\btemperature\s+(?:in|for)\s+([A-Za-z0-9 ,'-]+)",
        r"([A-Za-z0-9 ,'-]+)\s+weather",
    ]
    for pattern in patterns:
        match = re.search(pattern, instruction, re.IGNORECASE)
        if match:
            candidate = match.group(1).strip()
            candidate = re.split(r"[\.?!,]", candidate)[0].strip()
            if candidate:
                return candidate

    jp_match = re.search(r"([\w\u3040-\u30ff\u4e00-\u9faf\s]+?)の天気", instruction)
    if jp_match:
        candidate = jp_match.group(1).strip()
        if candidate:
            return candidate

    return None


def _keyword_plan(instruction: str) -> Optional[Dict[str, Any]]:
    # 単純なキーワード一致で最適アクションを一件抽出
    plans = _heuristic_multi_plan(instruction)
    return plans[0] if plans else None


def _extract_float(patterns: List[str], text: str) -> Optional[str]:
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1)
    return None


def _build_servo_parameters_from_instruction(
    instruction: str, lowered: str
) -> Dict[str, Any]:
    """Translate common servo-related phrases into script parameters."""

    instruction = _normalize_digits(instruction)
    lowered = instruction.lower()

    params: Dict[str, Any] = {}
    command_parts: List[str] = []

    # Detect requested channel (1-4)
    channel: Optional[int] = None
    channel_patterns = [
        r"\bch(?:annel)?\s*(\d+)",
        r"(?:CH|ＣＨ)\s*(\d+)",
        r"(\d+)\s*ch",
        r"チャンネル\s*(\d+)",
    ]
    for pattern in channel_patterns:
        match = re.search(pattern, instruction, re.IGNORECASE)
        if match:
            try:
                candidate = int(match.group(1))
            except ValueError:
                continue
            if 1 <= candidate <= 4:
                channel = candidate
                break

    pigpio_requested = "pigpio" in lowered or "ピグピオ" in instruction

    # Determine desired command
    if any(keyword in lowered for keyword in ["center", "centre"]) or any(
        kw in instruction for kw in ["センタ", "センター", "中央"]
    ):
        command_parts.append("center")
    elif any(keyword in lowered for keyword in ["off", "detach"]) or any(
        kw in instruction for kw in ["停止", "止め", "オフ"]
    ):
        command_parts.append("off")
    elif any(keyword in lowered for keyword in ["info", "information"]) or "配線" in instruction:
        command_parts.append("info")
    else:
        sweep_keywords = ["sweep", "scan", "swing"]
        sweep_matches = any(keyword in lowered for keyword in sweep_keywords) or any(
            kw in instruction for kw in ["スイープ", "往復", "揺", "振"]
        )
        if sweep_matches:
            command_parts.append("sweep")
            start_value = _extract_float(
                [
                    r"(?:from|start(?:ing)?(?:\s+at)?)\s*(\d+(?:\.\d+)?)",
                    r"(\d+(?:\.\d+)?)\s*(?:度|degrees?)\s*(?:から|~|〜)",
                ],
                instruction,
            )
            end_value = _extract_float(
                [
                    r"(?:to|until|end(?:ing)?(?:\s+at)?)\s*(\d+(?:\.\d+)?)",
                    r"(?:to|まで)\s*(\d+(?:\.\d+)?)",
                ],
                instruction,
            )

            numeric_candidates: List[str] = []
            if start_value is None or end_value is None:
                # Fallback: pick numbers from text excluding the channel id
                raw_numbers = re.findall(r"\d+(?:\.\d+)?", instruction)
                for raw in raw_numbers:
                    try:
                        value = float(raw)
                    except ValueError:
                        continue
                    if channel is not None and abs(value - channel) < 1e-9:
                        continue
                    numeric_candidates.append(raw)

            if start_value is None and numeric_candidates:
                start_value = numeric_candidates.pop(0)
            if end_value is None and numeric_candidates:
                end_value = numeric_candidates.pop(0)

            if start_value is not None:
                command_parts.extend(["--start", start_value])
            if end_value is not None:
                command_parts.extend(["--end", end_value])

            step_value = _extract_float([r"step(?: size)?\s*(\d+(?:\.\d+)?)", r"刻み\s*(\d+(?:\.\d+)?)"], instruction)
            if step_value is None and numeric_candidates:
                step_value = numeric_candidates.pop(0)
            if step_value is not None:
                command_parts.extend(["--step", step_value])

            delay_value = _extract_float(
                [
                    r"delay\s*(\d+(?:\.\d+)?)",
                    r"(\d+(?:\.\d+)?)\s*(?:sec|s|秒)(?:\s*delay)?",
                ],
                instruction,
            )
            if delay_value is not None:
                command_parts.extend(["--delay", delay_value])

            cycles_value = _extract_float(
                [r"(\d+)\s*(?:cycles?|回|往復)"],
                instruction,
            )
            if cycles_value is not None:
                command_parts.extend(["--cycles", cycles_value])
        else:
            angle_value = _extract_float(
                [
                    r"(?:to|at|angle|set)\s*(\d+(?:\.\d+)?)\s*(?:degrees?|°)",
                    r"(\d+(?:\.\d+)?)度",
                ],
                instruction,
            )
            if angle_value is not None:
                command_parts.extend(["set", "--angle", angle_value])

    hold_value = _extract_float(
        [r"hold\s*(\d+(?:\.\d+)?)", r"(\d+(?:\.\d+)?)\s*秒保持"],
        instruction,
    )

    if command_parts:
        if channel is not None:
            command_parts.extend(["--channel", str(channel)])
        if pigpio_requested:
            command_parts.append("--pigpio")
        if hold_value is not None:
            command_parts.extend(["--hold", hold_value])
        params["command"] = " ".join(command_parts)

    return params


def _heuristic_multi_plan(instruction: str) -> List[Dict[str, Any]]:
    """Resolve an instruction into a deterministic sequence of actions."""

    # ヒューリスティックで複数アクションを導出
    text = _normalize_digits(instruction).strip()
    if not text:
        return []

    lowered = text.lower()
    plans: List[Dict[str, Any]] = []
    seen: Set[str] = set()

    def _add(action: str, parameters: Dict[str, Any], message: Optional[str] = None) -> None:
        if action not in SUPPORTED_ACTIONS:
            return
        if action in seen:
            return
        seen.add(action)
        entry: Dict[str, Any] = {"action": action, "parameters": dict(parameters or {})}
        if message:
            entry["message"] = message
        plans.append(entry)

    if (
        "rock paper scissors" in lowered
        or "janken" in lowered
        or "じゃんけん" in text
        or "グー" in text
    ):
        _add("play_rock_paper_scissors", {})

    if any(
        keyword in lowered
        for keyword in ["tell me a joke", "joke", "ジョーク", "冗談", "笑い"]
    ):
        _add("tell_joke", {})

    if any(
        keyword in lowered
        for keyword in ["what time", "current time", "time is it", "clock", "時刻", "今何時"]
    ):
        _add("get_current_time", {})

    if "weather" in lowered or "temperature" in lowered or "forecast" in lowered or "天気" in text:
        location = _extract_weather_location(instruction)
        if not location and LOCATION and LOCATION.lower() != "lab":
            location = LOCATION
        if location:
            params: Dict[str, Any] = {"location": location}
            units = _infer_units_from_instruction(instruction)
            if units:
                params["units"] = units
            _add("get_weather", params)

    if any(
        keyword in lowered
        for keyword in ["camera", "photo", "picture", "snapshot"]
    ) or "カメラ" in text or "写真" in text or "撮影" in text:
        _add("capture_camera_photo", {})

    motor_keywords = ["motor test", "motor demo", "l293d", "dc motor"]
    if (
        any(keyword in lowered for keyword in motor_keywords)
        or ("モーター" in text and "サーボ" not in text)
    ) and "servo" not in lowered:
        _add("run_motor_test", {})

    led_keywords = ["led", "blink", "blinking", "light show"]
    if any(keyword in lowered for keyword in led_keywords) or "ライト" in text or "点滅" in text:
        _add("run_led_demo", {})

    oled_keywords = ["oled", "st7735", "robot face", "lcd animation", "mono eye", "zaku", "mono-eye"]
    if (
        any(keyword in lowered for keyword in oled_keywords)
        or ("ロボット" in text and "顔" in text)
        or "モノアイ" in text
        or "ザク" in text
        or "液晶" in text
    ):
        _add("run_oled_robot_demo", {})

    if "servo" in lowered or "サーボ" in text:
        servo_params = _build_servo_parameters_from_instruction(text, lowered)
        _add("run_servo_demo", servo_params)
        if any(
            keyword in lowered for keyword in ["dual servo", "two servo", "two servos", "double servo", "2 servo", "dual sweep"]
        ) or "2つのサーボ" in text or "二つのサーボ" in text:
            _add("run_dual_servo_demo", {})

    return plans


# ==== Main loop ============================================================


def _safe_job_id(job: Any) -> Optional[str]:
    raw_job_id: Any = None
    try:
        raw_job_id = job.get("job_id") or job.get("id")
    except Exception:
        return None

    if isinstance(raw_job_id, str):
        job_id = raw_job_id.strip()
    elif raw_job_id is not None:
        job_id = str(raw_job_id)
    else:
        job_id = None

    return job_id or None


def _process_job(
    session: requests.Session,
    llm: Llama,
    device_id: str,
    job: Dict[str, Any],
) -> None:
    # サーバーから受信したジョブを解析し、適切なアクションを実行
    raw_job_id: Any = job.get("job_id")
    if raw_job_id is None and "id" in job:
        raw_job_id = job.get("id")

    job_id = None
    if isinstance(raw_job_id, str):
        job_id = raw_job_id.strip()
    elif raw_job_id is not None:
        job_id = str(raw_job_id)

    command = job.get("command") or {}
    args = command.get("args") if isinstance(command, dict) else {}
    command_name = command.get("name") if isinstance(command, dict) else None
    if isinstance(command_name, str):
        command_name = command_name.strip()
    else:
        command_name = None

    job_device_id = job.get("device_id") or job.get("target_device_id")
    if job_device_id and job_device_id != device_id:
        message = (
            f"Job is targeted to device '{job_device_id}' but this agent is '{device_id}'."
        )
        logging.warning("Skipping job %s: %s", job_id or "<unknown>", message)
        if job_id:
            payload = _build_result_payload(
                device_id=device_id,
                job_id=job_id,
                ok=False,
                action=None,
                parameters=None,
                message=message,
                result=None,
                error=message,
            )
            if not _post_result(session, payload):
                logging.error("Failed to report mismatched device for job %s", job_id)
        return

    if not job_id:
        logging.error("Invalid job payload without a job_id: %s", job)
        return
    if not command_name:
        message = "Job is missing a command name."
        logging.error("Job %s missing command", job_id)
        payload = _build_result_payload(
            device_id=device_id,
            job_id=job_id,
            ok=False,
            action=None,
            parameters=None,
            message=message,
            result=None,
            error=message,
        )
        if not _post_result(session, payload):
            logging.error("Failed to report missing command for job %s", job_id)
        return

    resolved_action: Optional[str] = None
    resolved_parameters: Dict[str, Any] = {}
    resolved_message: Optional[str] = None
    ok = False
    return_value: Any = None
    error_message: Optional[str] = None
    if command_name == AGENT_COMMAND_NAME:
        instruction_value = args.get("instruction") if isinstance(args, dict) else None
        instruction = instruction_value.strip() if isinstance(instruction_value, str) else None
        if not instruction:
            message = "Job is missing instruction text."
            logging.error("Job %s missing instruction", job_id)
            payload = _build_result_payload(
                device_id=device_id,
                job_id=job_id,
                ok=False,
                action=None,
                parameters=None,
                message=message,
                result=None,
                error=message,
            )
            if not _post_result(session, payload):
                logging.error("Failed to report missing instruction for job %s", job_id)
            return

        logging.info("Processing job %s with instruction: %s", job_id, instruction)
        _console(
            "Job {} instruction received: {}".format(
                job_id,
                instruction,
            )
        )

        plans = _build_multi_action_plan(llm, instruction)
        (
            ok,
            return_value,
            resolved_message,
            error_message,
            resolved_action,
            resolved_parameters,
        ) = _execute_plan_sequence(plans)

        if resolved_action == "multi_action_sequence" and isinstance(return_value, dict):
            steps = return_value.get("steps") if isinstance(return_value.get("steps"), list) else []
            for step in steps:
                if not isinstance(step, dict):
                    continue
                step_no = step.get("step")
                step_action = step.get("action") or "unknown"
                status = "成功" if step.get("ok") else "失敗"
                _console(
                    "Job {} step {} '{}' 結果: {}".format(
                        job_id,
                        step_no if step_no is not None else "?",
                        step_action,
                        status,
                    )
                )

            summary_info = (
                return_value.get("summary")
                if isinstance(return_value.get("summary"), dict)
                else {}
            )
            if summary_info:
                _console(
                    "Job {} multi-action summary: {}".format(
                        job_id,
                        _format_for_log(summary_info),
                    )
                )
    else:
        resolved_action = command_name
        resolved_parameters = args if isinstance(args, dict) else {}
        logging.info(
            "Processing job %s with direct action: %s",
            job_id,
            resolved_action,
        )
        _console(
            "Job {} direct action request: {} with parameters {}.".format(
                job_id,
                resolved_action,
                _format_for_log(resolved_parameters),
            )
        )
        ok, return_value, error_message = _execute_action(
            resolved_action,
            resolved_parameters,
        )

    if not isinstance(resolved_action, str) or not resolved_action:
        error_message = "Resolved action is invalid."
        payload = _build_result_payload(
            device_id=device_id,
            job_id=job_id,
            ok=False,
            action=None,
            parameters=None,
            message=error_message,
            result=None,
            error=error_message,
        )
        if not _post_result(session, payload):
            logging.error("Failed to report invalid action for job %s", job_id)
        _console(
            "Job {} failed: resolved action invalid, notified server.".format(job_id)
        )
        return

    if command_name == AGENT_COMMAND_NAME and resolved_action != "multi_action_sequence":
        _console(
            "Job {} executing action '{}' with parameters {}.".format(
                job_id,
                resolved_action,
                _format_for_log(resolved_parameters),
            )
        )

    if ok:
        if resolved_action == "multi_action_sequence":
            logging.info("All actions succeeded for job %s", job_id)
            _console(
                "Job {} multi-action sequence completed successfully.".format(job_id)
            )
        else:
            logging.info(
                "Action '%s' succeeded for job %s", resolved_action, job_id
            )
            logging.info("Result payload: %s", _format_for_log(return_value))
            _console(
                "Job {} action '{}' succeeded. Result: {}".format(
                    job_id,
                    resolved_action,
                    _format_for_log(return_value),
                )
            )
    else:
        logging.error(
            "Action '%s' failed for job %s: %s",
            resolved_action,
            job_id,
            error_message,
        )
        if return_value is not None:
            logging.error(
                "Partial result for failed action '%s': %s",
                resolved_action,
                _format_for_log(return_value),
            )
        _console(
            "Job {} action '{}' failed: {}".format(
                job_id,
                resolved_action,
                error_message or "unknown error",
            )
        )
        if return_value is not None:
            _console(
                "Job {} partial result: {}".format(
                    job_id,
                    _format_for_log(return_value),
                )
            )

    result_payload = _build_result_payload(
        device_id=device_id,
        job_id=job_id,
        ok=bool(ok),
        action=resolved_action,
        parameters=resolved_parameters,
        message=resolved_message,
        result=return_value,
        error=error_message,
    )

    logging.info(
        "Job %s completed: action=%s ok=%s error=%s",
        job_id,
        resolved_action,
        ok,
        error_message,
    )

    if resolved_message:
        logging.info("Job %s agent message: %s", job_id, resolved_message)
        _console(
            "Job {} message to user: {}".format(job_id, resolved_message)
        )

    if not _post_result(session, result_payload):
        logging.error("Failed to deliver result for job %s", job_id)
        _console("Job {} result delivery failed after retries.".format(job_id))


def main() -> None:
    # エージェントのエントリーポイント。モデル読み込みとポーリングループを開始
    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    session = requests.Session()
    device_id = _load_device_id()
    _console("Device ID resolved: {}".format(device_id))
    llm = _create_llm()
    _console(
        "Model ready (path='{}', threads={}, context={}).".format(
            MODEL_PATH,
            LLAMA_THREADS,
            LLAMA_CONTEXT,
        )
    )

    if AUTO_APPROVE:
        logging.info(
            "Auto-approval enabled; device '%s' will register itself with capabilities.",
            device_id,
        )
        _console(
            "Auto-approval ON. Registering '{}' directly with full capabilities.".format(
                device_id
            )
        )
    else:
        if AUTO_REGISTRATION_REQUESTED:
            logging.warning(
                "IOT_AGENT_AUTO_REGISTER is deprecated. Manual approval is now required;"
                " the device will not auto-register with the server."
            )
            _console(
                "AUTO_REGISTER flag detected but manual approval workflow is in effect."
            )
        logging.info(
            "Manual registration is required. Add device '%s' from the dashboard to approve it.",
            device_id,
        )
        _console(
            "Manual approval required on dashboard for device '{}'.".format(device_id)
        )

    manual_approval_required_logged = False
    while True:
        registered, manual_required = _register_device(session, device_id)
        if registered:
            break

        if manual_required and not manual_approval_required_logged:
            logging.warning(
                "Waiting for manual approval of device '%s'. Once approved, registration will complete automatically.",
                device_id,
            )
            manual_approval_required_logged = True
            _console(
                "Waiting for manual approval of device '{}' on server.".format(device_id)
            )

        logging.error("Unable to register device. Retrying in 30 seconds...")
        _console(
            "Device '{}' registration attempt failed. Retrying soon...".format(device_id)
        )
        time.sleep(30 if manual_required else 10)

    logging.info("Starting polling loop as %s", device_id)
    _console("Entering polling loop as device '{}'.".format(device_id))

    try:
        while True:
            job = _poll_next_job(session, device_id)
            if job:
                try:
                    _process_job(session, llm, device_id, job)
                except Exception as exc:
                    logging.exception("Unexpected error while processing job")
                    job_id = _safe_job_id(job)
                    command = job.get("command") if isinstance(job, dict) else {}
                    action_name = None
                    if isinstance(command, dict):
                        action_name = command.get("name")
                        if isinstance(action_name, str):
                            action_name = action_name.strip() or None
                    error_text = str(exc)
                    if job_id:
                        try:
                            payload = _build_result_payload(
                                device_id=device_id,
                                job_id=job_id,
                                ok=False,
                                action=action_name,
                                parameters=None,
                                message=error_text,
                                result=None,
                                error=error_text,
                            )
                            _post_result(session, payload)
                        except Exception:
                            logging.exception("Failed to report unexpected error for job %s", job_id)
                    _console(
                        "Job {} processing failed unexpectedly: {}".format(
                            job_id or "<unknown>",
                            error_text,
                        )
                    )
            else:
                time.sleep(POLL_INTERVAL)
    except KeyboardInterrupt:
        logging.info("Stopping agent")
        _console("Keyboard interrupt received. Stopping agent loop.")


if __name__ == "__main__":
    main()
