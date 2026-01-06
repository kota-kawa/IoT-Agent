import json
import os
import re
import time
import traceback
import asyncio
import threading
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple, Union
from types import SimpleNamespace

from openai import OpenAI, AsyncOpenAI, APIError
try:
    from openai import APIConnectionError, APITimeoutError, BadRequestError, RateLimitError
except Exception:  # pragma: no cover - fallback for older SDKs
    APIConnectionError = APITimeoutError = BadRequestError = RateLimitError = APIError
try:
    from anthropic import Anthropic, AsyncAnthropic
except ImportError:
    Anthropic = None
    AsyncAnthropic = None

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


def _split_text_and_images(text: str) -> List[Dict[str, Any]]:
    """Split text containing data URLs into a list of text/image_url content parts."""
    parts = []
    last_idx = 0
    for match in _IMAGE_DATA_URL_RE.finditer(text):
        start, end = match.span()
        if start > last_idx:
            parts.append({"type": "text", "text": text[last_idx:start]})

        data_url = match.group(0)
        # Ensure data URL is a single line for API compatibility
        clean_url = data_url.replace("\n", "").replace("\r", "")
        parts.append({"type": "image_url", "image_url": {"url": clean_url}})
        last_idx = end

    if last_idx < len(text):
        parts.append({"type": "text", "text": text[last_idx:]})

    return parts


def _strip_image_data_from_text(text: str) -> str:
    """Remove inline data URLs to avoid leaking base64 image data into history."""

    if not isinstance(text, str) or "data:image" not in text:
        return text

    def _replace(match: re.Match[str]) -> str:
        payload = match.group(0) or ""
        return f"[画像データ省略({len(payload)} chars)]"

    cleaned, count = _IMAGE_DATA_URL_RE.subn(_replace, text)
    return cleaned if count else text


def _sanitize_messages(
    messages: List[Dict[str, Any]], allow_vision: bool = False
) -> List[Dict[str, Any]]:
    """
    メッセージリストから画像データを処理する。
    allow_vision=True の場合、インライン画像URLをVision API用の形式(image_url)に変換して保持する。
    allow_vision=False の場合、画像を除去してトークン数を削減する。
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

        # content が文字列の場合
        if isinstance(content, str):
            if allow_vision and "data:image" in content:
                # Vision有効かつ画像データが含まれる場合、分割して保持
                new_parts = _split_text_and_images(content)
                if extra_image_count > 0:
                    new_parts.append(
                        {"type": "text", "text": f"\n[画像: {extra_image_count}枚省略]"}
                    )
                base_msg["content"] = new_parts
                stripped.append(base_msg)
                continue

            # Vision無効、または画像なし -> テキスト除去/維持
            cleaned_text = _strip_image_data_from_text(content)
            if extra_image_count > 0:
                placeholder = f"[画像: {extra_image_count}枚省略]"
                cleaned_text = (
                    f"{cleaned_text}\n{placeholder}" if cleaned_text else placeholder
                )
            base_msg["content"] = cleaned_text
            stripped.append(base_msg)
            continue

        # content がリストの場合
        if isinstance(content, list):
            new_content = []
            image_count = 0
            for part in content:
                if isinstance(part, dict) and part.get("type") == "image_url":
                    if allow_vision:
                        # Vision有効なら維持
                        new_content.append(part)
                    else:
                        image_count += 1
                    continue

                if isinstance(part, dict) and part.get("type") == "text":
                    text_value = part.get("text")
                    if isinstance(text_value, str):
                        if allow_vision and "data:image" in text_value:
                            new_content.extend(_split_text_and_images(text_value))
                        else:
                            part = dict(part)
                            part["text"] = _strip_image_data_from_text(text_value)
                            new_content.append(part)
                    else:
                        new_content.append(part)
                elif isinstance(part, str):
                    if allow_vision and "data:image" in part:
                        new_content.extend(_split_text_and_images(part))
                    else:
                        new_content.append(_strip_image_data_from_text(part))
                else:
                    new_content.append(part)

            total_images = image_count + extra_image_count
            if total_images > 0 and not allow_vision:
                new_content.append(
                    {"type": "text", "text": f"[画像: {total_images}枚省略]"}
                )

            new_msg = base_msg
            new_msg["content"] = (
                new_content
                if new_content
                else [{"type": "text", "text": "[画像のみのメッセージ]"}]
            )
            stripped.append(new_msg)
            continue

        # Fallback for unexpected shapes
        base_msg["content"] = content
        stripped.append(base_msg)

    return stripped


def _latest_user_turn(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    会話履歴を参照せず、直近のユーザー発話のみを返す。
    期待外の型が混ざっていても安全に空リストを返す。
    """

    if not isinstance(messages, list):
        return []

    for entry in reversed(messages):
        if isinstance(entry, dict) and entry.get("role") == "user":
            return [entry]

    return []


