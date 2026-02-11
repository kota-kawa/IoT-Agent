import asyncio
import json
import logging
import os
import queue
import threading
import time
import uuid
from contextlib import asynccontextmanager
from importlib import metadata
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import anyio
import requests
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from mcp.types import JSONRPCMessage
from pydantic import TypeAdapter

from iot_agent.config import (
    DEVICE_RESULT_TIMEOUT,
    IOT_AGENT_API_BASE_URL,
    PROMPT_GUARD_BLOCK_MESSAGE,
)
from iot_agent.device_utils import (
    _agent_device,
    _await_device_result,
    _device_supports_capability,
    _enqueue_device_command,
    _normalise_capabilities,
    _serialize_device,
)
from iot_agent.execution import _chat_via_legacy, _execute_device_command_sequence
from iot_agent.llm import (
    _call_llm_and_parse_async,
    _client,
    _latest_user_turn,
    _prompt_guard_check,
)
from iot_agent.mcp_server import mcp_server
from iot_agent.models import DeviceState
from iot_agent.storage import get_store
from iot_agent.validation import _validate_device_command, _validate_device_command_sequence
from iot_agent.virtual_device import VirtualDeviceRunner
from model_selection import (
    apply_model_selection,
    current_available_models,
    provider_supports_vision,
    update_override,
)

_virtual_device = VirtualDeviceRunner()

@asynccontextmanager
async def lifespan(app: FastAPI):
    _init_storage()
    _virtual_device.start()
    yield
    _virtual_device.stop()
    _shutdown_storage()

def _init_storage() -> None:
    get_store()

def _shutdown_storage() -> None:
    store = get_store()
    store.close()

app = FastAPI(lifespan=lifespan)

_BASE_DIR = Path(__file__).resolve().parent
app.mount("/static", StaticFiles(directory=_BASE_DIR / "static"), name="static")
_FRONTEND_DIST = _BASE_DIR / "frontend" / "dist_v2"
_FRONTEND_INDEX = _FRONTEND_DIST / "index.html"

if (_FRONTEND_DIST / "assets").exists():
    app.mount("/assets", StaticFiles(directory=_FRONTEND_DIST / "assets"), name="spa-assets")

logger = logging.getLogger("iot-agent")

# Default platform base for pushing model changes back to Multi-Agent-Platform
_PLATFORM_BASE = os.getenv("MULTI_AGENT_PLATFORM_BASE", "http://web:5050").rstrip("/")

_ENV_JA_KEYWORDS = [
    "周りの状況",
    "周りの様子",
    "周囲の状況",
    "周囲の様子",
    "周辺の状況",
    "周辺の様子",
    "周りを見",
    "周囲を見",
    "周辺を見",
    "何が見える",
    "カメラ",
    "写真",
    "映像",
]
_ENV_EN_KEYWORDS = [
    "surrounding",
    "around you",
    "see around",
    "look around",
    "what do you see",
    "photo",
    "picture",
    "snapshot",
    "camera",
]


def _iter_platform_bases() -> list[str]:
    """Return potential Multi-Agent Platform bases for sync in priority order."""

    candidates: list[str] = []
    configured = os.getenv("MULTI_AGENT_PLATFORM_BASE", "")
    if configured:
        candidates.extend(part.strip().rstrip("/") for part in configured.split(",") if part.strip())

    if _PLATFORM_BASE:
        candidates.append(_PLATFORM_BASE)

    for fallback in ("http://localhost:5050", "http://web:5050"):
        if fallback not in candidates:
            candidates.append(fallback)

    deduped: list[str] = []
    seen: set[str] = set()
    for base in candidates:
        if not base:
            continue
        if base in seen:
            continue
        seen.add(base)
        deduped.append(base)
    return deduped


