import os

from dotenv import load_dotenv as loadenv
from flask import Flask, jsonify, request
from openai import OpenAI


loadenv()

app = Flask(__name__, static_folder=".", static_url_path="")


def _client() -> OpenAI:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set")
    return OpenAI(api_key=api_key)


@app.get("/")
def index():
    return app.send_static_file("index.html")


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
        response = client.responses.create(
            model="gpt-5",
            input=[
                {"role": "system", "content": "You are a friendly assistant for an IoT dashboard."},
                *formatted_messages,
            ],
        )
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 500
    except Exception as exc:  # pragma: no cover - network/SDK errors
        return jsonify({"error": str(exc)}), 500

    reply_text = getattr(response, "output_text", None) or ""

    return jsonify({"reply": reply_text})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5006)
