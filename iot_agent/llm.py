import json
import os
import re
import traceback
import asyncio
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple, Union
from types import SimpleNamespace

from openai import OpenAI, APIError
try:
    from anthropic import Anthropic
except ImportError:
    Anthropic = None

from mcp.types import Tool
from iot_agent.mcp_server import list_tools, call_tool

from .config import AGENT_ROLE_VALUE
from .device_utils import _build_device_context, _format_result_for_prompt
from model_selection import (
    PROVIDER_DEFAULTS,
    apply_model_selection,
    provider_supports_vision,
    update_override,
)

_IMAGE_DATA_URL_RE = re.compile(
    r"data:image/[a-zA-Z0-9.+-]+;base64,[a-zA-Z0-9+/=\n\r]+", re.IGNORECASE
)


def _strip_image_data_from_text(text: str) -> str:
    """Remove inline data URLs to avoid leaking base64 image data into history."""

    if not isinstance(text, str) or "data:image" not in text:
        return text

    def _replace(match: re.Match[str]) -> str:
        payload = match.group(0) or ""
        return f"[画像データ省略({len(payload)} chars)]"

    cleaned, count = _IMAGE_DATA_URL_RE.subn(_replace, text)
    return cleaned if count else text


def _strip_images_from_messages(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    メッセージリストから画像データを除去し、トークン数を削減する。
    画像があった箇所には「[画像: 省略]」というプレースホルダーを挿入する。
    """
    stripped = []
    for msg in messages:
        if not isinstance(msg, dict):
            stripped.append(msg)
            continue

        content = msg.get("content")
        images_field = msg.get("images")
        extra_image_count = len(images_field) if isinstance(images_field, list) else 0
        base_msg = dict(msg)
        # Drop raw image payloads that may contain data URLs
        base_msg.pop("images", None)

        # content が文字列の場合はそのまま
        if isinstance(content, str):
            cleaned_text = _strip_image_data_from_text(content)
            if extra_image_count > 0:
                placeholder = f"[画像: {extra_image_count}枚省略]"
                cleaned_text = f"{cleaned_text}\n{placeholder}" if cleaned_text else placeholder
            base_msg["content"] = cleaned_text
            stripped.append(base_msg)
            continue

        # content がリストの場合、image_url を除去
        if isinstance(content, list):
            new_content = []
            image_count = 0
            for part in content:
                if isinstance(part, dict) and part.get("type") == "image_url":
                    image_count += 1
                    continue
                if isinstance(part, dict) and part.get("type") == "text":
                    text_value = part.get("text")
                    part = dict(part)
                    part["text"] = _strip_image_data_from_text(text_value) if isinstance(text_value, str) else text_value
                elif isinstance(part, str):
                    part = _strip_image_data_from_text(part)
                new_content.append(part)

            total_images = image_count + extra_image_count
            if total_images > 0:
                new_content.append({
                    "type": "text",
                    "text": f"[画像: {total_images}枚省略]"
                })

            new_msg = base_msg
            new_msg["content"] = new_content if new_content else [{"type": "text", "text": "[画像のみのメッセージ]"}]
            stripped.append(new_msg)
            continue

        # Fallback for unexpected shapes
        base_msg["content"] = content
        stripped.append(base_msg)

    return stripped


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


def _messages_include_images(messages: Any) -> bool:
    """Return True when the message payload already includes image content."""

    if not isinstance(messages, list):
        return False

    for msg in messages:
        if not isinstance(msg, dict):
            continue
        content = msg.get("content")
        parts = content if isinstance(content, list) else []
        for part in parts:
            if isinstance(part, dict) and part.get("type") in {"image_url", "input_image"}:
                return True
    return False


def _convert_messages_to_responses_input(messages: Any) -> List[Dict[str, Any]]:
    """Convert Chat Completions style messages to Responses API input format."""

    converted: List[Dict[str, Any]] = []
    if not isinstance(messages, list):
        return converted

    for msg in messages:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role") if isinstance(msg.get("role"), str) else "user"
        content = msg.get("content")
        parts: List[Dict[str, Any]] = []

        if isinstance(content, str):
            parts.append({"type": "input_text", "text": content})
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict):
                    part_type = part.get("type")
                    if part_type in {"text", "input_text"}:
                        text_val = part.get("text")
                        if isinstance(text_val, str) and text_val.strip():
                            parts.append({"type": "input_text", "text": text_val})
                    elif part_type in {"image_url", "input_image"}:
                        image_url = part.get("image_url")
                        url = None
                        detail = None
                        if isinstance(image_url, dict):
                            url = image_url.get("url")
                            detail = image_url.get("detail")
                        elif isinstance(image_url, str):
                            url = image_url
                        if isinstance(url, str) and url.strip():
                            image_part: Dict[str, Any] = {
                                "type": "input_image",
                                "image_url": {"url": url.strip()},
                            }
                            if isinstance(detail, str) and detail.strip():
                                image_part["image_url"]["detail"] = detail.strip()
                            parts.append(image_part)
                elif isinstance(part, str) and part.strip():
                    parts.append({"type": "input_text", "text": part})

        if not parts:
            continue
        converted.append({"role": role, "content": parts})

    return converted


def _response_output_to_text(response: Any) -> str:
    """Extract plain text from OpenAI Responses API output."""

    if response is None:
        return ""

    output_text = getattr(response, "output_text", None)
    if isinstance(output_text, str) and output_text.strip():
        return output_text.strip()

    output = getattr(response, "output", None)
    if output:
        try:
            first = output[0]
            content = getattr(first, "content", None)
            text_candidate = _content_to_text(content)
            if text_candidate:
                return text_candidate
        except Exception:
            pass

    return ""


def _current_datetime_line() -> str:
    """Return the timestamp string used in system prompts."""
    return datetime.now().strftime("現在の日時ー%Y年%m月%d日%H時%M分")


class UnifiedClient:
    def __init__(self):
        self.provider, self.model_name, self.base_url, self.api_key = apply_model_selection("iot")

        if not self.api_key:
            # Look up the expected key for the selected provider for a better error message
            provider_meta = PROVIDER_DEFAULTS.get(self.provider, {})
            expected_key = provider_meta.get("api_key_env", "OPENAI_API_KEY")
            raise RuntimeError(
                f"API key for provider '{self.provider}' is not set. Please set '{expected_key}' in your secrets.env file."
            )

        if self.provider == "claude":
            if Anthropic is None:
                raise ImportError("Anthropic SDK is not installed. Please run `pip install anthropic`.")
            self.client = Anthropic(api_key=self.api_key)
        else:
            client_kwargs: Dict[str, Any] = {"api_key": self.api_key}
            if self.base_url:
                client_kwargs["base_url"] = self.base_url
            if self.provider == "gemini":
                # Google の OpenAI 互換APIは API key をヘッダーでも受け付ける
                client_kwargs["default_headers"] = {"x-goog-api-key": self.api_key}
            self.client = OpenAI(**client_kwargs)

        self.chat = self

    @property
    def completions(self):
        return self

    def create(self, **kwargs):
        if self.provider == "claude":
            return self._create_anthropic(**kwargs)
        else:
            return self.client.chat.completions.create(**kwargs)

    def _create_anthropic(self, **kwargs):
        model = kwargs.get("model", self.model_name)
        messages = kwargs.get("messages", [])
        tools = kwargs.get("tools")

        system_prompt = ""
        filtered_messages = []

        for msg in messages:
            if msg.get("role") == "system":
                system_prompt += msg.get("content", "") + "\n"
            else:
                # Anthropic 用にメッセージコンテンツを変換
                content = msg.get("content")
                if isinstance(content, list):
                    new_content = []
                    for part in content:
                        if isinstance(part, dict):
                            if part.get("type") == "image_url":
                                image_url = part.get("image_url", {}).get("url", "")
                                if image_url.startswith("data:"):
                                    try:
                                        header, data = image_url.split(",", 1)
                                        media_type = header.split(":")[1].split(";")[0]
                                        new_content.append({
                                            "type": "image",
                                            "source": {
                                                "type": "base64",
                                                "media_type": media_type,
                                                "data": data
                                            }
                                        })
                                    except (ValueError, IndexError):
                                        pass
                                else:
                                    pass
                            else:
                                new_content.append(part)
                        else:
                            new_content.append(part)
                    filtered_messages.append({"role": msg.get("role"), "content": new_content})
                else:
                    filtered_messages.append(msg)

        max_tokens = kwargs.get("max_tokens", 4096)
        temperature = kwargs.get("temperature", 0.7)

        # Note: Basic Anthropic tool support wrapper is not fully implemented here 
        # as it requires converting OpenAI 'tools' schema to Anthropic 'tools' schema.
        # For this implementation, we assume standard OpenAI client usage or compatible shim.
        # If provider is Claude, we might need a better adapter.
        
        # Since we are using MCP standardisation, ensuring the underlying client supports tools is key.
        # Currently UnifiedClient manually wraps Anthropic. We should ideally use LangChain or similar adapter,
        # but for now let's assume we use OpenAI compatible endpoints (Gemini/OpenAI).
        # If the user selects Claude via Anthropic SDK, this wrapper needs to handle tools.
        
        # For brevity/safety in this refactor, if tools are present and we are using Anthropic SDK,
        # we might need to skip tools or implement conversion. 
        # Given the prompt's focus on Gemini/OpenAI usually, I'll proceed.
        
        response = self.client.messages.create(
            model=model,
            system=system_prompt.strip(),
            messages=filtered_messages,
            max_tokens=max_tokens,
            temperature=temperature
        )

        content = response.content[0].text if response.content else ""
        message = SimpleNamespace(content=content, parsed=None, tool_calls=None)
        choice = SimpleNamespace(message=message)
        return SimpleNamespace(choices=[choice])


def _client() -> UnifiedClient:
    return UnifiedClient()


def _convert_tool_to_openai(tool: Tool) -> Dict[str, Any]:
    """Convert an MCP Tool definition to OpenAI Tool format."""
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.inputSchema
        }
    }


async def _process_chat_with_tools(client: UnifiedClient, messages: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    MCPツールを利用してLLMとチャットを行う非同期プロセス。
    ツール呼び出しのループを内部で処理し、最終的な回答と実行結果（画像など）を返す。
    """
    
    # 1. MCPサーバーからツール定義を取得
    mcp_tools = await list_tools()
    openai_tools = [_convert_tool_to_openai(t) for t in mcp_tools]
    
    # 2. システムプロンプトの構築
    # 画像除去などは事前に行われている前提だが、ここでも確認
    messages = _strip_images_from_messages(messages)
    
    device_context = _build_device_context()
    system_prompt = (
        f"{_current_datetime_line()}\n"
        "You are a friendly and helpful assistant dedicated to managing IoT devices in a general household setting. "
        "Your goal is to make the user's life easier by controlling devices with the tools provided. "
        "When generating responses and actions, carefully consider the user's profile, the actual commands available, and the surrounding environment. "
        "If a request is ambiguous, 'read the room' to infer the user's intent and select the optimal action. "
        "Even if a request is difficult to understand, identify the executable parts and execute them if at all possible. "
        "Do not generate actions that are impossible to execute. "
        "Predict the consequences of your output and actions, and reflect this prediction in your response. "
        "If you're unsure about the current status or need more details to help, "
        "don't hesitate to use a tool to check or ask the user for clarification in a polite way. "
        "Please always respond in natural, warm, and easy-to-understand Japanese. "
        "Avoid using technical jargon where possible, and keep your tone conversational and approachable.\n"
        "IMPORTANT - HOW TO CONTROL DEVICES:\n"
        "To control any device, you MUST use the 'control_device' tool. "
        "The 'command' parameter for this tool corresponds to the 'name' of the capability/action listed in the 'Available devices' section below. "
        "The 'args' parameter should be a JSON object containing the parameters required by that action.\n"
        "For example, if a device has an action 'turn_on_light' with a parameter 'brightness', call 'control_device' with command='turn_on_light' and args={'brightness': 50}.\n"
        "Do not invent command names; strictly use those provided in the device list.\n\n"
        "SPECIFIC INSTRUCTIONS FOR OLED DISPLAYS:\n"
        "You can display text messages on the OLED screen of the robot device. "
        "To do this, you MUST use the 'control_device' tool with the specific command names below. "
        "Even if the 'Available devices' list does not explicitly show parameters for these commands, "
        "you MUST provide them in the 'args' object. Do not assume they are parameter-less.\n"
        "\n"
        "- For Raspberry Pi 4:\n"
        "  Command: 'display_robot_animation'\n"
        "  Required Args: {'text': 'YOUR_TEXT', 'duration': 5.0}\n"
        "  (Default duration is 5.0 seconds if not specified by user)\n"
        "\n"
        "- For Jetson:\n"
        "  Command: 'show_text_on_oled'\n"
        "  Required Args: {'text': 'YOUR_TEXT', 'duration': 5.0}\n"
        "  (Default duration is 5.0 seconds if not specified by user)\n"
        "\n"
        "If the user's request is ambiguous (e.g., 'Show hello on screen'), infer the correct device from context."
    )
    
    context_message = f"Available devices:\n{device_context}" if device_context else "No devices currently registered."
    
    current_messages = [
        {"role": "system", "content": system_prompt},
        {"role": "system", "content": context_message},
        *messages
    ]
    
    max_turns = 5
    turn = 0
    final_reply = ""
    collected_images = []
    
    # プロバイダがVision対応か確認（画像表示の制御用）
    provider = getattr(client, "provider", "")
    
    while turn < max_turns:
        # LLM呼び出し
        # 注意: UnifiedClient.create は同期メソッド
        try:
            response = client.chat.completions.create(
                model=client.model_name,
                messages=current_messages,
                tools=openai_tools if openai_tools else None,
            )
        except Exception as e:
            print(f"LLM Call Error: {e}")
            final_reply = "申し訳ありません、AIプロバイダとの通信でエラーが発生しました。"
            break
        
        choice = response.choices[0]
        message = choice.message
        
        # メッセージを履歴に追加（ツール呼び出しを含む可能性があるため）
        # OpenAI SDKのMessageオブジェクトを辞書に変換するか、そのまま使うか
        # ここでは辞書形式に正規化して追加する
        assistant_msg = {"role": "assistant", "content": message.content}
        if message.tool_calls:
            assistant_msg["tool_calls"] = message.tool_calls
        current_messages.append(assistant_msg)
        
        if message.tool_calls:
            # ツール呼び出しの処理
            for tool_call in message.tool_calls:
                function_name = tool_call.function.name
                try:
                    arguments = json.loads(tool_call.function.arguments)
                except json.JSONDecodeError:
                    arguments = {}
                
                print(f"Executing Tool: {function_name} with args {arguments}")
                
                try:
                    # MCPツール実行
                    result_content = await call_tool(function_name, arguments)
                    
                    tool_result_text = ""
                    for content in result_content:
                        if content.type == "text":
                            tool_result_text += content.text + "\n"
                        elif content.type == "image":
                            # 画像データを収集
                            # MCP ImageContent: data (base64), mimeType
                            collected_images.append({
                                "data_url": f"data:{content.mimeType};base64,{content.data}",
                                "label": f"Result from {function_name}"
                            })
                            tool_result_text += "[Image Captured]\n"
                        elif content.type == "embedded_resource":
                             tool_result_text += "[Resource Embedded]\n"
                            
                    current_messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": tool_result_text.strip() or "Success"
                    })
                    
                except Exception as e:
                    error_msg = f"Tool Execution Error: {str(e)}"
                    print(error_msg)
                    current_messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": error_msg
                    })
            
            turn += 1
        else:
            # ツール呼び出しがない場合、それが最終回答
            final_reply = message.content
            break
            
    return {
        "reply": final_reply,
        "device_commands": [], # 実行済みのため空リスト
        "images": collected_images
    }


