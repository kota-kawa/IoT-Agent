#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""LLM prompting and instruction planning for the Raspberry Pi edge agent."""

import json
import logging
import re
from typing import Any, Dict, List, Optional, Set, Tuple

import edge_actions as actions
import edge_config as config

LLM_SYSTEM_PROMPT = (
    "You convert simple English instructions into JSON commands for a Raspberry Pi automation agent.\n"
    "Return ONLY a single JSON object that exactly matches the schema:\n"
    '{"action": "<one of the supported actions>", "parameters": { ... }, "message": "<optional string>"}\n'
    "Do not include code fences, explanations, or any trailing text.\n"
    "Valid actions are: "
    + ", ".join(sorted(actions.SUPPORTED_ACTIONS.keys()))
    + ".\n"
    "Always choose the action that best fulfills the instruction.\n"
    "Only respond with 'no_action' when the request is impossible or unrelated to the available actions.\n"
    "When a request mentions moving two servos (two/dual/both servos), always choose 'run_dual_servo_demo' instead of 'run_servo_demo' so both servos move together.\n"
    "Include all required parameters.\n"
    "Examples:\n"
    "Instruction: Let's play rock paper scissors, I choose rock.\n"
    "{\"action\": \"play_rock_paper_scissors\", \"parameters\": {\"player_move\": \"rock\"}}\n"
    "Instruction: What time is it right now?\n"
    "{\"action\": \"get_current_time\", \"parameters\": {}}\n"
    "Instruction: Make the buzzer play a short melody.\n"
    "{\"action\": \"play_buzzer\", \"parameters\": {}}\n"
    "Instruction: Just saying thank you!\n"
    "{\"action\": \"no_action\", \"parameters\": {}, \"message\": \"No task requested.\"}"
)


def _extract_json(text: str) -> Optional[Any]:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        candidate = text[start : end + 1]
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            return None
    return None


def _infer_units_from_instruction(instruction: str) -> Optional[str]:
    text = instruction.lower()
    if "fahrenheit" in text or "imperial" in text:
        return "imperial"
    if "celsius" in text or "metric" in text:
        return "metric"
    if "kelvin" in text or "standard" in text:
        return "standard"
    return None


def _extract_float(patterns: List[str], text: str) -> Optional[str]:
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1)
    return None


def _is_dual_servo_request(text: str, lowered: str) -> bool:
    if not text:
        return False

    dual_keywords = [
        "dual servo",
        "dual servos",
        "dual servo motors",
        "two servo",
        "two servos",
        "two servo motors",
        "double servo",
        "2 servo",
        "2 servos",
        "both servos",
        "both servo",
        "both servo motors",
        "pair of servos",
    ]
    if any(keyword in lowered for keyword in dual_keywords):
        return True

    jp_phrases = [
        "2つのサーボ",
        "二つのサーボ",
        "サーボ2つ",
        "サーボ２つ",
        "サーボ2個",
        "サーボ２個",
        "サーボ2基",
        "サーボ２基",
        "二基のサーボ",
        "サーボ2台",
        "サーボ２台",
        "2台のサーボ",
        "二台のサーボ",
        "サーボ二台",
        "両方のサーボ",
        "両サーボ",
        "左右のサーボ",
        "サーボを2つ",
        "サーボを２つ",
    ]
    if any(phrase in text for phrase in jp_phrases):
        return True

    if re.search(r"(サーボ).*(?:2つ|2個|二つ|二個|2基|二基|2台|二台)", text):
        return True
    if re.search(r"(?:2つ|2個|二つ|二個|2基|二基|2台|二台).*(サーボ)", text):
        return True

    return False