def _user_requests_environment_photo(messages: List[Dict[str, str]]) -> bool:
    """Return True when the latest user message asks for a camera view or surroundings."""

    if not isinstance(messages, list):
        return False

    for entry in reversed(messages):
        if entry.get("role") != "user":
            continue
        content = entry.get("content")
        if not isinstance(content, str):
            return False

        lowered = content.lower()
        if any(keyword in content for keyword in _ENV_JA_KEYWORDS):
            return True
        if any(keyword in lowered for keyword in _ENV_EN_KEYWORDS):
            return True
        return False

    return False


def _dependency_report() -> Dict[str, Any]:
    """Return a lightweight snapshot of dependency and environment readiness."""

    packages = {}
    for pkg in ("fastapi", "uvicorn", "openai", "python-dotenv"):
        try:
            packages[pkg] = metadata.version(pkg)
        except metadata.PackageNotFoundError:
            packages[pkg] = "missing"

    env_flags = {
        "OPENAI_API_KEY": bool(os.getenv("OPENAI_API_KEY")),
        "CLAUDE_API_KEY|ANTHROPIC_API_KEY": bool(
            os.getenv("CLAUDE_API_KEY") or os.getenv("ANTHROPIC_API_KEY")
        ),
        "GEMINI_API_KEY|GOOGLE_API_KEY": bool(
            os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or os.getenv("PALM_API_KEY")
        ),
        "GROQ_API_KEY": bool(os.getenv("GROQ_API_KEY")),
        "FLASK_SECRET_KEY": bool(os.getenv("FLASK_SECRET_KEY")),
    }

    provider, model, base_url, _ = apply_model_selection("iot")

    return {
        "packages": packages,
        "environment": env_flags,
        "model": {"provider": provider, "model": model, "base_url": base_url},
    }


def _llm_unavailable_response(exc: Exception) -> Tuple[Dict[str, Any], int]:
    """Build a friendly fallback when the LLM/Responses API cannot be reached."""

    logger.exception("LLM chat flow failed", exc_info=exc)
    message = (
        "LLM への接続に失敗しました。API キーとネットワークを確認したうえで、"
        "しばらくしてから再度お試しください。"
    )
    return {"reply": message, "error": str(exc)}, 200


def _json_response(payload: Dict[str, Any], status_code: int = 200) -> JSONResponse:
    return JSONResponse(content=payload, status_code=status_code)


def _spa_available() -> bool:
    return _FRONTEND_INDEX.exists()


def _spa_response() -> FileResponse:
    return FileResponse(_FRONTEND_INDEX)


def _spa_missing_response() -> Response:
    message = "SPA build not found. Run `cd frontend && npm install && npm run build`."
    return Response(content=message, status_code=503)


@app.get("/")
async def index(request: Request):
    if _spa_available():
        return _spa_response()
    return _spa_missing_response()


@app.get("/login")
async def login_get(request: Request):
    if _spa_available():
        return _spa_response()
    return _spa_missing_response()


@app.get("/config.js")
async def frontend_config():
    payload = {"apiBase": IOT_AGENT_API_BASE_URL} if IOT_AGENT_API_BASE_URL else {"apiBase": ""}
    content = f"window.__APP_CONFIG__ = {json.dumps(payload)};"
    return Response(content=content, media_type="application/javascript")


@app.post("/login")
async def login_post(request: Request):
    return RedirectResponse(url="/", status_code=302)


@app.post("/logout")
async def logout(request: Request):
    return RedirectResponse(url="/", status_code=302)


@app.get("/app.js")
async def app_js():
    raise HTTPException(status_code=404, detail="Not Found")


@app.get("/styles.css")
async def styles_css():
    raise HTTPException(status_code=404, detail="Not Found")


@app.get("/index.html")
async def index_html():
    if _spa_available():
        return _spa_response()
    return _spa_missing_response()


@app.get("/agent-result")
async def agent_result(request: Request):
    if _spa_available():
        return _spa_response()
    return _spa_missing_response()


@app.get("/agent_result.html")
async def agent_result_html():
    if _spa_available():
        return _spa_response()
    return _spa_missing_response()


@app.get("/login.html")
async def login_html():
    if _spa_available():
        return _spa_response()
    return _spa_missing_response()




@app.get("/api/session")
async def session_status(request: Request):
    return _json_response({"authenticated": True})


