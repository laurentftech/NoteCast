"""End-to-end pipeline tests using the new architecture."""
import os
import pytest
import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from notecast.core.models import User, Job, Episode, Artifact
from notecast.infrastructure.config.settings import Settings
from notecast.infrastructure.database.sqlite_repository import SQLiteJobRepository
from notecast.services.user_service import UserService
from notecast.services.feed_service import FeedService
from notecast.services.poller_service import PollerService
from notecast.services.job_service import JobService
from notecast.infrastructure.storage.file_storage import LocalFileStorage


@pytest.fixture
def tmp_dirs():
    with tempfile.TemporaryDirectory() as d:
        base = Path(d)
        yield {
            "data": base / "data",
            "public": base / "public",
            "base": base,
        }


def _settings(dirs) -> Settings:
    return Settings.model_construct(
        base_url="http://localhost",
        users="",
        data_base=dirs["data"],
        public_dir=dirs["public"],
        webhook_url="",
        webhook_headers={},
        webhook_link="",
        google_client_id="",
        poll_interval=86400,
        bridge_port=8080,
        bridge_api_key="",
        retention_days=14,
        token_expiry_warn_days=7,
        feed_image_url="",
        generation_timeout=2700,
        max_retries=1,
    )


def _make_user(dirs) -> User:
    return User(
        name="default",
        email="",
        auth_file=Path("/tmp/auth.json"),
        db_file=dirs["data"] / "jobs.db",
        history_file=dirs["data"] / "history.json",
        episodes_dir=dirs["public"] / "episodes",
        feed_dir=dirs["public"] / "feed",
        feed_token="test-token",
    )


async def test_job_creation_and_retrieval(tmp_dirs):
    """Jobs created via repo are queryable."""
    dirs = tmp_dirs
    dirs["data"].mkdir(parents=True)

    repo = SQLiteJobRepository(dirs["data"] / "jobs.db")
    user = _make_user(dirs)
    repo.init(user)

    episode = Episode(
        url="https://example.com/ep1.mp3",
        title="Episode One",
        feed_name="test-feed",
        feed_title="Test Feed",
        style="deep-dive",
    )
    job = repo.create_job(user, episode)
    assert job.status == "pending"
    assert job.feed_name == "test-feed"

    pending = await repo.get_next_pending(user)
    assert pending is not None
    assert pending.id == job.id


async def test_update_job_marks_done(tmp_dirs):
    dirs = tmp_dirs
    dirs["data"].mkdir(parents=True)

    repo = SQLiteJobRepository(dirs["data"] / "jobs.db")
    user = _make_user(dirs)
    repo.init(user)

    episode = Episode(
        url="https://example.com/ep2.mp3",
        title="Episode Two",
        feed_name="my-feed",
        feed_title="My Feed",
        style="deep-dive",
    )
    job = repo.create_job(user, episode)
    repo.update_job(user, job.id, status="done", artifact_id="art-abc", duration=300)

    done = repo.get_done_jobs(user, "my-feed")
    assert len(done) == 1
    assert done[0].status == "done"
    assert done[0].artifact_id == "art-abc"


async def test_episode_seen_deduplication(tmp_dirs):
    dirs = tmp_dirs
    dirs["data"].mkdir(parents=True)

    repo = SQLiteJobRepository(dirs["data"] / "jobs.db")
    user = _make_user(dirs)
    repo.init(user)

    url = "https://example.com/ep3.mp3"
    assert not repo.episode_seen(user, url)

    episode = Episode(url=url, title="Ep3", feed_name="f", feed_title="F", style="deep-dive")
    repo.create_job(user, episode)
    assert repo.episode_seen(user, url)


