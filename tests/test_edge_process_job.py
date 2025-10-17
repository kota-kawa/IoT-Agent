import importlib.util
import logging
import sys
from pathlib import Path

# エッジデバイス用スクリプトの動作を検証するテスト群
import pytest


@pytest.fixture(scope="module")
def edge_agent_module():
    # 実際のエージェントモジュールをテスト用に読み込み、外部依存をスタブ化
    module_name = "raspberrypi_iot_edge_testmodule"
    module_path = (
        Path(__file__).resolve().parents[1]
        / "edge_device_code"
        / "raspberrypi4"
        / "raspberrypi-iot-edge.py"
    )
    import types

    if "requests" not in sys.modules:
        # HTTP 通信を行う requests が無くても動作するようスタブを登録
        fake_requests = types.ModuleType("requests")

        class _FakeSession:
            pass

        def _not_implemented(*args, **kwargs):
            raise NotImplementedError

        fake_requests.Session = _FakeSession
        fake_requests.RequestException = Exception
        fake_requests.get = _not_implemented
        fake_requests.post = _not_implemented
        sys.modules["requests"] = fake_requests

    if "llama_cpp" not in sys.modules:
        # ローカル LLM 実装が無い環境向けにダミーのクラスを提供
        fake_llama = types.ModuleType("llama_cpp")

        class _FakeLlama:  # pragma: no cover - simple stub
            def __init__(self, *args, **kwargs):
                pass

        fake_llama.Llama = _FakeLlama
        sys.modules["llama_cpp"] = fake_llama
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.fixture(autouse=True)
def disable_console(monkeypatch, edge_agent_module):
    # コンソール出力を捕捉し、ログ内容をテストで検証可能にする
    messages = []

    def _capture(message: str) -> None:
        messages.append(message)

    monkeypatch.setattr(edge_agent_module, "_console", _capture)
    yield messages


def _capture_posts(monkeypatch, edge_agent_module):
    # _post_result 呼び出しをフックし、送信されるペイロードを記録
    posted = []

    def _fake_post(session, payload, **kwargs):
        posted.append(payload)
        return True

    monkeypatch.setattr(edge_agent_module, "_post_result", _fake_post)
    return posted


def test_process_job_direct_action_success(monkeypatch, caplog, edge_agent_module):
    # 通常アクション成功時に正常な結果が送信されることを確認
    posted = _capture_posts(monkeypatch, edge_agent_module)

    def fake_execute(action, params):
        return True, {"status": "ok"}, None

    monkeypatch.setattr(edge_agent_module, "_execute_action", fake_execute)

    caplog.set_level(logging.INFO)

    job = {
        "job_id": "job-1",
        "command": {"name": "reboot", "args": {"delay": 5}},
    }

    edge_agent_module._process_job(
        session=object(),
        llm=None,
        device_id="edge-1",
        job=job,
    )

    assert posted
    assert posted[0]["return_value"]["action"] == "reboot"
    assert any("action=reboot" in record.getMessage() for record in caplog.records)


def test_process_job_direct_action_failure(monkeypatch, caplog, edge_agent_module):
    # アクション失敗時にエラーメッセージが送信されることを検証
    posted = _capture_posts(monkeypatch, edge_agent_module)

    def fake_execute(action, params):
        return False, {"partial": True}, "boom"

    monkeypatch.setattr(edge_agent_module, "_execute_action", fake_execute)

    caplog.set_level(logging.INFO)

    job = {
        "job_id": "job-2",
        "command": {"name": "diagnose", "args": {"level": 1}},
    }

    edge_agent_module._process_job(
        session=object(),
        llm=None,
        device_id="edge-1",
        job=job,
    )

    assert posted
    assert not posted[0]["ok"]
    assert posted[0]["error"] == "boom"
    assert posted[0]["return_value"]["action"] == "diagnose"
    assert any("failed" in record.getMessage() for record in caplog.records)


def test_process_job_instruction_logs_message(monkeypatch, caplog, edge_agent_module):
    # エージェント指示ジョブがログと返却メッセージを残すことを確認
    posted = _capture_posts(monkeypatch, edge_agent_module)

    def fake_build_plan(llm, instruction):
        return ["step"]

    def fake_execute_plan(plans):
        return (
            True,
            {"steps": [], "summary": {}},
            "Agent からのメッセージ",
            None,
            "turn_on_light",
            {"room": "lab"},
        )

    monkeypatch.setattr(edge_agent_module, "_build_multi_action_plan", fake_build_plan)
    monkeypatch.setattr(edge_agent_module, "_execute_plan_sequence", fake_execute_plan)

    caplog.set_level(logging.INFO)

    job = {
        "job_id": "job-3",
        "command": {
            "name": edge_agent_module.AGENT_COMMAND_NAME,
            "args": {"instruction": "ライトを点灯"},
        },
    }

    edge_agent_module._process_job(
        session=object(),
        llm=object(),
        device_id="edge-1",
        job=job,
    )

    assert posted
    assert posted[0]["return_value"]["message"] == "Agent からのメッセージ"
    assert any("agent message" in record.getMessage() for record in caplog.records)


def test_process_job_other_device(monkeypatch, edge_agent_module):
    # 別デバイス宛てのジョブはスキップされることを確認
    posted = _capture_posts(monkeypatch, edge_agent_module)

    def fail_execute(*args, **kwargs):
        raise AssertionError("_execute_action should not be called")

    monkeypatch.setattr(edge_agent_module, "_execute_action", fail_execute)

    job = {
        "job_id": "job-4",
        "device_id": "edge-2",
        "command": {"name": "noop", "args": {}},
    }

    edge_agent_module._process_job(
        session=object(),
        llm=None,
        device_id="edge-1",
        job=job,
    )

    assert posted
    assert posted[0]["error"].startswith("Job is targeted to device")


def test_process_job_missing_command(monkeypatch, edge_agent_module):
    # コマンド情報が欠落しているジョブを適切にエラー化するか検証
    posted = _capture_posts(monkeypatch, edge_agent_module)

    job = {
        "job_id": "job-5",
        "command": {},
    }

    edge_agent_module._process_job(
        session=object(),
        llm=None,
        device_id="edge-1",
        job=job,
    )

    assert posted
    assert posted[0]["error"] == "Job is missing a command name."
