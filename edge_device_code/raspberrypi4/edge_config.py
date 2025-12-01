#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Shared configuration and helpers for the Raspberry Pi edge agent."""

import json
import logging
import os
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, Optional


MODEL_PATH = os.getenv("LLAMA_MODEL_PATH", "Llama-3.2-3B-Instruct-Q3_K_M.gguf")
LLAMA_THREADS = int(os.getenv("LLAMA_THREADS", "4"))
LLAMA_CONTEXT = int(os.getenv("LLAMA_CONTEXT", "1024"))
LLAMA_TEMPERATURE = float(os.getenv("LLAMA_TEMPERATURE", "0.2"))

SERVER_BASE_URL = os.getenv("IOT_SERVER_URL", "https://iot-agent.project-kk.com").rstrip("/")
REQUEST_TIMEOUT = float(os.getenv("IOT_AGENT_HTTP_TIMEOUT", "60"))
POLL_INTERVAL = float(os.getenv("IOT_AGENT_POLL_INTERVAL", "2.0"))

_AUTO_REGISTER_RAW = os.getenv("IOT_AGENT_AUTO_REGISTER")
AUTO_REGISTRATION_REQUESTED = (_AUTO_REGISTER_RAW or "").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}

_AUTO_APPROVE_RAW = os.getenv("IOT_AGENT_AUTO_APPROVE", "1")
AUTO_APPROVE = (_AUTO_APPROVE_RAW or "").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}

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

DEVICE_TEST_DIR = Path(__file__).resolve().parent / "device_test"
CAMERA_SAVE_DIR = Path(os.getenv("IOT_AGENT_CAMERA_DIR", "/home/kota/iot-agent/test")).expanduser()
CAMERA_WARMUP_SECONDS = float(os.getenv("IOT_AGENT_CAMERA_WARMUP", "1.2"))

DISPLAY_NAME = os.getenv("IOT_AGENT_DISPLAY_NAME", "Raspberry Pi 4 Agent")
LOCATION = os.getenv("IOT_AGENT_LOCATION", "Lab")

REGISTER_PATH = "/api/devices/register"
NEXT_PATH = "/api/devices/{device_id}/jobs/next"
RESULT_PATH = "/api/devices/{device_id}/jobs/result"

AGENT_ROLE_VALUE = "raspberrypi-agent"
AGENT_COMMAND_NAME = "agent_instruction"


def _console(message: str) -> None:
    """Emit a human-readable status line to the terminal."""

    try:
        print(f"[agent] {message}", flush=True)
    except Exception:  # pragma: no cover - printing should never fail, but stay safe
        pass


def _build_url(path: str) -> str:
    return f"{SERVER_BASE_URL}{path}"


def _format_for_log(value: Any, *, max_length: int = 500) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    except TypeError:
        text = repr(value)

    if len(text) > max_length:
        return text[: max_length - 20] + "...<truncated>"
    return text


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


def _log_dict(label: str, value: Dict[str, Any], *, level: int = logging.INFO) -> None:
    try:
        message = json.dumps(value, ensure_ascii=False, sort_keys=True)
    except TypeError:
        message = repr(value)
    logging.log(level, "%s: %s", label, message)


__all__ = [
    "AGENT_COMMAND_NAME",
    "AGENT_ROLE_VALUE",
    "AUTO_APPROVE",
    "AUTO_REGISTRATION_REQUESTED",
    "CAMERA_SAVE_DIR",
    "CAMERA_WARMUP_SECONDS",
    "DEVICE_ID_ENV",
    "DEVICE_ID_PATH",
    "DEVICE_TEST_DIR",
    "DISPLAY_NAME",
    "LLAMA_CONTEXT",
    "LLAMA_TEMPERATURE",
    "LLAMA_THREADS",
    "LOCATION",
    "MODEL_PATH",
    "NEXT_PATH",
    "OPEN_WEATHER_API_KEY",
    "OPEN_WEATHER_BASE_URL",
    "POLL_INTERVAL",
    "REGISTER_PATH",
    "REQUEST_TIMEOUT",
    "RESULT_PATH",
    "SERVER_BASE_URL",
    "_build_url",
    "_console",
    "_format_for_log",
    "_load_device_id",
    "_log_dict",
]
