import json
import os
import threading
from typing import Any, Dict, List, Optional, Tuple

from openai import OpenAI

from .config import AGENT_COMMAND_NAME, DEVICE_RESULT_TIMEOUT
from .device_utils import (
    _await_device_result,
    _build_device_context,
    _device_is_agent,
    _device_label_for_prompt,
    _enqueue_device_command,
    _format_result_for_prompt,
)
from .llm import (
    _call_llm_and_parse,
    _call_llm_text,
    _client,
    _sanitize_messages,
    _structured_agent_instruction_prompt,
)
from .models import DeviceState, _CommandExecutionSummary
from .state import _DEVICES, _JOB_METADATA, _PENDING_JOBS
from .validation import _validate_device_command_sequence
from model_selection import apply_model_selection, provider_supports_vision


def _format_return_value_for_user(value: Any) -> str:
    # 戻り値を人間が理解しやすい日本語文に再構成する

    if value is None:
        return "値は返されませんでした。"
    if isinstance(value, (str, int, float, bool)):
        return str(value)
    if isinstance(value, dict):
        sanitized: Dict[str, Any] = dict(value)
        for media_key in ("image_base64", "image_data", "image_base64_jpeg"):
            if media_key in sanitized:
                encoded = sanitized.get(media_key)
                length = len(encoded) if isinstance(encoded, str) else None
                detail = f" ({length} chars)" if length else ""
                sanitized[media_key] = f"[base64 image data omitted{detail}]"
        value = sanitized

        if not value:
            return "詳細データは空でした。"

        action_name = value.get("action") if isinstance(value.get("action"), str) else None
        has_result_field = "result" in value
        if action_name and has_result_field:
            parameters = value.get("parameters")
            message = value.get("message") if isinstance(value.get("message"), str) else None
            result_payload = value.get("result")

            def _format_capture_result(result_data: Any) -> str:
                description_candidates = []
                if isinstance(result_data, dict):
                    for key in ("description", "analysis", "summary"):
                        candidate = result_data.get(key)
                        if isinstance(candidate, str) and candidate.strip():
                            description_candidates.append(candidate.strip())
                    filename = result_data.get("filename")
                    size_bytes = result_data.get("file_size_bytes")
                else:
                    filename = None
                    size_bytes = None

                description_candidates.append(message)
                description = next(
                    (text for text in description_candidates if isinstance(text, str) and text.strip()),
                    "カメラで周囲の様子を撮影しました。"
                )

                details: List[str] = []
                if isinstance(filename, str) and filename.strip():
                    details.append(filename.strip())
                if isinstance(size_bytes, (int, float)) and size_bytes > 0:
                    mb = size_bytes / (1024 * 1024)
                    details.append(f"約{mb:.2f} MB")

                if details:
                    return f"{description}（{', '.join(details)}）"
                return description

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

            if action_name == "capture_camera_photo":
                return _format_capture_result(result_payload)

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
    # API の生結果をユーザーへ伝わりやすい文章に整形
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
    # ジョブがタイムアウトした際にユーザーへ通知するメッセージ生成
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
    # 通常デバイスに対して単発コマンドを送り、結果をまとめる

    device_id = command.get("device_id")
    command_name = (
        str(command.get("name")) if isinstance(command.get("name"), str) else "不明なコマンド"
    )
    args_dict = command.get("args") if isinstance(command.get("args"), dict) else {}

    command_payload = {"name": command_name, "args": args_dict}
    job_id = _enqueue_device_command(device_id, command_payload, source="llm")
    if job_id is None:
        notice = "(注意: デバイスにコマンドを送信できませんでした。)"
        combined = (initial_reply + "\n" if initial_reply else "") + notice
        return _CommandExecutionSummary(
            device_id=device_id,
            command_name=command_name,
            job_id=job_id,
            args=args_dict,
            manual_reply=combined,
            error_text=notice,
            status=404,
        )

    result = _await_device_result(device_id, job_id, timeout=DEVICE_RESULT_TIMEOUT)
    device_label = _device_label_for_prompt(device_id) if device_id else "対象デバイス"

    if result:
        manual_reply = _manual_result_reply(device_label, command_name, result)
        return _CommandExecutionSummary(
            device_id=device_id,
            command_name=command_name,
            job_id=job_id,
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
        job_id=job_id,
        args=args_dict,
        manual_reply=timeout_reply,
        error_text=timeout_reply,
        status=504,
    )


