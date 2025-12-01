"""LLM utilities for plan generation."""

import json
import logging
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from llama_cpp import Llama

import jetson_config as config

_JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)


def _create_llm() -> Llama:
    if not Path(config.MODEL_PATH).exists():
        logging.error("Model file not found: %s", config.MODEL_PATH)
        sys.exit(1)

    logging.info("Loading model from %s", config.MODEL_PATH)
    return Llama(
        model_path=config.MODEL_PATH,
        n_threads=config.LLAMA_THREADS,
        n_ctx=config.LLAMA_CONTEXT,
        n_batch=config.LLAMA_BATCH,
        n_gpu_layers=config.LLAMA_GPU_LAYERS,
        seed=config.LLAMA_SEED,
        verbose=False,
    )


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
        return {"action": "show_text_on_oled", "parameters": {"text": "Hello (Keyword)"}}
    if "distance" in lowered or "ultrasonic" in lowered or "sr04" in lowered:
        return {"action": "measure_distance_cm", "parameters": {}}
    if "motion" in lowered or "pir" in lowered or "sr501" in lowered:
        return {"action": "monitor_motion", "parameters": {}}
    return {"action": "no_action", "parameters": {}, "message": "No relevant action found."}


def _plan_from_instruction(llm: Llama, instruction: str) -> Dict[str, Any]:
    def _validate_plan(payload: Any) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        if not isinstance(payload, dict):
            return None, "response was not a JSON object."
        action = payload.get("action")
        if not isinstance(action, str) or not action.strip():
            return None, "action must be a non-empty string."
        action = action.strip()
        if action not in config.SUPPORTED_ACTIONS:
            return None, f"action '{action}' is not supported."
        parameters = payload.get("parameters")
        if parameters is None:
            parameters = {}
        if not isinstance(parameters, dict):
            return None, "parameters must be a JSON object."
        message = payload.get("message")
        if message is not None and not isinstance(message, str):
            return None, "message must be a string when provided."

        normalised: Dict[str, Any] = {"action": action, "parameters": parameters}
        if isinstance(message, str) and message.strip():
            normalised["message"] = message.strip()
        return normalised, None

    retry_instruction: Optional[str] = None
    plan: Dict[str, Any] = {}
    max_attempts = 3

    for attempt in range(1, max_attempts + 1):
        messages: List[Dict[str, str]] = [
            {"role": "system", "content": config.LLM_SYSTEM_PROMPT},
        ]
        if retry_instruction:
            messages.append({"role": "system", "content": retry_instruction})
        messages.append({"role": "user", "content": instruction})

        logging.debug("LLM request (attempt %s): %s", attempt, instruction)
        response = llm.create_chat_completion(
            messages=messages,
            temperature=config.LLAMA_TEMPERATURE,
        )

        text = response["choices"][0]["message"]["content"].strip()
        logging.debug("LLM raw response: %s", text)

        candidate = _extract_json(text)
        validated, error = _validate_plan(candidate)
        if validated:
            plan = validated
            break

        if attempt == max_attempts:
            break

        retry_instruction = (
            "The previous reply was invalid JSON. Respond ONLY with a JSON object shaped as "
            '{"action": "<supported action>", "parameters": { ... }, "message": "<optional string>"}. '
            f"Error: {error or 'Unable to parse response.'}"
        )

    if not plan:
        plan = _keyword_plan(instruction)

    action = plan.get("action")
    if action not in config.SUPPORTED_ACTIONS:
        plan = _keyword_plan(instruction)
    plan.setdefault("parameters", {})
    return plan