def _build_servo_parameters_from_instruction(
    instruction: str, lowered: str
) -> Dict[str, Any]:
    instruction = actions._normalize_digits(instruction)
    lowered = instruction.lower()

    params: Dict[str, Any] = {}
    command_parts: List[str] = []

    channel: Optional[int] = None
    channel_patterns = [
        r"\bch(?:annel)?\s*(\d+)",
        r"(?:CH|ＣＨ)\s*(\d+)",
        r"(\d+)\s*ch",
        r"チャンネル\s*(\d+)",
    ]
    for pattern in channel_patterns:
        match = re.search(pattern, instruction, re.IGNORECASE)
        if match:
            try:
                candidate = int(match.group(1))
            except ValueError:
                continue
            if 1 <= candidate <= 4:
                channel = candidate
                break

    pigpio_requested = "pigpio" in lowered or "ピグピオ" in instruction

    if any(keyword in lowered for keyword in ["center", "centre"]) or any(
        kw in instruction for kw in ["センタ", "センター", "中央"]
    ):
        command_parts.append("center")
    elif any(keyword in lowered for keyword in ["off", "detach"]) or any(
        kw in instruction for kw in ["停止", "止め", "オフ"]
    ):
        command_parts.append("off")
    elif any(keyword in lowered for keyword in ["info", "information"]) or "配線" in instruction:
        command_parts.append("info")
    else:
        sweep_keywords = ["sweep", "scan", "swing"]
        sweep_matches = any(keyword in lowered for keyword in sweep_keywords) or any(
            kw in instruction for kw in ["スイープ", "往復", "揺", "振"]
        )
        if sweep_matches:
            command_parts.append("sweep")
            start_value = _extract_float(
                [
                    r"(?:from|start(?:ing)?(?:\s+at)?)\s*(\d+(?:\.\d+)?)",
                    r"(\d+(?:\.\d+)?)\s*(?:度|degrees?)\s*(?:から|~|〜)",
                ],
                instruction,
            )
            end_value = _extract_float(
                [
                    r"(?:to|until|end(?:ing)?(?:\s+at)?)\s*(\d+(?:\.\d+)?)",
                    r"(?:to|まで)\s*(\d+(?:\.\d+)?)",
                ],
                instruction,
            )

            numeric_candidates: List[str] = []
            if start_value is None or end_value is None:
                raw_numbers = re.findall(r"\d+(?:\.\d+)?", instruction)
                for raw in raw_numbers:
                    try:
                        value = float(raw)
                    except ValueError:
                        continue
                    if channel is not None and abs(value - channel) < 1e-9:
                        continue
                    numeric_candidates.append(raw)

            if start_value is None and numeric_candidates:
                start_value = numeric_candidates.pop(0)
            if end_value is None and numeric_candidates:
                end_value = numeric_candidates.pop(0)

            if start_value is not None:
                command_parts.extend(["--start", start_value])
            if end_value is not None:
                command_parts.extend(["--end", end_value])

            step_value = _extract_float([r"step(?: size)?\s*(\d+(?:\.\d+)?)", r"刻み\s*(\d+(?:\.\d+)?)"], instruction)
            if step_value is None and numeric_candidates:
                step_value = numeric_candidates.pop(0)
            if step_value is not None:
                command_parts.extend(["--step", step_value])

            delay_value = _extract_float(
                [
                    r"delay\s*(\d+(?:\.\d+)?)",
                    r"(\d+(?:\.\d+)?)\s*(?:sec|s|秒)(?:\s*delay)?",
                ],
                instruction,
            )
            if delay_value is not None:
                command_parts.extend(["--delay", delay_value])

            cycles_value = _extract_float(
                [r"(\d+)\s*(?:cycles?|回|往復)"],
                instruction,
            )
            if cycles_value is not None:
                command_parts.extend(["--cycles", cycles_value])
        else:
            angle_value = _extract_float(
                [
                    r"(?:to|at|angle|set)\s*(\d+(?:\.\d+)?)\s*(?:degrees?|°)",
                    r"(\d+(?:\.\d+)?)度",
                ],
                instruction,
            )
            if angle_value is not None:
                command_parts.extend(["set", "--angle", angle_value])

    hold_value = _extract_float(
        [r"hold\s*(\d+(?:\.\d+)?)", r"(\d+(?:\.\d+)?)\s*秒保持"],
        instruction,
    )

    if command_parts:
        if channel is not None:
            command_parts.extend(["--channel", str(channel)])
        if pigpio_requested:
            command_parts.append("--pigpio")
        if hold_value is not None:
            command_parts.extend(["--hold", hold_value])
        params["command"] = " ".join(command_parts)

    return params


