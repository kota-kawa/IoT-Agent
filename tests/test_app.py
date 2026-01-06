import asyncio

import httpx
import pytest

from app import app
from iot_agent.config import APP_PASSWORD
from iot_agent.storage import reset_store


@pytest.fixture(autouse=True)
def reset_state():
    reset_store()
    yield


async def _with_client(assertions):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await assertions(client)


def test_root_redirects_when_unauthenticated():
    async def run(client):
        response = await client.get("/", follow_redirects=False)
        assert response.status_code == 302
        assert response.headers.get("location") == "/login"

    asyncio.run(_with_client(run))


def test_session_login_logout_flow():
    async def run(client):
        response = await client.post("/api/session", json={"password": APP_PASSWORD})
        assert response.status_code == 200
        assert response.json() == {"authenticated": True}

        response = await client.get("/api/session")
        assert response.status_code == 200
        assert response.json() == {"authenticated": True}

        response = await client.delete("/api/session")
        assert response.status_code == 200
        assert response.json() == {"authenticated": False}

        response = await client.get("/api/session")
        assert response.status_code == 200
        assert response.json() == {"authenticated": False}

    asyncio.run(_with_client(run))


def test_login_form_sets_session():
    async def run(client):
        response = await client.post("/login", data={"password": APP_PASSWORD}, follow_redirects=False)
        assert response.status_code == 302
        assert response.headers.get("location") == "/"

        response = await client.get("/api/session")
        assert response.status_code == 200
        assert response.json() == {"authenticated": True}

    asyncio.run(_with_client(run))


def test_register_device_validation_and_list():
    async def run(client):
        response = await client.post("/api/devices/register", json={"device_id": "", "capabilities": []})
        assert response.status_code == 400

        payload = {
            "device_id": "device-1",
            "capabilities": [],
            "meta": {"registered_via": "dashboard"},
        }
        response = await client.post("/api/devices/register", json=payload)
        assert response.status_code == 200
        assert response.json().get("status") == "registered"

        response = await client.get("/api/devices")
        assert response.status_code == 200
        devices = response.json().get("devices", [])
        assert len(devices) == 1
        assert devices[0]["device_id"] == "device-1"

    asyncio.run(_with_client(run))