def _execute_device_command_sequence(
    client: OpenAI,
    messages: List[Dict[str, str]],
    initial_reply: str,
    commands: List[Dict[str, Any]],
) -> Tuple[str, int, List[Dict[str, Any]]]:
    # 連続コマンドを sequence_group ごとに並列実行し、グループ間は順次処理する

    if not commands:
        return initial_reply, 200, []

    summaries: List[Optional[_CommandExecutionSummary]] = [None] * len(commands)

    def _sequence_group_for_command(command: Dict[str, Any]) -> int:
        raw_value = command.get("sequence_group")
        if isinstance(raw_value, int) and raw_value > 0:
            return raw_value
        if isinstance(raw_value, str) and raw_value.strip().isdigit():
            candidate = int(raw_value.strip())
            if candidate > 0:
                return candidate
        return 1

    def _run_command(index: int, command: Dict[str, Any]) -> None:
        device_id = command.get("device_id")
        device = _DEVICES.get(device_id) if isinstance(device_id, str) else None

        try:
            if device and _device_is_agent(device):
                # For agent devices, allow direct capability execution (e.g., camera capture)
                # without rephrasing the request into English instructions.
                command_name_raw = command.get("name")
                command_name = command_name_raw.strip() if isinstance(command_name_raw, str) else None
                if command_name and command_name != AGENT_COMMAND_NAME:
                    summary = _execute_standard_device_command(
                        client, messages, initial_reply, command
                    )
                else:
                    summary = _execute_agent_device_command(
                        client, device, messages, initial_reply, command
                    )
            else:
                summary = _execute_standard_device_command(
                    client, messages, initial_reply, command
                )
        except Exception as exc:  # pragma: no cover - defensive guard
            message = str(exc)
            summary = _CommandExecutionSummary(
                device_id=device_id,
                command_name=str(command.get("name") or "不明なコマンド"),
                args=command.get("args") if isinstance(command.get("args"), dict) else {},
                manual_reply=message,
                status=500,
                error_text=message,
            )

        summaries[index] = summary

    grouped_commands: Dict[int, List[Tuple[int, Dict[str, Any]]]] = {}
    for index, command in enumerate(commands):
        group_no = _sequence_group_for_command(command)
        grouped_commands.setdefault(group_no, []).append((index, command))

    for _, grouped in sorted(grouped_commands.items(), key=lambda item: item[0]):
        threads: List[threading.Thread] = []
        for index, command in grouped:
            worker = threading.Thread(target=_run_command, args=(index, command))
            worker.daemon = True
            threads.append(worker)
            worker.start()

        for worker in threads:
            worker.join()

    completed_summaries: List[_CommandExecutionSummary] = [
        summary for summary in summaries if isinstance(summary, _CommandExecutionSummary)
    ]

    if not completed_summaries:
        return initial_reply, 200, []

    # マルチデバイス時に一部が無応答でも成功と見なされないように補正する
    for summary in completed_summaries:
        if summary.result is None and summary.status == 200:
            summary.status = 504
            summary.error_text = summary.error_text or "デバイスから結果を受信できませんでした。"
            summary.manual_reply = summary.manual_reply or summary.error_text

    failure_messages: List[str] = []
    failure_status: Optional[int] = None

    for summary in completed_summaries:
        if summary.status != 200:
            candidate = ""
            if isinstance(summary.manual_reply, str) and summary.manual_reply.strip():
                candidate = summary.manual_reply.strip()
            elif isinstance(summary.error_text, str) and summary.error_text.strip():
                candidate = summary.error_text.strip()

            if candidate:
                failure_messages.append(candidate)
            failure_status = failure_status or summary.status

    if failure_messages:
        # 失敗時も画像が含まれている可能性があるなら抽出してもよいが、
        # 通常はエラーで画像はないため空リストを返す
        return "\n\n".join(failure_messages), failure_status or 500, []

    final_reply, images = _summarize_device_command_sequence(
        client, messages, initial_reply, completed_summaries
    )
    return final_reply, 200, images


