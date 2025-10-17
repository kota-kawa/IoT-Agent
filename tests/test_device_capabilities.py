from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import app, _DEVICES, _PENDING_JOBS


@pytest.fixture(autouse=True)
def clear_state():
    _DEVICES.clear()
    _PENDING_JOBS.clear()
    yield
    _DEVICES.clear()
    _PENDING_JOBS.clear()


@pytest.fixture
def client():
    with app.test_client() as client:
        yield client


def _manual_register(client, device_id: str) -> None:
    payload = {
        "device_id": device_id,
        "capabilities": [],
        "meta": {"registered_via": "dashboard"},
        "approved": True,
    }
    response = client.post("/api/devices/register", json=payload)
    assert response.status_code == 200


def test_capabilities_are_normalised(client):
    device_id = "test-device"
    _manual_register(client, device_id)

    raw_capabilities = [
        {
            "name": "  led  ",
            "description": " Blink onboard LED ",
            "params": [
                {"name": " times ", "type": " int ", "required": "yes", "default": 5},
                {
                    "name": "interval_sec",
                    "type": "float",
                    "required": False,
                    "default": 0.5,
                    "description": " Interval between blinks ",
                },
                None,
            ],
        },
        {"name": "", "description": "ignored"},
        "not-a-dict",
        {
            "name": "temp",
            "description": None,
            "params": [
                {"name": "", "type": "int"},
                {"name": "samples", "required": True, "default": 3},
            ],
        },
    ]

    response = client.post(
        "/api/devices/register",
        json={"device_id": device_id, "capabilities": raw_capabilities, "meta": {}},
    )
    assert response.status_code == 200
    data = response.get_json()

    expected = [
        {
            "name": "led",
            "description": "Blink onboard LED",
            "params": [
                {"name": "times", "type": "int", "required": True, "default": 5},
                {
                    "name": "interval_sec",
                    "type": "float",
                    "required": False,
                    "default": 0.5,
                    "description": "Interval between blinks",
                },
            ],
        },
        {"name": "temp", "params": [{"name": "samples", "required": True, "default": 3}]},
    ]

    assert data["device"]["capabilities"] == expected
    assert _DEVICES[device_id].capabilities == expected
