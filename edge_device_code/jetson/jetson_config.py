"""Configuration and capability definitions for the Jetson edge agent."""

import os
from pathlib import Path
from typing import Any, Dict, List

MODEL_PATH = os.getenv("LLAMA_MODEL_PATH", "TinySwallow-1.5B-Instruct-Q5_K_S.gguf")
LLAMA_THREADS = int(os.getenv("LLAMA_THREADS", "4"))
LLAMA_CONTEXT = int(os.getenv("LLAMA_CONTEXT", "1024"))
LLAMA_BATCH = int(os.getenv("LLAMA_BATCH", "32"))
LLAMA_GPU_LAYERS = int(os.getenv("LLAMA_GPU_LAYERS", "16"))
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
    "You convert short English instructions into JSON commands for a Jetson hardware agent.\n"
    "Respond ONLY with a single JSON object that exactly matches the schema:\n"
    '{"action": "<one of the supported actions>", "parameters": { ... }, "message": "<optional string>"}\n'
    "Do not include code fences, explanations, or additional text.\n"
    "Valid actions are: "
    + ", ".join(sorted(SUPPORTED_ACTIONS.keys()))
    + ".\n"
    "Choose the closest matching action and include required parameters; if none apply, use 'no_action'.\n"
    "Examples:\n"
    "Instruction: Show 'Hello World' on the display.\n"
    "{\"action\": \"show_text_on_oled\", \"parameters\": {\"text\": \"Hello World\"}}\n"
    "Instruction: Measure distance with the ultrasonic sensor.\n"
    "{\"action\": \"measure_distance_cm\", \"parameters\": {}}\n"
    "Instruction: Run the motor forwards and backwards.\n"
    "{\"action\": \"run_motor_test\", \"parameters\": {}}\n"
    "Instruction: Thanks!\n"
    "{\"action\": \"no_action\", \"parameters\": {}, \"message\": \"No task requested.\"}\n"
    "Any response that is not valid JSON will be rejected and retried automatically."
)

_RETURN_TEXT_LIMIT = 3000
_RETURN_TEXT_KEEP = 1500
