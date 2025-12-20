"""Configuration and capability definitions for the Jetson edge agent."""

import os
from pathlib import Path
from typing import Any, Dict, List

MODEL_PATH = os.getenv("LLAMA_MODEL_PATH", "Qwen3-1.7B-Q4_K_S.gguf")
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
REQUEST_TIMEOUT = float(os.getenv("IOT_AGENT_HTTP_TIMEOUT", "300"))
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
    "run_sequence": {
        "description": "Execute multiple actions sequentially or with limited parallelism (one GPIO-heavy task at a time).",
        "params": [
            {
                "name": "commands",
                "type": "array",
                "required": True,
                "description": "List of commands to run. Each item should include 'name' and optional 'args' dict.",
            },
            {
                "name": "mode",
                "type": "string",
                "required": False,
                "description": "Execution mode: 'sequential' or 'parallel' (parallel supports one GPIO-heavy action plus lightweight tasks).",
            },
        ],
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
                "description": "Movement direction: 'forward', 'backward', 'left', 'right', 'left_forward', 'left_backward', 'right_forward', 'right_backward'. Supports 'right leg' (right motor) and 'left leg' (left motor) commands.",
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
    "You are a command conversion AI for the Jetson hardware agent.\n"
    "Convert user instructions into the following JSON format:\n"
    '{"action": "<action_name>", "parameters": { ... }, "message": "<optional message>"}\n'
    "Output only JSON. Do not include explanations or code fences.\n\n"
    "【Available Actions】\n"
    "- get_current_time: Get current time (no parameters)\n"
    "- run_sequence: Execute multiple actions sequentially or in simple parallel\n"
    "  - commands (required): List of {name, args}\n"
    "  - mode (optional): 'sequential' or 'parallel'\n"
    "- control_motor: Motor control\n"
    "  - direction (required): 'forward', 'backward', 'left', 'right', 'left_forward', 'left_backward', 'right_forward', 'right_backward'\n"
    "    * Interpret 'right leg' as right motor and 'left leg' as left motor.\n"
    "  - duration (optional): Seconds (default: 1.0)\n"
    "- run_motor_test: Motor test (Forward -> Brake -> Reverse)\n"
    "  - forward_seconds (optional): Forward seconds (default: 3)\n"
    "  - reverse_seconds (optional): Reverse seconds (default: 3)\n"
    "  - brake_seconds (optional): Brake seconds (default: 1)\n"
    "- show_text_on_oled: Display text on OLED\n"
    "  - text (required): Text to display\n"
    "  - duration (optional): Display seconds (default: 10)\n"
    "- measure_distance_cm: Measure distance with ultrasonic sensor (no parameters)\n"
    "- monitor_motion: Detect motion with PIR sensor\n"
    "  - duration (optional): Monitoring seconds (default: 20)\n"
    "- no_action: When no operation is needed\n\n"
    "【Examples】\n"
    "Instruction: What time is it?\n"
    '{"action": "get_current_time", "parameters": {}}\n'
    "Instruction: Move forward for 2 seconds\n"
    '{"action": "control_motor", "parameters": {"direction": "forward", "duration": 2.0}}\n'
    "Instruction: Move right leg forward (Right motor forward)\n"
    '{"action": "control_motor", "parameters": {"direction": "right_forward", "duration": 1.0}}\n'
    "Instruction: Display hi on screen and move forward for 2 seconds\n"
    '{"action": "run_sequence", "parameters": {"mode": "parallel", "commands": ['
    '{"name": "show_text_on_oled", "args": {"text": "hi", "duration": 10}}, '
    '{"name": "control_motor", "args": {"direction": "forward", "duration": 2.0}}]}}\n'
    "Instruction: Display Hello on screen\n"
    '{"action": "show_text_on_oled", "parameters": {"text": "Hello"}}\n'
    "Instruction: Measure distance\n"
    '{"action": "measure_distance_cm", "parameters": {}}\n'
    "Instruction: Monitor motion for 10 seconds\n"
    '{"action": "monitor_motion", "parameters": {"duration": 10}}\n'
    "Instruction: Thank you\n"
    '{"action": "no_action", "parameters": {}, "message": "No operation required"}\n'
)

_RETURN_TEXT_LIMIT = 3000
_RETURN_TEXT_KEEP = 1500
