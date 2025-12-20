import json
import time
import uuid
import asyncio
from typing import Any, Deque, Dict, List, Optional

from .config import AGENT_CAPABILITY_NAME, AGENT_ROLE_VALUE, MAX_COMPLETED_JOBS
from .models import DeviceState
from .state import (
    _COMPLETED_JOBS,
    _COMPLETED_JOB_ORDER,
    _DEVICES,
    _JOB_METADATA,
    _PENDING_JOBS,
)


def _normalise_capability_params(params: Any) -> List[Dict[str, Any]]:
    # capability の params 部分を検証・整形するユーティリティ

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
    # 能力宣言の配列を API 内部向けにクリーニングする

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


def _first_device_id() -> Optional[str]:
    # 最初に登録されたデバイス ID を取得（ダッシュボード表示用）
    return next(iter(_DEVICES), None)


def _device_is_agent(device: DeviceState) -> bool:
    # デバイスがエージェント役割（LLM 制御対象）か判定する

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
    # 登録デバイスの中からエージェント役割を担うものを検索する
    for device in _DEVICES.values():
        if _device_is_agent(device):
            return device
    return None


def _device_supports_capability(device: DeviceState, capability_name: str) -> bool:
    # デバイスが特定の capability 名をサポートするか簡易判定する

    if not isinstance(capability_name, str) or not capability_name.strip():
        return False

    target = capability_name.strip().lower()
    for capability in device.capabilities:
        if not isinstance(capability, dict):
            continue
        raw_name = capability.get("name")
        if not isinstance(raw_name, str):
            continue
        if raw_name.strip().lower() == target:
            return True
    return False


def _action_catalog_for_device(device: DeviceState) -> List[Dict[str, Any]]:
    # デバイスが提供するアクション一覧を取得し、LLM 用に整形
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
    # エージェント向けにデバイスの役割や行動カタログを文章化する

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
    # LLM へのプロンプトに用いる全デバイスの状況サマリーを構築
    if not _DEVICES:
        return "No devices are currently registered."

    lines: List[str] = []
    
    # デバイス数に応じた指示を記載
    if len(_DEVICES) == 1:
        only_device = next(iter(_DEVICES.values()))
        lines.append(f"[IMPORTANT] Only ONE device is registered: {only_device.device_id}")
        lines.append("Always use this device without asking for clarification.")
        lines.append("")
    else:
        lines.append(f"[INFO] {len(_DEVICES)} devices are registered.")
        lines.append("If the user's request clearly maps to a specific device or capability, execute immediately without confirmation.")
        lines.append("Only ask for clarification when there are genuinely ambiguous choices that cannot be inferred from context.")
        lines.append("")
    
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
                + _format_result_for_prompt(summary)
            )
        lines.append("")
    return "\n".join(lines).strip()


def _store_completed_job(job_id: Optional[str], result: Dict[str, Any]) -> None:
    # 完了済みジョブの結果を保持し、必要に応じて古いものを破棄

    if not isinstance(job_id, str) or not job_id:
        return

    _COMPLETED_JOBS[job_id] = dict(result)
    try:
        _COMPLETED_JOB_ORDER.remove(job_id)
    except ValueError:
        pass
    _COMPLETED_JOB_ORDER.append(job_id)

    while len(_COMPLETED_JOB_ORDER) > MAX_COMPLETED_JOBS:
        oldest = _COMPLETED_JOB_ORDER.popleft()
        _COMPLETED_JOBS.pop(oldest, None)
        _JOB_METADATA.pop(oldest, None)


def _enqueue_device_command(
    device_id: str, command: Dict[str, Any], *, source: str = "internal"
) -> Optional[str]:
    # 指定デバイスのジョブキューへコマンドを追加し、job_id を返す
    device = _DEVICES.get(device_id)
    if not device:
        return None

    job_id = uuid.uuid4().hex
    device.job_queue.append({"job_id": job_id, "command": command})
    device.last_seen = time.time()
    _PENDING_JOBS[job_id] = device_id
    _JOB_METADATA[job_id] = {
        "job_id": job_id,
        "device_id": device_id,
        "command": dict(command),
        "queued_at": time.time(),
        "status": "pending",
        "source": source,
    }
    return job_id


