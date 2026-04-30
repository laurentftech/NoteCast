import json
import pytest
import aiohttp
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch, AsyncMock
from aiohttp import web


# ── Helpers ────────────────────────────────────────────────────────────────

def make_entry(downloaded_at=None, created_at=None, mp3_filename="ep.m4a"):
    entry = {"title": "T", "mp3_filename": mp3_filename}
    if downloaded_at:
        entry["downloaded_at"] = downloaded_at
    if created_at:
        entry["created_at"] = created_at
    return entry

def iso(dt):
    return dt.isoformat()

def days_ago(n):
    return datetime.now(timezone.utc) - timedelta(days=n)


# ── purge_old_episodes ──────────────────────────────────────────────────────

def test_purge_keeps_recent_episode(tmp_path):
    import harvester
    mp3 = tmp_path / "ep.m4a"
    mp3.write_bytes(b"")
    history = {"a1": make_entry(downloaded_at=iso(days_ago(3)), mp3_filename="ep.m4a")}
    with patch.object(harvester, "RETENTION_DAYS", 14):
        result = harvester.purge_old_episodes(history, tmp_path)
    assert "a1" in result
    assert mp3.exists()

def test_purge_removes_old_episode(tmp_path):
    import harvester
    mp3 = tmp_path / "old.m4a"
    mp3.write_bytes(b"")
    history = {"a2": make_entry(downloaded_at=iso(days_ago(20)), mp3_filename="old.m4a")}
    with patch.object(harvester, "RETENTION_DAYS", 14):
        result = harvester.purge_old_episodes(history, tmp_path)
    assert "a2" not in result
    assert not mp3.exists()

def test_purge_uses_downloaded_at_not_created_at(tmp_path):
    """Regression: old bug used created_at (weeks-old artifact date) causing fresh episodes to purge."""
    import harvester
    mp3 = tmp_path / "fresh.m4a"
    mp3.write_bytes(b"")
    history = {"a3": make_entry(
        downloaded_at=iso(days_ago(1)),   # downloaded yesterday — keep
        created_at=iso(days_ago(30)),     # created 30 days ago — would wrongly purge
        mp3_filename="fresh.m4a",
    )}
    with patch.object(harvester, "RETENTION_DAYS", 14):
        result = harvester.purge_old_episodes(history, tmp_path)
    assert "a3" in result, "Episode downloaded recently must not be purged"

def test_purge_falls_back_to_created_at(tmp_path):
    import harvester
    mp3 = tmp_path / "old2.m4a"
    mp3.write_bytes(b"")
    history = {"a4": make_entry(created_at=iso(days_ago(20)), mp3_filename="old2.m4a")}
    with patch.object(harvester, "RETENTION_DAYS", 14):
        result = harvester.purge_old_episodes(history, tmp_path)
    assert "a4" not in result


# ── recover_history_from_disk ───────────────────────────────────────────────

def test_recover_adds_untracked_mp3(tmp_path):
    import harvester
    mp3 = tmp_path / "abc12345.mp3"
    mp3.write_bytes(b"")
    history = {}
    harvester.recover_history_from_disk(history, tmp_path)
    assert "abc12345" in history
    assert history["abc12345"]["mp3_filename"] == "abc12345.mp3"

def test_recover_adds_untracked_m4a(tmp_path):
    import harvester
    m4a = tmp_path / "abc12345.m4a"
    m4a.write_bytes(b"")
    history = {}
    harvester.recover_history_from_disk(history, tmp_path)
    assert "abc12345" in history
    assert history["abc12345"]["mp3_filename"] == "abc12345.m4a"

def test_recover_skips_already_tracked(tmp_path):
    import harvester
    mp3 = tmp_path / "known.mp3"
    mp3.write_bytes(b"")
    history = {"x": {"mp3_filename": "known.mp3", "title": "T", "created_at": "", "notebook": ""}}
    harvester.recover_history_from_disk(history, tmp_path)
    assert len(history) == 1


# ── get_token_expiry ────────────────────────────────────────────────────────

def test_token_expiry_missing_file(tmp_path):
    import harvester
    ts, days, iso_str = harvester.get_token_expiry(tmp_path / "missing.json")
    assert ts is None and days is None and iso_str is None

