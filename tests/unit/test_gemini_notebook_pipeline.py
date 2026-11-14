"""Tests for the hybrid public-notebook / legacy-audio pipeline."""
import json
import stat
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from notecast.core.models import Job, User
from notecast.infrastructure.external.gemini_notebook_credentials import (
    GeminiNotebookCredentialStore,
)
from notecast.services.job_service import JobService
from notecast.api.http.handlers import gemini_notebook as gemini_handler
from notecast.infrastructure.config.settings import Settings


def _user(tmp_path: Path) -> User:
    return User(
        name="alice",
        email="alice@gmail.com",
        auth_file=tmp_path / "storage_state.json",
        db_file=tmp_path / "jobs.db",
        history_file=tmp_path / "history.json",
        episodes_dir=tmp_path / "episodes",
        feed_dir=tmp_path / "feed",
        feed_token="feed-token",
    )


def _job() -> Job:
    now = datetime.now()
    return Job(
        id="job-123",
        user_name="alice",
        feed_name="news",
        feed_title="Daily News",
        episode_url="https://example.com/episode",
        source_url="https://example.com/article",
        title="Episode One",
        status="pending",
        style="brief",
        instructions="Keep it concise",
        language="fr",
        created_at=now,
        updated_at=now,
    )


@pytest.mark.asyncio
async def test_credentials_are_saved_with_private_permissions_and_refresh_metadata(tmp_path):
    store = GeminiNotebookCredentialStore(tmp_path / "data", "client-id", "secret")
    user = _user(tmp_path)

    await store.save_authorization(user, {
        "access_token": "short-lived-token",
        "refresh_token": "long-lived-refresh-token",
        "expires_in": 3600,
    })

    path = store.path_for(user)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    saved = json.loads(path.read_text())
    assert saved["email"] == user.email
    assert saved["credentials"]["refresh_token"] == "long-lived-refresh-token"
    credentials = await store.get_credentials(user)
    assert credentials.token == "short-lived-token"
    assert credentials.valid


@pytest.mark.asyncio
async def test_hybrid_job_uses_public_api_for_notebook_source_and_legacy_for_audio(
    tmp_path, monkeypatch
):
    events = []

    class PublicApi:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def create_notebook(self, title):
            events.append(("create_notebook", title))
            return {"name": "notebooks/shared-id"}

        async def create_url_source(self, name, url, *, display_name=None):
            events.append(("create_source", name, url, display_name))
            return {"name": f"{name}/sources/source-id"}

        async def wait_for_source(self, name):
            events.append(("wait_for_source", name))
            return {"state": "ACTIVE"}

    class LegacyClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get_notebook(self, notebook_id):
            events.append(("verify_legacy_access", notebook_id))

        async def generate_audio(self, notebook_id, **kwargs):
            events.append(("generate_audio", notebook_id, kwargs))

    user = _user(tmp_path)
    job = _job()
    repo = MagicMock()
    legacy = MagicMock()
    legacy.session = AsyncMock(side_effect=lambda _user: LegacyClient())
    credentials = MagicMock()
    credentials.has_credentials.return_value = True
    credentials.get_credentials = AsyncMock(return_value=object())

    import notecast.services.job_service as job_module

    monkeypatch.setattr(job_module, "GeminiNotebookApiClient", lambda *_a, **_kw: PublicApi())
    service = JobService(
        repo_factory=lambda _user: repo,
        storage=MagicMock(),
        nb_client=legacy,
        feed_service=MagicMock(),
        gemini_credentials=credentials,
        gemini_project_id="notecast-test",
        gemini_pipeline_enabled=True,
    )

    await service.process_job(user, job, {})

    assert [event[0] for event in events] == [
        "create_notebook",
        "verify_legacy_access",
        "create_source",
        "wait_for_source",
        "generate_audio",
    ]
    assert events[1] == ("verify_legacy_access", "shared-id")
    assert events[-1][1] == "shared-id"
    assert repo.update_job.call_args_list[-1].kwargs == {"status": "generating"}


