import json
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
    _structured_agent_instruction_prompt,
)
from .models import DeviceState, _CommandExecutionSummary
from .state import _DEVICES, _JOB_METADATA, _PENDING_JOBS
from .validation import _validate_device_command_sequence


def _format_return_value_for_user(value: Any) -> str:
    # 戻り値を人間が理解しやすい日本語文に再構成する

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
    )


def _execute_device_command_sequence(
    client: OpenAI,
    messages: List[Dict[str, str]],
    initial_reply: str,
    commands: List[Dict[str, Any]],
) -> Tuple[str, int]:
    # 連続コマンドをできるだけ同時に処理し、レスポンス文と言語コードを返す

    if not commands:
        return initial_reply, 200

    summaries: List[Optional[_CommandExecutionSummary]] = [None] * len(commands)
    threads: List[threading.Thread] = []

    def _run_command(index: int, command: Dict[str, Any]) -> None:
        device_id = command.get("device_id")
        device = _DEVICES.get(device_id) if isinstance(device_id, str) else None

        try:
            if device and _device_is_agent(device):
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

    for index, command in enumerate(commands):
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
        return initial_reply, 200

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
        return "\n\n".join(failure_messages), failure_status or 500

    final_reply = _summarize_device_command_sequence(
        client, messages, initial_reply, completed_summaries
    )
    return final_reply, 200


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
    )


def _summarize_device_command_sequence(
    client: Optional[OpenAI],
    base_messages: List[Dict[str, str]],
    initial_reply: str,
    summaries: List[_CommandExecutionSummary],
) -> str:
    # 実行済みコマンドの要約を LLM もしくはフォールバックで生成

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
    # マルチステップの実行結果を踏まえた最終回答生成プロンプトを構築

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
    # エージェントデバイス不在時にレガシーフローでチャットを処理

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
