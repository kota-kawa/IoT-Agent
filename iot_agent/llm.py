import json
import os
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from openai import OpenAI

from .config import AGENT_ROLE_VALUE
from .device_utils import _build_device_context, _format_result_for_prompt
from model_selection import apply_model_selection, provider_supports_vision, update_override


def _content_to_text(content: Any) -> str:
    """Normalise chat completion content into a plain string."""

    if content is None:
        return ""
    if isinstance(content, str):
        return content

    if hasattr(content, "text") and isinstance(content.text, str):
        return content.text

    # Newer SDKs and some providers return a list of content parts
    if isinstance(content, list):
        parts: List[str] = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
                continue
            if hasattr(part, "text") and isinstance(part.text, str):
                parts.append(part.text)
                continue
            if isinstance(part, dict):
                for key in ("text", "data", "content"):
                    value = part.get(key)
                    if isinstance(value, str):
                        parts.append(value)
                        break
        joined = "\n".join(p.strip() for p in parts if isinstance(p, str) and p.strip())
        if joined:
            return joined

    if isinstance(content, dict):
        for key in ("text", "data", "content"):
            value = content.get(key)
            if isinstance(value, str) and value.strip():
                return value

    return str(content).strip()


def _current_datetime_line() -> str:
    """Return the timestamp string used in system prompts."""
    return datetime.now().strftime("現在の日時ー%Y年%m月%d日%H時%M分")


def _client() -> OpenAI:
    # OpenAI API クライアントを生成し、API キーが無い場合は例外を送出

    provider, model_name, base_url = apply_model_selection("iot")
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set")

    client_kwargs: Dict[str, Any] = {"api_key": api_key}
    if base_url:
        client_kwargs["base_url"] = base_url

    return OpenAI(**client_kwargs)


def _structured_llm_prompt(
    messages: List[Dict[str, str]], retry_instruction: Optional[str] = None
) -> Dict[str, Any]:
    # LLM へ投げる構造化プロンプトとコンテキストを組み立てる
    device_context = _build_device_context()
    timestamp_line = _current_datetime_line()
    system_prompt = (
        f"{timestamp_line}\n"
        "You are an assistant that manages IoT devices for the user. "
        "Always respond with a strict JSON object containing ONLY the keys "
        "'reply' and 'device_commands'. Your entire output must parse as JSON "
        "without code fences or trailing text. Valid shapes are exactly:\n"
        '{"reply": "日本語の返答", "device_commands": null}\n'
        '{"reply": "日本語の返答", "device_commands": []}\n'
        '{"reply": "日本語の返答", "device_commands": [{"device_id": "device-id", "name": "capability", "args": {"duration": 5}}]}\n'
        "The 'reply' field is a natural language response to the user in Japanese. "
        "The 'device_commands' field must be either null, an empty array, or an "
        "array of objects with the keys 'device_id', 'name', and 'args'. Each array "
        "element represents one sequential task for the devices to execute. Do "
        "not wrap the JSON inside code fences. If no device action is required, "
        "set 'device_commands' to null. Only use device IDs and capability names "
        "provided in the context. When an action is requested without a runtime "
        "or duration, default the operation to 5 seconds. When an action is "
        "required and multiple devices exist, you MUST select the single most "
        "appropriate device_id for each step by comparing the roles and "
        "capabilities described. Never omit 'device_id' or use an unknown value. "
        "If the correct device cannot be determined, set 'device_commands' to null "
        "and ask the user to clarify which device should be used. Do not propose "
        "or attempt any device operation that is unavailable or unsupported by the "
        "provided capabilities; if an action cannot be executed, explain that and "
        "set 'device_commands' to null. When the user requests a device action and "
        "it is possible to execute with the available devices and capabilities, you "
        "MUST return the executable command JSON in 'device_commands'. Prefer "
        f"devices tagged with the '{AGENT_ROLE_VALUE}' role for complex or "
        "conversational tasks. The 'reply' value must be written in Japanese prose "
        "without including JSON syntax, code formatting, or explicit mentions of "
        "'JSON'. Summarise any structured information conversationally."
    )
    provider, model_name, _ = apply_model_selection("iot")
    if provider_supports_vision(provider):
        system_prompt += (
            " When the user asks to see the surroundings or wants to know what the camera sees, "
            "enqueue the 'capture_camera_photo' capability first so you can base your reply on the photo."
        )
    else:
        system_prompt += (
            " Camera-based situational awareness is disabled for the current model selection; "
            "if the user asks to see the surroundings, explain that the current model cannot use the camera "
            "and do not add the 'capture_camera_photo' capability."
        )

    context_message = (
        "Available device information:\n" + device_context
        if device_context
        else "No devices are currently registered."
    )

    prompt_messages: List[Dict[str, str]] = [
        {"role": "system", "content": system_prompt},
        {"role": "system", "content": context_message},
        *messages,
    ]
    if retry_instruction:
        prompt_messages.append({"role": "system", "content": retry_instruction})

    return {
        "model": model_name,
        "messages": prompt_messages,
    }