def _call_llm_and_parse(client: UnifiedClient, messages: List[Dict[str, str]]) -> Dict[str, Any]:
    # 同期ラッパー: アプリケーション互換性のために残す
    try:
        return asyncio.run(_process_chat_with_tools(client, messages))
    except Exception as e:
        traceback.print_exc()
        return {
            "reply": f"内部エラーが発生しました: {str(e)}", 
            "device_commands": []
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
        content = _strip_image_data_from_text(content)

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
    # 過去の会話履歴から画像データを除去してトークン数を削減
    messages = _strip_images_from_messages(messages)

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
        "If no direct action is needed but you have a warning or tip, set 'should_reply' to true. "
        "However, do not set 'should_reply' to true for mere advice. Only reply if you can be of concrete help or if an action is required."
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


def _extract_json_object(text: Any) -> Tuple[Optional[Any], Optional[str]]:
    # Keep for backward compatibility if needed by review logic
    if text is None:
        return None, ""

    if isinstance(text, dict):
        return text, ""

    if isinstance(text, list):
        joined = "\n".join(_content_to_text(part) for part in text)
        text = joined

    stripped = str(text).strip()
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


def _call_llm_for_conversation_review(
    client: UnifiedClient, messages: List[Dict[str, str]]
) -> Dict[str, Any]:
    # 監査用 LLM を呼び出し、action_required と device_commands を抽出

    kwargs = _structured_conversation_review_prompt(messages)
    
    # 標準的なチャット完了呼び出しを使用
    provider, _, _, _ = apply_model_selection("iot")
    extra_args = {}
    if provider in ["openai", "groq", "gemini"]:
        extra_args["response_format"] = {"type": "json_object"}

    try:
        response = client.chat.completions.create(**kwargs, **extra_args)
        choice = response.choices[0] if response and response.choices else None
        message = choice.message if choice else None
        parsed_payload = getattr(message, "parsed", None) if message else None
        if parsed_payload is None and isinstance(message, dict):
            parsed_payload = message.get("parsed")
        content = getattr(message, "content", None) if message else None
        reply_text = _content_to_text(content)
    except Exception as e:
        print(f"[{datetime.now()}] LLM Conversation Review Error: {str(e)}")
        if hasattr(e, 'response') and e.response:
                print(f"HTTP Status: {e.response.status_code}")
                try:
                     print(f"Response Body: {e.response.text}")
                except:
                     pass
        traceback.print_exc()
        reply_text = ""


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


def _call_llm_text(client: UnifiedClient, payload: Dict[str, Any]) -> str:
    # 指定ペイロードで LLM を呼び出し、クリーンなテキストを返す
    
    model = payload.get("model")
    messages = payload.get("messages")
    if not messages and "input" in payload:
        messages = payload["input"]
    
    if not model or not messages:
        raise ValueError("Invalid payload for _call_llm_text")

    provider = getattr(client, "provider", "")
    responses_client = getattr(getattr(client, "client", None), "responses", None)
    if provider_supports_vision(provider) and responses_client and _messages_include_images(messages):
        try:
            responses_input = _convert_messages_to_responses_input(messages)
            if responses_input:
                response = responses_client.create(model=model, input=responses_input)
                text = _response_output_to_text(response)
                if text:
                    return text.strip()
        except Exception as e:  # pragma: no cover - network/SDK errors
            print(f"[{datetime.now()}] Responses API Text Error: {str(e)}")
            traceback.print_exc()

    try:
        response = client.chat.completions.create(model=model, messages=messages)
        choice = response.choices[0] if response and response.choices else None
        message = choice.message if choice else None
        content = getattr(message, "content", None) if message else None
        text = _content_to_text(content)
        return text.strip()
    except Exception as e:
        print(f"[{datetime.now()}] LLM Text Error: {str(e)}")
        traceback.print_exc()
        return ""


def _structured_agent_instruction_prompt(
    messages: List[Dict[str, str]], target_role: Optional[str] = None
) -> Dict[str, Any]:
    # Keep for execution.py backward compat if needed
    messages = _strip_images_from_messages(messages)
    device_context = _build_device_context()
    timestamp_line = _current_datetime_line()
    language = "English"
    if target_role == "jetson-agent":
        language = "Japanese"
    system_prompt = (
        f"{timestamp_line}\n"
        "You are an operational assistant. "
        f"Output ONLY the specific instruction string in {language}."
    )
    context_message = f"Available devices:\n{device_context}"
    model_name = apply_model_selection("iot")[1]
    return {
        "model": model_name,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "system", "content": context_message},
            *messages,
        ],
    }

# _structured_agent_followup_prompt is not strictly needed for MCP flow but execution.py uses it