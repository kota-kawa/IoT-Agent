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
import shutil
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import requests
from llama_cpp import Llama

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
REQUEST_TIMEOUT = float(os.getenv("IOT_AGENT_HTTP_TIMEOUT", "10"))
POLL_INTERVAL = float(os.getenv("IOT_AGENT_POLL_INTERVAL", "2.0"))

DEVICE_ID_ENV = os.getenv("IOT_AGENT_DEVICE_ID")
DEVICE_ID_PATH = Path(
    os.getenv(
        "IOT_AGENT_DEVICE_ID_PATH",
        str(Path.home() / ".cache" / "iot-agent-device-id"),
    )
)

DISPLAY_NAME = os.getenv("IOT_AGENT_DISPLAY_NAME", "Raspberry Pi 4 Agent")
LOCATION = os.getenv("IOT_AGENT_LOCATION", "Lab")

REGISTER_PATH = "/pico-w/register"
NEXT_PATH = "/pico-w/next"
RESULT_PATH = "/pico-w/result"

AGENT_ROLE_VALUE = "raspberrypi-agent"
AGENT_COMMAND_NAME = "agent_instruction"

SUPPORTED_ACTIONS: Dict[str, Dict[str, Any]] = {
    "get_current_time": {
        "description": "Return the current local time in ISO 8601 format.",
        "params": [],
    },
    "get_system_status": {
        "description": "Report CPU load average, uptime, disk usage and memory information.",
        "params": [],
    },
    "list_directory": {
        "description": "List files and folders within a directory.",
        "params": [
            {"name": "path", "type": "string", "required": False, "default": "."},
            {"name": "limit", "type": "integer", "required": False, "default": 20},
        ],
    },
    "read_text_file": {
        "description": "Read a UTF-8 text file and return its first characters.",
        "params": [
            {"name": "path", "type": "string", "required": True},
            {"name": "max_chars", "type": "integer", "required": False, "default": 4000},
        ],
    },
    "no_action": {
        "description": "Used when the request should not trigger a device operation.",
        "params": [
            {"name": "message", "type": "string", "required": False},
        ],
    },
}

CAPABILITIES = [
    {
        "name": AGENT_COMMAND_NAME,
        "description": "Execute Raspberry Pi automation tasks derived from simple English instructions.",
        "params": [
            {"name": "instruction", "type": "string", "required": True},
        ],
    }
]

ACTION_CATALOG = [
    {
        "name": action,
        "description": spec["description"],
        "params": spec.get("params", []),
    }
    for action, spec in SUPPORTED_ACTIONS.items()
    if action != "no_action"
]

