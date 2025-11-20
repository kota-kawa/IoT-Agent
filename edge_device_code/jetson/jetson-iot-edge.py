#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Jetson edge agent using a local GGUF model and existing hardware demos."""

import json
import logging
import os
import re
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
from dotenv import load_dotenv
from llama_cpp import Llama

try:
    import Jetson.GPIO as GPIO
except ImportError:  # pragma: no cover - Jetson-only dependency
    GPIO = None

try:
    from luma.core.error import DeviceNotFoundError
    from luma.core.interface.serial import i2c
    from luma.core.render import canvas
    from luma.oled.device import sh1107
except ImportError:  # pragma: no cover - Jetson-only dependency
    DeviceNotFoundError = None  # type: ignore
    i2c = None  # type: ignore
    canvas = None  # type: ignore
    sh1107 = None  # type: ignore

# ==== Environment bootstrap ==============================================

_ENV_CANDIDATES = [
    Path(__file__).resolve().parent / ".env",
    Path(__file__).resolve().parent.parent / ".env",
    Path.cwd() / ".env",
]
for _env_file in _ENV_CANDIDATES:
    if _env_file.exists():
        load_dotenv(_env_file, override=False)
load_dotenv(override=False)

# ==== Configuration =======================================================

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
REQUEST_TIMEOUT = float(os.getenv("IOT_AGENT_HTTP_TIMEOUT", "180"))
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
    "run_oled_demo": {
        "description": "Render a simple SH1107 animation over I2C bus 7 (addr 0x3C).",
        "params": [
            {
                "name": "duration",
                "type": "number",
                "required": False,
                "description": "How many seconds to run the animation (default: 20).",
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
        "description": "Execute Jetson automation tasks derived from simple English instructions.",
        "params": [
            {"name": "instruction", "type": "string", "required": True},
        ],
    },
    *ACTION_CATALOG,
]

LLM_SYSTEM_PROMPT = (
    "You convert short English instructions into JSON commands for a Jetson hardware agent.\n"
    "Respond ONLY with a JSON object containing keys 'action' and 'parameters'.\n"
    "Valid actions are: "
    + ", ".join(sorted(SUPPORTED_ACTIONS.keys()))
    + ".\n"
    "Choose the closest matching action and include required parameters.\n"
    "Use 'no_action' only when nothing should be executed.\n"
    "Examples:\n"
    "Instruction: Show the OLED demo for 10 seconds.\n"
    "{\"action\": \"run_oled_demo\", \"parameters\": {\"duration\": 10}}\n"
    "Instruction: Measure distance with the ultrasonic sensor.\n"
    "{\"action\": \"measure_distance_cm\", \"parameters\": {}}\n"
    "Instruction: Run the motor forwards and backwards.\n"
    "{\"action\": \"run_motor_test\", \"parameters\": {}}\n"
    "Instruction: Thanks!\n"
    "{\"action\": \"no_action\", \"parameters\": {\"message\": \"No task requested.\"}}"
)

# ==== Helpers ==============================================================


def _console(message: str) -> None:
    try:
        print(f"[agent] {message}", flush=True)
    except Exception:
        pass


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
    except Exception as exc:
        logging.warning("Failed to read device id file: %s", exc)

    new_id = f"jetson-agent-{uuid.uuid4().hex[:12]}"
    try:
        DEVICE_ID_PATH.parent.mkdir(parents=True, exist_ok=True)
        DEVICE_ID_PATH.write_text(new_id, encoding="utf-8")
    except Exception as exc:
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
        n_batch=LLAMA_BATCH,
        n_gpu_layers=LLAMA_GPU_LAYERS,
        seed=LLAMA_SEED,
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
            "note": "TinySwallow-powered Jetson agent",
        },
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


# ==== Action helpers ======================================================


IN1 = 24
IN2 = 23
IN3 = 27
IN4 = 22
TRIG_PIN = 5
ECHO_PIN = 6
PIR_PIN = 26