@app.post("/api/session")
async def session_login(request: Request):
    return _json_response({"authenticated": True})


@app.delete("/api/session")
async def session_logout(request: Request):
    return _json_response({"authenticated": True})


def _notify_platform(selection: dict) -> None:
    """Best-effort push of the IoT model selection back to the platform."""

    if not isinstance(selection, dict):
        return

    payload = {"selection": {"iot": selection}}
    headers = {"X-Agent-Origin": "iot"}
    errors: list[str] = []

    for base in _iter_platform_bases():
        url = f"{base}/api/model_settings"
        try:
            res = requests.post(url, json=payload, headers=headers, timeout=2.0)
        except requests.exceptions.RequestException as exc:  # pragma: no cover - network failure
            errors.append(f"{url}: {exc}")
            continue
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{url}: {exc}")
            continue

        if res.ok:
            return
        errors.append(f"{url}: {res.status_code} {res.text}")

    if errors:
        logger.info("Platform model sync skipped (%s)", "; ".join(errors))


@app.post("/model_settings")
async def update_model_settings(request: Request):
    """Update LLM model selection without restarting the service."""
    # This endpoint allows the frontend to switch models dynamically.

    try:
        payload = await request.json()
    except Exception:
        payload = {}

    payload = payload if isinstance(payload, dict) else {}
    raw_selection = payload.get("selection") if "selection" in payload else payload
    selection = raw_selection.get("iot") if isinstance(raw_selection, dict) and "iot" in raw_selection else raw_selection
    selection = selection if isinstance(selection, dict) else {}
    try:
        provider, model, base_url, _ = update_override(selection if selection else None)
        applied_selection = {"provider": provider, "model": model, "base_url": base_url or ""}
        if request.headers.get("X-Platform-Propagation") != "1" and selection:
            # Run notification in background to avoid blocking response
            asyncio.create_task(asyncio.to_thread(_notify_platform, applied_selection))
    except Exception as exc:  # noqa: BLE001
        return _json_response({"error": f"モデル設定の更新に失敗しました: {exc}"}, status_code=500)
    return _json_response({"status": "ok", "applied": applied_selection if selection else "from_file"})


@app.get("/api/models")
async def list_models():
    """Expose available model choices and the active selection to the UI."""

    provider, model, base_url, _ = apply_model_selection("iot")
    return _json_response(
        {
            "models": current_available_models(),
            "current": {"provider": provider, "model": model, "base_url": base_url},
        }
    )


@app.get("/api/devices/ping")
async def device_ping():
    # エッジデバイスからの疎通確認に応答するシンプルなエンドポイント

    return _json_response({"message": "ok"})