def _extract_json_object(text: Any) -> Tuple[Optional[Any], Optional[str]]:
    # LLM 応答文字列から先頭の JSON オブジェクトを抽出する
    if text is None:
        return None, ""

    if isinstance(text, dict):
        return text, ""

    if isinstance(text, list):
        joined = "\n".join(_content_to_text(part) for part in text)
        text = joined

    stripped = str(text).strip()
    # コードフェンスの削除
    if stripped.startswith("```json"):
        stripped = stripped[7:]
    if stripped.startswith("```"):
        stripped = stripped[3:]
    if stripped.endswith("```"):
        stripped = stripped[:-3]
    stripped = stripped.strip()

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

    max_attempts = 3
    last_cleaned: str = ""
    last_raw: str = ""

    def _validate_payload(payload: Any, raw_text: str) -> Tuple[Optional[Dict[str, Any]], List[str]]:
        if not isinstance(payload, dict):
            return None, ["LLM response was not a JSON object with 'reply' and 'device_commands' keys."]

        errors: List[str] = []
        reply_field = payload.get("reply")
        if not isinstance(reply_field, str) or not reply_field.strip():
            errors.append("reply must be a non-empty string.")

        device_commands_field = payload.get("device_commands")
        commands: List[Dict[str, Any]] = []
        if device_commands_field is None:
            commands = []
        elif isinstance(device_commands_field, dict):
            device_commands_field = [device_commands_field]
        if isinstance(device_commands_field, list):
            for index, item in enumerate(device_commands_field, start=1):
                if not isinstance(item, dict):
                    errors.append(f"device_commands[{index}] must be an object.")
                    continue
                device_id = item.get("device_id")
                name = item.get("name")
                args = item.get("args")
                if not isinstance(device_id, str) or not device_id.strip():
                    errors.append(f"device_commands[{index}].device_id must be a non-empty string.")
                if not isinstance(name, str) or not name.strip():
                    errors.append(f"device_commands[{index}].name must be a non-empty string.")
                if args is None:
                    args = {}
                if not isinstance(args, dict):
                    errors.append(f"device_commands[{index}].args must be a JSON object.")
                commands.append(
                    {
                        "device_id": device_id.strip() if isinstance(device_id, str) else "",
                        "name": name.strip() if isinstance(name, str) else "",
                        "args": args if isinstance(args, dict) else {},
                    }
                )
        elif device_commands_field is not None:
            errors.append("device_commands must be null or an array of objects.")

        if errors:
            return None, errors

        reply_message = reply_field.strip()
        return {
            "reply": reply_message,
            "device_commands": commands,
            "raw": raw_text,
        }, []

    retry_instruction: Optional[str] = None
    provider, _, _ = apply_model_selection("iot")

    for attempt in range(1, max_attempts + 1):
        prompt_kwargs = _structured_llm_prompt(messages, retry_instruction)
        
        # JSONモードの使用可否
        # OpenAI, Gemini, Groq (Llama 3.1) は response_format={"type": "json_object"} をサポート
        extra_args = {}
        if provider in ["openai", "groq", "gemini"]:
            extra_args["response_format"] = {"type": "json_object"}

        try:
            response = client.chat.completions.create(**prompt_kwargs, **extra_args)
            choice = response.choices[0] if response and response.choices else None
            message = choice.message if choice else None
            parsed_payload = getattr(message, "parsed", None) if message else None
            if parsed_payload is None and isinstance(message, dict):
                parsed_payload = message.get("parsed")
            content = getattr(message, "content", None) if message else None
            reply_text = _content_to_text(content)
        except Exception as e:
            # 呼び出しエラーの場合は即座に失敗扱いせず、ログに残してリトライするか例外を投げる
            # ここではエラーとして処理を継続
            reply_text = ""
            last_raw = str(e)
            # ネットワークエラーなどはリトライしても無駄な場合があるため、attemptを進める

        parsed_obj = parsed_payload
        cleaned_text = ""
        if parsed_obj is None:
            parsed_obj, cleaned_text = _extract_json_object(reply_text)

        raw_text = reply_text or (json.dumps(parsed_obj) if parsed_obj is not None else "")
        last_raw = raw_text
        last_cleaned = cleaned_text or raw_text.strip()

        validated, validation_errors = _validate_payload(parsed_obj, raw_text)
        if validated:
            return validated

        if attempt == max_attempts:
            break

        error_line = "; ".join(validation_errors) if validation_errors else "Output was not valid JSON."
        retry_instruction = (
            "The previous reply was invalid and could not be parsed. Reason: "
            f"{error_line} Regenerate NOW using only a valid JSON object that matches "
            'the schema {"reply": "<string>", "device_commands": null | [] | [{"device_id": "<id>", "name": "<capability>", "args": {}}]}. '
            "Do not include code fences, markdown, or any commentary."
        )

    return {
        "reply": last_cleaned or last_raw or "LLM response could not be parsed.",
        "device_commands": [],
        "raw": last_raw,
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

    review_model = apply_model_selection("iot")[1] or "gpt-4o"

    return {
        "model": review_model,
        "messages": [
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

    kwargs = _structured_conversation_review_prompt(messages)
    
    # 標準的なチャット完了呼び出しを使用
    provider, _, _ = apply_model_selection("iot")
    extra_args = {}
    if provider in ["openai", "groq", "gemini"]:
        extra_args["response_format"] = {"type": "json_object"}

    response = client.chat.completions.create(**kwargs, **extra_args)
    choice = response.choices[0] if response and response.choices else None
    message = choice.message if choice else None
    parsed_payload = getattr(message, "parsed", None) if message else None
    if parsed_payload is None and isinstance(message, dict):
        parsed_payload = message.get("parsed")
    content = getattr(message, "content", None) if message else None
    reply_text = _content_to_text(content)

    parsed_obj = parsed_payload
    cleaned_text = ""
    if parsed_obj is None:
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
    # payloadには "model" と "input" (これは古い形式) または "messages" が含まれる可能性がある
    
    model = payload.get("model")
    messages = payload.get("messages")
    if not messages and "input" in payload:
        messages = payload["input"]
    
    if not model or not messages:
        raise ValueError("Invalid payload for _call_llm_text")

    response = client.chat.completions.create(model=model, messages=messages)
    choice = response.choices[0] if response and response.choices else None
    message = choice.message if choice else None
    content = getattr(message, "content", None) if message else None
    text = _content_to_text(content)
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

    model_name = apply_model_selection("iot")[1]

    return {
        "model": model_name,
        "messages": [
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

    model_name = apply_model_selection("iot")[1]
    return {"model": model_name, "messages": messages}