class _ActionContext:
    def __init__(self, timeout: Optional[float] = None):
        self.started = time.monotonic()
        self.deadline = self.started + timeout if timeout else None
        self.events: List[Dict[str, Any]] = []
        self.timed_out = False

    def log(self, message: str, **extra: Any) -> None:
        entry: Dict[str, Any] = {
            "time": round(time.monotonic() - self.started, 3),
            "message": message,
        }
        if extra:
            entry.update(extra)
        self.events.append(entry)

    def remaining(self) -> Optional[float]:
        if self.deadline is None:
            return None
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            self.timed_out = True
            return 0.0
        return remaining


def _coerce_positive_float(params: Any, name: str, default: float) -> float:
    if not isinstance(params, dict):
        return default
    value = params.get(name)
    if value is None:
        return default
    if isinstance(value, (int, float)):
        candidate = float(value)
    elif isinstance(value, str) and value.strip():
        candidate = float(value.strip())
    else:
        raise ValueError(f"{name} must be a number of seconds")
    if candidate <= 0:
        raise ValueError(f"{name} must be greater than zero")
    return candidate


def _require_gpio() -> None:
    if GPIO is None:
        raise RuntimeError("Jetson.GPIO is not available on this system.")


def _run_motor_test(parameters: Dict[str, Any]) -> Dict[str, Any]:
    _require_gpio()
    forward_seconds = _coerce_positive_float(parameters, "forward_seconds", 3.0)
    reverse_seconds = _coerce_positive_float(parameters, "reverse_seconds", 3.0)
    brake_seconds = _coerce_positive_float(parameters, "brake_seconds", 1.0)
    context = _ActionContext()

    GPIO.setmode(GPIO.BCM)
    GPIO.setwarnings(False)
    for pin in (IN1, IN2, IN3, IN4):
        GPIO.setup(pin, GPIO.OUT, initial=GPIO.LOW)

    try:
        context.log("Motor forward start")
        GPIO.output(IN1, GPIO.HIGH)
        GPIO.output(IN2, GPIO.LOW)
        GPIO.output(IN3, GPIO.HIGH)
        GPIO.output(IN4, GPIO.LOW)
        time.sleep(forward_seconds)

        context.log("Brake hold")
        GPIO.output(IN1, GPIO.HIGH)
        GPIO.output(IN2, GPIO.HIGH)
        GPIO.output(IN3, GPIO.HIGH)
        GPIO.output(IN4, GPIO.HIGH)
        time.sleep(brake_seconds)

        context.log("Motor reverse start")
        GPIO.output(IN1, GPIO.LOW)
        GPIO.output(IN2, GPIO.HIGH)
        GPIO.output(IN3, GPIO.LOW)
        GPIO.output(IN4, GPIO.HIGH)
        time.sleep(reverse_seconds)

        context.log("Coast stop")
        GPIO.output(IN1, GPIO.LOW)
        GPIO.output(IN2, GPIO.LOW)
        GPIO.output(IN3, GPIO.LOW)
        GPIO.output(IN4, GPIO.LOW)
    finally:
        GPIO.cleanup()

    return {
        "events": context.events,
        "forward_seconds": forward_seconds,
        "reverse_seconds": reverse_seconds,
        "brake_seconds": brake_seconds,
    }


I2C_BUS = 7
I2C_ADDR = 0x3C
OLED_WIDTH = 128
OLED_HEIGHT = 128
FPS = 2.0
FRAME_INTERVAL = 1.0 / FPS
MAX_INIT_RETRY = 3
MAX_ERROR_STREAK_BEFORE_RESET = 5
MAX_RECOVER_RETRY = 3


def _require_oled_lib() -> None:
    if any(value is None for value in (i2c, canvas, sh1107)):
        raise RuntimeError("luma.oled is not available on this system.")