@app.post("/api/chat")
async def chat(request: Request):
    # チャット API のメインエントリーポイントで、LLM 連携とデバイス制御を仲介

    try:
        payload = await request.json()
    except Exception:
        payload = {}

    payload = payload if isinstance(payload, dict) else {}
    messages = payload.get("messages", [])

    if not isinstance(messages, list):
        return _json_response({"error": "messages must be a list"}, status_code=400)

    formatted_messages = []
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")
        content = msg.get("content")
        if role not in {"system", "user", "assistant"}:
            continue
        if not isinstance(content, (str, list)):
            continue
        formatted_messages.append({"role": role, "content": content})

    # 会話履歴を一切参照せず、直近のユーザー発話のみを送信する
    formatted_messages = _latest_user_turn(formatted_messages)

    if not formatted_messages:
        return _json_response({"error": "no user message found"}, status_code=400)

    if not formatted_messages or formatted_messages[-1]["role"] != "user":
        return _json_response({"error": "last message must be from user"}, status_code=400)

    guard_decision = await _prompt_guard_check(formatted_messages)
    if guard_decision and guard_decision.get("blocked"):
        logger.warning("Prompt guard blocked request: %s", guard_decision)
        return _json_response({"reply": PROMPT_GUARD_BLOCK_MESSAGE}, status_code=200)

    agent_device = _agent_device()
    provider, _, _, _ = apply_model_selection("iot")
    vision_supported = provider_supports_vision(provider)
    wants_environment_view = _user_requests_environment_photo(formatted_messages)
    capture_supported = bool(
        agent_device and _device_supports_capability(agent_device, "capture_camera_photo")
    )

    response_payload: Dict[str, Any]
    status: int

    try:
        if agent_device:
            client = _client()
            parsed_response = await _call_llm_and_parse_async(client, formatted_messages)

            reply_message = parsed_response.get("reply")
            if not isinstance(reply_message, str):
                reply_message = parsed_response.get("raw", "").strip()

            validated_commands, validation_errors = _validate_device_command_sequence(
                parsed_response.get("device_commands")
            )

            limitation_notice: Optional[str] = None
            if wants_environment_view:
                if capture_supported:
                    # カメラが使えるなら、モデルがVision非対応でも撮影コマンドを追加する（画像表示のため）
                    if not any(str(cmd.get("name")) == "capture_camera_photo" for cmd in validated_commands):
                        validated_commands = [
                            {
                                "device_id": agent_device.device_id,
                                "name": "capture_camera_photo",
                                "args": {},
                            },
                            *validated_commands,
                        ]

                    if not vision_supported:
                        limitation_notice = (
                            "（※選択中のモデルは画像分析非対応のため、撮影画像の表示のみ行います）"
                        )
                elif not vision_supported:
                    pass

            if limitation_notice:
                reply_message = (reply_message + "\n" if reply_message else "") + limitation_notice

            response_payload = {"reply": reply_message}

            # If images were captured during tool execution (MCP flow), include them
            captured_images = parsed_response.get("images")
            if captured_images:
                response_payload["images"] = captured_images

            status = 200

            if validation_errors:
                notice = "\n".join(f"(システム通知: {error})" for error in validation_errors)
                response_payload["reply"] = (reply_message + "\n" if reply_message else "") + notice
            elif validated_commands:
                final_reply, status, images = await asyncio.to_thread(
                    _execute_device_command_sequence,
                    client, formatted_messages, reply_message, validated_commands
                )
                response_payload = {"reply": final_reply, "images": images}
        else:
            response_payload, status = await _chat_via_legacy(formatted_messages)
    except Exception as exc:
        response_payload, status = _llm_unavailable_response(exc)

    # Multi-Agent-Platform (requests) からのアクセスの場合は画像を削除する
    if "python-requests" in request.headers.get("User-Agent", ""):
        response_payload.pop("images", None)

    return _json_response(response_payload, status_code=status)