def _execute_agent_device_command(
    client: OpenAI,
    agent: DeviceState,
    messages: List[Dict[str, str]],
    initial_reply: str,
    command: Dict[str, Any],
) -> _CommandExecutionSummary:
    # エージェント役デバイスに英語指示を生成して送信し、結果を整理

    args = command.get("args") if isinstance(command, dict) else {}
    args_dict = args if isinstance(args, dict) else {}
    raw_instruction = args_dict.get("instruction")
    english_instruction: Optional[str] = None

    if isinstance(raw_instruction, str) and raw_instruction.strip():
        english_instruction = raw_instruction.strip()
    else:
        target_role = None
        if isinstance(agent.meta, dict):
            target_role = agent.meta.get("role")

        try:
            english_instruction = _call_llm_text(
                client, _structured_agent_instruction_prompt(messages, target_role=target_role)
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
            message = "デバイスへの指示の生成に失敗しました。"
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

    job_id = _enqueue_device_command(agent.device_id, command_payload, source="agent")
    if job_id is None:
        failure_message = "指示を送信できませんでした。デバイスの接続状態を確認してください。"
        combined = (initial_reply + "\n" if initial_reply else "") + failure_message
        return _CommandExecutionSummary(
            device_id=agent.device_id,
            command_name=AGENT_COMMAND_NAME,
            job_id=job_id,
            args=command_args,
            manual_reply=combined,
            instruction=english_instruction,
            is_agent=True,
            error_text=failure_message,
            status=500,
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
            job_id=job_id,
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
        job_id=job_id,
        args=command_args,
        manual_reply=timeout_reply,
        instruction=english_instruction,
        is_agent=True,
        error_text=timeout_reply,
        status=504,
    )


def _summarize_device_command_sequence(
    client: Optional[OpenAI],
    base_messages: List[Dict[str, str]],
    initial_reply: str,
    summaries: List[_CommandExecutionSummary],
) -> Tuple[str, List[Dict[str, Any]]]:
    # 実行済みコマンドの要約を LLM もしくはフォールバックで生成

    fallback_parts = [
        summary.manual_reply.strip()
        for summary in summaries
        if isinstance(summary.manual_reply, str) and summary.manual_reply.strip()
    ]
    fallback_reply = "\n\n".join(fallback_parts) if fallback_parts else initial_reply
    
    image_inputs = _extract_image_inputs(summaries)

    if client is None:
        if not fallback_reply:
            if summaries:
                return "デバイス操作を実行しました（LLMなし）。", image_inputs
            return "特に行うべき操作はありませんでした。", image_inputs
        return fallback_reply, image_inputs

    provider, model_name, _, _ = apply_model_selection("iot")
    vision_model = os.getenv("IOT_VISION_MODEL") or model_name
    vision_supported = provider_supports_vision(provider)
    if vision_supported and image_inputs:
        try:
            prompt_payload = _structured_multi_command_followup_prompt_with_images(
                base_messages,
                initial_reply,
                summaries,
                image_inputs,
                vision_model,
            )
            llm_reply = _call_llm_text(client, prompt_payload)
            cleaned = llm_reply.strip() if isinstance(llm_reply, str) else ""
            if cleaned:
                return cleaned, image_inputs
        except Exception:
            # Fall back to text-only summarisation below
            pass

    try:
        prompt_payload = _structured_multi_command_followup_prompt(
            base_messages, initial_reply, summaries, model_name
        )
        llm_reply = _call_llm_text(client, prompt_payload)
    except Exception:
        return fallback_reply or "処理が完了しましたが、応答メッセージの生成に失敗しました。", image_inputs

    cleaned_reply = llm_reply.strip() if isinstance(llm_reply, str) else ""
    final_reply = cleaned_reply or fallback_reply
    
    if not final_reply:
        if summaries:
            final_reply = "デバイス操作を実行しました。"
        else:
            final_reply = "特に行うべき操作はありませんでした。"

    return final_reply, image_inputs


def _extract_image_inputs(summaries: List[_CommandExecutionSummary]) -> List[Dict[str, Any]]:
    # capture_camera_photo の結果から LLM へ渡す画像データ URL とメタ情報を抽出
    # 複数のデータ構造パターンに対応

    def _has_image(candidate: Any) -> bool:
        return isinstance(candidate, dict) and bool(candidate.get("image_base64"))

    images: List[Dict[str, Any]] = []
    for summary in summaries:
        result_payload = summary.result or {}
        if not isinstance(result_payload, dict):
            continue

        # 複数のパターンに対応（優先度順）
        action_result = None
        return_value = result_payload.get("return_value")

        # パターン1: return_value.result (Raspberry Pi actual structure)
        if isinstance(return_value, dict):
            candidate = return_value.get("result")
            if _has_image(candidate):
                action_result = candidate
            # パターン2: return_value直下にimage_base64がある場合
            elif _has_image(return_value):
                action_result = return_value

        # パターン3: result直下
        if not _has_image(action_result):
            candidate = result_payload.get("result")
            if _has_image(candidate):
                action_result = candidate

        # パターン4: 直接result_payloadに含まれる
        if not _has_image(action_result):
            if _has_image(result_payload):
                action_result = result_payload

        if not isinstance(action_result, dict):
            continue

        base64_value = action_result.get("image_base64") or action_result.get("image_data")
        if not isinstance(base64_value, str) or not base64_value.strip():
            continue

        mime_raw = action_result.get("image_mime_type")
        mime_type = (
            mime_raw.strip() if isinstance(mime_raw, str) and mime_raw.strip() else "image/jpeg"
        )

        label: Optional[str] = None
        for candidate in (action_result.get("filename"), action_result.get("saved_path")):
            if isinstance(candidate, str) and candidate.strip():
                label = candidate.strip()
                break

        images.append(
            {
                "data_url": f"data:{mime_type};base64,{base64_value.strip()}",
                "device_id": summary.device_id,
                "label": label,
                "details": _format_return_value_for_user(action_result),
            }
        )

    return images


def _structured_multi_command_followup_prompt_with_images(
    base_messages: List[Dict[str, str]],
    initial_reply: str,
    summaries: List[_CommandExecutionSummary],
    images: List[Dict[str, Any]],
    model_name: Optional[str] = None,
) -> Dict[str, Any]:
    # 画像付きの最終回答生成プロンプトを構築（視覚モデル向け）
    # 過去の会話履歴から画像データを除去してトークン数を削減
    # 注意: `images` パラメータは現在のリクエストで撮影した最新の画像なので除去しない
    base_messages = _sanitize_messages(base_messages, allow_vision=False)

    device_context = _build_device_context()

    conversation_lines: List[str] = []
    for entry in base_messages:
        role = entry.get("role", "")
        content = entry.get("content")
        if isinstance(content, str):
            conversation_lines.append(f"{role}: {content.strip()}")
    conversation_dump = "\n".join(conversation_lines) or "Conversation log was empty."

    step_descriptions: List[str] = []
    for index, summary in enumerate(summaries, start=1):
        device_label = (
            _device_label_for_prompt(summary.device_id)
            if summary.device_id
            else "対象デバイス"
        )
        args_text = json.dumps(summary.args, ensure_ascii=False, default=str)
        if summary.result is not None:
            result_text = _format_return_value_for_user(summary.result)
        else:
            result_text = "構造化された結果は報告されませんでした。"

        manual = summary.manual_reply.strip() if isinstance(summary.manual_reply, str) else ""
        error_context = summary.error_text.strip() if isinstance(summary.error_text, str) else ""

        lines = [
            f"ステップ {index}:",
            f"  デバイス: {device_label}",
            f"  コマンドまたは指示: {summary.instruction or summary.command_name}",
            f"  引数: {args_text}",
            f"  結果詳細 (内部): {result_text}",
        ]
        if manual:
            lines.append(f"  提案される言い回し (Suggested phrasing): {manual}")
        if error_context:
            lines.append(f"  エラーコンテキスト: {error_context}")

        step_descriptions.append("\n".join(lines))

    step_block = "\n\n".join(step_descriptions)

    user_contents: List[Dict[str, Any]] = [
        {
            "type": "text",
            "text": (
                "**重要**: デバイスがたった今、添付の画像を撮影しました。"
                "画像に何が写っているかを必ず日本語でユーザーに説明してください。"
                "単に「写真が撮られました」と報告するのではなく、中身を伝えてください。"
                "シーン、物体、色、あるいはテキストなど、見えるものを説明してください。"
                "テキストログに「画像省略」とあっても無視してください。ここにあるのが本物の画像データです。"
            ),
        },
        {"type": "text", "text": "会話のコンテキスト:\n" + conversation_dump},
        {"type": "text", "text": "実行サマリ:\n" + step_block},
    ]

    if initial_reply:
        user_contents.append({"type": "text", "text": f"以前の仮の返答: {initial_reply}"})

    for image in images:
        note_lines: List[str] = []
        label = image.get("label")
        if isinstance(label, str) and label.strip():
            note_lines.append(f"画像のラベル: {label.strip()}")
        device_label = image.get("device_id")
        if isinstance(device_label, str) and device_label.strip():
            note_lines.append(f"撮影デバイス: {device_label.strip()}")
        details = image.get("details")
        if isinstance(details, str) and details.strip():
            note_lines.append(f"メタデータ: {details.strip()}")
        if note_lines:
            user_contents.append({"type": "text", "text": "\n".join(note_lines)})

        data_url = image.get("data_url")
        if isinstance(data_url, str) and data_url.strip():
            user_contents.append(
                {
                    "type": "image_url",
                    "image_url": {"url": data_url.strip(), "detail": "high"},
                }
            )

    messages: List[Dict[str, Any]] = [
        {
            "role": "system",
            "content": [
                {
                    "type": "text",
                    "text": (
                        "あなたはIoTデバイスとカメラをサポートする親切なアシスタントです。"
                        "添付された写真に基づいて、温かく簡潔な日本語の返答を提供してください。"
                        "推測を避け、自然な方法で見えるものを説明してください。"
                        "機械的なログ、ジョブID、生のエラーコードなどは避け、ユーザーに話しかけるように会話してください。"
                        "入力に「Suggested phrasing（提案された言い回し）」がある場合、それは生のログとして扱い、親しみやすく分かりやすい説明に言い換えてください。"
                    ),
                }
            ],
        },
    ]
    if device_context:
        messages.append(
            {
                "role": "system",
                "content": [{"type": "text", "text": "利用可能なデバイス情報:\n" + device_context}],
            }
        )

    messages.append({"role": "user", "content": user_contents})

    resolved_model = model_name or apply_model_selection("iot")[1]
    return {"model": resolved_model, "input": messages}


def _structured_multi_command_followup_prompt(
    base_messages: List[Dict[str, str]],
    initial_reply: str,
    summaries: List[_CommandExecutionSummary],
    model_name: Optional[str] = None,
) -> Dict[str, Any]:
    # マルチステップの実行結果を踏まえた最終回答生成プロンプトを構築
    # 過去の会話履歴から画像データを除去してトークン数を削減
    base_messages = _sanitize_messages(base_messages, allow_vision=False)

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
            result_text = _format_return_value_for_user(summary.result)
        else:
            result_text = "構造化された結果は報告されませんでした。"

        manual = summary.manual_reply.strip() if isinstance(summary.manual_reply, str) else ""
        error_context = summary.error_text.strip() if isinstance(summary.error_text, str) else ""

        lines = [
            f"ステップ {index}:",
            f"  デバイス: {device_label}",
            f"  コマンドまたは指示: {summary.instruction or summary.command_name}",
            f"  引数: {args_text}",
            f"  結果詳細 (内部): {result_text}",
        ]
        if manual:
            lines.append(f"  提案される言い回し (Suggested phrasing): {manual}")
        if error_context:
            lines.append(f"  エラーコンテキスト: {error_context}")

        step_descriptions.append("\n".join(lines))

    step_block = "\n\n".join(step_descriptions)

    guidance = (
        "キューに入っていたすべてのデバイスコマンドが完了しました。以下のステップ情報を基に、"
        "親しみやすい日本語で最終的なアシスタントの返答を作成してください。\n"
        "提供されている「Suggested phrasing（提案された言い回し）」は機械的なログです。これをそのまま出力しないでください。"
        "代わりに、友達とチャットしているかのように、結果を自然かつ温かく説明してください。"
        "技術的な詳細は分かりやすい言葉に要約してください。ジョブID、エラーコード（[Errno 121]など）、"
        "または生のJSONは、特に求められない限り表示しないでください。"
        "各ステップにつき簡潔な段落を1つ書き、ステップの順序を維持し、"
        "段落の間には空行を入れてください。\n"
        "各ステップについて、どのデバイスで何を試み、どのような結果になったのかを役立つように明確に言及してください。\n"
        "ユーザーから明示的に要求されない限り、新しいステップを勝手に作ったり、さらなるアクションを要求したりしないでください。"
    )

    if initial_reply:
        guidance += f"\nThe assistant previously told the user: {initial_reply}"

    messages: List[Dict[str, str]] = [
        {"role": "system", "content": "あなたはIoTデバイスをサポートする親切なアシスタントです。"},
    ]
    if device_context:
        messages.append({"role": "system", "content": "利用可能なデバイス情報:\n" + device_context})

    messages.extend(base_messages)

    if initial_reply:
        messages.append({"role": "assistant", "content": initial_reply})

    messages.append({"role": "system", "content": guidance})
    messages.append({"role": "system", "content": "ステップサマリ:\n" + step_block})
    messages.append({"role": "system", "content": "今すぐ最終的な日本語の返答を返してください。"})

    resolved_model = model_name or apply_model_selection("iot")[1]
    return {"model": resolved_model, "input": messages}


def _chat_via_legacy(messages: List[Dict[str, str]]) -> Tuple[Dict[str, Any], int]:
    # エージェントデバイス不在時にレガシーフローでチャットを処理

    client = _client()
    parsed_response = _call_llm_and_parse(client, messages)

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
        final_reply, status, images = _execute_device_command_sequence(
            client, messages, reply_message, validated_commands
        )
        return {"reply": final_reply, "images": images}, status

    return {"reply": final_reply}, 200