def _content_to_text(content: Any) -> str:
    """Normalise chat completion content into a plain string."""

    if content is None:
        return ""
    if isinstance(content, str):
        return _strip_internal_thoughts(content)

    if hasattr(content, "text") and isinstance(content.text, str):
        return content.text

    # Newer SDKs and some providers return a list of content parts
    if isinstance(content, list):
        parts: List[str] = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
                continue
            
            # Skip image/vision blocks to prevent leaking base64 into text logs/history
            if isinstance(part, dict):
                p_type = part.get("type")
                if p_type in {"image_url", "image", "input_image"}:
                    continue
                
                # Check known text keys
                for key in ("text", "data", "content"):
                    value = part.get(key)
                    if isinstance(value, str):
                        parts.append(value)
                        break
            elif hasattr(part, "text") and isinstance(part.text, str):
                parts.append(part.text)
                
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


def _strip_internal_thoughts(text: Any) -> str:
    """Remove inline <function_calls>...</function_calls> and <think>...</think> markup from a string."""

    if not isinstance(text, str):
        return _content_to_text(text)

    # Remove <function_calls>...</function_calls>
    cleaned_text = re.sub(r"<function_calls>.*?</function_calls>", "", text, flags=re.IGNORECASE | re.DOTALL)
    # Remove <think>...</think>
    cleaned_text = re.sub(r"<think>.*?</think>", "", cleaned_text, flags=re.IGNORECASE | re.DOTALL)

    return cleaned_text.strip()


def _normalise_tool_calls(raw_tool_calls: Any) -> List[Dict[str, Any]]:
    """Convert provider-specific tool call objects into plain dicts for reuse."""

    if not raw_tool_calls:
        return []

    normalised: List[Dict[str, Any]] = []

    for index, call in enumerate(raw_tool_calls):
        call_id = ""
        function_name = ""
        arguments: Any = {}

        if isinstance(call, dict):
            call_id = call.get("id") or call.get("tool_call_id") or call.get("call_id") or ""
            function = call.get("function") or {}
            if isinstance(function, dict):
                function_name = function.get("name") or function.get("tool_name") or ""
                arguments = function.get("arguments", {})
            else:
                function_name = call.get("name") or ""
                arguments = call.get("arguments", {})
        else:
            call_id = getattr(call, "id", None) or getattr(call, "tool_call_id", None) or ""
            function = getattr(call, "function", None)
            if isinstance(function, dict):
                function_name = function.get("name") or function.get("tool_name") or ""
                arguments = function.get("arguments", {})
            else:
                function_name = getattr(function, "name", "") if function else ""
                arguments = getattr(function, "arguments", {}) if function else {}

        if not function_name:
            continue

        if not isinstance(arguments, str):
            try:
                arguments = json.dumps(arguments or {})
            except Exception:
                arguments = "{}"

        normalised.append(
            {
                "id": call_id or f"call_{index + 1}",
                "type": "function",
                "function": {"name": function_name, "arguments": arguments},
            }
        )

    return normalised


def _extract_embedded_tool_calls(text: Any) -> List[Dict[str, Any]]:
    """
    Some providers or prompt styles return tool calls as inline text like:
    <function_calls>[{"tool_name": "...", "parameters": {...}}]</function_calls>
    Extract them so execution can still proceed.
    """

    if not isinstance(text, str):
        return []

    lowered = text.lower()
    start = lowered.find("<function_calls>")
    end = lowered.find("</function_calls>")
    snippet = ""

    if start != -1 and end != -1 and end > start:
        snippet = text[start + len("<function_calls>") : end]
    else:
        snippet = text

    # Try to isolate a JSON array within the snippet
    content = snippet.strip().strip("`").strip()
    if "[" not in content or "]" not in content:
        return []

    try:
        prefix_index = content.index("[")
        suffix_index = content.rindex("]") + 1
        json_fragment = content[prefix_index:suffix_index]
        parsed = json.loads(json_fragment)
    except Exception:
        return []

    if not isinstance(parsed, list):
        return []

    tool_like_calls: List[Dict[str, Any]] = []
    for index, entry in enumerate(parsed):
        if not isinstance(entry, dict):
            continue
        name = (
            entry.get("tool_name")
            or entry.get("name")
            or (entry.get("function") or {}).get("name")
            or ""
        )
        params = (
            entry.get("parameters")
            or entry.get("args")
            or (entry.get("function") or {}).get("arguments")
            or {}
        )
        if not name:
            continue
        try:
            arguments = params if isinstance(params, str) else json.dumps(params or {})
        except Exception:
            arguments = "{}"

        tool_like_calls.append(
            {
                "id": entry.get("id") or entry.get("tool_call_id") or f"embedded_call_{index + 1}",
                "type": "function",
                "function": {"name": name, "arguments": arguments},
            }
        )

    return tool_like_calls


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
                        if isinstance(image_url, dict):
                            url = image_url.get("url")
                        elif isinstance(image_url, str):
                            url = image_url

                        if isinstance(url, str) and url.strip():
                            url = url.strip()

                            # Responses API expects a bare URL string, not an object; data URLs can be huge.
                            if url.startswith("data:"):
                                # 長大な base64 はトークン上限を溢れさせるので、プレースホルダーに置換
                                parts.append({"type": "input_text", "text": "[画像: 省略]"})
                            else:
                                parts.append({"type": "input_image", "image_url": url})
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