@pytest.mark.asyncio
async def test_hybrid_pipeline_falls_back_for_user_without_saved_oauth(tmp_path, monkeypatch):
    user = _user(tmp_path)
    job = _job()
    repo = MagicMock()
    legacy_client = MagicMock()
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    client.create_notebook = AsyncMock(return_value=MagicMock(id="legacy-id"))
    client.add_source = AsyncMock()
    client.generate_audio = AsyncMock()
    legacy_client.session = AsyncMock(return_value=client)
    credentials = MagicMock()
    credentials.has_credentials.return_value = False
    credentials.get_credentials = AsyncMock()

    import notecast.services.job_service as job_module

    public_api = MagicMock()
    monkeypatch.setattr(job_module, "GeminiNotebookApiClient", public_api)
    service = JobService(
        repo_factory=lambda _user: repo,
        storage=MagicMock(),
        nb_client=legacy_client,
        feed_service=MagicMock(),
        gemini_credentials=credentials,
        gemini_project_id="notecast-test",
        gemini_pipeline_enabled=True,
    )

    await service.process_job(user, job, {})

    public_api.assert_not_called()
    credentials.get_credentials.assert_not_awaited()
    client.create_notebook.assert_awaited_once_with(job.title)
    client.add_source.assert_awaited_once_with("legacy-id", url=job.source_url)
    client.generate_audio.assert_awaited_once_with(
        "legacy-id",
        style=job.style,
        instructions=job.instructions,
        language=job.language,
    )


@pytest.mark.asyncio
async def test_oauth_code_exchange_validates_identity_and_saves_refresh_credential(
    tmp_path, monkeypatch
):
    user = _user(tmp_path)
    settings = Settings(
        google_client_id="client-id",
        google_client_secret="client-secret",
        config_dir=tmp_path / "config",
        data_base=tmp_path / "data",
        base_url="http://localhost:8081",
    )
    app = web.Application()
    app["settings"] = settings
    request = make_mocked_request(
        "POST",
        "/api/gemini-notebook/oauth/exchange",
        headers={
            "Origin": "http://localhost:8081",
            "X-Requested-With": "XmlHttpRequest",
            "Content-Type": "application/json",
        },
        app=app,
    )
    request["user"] = user
    request.json = AsyncMock(return_value={
        "code": "one-time-code",
        "scope": gemini_handler.NOTEBOOK_SCOPE,
    })
    saved = AsyncMock()

    class Response:
        def __init__(self, data):
            self._data = data

        def raise_for_status(self):
            return None

        def json(self):
            return self._data

    class Http:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, _url, data):
            assert data["code"] == "one-time-code"
            return Response({
                "access_token": "access",
                "refresh_token": "refresh",
                "scope": gemini_handler.NOTEBOOK_SCOPE,
            })

        async def get(self, _url, headers):
            assert headers["Authorization"] == "Bearer access"
            return Response({"email": user.email, "email_verified": True})

    monkeypatch.setattr(gemini_handler.httpx, "AsyncClient", lambda **_kwargs: Http())

    store_args = []

    class Store:
        def __init__(self, *_args):
            store_args.extend(_args)
            self.save_authorization = saved

    monkeypatch.setattr(gemini_handler, "GeminiNotebookCredentialStore", Store)
    response = await gemini_handler.exchange_gemini_notebook_code(request)

    assert response.status == 200
    assert json.loads(response.text)["access_token"] == "access"
    assert store_args[0] == settings.data_base / "gemini_notebook_oauth"
    saved.assert_awaited_once_with(user, {
        "access_token": "access",
        "refresh_token": "refresh",
        "scope": gemini_handler.NOTEBOOK_SCOPE,
    })


@pytest.mark.asyncio
async def test_oauth_status_reports_saved_user_connection(tmp_path):
    user = _user(tmp_path)
    settings = Settings(
        google_client_id="client-id",
        google_client_secret="client-secret",
        data_base=tmp_path / "data",
    )
    store = GeminiNotebookCredentialStore(
        settings.data_base / "gemini_notebook_oauth",
        settings.google_client_id,
        settings.google_client_secret,
    )
    await store.save_authorization(user, {
        "access_token": "access",
        "refresh_token": "refresh",
        "expires_in": 3600,
    })
    app = web.Application()
    app["settings"] = settings
    request = make_mocked_request(
        "GET", "/api/gemini-notebook/oauth/status", app=app
    )
    request["user"] = user

    response = await gemini_handler.get_gemini_notebook_oauth_status(request)

    assert json.loads(response.text) == {
        "connected": True,
        "email": user.email,
    }