def _create_serial() -> i2c:
    kwargs = {"port": I2C_BUS, "address": I2C_ADDR}
    try:
        varnames = i2c.__init__.__code__.co_varnames  # type: ignore[attr-defined]
    except Exception:
        varnames = ()
    if "bus_speed_hz" in varnames:
        kwargs["bus_speed_hz"] = 100_000
    return i2c(**kwargs)  # type: ignore[operator]


def _init_device_once() -> sh1107:
    serial = _create_serial()
    dev = sh1107(serial, width=OLED_WIDTH, height=OLED_HEIGHT)  # type: ignore[operator]

    def _noop_cleanup(self) -> None:  # type: ignore[override]
        return

    dev.cleanup = _noop_cleanup.__get__(dev, dev.__class__)  # type: ignore[assignment]
    return dev


def _init_device_with_retry() -> sh1107:
    last_exc: Optional[BaseException] = None
    for attempt in range(1, MAX_INIT_RETRY + 1):
        try:
            logging.info(
                "Initializing OLED on /dev/i2c-%s addr=0x%02X (attempt %s/%s)",
                I2C_BUS,
                I2C_ADDR,
                attempt,
                MAX_INIT_RETRY,
            )
            dev = _init_device_once()
            return dev
        except (OSError, DeviceNotFoundError) as exc:  # type: ignore[arg-type]
            last_exc = exc
            time.sleep(0.3)
        except Exception as exc:  # pragma: no cover - defensive
            last_exc = exc
            time.sleep(0.3)
    if last_exc:
        raise last_exc
    raise RuntimeError("OLED init failed")


def _try_recover_device(device: Optional[sh1107], frame_no: int) -> Optional[sh1107]:
    last_exc: Optional[BaseException] = None
    for attempt in range(1, MAX_RECOVER_RETRY + 1):
        try:
            if device is not None:
                try:
                    device.cleanup()  # type: ignore[call-arg]
                except Exception:
                    pass
            dev = _init_device_once()
            return dev
        except (OSError, DeviceNotFoundError) as exc:  # type: ignore[arg-type]
            last_exc = exc
            time.sleep(0.3)
        except Exception as exc:  # pragma: no cover - defensive
            last_exc = exc
            time.sleep(0.3)
    if last_exc:
        logging.error("Failed to recover OLED: %s", last_exc)
    return None


