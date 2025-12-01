"""Shared helpers for the Jetson edge agent."""

import json
import logging
import time
import uuid
from typing import Any, Dict, Optional

import jetson_config as config


def _console(message: str) -> None:
    try:
        print(f"[agent] {message}", flush=True)
    except Exception:
        pass


def _build_url(path: str) -> str:
    return f"{config.SERVER_BASE_URL}{path}"


def _load_device_id() -> str:
    if config.DEVICE_ID_ENV:
        return config.DEVICE_ID_ENV.strip()

    try:
        if config.DEVICE_ID_PATH.exists():
            stored = config.DEVICE_ID_PATH.read_text(encoding="utf-8").strip()
            if stored:
                return stored
    except Exception as exc:
        logging.warning("Failed to read device id file: %s", exc)

    new_id = f"jetson-agent-{uuid.uuid4().hex[:12]}"
    try:
        config.DEVICE_ID_PATH.parent.mkdir(parents=True, exist_ok=True)
        config.DEVICE_ID_PATH.write_text(new_id, encoding="utf-8")
    except Exception as exc:
        logging.warning("Unable to persist device id: %s", exc)
    return new_id


def _log_dict(label: str, value: Dict[str, Any], *, level: int = logging.INFO) -> None:
    try:
        message = json.dumps(value, ensure_ascii=False, sort_keys=True)
    except TypeError:
        message = repr(value)
    logging.log(level, "%s: %s", label, message)


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
    def _truncate_text(value: Any) -> Any:
        if isinstance(value, str) and len(value) > config._RETURN_TEXT_LIMIT:
            head = value[:config._RETURN_TEXT_KEEP]
            tail = value[-config._RETURN_TEXT_KEEP:]
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


def _safe_job_id(job: Any) -> Optional[str]:
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
