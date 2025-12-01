#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Entrypoint for the Jetson edge agent."""

import logging
import time

import requests

import jetson_config as config
from jetson_device import _poll_next_job, _post_result, _register_device
from jetson_helpers import _build_result_payload, _console, _load_device_id, _safe_job_id
from jetson_jobs import _process_job
from jetson_llm import _create_llm


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
            config.MODEL_PATH,
            config.LLAMA_THREADS,
            config.LLAMA_CONTEXT,
            config.LLAMA_BATCH,
            config.LLAMA_GPU_LAYERS,
        )
    )

    if config.AUTO_APPROVE:
        logging.info(
            "Auto-approval enabled; device '%s' will register itself with capabilities.",
            device_id,
        )
        _console(
            "Auto-approval ON. Registering '{}' directly with full capabilities.".format(
                device_id
            )
        )
    else:
        logging.info(
            "Manual registration is required. Add device '%s' from the dashboard to approve it.",
            device_id,
        )
        _console(
            "Manual approval required on dashboard for device '{}'.".format(device_id)
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
                try:
                    _process_job(session, llm, device_id, job)
                except Exception as exc:
                    logging.exception("Unexpected error while processing job")
                    job_id = _safe_job_id(job)
                    command = job.get("command") if isinstance(job, dict) else {}
                    action_name = None
                    if isinstance(command, dict):
                        action_name = command.get("name")
                        if isinstance(action_name, str):
                            action_name = action_name.strip() or None
                    error_text = str(exc)
                    if job_id:
                        try:
                            payload = _build_result_payload(
                                device_id=device_id,
                                job_id=job_id,
                                ok=False,
                                action=action_name,
                                parameters=None,
                                message=error_text,
                                result=None,
                                error=error_text,
                            )
                            _post_result(session, payload)
                        except Exception:
                            logging.exception("Failed to report unexpected error for job %s", job_id)
                    _console(
                        "Job {} processing failed unexpectedly: {}".format(
                            job_id or "<unknown>",
                            error_text,
                        )
                    )
            else:
                time.sleep(config.POLL_INTERVAL)
    except KeyboardInterrupt:
        logging.info("Stopping agent")
        _console("Keyboard interrupt received. Stopping agent loop.")


if __name__ == "__main__":
    main()
