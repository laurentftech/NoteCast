"""Integration tests for HTTP API handlers using aiohttp TestClient."""
import json
import pytest
import pytest_asyncio
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from aiohttp import web

from notecast.api.http.server import create_app
from notecast.core.models import Job, User
from notecast.infrastructure.config.settings import Settings


def _make_job(job_id="job1", feed_name="my-feed", artifact_id: str | None = "art1") -> Job:
    now = datetime(2026, 1, 1)
    return Job(
        id=job_id,
        user_name="alice",
        feed_name=feed_name,
        feed_title="My Feed",
        episode_url="https://example.com/ep1.mp3",
        title="Episode 1",
        status="done",
        artifact_id=artifact_id,
        created_at=now,
        updated_at=now,
    )


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

    repo = MagicMock()
    repo.get_all_done_jobs = MagicMock(return_value=[])
    repo.get_job = MagicMock(return_value=None)
    repo.update_job = MagicMock()
    repo.get_queue_counts = MagicMock(return_value={"pending": 0, "generating": 0})
    repo_factory = MagicMock(return_value=repo)

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
    return create_app(settings, job_service, feed_service, poller_service, user_service, storage, repo_factory=repo_factory)


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


async def test_delete_episode_no_token_returns_401(auth_client):
    resp = await auth_client.delete("/api/episodes/job1")
    assert resp.status == 401


async def test_delete_episode_not_found_returns_404(auth_client):
    resp = await auth_client.delete(
        "/api/episodes/missing",
        headers={"Authorization": "Bearer test-token"},
    )
    assert resp.status == 404


async def test_delete_episode_success(aiohttp_client):
    job = _make_job()
    app = _make_app(users="")
    repo = app["repo_factory"](None)
    repo.get_job.return_value = job
    audio_path = MagicMock()
    app["storage"].episode_path.return_value = audio_path

    client = await aiohttp_client(app)
    resp = await client.delete("/api/episodes/job1")
    assert resp.status == 200
    data = await resp.json()
    assert data["ok"] is True
    audio_path.unlink.assert_called_once_with(missing_ok=True)
    # verify job marked deleted
    call_kwargs = repo.update_job.call_args
    assert call_kwargs.kwargs.get("status") == "deleted" or "deleted" in call_kwargs.args


async def test_delete_episode_no_artifact_skips_file_deletion(aiohttp_client):
    job = _make_job(artifact_id=None)
    app = _make_app(users="")
    app["repo_factory"](None).get_job.return_value = job

    client = await aiohttp_client(app)
    resp = await client.delete("/api/episodes/job1")
    assert resp.status == 200
    app["storage"].episode_path.assert_not_called()


async def test_browser_cookies_no_token_returns_401(auth_client):
    resp = await auth_client.post(
        "/api/auth/browser-cookies",
        json={"browser": "chrome"},
    )
    assert resp.status == 401


async def test_browser_cookies_rookiepy_not_installed_returns_400(auth_client):
    with patch.dict("sys.modules", {"notebooklm": None}):
        resp = await auth_client.post(
            "/api/auth/browser-cookies",
            json={"browser": "chrome"},
            headers={"Authorization": "Bearer test-token"},
        )
    assert resp.status == 400


async def test_browser_cookies_success(aiohttp_client, tmp_path):
    import sys
    fake_state = {"cookies": [{"name": "SID", "value": "abc"}], "origins": []}
    app = _make_app(users="")
    user = _make_user()
    auth_file = tmp_path / "storage_state.json"
    patched_user = user.model_copy(update={"auth_file": auth_file})
    app["user_service"].get_all = AsyncMock(return_value=[patched_user])

    fake_module = MagicMock()
    fake_module.convert_rookiepy_cookies_to_storage_state.return_value = fake_state
    with patch.dict(sys.modules, {"notebooklm": fake_module}):
        client = await aiohttp_client(app)
        resp = await client.post("/api/auth/browser-cookies", json={"browser": "chrome"})

    assert resp.status == 200
    data = await resp.json()
    assert data["ok"] is True
    assert auth_file.exists()
    assert json.loads(auth_file.read_text()) == fake_state


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
