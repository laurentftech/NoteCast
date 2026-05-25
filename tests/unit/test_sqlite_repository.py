"""Unit tests for SQLiteJobRepository."""
from datetime import datetime
from pathlib import Path

import pytest

from notecast.core.models import Episode, Job, User
from notecast.infrastructure.database.sqlite_repository import SQLiteJobRepository


def _make_user(name="alice") -> User:
    return User(
        name=name,
        email="alice@example.com",
        auth_file=Path("/tmp/auth"),
        db_file=Path("/tmp/jobs.db"),
        history_file=Path("/tmp/history.json"),
        episodes_dir=Path("/tmp/episodes"),
        feed_dir=Path("/tmp/feed"),
        feed_token="tok",
    )


def _make_episode(url="https://example.com/ep.mp3") -> Episode:
    return Episode(
        url=url,
        title="Test Episode",
        feed_name="test-feed",
        feed_title="Test Feed",
        style="deep-dive",
    )


@pytest.fixture
def repo(tmp_path) -> SQLiteJobRepository:
    r = SQLiteJobRepository(tmp_path / "jobs.db")
    r.init(_make_user())
    return r


def test_get_job_returns_job_when_found(repo):
    user = _make_user()
    job = repo.create_job(user, _make_episode())
    found = repo.get_job(user, job.id)
    assert found is not None
    assert found.id == job.id
    assert found.title == "Test Episode"


def test_get_job_returns_none_when_not_found(repo):
    user = _make_user()
    assert repo.get_job(user, "nonexistent") is None


def test_get_job_returns_none_for_other_user(repo):
    alice = _make_user("alice")
    bob = _make_user("bob")
    job = repo.create_job(alice, _make_episode())
    assert repo.get_job(bob, job.id) is None


def test_delete_marks_status_deleted(repo):
    user = _make_user()
    job = repo.create_job(user, _make_episode())
    repo.update_job(user, job.id, status="deleted")
    found = repo.get_job(user, job.id)
    assert found is not None
    assert found.status == "deleted"


def test_deleted_job_excluded_from_done_list(repo):
    user = _make_user()
    job = repo.create_job(user, _make_episode())
    repo.update_job(user, job.id, status="done")
    repo.update_job(user, job.id, status="deleted")
    assert repo.get_all_done_jobs(user) == []


def test_episode_seen_still_true_after_delete(repo):
    user = _make_user()
    ep = _make_episode()
    job = repo.create_job(user, ep)
    repo.update_job(user, job.id, status="deleted")
    assert repo.episode_seen(user, ep.url) is True


def test_episode_seen_by_title_true_for_active_job(repo):
    user = _make_user()
    repo.create_job(user, _make_episode())
    assert repo.episode_seen_by_title(user, "Test Episode") is True


def test_episode_seen_by_title_false_when_only_deleted(repo):
    user = _make_user()
    job = repo.create_job(user, _make_episode())
    repo.update_job(user, job.id, status="deleted")
    assert repo.episode_seen_by_title(user, "Test Episode") is False


def test_episode_seen_by_title_false_for_unknown_title(repo):
    user = _make_user()
    repo.create_job(user, _make_episode())
    assert repo.episode_seen_by_title(user, "Other Title") is False


def test_episode_seen_by_any_url_matches_episode_url(repo):
    user = _make_user()
    repo.create_job(user, _make_episode(url="https://youtu.be/abc"))
    assert repo.episode_seen_by_any_url(user, "https://youtu.be/abc") is True


def test_episode_seen_by_any_url_matches_source_url(repo):
    user = _make_user()
    ep = Episode(
        url="https://cdn.example.com/ep.mp3",
        source_url="https://example.com/article",
        title="Src Episode",
        feed_name="test-feed",
        feed_title="Test Feed",
        style="deep-dive",
    )
    repo.create_job(user, ep)
    assert repo.episode_seen_by_any_url(user, "https://example.com/article") is True


def test_episode_seen_by_any_url_false_for_unknown(repo):
    user = _make_user()
    repo.create_job(user, _make_episode())
    assert repo.episode_seen_by_any_url(user, "https://nope.example/x") is False
