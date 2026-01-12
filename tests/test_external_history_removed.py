import asyncio

import httpx

from app import app


async def _with_client(assertions):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await assertions(client)


def test_conversation_review_endpoint_removed():
    async def run(client):
        res = await client.post("/api/conversations/review", json={"history": []})
        assert res.status_code == 404

    asyncio.run(_with_client(run))