@app.post("/api/devices/register")
async def register_device(request: Request):
    # 新しいデバイスを手動登録し、メタ情報を保存

    try:
        payload = await request.json()
    except Exception:
        payload = {}

    payload = payload if isinstance(payload, dict) else {}
    device_id = payload.get("device_id")
    capabilities = payload.get("capabilities")
    meta = payload.get("meta") or {}

    if not isinstance(device_id, str) or not device_id.strip():
        return _json_response({"error": "device_id is required"}, status_code=400)
    if not isinstance(capabilities, list):
        return _json_response({"error": "capabilities must be a list"}, status_code=400)
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

    store = get_store()
    existing = store.get_device(cleaned_id)

    if existing:
        if not existing.approved and not manual_registration:
            return _json_response(
                {
                    "error": "device not approved",
                    "message": "Device must be registered from the dashboard before connecting.",
                },
                status_code=403,
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
            return _json_response(
                {
                    "error": "device not approved",
                    "message": "Device must be registered from the dashboard before connecting.",
                },
                status_code=403,
            )

        device_state = DeviceState(
            device_id=cleaned_id,
            capabilities=capabilities,
            meta=metadata,
            last_seen=now,
            approved=True,
        )
        device_state.registered_at = now
        status = "registered"

    store.save_device(device_state)

    return _json_response(
        {
            "status": status,
            "device_id": device_state.device_id,
            "device": _serialize_device(device_state),
        }
    )


@app.get("/api/devices/{device_id}")
async def get_device(device_id: str):
    # 指定デバイスの詳細情報を取得する API

    cleaned_id = (device_id or "").strip()
    if not cleaned_id:
        return _json_response({"error": "device_id is required"}, status_code=400)

    device = get_store().get_device(cleaned_id)
    if not device:
        return _json_response({"error": "device not registered"}, status_code=404)

    return _json_response({"device": _serialize_device(device)})


@app.put("/api/devices/{device_id}")
async def update_device(device_id: str, request: Request):
    # デバイスのメタ情報や機能を更新する API

    cleaned_id = (device_id or "").strip()
    if not cleaned_id:
        return _json_response({"error": "device_id is required"}, status_code=400)

    store = get_store()
    device = store.get_device(cleaned_id)
    if not device:
        return _json_response({"error": "device not registered"}, status_code=404)

    try:
        payload = await request.json()
    except Exception:
        payload = {}

    payload = payload if isinstance(payload, dict) else {}

    if "capabilities" in payload:
        capabilities = payload.get("capabilities")
        if capabilities is None:
            device.capabilities = []
        elif isinstance(capabilities, list):
            device.capabilities = _normalise_capabilities(capabilities)
        else:
            return _json_response({"error": "capabilities must be a list or null"}, status_code=400)

    if "meta" in payload:
        meta = payload.get("meta")
        if meta is None:
            device.meta = {}
        elif isinstance(meta, dict):
            if not isinstance(device.meta, dict):
                device.meta = {}
            for key, value in meta.items():
                if value is None:
                    device.meta.pop(key, None)
                else:
                    device.meta[key] = value

            display_name = device.meta.get("display_name")
            if isinstance(display_name, str):
                trimmed = display_name.strip()
                if trimmed:
                    device.meta["display_name"] = trimmed
                else:
                    device.meta.pop("display_name", None)
        else:
            return _json_response({"error": "meta must be an object or null"}, status_code=400)

    if "approved" in payload:
        device.approved = bool(payload.get("approved"))

    device.last_seen = time.time()
    store.save_device(device)
    return _json_response({"status": "updated", "device": _serialize_device(device)})


@app.get("/api/devices/{device_id}/jobs")
async def list_device_jobs(device_id: str):
    # デバイスごとのジョブ履歴を取得

    cleaned_id = (device_id or "").strip()
    if not cleaned_id:
        return _json_response({"error": "device_id is required"}, status_code=400)

    store = get_store()
    device = store.get_device(cleaned_id)
    if not device:
        return _json_response({"error": "device not registered"}, status_code=404)

    jobs = store.list_device_jobs(cleaned_id)
    return _json_response({"device_id": cleaned_id, "jobs": jobs})


@app.post("/api/devices/{device_id}/jobs")
async def create_device_job(device_id: str, request: Request):
    # 外部サービスから直接ジョブを投入する API

    cleaned_id = (device_id or "").strip()
    if not cleaned_id:
        return _json_response({"error": "device_id is required"}, status_code=400)

    store = get_store()
    if not store.get_device(cleaned_id):
        return _json_response({"error": "device not registered"}, status_code=404)

    try:
        payload = await request.json()
    except Exception:
        payload = {}

    payload = payload if isinstance(payload, dict) else {}

    command_payload: Dict[str, Any]
    raw_command = payload.get("command")
    if isinstance(raw_command, dict):
        command_payload = dict(raw_command)
    else:
        command_payload = {
            "device_id": cleaned_id,
            "name": payload.get("name"),
            "args": payload.get("args"),
        }

    command_payload.setdefault("device_id", cleaned_id)

    validated_command, error_message = _validate_device_command(command_payload)
    if not validated_command:
        return _json_response({"error": error_message or "invalid command"}, status_code=400)

    queue_command = {
        "name": validated_command["name"],
        "args": validated_command.get("args", {}),
    }

    job_id = _enqueue_device_command(cleaned_id, queue_command, source="api")
    if job_id is None:
        return _json_response({"error": "device not registered"}, status_code=404)

    wait_for_result = bool(payload.get("wait_for_result"))
    timeout_value = payload.get("timeout")
    try:
        timeout_seconds = float(timeout_value)
        if timeout_seconds <= 0:
            raise ValueError
    except (TypeError, ValueError):
        timeout_seconds = DEVICE_RESULT_TIMEOUT

    metadata_fields: Dict[str, Any] = {"wait_for_result": wait_for_result}
    requested_via = payload.get("requested_via")
    if isinstance(requested_via, str) and requested_via.strip():
        metadata_fields["requested_via"] = requested_via.strip()
    else:
        metadata_fields["requested_via"] = "api"
    store.update_job_metadata(job_id, metadata_fields)

    response_payload: Dict[str, Any] = {
        "status": "queued",
        "job_id": job_id,
        "device_id": cleaned_id,
        "command": queue_command,
        "wait_for_result": wait_for_result,
    }

    job_info = store.get_job(job_id)
    if job_info is not None:
        response_payload["queued_at"] = job_info.get("queued_at")

    if wait_for_result:
        result = _await_device_result(cleaned_id, job_id, timeout=timeout_seconds)
        if result is not None:
            response_payload.update({"status": "completed", "result": result})
            status_code = 200
        else:
            response_payload.update(
                {
                    "status": "timeout",
                    "message": f"Result not available within {int(timeout_seconds)} seconds.",
                }
            )
            status_code = 202
    else:
        status_code = 202

    return _json_response(response_payload, status_code=status_code)


@app.get("/api/devices")
async def list_devices():
    # 登録済みデバイス一覧を JSON 形式で返却

    devices = [_serialize_device(device) for device in get_store().list_devices()]
    devices.sort(key=lambda d: d["device_id"])
    return _json_response({"devices": devices})


@app.patch("/api/devices/{device_id}/name")
async def update_device_name(device_id: str, request: Request):
    # デバイスの表示名を更新する PATCH エンドポイント

    cleaned_id = (device_id or "").strip()
    if not cleaned_id:
        return _json_response({"error": "device_id is required"}, status_code=400)

    store = get_store()
    device = store.get_device(cleaned_id)
    if not device:
        return _json_response({"error": "device not registered"}, status_code=404)

    try:
        payload = await request.json()
    except Exception:
        payload = {}

    payload = payload if isinstance(payload, dict) else {}
    display_name = payload.get("display_name") if payload else None

    if not isinstance(device.meta, dict):
        device.meta = {}

    if display_name is None:
        new_name = ""
    elif isinstance(display_name, str):
        new_name = display_name.strip()
    else:
        return _json_response({"error": "display_name must be a string or null"}, status_code=400)

    if new_name:
        device.meta["display_name"] = new_name
    else:
        device.meta.pop("display_name", None)

    device.last_seen = time.time()
    store.save_device(device)
    return _json_response({"status": "updated", "device": _serialize_device(device)})


@app.delete("/api/devices/{device_id}")
async def delete_device(device_id: str):
    # デバイスを削除し、関連ジョブもクリア

    cleaned_id = (device_id or "").strip()
    if not cleaned_id:
        return _json_response({"error": "device_id is required"}, status_code=400)

    store = get_store()
    device = store.get_device(cleaned_id)
    if not device:
        return _json_response({"error": "device not registered"}, status_code=404)

    store.clear_device_jobs(cleaned_id)
    store.delete_device(cleaned_id)

    return _json_response({"status": "deleted", "device_id": cleaned_id})


@app.delete("/api/devices/{device_id}/jobs")
async def clear_device_jobs(device_id: str):
    # 指定デバイスの待機ジョブをすべてクリア

    cleaned_id = (device_id or "").strip()
    if not cleaned_id:
        return _json_response({"error": "device_id is required"}, status_code=400)

    store = get_store()
    device = store.get_device(cleaned_id)
    if not device:
        return _json_response({"error": "device not registered"}, status_code=404)

    count = store.clear_device_jobs(cleaned_id)
    store.touch_device(cleaned_id, time.time())
    return _json_response({"status": "cleared", "device_id": cleaned_id, "count": count})


@app.get("/api/devices/{device_id}/jobs/next")
async def next_job(device_id: str):
    # エッジデバイスが次に取得するジョブをポーリング

    cleaned_id = (device_id or "").strip()
    if not cleaned_id:
        return _json_response({"error": "device_id is required"}, status_code=400)

    store = get_store()
    device = store.get_device(cleaned_id)
    if not device:
        return _json_response({"error": "device not registered"}, status_code=404)

    store.touch_device(cleaned_id, time.time())
    job = store.pop_next_job(cleaned_id)
    if not job:
        return Response(content="", status_code=204)
    return _json_response(job)


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str):
    # ジョブ ID に紐づく状態と結果を返す

    cleaned_id = (job_id or "").strip()
    if not cleaned_id:
        return _json_response({"error": "job_id is required"}, status_code=400)

    job_info = get_store().get_job(cleaned_id)
    if not job_info:
        return _json_response({"error": "job not found"}, status_code=404)

    return _json_response(job_info)


