#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Raspberry Pi 4 edge agent entrypoint using modular components."""

import logging
import time

import requests

import edge_actions as actions
import edge_config as config
import edge_jobs as jobs
import edge_llm as llm_loader


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    session = requests.Session()
    device_id = config._load_device_id()
    config._console("Device ID resolved: {}".format(device_id))
    actions._start_mono_eye_daemon_if_possible()
    llm = llm_loader._create_llm()
    config._console(
        "Model ready (path='{}', threads={}, context={}).".format(
            config.MODEL_PATH,
            config.LLAMA_THREADS,
            config.LLAMA_CONTEXT,
        )
    )

    if config.AUTO_APPROVE:
        logging.info(
            "Auto-approval enabled; device '%s' will register itself with capabilities.",
            device_id,
        )
        config._console(
            "Auto-approval ON. Registering '{}' directly with full capabilities.".format(
                device_id
            )
        )
    else:
        if config.AUTO_REGISTRATION_REQUESTED:
            logging.warning(
                "IOT_AGENT_AUTO_REGISTER is deprecated. Manual approval is now required;"
                " the device will not auto-register with the server."
            )
            config._console(
                "AUTO_REGISTER flag detected but manual approval workflow is in effect."
            )
        logging.info(
            "Manual registration is required. Add device '%s' from the dashboard to approve it.",
            device_id,
        )
        config._console(
            "Manual approval required on dashboard for device '{}'.".format(device_id)
        )

    manual_approval_required_logged = False
    while True:
        registered, manual_required = jobs._register_device(session, device_id)
        if registered:
            break

        if manual_required and not manual_approval_required_logged:
            logging.warning(
                "Waiting for manual approval of device '%s'. Once approved, registration will complete automatically.",
                device_id,
            )
            manual_approval_required_logged = True
            config._console(
                "Waiting for manual approval of device '{}' on server.".format(device_id)
            )

        logging.error("Unable to register device. Retrying in 30 seconds...")
        config._console(
            "Device '{}' registration attempt failed. Retrying soon...".format(device_id)
        )
        time.sleep(30 if manual_required else 10)

    logging.info("Starting polling loop as %s", device_id)
    config._console("Entering polling loop as device '{}'.".format(device_id))

    try:
        while True:
            job = jobs._poll_next_job(session, device_id)
            if job:
                try:
                    jobs._process_job(session, llm, device_id, job)
                except Exception as exc:
                    logging.exception("Unexpected error while processing job")
                    job_id = jobs._safe_job_id(job)
                    command = job.get("command") if isinstance(job, dict) else {}
                    action_name = None
                    if isinstance(command, dict):
                        action_name = command.get("name")
                        if isinstance(action_name, str):
                            action_name = action_name.strip() or None
                    error_text = str(exc)
                    if job_id:
                        try:
                            payload = jobs._build_result_payload(
                                device_id=device_id,
                                job_id=job_id,
                                ok=False,
                                action=action_name,
                                parameters=None,
                                message=error_text,
                                result=None,
                                error=error_text,
                            )
                            jobs._post_result(session, payload)
                        except Exception:
                            logging.exception("Failed to report unexpected error for job %s", job_id)
                    config._console(
                        "Job {} processing failed unexpectedly: {}".format(
                            job_id or "<unknown>",
                            error_text,
                        )
                    )
            else:
                time.sleep(config.POLL_INTERVAL)
    except KeyboardInterrupt:
        logging.info("Stopping agent")
        config._console("Keyboard interrupt received. Stopping agent loop.")


if __name__ == "__main__":
    main()