def _await_device_result(device_id: str, job_id: str, timeout: float = 300.0) -> Optional[Dict[str, Any]]:
    # デバイスから結果が返るまでポーリングし、タイムアウトしたら None
    deadline = time.time() + timeout
    while time.time() < deadline:
        device = _DEVICES.get(device_id)
        if not device:
            return None
        result = device.job_results.pop(job_id, None)
        if result:
            _PENDING_JOBS.pop(job_id, None)
            metadata = _JOB_METADATA.get(job_id)
            if metadata is not None:
                metadata["status"] = "completed"
                metadata["completed_at"] = time.time()
            _store_completed_job(job_id, result)
            return result
        time.sleep(0.2)
    return None


async def _await_device_result_async(device_id: str, job_id: str, timeout: float = 300.0) -> Optional[Dict[str, Any]]:
    # デバイスから結果が返るまでポーリングし、タイムアウトしたら None (Async版)
    deadline = time.time() + timeout
    while time.time() < deadline:
        device = _DEVICES.get(device_id)
        if not device:
            return None
        result = device.job_results.pop(job_id, None)
        if result:
            _PENDING_JOBS.pop(job_id, None)
            metadata = _JOB_METADATA.get(job_id)
            if metadata is not None:
                metadata["status"] = "completed"
                metadata["completed_at"] = time.time()
            _store_completed_job(job_id, result)
            return result
        await asyncio.sleep(0.2)
    return None


def _serialize_device(device: DeviceState) -> Dict[str, Any]:
    # クライアント向けにデバイス状態を辞書へ変換する

    def _strip_media(value: Any) -> Any:
        if isinstance(value, dict):
            cleaned: Dict[str, Any] = {}
            for key, val in value.items():
                if key in {"image_base64", "image_data", "image_base64_jpeg"} and isinstance(val, str):
                    cleaned[key] = f"[base64 data omitted ({len(val)} chars)]"
                else:
                    cleaned[key] = _strip_media(val)
            return cleaned
        if isinstance(value, list):
            return [_strip_media(item) for item in value]
        return value

    safe_result = _strip_media(device.last_result) if device.last_result else None

    return {
        "device_id": device.device_id,
        "capabilities": device.capabilities,
        "meta": device.meta,
        "action_catalog": _action_catalog_for_device(device),
        "queue_depth": len(device.job_queue),
        "last_seen": device.last_seen,
        "registered_at": device.registered_at,
        "last_result": safe_result,
        "approved": device.approved,
    }


def _device_label_for_prompt(device_id: str) -> str:
    # ユーザーへの説明に使うラベル文字列を生成
    device = _DEVICES.get(device_id)
    if not device:
        return device_id
    display_name = device.meta.get("display_name") if isinstance(device.meta, dict) else None
    if isinstance(display_name, str) and display_name.strip():
        return f"{display_name.strip()} (ID: {device.device_id})"
    return device.device_id


def _format_result_for_prompt(result: Dict[str, Any]) -> str:
    # ジョブ結果を JSON 文字列化し、日本語応答の下準備を行う
    def _strip_media(value: Any) -> Any:
        if isinstance(value, dict):
            cleaned: Dict[str, Any] = {}
            for key, val in value.items():
                if key in {"image_base64", "image_data", "image_base64_jpeg"} and isinstance(val, str):
                    cleaned[key] = f"[base64 data omitted ({len(val)} chars)]"
                else:
                    cleaned[key] = _strip_media(val)
            return cleaned
        if isinstance(value, list):
            return [_strip_media(item) for item in value]
        return value

    safe_result = _strip_media(result)
    return json.dumps(safe_result, ensure_ascii=False, default=str)
