"""Integration tests for HTTP API handlers using aiohttp TestClient."""
import pytest
import pytest_asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from aiohttp import web

from notecast.api.http.server import create_app
from notecast.core.models import User
from notecast.infrastructure.config.settings import Settings


def _make_user(name="alice", feed_token="test-token", email="alice@example.com") -> User:
    return User(
        name=name,
        email=email,
        auth_file=Path("/tmp/api/auth"),
        db_file=Path("/tmp/jobs.db"),
        history_file=Path("/tmp/history.json"),
        episodes_dir=Path("/tmp/episodes"),
        feed_dir=Path("/tmp/feed"),
        feed_token=feed_token,
    )


def _make_app(google_client_id: str = "", users: str = "alice") -> web.Application:
    user = _make_user()
    user_service = AsyncMock()
    user_service.get_all = AsyncMock(return_value=[user])
    user_service.get_by_name = AsyncMock(return_value=user)
    user_service.get_by_email = MagicMock(return_value=user)

    job_service = MagicMock()
    feed_service = AsyncMock()
    poller_service = AsyncMock()
    poller_service.poll_feeds = AsyncMock(return_value=3)
    storage = MagicMock()

    settings = Settings.model_construct(
        base_url="http://localhost",
        users=users,
        data_base=Path("/tmp/data"),
        public_dir=Path("/tmp/public"),
        webhook_url="",
        webhook_headers={},
        webhook_link="",
        google_client_id=google_client_id,
        poll_interval=86400,
        bridge_port=8080,
        bridge_api_key="",
        retention_days=14,
        token_expiry_warn_days=7,
        feed_image_url="",
        generation_timeout=2700,
        max_retries=1,
    )
    return create_app(settings, job_service, feed_service, poller_service, user_service, storage)


@pytest_asyncio.fixture
async def client(aiohttp_client):
    """No-auth mode: USERS empty → single-user, no sign-in."""
    return await aiohttp_client(_make_app(users=""))


@pytest_asyncio.fixture
async def auth_client(aiohttp_client):
    """Auth-required mode: USERS set + google_client_id."""
    return await aiohttp_client(_make_app(google_client_id="test-client-id", users="alice"))


async def test_health_no_auth_required(client):
    resp = await client.get("/api/health")
    assert resp.status == 200


async def test_protected_route_no_token_returns_401(auth_client):
    resp = await auth_client.post("/api/poll")
    assert resp.status == 401


async def test_protected_route_wrong_token_returns_401(auth_client):
    resp = await auth_client.post("/api/poll", headers={"Authorization": "Bearer wrong"})
    assert resp.status == 401


async def test_poll_with_valid_token_returns_queued_count(auth_client):
    resp = await auth_client.post("/api/poll", headers={"Authorization": "Bearer test-token"})
    assert resp.status == 200
    data = await resp.json()
    assert "queued" in data
    assert data["queued"] == 3


async def test_auth_endpoint_returns_user_info(auth_client):
    resp = await auth_client.post("/api/auth", headers={"Authorization": "Bearer test-token"})
    assert resp.status == 200
    data = await resp.json()
    assert data["authenticated"] is True
    assert data["user"] == "alice"


async def test_upload_no_token_returns_401(auth_client):
    resp = await auth_client.post("/api/auth/upload")
    assert resp.status == 401


async def test_upload_valid_credentials(auth_client):
    import json
    from aiohttp import FormData

    creds = {"cookies": [], "origins": []}
    fd = FormData()
    fd.add_field("file", json.dumps(creds), filename="storage_state.json", content_type="application/json")

    resp = await auth_client.post(
        "/api/auth/upload",
        data=fd,
        headers={"Authorization": "Bearer test-token"},
    )
    assert resp.status == 200
    data = await resp.json()
    assert data["ok"] is True