LLM_SYSTEM_PROMPT = (
    "You convert simple English instructions into JSON commands for a Raspberry Pi automation agent.\n"
    "Return ONLY a JSON object with the keys 'action', 'parameters', and optional 'message'.\n"
    "Valid actions are: "
    + ", ".join(sorted(SUPPORTED_ACTIONS.keys()))
    + ".\n"
    "If the instruction cannot be completed with the available actions, set action to 'no_action' and provide a short reason in 'message'.\n"
    "Examples:\n"
    "Instruction: List the home folder.\n"
    "{\"action\": \"list_directory\", \"parameters\": {\"path\": \"~/\"}}\n"
    "Instruction: I just wanted to say thanks.\n"
    "{\"action\": \"no_action\", \"parameters\": {}, \"message\": \"Gratitude only.\"}"
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


def _register_device(session: requests.Session, device_id: str) -> bool:
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
        resp.raise_for_status()
        try:
            data = resp.json()
        except ValueError:
            data = {}
        logging.info("Device registered: %s", data.get("status", "ok"))
        return True
    except Exception as exc:
        logging.error("Registration failed: %s", exc)
        return False


def _poll_next_job(session: requests.Session, device_id: str) -> Optional[Dict[str, Any]]:
    try:
        resp = session.get(
            _build_url(NEXT_PATH),
            params={"device_id": device_id},
            timeout=REQUEST_TIMEOUT,
        )
    except Exception as exc:
        logging.error("Failed to poll for job: %s", exc)
        return None

    if resp.status_code == 204:
        return None

    if resp.status_code == 404:
        logging.warning("Device not registered on server. Re-registering...")
        _register_device(session, device_id)
        return None

    if resp.status_code != 200:
        logging.error("Unexpected status from job endpoint: %s", resp.status_code)
        return None

    try:
        return resp.json()
    except json.JSONDecodeError:
        logging.error("Job payload is not valid JSON: %s", resp.text[:200])
    return None


def _post_result(session: requests.Session, payload: Dict[str, Any]) -> None:
    try:
        session.post(
            _build_url(RESULT_PATH),
            json=payload,
            timeout=REQUEST_TIMEOUT,
        )
    except Exception as exc:
        logging.error("Failed to post result: %s", exc)


# ==== Task execution =======================================================


def _list_directory(params: Dict[str, Any]) -> Dict[str, Any]:
    path = params.get("path") or "."
    limit = params.get("limit", 20)
    try:
        limit = int(limit)
    except Exception:
        limit = 20

    if limit <= 0:
        limit = 20

    path_obj = Path(path).expanduser().resolve()
    entries = []
    limited = False
    for entry in sorted(path_obj.iterdir(), key=lambda p: p.name):
        if len(entries) >= limit:
            limited = True
            break
        entry_type = "dir" if entry.is_dir() else "file" if entry.is_file() else "other"
        entries.append({"name": entry.name, "type": entry_type})

    return {
        "path": str(path_obj),
        "entries": entries,
        "limited": limited,
    }


def _read_text_file(params: Dict[str, Any]) -> Dict[str, Any]:
    path_value = params.get("path")
    if not isinstance(path_value, str) or not path_value.strip():
        raise ValueError("path parameter is required")

    max_chars = params.get("max_chars", 4000)
    try:
        max_chars = int(max_chars)
    except Exception:
        max_chars = 4000

    if max_chars <= 0:
        max_chars = 4000

    file_path = Path(path_value).expanduser().resolve()
    if not file_path.is_file():
        raise FileNotFoundError(f"File not found: {file_path}")

    content = file_path.read_text(encoding="utf-8", errors="replace")
    truncated = False
    if len(content) > max_chars:
        content = content[:max_chars]
        truncated = True

    return {
        "path": str(file_path),
        "content": content,
        "truncated": truncated,
    }


def _read_meminfo() -> Dict[str, int]:
    info: Dict[str, int] = {}
    try:
        with open("/proc/meminfo", "r", encoding="utf-8") as handle:
            for line in handle:
                parts = line.split(":")
                if len(parts) != 2:
                    continue
                key = parts[0].strip()
                value_part = parts[1].strip().split()
                if not value_part:
                    continue
                try:
                    value = int(value_part[0])
                except ValueError:
                    continue
                info[key] = value
    except Exception:
        pass
    return info


def _get_system_status() -> Dict[str, Any]:
    status: Dict[str, Any] = {}

    try:
        load1, load5, load15 = os.getloadavg()
        status["load_average"] = {"1m": load1, "5m": load5, "15m": load15}
    except (OSError, AttributeError):
        pass

    try:
        with open("/proc/uptime", "r", encoding="utf-8") as handle:
            uptime_seconds = float(handle.read().split()[0])
            status["uptime_seconds"] = uptime_seconds
    except Exception:
        pass

    try:
        disk = shutil.disk_usage("/")
        status["disk_usage"] = {
            "total": disk.total,
            "used": disk.used,
            "free": disk.free,
        }
    except Exception:
        pass

    meminfo = _read_meminfo()
    if meminfo:
        status["memory_kib"] = {
            key: meminfo[key]
            for key in ("MemTotal", "MemAvailable", "MemFree", "Buffers", "Cached")
            if key in meminfo
        }

    return status


def _execute_action(action: str, parameters: Dict[str, Any]) -> Tuple[bool, Any, Optional[str]]:
    try:
        if action == "get_current_time":
            now = datetime.now(timezone.utc).astimezone()
            return True, {"current_time": now.isoformat()}, None
        if action == "get_system_status":
            return True, _get_system_status(), None
        if action == "list_directory":
            return True, _list_directory(parameters or {}), None
        if action == "read_text_file":
            return True, _read_text_file(parameters or {}), None
        if action == "no_action":
            message = parameters.get("message") if isinstance(parameters, dict) else None
            return True, {"message": message or "No action executed."}, None

        return False, None, f"Unsupported action: {action}"
    except Exception as exc:
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

    action = plan.get("action")
    if action not in SUPPORTED_ACTIONS:
        plan["action"] = "no_action"
        plan.setdefault("parameters", {})
        plan.setdefault("message", "Model returned an unsupported action.")
    else:
        plan.setdefault("parameters", {})

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


# ==== Main loop ============================================================


def _process_job(
    session: requests.Session,
    llm: Llama,
    device_id: str,
    job: Dict[str, Any],
) -> None:
    job_id = job.get("job_id")
    command = job.get("command") or {}
    args = command.get("args") if isinstance(command, dict) else {}
    instruction = args.get("instruction") if isinstance(args, dict) else None

    if not job_id or not isinstance(job_id, str):
        logging.error("Invalid job payload: missing job_id")
        return
    if not instruction or not isinstance(instruction, str):
        logging.error("Job %s missing instruction", job_id)
        return

    logging.info("Processing job %s with instruction: %s", job_id, instruction)

    plan = _plan_from_instruction(llm, instruction)
    action = plan.get("action", "no_action")
    parameters = plan.get("parameters") if isinstance(plan.get("parameters"), dict) else {}
    message = plan.get("message") if isinstance(plan.get("message"), str) else None

    ok, return_value, error_message = _execute_action(action, parameters)

    result_payload = {
        "device_id": device_id,
        "job_id": job_id,
        "ok": bool(ok),
        "return_value": {
            "action": action,
            "parameters": parameters,
            "message": message,
            "result": return_value,
        },
        "stdout": None,
        "stderr": None,
        "error": error_message,
        "ts": time.time(),
    }

    logging.info(
        "Job %s completed: action=%s ok=%s error=%s",
        job_id,
        action,
        ok,
        error_message,
    )

    _post_result(session, result_payload)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    session = requests.Session()
    device_id = _load_device_id()
    llm = _create_llm()

    if not _register_device(session, device_id):
        logging.error("Unable to register device. Retrying in 10 seconds...")
        time.sleep(10)

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
