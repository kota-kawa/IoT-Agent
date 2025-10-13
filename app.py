import json
import os
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, List, Optional

from dotenv import load_dotenv as loadenv
from flask import Flask, jsonify, redirect, request, session, url_for
from openai import OpenAI


loadenv()

app = Flask(__name__, static_folder=".", static_url_path="")
app.secret_key = os.getenv("FLASK_SECRET_KEY", "change-this-secret")

APP_PASSWORD = "kkawagoe"


@dataclass
class DeviceState:
    device_id: str
    capabilities: List[Dict[str, Any]]
    meta: Dict[str, Any]
    job_queue: Deque[Dict[str, Any]] = field(default_factory=deque)
    last_seen: float = field(default_factory=time.time)
    last_result: Optional[Dict[str, Any]] = None
    registered_at: float = field(default_factory=time.time)


_DEVICES: Dict[str, DeviceState] = {}


def _client() -> OpenAI:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set")
    return OpenAI(api_key=api_key)


def _first_device_id() -> Optional[str]:
    return next(iter(_DEVICES), None)


def _build_device_context() -> str:
    if not _DEVICES:
        return "No devices are currently registered."

    lines: List[str] = []
    for device in _DEVICES.values():
        lines.append(f"Device ID: {device.device_id}")
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
    return job_id


def _validate_device_command(command: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not isinstance(command, dict):
        return None

    device_id = command.get("device_id") or _first_device_id()
    if not device_id or device_id not in _DEVICES:
        return None

    name = command.get("name")
    if not isinstance(name, str) or not name.strip():
        return None

    args = command.get("args")
    if args is None:
        args = {}
    if not isinstance(args, dict):
        return None

    validated = {
        "device_id": device_id,
        "name": name.strip(),
        "args": args,
    }
    return validated


def _serialize_device(device: DeviceState) -> Dict[str, Any]:
    return {
        "device_id": device.device_id,
        "capabilities": device.capabilities,
        "meta": device.meta,
        "queue_depth": len(device.job_queue),
        "last_seen": device.last_seen,
        "registered_at": device.registered_at,
        "last_result": device.last_result,
    }


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
        "use device IDs and capability names provided in the context."
    )

    context_message = (
        "Available device information:\n" + device_context
        if device_context
        else "No devices are currently registered."
    )

    return {
        "model": "gpt-5",
        "input": [
            {"role": "system", "content": system_prompt},
            {"role": "system", "content": context_message},
            *messages,
        ],
    }


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


@app.get("/pico-w-test")
def pico_w_test():
    return jsonify({"message": "Successful!"})


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

    try:
        client = _client()
        response = client.responses.create(**_structured_llm_prompt(formatted_messages))
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 500
    except Exception as exc:  # pragma: no cover - network/SDK errors
        return jsonify({"error": str(exc)}), 500

    reply_text = getattr(response, "output_text", None) or ""

    try:
        parsed = json.loads(reply_text)
    except json.JSONDecodeError:
        parsed = {"reply": reply_text.strip(), "device_command": None}

    reply_message = parsed.get("reply")
    if not isinstance(reply_message, str):
        reply_message = reply_text.strip()

    device_command = parsed.get("device_command")
    validated_command = _validate_device_command(device_command) if device_command else None

    if validated_command:
        command_payload = {
            "name": validated_command["name"],
            "args": validated_command["args"],
        }
        job_id = _enqueue_device_command(validated_command["device_id"], command_payload)
        if job_id is None:
            reply_message += "\n(注意: デバイスにコマンドを送信できませんでした。)"

    return jsonify({"reply": reply_message})


@app.post("/pico-w/register")
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

    existing = _DEVICES.get(cleaned_id)
    if existing:
        existing.capabilities = capabilities
        existing.meta = metadata
        existing.last_seen = now
        status = "updated"
        device_state = existing
    else:
        device_state = DeviceState(
            device_id=cleaned_id,
            capabilities=capabilities,
            meta=metadata,
            last_seen=now,
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


@app.get("/pico-w/next")
def next_job():
    device_id = request.args.get("device_id", "").strip()
    if not device_id:
        return jsonify({"error": "device_id is required"}), 400

    device = _DEVICES.get(device_id)
    if not device:
        return jsonify({"error": "device not registered"}), 404

    device.last_seen = time.time()

    if not device.job_queue:
        return ("", 204)

    job = device.job_queue.popleft()
    return jsonify(job)


@app.post("/pico-w/result")
def post_result():
    payload = request.get_json(silent=True) or {}
    device_id = payload.get("device_id")
    if not isinstance(device_id, str) or not device_id.strip():
        return jsonify({"error": "device_id is required"}), 400

    device = _DEVICES.get(device_id.strip())
    if not device:
        return jsonify({"error": "device not registered"}), 404

    device.last_seen = time.time()
    device.last_result = {
        "job_id": payload.get("job_id"),
        "ok": bool(payload.get("ok")),
        "return_value": payload.get("return_value"),
        "ts": payload.get("ts"),
    }

    return jsonify({"status": "ack"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5006)
