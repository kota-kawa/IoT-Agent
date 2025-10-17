import json
import os
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, List, Optional, Tuple

from dotenv import load_dotenv as loadenv
from flask import Flask, jsonify, redirect, request, session, url_for
from openai import OpenAI


loadenv()

app = Flask(__name__, static_folder=".", static_url_path="")
app.secret_key = os.getenv("FLASK_SECRET_KEY", "change-this-secret")

APP_PASSWORD = "kkawagoe"

AGENT_ROLE_VALUE = "raspberrypi-agent"
AGENT_CAPABILITY_NAME = "agent_instruction"
AGENT_COMMAND_NAME = "agent_instruction"


@dataclass
class DeviceState:
    device_id: str
    capabilities: List[Dict[str, Any]]
    meta: Dict[str, Any]
    job_queue: Deque[Dict[str, Any]] = field(default_factory=deque)
    job_results: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    last_seen: float = field(default_factory=time.time)
    last_result: Optional[Dict[str, Any]] = None
    registered_at: float = field(default_factory=time.time)
    approved: bool = False


_DEVICES: Dict[str, DeviceState] = {}
_PENDING_JOBS: Dict[str, str] = {}


DEVICE_RESULT_TIMEOUT = float(os.getenv("DEVICE_RESULT_TIMEOUT", "120"))


def _normalise_capability_params(params: Any) -> List[Dict[str, Any]]:
    if not isinstance(params, list):
        return []

    cleaned_params: List[Dict[str, Any]] = []
    for raw_param in params:
        if not isinstance(raw_param, dict):
            continue

        raw_name = raw_param.get("name")
        if not isinstance(raw_name, str):
            continue

        name = raw_name.strip()
        if not name:
            continue

        cleaned: Dict[str, Any] = {"name": name}

        raw_type = raw_param.get("type")
        if isinstance(raw_type, str):
            type_name = raw_type.strip()
            if type_name:
                cleaned["type"] = type_name

        if "required" in raw_param:
            cleaned["required"] = bool(raw_param.get("required"))

        if "default" in raw_param:
            cleaned["default"] = raw_param.get("default")

        raw_description = raw_param.get("description")
        if isinstance(raw_description, str):
            description = raw_description.strip()
            if description:
                cleaned["description"] = description

        cleaned_params.append(cleaned)

    return cleaned_params


def _normalise_capabilities(raw_capabilities: Any) -> List[Dict[str, Any]]:
    if not isinstance(raw_capabilities, list):
        return []

    cleaned_capabilities: List[Dict[str, Any]] = []
    for raw_capability in raw_capabilities:
        if not isinstance(raw_capability, dict):
            continue

        raw_name = raw_capability.get("name")
        if not isinstance(raw_name, str):
            continue

        name = raw_name.strip()
        if not name:
            continue

        cleaned: Dict[str, Any] = {"name": name}

        raw_description = raw_capability.get("description")
        if isinstance(raw_description, str):
            description = raw_description.strip()
            if description:
                cleaned["description"] = description

        capability_ref = raw_capability.get("capability")
        if isinstance(capability_ref, str):
            capability_name = capability_ref.strip()
            if capability_name:
                cleaned["capability"] = capability_name

        params = _normalise_capability_params(raw_capability.get("params"))
        if params:
            cleaned["params"] = params

        cleaned_capabilities.append(cleaned)

    return cleaned_capabilities


def _client() -> OpenAI:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set")
    return OpenAI(api_key=api_key)


def _first_device_id() -> Optional[str]:
    return next(iter(_DEVICES), None)


def _device_is_agent(device: DeviceState) -> bool:
    meta = device.meta if isinstance(device.meta, dict) else {}
    role = meta.get("role") or meta.get("device_role")
    if isinstance(role, str) and role.strip().lower() == AGENT_ROLE_VALUE:
        return True

    for capability in device.capabilities:
        name = capability.get("name") if isinstance(capability, dict) else None
        if isinstance(name, str) and name.strip().lower() == AGENT_CAPABILITY_NAME:
            return True

    return False


def _agent_device() -> Optional[DeviceState]:
    for device in _DEVICES.values():
        if _device_is_agent(device):
            return device
    return None


def _action_catalog_for_device(device: DeviceState) -> List[Dict[str, Any]]:
    meta = device.meta if isinstance(device.meta, dict) else {}
    catalog = meta.get("action_catalog") if isinstance(meta, dict) else None

    valid_entries: List[Dict[str, Any]] = []
    if isinstance(catalog, list):
        for entry in catalog:
            if not isinstance(entry, dict):
                continue
            raw_name = entry.get("name")
            if not isinstance(raw_name, str):
                continue
            name = raw_name.strip()
            if not name:
                continue
            cleaned = dict(entry)
            cleaned["name"] = name
            valid_entries.append(cleaned)
    if valid_entries:
        return valid_entries

    fallback_catalog: List[Dict[str, Any]] = []
    for capability in device.capabilities:
        if not isinstance(capability, dict):
            continue
        raw_name = capability.get("name")
        if not isinstance(raw_name, str):
            continue
        name = raw_name.strip()
        if not name:
            continue
        fallback_entry: Dict[str, Any] = {
            "name": name,
            "capability": name,
        }
        description = capability.get("description")
        if isinstance(description, str) and description.strip():
            fallback_entry["description"] = description.strip()
        params = capability.get("params")
        if isinstance(params, list) and params:
            fallback_entry["params"] = params
        fallback_catalog.append(fallback_entry)
    return fallback_catalog