def _classify_provider_error(exc: Exception) -> Tuple[bool, bool, str]:
    """
    Return (retryable, drop_tools, message) flags for provider errors.
    retryable: transient issues worth retrying
    drop_tools: hints the provider rejected tool schemas; retry without them
    """
    message = str(exc).strip() if exc else ""
    lowered = message.lower()
    status = getattr(exc, "status", None) or getattr(exc, "status_code", None)
    if status is None:
        response_obj = getattr(exc, "response", None)
        status = getattr(response_obj, "status_code", None)

    retryable = False
    drop_tools = False

    if isinstance(exc, (APITimeoutError, APIConnectionError, RateLimitError)):
        retryable = True
    elif isinstance(exc, APIError) and status:
        try:
            retryable = int(status) >= 500
        except Exception:
            retryable = False

    if isinstance(exc, BadRequestError):
        if "tool" in lowered or "function" in lowered:
            if any(keyword in lowered for keyword in ("unsupported", "not available", "disable", "unavailable")):
                drop_tools = True

    if "tools" in lowered and "support" in lowered and "not" in lowered:
        drop_tools = True

    return retryable, drop_tools, message or "Unknown provider error"


def _provider_error_message(exc: Optional[Exception]) -> str:
    """Return a user-facing fallback message with a short provider error summary."""

    base = "AIプロバイダへのリクエストが連続で失敗しました。時間をおいて再実行してください。"
    if not exc:
        return base

    detail = str(exc).strip().split("\n")[0]
    if len(detail) > 160:
        detail = detail[:160] + "..."
    return f"{base}\n詳細: {detail}"


async def _chat_completion_with_retries_async(
    client: "UnifiedClient",
    *,
    max_attempts: int = 3,
    **kwargs: Any,
) -> Tuple[Optional[Any], Optional[Exception]]:
    """Call chat.completions with bounded retries and optional tool fallback."""

    attempts = max(1, max_attempts)
    delay = 1.0
    last_error: Optional[Exception] = None
    call_kwargs = dict(kwargs)

    for attempt in range(1, attempts + 1):
        try:
            return await client.chat.completions.create(**call_kwargs), None
        except Exception as exc:
            last_error = exc
            retryable, drop_tools, message = _classify_provider_error(exc)
            print(f"[LLM Retry] attempt {attempt}/{attempts} failed: {message}")

            if drop_tools and call_kwargs.get("tools"):
                call_kwargs["tools"] = None
                call_kwargs["tool_choice"] = None
                print("[LLM Retry] Retrying without tool payload due to provider rejection.")
                continue

            if retryable and attempt < attempts:
                await asyncio.sleep(delay)
                delay = min(delay * 2, 8)
                continue
            break

    return None, last_error