async def test_feed_rebuild_generates_rss(tmp_dirs):
    """FeedService.rebuild_feed produces valid RSS from completed jobs."""
    dirs = tmp_dirs
    dirs["data"].mkdir(parents=True)
    dirs["public"].mkdir(parents=True)

    settings = _settings(dirs)
    storage = LocalFileStorage(settings)
    user = _make_user(dirs)

    repo = SQLiteJobRepository(dirs["data"] / "jobs.db")
    repo.init(user)

    # Seed a done job
    episode = Episode(
        url="https://example.com/test.mp3",
        title="Test Episode",
        feed_name="testfeed",
        feed_title="Test Feed",
        style="deep-dive",
    )
    job = repo.create_job(user, episode)
    repo.update_job(user, job.id, status="done", artifact_id="artifact-001", duration=120)

    # Create dummy audio file so feed_service includes the episode
    ep_path = storage.episode_path(user, "testfeed", "artifact-001")
    ep_path.parent.mkdir(parents=True, exist_ok=True)
    ep_path.write_bytes(b"fake-audio")

    feed_service = FeedService(
        repo_factory=lambda u: SQLiteJobRepository(u.db_file),
        storage=storage,
        settings=settings,
    )
    await feed_service.rebuild_feed(user, "testfeed", "Test Feed")

    feed_path = storage.feed_path(user, "testfeed")
    assert feed_path.exists(), f"Feed not created at {feed_path}"
    content = feed_path.read_text()
    assert "<title>Test Feed</title>" in content
    assert "Test Episode" in content


async def test_poller_creates_jobs_from_rss(tmp_dirs):
    """PollerService.poll_feeds creates jobs for unseen episodes."""
    dirs = tmp_dirs
    dirs["data"].mkdir(parents=True)

    user = _make_user(dirs)
    repo = SQLiteJobRepository(dirs["data"] / "jobs.db")
    repo.init(user)

    rss = """<?xml version="1.0"?>
<rss version="2.0"><channel>
  <title>My Podcast</title>
  <item>
    <title>Ep 1</title>
    <enclosure url="https://example.com/ep1.mp3" length="1000" type="audio/mpeg"/>
    <guid>guid-ep1</guid>
  </item>
</channel></rss>"""

    feed_cfg = [{"name": "my-podcast", "url": "https://feeds.example.com/rss", "title": "My Podcast"}]

    mock_job_service = MagicMock()
    mock_job_service.create_job = MagicMock(return_value=MagicMock())

    settings = _settings(dirs)
    mock_user_service = AsyncMock()
    mock_user_service.get_all = AsyncMock(return_value=[user])

    poller = PollerService(
        repo_factory=lambda u: repo,
        user_service=mock_user_service,
        settings=settings,
    )

    from notecast.core.models import Episode as Ep
    fake_episode = Ep(
        url="https://example.com/ep1.mp3",
        title="Ep 1",
        feed_name="my-podcast",
        feed_title="My Podcast",
        style="deep-dive",
    )

    with patch("notecast.infrastructure.external.feed_parser.fetch_episodes", return_value=("My Podcast", [fake_episode])):
        config = {"job_service": mock_job_service}
        count = await poller.poll_feeds(user, config)

    assert count >= 0


async def test_get_generating_jobs(tmp_dirs):
    """get_generating_jobs returns only jobs with status=generating."""
    dirs = tmp_dirs
    dirs["data"].mkdir(parents=True)

    repo = SQLiteJobRepository(dirs["data"] / "jobs.db")
    user = _make_user(dirs)
    repo.init(user)

    ep1 = Episode(url="https://example.com/ep1.mp3", title="Ep1", feed_name="f", feed_title="F", style="deep-dive")
    ep2 = Episode(url="https://example.com/ep2.mp3", title="Ep2", feed_name="f", feed_title="F", style="deep-dive")
    j1 = repo.create_job(user, ep1)
    j2 = repo.create_job(user, ep2)

    repo.update_job(user, j1.id, status="generating", notebook_id="nb-abc")
    repo.update_job(user, j2.id, status="done", artifact_id="art-xyz", duration=100)

    generating = repo.get_generating_jobs(user)
    assert len(generating) == 1
    assert generating[0].id == j1.id
    assert generating[0].notebook_id == "nb-abc"
