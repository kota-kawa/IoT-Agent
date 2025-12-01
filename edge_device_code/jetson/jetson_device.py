"""HTTP interactions with the IoT agent server."""

import json
import logging
import time
from typing import Any, Dict, Optional, Tuple

import requests

import jetson_config as config
from jetson_helpers import _build_url, _console, _log_dict


def _register_device(session: requests.Session, device_id: str) -> Tuple[bool, bool]:
    payload = {
        "device_id": device_id,
        "capabilities": config.CAPABILITIES,
        "meta": {
            "display_name": config.DISPLAY_NAME,
            "role": config.AGENT_ROLE_VALUE,
            "location": config.LOCATION,
            "action_catalog": config.ACTION_CATALOG,
            "note": "TinySwallow-powered Jetson agent",
            "registered_via": "edge-device",
        },
        "approved": config.AUTO_APPROVE,
    }

    _console(
        "Attempting to register device '{}' (display='{}', location='{}').".format(
            device_id,
            config.DISPLAY_NAME,
            config.LOCATION,
        )
    )
    try:
        resp = session.post(
            _build_url(config.REGISTER_PATH),
            json=payload,
            timeout=config.REQUEST_TIMEOUT,
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
            _build_url(config.NEXT_PATH.format(device_id=device_id)),
            timeout=config.REQUEST_TIMEOUT,
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

    url = _build_url(config.RESULT_PATH.format(device_id=device_id_value))
    attempt = 0
    while True:
        attempt += 1
        try:
            response = session.post(url, json=payload, timeout=config.REQUEST_TIMEOUT)
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