def _draw_frame(device: sh1107, frame_no: int) -> None:
    bar_len = 20
    step = 4
    max_x = OLED_WIDTH - bar_len - 1
    raw_pos = (frame_no * step) % (2 * max_x)
    if raw_pos <= max_x:
        x = raw_pos
    else:
        x = 2 * max_x - raw_pos

    with canvas(device) as draw:  # type: ignore[operator]
        draw.rectangle((0, 0, OLED_WIDTH - 1, OLED_HEIGHT - 1), outline=255, fill=0)
        draw.rectangle((x, OLED_HEIGHT // 2 - 5, x + bar_len, OLED_HEIGHT // 2 + 5), fill=255)
        draw.text((5, 5), f"Frame {frame_no}", fill=255)


def _run_oled_demo(parameters: Dict[str, Any]) -> Dict[str, Any]:
    _require_oled_lib()
    duration = _coerce_positive_float(parameters, "duration", 20.0)
    context = _ActionContext(timeout=duration)
    device = _init_device_with_retry()
    frame = 0
    error_streak = 0

    try:
        while True:
            remaining = context.remaining()
            if remaining is not None and remaining <= 0:
                context.log("Reached duration timeout")
                break
            try:
                _draw_frame(device, frame)
                context.log("Frame drawn", frame=frame)
                error_streak = 0
            except (OSError, DeviceNotFoundError) as exc:  # type: ignore[arg-type]
                error_streak += 1
                context.log("Frame failed", frame=frame, error=str(exc))
                if error_streak >= MAX_ERROR_STREAK_BEFORE_RESET:
                    recovered = _try_recover_device(device, frame)
                    device = recovered if recovered is not None else device
                    error_streak = 0
            frame += 1
            time.sleep(FRAME_INTERVAL)
    finally:
        try:
            device.cleanup()  # type: ignore[call-arg]
        except Exception:
            pass

    return {
        "frames": frame,
        "duration_seconds": round(time.monotonic() - context.started, 3),
        "events": context.events,
        "timed_out": context.timed_out,
    }


def _measure_distance(parameters: Dict[str, Any]) -> Dict[str, Any]:
    _require_gpio()
    GPIO.setmode(GPIO.BCM)
    GPIO.setup(TRIG_PIN, GPIO.OUT)
    GPIO.setup(ECHO_PIN, GPIO.IN)

    try:
        GPIO.output(TRIG_PIN, GPIO.LOW)
        time.sleep(0.1)
        GPIO.output(TRIG_PIN, GPIO.HIGH)
        time.sleep(0.00001)
        GPIO.output(TRIG_PIN, GPIO.LOW)

        start_time = time.time()
        timeout_at = start_time + 0.03
        while GPIO.input(ECHO_PIN) == GPIO.LOW:
            if time.time() > timeout_at:
                raise RuntimeError("ECHO did not go HIGH (timeout waiting for start)")
            start_time = time.time()

        stop_time = time.time()
        timeout_at = stop_time + 0.05
        while GPIO.input(ECHO_PIN) == GPIO.HIGH:
            if time.time() > timeout_at:
                raise RuntimeError("ECHO did not return LOW (timeout during measurement)")
            stop_time = time.time()

        elapsed = stop_time - start_time
        distance_cm = (elapsed * 34300) / 2
        return {
            "distance_cm": round(distance_cm, 2),
            "elapsed_seconds": round(elapsed, 6),
        }
    finally:
        GPIO.cleanup([TRIG_PIN, ECHO_PIN])


def _monitor_motion(parameters: Dict[str, Any]) -> Dict[str, Any]:
    _require_gpio()
    duration = _coerce_positive_float(parameters, "duration", 20.0)
    context = _ActionContext(timeout=duration)

    GPIO.setmode(GPIO.BCM)
    GPIO.setwarnings(True)
    GPIO.setup(PIR_PIN, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)

    last = None
    detections = 0
    try:
        while True:
            remaining = context.remaining()
            if remaining is not None and remaining <= 0:
                context.log("Reached duration timeout")
                break
            val = GPIO.input(PIR_PIN)
            if val != last:
                context.log("Raw change", value=int(val))
                last = val
            if val == GPIO.HIGH:
                detections += 1
                context.log("Motion detected", count=detections)
                time.sleep(2.0)
            else:
                time.sleep(0.1)
    finally:
        GPIO.cleanup([PIR_PIN])

    return {
        "detections": detections,
        "events": context.events,
        "timed_out": context.timed_out,
        "duration_seconds": round(time.monotonic() - context.started, 3),
    }


# ==== LLM interaction =====================================================


_JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)


def _extract_json(text: str) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(text)
    except Exception:
        pass
    match = _JSON_BLOCK_RE.search(text)
    if match:
        try:
            return json.loads(match.group(0))
        except Exception:
            return None
    return None


def _keyword_plan(instruction: str) -> Dict[str, Any]:
    lowered = instruction.lower()
    if "time" in lowered:
        return {"action": "get_current_time", "parameters": {}}
    if "motor" in lowered or "l293" in lowered:
        return {"action": "run_motor_test", "parameters": {}}
    if "oled" in lowered or "display" in lowered:
        return {"action": "run_oled_demo", "parameters": {}}
    if "distance" in lowered or "ultrasonic" in lowered or "sr04" in lowered:
        return {"action": "measure_distance_cm", "parameters": {}}
    if "motion" in lowered or "pir" in lowered or "sr501" in lowered:
        return {"action": "monitor_motion", "parameters": {}}
    return {"action": "no_action", "parameters": {}, "message": "No relevant action found."}


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

    plan = _extract_json(text) or {}
    if not plan:
        plan = _keyword_plan(instruction)

    action = plan.get("action")
    if action not in SUPPORTED_ACTIONS:
        plan = _keyword_plan(instruction)
    plan.setdefault("parameters", {})
    return plan


# ==== Action dispatch =====================================================


def _execute_action(action: str, parameters: Dict[str, Any]) -> Tuple[bool, Any, Optional[str]]:
    logging.info("Executing action '%s' with parameters=%s", action, parameters)
    try:
        if action == "get_current_time":
            now = datetime.now(timezone.utc).astimezone()
            return True, {"current_time": now.isoformat()}, None
        if action == "run_motor_test":
            return True, _run_motor_test(parameters or {}), None
        if action == "run_oled_demo":
            return True, _run_oled_demo(parameters or {}), None
        if action == "measure_distance_cm":
            return True, _measure_distance(parameters or {}), None
        if action == "monitor_motion":
            return True, _monitor_motion(parameters or {}), None
        if action == "no_action":
            message = parameters.get("message") if isinstance(parameters, dict) else None
            return True, {"message": message or "No action executed."}, None
        return False, None, f"Unsupported action: {action}"
    except Exception as exc:
        logging.exception("Action '%s' raised an exception", action)
        return False, None, str(exc)


# ==== Job processing ======================================================


def _process_job(session: requests.Session, llm: Llama, device_id: str, job: Dict[str, Any]) -> None:
    raw_job_id = job.get("job_id") or job.get("id")
    job_id = str(raw_job_id).strip() if raw_job_id is not None else None

    command = job.get("command") or {}
    args = command.get("args") if isinstance(command, dict) else {}
    command_name = command.get("name") if isinstance(command, dict) else None
    command_name = command_name.strip() if isinstance(command_name, str) else None

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
            _post_result(session, payload)
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
        _post_result(session, payload)
        return

    resolved_action: Optional[str]
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
            _post_result(session, payload)
            return

        logging.info("Processing job %s with instruction: %s", job_id, instruction)
        _console(
            "Job {} instruction received: {}".format(
                job_id,
                instruction,
            )
        )

        plan = _plan_from_instruction(llm, instruction)
        resolved_action = str(plan.get("action") or "no_action")
        resolved_parameters = dict(plan.get("parameters") or {})
        resolved_message = plan.get("message") if isinstance(plan.get("message"), str) else None
        ok, return_value, error_message = _execute_action(resolved_action, resolved_parameters)
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
                resolved_parameters,
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
        _post_result(session, payload)
        _console(
            "Job {} failed: resolved action invalid, notified server.".format(job_id)
        )
        return

    if ok:
        logging.info("Action '%s' succeeded for job %s", resolved_action, job_id)
        _console(
            "Job {} action '{}' succeeded.".format(
                job_id,
                resolved_action,
            )
        )
    else:
        logging.error(
            "Action '%s' failed for job %s: %s",
            resolved_action,
            job_id,
            error_message,
        )
        _console(
            "Job {} action '{}' failed: {}".format(
                job_id,
                resolved_action,
                error_message or "unknown error",
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

    _post_result(session, result_payload)


# ==== Entrypoint ==========================================================


def main() -> None:
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
        "Model ready (path='{}', threads={}, context={}, batch={}, gpu_layers={}).".format(
            MODEL_PATH,
            LLAMA_THREADS,
            LLAMA_CONTEXT,
            LLAMA_BATCH,
            LLAMA_GPU_LAYERS,
        )
    )

    manual_approval_required_logged = False
    while True:
        registered, manual_required = _register_device(session, device_id)
        if registered:
            break

        if manual_required and not manual_approval_required_logged:
            manual_approval_required_logged = True
            _console(
                "Waiting for manual approval of device '{}' on server.".format(device_id)
            )

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
                _process_job(session, llm, device_id, job)
            else:
                time.sleep(POLL_INTERVAL)
    except KeyboardInterrupt:
        logging.info("Stopping agent")
        _console("Keyboard interrupt received. Stopping agent loop.")


if __name__ == "__main__":
    main()
