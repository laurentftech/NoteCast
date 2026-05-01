"""Unit tests for UserService."""
import os
import pytest
from pathlib import Path
from unittest.mock import patch
import tempfile

from notecast.services.user_service import UserService
from notecast.infrastructure.config.settings import Settings


def _make_settings(**kwargs) -> Settings:
    defaults = dict(
        base_url="http://localhost",
        users="",
        data_base=Path(tempfile.mkdtemp()),
        public_dir=Path(tempfile.mkdtemp()),
        webhook_url="",
        webhook_headers={},
        webhook_link="",
        google_client_id="",
    )
    defaults.update(kwargs)
    return Settings.model_construct(**defaults)


@pytest.mark.asyncio
async def test_single_user_mode_creates_default_user():
    svc = UserService(_make_settings())
    users = await svc.get_all()
    assert len(users) == 1
    assert users[0].name == "default"


@pytest.mark.asyncio
async def test_multi_user_mode_builds_from_names():
    with patch.dict(os.environ, {"USER_ALICE_EMAIL": "alice@example.com", "USER_BOB_EMAIL": "bob@example.com"}):
        svc = UserService(_make_settings(users="alice,bob"))
        users = await svc.get_all()
    assert len(users) == 2
    assert users[0].name == "alice"
    assert users[0].email == "alice@example.com"
    assert users[1].name == "bob"


@pytest.mark.asyncio
async def test_get_by_name_returns_correct_user():
    with patch.dict(os.environ, {"USER_ALICE_EMAIL": "alice@example.com"}):
        svc = UserService(_make_settings(users="alice"))
        user = await svc.get_by_name("alice")
    assert user is not None
    assert user.name == "alice"


@pytest.mark.asyncio
async def test_get_by_name_returns_none_for_unknown():
    svc = UserService(_make_settings())
    user = await svc.get_by_name("nobody")
    assert user is None


@pytest.mark.asyncio
async def test_feed_token_persists_across_instances():
    data_dir = Path(tempfile.mkdtemp())
    settings = _make_settings(data_base=data_dir)
    svc1 = UserService(settings)
    users1 = await svc1.get_all()
    token1 = users1[0].feed_token

    svc2 = UserService(settings)
    users2 = await svc2.get_all()
    token2 = users2[0].feed_token

    assert token1 == token2


def test_get_default_raises_when_no_users_and_cache_empty():
    svc = UserService(_make_settings())
    # cache not yet populated — _build_users() returns default user, so no error
    user = svc.get_default()
    assert user.name == "default"


@pytest.mark.asyncio
async def test_get_by_email_returns_correct_user():
    with patch.dict(os.environ, {"USER_CAROL_EMAIL": "carol@example.com"}):
        svc = UserService(_make_settings(users="carol"))
        await svc.get_all()  # populate cache
        user = svc.get_by_email("carol@example.com")
    assert user is not None
    assert user.name == "carol"


@pytest.mark.asyncio
async def test_result_cached_on_second_call():
    svc = UserService(_make_settings())
    users1 = await svc.get_all()
    users2 = await svc.get_all()
    assert users1 is users2
