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

    action_catalog = meta.get("action_catalog") if isinstance(meta, dict) else None
    if isinstance(action_catalog, list):
        action_names = [
            str(entry.get("name")).strip()
            for entry in action_catalog
            if isinstance(entry, dict) and entry.get("name")
        ]
        filtered = [name for name in action_names if name]
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


def _serialize_device(device: DeviceState) -> Dict[str, Any]:
    return {
        "device_id": device.device_id,
        "capabilities": device.capabilities,
        "meta": device.meta,
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
        "'reply' and 'device_command'. The 'reply' field is a natural "
        "language response to the user. The 'device_command' field must "
        "be either null or an object with the keys 'device_id', 'name', "
        "and 'args'. Do not wrap the JSON inside code fences. If no "
        "device action is required, set 'device_command' to null. Only "
        "use device IDs and capability names provided in the context. "
        "When an action is required and multiple devices exist, you MUST "
        "select the single most appropriate device_id by comparing the "
        "roles and capabilities described. Never omit 'device_id' or use "
        "an unknown value. If the correct device cannot be determined, "
        "set 'device_command' to null and ask the user to clarify which "
        "device should be used. Prefer devices tagged with the "
        f"'{AGENT_ROLE_VALUE}' role for complex or conversational tasks."
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


def _call_llm_and_parse(client: OpenAI, messages: List[Dict[str, str]]) -> Dict[str, Any]:
    response = client.responses.create(**_structured_llm_prompt(messages))
    reply_text = getattr(response, "output_text", None) or ""
    try:
        parsed = json.loads(reply_text)
    except json.JSONDecodeError:
        parsed = {"reply": reply_text.strip(), "device_command": None}

    reply_message = parsed.get("reply")
    if not isinstance(reply_message, str):
        reply_message = reply_text.strip()

    device_command = parsed.get("device_command")
    return {
        "reply": reply_message,
        "device_command": device_command,
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
        f"Device response JSON: {_format_result_for_prompt(result)}\n"
        "Write a concise Japanese message for the user that summarises the "
        "outcome. Mention success or failure clearly and include key "
        "details from the result when helpful. Do not request further "
        "actions unless the user explicitly asked."
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
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except TypeError:
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


def _finalize_reply_with_result(
    client: OpenAI,
    base_messages: List[Dict[str, str]],
    initial_reply: str,
    command: Dict[str, Any],
    result: Dict[str, Any],
) -> str:
    device_id = command.get("device_id")
    device_label = _device_label_for_prompt(device_id) if device_id else "対象デバイス"
    command_name = command.get("name", "不明なコマンド")
    instruction = (
        "The previous device command has completed.\n"
        f"Device: {device_label}\n"
        f"Command: {command_name}\n"
        f"Arguments: {json.dumps(command.get('args') or {}, ensure_ascii=False, default=str)}\n"
        f"Result JSON: {_format_result_for_prompt(result)}\n"
        "Provide a concise Japanese reply for the user that summarises this outcome. "
        "Do not create a new device_command unless the user explicitly asked for more actions."
    )

    followup_messages: List[Dict[str, str]] = [*base_messages]
    if initial_reply:
        followup_messages.append({"role": "assistant", "content": initial_reply})
    followup_messages.append({"role": "system", "content": instruction})

    try:
        followup = _call_llm_and_parse(client, followup_messages)
    except Exception:
        return _manual_result_reply(device_label, command_name, result)

    followup_reply = followup.get("reply")
    if isinstance(followup_reply, str) and followup_reply.strip():
        return followup_reply.strip()

    return _manual_result_reply(device_label, command_name, result)


def _execute_standard_device_command(
    client: OpenAI,
    messages: List[Dict[str, str]],
    initial_reply: str,
    command: Dict[str, Any],
) -> str:
    command_payload = {
        "name": command["name"],
        "args": command["args"],
    }
    job_id = _enqueue_device_command(command["device_id"], command_payload)
    if job_id is None:
        notice = "(注意: デバイスにコマンドを送信できませんでした。)"
        return (initial_reply + "\n" if initial_reply else "") + notice

    result = _await_device_result(command["device_id"], job_id, timeout=DEVICE_RESULT_TIMEOUT)
    if result:
        return _finalize_reply_with_result(
            client,
            messages,
            initial_reply,
            command,
            result,
        )

    return _timeout_reply(command, DEVICE_RESULT_TIMEOUT)


def _execute_agent_device_command(
    client: OpenAI,
    agent: DeviceState,
    messages: List[Dict[str, str]],
    initial_reply: str,
    command: Dict[str, Any],
) -> Tuple[Dict[str, Any], int]:
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
            return {"error": str(exc)}, 500

        if not english_instruction:
            return {"error": "Failed to build instruction for device."}, 500

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
        return {"reply": combined}, 200

    result = _await_device_result(agent.device_id, job_id, timeout=DEVICE_RESULT_TIMEOUT)
    if result:
        try:
            final_reply = _call_llm_text(
                client,
                _structured_agent_followup_prompt(messages, english_instruction, result),
            )
        except Exception:  # pragma: no cover - network/SDK errors
            final_reply = ""

        reply_text = final_reply.strip() if isinstance(final_reply, str) else ""
        if not reply_text:
            reply_text = _manual_result_reply(
                _device_label_for_prompt(agent.device_id), english_instruction, result
            )

        return {"reply": reply_text}, 200

    timeout_reply = _timeout_reply(
        {
            "device_id": agent.device_id,
            "name": english_instruction,
            "args": command_args,
        },
        DEVICE_RESULT_TIMEOUT,
    )
    return {"reply": timeout_reply}, 200


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

    device_command = parsed_response.get("device_command")
    if device_command:
        validated_command, validation_error = _validate_device_command(device_command)
    else:
        validated_command, validation_error = (None, None)

    final_reply = reply_message

    if validation_error:
        notice = f"(システム通知: {validation_error})"
        final_reply = (reply_message + "\n" if reply_message else "") + notice

    if validated_command:
        final_reply = _execute_standard_device_command(
            client, messages, reply_message, validated_command
        )

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

        device_command = parsed_response.get("device_command")
        if device_command:
            validated_command, validation_error = _validate_device_command(device_command)
        else:
            validated_command, validation_error = (None, None)

        payload: Dict[str, Any] = {"reply": reply_message}
        status: int = 200

        if validation_error:
            notice = f"(システム通知: {validation_error})"
            payload["reply"] = (reply_message + "\n" if reply_message else "") + notice

        if validated_command:
            target_device = _DEVICES.get(validated_command["device_id"])
            if target_device and _device_is_agent(target_device):
                payload, status = _execute_agent_device_command(
                    client,
                    target_device,
                    formatted_messages,
                    reply_message,
                    validated_command,
                )
            else:
                final_reply = _execute_standard_device_command(
                    client, formatted_messages, reply_message, validated_command
                )
                payload, status = {"reply": final_reply}, 200
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
