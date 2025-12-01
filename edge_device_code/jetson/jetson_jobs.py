"""Job processing and dispatch for the Jetson edge agent."""

import logging
from typing import Any, Dict, Optional

import requests
from llama_cpp import Llama

import jetson_config as config
from jetson_actions import _execute_action
from jetson_device import _post_result
from jetson_helpers import _build_result_payload, _console
from jetson_llm import _plan_from_instruction


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