def _chat_completion_with_retries_sync(
    client: "UnifiedClient",
    *,
    max_attempts: int = 3,
    **kwargs: Any,
) -> Tuple[Optional[Any], Optional[Exception]]:
    """Synchronous wrapper for retrying chat completions."""

    try:
        return asyncio.run(_chat_completion_with_retries_async(
            client, max_attempts=max_attempts, **kwargs
        ))
    except Exception as exc:
        return None, exc


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
            if AsyncAnthropic is None:
                raise ImportError("Anthropic SDK is not installed. Please run `pip install anthropic`.")
            self.client = AsyncAnthropic(api_key=self.api_key)
        else:
            client_kwargs: Dict[str, Any] = {"api_key": self.api_key}
            if self.base_url:
                client_kwargs["base_url"] = self.base_url
            if self.provider == "gemini":
                # Google の OpenAI 互換APIは API key をヘッダーでも受け付ける
                client_kwargs["default_headers"] = {"x-goog-api-key": self.api_key}
            self.client = AsyncOpenAI(**client_kwargs)

        self.chat = self

    @property
    def completions(self):
        return self

    async def create(self, **kwargs):
        if self.provider == "claude":
            return await self._create_anthropic(**kwargs)
        else:
            return await self.client.chat.completions.create(**kwargs)

    async def _create_anthropic(self, **kwargs):
        model = kwargs.get("model", self.model_name)
        messages = kwargs.get("messages", [])
        tools = kwargs.get("tools")

        system_prompt = ""
        filtered_messages = []

        for msg in messages:
            role = msg.get("role")
            content = msg.get("content")

            if role == "system":
                if isinstance(content, list):
                    text_parts = []
                    for part in content:
                        if isinstance(part, dict) and part.get("type") == "text":
                            text_parts.append(part.get("text", ""))
                        elif isinstance(part, str):
                            text_parts.append(part)
                    system_prompt += "\n".join(text_parts) + "\n"
                else:
                    system_prompt += (content or "") + "\n"
                continue

            if role == "tool":
                # Convert OpenAI 'tool' role to Anthropic 'tool_result' block in 'user' message
                tool_call_id = msg.get("tool_call_id")
                filtered_messages.append({
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": tool_call_id,
                            "content": content or ""
                        }
                    ]
                })
                continue

            if role == "assistant" and msg.get("tool_calls"):
                # Convert OpenAI 'tool_calls' to Anthropic 'tool_use' blocks
                anthropic_content = []
                if content:
                    anthropic_content.append({"type": "text", "text": content})
                
                for tc in msg["tool_calls"]:
                    func = tc.get("function", {})
                    args = func.get("arguments", "{{}}")
                    if isinstance(args, str):
                        try:
                            args = json.loads(args)
                        except json.JSONDecodeError:
                            args = {}
                    
                    anthropic_content.append({
                        "type": "tool_use",
                        "id": tc.get("id"),
                        "name": func.get("name"),
                        "input": args
                    })
                
                filtered_messages.append({"role": "assistant", "content": anthropic_content})
                continue

            # Standard message handling (User / Assistant text)
            if isinstance(content, list):
                new_content = []
                for part in content:
                    if isinstance(part, dict):
                        if part.get("type") == "image_url":
                            image_url = part.get("image_url", {}).get("url", "")
                            if image_url.startswith("data:"):
                                try:
                                    # Split on the first comma to separate header and data
                                    header, data = image_url.split(",", 1)
                                    # header example: data:image/jpeg;base64
                                    media_type = header.split(":")[1].split(";")[0]
                                    
                                    # Anthropic requires standard base64 without newlines
                                    clean_data = data.replace("\n", "").replace("\r", "").strip()
                                    
                                    new_content.append({
                                        "type": "image",
                                        "source": {
                                            "type": "base64",
                                            "media_type": media_type,
                                            "data": clean_data
                                        }
                                    })
                                except (ValueError, IndexError) as e:
                                    print(f"Failed to process image data for Anthropic: {e}")
                            else:
                                # Remote URLs are not supported by Anthropic API directly in this flow usually,
                                # unless the SDK handles it. We'll leave it out to prevent errors.
                                pass
                        else:
                            new_content.append(part)
                    else:
                        new_content.append(part)
                filtered_messages.append({"role": role, "content": new_content})
            else:
                filtered_messages.append(msg)

        max_tokens = kwargs.get("max_tokens", 4096)
        temperature = kwargs.get("temperature", 0.7)

        # Convert OpenAI tools schema to Anthropic tools schema if present
        anthropic_tools = []
        if tools:
            for t in tools:
                if t.get("type") == "function":
                    func = t.get("function", {})
                    anthropic_tools.append({
                        "name": func.get("name"),
                        "description": func.get("description"),
                        "input_schema": func.get("parameters")
                    })

        create_kwargs = {
            "model": model,
            "system": system_prompt.strip(),
            "messages": filtered_messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if anthropic_tools:
            create_kwargs["tools"] = anthropic_tools

        response = await self.client.messages.create(**create_kwargs)

        # Convert Anthropic response back to OpenAI-like format for uniformity
        content_text = ""
        tool_calls = []
        
        for block in response.content:
            if block.type == "text":
                content_text += block.text
            elif block.type == "tool_use":
                tool_calls.append({
                    "id": block.id,
                    "type": "function",
                    "function": {
                        "name": block.name,
                        "arguments": json.dumps(block.input)
                    }
                })

        message = SimpleNamespace(content=content_text, parsed=None, tool_calls=tool_calls if tool_calls else None)
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
    # プロバイダがVision対応なら画像を維持する
    vision_supported = provider_supports_vision(getattr(client, "provider", ""))
    messages = _sanitize_messages(messages, allow_vision=vision_supported)
    
    device_context = _build_device_context()
    system_prompt = f"""{_current_datetime_line()}
あなたは一般家庭のIoTデバイス管理を専門とする、親切で役立つアシスタントです。あなたは目標は、提供されたツールを使ってデバイスを制御し、ユーザーの生活を便利にすることです。応答やアクションを生成する際は、ユーザーのプロファイル、実際に利用可能なコマンド、周囲の環境を慎重に考慮してください。依頼が曖昧な場合は、その場の状況を読み取り（read the room）、ユーザーの意図を推測して最適なアクションを選択してください。**過度な質問を避けること**: ユーザーは細かい設定（音の種類、表示行、色、速度、回数など）を聞かれることを嫌います。これらが指定されていない場合は、質問せずに**最も一般的で標準的な設定**を勝手に選択して実行してください。特に**移動やモーター制御（「前に進んで」「右を向いて」「サーボを動かして」など）**の場合、時間や角度が未指定なら**黙って標準値（duration=5.0, angle=90, または action='sweep'）で実行**してください。「どのくらい？」「どの角度？」と聞くのは厳禁です。「全部動かして」など、対象が複数の場合で、もし技術的に全同時実行が難しくても、ユーザーに聞き返さず、代表的なデバイス（例: サーボ1と2）を勝手に選んで実行してください。
依頼が曖昧な場合でも、実行可能な部分を特定し、可能であれば実行してください。実行不可能なアクションは生成しないでください。自分の出力やアクションの結果を予測し、その予測を応答に反映させてください。

【重要: 確認を求めずに即座に実行すべきケース】:
- **デバイスが1つだけ登録されている場合**: ユーザーの発言が**デバイスの操作や状態確認を意図していると判断できる場合**に限り、その唯一のデバイスを対象として即座に実行してください。ただし、**日常会話、感想、質問、挨拶など、物理的なデバイス操作を伴わない発言に対しては、絶対にコマンドを生成せず、会話のみで返答してください**。
- **コマンドが明確な場合**: 「ブザーを鳴らして」「LEDを点灯して」「モーターを動かして」など、実行すべき機能が明らかな場合は確認せずに実行してください。
- **パラメータが省略されている場合**: デフォルト値を使用し、さらに細かいオプションも勝手に決めて実行してください。例: ブザーなら melody='alert'、LEDなら pattern='demo'、移動なら duration=5.0 を使用。「どのような音にしますか？」や「どのくらい動かしますか？」と聞くのは禁止です。
- **サーボ・モーター操作**: 「サーボを動かして」と言われたら、'servo_1'と'servo_2'（または利用可能なもの）を対象に、angle=90 または action='sweep' で即座に実行してください。「どのサーボですか？」「角度は？」と聞くのは禁止です。
常に自然的で温かみがあり、わかりやすい日本語で応答してください。可能な限り専門用語は避け、親しみやすい会話調のトーンを心がけてください。

【ツール使用に関する重要指示 - よく読んでください】:
あなたはユーザーと物理的なIoTデバイスとの間の架け橋です。あなた自身が物理的なアクションを行うことはできません。「control_device」ツールを介してのみ実行可能です。
1. **ツール呼び出しなしのアクション禁止**: ユーザーが物理的なアクション（例: 「テキストを表示して」、「モーターを動かして」、「写真を撮って」）を要求した場合、必ず「control_device」関数を呼び出してください。
2. **嘘をつかない**: 実際に同じターンでツール呼び出しを生成していない限り、「やりました」や「表示しています」と言わないでください。ツール呼び出しなしでテキスト応答のみを返すことは失敗とみなされます。
3. **黙って失敗しない**: 適切なツールやデバイスが見つからない場合は、それを認めてください。コマンドを実行したふりをしないでください。
4. **厳格なマッピング**: 
   - ユーザー: 「OLEDにgoodと表示して」 -> ツール: control_device(device_id='...', command='display_robot_animation', args={{'text': 'good'}})
   - ユーザー: 「サーボを動かして」 -> ツール: control_device(device_id='...', command='operate_dc_motors', ...)
5. **複数アクションの最適化**: 
   - 同一デバイスに対して複数のアクション（例: 「LEDをつけてブザーを鳴らして」）を行う場合は、control_device を複数回呼ぶのではなく、**必ず 'commands' パラメータ（リスト）を使用して1回の呼び出しにまとめてください**。
   - 同時実行が必要な場合（例: 「ブザーを鳴らしながらモーターを動かして」）は、mode='parallel' を指定してください。特に指定がない場合は mode='sequential'（デフォルト）で順次実行されます。
6. **パラメータの検証**: デバイスリストで指定されている必須フィールド（例: 'text', 'duration'）が 'args' に含まれていることを確認してください。
7. **許可待ち禁止**: パラメータが明確な場合は確認を求めずに即座に「control_device」を呼び出してください。説明だけしてツールを呼ばないことは失敗です。

【OLEDディスプレイに関する具体的指示】:
ロボットデバイスのOLED画面にテキストメッセージを表示できます。これを行うには、以下の特定のコマンド名で「control_device」ツールを使用する必要があります。「Available devices」リストにこれらのコマンドのパラメータが明示的に表示されていなくても、必ず 'args' オブジェクトにそれらを提供してください。パラメータなしと想定しないでください。

- Raspberry Pi 4の場合:
  * コマンド: 'display_robot_animation' のみを使う（'show_text_on_oled' は使わない）。
  * 必須引数: {{'text': 'YOUR_TEXT', 'duration': 5.0}}
  * 推奨: motion は指定がなくても 'default' を設定し、text は20文字以内・改行なしの短い日本語/英数字に要約する。
  * duration をユーザーが指定しない場合でも 5.0 の数値を必ず入れる。
  * **表示位置（行）について質問禁止**: テキストは自動的に中央揃えされます。「どの行にしますか？」という質問は無意味なので絶対にしないでください。
  * 「表示してよいか？」と尋ねずに、単一ターンで control_device を発行し、説明とツール呼び出しを同じ応答に含める。

- Jetsonの場合:
  コマンド: 'show_text_on_oled'
  必須引数: {{'text': 'YOUR_TEXT', 'duration': 5.0}}
  (ユーザーが指定しない場合、デフォルト時間は5.0秒)

ユーザーの要求が曖昧な場合（例: 「画面にこんにちはと表示して」）、文脈から正しいデバイスを推測してください。"""
    
    context_message = f"利用可能なデバイス:\n{device_context}" if device_context else "現在登録されているデバイスはありません。"
    
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
        # 注意: UnifiedClient.create は同期メソッド -> Now Async!
        response, llm_error = await _chat_completion_with_retries_async(
            client,
            model=client.model_name,
            messages=current_messages,
            tools=openai_tools if openai_tools else None,
            tool_choice="auto" if openai_tools else None,
        )
        if response is None:
            final_reply = _provider_error_message(llm_error)
            break
        
        choice = response.choices[0]
        message = choice.message

        # Execution logic needs normalised calls
        raw_tool_calls = getattr(message, "tool_calls", None)
        tool_calls_for_execution = _normalise_tool_calls(raw_tool_calls)
        
        embedded_calls = _extract_embedded_tool_calls(message.content)
        if not tool_calls_for_execution and embedded_calls:
            tool_calls_for_execution = embedded_calls

        cleaned_content = _strip_internal_thoughts(message.content)

        # History: Prefer raw tool calls to preserve provider-specific fields (e.g. Gemini thought_signature)
        assistant_msg = {"role": "assistant", "content": cleaned_content}
        
        if raw_tool_calls:
            # Convert Pydantic/object list to dict list if needed
            history_tool_calls = []
            for tc in raw_tool_calls:
                if hasattr(tc, "model_dump"):
                    history_tool_calls.append(tc.model_dump())
                elif hasattr(tc, "to_dict"):
                    history_tool_calls.append(tc.to_dict())
                elif isinstance(tc, dict):
                    history_tool_calls.append(tc)
                else:
                    # Fallback if we can't serialize easily
                    history_tool_calls = tool_calls_for_execution
                    break
            assistant_msg["tool_calls"] = history_tool_calls
        elif tool_calls_for_execution:
            assistant_msg["tool_calls"] = tool_calls_for_execution
            
        current_messages.append(assistant_msg)

        if tool_calls_for_execution:
            # ツール呼び出しの処理
            for tool_call in tool_calls_for_execution:
                function = tool_call.get("function") or {}
                function_name = function.get("name", "")
                try:
                    arguments_raw = function.get("arguments")
                    arguments = json.loads(arguments_raw) if isinstance(arguments_raw, str) else arguments_raw
                except json.JSONDecodeError:
                    arguments = {}
                except Exception:
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
                        "tool_call_id": tool_call.get("id", ""),
                        "content": tool_result_text.strip() or "Success"
                    })

                except Exception as e:
                    error_msg = f"Tool Execution Error: {str(e)}"
                    print(error_msg)
                    current_messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call.get("id", ""),
                        "content": error_msg
                    })

            turn += 1
        else:
            # ツール呼び出しがない場合、それが最終回答
            final_reply = cleaned_content
            break

    return {
        "reply": final_reply,
        "device_commands": [], # 実行済みのため空リスト
        "images": collected_images
    }