def _heuristic_multi_plan(instruction: str) -> List[Dict[str, Any]]:
    text = actions._normalize_digits(instruction).strip()
    if not text:
        return []

    lowered = text.lower()
    plans: List[Dict[str, Any]] = []
    seen: Set[str] = set()

    def _add(action: str, parameters: Dict[str, Any], message: Optional[str] = None) -> None:
        if action not in actions.SUPPORTED_ACTIONS:
            return
        if action in seen:
            return
        seen.add(action)
        entry: Dict[str, Any] = {"action": action, "parameters": dict(parameters or {})}
        if message:
            entry["message"] = message
        plans.append(entry)

    if (
        "rock paper scissors" in lowered
        or "janken" in lowered
        or "じゃんけん" in text
        or "グー" in text
    ):
        _add("play_rock_paper_scissors", {})

    if any(
        keyword in lowered
        for keyword in ["what time", "current time", "time is it", "clock", "時刻", "今何時"]
    ):
        _add("get_current_time", {})

    if any(
        keyword in lowered
        for keyword in ["camera", "photo", "picture", "snapshot"]
    ) or "カメラ" in text or "写真" in text or "撮影" in text:
        _add("capture_camera_photo", {})

    motor_keywords = ["motor test", "motor demo", "l293d", "dc motor"]
    if (
        any(keyword in lowered for keyword in motor_keywords)
        or ("モーター" in text and "サーボ" not in text)
    ) and "servo" not in lowered:
        _add("run_motor_test", {})

    led_keywords = ["led", "blink", "blinking", "light show"]
    if any(keyword in lowered for keyword in led_keywords) or "ライト" in text or "点滅" in text:
        _add("run_led_demo", {})

    buzzer_keywords = ["buzzer", "beep", "tone", "melody"]
    if (
        any(keyword in lowered for keyword in buzzer_keywords)
        or "ブザー" in text
        or "ビープ" in text
    ):
        _add("play_buzzer", {})

    oled_keywords = ["oled", "st7735", "robot face", "lcd animation", "mono eye", "zaku", "mono-eye"]
    if (
        any(keyword in lowered for keyword in oled_keywords)
        or ("ロボット" in text and "顔" in text)
        or "モノアイ" in text
        or "ザク" in text
        or "液晶" in text
    ):
        _add("run_oled_robot_demo", {})

    if "servo" in lowered or "サーボ" in text:
        dual_servos = _is_dual_servo_request(text, lowered)
        if dual_servos:
            _add("run_dual_servo_demo", {})
        else:
            servo_params = _build_servo_parameters_from_instruction(text, lowered)
            _add("run_servo_demo", servo_params)

    return plans


def _keyword_plan(instruction: str) -> Optional[Dict[str, Any]]:
    plans = _heuristic_multi_plan(instruction)
    return plans[0] if plans else None


def _plan_from_instruction(llm: Any, instruction: str) -> Dict[str, Any]:
    def _validate_plan(payload: Any) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        if not isinstance(payload, dict):
            return None, "response was not a JSON object."
        action = payload.get("action")
        if not isinstance(action, str) or not action.strip():
            return None, "action must be a non-empty string."
        action = action.strip()
        if action not in actions.SUPPORTED_ACTIONS:
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
            {"role": "system", "content": LLM_SYSTEM_PROMPT},
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
        fallback = _keyword_plan(instruction)
        if fallback:
            plan = dict(fallback)

    action = plan.get("action")
    if action not in actions.SUPPORTED_ACTIONS:
        plan["action"] = "no_action"
        plan.setdefault("parameters", {})
        plan.setdefault("message", "Model returned an unsupported action.")
    else:
        plan.setdefault("parameters", {})

    if plan.get("action") == "no_action":
        fallback = _keyword_plan(instruction)
        if fallback:
            plan["action"] = fallback["action"]
            plan["parameters"] = fallback.get("parameters", {})
            plan.pop("message", None)

    logging.info("LLM plan resolved: %s", config._format_for_log(plan))
    return plan


