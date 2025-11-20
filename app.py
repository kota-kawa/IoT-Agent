import json
import time
from typing import Any, Dict, List

from flask import Flask, jsonify, redirect, request, session, url_for

from iot_agent.config import APP_PASSWORD, DEVICE_RESULT_TIMEOUT, SECRET_KEY
from iot_agent.device_utils import (
    _agent_device,
    _await_device_result,
    _enqueue_device_command,
    _normalise_capabilities,
    _serialize_device,
    _store_completed_job,
)
from iot_agent.execution import _chat_via_legacy, _execute_device_command_sequence
from iot_agent.llm import (
    _call_llm_and_parse,
    _call_llm_for_conversation_review,
    _client,
    _normalise_conversation_messages,
)
from iot_agent.models import DeviceState
from iot_agent.state import _COMPLETED_JOBS, _DEVICES, _JOB_METADATA, _PENDING_JOBS
from iot_agent.validation import _validate_device_command, _validate_device_command_sequence

app = Flask(__name__, static_folder=".", static_url_path="")
app.secret_key = SECRET_KEY


@app.get("/")
def index():
    # 認証済みでなければログインページへリダイレクト

    if not session.get("authenticated"):
        return redirect(url_for("login"))
    return app.send_static_file("index.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    # シンプルなパスワード認証を行い、成功時にセッションを確立

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
    # セッション情報を破棄してログイン画面へ戻す

    session.clear()
    return redirect(url_for("login"))


@app.get("/api/session")
def session_status():
    # 現在のセッションが認証済みかどうかを返す API

    return jsonify({"authenticated": bool(session.get("authenticated"))})


@app.post("/api/session")
def session_login():
    # JSON 経由でのログイン要求を処理し、成功時にセッションを確立

    payload = request.get_json(silent=True) or {}
    password = payload.get("password") if isinstance(payload, dict) else None

    if isinstance(password, str) and password == APP_PASSWORD:
        session["authenticated"] = True
        return jsonify({"authenticated": True})

    session.pop("authenticated", None)
    return jsonify({"authenticated": False, "error": "invalid credentials"}), 401


@app.delete("/api/session")
def session_logout():
    # API 経由でのログアウト要求。セッションを破棄して応答

    session.clear()
    return jsonify({"authenticated": False})


@app.get("/api/devices/ping")
def device_ping():
    # エッジデバイスからの疎通確認に応答するシンプルなエンドポイント

    return jsonify({"message": "ok"})


@app.post("/api/chat")
def chat():
    # チャット API のメインエントリーポイントで、LLM 連携とデバイス制御を仲介

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


@app.post("/api/conversations/review")
def review_conversation():
    # 他エージェントから渡された会話ログを評価し、必要なら IoT 操作を実行

    payload = request.get_json(silent=True) or {}
    raw_history = payload.get("history")
    if raw_history is None:
        raw_history = payload.get("messages")

    if raw_history is None:
        return jsonify({"error": "history is required"}), 400
    if not isinstance(raw_history, list):
        return jsonify({"error": "history must be a list"}), 400

    messages = _normalise_conversation_messages(raw_history)

    try:
        client = _client()
        analysis = _call_llm_for_conversation_review(client, messages)
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 500
    except Exception as exc:  # pragma: no cover - network/SDK errors
        return jsonify({"error": str(exc)}), 500

    validation_target: Any = analysis.get("device_commands")
    validated_commands, validation_errors = _validate_device_command_sequence(validation_target)
    action_required = bool(analysis.get("action_required"))

    response_payload: Dict[str, Any] = {
        "analysis": {
            "action_required": action_required,
            "reason": analysis.get("reason", ""),
            "notes": analysis.get("notes"),
            "raw": analysis.get("raw"),
            "suggested_device_commands": validation_target if isinstance(validation_target, list) else [],
        },
        "action_taken": False,
    }

    if validation_errors:
        response_payload["analysis"]["validation_errors"] = validation_errors
        return jsonify(response_payload), 200

    if action_required and not validated_commands:
        response_payload["analysis"]["validation_errors"] = [
            "LLM indicated action_required but did not provide executable commands."
        ]
        return jsonify(response_payload), 200

    if action_required and validated_commands:
        initial_reply = analysis.get("reason", "")
        final_reply, status = _execute_device_command_sequence(
            client, messages, initial_reply, validated_commands
        )
        response_payload["action_taken"] = True
        response_payload["analysis"]["executed_commands"] = validated_commands
        response_payload["execution_reply"] = final_reply
        return jsonify(response_payload), status

    return jsonify(response_payload), 200


@app.post("/api/devices/register")
def register_device():
    # 新しいデバイスを手動登録し、メタ情報を保存

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


@app.get("/api/devices/<device_id>")
def get_device(device_id: str):
    # 指定デバイスの詳細情報を取得する API

    cleaned_id = (device_id or "").strip()
    if not cleaned_id:
        return jsonify({"error": "device_id is required"}), 400

    device = _DEVICES.get(cleaned_id)
    if not device:
        return jsonify({"error": "device not registered"}), 404

    return jsonify({"device": _serialize_device(device)})


@app.put("/api/devices/<device_id>")
def update_device(device_id: str):
    # デバイスのメタ情報や機能を更新する API

    cleaned_id = (device_id or "").strip()
    if not cleaned_id:
        return jsonify({"error": "device_id is required"}), 400

    device = _DEVICES.get(cleaned_id)
    if not device:
        return jsonify({"error": "device not registered"}), 404

    payload = request.get_json(silent=True) or {}

    if "capabilities" in payload:
        capabilities = payload.get("capabilities")
        if capabilities is None:
            device.capabilities = []
        elif isinstance(capabilities, list):
            device.capabilities = _normalise_capabilities(capabilities)
        else:
            return jsonify({"error": "capabilities must be a list or null"}), 400

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
            return jsonify({"error": "meta must be an object or null"}), 400

    if "approved" in payload:
        device.approved = bool(payload.get("approved"))

    device.last_seen = time.time()
    return jsonify({"status": "updated", "device": _serialize_device(device)})


@app.get("/api/devices/<device_id>/jobs")
def list_device_jobs(device_id: str):
    # デバイスごとのジョブ履歴を取得

    cleaned_id = (device_id or "").strip()
    if not cleaned_id:
        return jsonify({"error": "device_id is required"}), 400

    device = _DEVICES.get(cleaned_id)
    if not device:
        return jsonify({"error": "device not registered"}), 404

    jobs: List[Dict[str, Any]] = []
    for job_id, metadata in _JOB_METADATA.items():
        if metadata.get("device_id") != cleaned_id:
            continue

        job_info = dict(metadata)
        job_info["job_id"] = job_id

        if job_id in _PENDING_JOBS:
            if job_info.get("status") not in {"dispatched", "cancelled"}:
                job_info["status"] = "pending"
        elif job_id in _COMPLETED_JOBS and job_info.get("status") != "cancelled":
            job_info["status"] = "completed"
            job_info["result"] = _COMPLETED_JOBS[job_id]

        jobs.append({k: v for k, v in job_info.items() if v is not None})

    jobs.sort(key=lambda item: item.get("queued_at") or 0)
    return jsonify({"device_id": cleaned_id, "jobs": jobs})


@app.post("/api/devices/<device_id>/jobs")
def create_device_job(device_id: str):
    # 外部サービスから直接ジョブを投入する API

    cleaned_id = (device_id or "").strip()
    if not cleaned_id:
        return jsonify({"error": "device_id is required"}), 400

    if cleaned_id not in _DEVICES:
        return jsonify({"error": "device not registered"}), 404

    payload = request.get_json(silent=True) or {}

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
        return jsonify({"error": error_message or "invalid command"}), 400

    queue_command = {
        "name": validated_command["name"],
        "args": validated_command.get("args", {}),
    }

    job_id = _enqueue_device_command(cleaned_id, queue_command, source="api")
    if job_id is None:
        return jsonify({"error": "device not registered"}), 404

    wait_for_result = bool(payload.get("wait_for_result"))
    timeout_value = payload.get("timeout")
    try:
        timeout_seconds = float(timeout_value)
        if timeout_seconds <= 0:
            raise ValueError
    except (TypeError, ValueError):
        timeout_seconds = DEVICE_RESULT_TIMEOUT

    metadata = _JOB_METADATA.get(job_id)
    if metadata is not None:
        metadata["wait_for_result"] = wait_for_result
        requested_via = payload.get("requested_via")
        if isinstance(requested_via, str) and requested_via.strip():
            metadata["requested_via"] = requested_via.strip()
        else:
            metadata.setdefault("requested_via", "api")

    response_payload: Dict[str, Any] = {
        "status": "queued",
        "job_id": job_id,
        "device_id": cleaned_id,
        "command": queue_command,
        "wait_for_result": wait_for_result,
    }

    if metadata is not None:
        response_payload["queued_at"] = metadata.get("queued_at")

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

    return jsonify(response_payload), status_code


@app.get("/api/devices")
def list_devices():
    # 登録済みデバイス一覧を JSON 形式で返却

    devices = [_serialize_device(device) for device in _DEVICES.values()]
    devices.sort(key=lambda d: d["device_id"])
    return jsonify({"devices": devices})


@app.patch("/api/devices/<device_id>/name")
def update_device_name(device_id: str):
    # デバイスの表示名を更新する PATCH エンドポイント

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
    # デバイスを削除し、関連ジョブもクリア

    cleaned_id = (device_id or "").strip()
    if not cleaned_id:
        return jsonify({"error": "device_id is required"}), 400

    device = _DEVICES.pop(cleaned_id, None)
    if not device:
        return jsonify({"error": "device not registered"}), 404

    stale_jobs = [job_id for job_id, mapped in _PENDING_JOBS.items() if mapped == cleaned_id]
    for job_id in stale_jobs:
        _PENDING_JOBS.pop(job_id, None)
        metadata = _JOB_METADATA.get(job_id)
        if metadata is not None:
            metadata["status"] = "cancelled"
            metadata["cancelled_at"] = time.time()

    return jsonify({"status": "deleted", "device_id": cleaned_id})


@app.get("/api/devices/<device_id>/jobs/next")
def next_job(device_id: str):
    # エッジデバイスが次に取得するジョブをポーリング

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
    job_id = job.get("job_id") if isinstance(job, dict) else None
    if isinstance(job_id, str):
        metadata = _JOB_METADATA.get(job_id)
        if metadata is not None and metadata.get("status") == "pending":
            metadata["status"] = "dispatched"
            metadata["dispatched_at"] = time.time()
    return jsonify(job)


@app.get("/api/jobs/<job_id>")
def get_job(job_id: str):
    # ジョブ ID に紐づく状態と結果を返す

    cleaned_id = (job_id or "").strip()
    if not cleaned_id:
        return jsonify({"error": "job_id is required"}), 400

    metadata = _JOB_METADATA.get(cleaned_id)
    pending_device = _PENDING_JOBS.get(cleaned_id)
    result = _COMPLETED_JOBS.get(cleaned_id)

    if not metadata and not pending_device and result is None:
        return jsonify({"error": "job not found"}), 404

    response_payload: Dict[str, Any] = {"job_id": cleaned_id}

    if metadata:
        response_payload.update({k: v for k, v in metadata.items() if v is not None})

    if pending_device:
        response_payload["device_id"] = pending_device
        if metadata and metadata.get("status") == "dispatched":
            response_payload["status"] = "dispatched"
        else:
            response_payload["status"] = "pending"

    if result is not None:
        response_payload["status"] = "completed"
        response_payload["result"] = result
        response_payload.setdefault("device_id", result.get("device_id"))

    response_payload.setdefault("status", metadata.get("status") if metadata else "unknown")

    return jsonify(response_payload)


@app.delete("/api/jobs/<job_id>")
def cancel_job(job_id: str):
    # キューに残っているジョブをキャンセル

    cleaned_id = (job_id or "").strip()
    if not cleaned_id:
        return jsonify({"error": "job_id is required"}), 400

    if cleaned_id in _COMPLETED_JOBS:
        return jsonify({"error": "job already completed"}), 409

    device_id = _PENDING_JOBS.get(cleaned_id)
    metadata = _JOB_METADATA.get(cleaned_id)

    if not device_id:
        if metadata and metadata.get("status") == "cancelled":
            response_payload = {
                "status": "cancelled",
                "job_id": cleaned_id,
                "device_id": metadata.get("device_id"),
            }
            return jsonify(response_payload)
        return jsonify({"error": "job not found or already dispatched"}), 404

    device = _DEVICES.get(device_id)
    if not device:
        _PENDING_JOBS.pop(cleaned_id, None)
        if metadata is not None:
            metadata["status"] = "cancelled"
            metadata["cancelled_at"] = time.time()
        return jsonify({"status": "cancelled", "job_id": cleaned_id, "device_id": device_id})

    removed = False
    new_queue: Deque[Dict[str, Any]] = deque()
    while device.job_queue:
        job = device.job_queue.popleft()
        if not removed and job.get("job_id") == cleaned_id:
            removed = True
            continue
        new_queue.append(job)

    device.job_queue = new_queue

    if not removed:
        # ジョブは既にデバイスに取得されている
        device.job_queue = new_queue
        return jsonify({"error": "job already dispatched"}), 409

    device.last_seen = time.time()
    _PENDING_JOBS.pop(cleaned_id, None)
    if metadata is not None:
        metadata["status"] = "cancelled"
        metadata["cancelled_at"] = time.time()

    return jsonify({"status": "cancelled", "job_id": cleaned_id, "device_id": device_id})


@app.post("/api/devices/<device_id>/jobs/result")
def post_result(device_id: str):
    # エッジデバイスからのジョブ結果を受け取り記録

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
        metadata = _JOB_METADATA.setdefault(job_id, {"job_id": job_id})
        metadata["device_id"] = device.device_id
        metadata.setdefault("command", payload.get("command"))
        metadata.setdefault("queued_at", time.time())
        metadata["status"] = "completed"
        metadata["completed_at"] = time.time()
        metadata["result_ok"] = bool(payload.get("ok"))
    _store_completed_job(job_id, result_record)

    response_payload = {"status": "ack"}
    if mismatch_resolved_via_job:
        response_payload["warning"] = "device_id mismatch resolved via job_id"

    return jsonify(response_payload)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5006)