def _describe_device_role(device: DeviceState) -> List[str]:
    lines: List[str] = []
    meta = device.meta if isinstance(device.meta, dict) else {}
    raw_role = None
    if isinstance(meta, dict):
        candidate = meta.get("role") or meta.get("device_role")
        if isinstance(candidate, str) and candidate.strip():
            raw_role = candidate.strip()

    if raw_role:
        lines.append(f"  Role tag: {raw_role}")
        if raw_role.lower() == AGENT_ROLE_VALUE:
            lines.append(
                "  Role details: High-capability automation agent for "
                "multi-step or conversational instructions."
            )
    elif _device_is_agent(device):
        lines.append(
            "  Role details: Treated as an automation agent because it exposes "
            "the agent_instruction capability."
        )
    else:
        lines.append(
            "  Role details: Peripheral or sensor device. Only execute the "
            "explicit capabilities listed below."
        )

    action_catalog = _action_catalog_for_device(device)
    if action_catalog:
        action_names = [
            str(entry.get("name"))
            for entry in action_catalog
            if isinstance(entry, dict) and entry.get("name")
        ]
        filtered = [name.strip() for name in action_names if isinstance(name, str) and name.strip()]
        if filtered:
            lines.append("  Agent predefined actions: " + ", ".join(filtered))

    return lines


def _build_device_context() -> str:
    if not _DEVICES:
        return "No devices are currently registered."

    lines: List[str] = []
    for device in _DEVICES.values():
        lines.append(f"Device ID: {device.device_id}")
        display_name = device.meta.get("display_name") if isinstance(device.meta, dict) else None
        if isinstance(display_name, str) and display_name.strip():
            lines.append(f"  Friendly name: {display_name.strip()}")
        lines.extend(_describe_device_role(device))
        if device.meta:
            lines.append(f"  Meta: {json.dumps(device.meta, ensure_ascii=False)}")
        lines.append(
            "  Registered at: "
            + time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(device.registered_at))
        )
        lines.append(
            "  Last seen: "
            + time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(device.last_seen))
        )
        lines.append(f"  Queue depth: {len(device.job_queue)}")
        lines.append("  Capabilities:")
        for cap in device.capabilities:
            params = cap.get("params") or []
            if params:
                param_desc = ", ".join(
                    f"{p.get('name')} ({p.get('type', 'unknown')})"
                    + (
                        f" default={json.dumps(p.get('default'))}"
                        if p.get("default") is not None
                        else ""
                    )
                    for p in params
                )
            else:
                param_desc = "no parameters"
            lines.append(
                f"    - {cap.get('name')}: {cap.get('description', '')} | params: {param_desc}"
            )
        if device.last_result:
            summary = {
                "job_id": device.last_result.get("job_id"),
                "ok": device.last_result.get("ok"),
                "return_value": device.last_result.get("return_value"),
            }
            lines.append(
                "  Most recent result: "
                + json.dumps(summary, ensure_ascii=False, default=str)
            )
        lines.append("")
    return "\n".join(lines).strip()


def _enqueue_device_command(device_id: str, command: Dict[str, Any]) -> Optional[str]:
    device = _DEVICES.get(device_id)
    if not device:
        return None

    job_id = uuid.uuid4().hex
    device.job_queue.append({"job_id": job_id, "command": command})
    device.last_seen = time.time()
    _PENDING_JOBS[job_id] = device_id
    return job_id


def _await_device_result(device_id: str, job_id: str, timeout: float = 120.0) -> Optional[Dict[str, Any]]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        device = _DEVICES.get(device_id)
        if not device:
            return None
        result = device.job_results.pop(job_id, None)
        if result:
            _PENDING_JOBS.pop(job_id, None)
            return result
        time.sleep(0.2)
    return None