def _build_multi_action_plan(llm: Any, instruction: str) -> List[Dict[str, Any]]:
    normalized_text = actions._normalize_digits(instruction).strip()
    lowered = normalized_text.lower()
    wants_dual_servos = _is_dual_servo_request(normalized_text, lowered)

    heuristic = _heuristic_multi_plan(instruction)
    if heuristic:
        return heuristic

    plan = _plan_from_instruction(llm, instruction)
    if isinstance(plan, dict) and plan:
        if wants_dual_servos and plan.get("action") == "run_servo_demo":
            plan = dict(plan)
            plan["action"] = "run_dual_servo_demo"
            plan["parameters"] = {}
        return [plan]

    return []


def _execute_plan_sequence(
    plans: List[Dict[str, Any]]
) -> Tuple[bool, Any, Optional[str], Optional[str], str, Dict[str, Any]]:
    if not plans:
        message = "No executable actions resolved from instruction."
        return False, None, message, message, "no_action", {}

    if len(plans) == 1:
        plan = plans[0]
        action = str(plan.get("action") or "no_action")
        parameters = dict(plan.get("parameters") or {})
        message = plan.get("message") if isinstance(plan.get("message"), str) else None
        ok, result, error = actions._execute_action(action, parameters)
        return ok, result, message, error, action, parameters

    executed_steps: List[Dict[str, Any]] = []
    status_parts: List[str] = []
    plan_messages: List[str] = []
    error_messages: List[str] = []

    for index, plan in enumerate(plans, start=1):
        action = str(plan.get("action") or "no_action")
        parameters = dict(plan.get("parameters") or {})
        message = plan.get("message") if isinstance(plan.get("message"), str) else None

        ok, result, error = actions._execute_action(action, parameters)

        step_record: Dict[str, Any] = {
            "step": index,
            "action": action,
            "ok": ok,
            "parameters": parameters,
        }

        if result is not None:
            step_record["result"] = result

        if message:
            plan_messages.append(message)
            step_record["plan_message"] = message

        if error:
            error_entry = f"{action}: {error}"
            error_messages.append(error_entry)
            step_record["error"] = error

        status_parts.append(f"{action}: {'成功' if ok else '失敗'}")
        executed_steps.append(step_record)

    overall_ok = all(step["ok"] for step in executed_steps)

    summary: Dict[str, Any] = {
        "actions": [step["action"] for step in executed_steps],
        "total_steps": len(executed_steps),
        "successful_steps": sum(1 for step in executed_steps if step["ok"]),
        "success": overall_ok,
    }

    if not overall_ok:
        summary["failed_steps"] = [step["step"] for step in executed_steps if not step["ok"]]

    message_parts: List[str] = list(dict.fromkeys(status_parts))
    if plan_messages:
        message_parts.extend(part for part in plan_messages if part)

    message_text = " / ".join(part for part in message_parts if part) or None

    error_text = " / ".join(dict.fromkeys(error_messages)) or None
    if error_text:
        message_text = (message_text + " / " if message_text else "") + f"エラー: {error_text}"

    result_value: Dict[str, Any] = {
        "summary": summary,
        "steps": executed_steps,
    }

    logging.info("Multi-action plan summary: %s", config._format_for_log(summary))
    for step in executed_steps:
        logging.info(
            "Step %s/%s '%s' -> %s",
            step["step"],
            summary["total_steps"],
            step["action"],
            "success" if step["ok"] else "failure",
        )

    return overall_ok, result_value, message_text, error_text, "multi_action_sequence", summary


__all__ = [
    "LLM_SYSTEM_PROMPT",
    "_build_multi_action_plan",
    "_execute_plan_sequence",
]