def test_token_expiry_reads_earliest_cookie(tmp_path):
    import harvester
    future = datetime.now(timezone.utc) + timedelta(days=10)
    far_future = datetime.now(timezone.utc) + timedelta(days=30)
    auth = {"cookies": [
        {"name": "a", "expires": far_future.timestamp()},
        {"name": "b", "expires": future.timestamp()},
    ]}
    f = tmp_path / "storage_state.json"
    f.write_text(json.dumps(auth))
    ts, days, iso_str = harvester.get_token_expiry(f)
    assert ts == int(future.timestamp())
    assert 9 <= days <= 10

def test_token_expiry_no_cookies(tmp_path):
    import harvester
    f = tmp_path / "storage_state.json"
    f.write_text(json.dumps({"cookies": []}))
    ts, days, _ = harvester.get_token_expiry(f)
    assert ts is None


# ── HTTP handlers ───────────────────────────────────────────────────────────

@pytest.fixture
def make_user(tmp_path):
    """Factory: returns a User-like object backed by tmp_path files."""
    import harvester
    def _make(name="default", email="", multi=False, history=None):
        episodes_dir = tmp_path / "episodes" / name if multi else tmp_path / "episodes"
        episodes_dir.mkdir(parents=True, exist_ok=True)
        history_file = tmp_path / f"{name}_history.json"
        if history:
            history_file.write_text(json.dumps(history))
        return harvester.User(
            name=name,
            email=email,
            auth_file=tmp_path / "storage_state.json",
            history_file=history_file,
            episodes_dir=episodes_dir,
            feed_file=tmp_path / "feed.xml",
            feed_token="testtoken",
        )
    return _make


@pytest.fixture
def aio_app():
    import harvester
    a = web.Application()
    a.router.add_get('/health', harvester.handle_health)
    a.router.add_get('/api/config', harvester.handle_config)
    a.router.add_get('/api/status', harvester.handle_status)
    a.router.add_get('/api/episodes', harvester.handle_episodes)
    a.router.add_post('/api/poll', harvester.handle_poll)
    a.router.add_post('/auth/upload', harvester.handle_auth_upload)
    return a


@pytest.mark.asyncio
async def test_health(aiohttp_client, aio_app):
    client = await aiohttp_client(aio_app)
    resp = await client.get('/health')
    assert resp.status == 200
    data = await resp.json()
    assert data['ok'] is True


@pytest.mark.asyncio
async def test_config_single_user(aiohttp_client, aio_app):
    import harvester
    with patch.object(harvester, 'GOOGLE_CLIENT_ID', ''), \
         patch.object(harvester, '_MULTI_USER', False):
        client = await aiohttp_client(aio_app)
        resp = await client.get('/api/config')
    assert resp.status == 200
    data = await resp.json()
    assert data['google_client_id'] is None
    assert data['multi_user'] is False


@pytest.mark.asyncio
async def test_config_multi_user(aiohttp_client, aio_app):
    import harvester
    with patch.object(harvester, 'GOOGLE_CLIENT_ID', 'test-id.apps.googleusercontent.com'), \
         patch.object(harvester, '_MULTI_USER', True):
        client = await aiohttp_client(aio_app)
        resp = await client.get('/api/config')
    assert resp.status == 200
    data = await resp.json()
    assert data['google_client_id'] == 'test-id.apps.googleusercontent.com'
    assert data['multi_user'] is True


@pytest.mark.asyncio
async def test_status_single_user(aiohttp_client, aio_app, make_user):
    import harvester
    user = make_user()
    with patch.object(harvester, 'GOOGLE_CLIENT_ID', ''), \
         patch.object(harvester, 'USERS_CONFIG', [user]), \
         patch.object(harvester, '_MULTI_USER', False), \
         patch.object(harvester, 'BASE_URL', 'http://localhost'):
        client = await aiohttp_client(aio_app)
        resp = await client.get('/api/status')
    assert resp.status == 200
    data = await resp.json()
    assert 'episodes' in data
    assert 'next_poll_in' in data
    assert data['feed_url'] == 'http://localhost/feed.xml'


@pytest.mark.asyncio
async def test_status_requires_auth_in_multi_user(aiohttp_client, aio_app, make_user):
    import harvester
    user = make_user(name='alice', email='alice@example.com', multi=True)
    with patch.object(harvester, 'GOOGLE_CLIENT_ID', 'test-client-id'), \
         patch.object(harvester, '_MULTI_USER', True), \
         patch.object(harvester, 'USERS_CONFIG', [user]):
        client = await aiohttp_client(aio_app)
        resp = await client.get('/api/status')
    assert resp.status == 401


