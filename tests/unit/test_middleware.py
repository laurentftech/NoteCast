"""Unit tests for auth middleware."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from pathlib import Path

from notecast.api.http.middleware import _validate_token, _verify_google_id_token
from notecast.core.models import User


def _make_user(name="alice", feed_token="tok123", email="alice@example.com") -> User:
    return User(
        name=name,
        email=email,
        auth_file=Path("/tmp/auth"),
        db_file=Path("/tmp/jobs.db"),
        history_file=Path("/tmp/history.json"),
        episodes_dir=Path("/tmp/episodes"),
        feed_dir=Path("/tmp/feed"),
        feed_token=feed_token,
    )


@pytest.mark.asyncio
async def test_validate_feed_token_matches():
    user = _make_user(feed_token="secret")
    svc = AsyncMock()
    svc.get_all = AsyncMock(return_value=[user])
    result = await _validate_token("secret", svc, "")
    assert result is user


@pytest.mark.asyncio
async def test_validate_wrong_feed_token_returns_none():
    user = _make_user(feed_token="secret")
    svc = AsyncMock()
    svc.get_all = AsyncMock(return_value=[user])
    result = await _validate_token("wrong", svc, "")
    assert result is None


@pytest.mark.asyncio
async def test_validate_google_token_falls_through_to_email_lookup():
    user = _make_user(email="alice@example.com")
    svc = AsyncMock()
    svc.get_all = AsyncMock(return_value=[])  # no feed token match
    svc.get_by_email = MagicMock(return_value=user)

    with patch("notecast.api.http.middleware._verify_google_id_token", return_value="alice@example.com"):
        result = await _validate_token("google-jwt", svc, "client-id")

    svc.get_by_email.assert_called_once_with("alice@example.com")
    assert result is user


@pytest.mark.asyncio
async def test_validate_google_token_skipped_when_no_client_id():
    svc = AsyncMock()
    svc.get_all = AsyncMock(return_value=[])
    result = await _validate_token("google-jwt", svc, "")
    assert result is None


def test_verify_google_id_token_returns_none_on_error():
    with patch("google.oauth2.id_token.verify_oauth2_token", side_effect=ValueError("bad")):
        result = _verify_google_id_token("bad-token", "client-id")
    assert result is None