async def _call_llm_and_parse_async(
    client: UnifiedClient, messages: List[Dict[str, str]]
) -> Dict[str, Any]:
    try:
        return await _process_chat_with_tools(client, messages)
    except Exception as e:
        traceback.print_exc()
        return {
            "reply": f"内部エラーが発生しました: {str(e)}",
            "device_commands": [],
        }


def _call_llm_and_parse(client: UnifiedClient, messages: List[Dict[str, str]]) -> Dict[str, Any]:
    # 同期ラッパー: 同期コードから呼ばれる前提で維持
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        try:
            return asyncio.run(_process_chat_with_tools(client, messages))
        except Exception as e:
            traceback.print_exc()
            return {
                "reply": f"内部エラーが発生しました: {str(e)}",
                "device_commands": [],
            }

    result: Dict[str, Any] = {}
    error: Optional[Exception] = None

    def _runner() -> None:
        nonlocal result, error
        try:
            result = asyncio.run(_process_chat_with_tools(client, messages))
        except Exception as exc:
            error = exc

    thread = threading.Thread(target=_runner)
    thread.start()
    thread.join()

    if error is not None:
        traceback.print_exc()
        return {
            "reply": f"内部エラーが発生しました: {str(error)}",
            "device_commands": [],
        }
    return result


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
    messages = _latest_user_turn(_sanitize_messages(messages, allow_vision=False))

    if not messages:
        raise RuntimeError("No user message available for review.")

    device_context = _build_device_context()
    timestamp_line = _current_datetime_line()
    system_prompt = f"""{timestamp_line}
あなたは「IoTデバイス操作専門」の運用アナリストです。

【あなたの専門分野（発言可能な範囲）】
- スマートホームデバイスの操作: 照明、エアコン、スマートプラグ
- センサーデータの確認: 温度、湿度、モーションセンサー
- デバイスの状態監視: 接続状態、バッテリー残量
- 自動化ルールの提案: デバイス連携、スケジュール実行

【重要: 即座に実行すべきケース】
- デバイスが1つだけ登録されている場合: ユーザーの発言が**明確にデバイスの操作や状態確認を意図している場合**に限り、自動的に選択してください。**日常会話や一般的な質問など、デバイスへの介入が不要な場合は、操作不要（action_required: false）としてください**。
- コマンドが明確な場合（「ブザーを鳴らして」「LEDを点灯」など）: 確認なしに device_commands を生成してください。
- パラメータが省略されている場合: 音の種類や表示位置、移動時間などの詳細が不明でも、勝手に標準的なデフォルト値（例: melody='alert'、textは自動中心揃え、移動ならduration=5.0）を補完して device_commands を生成してください。質問は不要です。

【実行順序と同時実行の指定方法】
- 同時に動かしても安全な場合（例: ブザーを鳴らしながらモーターを回す）は、各コマンドに同じ `sequence_group` (1以上の整数) を付けて並列に実行させてください。デバイスが異なっても（Pico W / Jetson / Raspberry Pi 4など）同じ番号なら同時に動かします。
- 明確に順番が必要な場合（例: 先にLED点灯を確認してからモーターを回す）は、`sequence_group` を 1, 2, 3... と段階的に上げてください。同じ番号同士は同時実行、番号が小さいものから順番に処理されます。
- `sequence_group` が指定されない場合は 1 とみなし、同じ番号のコマンドはまとめて即時実行します。不要な待ち時間を避けるため、できるだけ並列実行できるものは同じ番号にまとめてください。

【発言してはいけない場合】
- Web検索・ブラウザ操作の話題 -> Browser Agentの専門
- 料理・洗濯・家庭科の知識 -> Life-Style Agentの専門
- スケジュール・予定管理 -> Scheduler Agentの専門
- IoTデバイスと無関係な一般的な話題

【判断ルール】
1. `action_required: true` は、登録済みデバイスへの具体的な操作が必要な場合のみ
2. `should_reply: true` は、IoTに関する質問・問題解決の場合のみ
3. 他エージェントへの呼びかけは禁止（自分の専門外は無視する）
4. 単なるアドバイスやコメントでは発言しない
5. デバイスが1つしかない場合は、device_commands に自動的にそのデバイスIDを設定する

【発言する例】
- 「照明をつけて」-> action_required: true, device_commands を即生成
- 「ブザーを鳴らして」-> action_required: true, device_commands を即生成（デバイスが1つなら確認不要）
- 「部屋の温度は？」-> should_reply: true（センサー確認）

【発言しない例】
- 「天気を調べて」-> 発言しない
- 「夕食のレシピ」-> 発言しない
- 「明日の予定」-> 発言しない

常に以下のフィールドを含む厳密なJSONオブジェクトで応答してください: 'action_required' (boolean), 'reason' (あなたの判断を説明する文字列), 'device_commands' (null または 'device_id', 'name', 'args' を持つコマンドオブジェクトの配列; 並列・順次を切り替える場合は各コマンドに任意で 'sequence_group' (1以上の整数) を付ける), 'notes' (任意), 'should_reply' (boolean), 'reply' (短く役立つメッセージ), 'addressed_agents' (呼びかけるエージェント名の配列; なければ空). """

    context_message = (
        "利用可能なデバイス情報:\n" + device_context
        if device_context
        else "現在登録されているデバイスはありません。"
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
                    "以下の会話記録を確認してください。 "
                    "IoTによる介入が必要かどうか判断してください。"
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

    call_kwargs = dict(kwargs)
    call_kwargs.update(extra_args)

    response, llm_error = _chat_completion_with_retries_sync(
        client,
        max_attempts=2,
        **call_kwargs,
    )

    if response is None:
        fail_reason = _provider_error_message(llm_error)
        return {
            "action_required": False,
            "reason": fail_reason,
            "device_commands": [],
            "notes": None,
            "should_reply": True,
            "reply": fail_reason,
            "addressed_agents": [],
            "raw": fail_reason,
        }

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
    
    async def _internal_call():
        if provider_supports_vision(provider) and responses_client and _messages_include_images(messages):
            try:
                responses_input = _convert_messages_to_responses_input(messages)
                if responses_input:
                    # Note: If responses API client is also async, await it.
                    # Assuming client.client is async, responses_client should support await if it follows the pattern.
                    # However, typical OpenAI AsyncClient uses 'await client.chat.completions.create'
                    # We assume responses_client.create is awaitable.
                    response = await responses_client.create(model=model, input=responses_input)
                    text = _response_output_to_text(response)
                    if text:
                        return text.strip()
            except Exception as e:  # pragma: no cover - network/SDK errors
                print(f"[{datetime.now()}] Responses API Text Error: {str(e)}")
                traceback.print_exc()

        response, llm_error = await _chat_completion_with_retries_async(
            client,
            model=model,
            messages=messages,
            max_attempts=2,
        )

        if response:
            choice = response.choices[0] if response and response.choices else None
            message = choice.message if choice else None
            content = getattr(message, "content", None) if message else None
            text = _content_to_text(content)
            return text.strip()

        if llm_error:
            print(f"[{datetime.now()}] LLM Text Error: {str(llm_error)}")
            traceback.print_exc()

        return ""

    try:
        return asyncio.run(_internal_call())
    except Exception:
        # If we are already in an event loop, we might need a different approach or just fail/log
        # But this function is typically called from sync contexts.
        traceback.print_exc()
        return ""


def _structured_agent_instruction_prompt(
    messages: List[Dict[str, str]], target_role: Optional[str] = None
) -> Dict[str, Any]:
    # Keep for execution.py backward compat if needed
    messages = _sanitize_messages(messages, allow_vision=False)
    device_context = _build_device_context()
    timestamp_line = _current_datetime_line()
    language = "English"
    if target_role == "jetson-agent":
        # language = "Japanese"
        pass
    system_prompt = f"{timestamp_line}\nあなたは運用アシスタントです。{language}で特定の指示文字列のみを出力してください。"
    context_message = f"利用可能なデバイス:\n{device_context}"
    model_name = apply_model_selection("iot")[1]
    return {
        "model": model_name,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "system", "content": context_message},
            *messages,
        ],
    }
