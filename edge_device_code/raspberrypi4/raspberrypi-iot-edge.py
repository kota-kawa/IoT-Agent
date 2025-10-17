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

import json
import logging
import os
import random
import re
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import requests
from dotenv import load_dotenv
from llama_cpp import Llama

# Load environment variables from potential .env locations before reading them.
_ENV_CANDIDATES = [
    Path(__file__).resolve().parent / ".env",
    Path(__file__).resolve().parent.parent / ".env",
    Path.cwd() / ".env",
]
for _env_file in _ENV_CANDIDATES:
    if _env_file.exists():
        load_dotenv(_env_file, override=False)
# Also respect a .env in the current working directory if one exists.
load_dotenv(override=False)

# ==== Configuration ========================================================

MODEL_PATH = os.getenv("LLAMA_MODEL_PATH", "tinyllama-1.1b-chat-v1.0.Q4_K_M.gguf")
LLAMA_THREADS = int(os.getenv("LLAMA_THREADS", "4"))
LLAMA_CONTEXT = int(os.getenv("LLAMA_CONTEXT", "1024"))
LLAMA_TEMPERATURE = float(os.getenv("LLAMA_TEMPERATURE", "0.2"))

# NOTE: The IoT server is deployed remotely, so we default to the public
# endpoint. Set IOT_SERVER_URL to override when testing against a different
# environment.
SERVER_BASE_URL = os.getenv(
    "IOT_SERVER_URL", "https://iot-agent.project-kk.com"
).rstrip("/")
# Default to a 3 minute HTTP timeout to accommodate longer-running server
# operations, while still allowing customization through the environment
# variable.
REQUEST_TIMEOUT = float(os.getenv("IOT_AGENT_HTTP_TIMEOUT", "180"))
POLL_INTERVAL = float(os.getenv("IOT_AGENT_POLL_INTERVAL", "2.0"))

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

OPEN_WEATHER_API_KEY = os.getenv("OPEN_WEATHER_API_KEY")
OPEN_WEATHER_BASE_URL = os.getenv(
    "OPEN_WEATHER_BASE_URL", "https://api.openweathermap.org/data/2.5/weather"
)

DEVICE_ID_ENV = os.getenv("IOT_AGENT_DEVICE_ID")
DEVICE_ID_PATH = Path(
    os.getenv(
        "IOT_AGENT_DEVICE_ID_PATH",
        str(Path(__file__).resolve().parent / "device_id.txt"),
    )
)

DISPLAY_NAME = os.getenv("IOT_AGENT_DISPLAY_NAME", "Raspberry Pi 4 Agent")
LOCATION = os.getenv("IOT_AGENT_LOCATION", "Lab")

REGISTER_PATH = "/api/devices/register"
NEXT_PATH = "/api/devices/{device_id}/jobs/next"
RESULT_PATH = "/api/devices/{device_id}/jobs/result"

AGENT_ROLE_VALUE = "raspberrypi-agent"
AGENT_COMMAND_NAME = "agent_instruction"

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
    "no_action": {
        "description": "Used when the request should not trigger a device operation.",
        "params": [
            {"name": "message", "type": "string", "required": False},
        ],
    },
}

ACTION_CATALOG = [
    {
        "name": action,
        "description": spec["description"],
        "params": spec.get("params", []),
    }
    for action, spec in SUPPORTED_ACTIONS.items()
    if action != "no_action"
]

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