@pytest.mark.asyncio
async def test_episodes_empty(aiohttp_client, aio_app, make_user):
    import harvester
    user = make_user()
    with patch.object(harvester, 'GOOGLE_CLIENT_ID', ''), \
         patch.object(harvester, 'USERS_CONFIG', [user]), \
         patch.object(harvester, 'BASE_URL', 'http://localhost'):
        client = await aiohttp_client(aio_app)
        resp = await client.get('/api/episodes')
    assert resp.status == 200
    assert await resp.json() == []


@pytest.mark.asyncio
async def test_episodes_single_user_url(aiohttp_client, aio_app, make_user):
    """Single-user: episode URL = /episodes/{filename}, no username subdir."""
    import harvester
    history = {"id1": {
        "title": "T", "created_at": "2026-01-01T00:00:00+00:00",
        "mp3_filename": "id1.m4a", "notebook": "NB",
    }}
    user = make_user(history=history)
    with patch.object(harvester, 'GOOGLE_CLIENT_ID', ''), \
         patch.object(harvester, '_MULTI_USER', False), \
         patch.object(harvester, 'USERS_CONFIG', [user]), \
         patch.object(harvester, 'BASE_URL', 'http://localhost'):
        client = await aiohttp_client(aio_app)
        resp = await client.get('/api/episodes')
    assert resp.status == 200
    episodes = await resp.json()
    assert len(episodes) == 1
    assert episodes[0]['url'] == 'http://localhost/episodes/id1.m4a'


@pytest.mark.asyncio
async def test_episodes_multi_user_url(aiohttp_client, aio_app, make_user):
    """Multi-user: episode URL includes username subdir to avoid 404."""
    import harvester
    history = {"id2": {
        "title": "T", "created_at": "2026-01-01T00:00:00+00:00",
        "mp3_filename": "id2.m4a", "notebook": "NB",
    }}
    user = make_user(name='laurent', multi=True, history=history)
    with patch.object(harvester, 'GOOGLE_CLIENT_ID', ''), \
         patch.object(harvester, '_MULTI_USER', True), \
         patch.object(harvester, 'USERS_CONFIG', [user]), \
         patch.object(harvester, 'BASE_URL', 'http://localhost'):
        client = await aiohttp_client(aio_app)
        resp = await client.get('/api/episodes')
    assert resp.status == 200
    episodes = await resp.json()
    assert len(episodes) == 1
    assert episodes[0]['url'] == 'http://localhost/episodes/laurent/id2.m4a'


@pytest.mark.asyncio
async def test_auth_upload_saves_file(aiohttp_client, aio_app, make_user, tmp_path):
    import harvester
    user = make_user()
    with patch.object(harvester, 'GOOGLE_CLIENT_ID', ''), \
         patch.object(harvester, 'USERS_CONFIG', [user]):
        client = await aiohttp_client(aio_app)
        data = aiohttp.FormData()
        data.add_field('file', b'{"cookies":[]}', filename='storage_state.json',
                       content_type='application/json')
        resp = await client.post('/auth/upload', data=data)
    assert resp.status == 200
    result = await resp.json()
    assert result['ok'] is True
    assert user.auth_file.exists()


@pytest.mark.asyncio
async def test_auth_upload_unauthorized(aiohttp_client, aio_app, make_user):
    import harvester
    user = make_user(name='alice', email='alice@example.com', multi=True)
    with patch.object(harvester, 'GOOGLE_CLIENT_ID', 'test-client-id'), \
         patch.object(harvester, '_MULTI_USER', True), \
         patch.object(harvester, 'USERS_CONFIG', [user]), \
         patch.object(harvester, 'BRIDGE_API_KEY', ''):
        client = await aiohttp_client(aio_app)
        data = aiohttp.FormData()
        data.add_field('file', b'{}', filename='storage_state.json')
        resp = await client.post('/auth/upload', data=data)
    assert resp.status == 401


@pytest.mark.asyncio
async def test_poll_triggers_event(aiohttp_client, aio_app, make_user):
    import harvester
    user = make_user()
    with patch.object(harvester, 'GOOGLE_CLIENT_ID', ''), \
         patch.object(harvester, 'USERS_CONFIG', [user]):
        client = await aiohttp_client(aio_app)
        resp = await client.post('/api/poll')
    assert resp.status == 200
    assert (await resp.json())['ok'] is True