@app.delete("/api/jobs/{job_id}")
async def cancel_job(job_id: str):
    # キューに残っているジョブをキャンセル

    cleaned_id = (job_id or "").strip()
    if not cleaned_id:
        return _json_response({"error": "job_id is required"}, status_code=400)

    status, device_id = get_store().cancel_job(cleaned_id)
    if status == "completed":
        return _json_response({"error": "job already completed"}, status_code=409)
    if status == "dispatched":
        return _json_response({"error": "job already dispatched"}, status_code=409)
    if status == "not_found":
        return _json_response({"error": "job not found or already dispatched"}, status_code=404)

    response_payload = {"status": "cancelled", "job_id": cleaned_id, "device_id": device_id}
    return _json_response(response_payload)


@app.post("/api/devices/{device_id}/jobs/result")
async def post_result(device_id: str, request: Request):
    # エッジデバイスからのジョブ結果を受け取り記録

    try:
        payload = await request.json()
    except Exception:
        payload = None

    if payload is None:
        body_bytes = await request.body()
        raw_body = body_bytes.decode("utf-8") if body_bytes else ""
        try:
            payload = json.loads(raw_body) if raw_body else {}
        except json.JSONDecodeError:
            payload = {}

    job_id = payload.get("job_id") if isinstance(payload, dict) else None
    raw_device_id = payload.get("device_id") if isinstance(payload, dict) else None

    query_device_id = request.query_params.get("device_id", "")
    query_job_id = request.query_params.get("job_id", "")
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
        return _json_response({"error": "conflicting device_id values"}, status_code=400)

    if not isinstance(job_id, str) or not job_id.strip():
        cleaned_job_id = _normalise_candidate(query_job_id)
        if cleaned_job_id:
            job_id = cleaned_job_id
        else:
            job_id = None
    else:
        job_id = job_id.strip()

    store = get_store()
    mapped_device_id: Optional[str] = None
    if job_id:
        mapped = store.pending_job_device(job_id)
        if isinstance(mapped, str) and mapped.strip():
            mapped_device_id = mapped.strip()
        else:
            job_info = store.get_job(job_id)
            candidate = job_info.get("device_id") if isinstance(job_info, dict) else None
            if isinstance(candidate, str) and candidate.strip():
                mapped_device_id = candidate.strip()

    resolved_device: Optional[DeviceState] = None
    mismatch_resolved_via_job = False

    if mapped_device_id:
        resolved_device = store.get_device(mapped_device_id)
        if provided_ids and mapped_device_id not in provided_ids:
            mismatch_resolved_via_job = True

    if not resolved_device:
        for candidate in provided_ids:
            device_candidate = store.get_device(candidate)
            if device_candidate:
                resolved_device = device_candidate
                break

    if not resolved_device and store.device_count() == 1:
        # Some edge device firmwares omit the device_id on the result endpoint.
        # When there is only a single registered device we can safely assume it
        # is the source of the result so that measurements are not dropped.
        resolved_device = next(iter(store.list_devices()), None)

    if not resolved_device:
        if provided_ids or job_id:
            return _json_response({"error": "device not registered"}, status_code=404)
        return _json_response({"error": "device_id is required"}, status_code=400)

    if (
        mapped_device_id
        and resolved_device.device_id != mapped_device_id
    ):
        resolved_device = store.get_device(mapped_device_id)
        mismatch_resolved_via_job = True

    device = resolved_device

    if not isinstance(job_id, str) or not job_id:
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
    store.record_job_result(device.device_id, job_id, result_record, payload.get("command"))

    response_payload = {"status": "ack"}
    if mismatch_resolved_via_job:
        response_payload["warning"] = "device_id mismatch resolved via job_id"

    return _json_response(response_payload)