def _validate_device_command(command: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    if not isinstance(command, dict):
        return None, "device_command の形式が不正なため処理を中止しました。"

    raw_device_id = command.get("device_id")
    device_id: Optional[str] = None
    if isinstance(raw_device_id, str) and raw_device_id.strip():
        device_id = raw_device_id.strip()
    elif len(_DEVICES) == 1:
        device_id = _first_device_id()

    if not device_id:
        if _DEVICES:
            return None, "複数のデバイスが登録されているため、device_id を指定できないコマンドは実行しません。"
        return None, "実行可能なデバイスが登録されていません。"

    if device_id not in _DEVICES:
        return None, f"不明な device_id '{device_id}' が指定されたため処理を中止しました。"

    name = command.get("name")
    if not isinstance(name, str) or not name.strip():
        return None, "device_command の name が空です。"

    args = command.get("args")
    if args is None:
        args = {}
    if not isinstance(args, dict):
        return None, "device_command の args はオブジェクトである必要があります。"

    validated_name = name.strip()
    device = _DEVICES.get(device_id)
    capability_names = {
        str(cap.get("name")).strip()
        for cap in (device.capabilities if device else [])
        if isinstance(cap, dict) and cap.get("name")
    }
    if capability_names and validated_name not in capability_names:
        return (
            None,
            f"{device_id} は '{validated_name}' という機能をサポートしていないため実行を中止しました。",
        )

    validated = {
        "device_id": device_id,
        "name": validated_name,
        "args": args,
    }
    return validated, None


def _validate_device_command_sequence(
    commands: Any,
) -> Tuple[List[Dict[str, Any]], List[str]]:
    if commands is None:
        return [], []

    if isinstance(commands, dict):
        command_items: List[Any] = [commands]
    elif isinstance(commands, list):
        command_items = list(commands)
    else:
        return [], ["device_commands の形式が不正なため処理を中止しました。"]

    validated_commands: List[Dict[str, Any]] = []
    errors: List[str] = []

    for index, raw_command in enumerate(command_items, start=1):
        validated, error = _validate_device_command(raw_command)
        if validated:
            validated_commands.append(validated)
            continue
        message = error or "device_command の形式が不正なため処理を中止しました。"
        errors.append(f"ステップ{index}: {message}")

    return validated_commands, errors


def _serialize_device(device: DeviceState) -> Dict[str, Any]:
    return {
        "device_id": device.device_id,
        "capabilities": device.capabilities,
        "meta": device.meta,
        "action_catalog": _action_catalog_for_device(device),
        "queue_depth": len(device.job_queue),
        "last_seen": device.last_seen,
        "registered_at": device.registered_at,
        "last_result": device.last_result,
        "approved": device.approved,
    }


def _device_label_for_prompt(device_id: str) -> str:
    device = _DEVICES.get(device_id)
    if not device:
        return device_id
    display_name = device.meta.get("display_name") if isinstance(device.meta, dict) else None
    if isinstance(display_name, str) and display_name.strip():
        return f"{display_name.strip()} (ID: {device.device_id})"
    return device.device_id


def _format_result_for_prompt(result: Dict[str, Any]) -> str:
    return json.dumps(result, ensure_ascii=False, default=str)


def _structured_llm_prompt(messages: List[Dict[str, str]]) -> Dict[str, Any]:
    device_context = _build_device_context()
    system_prompt = (
        "You are an assistant that manages IoT devices for the user. "
        "Always respond with a strict JSON object containing the keys "
        "'reply' and 'device_commands'. The 'reply' field is a natural "
        "language response to the user. The 'device_commands' field must "
        "be either null, an empty array, or an array of objects with the "
        "keys 'device_id', 'name', and 'args'. Each array element "
        "represents one sequential task for the devices to execute. Do "
        "not wrap the JSON inside code fences. If no device action is "
        "required, set 'device_commands' to null. Only use device IDs and "
        "capability names provided in the context. When an action is "
        "required and multiple devices exist, you MUST select the single "
        "most appropriate device_id for each step by comparing the roles "
        "and capabilities described. Never omit 'device_id' or use an "
        "unknown value. If the correct device cannot be determined, set "
        "'device_commands' to null and ask the user to clarify which "
        "device should be used. Prefer devices tagged with the "
        f"'{AGENT_ROLE_VALUE}' role for complex or conversational tasks. "
        "The 'reply' value must be written in Japanese prose without "
        "including JSON syntax, code formatting, or explicit mentions of "
        "'JSON'. Summarise any structured information conversationally."
    )

    context_message = (
        "Available device information:\n" + device_context
        if device_context
        else "No devices are currently registered."
    )

    return {
        "model": "gpt-4.1-2025-04-14",
        "input": [
            {"role": "system", "content": system_prompt},
            {"role": "system", "content": context_message},
            *messages,
        ],
    }


def _extract_json_object(text: str) -> Tuple[Optional[Any], Optional[str]]:
    if not text:
        return None, ""

    stripped = text.strip()
    decoder = json.JSONDecoder()

    try:
        obj, end = decoder.raw_decode(stripped)
        cleaned = stripped[end:].strip()
        return obj, cleaned
    except json.JSONDecodeError:
        pass

    for index, char in enumerate(text):
        if char not in "{[":
            continue
        try:
            obj, end = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        cleaned = (text[:index] + text[index + end :]).strip()
        return obj, cleaned

    return None, text.strip()


def _call_llm_and_parse(client: OpenAI, messages: List[Dict[str, str]]) -> Dict[str, Any]:
    response = client.responses.create(**_structured_llm_prompt(messages))
    reply_text = getattr(response, "output_text", None) or ""

    parsed_obj, cleaned_text = _extract_json_object(reply_text)

    if isinstance(parsed_obj, dict):
        parsed = parsed_obj
    else:
        parsed = {"reply": cleaned_text or reply_text.strip(), "device_command": None}

    reply_message = parsed.get("reply")
    if not isinstance(reply_message, str):
        reply_message = (cleaned_text or reply_text).strip()

    device_commands_field = parsed.get("device_commands")
    if isinstance(device_commands_field, dict):
        device_commands: List[Dict[str, Any]] = [device_commands_field]
    elif isinstance(device_commands_field, list):
        device_commands = [
            command
            for command in device_commands_field
            if isinstance(command, dict)
        ]
    else:
        device_commands = []

    if not device_commands:
        single_command = parsed.get("device_command")
        if isinstance(single_command, dict):
            device_commands = [single_command]

    return {
        "reply": reply_message,
        "device_commands": device_commands,
        "raw": reply_text,
    }


def _call_llm_text(client: OpenAI, payload: Dict[str, Any]) -> str:
    response = client.responses.create(**payload)
    text = getattr(response, "output_text", "")
    return text.strip()


def _structured_agent_instruction_prompt(messages: List[Dict[str, str]]) -> Dict[str, Any]:
    device_context = _build_device_context()
    system_prompt = (
        "You translate the latest user instruction into a single, simple "
        "English sentence that describes the IoT task to perform. Use clear "
        "imperative phrasing and avoid technical jargon. If no action is "
        "required or the request cannot be fulfilled, respond with "
        "'No action required.'"
    )

    guidance = (
        "When referencing device capabilities, prefer the official names "
        "listed in the available context. Keep the response under 25 words."
    )

    context_message = (
        "Available device information:\n" + device_context
        if device_context
        else "No devices are currently registered."
    )

    return {
        "model": "gpt-4.1-2025-04-14",
        "input": [
            {"role": "system", "content": system_prompt},
            {"role": "system", "content": guidance},
            {"role": "system", "content": context_message},
            *messages,
            {
                "role": "system",
                "content": "Reply with one English sentence and no additional formatting.",
            },
        ],
    }


def _structured_agent_followup_prompt(
    base_messages: List[Dict[str, str]],
    english_instruction: str,
    result: Dict[str, Any],
) -> Dict[str, Any]:
    device_context = _build_device_context()
    summary_instruction = (
        "The edge device executed the request using the following simple "
        f"English instruction: {english_instruction}\n"
        f"Device response details (internal): {_format_result_for_prompt(result)}\n"
        "Write a concise Japanese message for the user that summarises the "
        "outcome. Mention success or failure clearly and include key "
        "details from the result when helpful. Do not request further "
        "actions unless the user explicitly asked. Never display JSON, "
        "code snippets, or mention that JSON was processed; keep the "
        "message purely conversational."
    )

    messages: List[Dict[str, str]] = [
        {"role": "system", "content": "You are an assistant supporting IoT devices."},
    ]
    if device_context:
        messages.append(
            {"role": "system", "content": "Available device information:\n" + device_context}
        )
    messages.extend(base_messages)
    messages.append({"role": "system", "content": summary_instruction})

    return {"model": "gpt-4.1-2025-04-14", "input": messages}


def _format_return_value_for_user(value: Any) -> str:
    if value is None:
        return "値は返されませんでした。"
    if isinstance(value, (str, int, float, bool)):
        return str(value)
    if isinstance(value, dict):
        if not value:
            return "詳細データは空でした。"

        action_name = value.get("action") if isinstance(value.get("action"), str) else None
        has_result_field = "result" in value
        if action_name and has_result_field:
            parameters = value.get("parameters")
            message = value.get("message") if isinstance(value.get("message"), str) else None
            result_payload = value.get("result")

            if (
                action_name == "multi_action_sequence"
                and isinstance(result_payload, dict)
            ):
                steps = result_payload.get("steps")
                formatted_steps: List[str] = []
                if isinstance(steps, list) and steps:
                    for index, raw_step in enumerate(steps, start=1):
                        if not isinstance(raw_step, dict):
                            formatted_steps.append(f"{index}. {raw_step}")
                            continue
                        label = str(
                            raw_step.get("action")
                            or raw_step.get("label")
                            or raw_step.get("name")
                            or f"ステップ{index}"
                        )
                        status = "成功" if raw_step.get("ok") else "失敗"
                        details: List[str] = []
                        if raw_step.get("parameters"):
                            details.append(
                                "パラメータ: "
                                + _format_return_value_for_user(raw_step.get("parameters"))
                            )
                        if "result" in raw_step:
                            details.append(
                                "結果: "
                                + _format_return_value_for_user(raw_step.get("result"))
                            )
                        plan_note = raw_step.get("plan_message")
                        if isinstance(plan_note, str) and plan_note.strip():
                            details.append(f"メモ: {plan_note.strip()}")
                        if raw_step.get("error"):
                            details.append(f"エラー: {raw_step.get('error')}")
                        detail_text = " / ".join(details)
                        step_no = raw_step.get("step")
                        prefix = f"{step_no}. " if isinstance(step_no, int) else f"{index}. "
                        formatted_steps.append(
                            (prefix + f"{label}（{status}）" + (f" {detail_text}" if detail_text else "")).strip()
                        )

                extras: List[str] = []
                if isinstance(parameters, dict) and parameters:
                    extras.append(
                        "サマリ: " + _format_return_value_for_user(parameters)
                    )
                if message:
                    extras.append(f"メッセージ: {message}")

                combined = " / ".join(filter(None, [" / ".join(formatted_steps), *extras]))
                return combined or "マルチステップ結果が空でした。"

            parts: List[str] = [f"アクション: {action_name}"]
            if parameters:
                parts.append(
                    "パラメータ: " + _format_return_value_for_user(parameters)
                )
            if "result" in value:
                parts.append(
                    "結果: " + _format_return_value_for_user(result_payload)
                )
            if message:
                parts.append(f"メッセージ: {message}")
            return " / ".join(parts)

        parts = []
        for key, val in value.items():
            formatted = _format_return_value_for_user(val)
            parts.append(f"{key}: {formatted}")
        return " / ".join(parts)
    if isinstance(value, (list, tuple, set)):
        items = [_format_return_value_for_user(item) for item in value]
        if not items:
            return "詳細データは空でした。"
        return "、".join(items)
    return str(value)


def _manual_result_reply(
    device_label: str, command_name: str, result: Dict[str, Any]
) -> str:
    status = "成功" if result.get("ok") else "失敗"
    if command_name and any(ch.isspace() for ch in command_name):
        command_label = f"指示「{command_name}」"
    else:
        command_label = f"コマンド『{command_name}』"

    lines = [f"{device_label} で{command_label}を実行しました。", f"結果: {status}"]

    if result.get("job_id"):
        lines.append(f"ジョブID: {result.get('job_id')}")

    if "return_value" in result:
        lines.append(f"戻り値: {_format_return_value_for_user(result.get('return_value'))}")

    stdout = result.get("stdout")
    if isinstance(stdout, str) and stdout.strip():
        lines.append(f"標準出力: {stdout.strip()}")

    stderr = result.get("stderr")
    if isinstance(stderr, str) and stderr.strip():
        lines.append(f"標準エラー: {stderr.strip()}")

    error_message = result.get("error")
    if isinstance(error_message, str) and error_message.strip():
        lines.append(f"エラー: {error_message.strip()}")

    return "\n".join(lines)


@dataclass
class _CommandExecutionSummary:
    device_id: Optional[str]
    command_name: str
    args: Dict[str, Any] = field(default_factory=dict)
    manual_reply: str = ""
    result: Optional[Dict[str, Any]] = None
    instruction: Optional[str] = None
    is_agent: bool = False
    status: int = 200
    error_text: Optional[str] = None


def _timeout_reply(command: Dict[str, Any], timeout_seconds: float) -> str:
    device_id = command.get("device_id")
    device_label = _device_label_for_prompt(device_id) if device_id else "対象デバイス"
    command_name = command.get("name", "不明なコマンド")
    instruction_text = None
    args = command.get("args")
    if isinstance(args, dict):
        instruction_text = args.get("instruction")
        if isinstance(instruction_text, str) and not instruction_text.strip():
            instruction_text = None

    if command_name == AGENT_COMMAND_NAME and instruction_text:
        command_label = f"指示「{instruction_text.strip()}」"
    elif isinstance(command_name, str) and any(ch.isspace() for ch in command_name):
        command_label = f"指示「{command_name}」"
    else:
        command_label = f"コマンド『{command_name}』"
    seconds = int(timeout_seconds) if timeout_seconds >= 1 else timeout_seconds
    return (
        f"{device_label} に{command_label}を送信しましたが、"
        f"{seconds}秒以内に結果を受信できませんでした。\n"
        "デバイスの状態を確認してから、もう一度お試しください。"
    )


def _execute_standard_device_command(
    client: OpenAI,
    messages: List[Dict[str, str]],
    initial_reply: str,
    command: Dict[str, Any],
) -> _CommandExecutionSummary:
    device_id = command.get("device_id")
    command_name = (
        str(command.get("name")) if isinstance(command.get("name"), str) else "不明なコマンド"
    )
    args_dict = command.get("args") if isinstance(command.get("args"), dict) else {}

    command_payload = {"name": command_name, "args": args_dict}
    job_id = _enqueue_device_command(device_id, command_payload)
    if job_id is None:
        notice = "(注意: デバイスにコマンドを送信できませんでした。)"
        combined = (initial_reply + "\n" if initial_reply else "") + notice
        return _CommandExecutionSummary(
            device_id=device_id,
            command_name=command_name,
            args=args_dict,
            manual_reply=combined,
        )

    result = _await_device_result(device_id, job_id, timeout=DEVICE_RESULT_TIMEOUT)
    device_label = _device_label_for_prompt(device_id) if device_id else "対象デバイス"

    if result:
        manual_reply = _manual_result_reply(device_label, command_name, result)
        return _CommandExecutionSummary(
            device_id=device_id,
            command_name=command_name,
            args=args_dict,
            manual_reply=manual_reply,
            result=result,
        )

    timeout_reply = _timeout_reply(
        {"device_id": device_id, "name": command_name, "args": args_dict},
        DEVICE_RESULT_TIMEOUT,
    )
    return _CommandExecutionSummary(
        device_id=device_id,
        command_name=command_name,
        args=args_dict,
        manual_reply=timeout_reply,
    )


def _execute_device_command_sequence(
    client: OpenAI,
    messages: List[Dict[str, str]],
    initial_reply: str,
    commands: List[Dict[str, Any]],
) -> Tuple[str, int]:
    if not commands:
        return initial_reply, 200

    summaries: List[_CommandExecutionSummary] = []
    current_initial = initial_reply

    for command in commands:
        device_id = command.get("device_id")
        device = _DEVICES.get(device_id) if isinstance(device_id, str) else None

        if device and _device_is_agent(device):
            summary = _execute_agent_device_command(
                client, device, messages, current_initial, command
            )
        else:
            summary = _execute_standard_device_command(
                client, messages, current_initial, command
            )

        if summary.status != 200:
            failure_messages: List[str] = []
            for existing in summaries:
                if isinstance(existing.manual_reply, str):
                    text = existing.manual_reply.strip()
                    if text:
                        failure_messages.append(text)

            candidate = ""
            if isinstance(summary.manual_reply, str) and summary.manual_reply.strip():
                candidate = summary.manual_reply.strip()
            elif isinstance(summary.error_text, str) and summary.error_text.strip():
                candidate = summary.error_text.strip()

            if candidate:
                failure_messages.append(candidate)

            if failure_messages:
                return "\n\n".join(failure_messages), summary.status
            return initial_reply, summary.status

        summaries.append(summary)
        if isinstance(summary.manual_reply, str) and summary.manual_reply.strip():
            current_initial = summary.manual_reply

    if not summaries:
        return initial_reply, 200

    final_reply = _summarize_device_command_sequence(
        client, messages, initial_reply, summaries
    )
    return final_reply, 200


def _execute_agent_device_command(
    client: OpenAI,
    agent: DeviceState,
    messages: List[Dict[str, str]],
    initial_reply: str,
    command: Dict[str, Any],
) -> _CommandExecutionSummary:
    args = command.get("args") if isinstance(command, dict) else {}
    args_dict = args if isinstance(args, dict) else {}
    raw_instruction = args_dict.get("instruction")
    english_instruction: Optional[str] = None

    if isinstance(raw_instruction, str) and raw_instruction.strip():
        english_instruction = raw_instruction.strip()
    else:
        try:
            english_instruction = _call_llm_text(
                client, _structured_agent_instruction_prompt(messages)
            ).strip()
        except Exception as exc:  # pragma: no cover - network/SDK errors
            message = str(exc)
            return _CommandExecutionSummary(
                device_id=agent.device_id,
                command_name=AGENT_COMMAND_NAME,
                args=args_dict,
                manual_reply=message,
                instruction=None,
                is_agent=True,
                status=500,
                error_text=message,
            )

        if not english_instruction:
            message = "Failed to build instruction for device."
            return _CommandExecutionSummary(
                device_id=agent.device_id,
                command_name=AGENT_COMMAND_NAME,
                args=args_dict,
                manual_reply=message,
                instruction=None,
                is_agent=True,
                status=500,
                error_text=message,
            )

    command_args = dict(args_dict)
    command_args["instruction"] = english_instruction

    command_payload = {
        "name": AGENT_COMMAND_NAME,
        "args": command_args,
    }

    job_id = _enqueue_device_command(agent.device_id, command_payload)
    if job_id is None:
        failure_message = "指示を送信できませんでした。デバイスの接続状態を確認してください。"
        combined = (initial_reply + "\n" if initial_reply else "") + failure_message
        return _CommandExecutionSummary(
            device_id=agent.device_id,
            command_name=AGENT_COMMAND_NAME,
            args=command_args,
            manual_reply=combined,
            instruction=english_instruction,
            is_agent=True,
        )

    result = _await_device_result(agent.device_id, job_id, timeout=DEVICE_RESULT_TIMEOUT)
    device_label = _device_label_for_prompt(agent.device_id)
    if result:
        manual_reply = _manual_result_reply(
            device_label,
            english_instruction or command_payload["name"],
            result,
        )
        return _CommandExecutionSummary(
            device_id=agent.device_id,
            command_name=AGENT_COMMAND_NAME,
            args=command_args,
            manual_reply=manual_reply,
            result=result,
            instruction=english_instruction,
            is_agent=True,
        )

    timeout_reply = _timeout_reply(
        {
            "device_id": agent.device_id,
            "name": english_instruction or command_payload["name"],
            "args": command_args,
        },
        DEVICE_RESULT_TIMEOUT,
    )
    return _CommandExecutionSummary(
        device_id=agent.device_id,
        command_name=AGENT_COMMAND_NAME,
        args=command_args,
        manual_reply=timeout_reply,
        instruction=english_instruction,
        is_agent=True,
    )


def _summarize_device_command_sequence(
    client: Optional[OpenAI],
    base_messages: List[Dict[str, str]],
    initial_reply: str,
    summaries: List[_CommandExecutionSummary],
) -> str:
    fallback_parts = [
        summary.manual_reply.strip()
        for summary in summaries
        if isinstance(summary.manual_reply, str) and summary.manual_reply.strip()
    ]
    fallback_reply = "\n\n".join(fallback_parts) if fallback_parts else initial_reply

    if client is None:
        return fallback_reply

    try:
        prompt_payload = _structured_multi_command_followup_prompt(
            base_messages, initial_reply, summaries
        )
        llm_reply = _call_llm_text(client, prompt_payload)
    except Exception:
        return fallback_reply

    cleaned_reply = llm_reply.strip() if isinstance(llm_reply, str) else ""
    return cleaned_reply or fallback_reply


def _structured_multi_command_followup_prompt(
    base_messages: List[Dict[str, str]],
    initial_reply: str,
    summaries: List[_CommandExecutionSummary],
) -> Dict[str, Any]:
    device_context = _build_device_context()
    step_descriptions: List[str] = []

    for index, summary in enumerate(summaries, start=1):
        device_label = (
            _device_label_for_prompt(summary.device_id)
            if summary.device_id
            else "対象デバイス"
        )
        args_text = json.dumps(summary.args, ensure_ascii=False, default=str)
        if summary.result is not None:
            result_text = _format_result_for_prompt(summary.result)
        else:
            result_text = "No structured result was reported."

        manual = summary.manual_reply.strip() if isinstance(summary.manual_reply, str) else ""

        lines = [
            f"Step {index}:",
            f"  Device: {device_label}",
            f"  Command or instruction: {summary.instruction or summary.command_name}",
            f"  Arguments: {args_text}",
            f"  Result details (internal): {result_text}",
        ]
        if manual:
            lines.append(f"  Suggested phrasing: {manual}")

        step_descriptions.append("\n".join(lines))

    step_block = "\n\n".join(step_descriptions)

    guidance = (
        "All queued device commands have now completed. Use the step information provided "
        "below to craft the final assistant reply in Japanese.\n"
        "Write one concise paragraph per step, keep the steps in order, and separate "
        "paragraphs with a blank line.\n"
        "Clearly mention the device, what was attempted, and the outcome for each step.\n"
        "Do not invent new steps or request further actions unless explicitly requested "
        "by the user."
    )

    if initial_reply:
        guidance += f"\nThe assistant previously told the user: {initial_reply}"

    messages: List[Dict[str, str]] = [
        {"role": "system", "content": "You are an assistant supporting IoT devices."},
    ]
    if device_context:
        messages.append({"role": "system", "content": "Available device information:\n" + device_context})

    messages.extend(base_messages)

    if initial_reply:
        messages.append({"role": "assistant", "content": initial_reply})

    messages.append({"role": "system", "content": guidance})
    messages.append({"role": "system", "content": "Step summaries:\n" + step_block})
    messages.append({"role": "system", "content": "Respond now with the final Japanese reply."})

    return {"model": "gpt-4.1-2025-04-14", "input": messages}


def _chat_via_legacy(messages: List[Dict[str, str]]) -> Tuple[Dict[str, Any], int]:
    try:
        client = _client()
        parsed_response = _call_llm_and_parse(client, messages)
    except RuntimeError as exc:
        return {"error": str(exc)}, 500
    except Exception as exc:  # pragma: no cover - network/SDK errors
        return {"error": str(exc)}, 500

    reply_message = parsed_response.get("reply")
    if not isinstance(reply_message, str):
        reply_message = parsed_response.get("raw", "").strip()

    validated_commands, validation_errors = _validate_device_command_sequence(
        parsed_response.get("device_commands")
    )

    final_reply = reply_message

    if validation_errors:
        notice = "\n".join(f"(システム通知: {error})" for error in validation_errors)
        final_reply = (reply_message + "\n" if reply_message else "") + notice
        return {"reply": final_reply}, 200

    if validated_commands:
        final_reply, status = _execute_device_command_sequence(
            client, messages, reply_message, validated_commands
        )
        return {"reply": final_reply}, status

    return {"reply": final_reply}, 200


@app.get("/")
def index():
    if not session.get("authenticated"):
        return redirect(url_for("login"))
    return app.send_static_file("index.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        password = request.form.get("password", "")
        if password == APP_PASSWORD:
            session["authenticated"] = True
            return redirect(url_for("index"))
        return redirect(url_for("login", error="1"))

    if session.get("authenticated"):
        return redirect(url_for("index"))
    return app.send_static_file("login.html")


@app.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.get("/api/devices/ping")
def device_ping():
    return jsonify({"message": "ok"})


@app.post("/api/chat")
def chat():
    payload = request.get_json(silent=True) or {}
    messages = payload.get("messages", [])

    if not isinstance(messages, list):
        return jsonify({"error": "messages must be a list"}), 400

    formatted_messages = []
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")
        content = msg.get("content")
        if role not in {"system", "user", "assistant"} or not isinstance(content, str):
            continue
        formatted_messages.append({"role": role, "content": content})

    if not formatted_messages or formatted_messages[-1]["role"] != "user":
        return jsonify({"error": "last message must be from user"}), 400

    agent_device = _agent_device()
    if agent_device:
        try:
            client = _client()
            parsed_response = _call_llm_and_parse(client, formatted_messages)
        except RuntimeError as exc:
            return jsonify({"error": str(exc)}), 500
        except Exception as exc:  # pragma: no cover - network/SDK errors
            return jsonify({"error": str(exc)}), 500

        reply_message = parsed_response.get("reply")
        if not isinstance(reply_message, str):
            reply_message = parsed_response.get("raw", "").strip()

        validated_commands, validation_errors = _validate_device_command_sequence(
            parsed_response.get("device_commands")
        )

        payload: Dict[str, Any] = {"reply": reply_message}
        status: int = 200

        if validation_errors:
            notice = "\n".join(f"(システム通知: {error})" for error in validation_errors)
            payload["reply"] = (reply_message + "\n" if reply_message else "") + notice
        elif validated_commands:
            final_reply, status = _execute_device_command_sequence(
                client, formatted_messages, reply_message, validated_commands
            )
            payload = {"reply": final_reply}
    else:
        payload, status = _chat_via_legacy(formatted_messages)

    return jsonify(payload), status


@app.post("/api/devices/register")
def register_device():
    payload = request.get_json(silent=True) or {}
    device_id = payload.get("device_id")
    capabilities = payload.get("capabilities")
    meta = payload.get("meta") or {}

    if not isinstance(device_id, str) or not device_id.strip():
        return jsonify({"error": "device_id is required"}), 400
    if not isinstance(capabilities, list):
        return jsonify({"error": "capabilities must be a list"}), 400
    capabilities = _normalise_capabilities(capabilities)
    cleaned_id = device_id.strip()
    now = time.time()
    metadata = meta if isinstance(meta, dict) else {}
    manual_registration = metadata.get("registered_via") == "dashboard" or bool(
        payload.get("approved")
    )

    display_name = metadata.get("display_name") if isinstance(metadata, dict) else None
    if isinstance(display_name, str):
        trimmed = display_name.strip()
        if trimmed:
            metadata["display_name"] = trimmed
        else:
            metadata.pop("display_name", None)
    elif isinstance(metadata, dict) and "display_name" in metadata:
        metadata.pop("display_name", None)

    existing = _DEVICES.get(cleaned_id)

    if existing:
        if not existing.approved and not manual_registration:
            return (
                jsonify(
                    {
                        "error": "device not approved",
                        "message": "Device must be registered from the dashboard before connecting.",
                    }
                ),
                403,
            )

        existing.capabilities = capabilities

        if not isinstance(existing.meta, dict):
            existing.meta = {}

        incoming_meta = metadata.copy()
        if manual_registration:
            if "display_name" not in incoming_meta:
                existing.meta.pop("display_name", None)
        elif "display_name" in incoming_meta:
            incoming_meta.pop("display_name", None)

        existing.meta.update(incoming_meta)
        existing.last_seen = now
        if manual_registration:
            existing.approved = True
            existing.registered_at = existing.registered_at or now
        status = "updated"
        device_state = existing
    else:
        if not manual_registration:
            return (
                jsonify(
                    {
                        "error": "device not approved",
                        "message": "Device must be registered from the dashboard before connecting.",
                    }
                ),
                403,
            )

        device_state = DeviceState(
            device_id=cleaned_id,
            capabilities=capabilities,
            meta=metadata,
            last_seen=now,
            approved=True,
        )
        _DEVICES[cleaned_id] = device_state
        status = "registered"

    return jsonify({
        "status": status,
        "device_id": device_state.device_id,
        "device": _serialize_device(device_state),
    })


@app.get("/api/devices")
def list_devices():
    devices = [_serialize_device(device) for device in _DEVICES.values()]
    devices.sort(key=lambda d: d["device_id"])
    return jsonify({"devices": devices})


@app.patch("/api/devices/<device_id>/name")
def update_device_name(device_id: str):
    cleaned_id = (device_id or "").strip()
    if not cleaned_id:
        return jsonify({"error": "device_id is required"}), 400

    device = _DEVICES.get(cleaned_id)
    if not device:
        return jsonify({"error": "device not registered"}), 404

    payload = request.get_json(silent=True) or {}
    display_name = payload.get("display_name") if payload else None

    if not isinstance(device.meta, dict):
        device.meta = {}

    if display_name is None:
        new_name = ""
    elif isinstance(display_name, str):
        new_name = display_name.strip()
    else:
        return jsonify({"error": "display_name must be a string or null"}), 400

    if new_name:
        device.meta["display_name"] = new_name
    else:
        device.meta.pop("display_name", None)

    device.last_seen = time.time()
    return jsonify({"status": "updated", "device": _serialize_device(device)})


@app.delete("/api/devices/<device_id>")
def delete_device(device_id: str):
    cleaned_id = (device_id or "").strip()
    if not cleaned_id:
        return jsonify({"error": "device_id is required"}), 400

    device = _DEVICES.pop(cleaned_id, None)
    if not device:
        return jsonify({"error": "device not registered"}), 404

    stale_jobs = [job_id for job_id, mapped in _PENDING_JOBS.items() if mapped == cleaned_id]
    for job_id in stale_jobs:
        _PENDING_JOBS.pop(job_id, None)

    return jsonify({"status": "deleted", "device_id": cleaned_id})


@app.get("/api/devices/<device_id>/jobs/next")
def next_job(device_id: str):
    cleaned_id = (device_id or "").strip()
    if not cleaned_id:
        return jsonify({"error": "device_id is required"}), 400

    device = _DEVICES.get(cleaned_id)
    if not device:
        return jsonify({"error": "device not registered"}), 404

    device.last_seen = time.time()

    if not device.job_queue:
        return ("", 204)

    job = device.job_queue.popleft()
    return jsonify(job)


@app.post("/api/devices/<device_id>/jobs/result")
def post_result(device_id: str):
    payload = request.get_json(silent=True)
    if payload is None:
        raw_body = request.get_data(cache=False, as_text=True) or ""
        try:
            payload = json.loads(raw_body) if raw_body else {}
        except json.JSONDecodeError:
            payload = {}

    job_id = payload.get("job_id") if isinstance(payload, dict) else None
    raw_device_id = payload.get("device_id") if isinstance(payload, dict) else None

    query_device_id = request.args.get("device_id", "")
    query_job_id = request.args.get("job_id", "")
    header_device_id = request.headers.get("X-Device-ID", "")
    path_device_id = device_id

    provided_ids: List[str] = []

    def _normalise_candidate(value: Any) -> Optional[str]:
        if isinstance(value, str):
            cleaned = value.strip()
            if cleaned:
                return cleaned
        return None

    for candidate in (
        raw_device_id,
        query_device_id,
        header_device_id,
        path_device_id,
    ):
        cleaned = _normalise_candidate(candidate)
        if cleaned and cleaned not in provided_ids:
            provided_ids.append(cleaned)

    if len(provided_ids) > 1:
        return (
            jsonify({"error": "conflicting device_id values"}),
            400,
        )

    if not isinstance(job_id, str) or not job_id.strip():
        cleaned_job_id = _normalise_candidate(query_job_id)
        if cleaned_job_id:
            job_id = cleaned_job_id
        else:
            job_id = None
    else:
        job_id = job_id.strip()

    mapped_device_id: Optional[str] = None
    if job_id:
        mapped = _PENDING_JOBS.get(job_id)
        if isinstance(mapped, str) and mapped.strip():
            mapped_device_id = mapped.strip()

    resolved_device: Optional[DeviceState] = None
    mismatch_resolved_via_job = False

    if mapped_device_id and mapped_device_id in _DEVICES:
        resolved_device = _DEVICES[mapped_device_id]
        if provided_ids and mapped_device_id not in provided_ids:
            mismatch_resolved_via_job = True

    if not resolved_device:
        for candidate in provided_ids:
            device_candidate = _DEVICES.get(candidate)
            if device_candidate:
                resolved_device = device_candidate
                break

    if not resolved_device and len(_DEVICES) == 1:
        # Some edge device firmwares omit the device_id on the result endpoint.
        # When there is only a single registered device we can safely assume it
        # is the source of the result so that measurements are not dropped.
        resolved_device = next(iter(_DEVICES.values()))

    if not resolved_device:
        if provided_ids or job_id:
            return jsonify({"error": "device not registered"}), 404
        return jsonify({"error": "device_id is required"}), 400

    if (
        mapped_device_id
        and mapped_device_id in _DEVICES
        and resolved_device.device_id != mapped_device_id
    ):
        resolved_device = _DEVICES[mapped_device_id]
        mismatch_resolved_via_job = True

    device = resolved_device

    device.last_seen = time.time()
    if isinstance(job_id, str) and job_id:
        _PENDING_JOBS.pop(job_id, None)
    else:
        job_id = None
    result_record = {
        "job_id": job_id,
        "ok": bool(payload.get("ok")),
        "return_value": payload.get("return_value"),
        "stdout": payload.get("stdout"),
        "stderr": payload.get("stderr"),
        "error": payload.get("error"),
        "ts": payload.get("ts"),
        "device_id": device.device_id,
    }
    device.last_result = result_record
    if job_id:
        device.job_results[job_id] = dict(result_record)

    response_payload = {"status": "ack"}
    if mismatch_resolved_via_job:
        response_payload["warning"] = "device_id mismatch resolved via job_id"

    return jsonify(response_payload)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5006)