LLM_SYSTEM_PROMPT = (
    "You convert simple English instructions into JSON commands for a Raspberry Pi automation agent.\n"
    "Return ONLY a JSON object with the keys 'action', 'parameters', and optional 'message'.\n"
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
    return f"{SERVER_BASE_URL}{path}"


def _load_device_id() -> str:
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
    try:
        message = json.dumps(value, ensure_ascii=False, sort_keys=True)
    except TypeError:
        message = repr(value)
    logging.log(level, "%s: %s", label, message)


def _register_device(session: requests.Session, device_id: str) -> Tuple[bool, bool]:
    payload = {
        "device_id": device_id,
        "capabilities": CAPABILITIES,
        "meta": {
            "display_name": DISPLAY_NAME,
            "role": AGENT_ROLE_VALUE,
            "location": LOCATION,
            "action_catalog": ACTION_CATALOG,
            "note": "TinyLlama-powered Raspberry Pi agent",
        },
    }

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
            return False, True

        resp.raise_for_status()
        try:
            data = resp.json()
        except ValueError:
            data = {}
        logging.info("Device registration acknowledged: status=%s", data.get("status", "ok"))
        _log_dict("Server device snapshot", data.get("device") or {})
        return True, False
    except Exception as exc:
        logging.error("Registration failed: %s", exc)
        return False, False


def _poll_next_job(session: requests.Session, device_id: str) -> Optional[Dict[str, Any]]:
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
        registered, manual_required = _register_device(session, device_id)
        if not registered and manual_required:
            logging.warning(
                "Server still waiting for manual approval of device '%s'.", device_id
            )
        return None

    if resp.status_code != 200:
        logging.error("Unexpected status from job endpoint: %s", resp.status_code)
        return None

    try:
        return resp.json()
    except json.JSONDecodeError:
        logging.error("Job payload is not valid JSON: %s", resp.text[:200])
    return None


def _post_result(
    session: requests.Session,
    payload: Dict[str, Any],
    *,
    max_attempts: int = 3,
    backoff_seconds: float = 2.0,
) -> bool:
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
                return True

            body_preview = response.text[:200] if response.text else ""
            logging.error(
                "Result post attempt %s failed with status %s. Body preview: %s",
                attempt,
                response.status_code,
                body_preview,
            )
        except Exception as exc:
            logging.error("Result post attempt %s raised error: %s", attempt, exc)

        if attempt >= max_attempts:
            break

        sleep_for = min(backoff_seconds * (2 ** (attempt - 1)), 30.0)
        logging.info("Retrying result post in %.1f seconds", sleep_for)
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
    return {
        "device_id": device_id,
        "job_id": job_id,
        "ok": bool(ok),
        "return_value": {
            "action": action,
            "parameters": parameters or {},
            "message": message,
            "result": result,
        },
        "stdout": None,
        "stderr": None,
        "error": error,
        "ts": time.time(),
    }


# ==== Task execution =======================================================


def _format_for_log(value: Any, *, max_length: int = 500) -> str:
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
    key = value.strip().lower()
    return _MOVE_ALIASES.get(key)


def _play_rock_paper_scissors(params: Dict[str, Any]) -> Dict[str, Any]:
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
    joke = random.choice(JOKES)
    return {"joke": joke}


def _get_weather(params: Dict[str, Any]) -> Dict[str, Any]:
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


def _execute_action(action: str, parameters: Dict[str, Any]) -> Tuple[bool, Any, Optional[str]]:
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
        if action == "no_action":
            message = parameters.get("message") if isinstance(parameters, dict) else None
            return True, {"message": message or "No action executed."}, None

        return False, None, f"Unsupported action: {action}"
    except Exception as exc:
        logging.exception("Action '%s' raised an exception", action)
        return False, None, str(exc)


# ==== LLM interaction ======================================================


def _plan_from_instruction(llm: Llama, instruction: str) -> Dict[str, Any]:
    messages = [
        {"role": "system", "content": LLM_SYSTEM_PROMPT},
        {"role": "user", "content": instruction},
    ]

    logging.debug("LLM request: %s", instruction)
    response = llm.create_chat_completion(
        messages=messages,
        temperature=LLAMA_TEMPERATURE,
    )

    text = response["choices"][0]["message"]["content"].strip()
    logging.debug("LLM raw response: %s", text)

    plan = _extract_json(text)
    if not isinstance(plan, dict):
        plan = {}

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


def _extract_json(text: str) -> Optional[Any]:
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
    text = instruction.lower()
    if "fahrenheit" in text or "imperial" in text:
        return "imperial"
    if "celsius" in text or "metric" in text:
        return "metric"
    if "kelvin" in text or "standard" in text:
        return "standard"
    return None


def _extract_weather_location(instruction: str) -> Optional[str]:
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
    text = instruction.strip()
    if not text:
        return None

    lowered = text.lower()
    if "rock paper scissors" in lowered or "janken" in lowered or "じゃんけん" in text:
        return {"action": "play_rock_paper_scissors", "parameters": {}}

    if any(keyword in lowered for keyword in ["tell me a joke", "joke", "ジョーク", "冗談"]):
        return {"action": "tell_joke", "parameters": {}}

    if any(keyword in lowered for keyword in ["what time", "current time", "time is it", "clock", "時刻", "今何時"]):
        return {"action": "get_current_time", "parameters": {}}

    if "weather" in lowered or "temperature" in lowered or "forecast" in lowered or "天気" in text:
        location = _extract_weather_location(instruction)
        if not location and LOCATION and LOCATION.lower() != "lab":
            location = LOCATION
        if not location:
            return None
        params: Dict[str, Any] = {"location": location}
        units = _infer_units_from_instruction(instruction)
        if units:
            params["units"] = units
        return {"action": "get_weather", "parameters": params}

    return None


# ==== Main loop ============================================================


def _process_job(
    session: requests.Session,
    llm: Llama,
    device_id: str,
    job: Dict[str, Any],
) -> None:
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

    action: Optional[str] = None
    parameters: Dict[str, Any] = {}
    message: Optional[str] = None
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

        plan = _plan_from_instruction(llm, instruction)
        action = plan.get("action", "no_action")
        parameters = (
            plan.get("parameters") if isinstance(plan.get("parameters"), dict) else {}
        )
        message = plan.get("message") if isinstance(plan.get("message"), str) else None
    else:
        action = command_name
        parameters = args if isinstance(args, dict) else {}
        logging.info("Processing job %s with direct action: %s", job_id, action)

    if not isinstance(action, str) or not action:
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
        return

    ok, return_value, error_message = _execute_action(action, parameters)

    if ok:
        logging.info(
            "Action '%s' succeeded for job %s", action, job_id
        )
        logging.info("Result payload: %s", _format_for_log(return_value))
    else:
        logging.error("Action '%s' failed for job %s: %s", action, job_id, error_message)
        if return_value is not None:
            logging.error(
                "Partial result for failed action '%s': %s",
                action,
                _format_for_log(return_value),
            )

    result_payload = _build_result_payload(
        device_id=device_id,
        job_id=job_id,
        ok=bool(ok),
        action=action,
        parameters=parameters,
        message=message,
        result=return_value,
        error=error_message,
    )

    logging.info(
        "Job %s completed: action=%s ok=%s error=%s",
        job_id,
        action,
        ok,
        error_message,
    )

    if message:
        logging.info("Job %s agent message: %s", job_id, message)

    if not _post_result(session, result_payload):
        logging.error("Failed to deliver result for job %s", job_id)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    session = requests.Session()
    device_id = _load_device_id()
    llm = _create_llm()

    if AUTO_REGISTRATION_REQUESTED:
        logging.warning(
            "IOT_AGENT_AUTO_REGISTER is deprecated. Manual approval is now required;"
            " the device will not auto-register with the server."
        )
    logging.info(
        "Manual registration is required. Add device '%s' from the dashboard to approve it.",
        device_id,
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

        logging.error("Unable to register device. Retrying in 30 seconds...")
        time.sleep(30 if manual_required else 10)

    logging.info("Starting polling loop as %s", device_id)

    try:
        while True:
            job = _poll_next_job(session, device_id)
            if job:
                _process_job(session, llm, device_id, job)
            else:
                time.sleep(POLL_INTERVAL)
    except KeyboardInterrupt:
        logging.info("Stopping agent")


if __name__ == "__main__":
    main()
