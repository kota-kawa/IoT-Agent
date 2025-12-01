#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""HTTP interactions and job processing for the Raspberry Pi edge agent."""

import logging
import time
from typing import Any, Dict, Optional, Tuple

import requests

import edge_actions as actions
import edge_config as config
import edge_planner as planner

_RETURN_TEXT_LIMIT = 3000
_RETURN_TEXT_KEEP = 1500


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
        if isinstance(value, str) and len(value) > _RETURN_TEXT_LIMIT:
            head = value[:_RETURN_TEXT_KEEP]
            tail = value[-_RETURN_TEXT_KEEP:]
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


def _register_device(session: requests.Session, device_id: str) -> Tuple[bool, bool]:
    payload = {
        "device_id": device_id,
        "capabilities": actions.CAPABILITIES,
        "meta": {
            "display_name": config.DISPLAY_NAME,
            "role": config.AGENT_ROLE_VALUE,
            "location": config.LOCATION,
            "action_catalog": actions.ACTION_CATALOG,
            "note": "TinyLlama-powered Raspberry Pi agent",
            "registered_via": "edge-device",
        },
        "approved": config.AUTO_APPROVE,
    }

    config._console(
        "Attempting to register device '{}' (display='{}', location='{}').".format(
            device_id,
            config.DISPLAY_NAME,
            config.LOCATION,
        )
    )
    try:
        resp = session.post(
            config._build_url(config.REGISTER_PATH),
            json=payload,
            timeout=config.REQUEST_TIMEOUT,
        )
        if resp.status_code == 403:
            logging.warning(
                "Device not yet approved on server. Register the device ID '%s' manually via the dashboard.",
                device_id,
            )
            config._console(
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
        config._log_dict("Server device snapshot", data.get("device") or {})
        status_text = data.get("status") or "ok"
        config._console(
            "Device '{}' registration succeeded with status '{}'.".format(
                device_id,
                status_text,
            )
        )
        return True, False
    except Exception as exc:
        logging.error("Registration failed: %s", exc)
        config._console("Device '{}' registration failed: {}".format(device_id, exc))
        return False, False


def _poll_next_job(session: requests.Session, device_id: str) -> Optional[Dict[str, Any]]:
    try:
        resp = session.get(
            config._build_url(config.NEXT_PATH.format(device_id=device_id)),
            timeout=config.REQUEST_TIMEOUT,
        )
    except Exception as exc:
        logging.error("Failed to poll for job: %s", exc)
        return None

    if resp.status_code == 204:
        return None

    if resp.status_code == 404:
        logging.warning("Device not registered on server. Re-registering...")
        config._console(
            "Server returned 404 for device '{}'. Triggering re-registration.".format(
                device_id
            )
        )
        registered, manual_required = _register_device(session, device_id)
        if not registered and manual_required:
            logging.warning(
                "Server still waiting for manual approval of device '%s'.", device_id
            )
            config._console(
                "Device '{}' still awaiting manual approval on server.".format(device_id)
            )
        return None

    if resp.status_code != 200:
        logging.error("Unexpected status from job endpoint: %s", resp.status_code)
        config._console(
            "Polling jobs failed with status {} for device '{}'.".format(
                resp.status_code,
                device_id,
            )
        )
        return None

    try:
        job = resp.json()
        job_id = job.get("job_id") or job.get("id")
        config._console(
            "Received job {} from server.".format(job_id if job_id is not None else "<unknown>")
        )
        return job
    except ValueError:
        logging.error("Job payload is not valid JSON: %s", resp.text[:200])
        config._console("Received invalid job payload from server (JSON decode error).")
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

    url = config._build_url(config.RESULT_PATH.format(device_id=device_id_value))
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
                config._console(
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
            config._console(
                "Attempt {} to send result for job {} failed with status {}.".format(
                    attempt,
                    payload.get("job_id"),
                    response.status_code,
                )
            )
        except Exception as exc:
            logging.error("Result post attempt %s raised error: %s", attempt, exc)
            config._console(
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
        config._console(
            "Retrying result delivery for job {} in {:.1f} seconds.".format(
                payload.get("job_id"),
                sleep_for,
            )
        )
        time.sleep(sleep_for)

    return False


def _safe_job_id(job: Any) -> Optional[str]:
    raw_job_id: Any = None
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


def _process_job(
    session: requests.Session,
    llm: Any,
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

    resolved_action: Optional[str] = None
    resolved_parameters: Dict[str, Any] = {}
    resolved_message: Optional[str] = None
    ok = False
    return_value: Any = None
    error_message: Optional[str] = None
    if command_name == config.AGENT_COMMAND_NAME:
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
        config._console(
            "Job {} instruction received: {}".format(
                job_id,
                instruction,
            )
        )

        plans = planner._build_multi_action_plan(llm, instruction)
        (
            ok,
            return_value,
            resolved_message,
            error_message,
            resolved_action,
            resolved_parameters,
        ) = planner._execute_plan_sequence(plans)

        if resolved_action == "multi_action_sequence" and isinstance(return_value, dict):
            steps = return_value.get("steps") if isinstance(return_value.get("steps"), list) else []
            for step in steps:
                if not isinstance(step, dict):
                    continue
                step_no = step.get("step")
                step_action = step.get("action") or "unknown"
                status = "成功" if step.get("ok") else "失敗"
                config._console(
                    "Job {} step {} '{}' 結果: {}".format(
                        job_id,
                        step_no if step_no is not None else "?",
                        step_action,
                        status,
                    )
                )

            summary_info = (
                return_value.get("summary")
                if isinstance(return_value.get("summary"), dict)
                else {}
            )
            if summary_info:
                config._console(
                    "Job {} multi-action summary: {}".format(
                        job_id,
                        config._format_for_log(summary_info),
                    )
                )
    else:
        resolved_action = command_name
        resolved_parameters = args if isinstance(args, dict) else {}
        logging.info(
            "Processing job %s with direct action: %s",
            job_id,
            resolved_action,
        )
        config._console(
            "Job {} direct action request: {} with parameters {}.".format(
                job_id,
                resolved_action,
                config._format_for_log(resolved_parameters),
            )
        )
        ok, return_value, error_message = actions._execute_action(
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
        if not _post_result(session, payload):
            logging.error("Failed to report invalid action for job %s", job_id)
        config._console(
            "Job {} failed: resolved action invalid, notified server.".format(job_id)
        )
        return

    if command_name == config.AGENT_COMMAND_NAME and resolved_action != "multi_action_sequence":
        config._console(
            "Job {} executing action '{}' with parameters {}.".format(
                job_id,
                resolved_action,
                config._format_for_log(resolved_parameters),
            )
        )

    if ok:
        if resolved_action == "multi_action_sequence":
            logging.info("All actions succeeded for job %s", job_id)
            config._console(
                "Job {} multi-action sequence completed successfully.".format(job_id)
            )
        else:
            logging.info(
                "Action '%s' succeeded for job %s", resolved_action, job_id
            )
            logging.info("Result payload: %s", config._format_for_log(return_value))
            config._console(
                "Job {} action '{}' succeeded. Result: {}".format(
                    job_id,
                    resolved_action,
                    config._format_for_log(return_value),
                )
            )
    else:
        logging.error(
            "Action '%s' failed for job %s: %s",
            resolved_action,
            job_id,
            error_message,
        )
        if return_value is not None:
            logging.error(
                "Partial result for failed action '%s': %s",
                resolved_action,
                config._format_for_log(return_value),
            )
        config._console(
            "Job {} action '{}' failed: {}".format(
                job_id,
                resolved_action,
                error_message or "unknown error",
            )
        )
        if return_value is not None:
            config._console(
                "Job {} partial result: {}".format(
                    job_id,
                    config._format_for_log(return_value),
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

    logging.info(
        "Job %s completed: action=%s ok=%s error=%s",
        job_id,
        resolved_action,
        ok,
        error_message,
    )

    if resolved_message:
        logging.info("Job %s agent message: %s", job_id, resolved_message)
        config._console(
            "Job {} message to user: {}".format(job_id, resolved_message)
        )

    if not _post_result(session, result_payload):
        logging.error("Failed to deliver result for job %s", job_id)
        config._console("Job {} result delivery failed after retries.".format(job_id))


__all__ = [
    "_build_result_payload",
    "_poll_next_job",
    "_post_result",
    "_process_job",
    "_register_device",
    "_safe_job_id",
]