# --- MCP Server Bridge ---
_MCP_SESSIONS: Dict[str, queue.Queue] = {}


@app.get("/mcp/sse")
async def mcp_sse_endpoint():
    session_id = str(uuid.uuid4())
    input_queue: queue.Queue = queue.Queue()
    output_queue: queue.Queue = queue.Queue()
    _MCP_SESSIONS[session_id] = input_queue

    def run_server_loop():
        async def run():
            read_stream_send, read_stream_recv = anyio.create_memory_object_stream(10)
            write_stream_send, write_stream_recv = anyio.create_memory_object_stream(10)

            async def feed_input():
                while True:
                    try:
                        # Blocking get from queue in executor
                        msg = await asyncio.to_thread(input_queue.get)
                        if msg is None:
                            break
                        try:
                            parsed = TypeAdapter(JSONRPCMessage).validate_python(msg)
                            await read_stream_send.send(parsed)
                        except Exception as exc:
                            print(f"MCP Parse Error: {exc}")
                    except Exception:
                        break
                await read_stream_send.aclose()

            async def consume_output():
                async with write_stream_recv:
                    async for msg in write_stream_recv:
                        if hasattr(msg, "model_dump_json"):
                            data = msg.model_dump_json()
                        else:
                            data = json.dumps(msg)
                        output_queue.put(f"event: message\ndata: {data}\n\n")
                output_queue.put(None)

            async with anyio.create_task_group() as tg:
                tg.start_soon(feed_input)
                tg.start_soon(consume_output)
                output_queue.put(f"event: endpoint\ndata: /mcp/messages?session_id={session_id}\n\n")

                try:
                    await mcp_server.run(
                        read_stream_recv,
                        write_stream_send,
                        initialization_options=mcp_server.create_initialization_options(),
                    )
                except Exception as exc:
                    print(f"MCP Run Error: {exc}")

        try:
            asyncio.run(run())
        finally:
            if session_id in _MCP_SESSIONS:
                del _MCP_SESSIONS[session_id]

    t = threading.Thread(target=run_server_loop, daemon=True)
    t.start()

    def generate():
        while True:
            try:
                msg = output_queue.get(timeout=25)  # Keepalive timeout
                if msg is None:
                    break
                yield msg
            except queue.Empty:
                yield ": keepalive\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


@app.post("/mcp/messages")
async def mcp_messages_endpoint(request: Request):
    session_id = request.query_params.get("session_id")
    if not session_id or session_id not in _MCP_SESSIONS:
        return _json_response({"error": "Session not found"}, status_code=404)

    try:
        payload = await request.json()
    except Exception:
        payload = None

    _MCP_SESSIONS[session_id].put(payload)
    return _json_response({"status": "accepted"}, status_code=202)


@app.get("/api/dependencies")
async def dependencies_status():
    """Return dependency and environment readiness without exposing secrets."""

    return _json_response(_dependency_report())


@app.get("/{full_path:path}")
async def spa_fallback(full_path: str):
    if not _spa_available():
        raise HTTPException(status_code=404, detail="Not Found")
    if full_path.startswith(("api", "static", "assets")):
        raise HTTPException(status_code=404, detail="Not Found")
    return _spa_response()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5006)
