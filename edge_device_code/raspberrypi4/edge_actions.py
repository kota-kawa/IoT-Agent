#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Action definitions and implementations for the Raspberry Pi edge agent."""

import argparse
import base64
import json
import logging
import math
import os
import random
import re
import shlex
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import requests

import edge_config as config

# Pi がネイティブにサポートするアクション定義
SUPPORTED_ACTIONS: Dict[str, Dict[str, Any]] = {
    "get_current_time": {
        "description": "Return the current local time in ISO 8601 format.",
        "params": [],
    },
    "run_sequence": {
        "description": "Execute multiple actions sequentially (parallel requests fall back to sequential for safety).",
        "params": [
            {
                "name": "commands",
                "type": "array",
                "required": True,
                "description": "List of commands to run. Each entry should include 'name' and optional 'args' dict.",
            },
            {
                "name": "mode",
                "type": "string",
                "required": False,
                "description": "Requested execution mode: 'sequential' or 'parallel' (parallel currently runs sequentially).",
            },
            {
                "name": "timeout",
                "type": "number",
                "required": False,
                "description": "Overall timeout in seconds for the full sequence.",
            },
        ],
    },
    "play_buzzer": {
        "description": "Play tones on the passive buzzer wired to GPIO4. Can play specific notes, sequences, or predefined melodies.",
        "params": [
            {
                "name": "melody",
                "type": "string",
                "required": False,
                "description": "Name of a predefined melody to play (e.g., 'success', 'error', 'alert', 'startup', 'mario'). Overrides sequence/note if provided.",
            },
            {
                "name": "sequence",
                "type": "array",
                "required": False,
                "description": "List of tone entries, each with 'note' (e.g. 'A4') and optional 'duration' in seconds.",
            },
            {
                "name": "note",
                "type": "string",
                "required": False,
                "description": "Single tone name to play when no sequence is supplied.",
            },
            {
                "name": "duration",
                "type": "number",
                "required": False,
                "description": "Duration in seconds for the single note provided by 'note'. Defaults to 1.0 second.",
            },
        ],
    },
    "operate_dc_motors": {
        "description": "Control the dual DC motors connected via the L293D driver. Supports direction, speed, and duration.",
        "params": [
            {
                "name": "command",
                "type": "string",
                "required": False,
                "description": "Movement command: 'forward', 'backward', 'left', 'right', 'stop', or 'demo' (default).",
            },
            {
                "name": "speed",
                "type": "number",
                "required": False,
                "description": "Motor speed from 0.0 to 1.0 (default 1.0).",
            },
            {
                "name": "duration",
                "type": "number",
                "required": False,
                "description": "Duration in seconds to run the motors (default 5.0 for movements).",
            },
            {
                "name": "timeout",
                "type": "number",
                "required": False,
                "description": "Overall timeout in seconds.",
            }
        ],
    },
    "display_robot_animation": {
        "description": "Show a robot-style 'mono-eye' animation on the connected ST7735 OLED display. Can also display a custom text message over the animation.",
        "params": [
            {
                "name": "timeout",
                "type": "number",
                "required": False,
                "description": "Optional timeout in seconds before stopping the animation.",
            },
            {
                "name": "text",
                "type": "string",
                "required": False,
                "description": "Optional English text to draw under the mono-eye.",
            },
            {
                "name": "duration",
                "type": "number",
                "required": False,
                "description": "Seconds to show the requested text/motion (defaults to a short burst).",
            },
            {
                "name": "motion",
                "type": "string",
                "required": False,
                "description": "Mono-eye motion preset: 'default' (only option available).",
            }
        ],
    },
    "control_single_servo": {
        "description": "Control a single servo motor. Supports various subcommands like setting a specific angle, centering, sweeping between angles, or turning it off.",
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
                "description": "Servo channel (1-2).",
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
    "control_specific_servo": {
        "description": "Control an individual servo channel (1-2) with the same options as control_single_servo.",
        "params": [
            {
                "name": "servo_id",
                "type": "integer",
                "required": True,
                "description": "Servo channel number (1-2).",
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
        "description": "Capture a still image using the attached Picamera2 module and return it as a base64 encoded string. The image is also saved to a local directory.",
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
    "operate_led_pattern": {
        "description": "Run a predefined light pattern on the three LEDs connected to GPIO pins.",
        "params": [
            {
                "name": "pattern",
                "type": "string",
                "required": False,
                "description": "Pattern to run: 'chase', 'blink_all', 'all_on', 'random', 'breathing', 'police', or 'demo' (default).",
            },
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
    "control_specific_led": {
        "description": "Control a specific LED (1, 2, or 3) individually.",
        "params": [
            {
                "name": "led_id",
                "type": "integer",
                "required": True,
                "description": "ID of the LED to control (1, 2, or 3).",
            },
            {
                "name": "command",
                "type": "string",
                "required": False,
                "description": "Command: 'on', 'off', or 'blink' (default 'on').",
            },
            {
                "name": "duration",
                "type": "number",
                "required": False,
                "description": "Duration in seconds to keep the state (default 5.0).",
            },
             {
                "name": "blink_rate",
                "type": "number",
                "required": False,
                "description": "Blinks per second if command is 'blink'.",
            },
        ],
    },
    "control_all_leds": {
        "description": "Control all LEDs together (on/off/blink).",
        "params": [
            {
                "name": "command",
                "type": "string",
                "required": False,
                "description": "Command: 'on', 'off', or 'blink' (default 'off').",
            },
            {
                "name": "duration",
                "type": "number",
                "required": False,
                "description": "Duration in seconds to hold the state (0 or negative keeps state until another command).",
            },
            {
                "name": "blink_rate",
                "type": "number",
                "required": False,
                "description": "Blinks per second if command is 'blink'.",
            },
        ],
    },
    "control_specific_dc_motor": {
        "description": "Control a specific DC motor (1 or 2) individually.",
        "params": [
             {
                "name": "motor_id",
                "type": "integer",
                "required": True,
                "description": "ID of the motor to control (1 or 2).",
            },
            {
                "name": "command",
                "type": "string",
                "required": False,
                "description": "Command: 'forward', 'backward', or 'stop' (default 'forward').",
            },
            {
                "name": "speed",
                "type": "number",
                "required": False,
                "description": "Speed from 0.0 to 1.0 (default 1.0).",
            },
            {
                "name": "duration",
                "type": "number",
                "required": False,
                "description": "Duration in seconds (default 5.0).",
            },
        ],
    },
    "control_dual_servos": {
        "description": "Control two servo motors simultaneously.",
        "params": [
            {
                "name": "action",
                "type": "string",
                "required": False,
                "description": "High-level action/gesture: 'nod', 'shake', 'happy', 'synced_sweep', or 'demo'.",
            },
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
    "led1_on": {
        "description": "Turn LED1 on for a duration (default 5s).",
        "params": [
            {"name": "duration", "type": "number", "required": False, "description": "Seconds to keep LED1 on."},
            {"name": "timeout", "type": "number", "required": False},
        ],
    },
    "led1_off": {
        "description": "Turn LED1 off immediately.",
        "params": [
            {"name": "timeout", "type": "number", "required": False},
        ],
    },
    "led2_on": {
        "description": "Turn LED2 on for a duration (default 5s).",
        "params": [
            {"name": "duration", "type": "number", "required": False, "description": "Seconds to keep LED2 on."},
            {"name": "timeout", "type": "number", "required": False},
        ],
    },
    "led2_off": {
        "description": "Turn LED2 off immediately.",
        "params": [
            {"name": "timeout", "type": "number", "required": False},
        ],
    },
    "led3_on": {
        "description": "Turn LED3 on for a duration (default 5s).",
        "params": [
            {"name": "duration", "type": "number", "required": False, "description": "Seconds to keep LED3 on."},
            {"name": "timeout", "type": "number", "required": False},
        ],
    },
    "led3_off": {
        "description": "Turn LED3 off immediately.",
        "params": [
            {"name": "timeout", "type": "number", "required": False},
        ],
    },
    "servo1_control": {
        "description": "Control servo channel 1 with the same options as control_specific_servo.",
        "params": [
            {"name": "mode", "type": "string", "required": False, "description": "set/center/off/sweep/info"},
            {"name": "angle", "type": "number", "required": False},
            {"name": "start", "type": "number", "required": False},
            {"name": "end", "type": "number", "required": False},
            {"name": "step", "type": "number", "required": False},
            {"name": "delay", "type": "number", "required": False},
            {"name": "cycles", "type": "integer", "required": False},
            {"name": "pigpio", "type": "boolean", "required": False},
            {"name": "hold", "type": "number", "required": False},
            {"name": "timeout", "type": "number", "required": False},
        ],
    },
    "servo2_control": {
        "description": "Control servo channel 2 with the same options as control_specific_servo.",
        "params": [
            {"name": "mode", "type": "string", "required": False, "description": "set/center/off/sweep/info"},
            {"name": "angle", "type": "number", "required": False},
            {"name": "start", "type": "number", "required": False},
            {"name": "end", "type": "number", "required": False},
            {"name": "step", "type": "number", "required": False},
            {"name": "delay", "type": "number", "required": False},
            {"name": "cycles", "type": "integer", "required": False},
            {"name": "pigpio", "type": "boolean", "required": False},
            {"name": "hold", "type": "number", "required": False},
            {"name": "timeout", "type": "number", "required": False},
        ],
    },
    "dc_motor1_control": {
        "description": "Control DC motor 1 with forward/backward/stop commands.",
        "params": [
            {"name": "command", "type": "string", "required": False, "description": "forward/backward/stop"},
            {"name": "speed", "type": "number", "required": False},
            {"name": "duration", "type": "number", "required": False},
            {"name": "timeout", "type": "number", "required": False},
        ],
    },
    "dc_motor2_control": {
        "description": "Control DC motor 2 with forward/backward/stop commands.",
        "params": [
            {"name": "command", "type": "string", "required": False, "description": "forward/backward/stop"},
            {"name": "speed", "type": "number", "required": False},
            {"name": "duration", "type": "number", "required": False},
            {"name": "timeout", "type": "number", "required": False},
        ],
    },
    "move_right_hand": {
        "description": "Control the Right Hand (Servo 1).",
        "params": [
            {"name": "mode", "type": "string", "required": False, "description": "set/center/off/sweep/info"},
            {"name": "angle", "type": "number", "required": False, "description": "Angle (0-180)"},
            {"name": "start", "type": "number", "required": False},
            {"name": "end", "type": "number", "required": False},
            {"name": "step", "type": "number", "required": False},
            {"name": "delay", "type": "number", "required": False},
            {"name": "cycles", "type": "integer", "required": False},
            {"name": "pigpio", "type": "boolean", "required": False},
            {"name": "hold", "type": "number", "required": False},
            {"name": "timeout", "type": "number", "required": False},
        ],
    },
    "move_left_hand": {
        "description": "Control the Left Hand (Servo 2).",
        "params": [
            {"name": "mode", "type": "string", "required": False, "description": "set/center/off/sweep/info"},
            {"name": "angle", "type": "number", "required": False, "description": "Angle (0-180)"},
            {"name": "start", "type": "number", "required": False},
            {"name": "end", "type": "number", "required": False},
            {"name": "step", "type": "number", "required": False},
            {"name": "delay", "type": "number", "required": False},
            {"name": "cycles", "type": "integer", "required": False},
            {"name": "pigpio", "type": "boolean", "required": False},
            {"name": "hold", "type": "number", "required": False},
            {"name": "timeout", "type": "number", "required": False},
        ],
    },
    "move_left_leg": {
        "description": "Control the Left Leg (DC Motor 1).",
        "params": [
            {"name": "command", "type": "string", "required": False, "description": "forward/backward/stop"},
            {"name": "speed", "type": "number", "required": False},
            {"name": "duration", "type": "number", "required": False},
            {"name": "timeout", "type": "number", "required": False},
        ],
    },
    "move_right_leg": {
        "description": "Control the Right Leg (DC Motor 2).",
        "params": [
            {"name": "command", "type": "string", "required": False, "description": "forward/backward/stop"},
            {"name": "speed", "type": "number", "required": False},
            {"name": "duration", "type": "number", "required": False},
            {"name": "timeout", "type": "number", "required": False},
        ],
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
        "name": config.AGENT_COMMAND_NAME,
        "description": "Execute Raspberry Pi automation tasks derived from simple English instructions.",
        "params": [
            {"name": "instruction", "type": "string", "required": True},
        ],
    },
    *ACTION_CATALOG,
]


_DIGIT_NORMALIZATION = str.maketrans("０１２３４５６７８９", "0123456789")


def _normalize_digits(text: str) -> str:
    return text.translate(_DIGIT_NORMALIZATION)


_DEFAULT_MOTOR_TEST_TIMEOUT = 60.0
_DEFAULT_OLED_DEMO_TIMEOUT = 60.0
_DEFAULT_SERVO_TIMEOUT = 60.0
_DEFAULT_LED_TIMEOUT = 60.0
_DEFAULT_DUAL_SERVO_TIMEOUT = 60.0
_DEFAULT_SEQUENCE_TIMEOUT = 120.0
_OLED_RESULT_RETURN_SECONDS = 3.0
_OLED_SPI_PORT = 1
_OLED_SPI_DEVICE = 0
_OLED_PIN_DC = 26
_OLED_PIN_RST = 6
_OLED_PIN_BL = 13
_OLED_BUS_HZ = 16_000_000

_OLED_WIDTH = 160
_OLED_HEIGHT = 128
_OLED_ROTATE = 0
_OLED_BGR = False
_OLED_H_OFFSET = 0
_OLED_V_OFFSET = 0

_OLED_COL_BG = (6, 18, 10)
_OLED_COL_HEAD = (20, 44, 26)
_OLED_COL_VISOR = (10, 12, 16)
_OLED_COL_FRAME = (90, 150, 96)
_OLED_COL_TRACK = (24, 36, 30)
_OLED_COL_GLOW_SOFT = (82, 20, 44)
_OLED_COL_GLOW = (200, 54, 104)
_OLED_COL_GLOW_CORE = (255, 148, 196)
_OLED_COL_BEAM = (120, 210, 170)
_OLED_COL_TEXT = (225, 240, 228)
_OLED_COL_TEXT_BG = (12, 26, 16)
_OLED_COL_TEXT_BORDER = (60, 120, 74)
_OLED_COL_ACCENT = (36, 76, 46)

_OLED_FPS = 50
_OLED_TEXT_PANEL_HEIGHT = 34
_OLED_EYE_SWEEP = 0.48
_OLED_TRACK_PADDING = 18
_OLED_TEXT_PADDING = 18
_OLED_BACKLIGHT_STEPS = (20, 40, 60, 80, 100)
_OLED_TEXT_DURATION_LIMIT = 60.0
_OLED_TEXT_MIN_DURATION = 1.5
_OLED_MOTION_ALIASES: Dict[str, List[str]] = {
    "default": ["default", "normal", "通常"],
}

_oled_backlight_singleton: Optional[Any] = None
_oled_backlight_lock = threading.Lock()


def _parse_timeout_parameter(parameters: Any, default: float) -> float:
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


def _normalize_motion_mode(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None

    raw = value.strip()
    if not raw:
        return None

    lowered = raw.lower()
    collapsed = re.sub(r"[\s_]+", "", lowered)
    ascii_only = re.sub(r"[^a-z]", "", collapsed)

    for candidate in (lowered, collapsed, ascii_only):
        if candidate in _OLED_MOTION_PRESETS:
            return candidate

    for mode_key, keywords in _OLED_MOTION_ALIASES.items():
        if any(keyword in lowered for keyword in keywords):
            return mode_key
        if any(keyword in collapsed for keyword in keywords):
            return mode_key
        if any(keyword in ascii_only for keyword in keywords):
            return mode_key

    return None


def _infer_motion_from_text(text: Optional[str]) -> Optional[str]:
    if not isinstance(text, str):
        return None
    return _normalize_motion_mode(text)


def _oled_ensure_spidev_exists() -> None:
    path = f"/dev/spidev{_OLED_SPI_PORT}.{_OLED_SPI_DEVICE}"
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"{path} が見つかりません。/boot(または /boot/firmware)/config.txt に 'dtoverlay=spi1-1cs' を追記して再起動してください。"
        )


def _oled_setup_backlight() -> Any:
    from gpiozero import PWMLED

    global _oled_backlight_singleton
    with _oled_backlight_lock:
        if _oled_backlight_singleton is not None:
            return _oled_backlight_singleton
        _oled_backlight_singleton = PWMLED(_OLED_PIN_BL, frequency=1000, active_high=True, initial_value=1.0)
        return _oled_backlight_singleton


def _oled_set_backlight_percent(backlight: Any, percent: float) -> None:
    value = max(0.0, min(100.0, percent)) / 100.0
    backlight.value = value


def _oled_create_device() -> Any:
    from luma.core.interface.serial import spi
    from luma.lcd.device import st7735

    serial_if = spi(
        port=_OLED_SPI_PORT,
        device=_OLED_SPI_DEVICE,
        gpio_DC=_OLED_PIN_DC,
        gpio_RST=_OLED_PIN_RST,
        bus_speed_hz=_OLED_BUS_HZ,
    )
    return st7735(
        serial_interface=serial_if,
        width=_OLED_WIDTH,
        height=_OLED_HEIGHT,
        rotate=_OLED_ROTATE,
        bgr=_OLED_BGR,
        h_offset=_OLED_H_OFFSET,
        v_offset=_OLED_V_OFFSET,
    )


def _oled_load_font() -> Any:
    from PIL import ImageFont

    # Attempt to load a TrueType font (size 25)
    # Common locations for fonts on Raspberry Pi OS / Linux
    font_candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
        "DejaVuSans.ttf",
    ]

    for font_path in font_candidates:
        try:
            return ImageFont.truetype(font_path, 25)
        except (OSError, ImportError):
            continue

    logging.warning("Could not load any TrueType fonts (size 25). Falling back to small default font.")
    return ImageFont.load_default()


def _oled_text_width(font: Any, text: str) -> int:
    if hasattr(font, "getbbox"):
        bbox = font.getbbox(text)
        return bbox[2] - bbox[0]
    return font.getsize(text)[0]


def _oled_text_line_height(font: Any) -> int:
    if hasattr(font, "getbbox"):
        return (font.getbbox("Ag")[3] - font.getbbox("Ag")[1]) + 2
    return font.getsize("Ag")[1] + 2


def _oled_wrap_text_lines(text: str, font: Any, max_width: int) -> List[str]:
    if not text:
        return []
    lines: List[str] = []
    current = ""
    words = text.split()
    for word in words:
        candidate = word if not current else f"{current} {word}"
        if _oled_text_width(font, candidate) <= max_width:
            current = candidate
            continue
        if current:
            lines.append(current)
        chunk = ""
        for char in word:
            candidate_chunk = f"{chunk}{char}"
            if _oled_text_width(font, candidate_chunk) <= max_width:
                chunk = candidate_chunk
            else:
                if chunk:
                    lines.append(chunk)
                chunk = char
        current = chunk
    if current:
        lines.append(current)
    return lines[:3]


def _oled_draw_text_screen(draw: Any, font: Any, lines: List[str], width: int, height: int) -> None:
    draw.rectangle((0, 0, width, height), fill=_OLED_COL_TEXT_BG, outline=_OLED_COL_TEXT_BORDER, width=1)
    line_height = _oled_text_line_height(font)
    total_height = len(lines) * line_height
    y = max(8, (height - total_height) // 2)
    for line in lines:
        x = max(8, (width - _oled_text_width(font, line)) // 2)
        draw.text((x, y), line, fill=_OLED_COL_TEXT, font=font)
        y += line_height


def _oled_draw_mono_eye(
    draw: Any,
    t: float,
    width: int,
    height: int,
    panel_height: int,
    motion_profile: Optional[Dict[str, float]] = None,
) -> None:
    head_bottom = height - panel_height
    draw.rectangle((0, 0, width, height), fill=_OLED_COL_BG)
    draw.rectangle((0, 0, width, head_bottom), fill=_OLED_COL_HEAD)

    visor_left = 10
    visor_right = width - 10
    visor_top = 18
    visor_bottom = head_bottom - 8
    draw.rectangle((visor_left, visor_top, visor_right, visor_bottom), outline=_OLED_COL_FRAME, fill=_OLED_COL_VISOR, width=2)

    ridge_height = 8
    ridge_y = visor_top - ridge_height
    draw.rectangle((visor_left + 6, ridge_y, visor_right - 6, visor_top + 2), fill=_OLED_COL_HEAD, outline=_OLED_COL_ACCENT, width=1)

    track_left = visor_left + _OLED_TRACK_PADDING
    track_right = visor_right - _OLED_TRACK_PADDING
    track_mid_y = (visor_top + visor_bottom) // 2
    motion = motion_profile or _OLED_MOTION_PRESETS["default"]
    speed = motion.get("speed", 1.0)
    t_scaled = t * speed
    eye_scale = motion.get("eye_scale", 3.0)
    track_height = int(20 * eye_scale)
    track_top = track_mid_y - track_height // 2
    track_bottom = track_mid_y + track_height // 2
    draw.rectangle((track_left, track_top, track_right, track_bottom), fill=_OLED_COL_TRACK, outline=_OLED_COL_FRAME, width=1)

    beam_speed = motion.get("beam_speed", 60.0)
    beam_phase = (t_scaled * beam_speed) % (track_right - track_left)
    beam_x = track_left + beam_phase
    if beam_x < track_right:
        draw.line((beam_x, visor_top + 4, beam_x, visor_bottom - 4), fill=_OLED_COL_BEAM, width=2)

    glow_r = int(16 * eye_scale)
    eye_r = int(10 * eye_scale)
    core_r = int(6 * eye_scale)
    eye_sweep = motion.get("sweep", _OLED_EYE_SWEEP)
    eye_band = ((math.sin(t_scaled * 1.2) * eye_sweep) + (math.sin(t_scaled * 0.37) * 0.22)) * 0.5 + 0.5
    eye_min_x = visor_left + glow_r + 2
    eye_max_x = visor_right - glow_r - 2
    if eye_min_x >= eye_max_x:
        eye_x = (visor_left + visor_right) // 2
    else:
        eye_x = int(eye_min_x + eye_band * (eye_max_x - eye_min_x))
    bob_speed = motion.get("bob_speed", 0.7)
    bob_amplitude = motion.get("bob_amplitude", 3.0)
    eye_y = track_mid_y + int(math.sin(t_scaled * bob_speed) * bob_amplitude)
    eye_y = max(visor_top + glow_r, min(visor_bottom - glow_r, eye_y))

    draw.ellipse((eye_x - glow_r, eye_y - glow_r, eye_x + glow_r, eye_y + glow_r), fill=_OLED_COL_GLOW_SOFT)
    draw.ellipse((eye_x - eye_r, eye_y - eye_r, eye_x + eye_r, eye_y + eye_r), fill=_OLED_COL_GLOW, outline=_OLED_COL_FRAME, width=1)
    draw.ellipse((eye_x - core_r, eye_y - core_r, eye_x + core_r, eye_y + core_r), fill=_OLED_COL_GLOW_CORE)
    draw.line((eye_x - eye_r - 6, eye_y - core_r // 2, eye_x + eye_r + 6, eye_y + core_r // 2), fill=_OLED_COL_GLOW_CORE, width=1)

    meter_height = 20
    meter_width = 6
    meter_x = visor_left + 6
    meter_y = visor_bottom - meter_height - 6
    meter_value = int((math.sin(t * 1.5) * 0.5 + 0.5) * (meter_height - 4))
    draw.rectangle((meter_x, meter_y, meter_x + meter_width, meter_y + meter_height), outline=_OLED_COL_FRAME, width=1)
    draw.rectangle((meter_x + 1, meter_y + meter_height - meter_value, meter_x + meter_width - 1, meter_y + meter_height - 2), fill=_OLED_COL_ACCENT)

    right_meter_x = visor_right - meter_width - 6
    draw.rectangle((right_meter_x, meter_y, right_meter_x + meter_width, meter_y + meter_height), outline=_OLED_COL_FRAME, width=1)
    draw.rectangle(
        (right_meter_x + 1, meter_y + meter_height - meter_value, right_meter_x + meter_width - 1, meter_y + meter_height - 2),
        fill=_OLED_COL_ACCENT,
    )


class _ActionExecutionContext:
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


class _MonoEyeDisplayManager:
    def __init__(self) -> None:
        self.device: Any = None
        self.backlight: Any = None
        self.font: Any = None
        self.thread: Optional[threading.Thread] = None
        self.stop_event = threading.Event()
        self.lock = threading.Lock()
        self.text_lines: List[str] = []
        self.text_until = 0.0
        self.motion_mode = "default"
        self.motion_until = 0.0
        self.started_at = 0.0
        self.last_error: Optional[str] = None

    @property
    def is_running(self) -> bool:
        return bool(self.thread and self.thread.is_alive() and self.device is not None)

    def start(self) -> bool:
        if self.is_running:
            return True

        try:
            _oled_ensure_spidev_exists()
            device = _oled_create_device()
            backlight = _oled_setup_backlight()
            font = _oled_load_font()
        except Exception as exc:  # pragma: no cover - hardware dependent
            self.last_error = str(exc)
            logging.warning("OLED mono-eye display could not start: %s", exc)
            return False

        self.device = device
        self.backlight = backlight
        self.font = font
        self.last_error = None
        self.stop_event.clear()
        self.thread = threading.Thread(
            target=self._run_loop,
            name="oled-mono-eye",
            daemon=True,
        )
        self.thread.start()
        return True

    def show_text(self, text: str, duration_seconds: float) -> Dict[str, Any]:
        if not text.strip():
            return {"ok": False, "error": "Text must be a non-empty string."}

        if not self.is_running or self.device is None or self.font is None:
            return {"ok": False, "error": self.last_error or "OLED display is not running."}

        clamped_duration = max(_OLED_TEXT_MIN_DURATION, min(_OLED_TEXT_DURATION_LIMIT, duration_seconds))
        lines = _oled_wrap_text_lines(text, self.font, self.device.width - _OLED_TEXT_PADDING)
        if not lines:
            return {"ok": False, "error": "Unable to render empty text payload."}

        with self.lock:
            self.text_lines = lines
            self.text_until = time.monotonic() + clamped_duration

        return {
            "ok": True,
            "lines": lines,
            "duration_seconds": clamped_duration,
            "until": self.text_until,
        }

    def set_motion_mode(self, mode: str, duration_seconds: float) -> Dict[str, Any]:
        mode_key = _normalize_motion_mode(mode)
        if mode_key is None or mode_key not in _OLED_MOTION_PRESETS:
            return {
                "ok": False,
                "error": f"Unsupported mono-eye mode '{mode_key}'. Available: {', '.join(sorted(_OLED_MOTION_PRESETS))}",
            }

        clamped_duration = max(_OLED_TEXT_MIN_DURATION, min(_OLED_TEXT_DURATION_LIMIT, duration_seconds))
        with self.lock:
            self.motion_mode = mode_key
            self.motion_until = time.monotonic() + clamped_duration if mode_key != "default" else 0.0

        return {
            "ok": True,
            "mode": mode_key,
            "duration_seconds": clamped_duration,
            "until": self.motion_until,
        }

    def _run_loop(self) -> None:  # pragma: no cover - hardware dependent
        try:
            from luma.core.render import canvas
        except Exception as exc:
            self.last_error = str(exc)
            logging.warning("Failed to import luma canvas for OLED: %s", exc)
            return

        if self.device is None:
            self.last_error = "OLED device not initialized."
            return

        self.started_at = time.monotonic()
        frame_delay = 1.0 / _OLED_FPS

        for duty in _OLED_BACKLIGHT_STEPS:
            try:
                if self.backlight is not None:
                    _oled_set_backlight_percent(self.backlight, duty)
                with canvas(self.device) as draw:
                    _oled_draw_mono_eye(draw, 0.0, self.device.width, self.device.height, 0, _OLED_MOTION_PRESETS["default"])
                time.sleep(0.05)
            except Exception as exc:
                self.last_error = str(exc)
                logging.warning("OLED backlight warm-up error (continuing): %s", exc)
                # Continue despite error during warmup

        logging.info("OLED mono-eye loop started.")

        while not self.stop_event.is_set():
            try:
                now = time.monotonic()
                with self.lock:
                    active_text = bool(self.text_lines) and now < self.text_until
                    if not active_text and self.text_lines:
                        self.text_lines = []
                        self.text_until = 0.0
                    motion_mode = self.motion_mode
                    motion_until = self.motion_until
                    if motion_mode != "default" and motion_until and now >= motion_until:
                        self.motion_mode = "default"
                        self.motion_until = 0.0
                        motion_mode = "default"
                    text_lines = list(self.text_lines) if active_text else []

                with canvas(self.device) as draw:
                    if active_text:
                        _oled_draw_text_screen(draw, self.font, text_lines, self.device.width, self.device.height)
                    else:
                        motion_profile = _OLED_MOTION_PRESETS.get(motion_mode, _OLED_MOTION_PRESETS["default"])
                        _oled_draw_mono_eye(draw, now - self.started_at, self.device.width, self.device.height, 0, motion_profile)

                time.sleep(frame_delay)
            except Exception as exc:
                self.last_error = str(exc)
                # Log error but do not stop the loop
                logging.error("OLED mono-eye loop error (retrying in 1s): %s", exc)
                time.sleep(1.0)


_mono_eye_display_manager: Optional[_MonoEyeDisplayManager] = None
_oled_daemon_logged = False


def _get_or_start_oled_manager() -> Optional[_MonoEyeDisplayManager]:
    global _mono_eye_display_manager

    if _mono_eye_display_manager and _mono_eye_display_manager.is_running:
        return _mono_eye_display_manager

    manager = _MonoEyeDisplayManager()
    if manager.start():
        _mono_eye_display_manager = manager
        return manager
    return None


def _start_mono_eye_daemon_if_possible() -> None:
    global _oled_daemon_logged
    manager = _get_or_start_oled_manager()
    if manager and manager.is_running and not _oled_daemon_logged:
        logging.info("OLED mono-eye display started in background.")
        config._console("OLED mono-eye display is running in the background.")
        _oled_daemon_logged = True
    elif manager is None and not _oled_daemon_logged:
        logging.info("OLED mono-eye display not started (missing hardware drivers or SPI not enabled).")
        _oled_daemon_logged = True


_LED_PINS = {"led1": 2, "led2": 3, "led3": 16}
_BUZZER_PIN = 4
_DEFAULT_BUZZER_SEQUENCE = [
    {"note": "A4", "duration": 1.0},
    {"note": "C5", "duration": 1.0},
]

_BUZZER_MELODIES: Dict[str, List[Dict[str, Any]]] = {
    "success": [
        {"note": "C5", "duration": 0.1},
        {"note": "E5", "duration": 0.1},
        {"note": "G5", "duration": 0.2},
        {"note": "C6", "duration": 0.4},
    ],
    "error": [
        {"note": "C4", "duration": 0.2},
        {"note": "G3", "duration": 0.2},
        {"note": "C3", "duration": 0.4},
    ],
    "alert": [
        {"note": "A5", "duration": 0.1},
        {"note": "A5", "duration": 0.1},
        {"note": "A5", "duration": 0.1},
    ],
    "startup": [
        {"note": "C5", "duration": 0.15},
        {"note": "E5", "duration": 0.15},
        {"note": "G5", "duration": 0.15},
    ],
    "mario": [
         {"note": "E5", "duration": 0.1},
         {"note": "E5", "duration": 0.1},
         {"note": "E5", "duration": 0.1},
         {"note": "C5", "duration": 0.1},
         {"note": "E5", "duration": 0.1},
         {"note": "G5", "duration": 0.2},
         {"note": "G4", "duration": 0.2},
    ]
}


def _parse_buzzer_duration(value: Any, default: float) -> float:
    if value is None:
        return default
    if isinstance(value, (int, float)):
        duration_value = float(value)
    elif isinstance(value, str) and value.strip():
        try:
            duration_value = float(value.strip())
        except ValueError as exc:  # pragma: no cover - defensive
            raise ValueError("Buzzer duration must be a number of seconds.") from exc
    else:
        raise ValueError("Buzzer duration must be a number of seconds.")

    if duration_value <= 0:
        raise ValueError("Buzzer duration must be greater than zero.")
    return duration_value


def _extract_buzzer_note(value: Any) -> Optional[str]:
    if isinstance(value, str):
        candidate = value.strip()
        if candidate:
            return candidate
    return None


def _normalize_buzzer_sequence(parameters: Any) -> List[Dict[str, Any]]:
    normalized: List[Dict[str, Any]] = []

    def _copy_default() -> List[Dict[str, Any]]:
        return [{"note": entry["note"], "duration": entry["duration"]} for entry in _DEFAULT_BUZZER_SEQUENCE]

    if not isinstance(parameters, dict):
        return _copy_default()

    # Check for melody first
    melody_name = parameters.get("melody")
    if isinstance(melody_name, str) and melody_name.strip():
        melody_key = melody_name.strip().lower()
        if melody_key in _BUZZER_MELODIES:
            return [
                {"note": entry["note"], "duration": entry["duration"]}
                for entry in _BUZZER_MELODIES[melody_key]
            ]
        # If unknown melody, fall through to sequence/note or default

    seq_value = parameters.get("sequence")
    if isinstance(seq_value, list):
        for index, item in enumerate(seq_value, start=1):
            if isinstance(item, dict):
                note_value = _extract_buzzer_note(item.get("note"))
                if not note_value:
                    raise ValueError(f"Buzzer sequence entry {index} must include a note string.")
                duration_value = _parse_buzzer_duration(item.get("duration"), 1.0)
            else:
                note_value = _extract_buzzer_note(item)
                if not note_value:
                    raise ValueError(f"Buzzer sequence entry {index} is not a valid note string.")
                duration_value = 1.0
            normalized.append({"note": note_value, "duration": duration_value})

    if not normalized:
        note_value = _extract_buzzer_note(parameters.get("note"))
        if note_value:
            duration_value = _parse_buzzer_duration(parameters.get("duration"), 1.0)
            normalized.append({"note": note_value, "duration": duration_value})

    if not normalized:
        normalized = _copy_default()

    return normalized


def _play_buzzer(parameters: Any) -> Dict[str, Any]:
    sequence = _normalize_buzzer_sequence(parameters)
    try:
        from gpiozero import TonalBuzzer
    except ImportError as exc:
        raise RuntimeError(
            "gpiozero (TonalBuzzer) is required to play tones on the buzzer. Install it on the Raspberry Pi."
        ) from exc

    buzzer = TonalBuzzer(_BUZZER_PIN)
    played_sequence: List[Dict[str, Any]] = []
    total_duration = 0.0
    try:
        for entry in sequence:
            note = entry["note"]
            duration = entry["duration"]
            buzzer.play(note)
            time.sleep(duration)
            buzzer.stop()
            played_sequence.append({"note": note, "duration": round(duration, 3)})
            total_duration += duration
    finally:
        buzzer.stop()
        buzzer.close()

    return {
        "sequence": played_sequence,
        "pins": {"buzzer": _BUZZER_PIN},
        "duration_seconds": round(total_duration, 3),
    }


def _capture_camera_photo(parameters: Any) -> Dict[str, Any]:
    try:
        from picamera2 import Picamera2
    except ImportError as exc:
        raise RuntimeError(
            "picamera2 is required to capture photos on the Raspberry Pi. Install it before running this action."
        ) from exc

    save_dir = config.CAMERA_SAVE_DIR
    warmup = config.CAMERA_WARMUP_SECONDS
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

    max_retries = 3
    last_error = None
    duration = 0

    for attempt in range(max_retries):
        picam2 = None
        try:
            picam2 = Picamera2()
            picam2.configure(picam2.create_still_configuration())

            started = time.monotonic()
            picam2.start()
            if warmup:
                time.sleep(warmup)
            picam2.capture_file(str(outfile))
            picam2.stop()
            duration = time.monotonic() - started
            # Explicitly close to release resources
            if hasattr(picam2, "close"):
                picam2.close()
            break
        except Exception as e:
            last_error = e
            print(f"Camera capture failed (attempt {attempt + 1}/{max_retries}): {e}")
            if picam2:
                try:
                    picam2.stop()
                except Exception:
                    pass
                try:
                    if hasattr(picam2, "close"):
                        picam2.close()
                except Exception:
                    pass
            
            if attempt < max_retries - 1:
                time.sleep(2)
            else:
                raise RuntimeError(
                    f"Failed to capture image after {max_retries} attempts. Last error: {last_error}"
                ) from last_error

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
        "summary": "周囲の様子を確認するために写真を1枚撮影しました。",
    }


def _run_sequence(parameters: Any) -> Dict[str, Any]:
    timeout = _parse_timeout_parameter(parameters, _DEFAULT_SEQUENCE_TIMEOUT)
    context = _ActionExecutionContext(timeout)

    requested_mode = "sequential"
    commands: List[Dict[str, Any]] = []
    if isinstance(parameters, dict):
        mode_value = parameters.get("mode")
        if isinstance(mode_value, str) and mode_value.strip():
            requested_mode = mode_value.strip().lower()

        commands_value = parameters.get("commands")
        if isinstance(commands_value, list):
            commands = [cmd for cmd in commands_value if isinstance(cmd, dict)]

    if not commands:
        raise ValueError("commands must be a non-empty list of command dictionaries.")

    executed_mode = "sequential"
    if requested_mode not in ("sequential", "parallel"):
        context.log("Unsupported sequence mode requested; falling back to sequential", requested_mode=requested_mode)
    elif requested_mode == "parallel":
        context.log("Parallel mode requested; executing sequentially for safety")

    context.log(
        "Starting action sequence",
        command_count=len(commands),
        requested_mode=requested_mode,
        executed_mode=executed_mode,
        timeout_seconds=timeout,
    )

    results: List[Dict[str, Any]] = []
    for index, command in enumerate(commands, start=1):
        if context.timed_out or (context.remaining() is not None and context.remaining() <= 0):
            context.timed_out = True
            break

        name = command.get("name") if isinstance(command, dict) else None
        args = command.get("args") if isinstance(command, dict) and isinstance(command.get("args"), dict) else {}

        if not isinstance(name, str) or not name.strip():
            results.append({"index": index, "ok": False, "error": "command name is required"})
            continue

        normalized_name = name.strip()
        if normalized_name == "run_sequence":
            results.append(
                {
                    "index": index,
                    "name": normalized_name,
                    "ok": False,
                    "error": "nested run_sequence is not supported on this device",
                }
            )
            continue

        context.log("Executing sub-action", index=index, name=normalized_name)
        ok, result, error = _execute_action(normalized_name, args)
        entry: Dict[str, Any] = {
            "index": index,
            "name": normalized_name,
            "ok": bool(ok),
            "result": result,
        }
        if error:
            entry["error"] = error
        results.append(entry)

    context.log(
        "Sequence finished",
        executed=len(results),
        timed_out=context.timed_out,
        executed_mode=executed_mode,
    )

    return {
        "results": results,
        "requested_mode": requested_mode,
        "executed_mode": executed_mode,
        "timed_out": context.timed_out,
        "duration_seconds": context.elapsed(),
    }


def _run_led_demo(parameters: Any) -> Dict[str, Any]:
    timeout = _parse_timeout_parameter(parameters, _DEFAULT_LED_TIMEOUT)
    context = _ActionExecutionContext(timeout)

    try:
        from gpiozero import PWMLED
    except ImportError as exc:
        raise RuntimeError(
            "gpiozero is required to drive the LEDs. Install it on the Raspberry Pi."
        ) from exc

    pattern = "demo"
    cycles = 2
    if isinstance(parameters, dict):
        if "pattern" in parameters and isinstance(parameters["pattern"], str):
            pattern = parameters["pattern"].strip().lower() or "demo"
        
        if "cycles" in parameters:
            try:
                cycles_value = int(parameters["cycles"])
            except (TypeError, ValueError) as exc:
                raise ValueError("cycles must be an integer (0 for continuous).") from exc
            if cycles_value < 0:
                raise ValueError("cycles must be zero or a positive integer.")
            cycles = cycles_value
        elif pattern != "demo":
            # Default to more cycles for specific patterns if not specified
            cycles = 10 if pattern in ("random", "police", "blink_all", "chase") else 3

    led1 = PWMLED(_LED_PINS["led1"])
    led2 = PWMLED(_LED_PINS["led2"])
    led3 = PWMLED(_LED_PINS["led3"])
    leds = [led1, led2, led3]

    def _clear():
        for led in leds:
            led.off()

    def chase(delay: float = 0.15) -> bool:
        _clear()
        for led in leds:
            led.on()
            if not context.sleep(delay):
                return False
            led.off()
        return True

    def all_on(delay: float = 0.4) -> bool:
        for led in leds:
            led.on()
        if not context.sleep(delay):
            return False
        _clear()
        return not context.timed_out and context.sleep(delay)

    def blink_all(delay: float = 0.07) -> bool:
        for led in leds:
            led.on()
        if not context.sleep(delay):
            return False
        _clear()
        return not context.timed_out and context.sleep(delay)

    def random_pattern(delay: float = 0.1) -> bool:
        for led in leds:
            led.value = random.choice([0, 1])
        if not context.sleep(delay):
            return False
        _clear()
        return True

    def police(delay: float = 0.2) -> bool:
        # LED1 & 3 vs LED2
        led1.on()
        led2.off()
        led3.on()
        if not context.sleep(delay):
            return False
        led1.off()
        led2.on()
        led3.off()
        return not context.timed_out and context.sleep(delay)

    def breathing(duration: float = 2.0) -> bool:
        steps = 20
        step_delay = duration / (steps * 2)
        # Up
        for i in range(steps + 1):
            val = (i / steps) ** 2  # quadratic for better perception
            for led in leds:
                led.value = val
            if not context.sleep(step_delay):
                return False
        # Down
        for i in range(steps, -1, -1):
            val = (i / steps) ** 2
            for led in leds:
                led.value = val
            if not context.sleep(step_delay):
                return False
        return True

    executed_cycles = 0
    context.log(
        "LED action start",
        pins=_LED_PINS,
        pattern=pattern,
        cycles=cycles if cycles > 0 else "until timeout",
        timeout_seconds=timeout,
    )

    try:
        while not context.timed_out:
            if cycles > 0 and executed_cycles >= cycles:
                break

            success = True
            if pattern == "chase":
                success = chase()
            elif pattern == "all_on":
                success = all_on()
            elif pattern == "blink_all":
                success = blink_all()
            elif pattern == "random":
                success = random_pattern()
            elif pattern == "police":
                success = police()
            elif pattern == "breathing":
                success = breathing()
            else: # demo
                for _ in range(4):
                    if not chase(): break
                if context.timed_out: break
                for _ in range(3):
                    if not all_on(): break
                if context.timed_out: break
                for _ in range(8):
                    if not blink_all(): break
            
            if not success or context.timed_out:
                break

            executed_cycles += 1
    finally:
        _clear()
        for led in leds:
            led.close()
        context.log("LED action finished", cycles_executed=executed_cycles, timed_out=context.timed_out)

    return {
        "events": context.events,
        "timed_out": context.timed_out,
        "duration_seconds": context.elapsed(),
        "cycles_executed": executed_cycles,
        "pattern": pattern,
        "pins": _LED_PINS,
    }


def _run_motor_test(parameters: Any) -> Dict[str, Any]:
    timeout = _parse_timeout_parameter(parameters, _DEFAULT_MOTOR_TEST_TIMEOUT)
    context = _ActionExecutionContext(timeout)

    command = "demo"
    speed = 1.0
    duration = 5.0

    if isinstance(parameters, dict):
        if "command" in parameters and isinstance(parameters["command"], str):
            command = parameters["command"].strip().lower() or "demo"
        if "speed" in parameters:
            try:
                speed = float(parameters["speed"])
                speed = max(0.0, min(1.0, speed))
            except (ValueError, TypeError):
                pass
        if "duration" in parameters:
            try:
                duration = float(parameters["duration"])
                duration = max(0.1, duration)
            except (ValueError, TypeError):
                pass

    try:
        from gpiozero import OutputDevice, PWMOutputDevice
    except ImportError as exc:
        raise RuntimeError(
            "gpiozero is required to control the motors. Install it on the Raspberry Pi."
        ) from exc

    motor1 = {"EN": 25, "IN1": 24, "IN2": 23}
    motor2 = {"EN": 17, "IN1": 27, "IN2": 22}

    context.log("Initializing L293D motor outputs", motor1=motor1, motor2=motor2)

    # EN pins as PWM for speed control
    en1 = PWMOutputDevice(motor1["EN"], active_high=True, initial_value=0)
    en2 = PWMOutputDevice(motor2["EN"], active_high=True, initial_value=0)
    in1_1 = OutputDevice(motor1["IN1"])
    in1_2 = OutputDevice(motor1["IN2"])
    in2_1 = OutputDevice(motor2["IN1"])
    in2_2 = OutputDevice(motor2["IN2"])

    def set_motor(en, in1, in2, val):
        # val: -1.0 (full back) to 1.0 (full fwd)
        duty = abs(val)
        en.value = duty
        if val > 0:
            in1.off()
            in2.on()
        elif val < 0:
            in1.on()
            in2.off()
        else:
            # Stop/Coast
            en.off()
            in1.off()
            in2.off()

    def move(left_val, right_val):
        set_motor(en1, in1_1, in1_2, left_val)
        set_motor(en2, in2_1, in2_2, right_val)

    try:
        if command == "demo":
            context.log("Running motor demo sequence")
            context.log("FORWARD 5s")
            move(1.0, 1.0)
            if not context.sleep(5.0):
                return {"events": context.events, "timed_out": True}
            
            context.log("COAST 2s")
            move(0, 0)
            if not context.sleep(2.0):
                return {"events": context.events, "timed_out": True}
            
            context.log("BACKWARD 5s")
            move(-1.0, -1.0)
            if not context.sleep(5.0):
                return {"events": context.events, "timed_out": True}
        
        elif command == "forward":
            context.log(f"FORWARD speed={speed} duration={duration}s")
            move(speed, speed)
            context.sleep(duration)
        
        elif command == "backward":
            context.log(f"BACKWARD speed={speed} duration={duration}s")
            move(-speed, -speed)
            context.sleep(duration)

        elif command == "left":
            # Turn left: Left motor back, Right motor fwd
            context.log(f"LEFT speed={speed} duration={duration}s")
            move(-speed, speed)
            context.sleep(duration)

        elif command == "right":
            # Turn right: Left motor fwd, Right motor back
            context.log(f"RIGHT speed={speed} duration={duration}s")
            move(speed, -speed)
            context.sleep(duration)
            
        elif command == "stop":
            context.log("STOP")
            move(0, 0)
            
        else:
            context.log(f"Unknown command '{command}', stopping.")
            move(0, 0)

        return {
            "events": context.events,
            "timed_out": context.timed_out,
            "duration_seconds": context.elapsed(),
            "command": command,
            "motor_pins": {"motor1": motor1, "motor2": motor2},
        }
    finally:
        en1.off()
        en2.off()
        in1_1.off()
        in1_2.off()
        in2_1.off()
        in2_2.off()
        en1.close()
        en2.close()
        in1_1.close()
        in1_2.close()
        in2_1.close()
        in2_2.close()

def _run_named_led(led_id: int, command: str, parameters: Any) -> Dict[str, Any]:
    merged: Dict[str, Any] = {}
    if isinstance(parameters, dict):
        merged = dict(parameters)
    merged.update({"led_id": led_id, "command": command})
    return _control_specific_led(merged)


def _control_specific_led(parameters: Any) -> Dict[str, Any]:
    timeout = _parse_timeout_parameter(parameters, _DEFAULT_LED_TIMEOUT)
    context = _ActionExecutionContext(timeout)

    led_id = 1
    command = "on"
    duration = 5.0
    duration_provided = False
    blink_on_time = 0.5
    blink_off_time = 0.5

    if isinstance(parameters, dict):
        if "led_id" in parameters:
            try:
                led_id = int(parameters["led_id"])
            except (ValueError, TypeError):
                pass
        
        if "command" in parameters and isinstance(parameters["command"], str):
            command = parameters["command"].strip().lower()

        if "duration" in parameters:
            try:
                duration = float(parameters["duration"])
                duration_provided = True
            except (ValueError, TypeError):
                pass
        
        if "blink_rate" in parameters:
             try:
                rate = float(parameters["blink_rate"])
                if rate > 0:
                    blink_on_time = blink_off_time = 0.5 / rate # Approx
             except (ValueError, TypeError):
                pass

    if led_id not in (1, 2, 3):
        raise ValueError("led_id must be 1, 2, or 3.")

    led_key = f"led{led_id}"
    pin = _LED_PINS[led_key]

    try:
        from gpiozero import LED
    except ImportError as exc:
        raise RuntimeError(
            "gpiozero is required to drive the LEDs."
        ) from exc

    led = LED(pin)
    
    context.log("Specific LED control start", led_id=led_id, pin=pin, command=command, duration=duration)

    try:
        if command == "on":
            led.on()
            context.log("LED turned ON", led_id=led_id, pin=pin)
            if duration_provided and duration > 0:
                context.sleep(duration)
                led.off()
                context.log("LED turned OFF after duration", led_id=led_id, duration=duration)
            # If duration not provided or duration <= 0, leave LED on (no close)
            elif not duration_provided:
                # Default behavior: leave LED on indefinitely
                pass
        elif command == "off":
            led.off()
            context.log("LED turned OFF", led_id=led_id, pin=pin)
            led.close()
        elif command == "blink":
            led.blink(on_time=blink_on_time, off_time=blink_off_time, n=None, background=True)
            context.sleep(duration)
            led.off()
            led.close()
        else:
            context.log(f"Unknown command '{command}'")
            led.close()
    except Exception as exc:
        context.log(f"LED control error: {exc}", led_id=led_id)
        try:
            led.close()
        except Exception:
            pass
        raise

    return {
        "events": context.events,
        "led_id": led_id,
        "command": command,
        "duration_seconds": context.elapsed()
    }


def _control_all_leds(parameters: Any) -> Dict[str, Any]:
    timeout = _parse_timeout_parameter(parameters, _DEFAULT_LED_TIMEOUT)
    context = _ActionExecutionContext(timeout)

    command = "off"
    duration: Optional[float] = None
    duration_provided = False
    blink_on_time = 0.5
    blink_off_time = 0.5

    if isinstance(parameters, dict):
        if "command" in parameters and isinstance(parameters["command"], str):
            command = parameters["command"].strip().lower()

        if "duration" in parameters:
            try:
                duration = float(parameters["duration"])
                duration_provided = True
            except (ValueError, TypeError):
                pass

        if "blink_rate" in parameters:
            try:
                rate = float(parameters["blink_rate"])
                if rate > 0:
                    blink_on_time = blink_off_time = 0.5 / rate
            except (ValueError, TypeError):
                pass

    try:
        from gpiozero import LED
    except ImportError as exc:
        raise RuntimeError(
            "gpiozero is required to drive the LEDs."
        ) from exc

    leds = [LED(pin) for pin in _LED_PINS.values()]

    context.log(
        "All LED control start",
        pins=_LED_PINS,
        command=command,
        duration=duration,
    )

    try:
        if command == "on":
            for led in leds:
                led.on()
            context.log("All LEDs turned ON", pins=_LED_PINS)
            if duration_provided and duration is not None and duration > 0:
                context.sleep(duration)
                for led in leds:
                    led.off()
                for led in leds:
                    led.close()
                context.log("All LEDs turned OFF after duration", duration=duration)
            # If duration not provided, leave LEDs on (no close)
        elif command == "off":
            for led in leds:
                led.off()
            context.log("All LEDs turned OFF", pins=_LED_PINS)
            for led in leds:
                led.close()
        elif command == "blink":
            hold_seconds = duration if duration_provided and duration is not None else 5.0
            for led in leds:
                led.blink(on_time=blink_on_time, off_time=blink_off_time, n=None, background=True)
            if hold_seconds > 0:
                context.sleep(hold_seconds)
            for led in leds:
                led.off()
            for led in leds:
                led.close()
        else:
            context.log(f"Unknown command '{command}' for all LEDs.")
            for led in leds:
                led.close()
    except Exception as exc:
        context.log(f"All LED control error: {exc}")
        for led in leds:
            try:
                led.close()
            except Exception:
                pass
        raise

    return {
        "events": context.events,
        "command": command,
        "timed_out": context.timed_out,
        "duration_seconds": context.elapsed(),
        "pins": _LED_PINS,
    }


def _control_specific_dc_motor(parameters: Any) -> Dict[str, Any]:
    timeout = _parse_timeout_parameter(parameters, _DEFAULT_MOTOR_TEST_TIMEOUT)
    context = _ActionExecutionContext(timeout)

    motor_id = 1
    command = "forward"
    speed = 1.0
    duration = 5.0

    if isinstance(parameters, dict):
        if "motor_id" in parameters:
            try:
                motor_id = int(parameters["motor_id"])
            except (ValueError, TypeError):
                pass
        
        if "command" in parameters and isinstance(parameters["command"], str):
            command = parameters["command"].strip().lower()

        if "speed" in parameters:
             try:
                speed = float(parameters["speed"])
                speed = max(0.0, min(1.0, speed))
             except (ValueError, TypeError):
                pass

        if "duration" in parameters:
            try:
                duration = float(parameters["duration"])
            except (ValueError, TypeError):
                pass

    if motor_id not in (1, 2):
        raise ValueError("motor_id must be 1 or 2.")

    # Pin definitions (Same as _run_motor_test)
    motor_pins = {
        1: {"EN": 25, "IN1": 24, "IN2": 23},
        2: {"EN": 17, "IN1": 27, "IN2": 22}
    }
    pins = motor_pins[motor_id]

    try:
        from gpiozero import OutputDevice, PWMOutputDevice
    except ImportError as exc:
        raise RuntimeError("gpiozero is required.") from exc

    en = PWMOutputDevice(pins["EN"], active_high=True, initial_value=0)
    in1 = OutputDevice(pins["IN1"])
    in2 = OutputDevice(pins["IN2"])

    context.log("Specific DC Motor control", motor_id=motor_id, command=command, speed=speed, duration=duration)

    try:
        if command == "forward":
            en.value = speed
            in1.off()
            in2.on()
            context.sleep(duration)
        elif command == "backward":
            en.value = speed
            in1.on()
            in2.off()
            context.sleep(duration)
        elif command == "stop":
            en.off()
            in1.off()
            in2.off()
        else:
             context.log(f"Unknown command '{command}'")

    finally:
        en.off()
        in1.off()
        in2.off()
        en.close()
        in1.close()
        in2.close()

    return {
        "events": context.events,
        "motor_id": motor_id,
        "command": command,
        "duration_seconds": context.elapsed()
    }


def _run_named_dc_motor(motor_id: int, parameters: Any) -> Dict[str, Any]:
    merged: Dict[str, Any] = {}
    if isinstance(parameters, dict):
        merged = dict(parameters)
    merged["motor_id"] = motor_id
    return _control_specific_dc_motor(merged)


def _run_oled_robot_demo(parameters: Any) -> Dict[str, Any]:
    timeout = _parse_timeout_parameter(parameters, _DEFAULT_OLED_DEMO_TIMEOUT)
    context = _ActionExecutionContext(timeout)
    text_override: Optional[str] = None
    motion_mode: Optional[str] = None
    requested_motion: Optional[str] = None
    requested_duration: Optional[float] = None
    burst_default = 5.0

    if isinstance(parameters, dict):
        candidate_text = parameters.get("text") or parameters.get("message")
        if isinstance(candidate_text, list):
            candidate_text = " ".join(str(item) for item in candidate_text if item is not None)
        if isinstance(candidate_text, str) and candidate_text.strip():
            text_override = " ".join(candidate_text.split())
        candidate_motion = parameters.get("motion") or parameters.get("mode")
        if isinstance(candidate_motion, str) and candidate_motion.strip():
            requested_motion = candidate_motion.strip()
        candidate_duration = parameters.get("duration") or parameters.get("seconds") or parameters.get("display_seconds")
        if isinstance(candidate_duration, (int, float)) and float(candidate_duration) > 0:
            requested_duration = float(candidate_duration)
        elif isinstance(candidate_duration, str) and candidate_duration.strip():
            try:
                requested_duration = float(candidate_duration.strip())
            except ValueError:
                context.log("Ignoring non-numeric duration parameter", raw_value=candidate_duration)

    motion_mode = _normalize_motion_mode(requested_motion) or None
    if not motion_mode and requested_motion:
        context.log("Unsupported mono-eye motion requested; using default", requested=requested_motion)
    if not motion_mode:
        inferred_motion = _infer_motion_from_text(text_override or "")
        if inferred_motion:
            motion_mode = inferred_motion
            context.log("Inferred mono-eye motion from text payload", motion=motion_mode)

    manager = _get_or_start_oled_manager()
    if manager and manager.is_running:
        display_seconds = min(timeout, _OLED_TEXT_DURATION_LIMIT)
        display_seconds = min(display_seconds, max(_OLED_TEXT_MIN_DURATION, requested_duration or burst_default))
        motion_info: Optional[Dict[str, Any]] = None
        if motion_mode:
            motion_info = manager.set_motion_mode(motion_mode, display_seconds)
            context.log(
                "OLED mono-eye motion updated",
                ok=motion_info.get("ok"),
                mode=motion_info.get("mode") or motion_mode,
                duration_seconds=motion_info.get("duration_seconds", display_seconds),
                error=motion_info.get("error"),
            )
        if text_override:
            scheduled = manager.show_text(text_override, display_seconds)
            if scheduled.get("ok"):
                context.log(
                    "OLED text scheduled on mono-eye daemon",
                    lines=scheduled.get("lines"),
                    duration_seconds=scheduled.get("duration_seconds", display_seconds),
                )
                wait_window = min(
                    scheduled.get("duration_seconds", display_seconds),
                    _OLED_RESULT_RETURN_SECONDS,
                )
                if wait_window > 0:
                    context.sleep(wait_window)
                return {
                    "events": context.events,
                    "timed_out": context.timed_out,
                    "duration_seconds": context.elapsed(),
                    "display_seconds": scheduled.get("duration_seconds", display_seconds),
                    "text_lines": scheduled.get("lines"),
                    "motion_mode": (motion_info or {}).get("mode", motion_mode or "default"),
                    "mono_eye_background": True,
                }

            context.log("OLED daemon could not render text", error=scheduled.get("error"))
        else:
            context.log(
                "OLED mono-eye daemon already running",
                duration_hint=min(timeout, _OLED_RESULT_RETURN_SECONDS),
            )
            context.sleep(min(timeout, _OLED_RESULT_RETURN_SECONDS))
            return {
                "events": context.events,
                "timed_out": context.timed_out,
                "duration_seconds": context.elapsed(),
                "display_seconds": min(timeout, _OLED_RESULT_RETURN_SECONDS),
                "text_lines": [],
                "motion_mode": (motion_info or {}).get("mode", motion_mode or "default"),
                "mono_eye_background": True,
            }

    try:
        from luma.core.render import canvas
    except ImportError as exc:
        raise RuntimeError(
            "The OLED demo requires gpiozero, Pillow, and luma.lcd to be installed on the Raspberry Pi."
        ) from exc

    motion_key = (motion_mode or "default").lower()
    motion_profile = _OLED_MOTION_PRESETS.get(motion_key, _OLED_MOTION_PRESETS["default"])
    if motion_key not in _OLED_MOTION_PRESETS:
        context.log(
            "Unsupported mono-eye motion requested; using default",
            requested=motion_mode or requested_motion or "default",
        )
        motion_key = "default"

    _oled_ensure_spidev_exists()
    context.log("SPI device is available", device=f"/dev/spidev{_OLED_SPI_PORT}.{_OLED_SPI_DEVICE}")

    font = _oled_load_font()
    text_lines = _oled_wrap_text_lines(text_override or "", font, _OLED_WIDTH - _OLED_TEXT_PADDING)
    show_text_only = bool(text_lines)
    panel_height = _OLED_TEXT_PANEL_HEIGHT if show_text_only else 0
    if text_lines:
        context.log("OLED text prepared", lines=text_lines)

    backlight: Optional[Any] = None
    frames = 0
    max_run_seconds = min(timeout, _OLED_RESULT_RETURN_SECONDS)

    try:
        backlight = _oled_setup_backlight()
        context.log("Backlight PWM initialized", pin=_OLED_PIN_BL)
        device = _oled_create_device()
        context.log("ST7735 display initialized", resolution=f"{_OLED_WIDTH}x{_OLED_HEIGHT}")

        t0 = time.time()
        frame_delay = 1.0 / _OLED_FPS

        for duty in _OLED_BACKLIGHT_STEPS:
            if backlight is not None:
                _oled_set_backlight_percent(backlight, duty)
            with canvas(device) as draw:
                if show_text_only:
                    _oled_draw_text_screen(draw, font, text_lines or ["Showing message..."], device.width, device.height)
                else:
                    _oled_draw_mono_eye(draw, 0.0, device.width, device.height, panel_height, motion_profile)
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
                    _oled_draw_text_screen(draw, font, text_lines, device.width, device.height)
                else:
                    _oled_draw_mono_eye(draw, t, device.width, device.height, panel_height, motion_profile)
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
            "target_fps": _OLED_FPS,
            "display_left_on": True,
            "max_run_seconds": max_run_seconds,
            "text_lines": text_lines,
            "motion_mode": motion_key,
        }
    finally:
        if backlight is not None:
            context.log("Leaving OLED backlight on after demo")


_OLED_MOTION_PRESETS: Dict[str, Dict[str, float]] = {
    "default": {
        "speed": 1.0,
        "sweep": _OLED_EYE_SWEEP,
        "beam_speed": 60.0,
        "bob_speed": 0.7,
        "bob_amplitude": 3.0,
        "eye_scale": 3.0,
    },
}


def _extract_servo_arguments(parameters: Any) -> Tuple[List[str], bool]:
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
        " 🟠 信号（橙/黄/白）: CH1->GPIO12(物理32), CH2->GPIO19(物理35)"
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


def _control_specific_servo(parameters: Any) -> Dict[str, Any]:
    if not isinstance(parameters, dict):
        raise ValueError("parameters must be a dictionary.")

    merged: Dict[str, Any] = dict(parameters)
    servo_raw = merged.get("servo_id", merged.get("channel"))
    if servo_raw is None:
        raise ValueError("servo_id (1-2) is required.")

    try:
        servo_id = int(str(servo_raw).strip())
    except (ValueError, TypeError) as exc:
        raise ValueError("servo_id must be an integer between 1 and 2.") from exc

    if servo_id not in (1, 2):
        raise ValueError("servo_id must be between 1 and 2.")

    merged["channel"] = servo_id
    merged.pop("servo_id", None)

    result = _run_servo_demo(merged)
    result["servo_id"] = servo_id
    return result


def _run_named_servo(servo_id: int, parameters: Any) -> Dict[str, Any]:
    merged: Dict[str, Any] = {}
    if isinstance(parameters, dict):
        merged = dict(parameters)
    merged["servo_id"] = servo_id
    return _control_specific_servo(merged)


_DUAL_SERVO_PINS = {"servo1": 12, "servo2": 19}
_DUAL_SERVO_MIN_ANGLE = 0.0
_DUAL_SERVO_MAX_ANGLE = 180.0
_DUAL_SERVO_MIN_PW = 0.0005
_DUAL_SERVO_MAX_PW = 0.0025


def _run_dual_servo_demo(parameters: Any) -> Dict[str, Any]:
    timeout = _parse_timeout_parameter(parameters, _DEFAULT_DUAL_SERVO_TIMEOUT)
    context = _ActionExecutionContext(timeout)

    command = "demo"
    action = "demo"
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
        
        if "action" in parameters and isinstance(parameters["action"], str):
            action = parameters["action"].strip().lower() or "demo"

        if "cycles" in parameters:
            try:
                cycles_value = int(parameters["cycles"])
            except (TypeError, ValueError) as exc:
                raise ValueError("cycles must be an integer.") from exc
            if cycles_value < 0:
                raise ValueError("cycles must be zero or a positive integer.")
            cycles = cycles_value
        elif action in ("nod", "shake", "happy"):
            # Default cycles for gestures
            cycles = 5

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
        elif action in ("nod", "shake", "happy"):
             delay = 0.01 # Faster for gestures

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
        action=action,
        cycles=cycles if cycles > 0 else "until timeout",
        step=step,
        delay_seconds=delay,
        pigpio=use_pigpio,
    )

    executed_cycles = 0
    try:
        # Center first
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

        else: # command == "demo"
            while not context.timed_out:
                if cycles > 0 and executed_cycles >= cycles:
                    break
                
                if action == "nod":
                    # Nod: 70 -> 110 -> 70 (both)
                    a = 70.0
                    while a <= 110.0:
                        servo1.angle = a
                        servo2.angle = a # Synced
                        if not context.sleep(delay): break
                        a += step
                    a = 110.0
                    while a >= 70.0:
                        servo1.angle = a
                        servo2.angle = a
                        if not context.sleep(delay): break
                        a -= step

                elif action == "shake":
                    # Shake: S1 70->110, S2 110->70 (Opposite)
                    a = 70.0
                    while a <= 110.0:
                        servo1.angle = a
                        servo2.angle = 180.0 - a 
                        if not context.sleep(delay): break
                        a += step
                    a = 110.0
                    while a >= 70.0:
                        servo1.angle = a
                        servo2.angle = 180.0 - a
                        if not context.sleep(delay): break
                        a -= step
                        
                elif action == "happy":
                    # Happy: fast small wiggles around 90
                    # 80 -> 100 -> 80
                    a = 80.0
                    while a <= 100.0:
                        servo1.angle = a
                        servo2.angle = 180.0 - a
                        if not context.sleep(0.005): break
                        a += 5
                    a = 100.0
                    while a >= 80.0:
                        servo1.angle = a
                        servo2.angle = 180.0 - a
                        if not context.sleep(0.005): break
                        a -= 5

                elif action == "synced_sweep":
                    # Synced 0->180->0
                    angle = 0.0
                    while angle <= _DUAL_SERVO_MAX_ANGLE + 1e-9:
                        servo1.angle = angle
                        servo2.angle = angle
                        if not context.sleep(delay): break
                        angle += step
                    angle = _DUAL_SERVO_MAX_ANGLE
                    while angle >= _DUAL_SERVO_MIN_ANGLE - 1e-9:
                        servo1.angle = angle
                        servo2.angle = angle
                        if not context.sleep(delay): break
                        angle -= step

                else: # "demo" default (inverse sweep)
                    angle = 0.0
                    while angle <= _DUAL_SERVO_MAX_ANGLE + 1e-9:
                        servo1.angle = angle
                        servo2.angle = _DUAL_SERVO_MAX_ANGLE - angle
                        if int(angle) % 30 == 0:
                            context.log("Sweep up", a1=round(angle), a2=round(servo2.angle))
                        if not context.sleep(delay): break
                        angle += step
                    angle = _DUAL_SERVO_MAX_ANGLE
                    while angle >= _DUAL_SERVO_MIN_ANGLE - 1e-9:
                        servo1.angle = angle
                        servo2.angle = _DUAL_SERVO_MAX_ANGLE - angle
                        if int(angle) % 30 == 0:
                            context.log("Sweep down", a1=round(angle), a2=round(servo2.angle))
                        if not context.sleep(delay): break
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
            action=action
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
        "action": action,
        "angle1": angle1,
        "angle2": angle2,
        "hold": hold,
    }


def _execute_action(action: str, parameters: Dict[str, Any]) -> Tuple[bool, Any, Optional[str]]:
    logging.info(
        "Executing action '%s' with parameters=%s",
        action,
        config._format_for_log(parameters or {}),
    )
    try:
        if action == "get_current_time":
            now = datetime.now(timezone.utc).astimezone()
            return True, {"current_time": now.isoformat()}, None
        if action == "run_sequence":
            return True, _run_sequence(parameters or {}), None
        if action == "play_buzzer":
            return True, _play_buzzer(parameters or {}), None
        if action == "operate_dc_motors":
            return True, _run_motor_test(parameters or {}), None
        if action == "display_robot_animation":
            return True, _run_oled_robot_demo(parameters or {}), None
        if action == "control_single_servo":
            return True, _run_servo_demo(parameters or {}), None
        if action == "control_specific_servo":
            return True, _control_specific_servo(parameters or {}), None
        if action == "capture_camera_photo":
            return True, _capture_camera_photo(parameters or {}), None
        if action == "operate_led_pattern":
            return True, _run_led_demo(parameters or {}), None
        if action == "control_specific_led":
            return True, _control_specific_led(parameters or {}), None
        if action == "control_all_leds":
            return True, _control_all_leds(parameters or {}), None
        if action in ("led1_on", "led1_off", "led2_on", "led2_off", "led3_on", "led3_off"):
            command = "on" if action.endswith("_on") else "off"
            led_id = int(action[3])  # led1_on -> 1
            return True, _run_named_led(led_id, command, parameters or {}), None
        if action == "control_specific_dc_motor":
            return True, _control_specific_dc_motor(parameters or {}), None
        if action in ("dc_motor1_control", "dc_motor2_control"):
            motor_id = 1 if "motor1" in action else 2
            return True, _run_named_dc_motor(motor_id, parameters or {}), None
        if action == "move_left_leg":
            return True, _run_named_dc_motor(1, parameters or {}), None
        if action == "move_right_leg":
            return True, _run_named_dc_motor(2, parameters or {}), None
        if action == "control_dual_servos":
            return True, _run_dual_servo_demo(parameters or {}), None
        if action in ("servo1_control", "servo2_control"):
            servo_id = int(action.replace("servo", "").replace("_control", ""))
            return True, _run_named_servo(servo_id, parameters or {}), None
        if action == "move_right_hand":
            return True, _run_named_servo(1, parameters or {}), None
        if action == "move_left_hand":
            return True, _run_named_servo(2, parameters or {}), None
        if action == "no_action":
            message = parameters.get("message") if isinstance(parameters, dict) else None
            return True, {"message": message or "No action executed."}, None

        return False, None, f"Unsupported action: {action}"
    except Exception as exc:
        logging.exception("Action '%s' raised an exception", action)
        return False, None, str(exc)


__all__ = [
    "ACTION_CATALOG",
    "CAPABILITIES",
    "SUPPORTED_ACTIONS",
    "_ActionExecutionContext",
    "_execute_action",
    "_get_or_start_oled_manager",
    "_normalize_digits",
    "_start_mono_eye_daemon_if_possible",
]
