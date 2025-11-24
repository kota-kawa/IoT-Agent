import json
import os
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from openai import OpenAI

from .config import AGENT_ROLE_VALUE
from .device_utils import _build_device_context, _format_result_for_prompt
from model_selection import apply_model_selection, update_override


def _current_datetime_line() -> str:
    """Return the timestamp string used in system prompts."""
    return datetime.now().strftime("現在の日時ー%Y年%m月%d日%H時%M分")


def _client() -> OpenAI:
    # OpenAI API クライアントを生成し、API キーが無い場合は例外を送出

    _, model_name, base_url = apply_model_selection("iot")
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set")
    if base_url:
        return OpenAI(api_key=api_key, base_url=base_url)
    return OpenAI(api_key=api_key)


def _structured_llm_prompt(messages: List[Dict[str, str]]) -> Dict[str, Any]:
    # LLM へ投げる構造化プロンプトとコンテキストを組み立てる
    device_context = _build_device_context()
    timestamp_line = _current_datetime_line()
    system_prompt = (
        f"{timestamp_line}\n"
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
        "requested without a runtime or duration, default the operation to "
        "5 seconds. When an action is "
        "required and multiple devices exist, you MUST select the single "
        "most appropriate device_id for each step by comparing the roles "
        "and capabilities described. Never omit 'device_id' or use an "
        "unknown value. If the correct device cannot be determined, set "
        "'device_commands' to null and ask the user to clarify which "
        "device should be used. Do not propose or attempt any device "
        "operation that is unavailable or unsupported by the provided "
        "capabilities; if an action cannot be executed, explain that and "
        "set 'device_commands' to null. When the user requests a device "
        "action and it is possible to execute with the available devices "
        "and capabilities, you MUST return the executable command JSON in "
        "'device_commands'. Prefer devices tagged with the "
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

    _, model_name, _ = apply_model_selection("iot")

    return {
        "model": model_name,
        "input": [
            {"role": "system", "content": system_prompt},
            {"role": "system", "content": context_message},
            *messages,
        ],
    }


def _extract_json_object(text: str) -> Tuple[Optional[Any], Optional[str]]:
    # LLM 応答文字列から先頭の JSON オブジェクトを抽出する
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
    # LLM 応答から reply と device_commands を抽出して辞書化

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


def _normalise_conversation_messages(raw_messages: Any) -> List[Dict[str, str]]:
    # 外部エージェントから渡される会話履歴を内部フォーマットへ整形

    if not isinstance(raw_messages, list):
        return []

    normalised: List[Dict[str, str]] = []

    for entry in raw_messages:
        if not isinstance(entry, dict):
            continue
        content = entry.get("content")
        if not isinstance(content, str):
            continue

        raw_role = entry.get("role")
        role = "system"
        if isinstance(raw_role, str):
            lowered = raw_role.strip().lower()
            if lowered in {"system", "user", "assistant"}:
                role = lowered
            elif lowered in {"agent", "assistant_agent", "assistant_ai"}:
                role = "assistant"
            elif lowered in {"client", "customer", "human"}:
                role = "user"
            else:
                role = "system"

        normalised.append({"role": role, "content": content})

    return normalised


def _structured_conversation_review_prompt(messages: List[Dict[str, str]]) -> Dict[str, Any]:
    # 会話履歴を監査し、IoT 操作が必要か判定するプロンプトを構築

    device_context = _build_device_context()
    timestamp_line = _current_datetime_line()
    system_prompt = (
        f"{timestamp_line}\n"
        "You are an operations analyst that reviews past multi-agent conversations "
        "to decide whether IoT remediation is required. Always respond with a strict "
        "JSON object containing: "
        "'action_required' (boolean), "
        "'reason' (string explaining your decision), "
        "'device_commands' (null or array of command objects with 'device_id', 'name', 'args'), "
        "'notes' (optional), "
        "'should_reply' (boolean; true if you should speak up even briefly), "
        "'reply' (short helpful message; you may mention Browser Agent or Life-Assistant Agent by name if you want them to act), "
        "'addressed_agents' (array of agent names to call out; empty if none). "
        "Only mark 'action_required' true when a concrete IoT command should run. "
        "If no direct action is needed but you have a warning or tip, set 'should_reply' to true."
    )

    context_message = (
        "Available device information:\n" + device_context
        if device_context
        else "No devices are currently registered."
    )

    conversation_dump_lines: List[str] = []
    for entry in messages:
        role = entry.get("role", "")
        content = entry.get("content")
        if not isinstance(content, str):
            continue
        conversation_dump_lines.append(f"{role}: {content.strip()}")

    conversation_dump = "\n".join(conversation_dump_lines) or "Conversation log was empty."

    return {
        "model": "gpt-4.1-2025-04-14",
        "input": [
            {"role": "system", "content": system_prompt},
            {"role": "system", "content": context_message},
            {
                "role": "user",
                "content": (
                    "Review the following conversation transcript. "
                    "Decide whether any IoT intervention is required."
                    "\n\n"
                    f"{conversation_dump}"
                ),
            },
        ],
    }


def _call_llm_for_conversation_review(
    client: OpenAI, messages: List[Dict[str, str]]
) -> Dict[str, Any]:
    # 監査用 LLM を呼び出し、action_required と device_commands を抽出

    response = client.responses.create(**_structured_conversation_review_prompt(messages))
    reply_text = getattr(response, "output_text", None) or ""

    parsed_obj, cleaned_text = _extract_json_object(reply_text)

    result: Dict[str, Any] = {
        "action_required": False,
        "reason": "",
        "device_commands": [],
        "notes": None,
        "should_reply": False,
        "reply": "",
        "addressed_agents": [],
        "raw": reply_text,
    }

    if isinstance(parsed_obj, dict):
        action_required = parsed_obj.get("action_required")
        reason = parsed_obj.get("reason")
        device_commands_field = parsed_obj.get("device_commands")
        notes = parsed_obj.get("notes")
        should_reply = parsed_obj.get("should_reply")
        reply = parsed_obj.get("reply")
        addressed_agents = parsed_obj.get("addressed_agents")

        if isinstance(action_required, bool):
            result["action_required"] = action_required

        if isinstance(reason, str) and reason.strip():
            result["reason"] = reason.strip()

        commands: List[Dict[str, Any]] = []
        if isinstance(device_commands_field, dict):
            commands = [device_commands_field]
        elif isinstance(device_commands_field, list):
            commands = [cmd for cmd in device_commands_field if isinstance(cmd, dict)]

        result["device_commands"] = commands

        if isinstance(notes, str) and notes.strip():
            result["notes"] = notes.strip()

        if isinstance(should_reply, bool):
            result["should_reply"] = should_reply
        if isinstance(reply, str):
            result["reply"] = reply.strip()
        if isinstance(addressed_agents, list):
            filtered_agents = [agent for agent in addressed_agents if isinstance(agent, str)]
            result["addressed_agents"] = filtered_agents

        if not result["reason"]:
            result["reason"] = cleaned_text or reply_text.strip()
    else:
        result["reason"] = cleaned_text or reply_text.strip()

    return result


def _call_llm_text(client: OpenAI, payload: Dict[str, Any]) -> str:
    # 指定ペイロードで LLM を呼び出し、クリーンなテキストを返す

    response = client.responses.create(**payload)
    text = getattr(response, "output_text", "")
    return text.strip()


def _structured_agent_instruction_prompt(messages: List[Dict[str, str]]) -> Dict[str, Any]:
    # エージェント向け英語命令文の生成に必要なプロンプトを組み立てる

    device_context = _build_device_context()
    timestamp_line = _current_datetime_line()
    system_prompt = (
        f"{timestamp_line}\n"
        "You are an assistant that interprets user instructions and generates "
        "IoT device operation commands. Always respond with a strict JSON "
        "object containing the key 'device_commands'. The 'device_commands' "
        "field must be either null, an empty array, or an array of objects "
        "with the keys 'device_id', 'name', and 'args'. Each array element "
        "represents one sequential task for the devices to execute. Do not "
        "wrap the JSON inside code fences. If no device action is required, "
        "set 'device_commands' to null. Only use device IDs and capability "
        "names provided in the context. When an action is required and "
        "the user does not specify a runtime or duration, default the "
        "operation to 5 seconds. When an action is required and "
        "multiple devices exist, you MUST select the single most "
        "appropriate device_id for each step by comparing the roles and "
        "capabilities described. Never omit 'device_id' or use an unknown "
        "value. If the correct device cannot be determined, set "
        "'device_commands' to null and ask the user to clarify which device "
        "should be used. Do not invent or propose operations that the "
        "available devices and capabilities do not support. When the user "
        "asks for an actionable device operation and it is feasible, you "
        "MUST output the executable command JSON in 'device_commands'."
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


def _structured_agent_followup_prompt(
    base_messages: List[Dict[str, str]],
    english_instruction: str,
    result: Dict[str, Any],
) -> Dict[str, Any]:
    # デバイス応答をもとにユーザー向け日本語要約を生成するプロンプト

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
