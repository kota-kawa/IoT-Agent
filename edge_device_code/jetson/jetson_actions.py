"""Hardware action implementations for the Jetson edge agent."""

import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

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

IN1 = 24
IN2 = 23
IN3 = 27
IN4 = 22
TRIG_PIN = 5
ECHO_PIN = 6
PIR_PIN = 26

I2C_BUS = 7
I2C_ADDR = 0x3C
OLED_WIDTH = 128
OLED_HEIGHT = 128
MAX_INIT_RETRY = 3
MAX_RECOVER_RETRY = 3

_oled_device: Optional[Any] = None


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
    # Use rotate=1 for horizontal orientation
    dev = sh1107(serial, width=OLED_WIDTH, height=OLED_HEIGHT, rotate=1)  # type: ignore[operator]

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


def _get_oled_device() -> sh1107:
    global _oled_device
    if _oled_device is None:
        _oled_device = _init_device_with_retry()
    return _oled_device  # type: ignore


def _invalidate_oled_device() -> None:
    global _oled_device
    _oled_device = None


def _try_recover_device(device: Optional[sh1107]) -> Optional[sh1107]:
    _invalidate_oled_device()
    last_exc: Optional[BaseException] = None
    for attempt in range(1, MAX_RECOVER_RETRY + 1):
        try:
            if device is not None:
                try:
                    # Even though we have noop_cleanup, we might want to try to release resources if possible,
                    # but since we are re-initializing, we just rely on the new init.
                    pass
                except Exception:
                    pass
            dev = _get_oled_device()
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


def _draw_text(device: sh1107, text: str) -> None:
    with canvas(device) as draw:  # type: ignore[operator]
        # Clear with black
        draw.rectangle(device.bounding_box, outline="white", fill="black")
        draw.text((5, 5), text, fill="white")


def _show_text_on_oled(parameters: Dict[str, Any]) -> Dict[str, Any]:
    _require_oled_lib()
    text = str(parameters.get("text", "Hello World"))
    duration = _coerce_positive_float(parameters, "duration", 10.0)
    context = _ActionContext(timeout=duration)

    try:
        device = _get_oled_device()
    except Exception as exc:
        context.log("OLED init failed", error=str(exc))
        return {
            "text": text,
            "duration_seconds": duration,
            "events": context.events,
            "error": str(exc),
        }

    try:
        try:
            _draw_text(device, text)
            context.log("Text drawn", text=text)
        except (OSError, DeviceNotFoundError) as exc:
            context.log("First draw failed, retrying", error=str(exc))
            recovered = _try_recover_device(device)
            if recovered:
                device = recovered
                _draw_text(device, text)
                context.log("Text drawn after recovery", text=text)
            else:
                raise

        time.sleep(duration)
        # Clear the screen after duration instead of cleaning up
        try:
            device.clear()
        except Exception:
            pass

    except (OSError, DeviceNotFoundError) as exc:
        context.log("OLED error", error=str(exc))
        _invalidate_oled_device()
    
    return {
        "text": text,
        "duration_seconds": duration,
        "events": context.events,
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


def _execute_action(action: str, parameters: Dict[str, Any]) -> Tuple[bool, Any, Optional[str]]:
    logging.info("Executing action '%s' with parameters=%s", action, parameters)
    try:
        if action == "get_current_time":
            now = datetime.now(timezone.utc).astimezone()
            return True, {"current_time": now.isoformat()}, None
        if action == "run_motor_test":
            return True, _run_motor_test(parameters or {}), None
        if action == "show_text_on_oled":
            return True, _show_text_on_oled(parameters or {}), None
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
