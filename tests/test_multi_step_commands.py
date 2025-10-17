from typing import Any, Dict, List

import pytest

from app import (
    AGENT_CAPABILITY_NAME,
    AGENT_COMMAND_NAME,
    AGENT_ROLE_VALUE,
    DeviceState,
    _CommandExecutionSummary,
    _DEVICES,
    _execute_device_command_sequence,
    _format_return_value_for_user,
    _validate_device_command_sequence,
)


@pytest.fixture(autouse=True)
def clear_state():
    _DEVICES.clear()
    yield
    _DEVICES.clear()


def _register_device(
    device_id: str,
    capabilities: List[Dict[str, Any]],
    meta: Dict[str, Any],
    approved: bool = True,
) -> None:
    _DEVICES[device_id] = DeviceState(
        device_id=device_id,
        capabilities=capabilities,
        meta=meta,
        approved=approved,
    )


@pytest.fixture
def client():
    from app import app

    with app.test_client() as test_client:
        yield test_client


def test_validate_device_command_sequence_multiple():
    device_id = "sensor-1"
    _register_device(
        device_id,
        capabilities=[{"name": "read_temp"}, {"name": "sync_time"}],
        meta={},
    )

    commands = [
        {"device_id": device_id, "name": "read_temp", "args": {}},
        {"device_id": device_id, "name": "sync_time", "args": {"offset": 3}},
    ]

    validated, errors = _validate_device_command_sequence(commands)

    assert not errors
    assert validated == commands


def test_execute_device_command_sequence_mixed_types(monkeypatch):
    agent_id = "agent-1"
    sensor_id = "sensor-1"

    _register_device(
        agent_id,
        capabilities=[{"name": AGENT_CAPABILITY_NAME}],
        meta={"role": AGENT_ROLE_VALUE},
    )
    _register_device(
        sensor_id,
        capabilities=[{"name": "measure"}],
        meta={},
    )

    commands = [
        {"device_id": agent_id, "name": AGENT_COMMAND_NAME, "args": {"instruction": "Do X"}},
        {"device_id": sensor_id, "name": "measure", "args": {"duration": 5}},
    ]

    agent_calls: List[Dict[str, Any]] = []
    standard_calls: List[Dict[str, Any]] = []

    def fake_agent(client, agent, messages, initial_reply, command):
        agent_calls.append({
            "initial": initial_reply,
            "command": command,
        })
        return _CommandExecutionSummary(
            device_id=agent.device_id,
            command_name=AGENT_COMMAND_NAME,
            args=command.get("args", {}),
            manual_reply="Agent 完了",
            instruction=command.get("args", {}).get("instruction"),
            is_agent=True,
        )

    def fake_standard(client, messages, initial_reply, command):
        standard_calls.append({
            "initial": initial_reply,
            "command": command,
        })
        return _CommandExecutionSummary(
            device_id=command.get("device_id"),
            command_name=command.get("name", ""),
            args=command.get("args", {}),
            manual_reply="Standard 完了",
        )

    monkeypatch.setattr("app._execute_agent_device_command", fake_agent)
    monkeypatch.setattr("app._execute_standard_device_command", fake_standard)

    reply, status = _execute_device_command_sequence(
        client=None,
        messages=[],
        initial_reply="了解しました",
        commands=commands,
    )

    assert status == 200
    assert reply == "Agent 完了\n\nStandard 完了"

    assert agent_calls and agent_calls[0]["initial"] == "了解しました"
    assert standard_calls and standard_calls[0]["initial"] == "Agent 完了"


def test_execute_device_command_sequence_agent_failure(monkeypatch):
    agent_id = "agent-1"
    _register_device(
        agent_id,
        capabilities=[{"name": AGENT_CAPABILITY_NAME}],
        meta={"role": AGENT_ROLE_VALUE},
    )

    commands = [
        {"device_id": agent_id, "name": AGENT_COMMAND_NAME, "args": {"instruction": "Do X"}},
    ]

    def fake_agent(client, agent, messages, initial_reply, command):
        return _CommandExecutionSummary(
            device_id=agent.device_id,
            command_name=AGENT_COMMAND_NAME,
            args=command.get("args", {}),
            manual_reply="失敗しました",
            is_agent=True,
            status=500,
            error_text="失敗しました",
        )

    monkeypatch.setattr("app._execute_agent_device_command", fake_agent)

    reply, status = _execute_device_command_sequence(
        client=None,
        messages=[],
        initial_reply="初期応答",
        commands=commands,
    )

    assert status == 500
    assert reply == "失敗しました"


def test_chat_executes_multiple_commands(monkeypatch, client):
    device_id = "sensor-1"
    register_payload = {
        "device_id": device_id,
        "capabilities": [
            {"name": "read_temp"},
            {"name": "sync_time"},
        ],
        "meta": {},
        "approved": True,
    }

    response = client.post("/api/devices/register", json=register_payload)
    assert response.status_code == 200

    monkeypatch.setattr("app._client", lambda: object())

    calls: List[Dict[str, Any]] = []

    def fake_execute_standard(client_obj, messages, initial_reply, command):
        calls.append({"initial": initial_reply, "command": command})
        return _CommandExecutionSummary(
            device_id=command.get("device_id"),
            command_name=command.get("name", ""),
            args=command.get("args", {}),
            manual_reply=f"結果:{command['name']}",
        )

    def fake_llm_parse(client_obj, messages):
        return {
            "reply": "了解しました",
            "device_commands": [
                {"device_id": device_id, "name": "read_temp", "args": {}},
                {"device_id": device_id, "name": "sync_time", "args": {}},
            ],
            "raw": "",
        }

    monkeypatch.setattr("app._execute_standard_device_command", fake_execute_standard)
    monkeypatch.setattr("app._call_llm_and_parse", fake_llm_parse)

    chat_response = client.post(
        "/api/chat",
        json={
            "messages": [
                {"role": "user", "content": "センサーの値を読んで時間を同期して"},
            ]
        },
    )

    assert chat_response.status_code == 200
    payload = chat_response.get_json()
    assert payload["reply"] == "結果:read_temp\n\n結果:sync_time"

    assert len(calls) == 2
    assert calls[0]["initial"] == "了解しました"
    assert calls[1]["initial"] == "結果:read_temp"


def test_format_return_value_for_multi_action():
    payload = {
        "action": "multi_action_sequence",
        "parameters": {
            "actions": ["get_current_time", "tell_joke"],
            "total_steps": 2,
            "successful_steps": 1,
            "success": False,
            "failed_steps": [2],
        },
        "message": "get_current_time: 成功 / tell_joke: 失敗 / エラー: tell_joke: ネットワークエラー",
        "result": {
            "summary": {
                "actions": ["get_current_time", "tell_joke"],
                "total_steps": 2,
                "successful_steps": 1,
                "success": False,
                "failed_steps": [2],
            },
            "steps": [
                {
                    "step": 1,
                    "action": "get_current_time",
                    "ok": True,
                    "parameters": {},
                    "result": {"current_time": "2025-01-01T00:00:00+00:00"},
                },
                {
                    "step": 2,
                    "action": "tell_joke",
                    "ok": False,
                    "parameters": {},
                    "error": "ネットワークエラー",
                },
            ],
        },
    }

    formatted = _format_return_value_for_user(payload)

    assert "1. get_current_time" in formatted
    assert "成功" in formatted
    assert "2. tell_joke" in formatted
    assert "失敗" in formatted
    assert "メッセージ" in formatted
