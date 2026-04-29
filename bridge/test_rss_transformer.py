"""Tests for rss_transformer.py - RSS-to-NotebookLM audio pipeline."""

import json
import os
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest
import yaml
import feedparser

# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def tmp_data_dir(tmp_path):
    """Create isolated /data and /public directories for testing."""
    data_dir = tmp_path / "data"
    public_dir = tmp_path / "public"
    data_dir.mkdir()
    public_dir.mkdir()

    # Patch environment to use temp directories
    with patch.dict(
        os.environ,
        {
            "TRANSFORMER_CONFIG": str(data_dir / "transformer.yaml"),
            "BASE_URL": "http://localhost",
            "USERS": "",
        },
    ):
        # Force reimport to pick up new env vars
        import importlib

        import rss_transformer

        importlib.reload(rss_transformer)
        yield tmp_path, data_dir, public_dir
        importlib.reload(rss_transformer)


@pytest.fixture
def user_factory(tmp_path):
    """Create test User objects."""

    def _make(name="testuser", auth_file=None, multi_user=False):
        data_dir = tmp_path / "data" / name
        public_dir = tmp_path / "public"
        data_dir.mkdir(parents=True, exist_ok=True)

        auth_file = auth_file or (data_dir / "storage_state.json")
        db_file = data_dir / "transformer.db"

        from rss_transformer import User

        return User(
            name=name,
            auth_file=Path(str(auth_file)),
            db_file=Path(str(db_file)),
            episodes_dir=public_dir / "episodes" / name,
            feed_dir=public_dir / "feed" / name,
            feed_token="test_token_123",
        )

    return _make


@pytest.fixture
def config_file(tmp_path):
    """Create a test transformer.yaml config file."""
    config = {
        "poll_interval_minutes": 60,
        "notebooklm": {
            "default_style": "deep-dive",
            "instructions": "Create a concise summary",
        },
        "rss_feeds": [
            {
                "name": "test-feed",
                "url": "http://example.com/feed.xml",
                "title": "Test Feed",
            },
            {"name": "second-feed", "url": "http://example.com/feed2.xml"},
        ],
    }
    config_path = tmp_path / "transformer.yaml"
    config_path.write_text(yaml.dump(config))
    return config_path


# ── User Model Tests ────────────────────────────────────────────────────────


class TestBuildUsers:
    """Tests for _build_users() and User model."""

    def test_single_user_default_when_users_empty(self, tmp_path, monkeypatch):
        """When USERS is empty, returns single default user."""
        monkeypatch.setenv("USERS", "")
        monkeypatch.setenv("TRANSFORMER_CONFIG", str(tmp_path / "transformer.yaml"))

        import importlib

        import rss_transformer

        importlib.reload(rss_transformer)

        users = rss_transformer._build_users()
        importlib.reload(rss_transformer)

        assert len(users) == 1
        assert users[0].name == "default"
        assert str(users[0].db_file).endswith("transformer.db")

    def test_multi_user_from_env(self, tmp_path, monkeypatch):
        """When USERS has comma-separated names, creates user per name."""
        monkeypatch.setenv("USERS", "alice,bob")
        monkeypatch.setenv("TRANSFORMER_CONFIG", str(tmp_path / "transformer.yaml"))

        import importlib

        import rss_transformer

        importlib.reload(rss_transformer)

        users = rss_transformer._build_users()
        importlib.reload(rss_transformer)

        assert len(users) == 2
        names = {u.name for u in users}
        assert names == {"alice", "bob"}

        alice = next(u for u in users if u.name == "alice")
        assert "alice" in str(alice.episodes_dir)
        assert "alice" in str(alice.feed_dir)

    def test_multi_user_flag_set_correctly(self, tmp_path, monkeypatch):
        """_MULTI_USER flag reflects USERS env var."""
        monkeypatch.setenv("USERS", "")
        monkeypatch.setenv("TRANSFORMER_CONFIG", str(tmp_path / "transformer.yaml"))

        import importlib

        import rss_transformer

        importlib.reload(rss_transformer)

        assert rss_transformer._MULTI_USER is False
        importlib.reload(rss_transformer)

        monkeypatch.setenv("USERS", "alice,bob")
        importlib.reload(rss_transformer)
        assert rss_transformer._MULTI_USER is True
        importlib.reload(rss_transformer)


# ── Database Tests ──────────────────────────────────────────────────────────


class TestDatabase:
    """Tests for database functions."""

    def test_init_db_creates_tables(self, user_factory):
        """init_db creates jobs table with correct schema."""
        user = user_factory()

        from rss_transformer import init_db

        init_db(user)

        with sqlite3.connect(str(user.db_file)) as conn:
            cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = [row[0] for row in cursor.fetchall()]
            assert "jobs" in tables

            # Check columns
            cursor = conn.execute("PRAGMA table_info(jobs)")
            columns = [row[1] for row in cursor.fetchall()]
            assert "id" in columns
            assert "user_name" in columns
            assert "feed_name" in columns
            assert "episode_url" in columns
            assert "title" in columns
            assert "status" in columns

    def test_init_db_creates_indexes(self, user_factory):
        """init_db creates required indexes."""
        user = user_factory()

        from rss_transformer import init_db

        init_db(user)

        with sqlite3.connect(str(user.db_file)) as conn:
            cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='index'")
            indexes = [row[0] for row in cursor.fetchall()]
            assert "idx_status" in indexes
            assert "idx_feed" in indexes

    def test_create_job(self, user_factory):
        """create_job inserts a new job record."""
        user = user_factory()

        from rss_transformer import create_job, init_db

        init_db(user)

        job_id = create_job(
            user=user,
            feed_name="test-feed",
            feed_title="Test Feed",
            episode_url="http://example.com/ep1",
            title="Episode 1",
            style="deep-dive",
        )

        assert job_id is not None
        with sqlite3.connect(str(user.db_file)) as conn:
            conn.row_factory = sqlite3.Row
            job = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            assert job is not None
            assert job["user_name"] == "testuser"
            assert job["feed_name"] == "test-feed"
            assert job["episode_url"] == "http://example.com/ep1"
            assert job["status"] == "pending"

    def test_episode_seen_false_when_not_exists(self, user_factory):
        """episode_seen returns False for unseen episodes."""
        user = user_factory()

        from rss_transformer import episode_seen, init_db

        init_db(user)

        assert episode_seen(user, "http://example.com/ep1") is False

    def test_episode_seen_true_when_exists(self, user_factory):
        """episode_seen returns True for seen episodes."""
        user = user_factory()

        from rss_transformer import create_job, episode_seen, init_db

        init_db(user)
        create_job(user, "feed", "Title", "http://example.com/ep1", "Ep 1", "deep-dive")

        assert episode_seen(user, "http://example.com/ep1") is True

    def test_get_next_pending_returns_oldest(self, user_factory):
        """get_next_pending returns oldest pending job."""
        user = user_factory()

        from rss_transformer import create_job, get_next_pending, init_db

        init_db(user)

        create_job(
            user, "feed1", "Title1", "http://example.com/ep1", "Ep 1", "deep-dive"
        )
        create_job(
            user, "feed2", "Title2", "http://example.com/ep2", "Ep 2", "deep-dive"
        )

        job = get_next_pending(user)
        assert job is not None
        assert job["episode_url"] == "http://example.com/ep1"

    def test_get_next_pending_returns_none_when_empty(self, user_factory):
        """get_next_pending returns None when no pending jobs."""
        user = user_factory()

        from rss_transformer import create_job, get_next_pending, init_db

        init_db(user)

        # Create and complete a job
        job_id = create_job(
            user, "feed", "Title", "http://example.com/ep1", "Ep 1", "deep-dive"
        )
        from rss_transformer import update_job

        update_job(user, job_id, status="done")

        assert get_next_pending(user) is None

    def test_update_job(self, user_factory):
        """update_job updates job fields."""
        user = user_factory()

        from rss_transformer import create_job, get_next_pending, init_db, update_job

        init_db(user)

        job_id = create_job(
            user, "feed", "Title", "http://example.com/ep1", "Ep 1", "deep-dive"
        )

        update_job(user, job_id, status="processing", notebook_id="nb123")

        job = get_next_pending(user)
        assert job is None  # No longer pending

        with sqlite3.connect(str(user.db_file)) as conn:
            conn.row_factory = sqlite3.Row
            updated = conn.execute(
                "SELECT * FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()
            assert updated["status"] == "processing"
            assert updated["notebook_id"] == "nb123"

    def test_get_done_jobs(self, user_factory):
        """get_done_jobs returns completed jobs for feed."""
        user = user_factory()

        from rss_transformer import create_job, get_done_jobs, init_db, update_job

        init_db(user)

        # Create jobs for different feeds
        job1 = create_job(
            user, "feed1", "Feed 1", "http://example.com/ep1", "Ep 1", "deep-dive"
        )
        job2 = create_job(
            user, "feed1", "Feed 1", "http://example.com/ep2", "Ep 2", "deep-dive"
        )
        job3 = create_job(
            user, "feed2", "Feed 2", "http://example.com/ep3", "Ep 3", "deep-dive"
        )

        update_job(user, job1, status="done")
        update_job(user, job2, status="done")
        update_job(user, job3, status="done")

        done = get_done_jobs(user, "feed1")
        assert len(done) == 2
        urls = {j["episode_url"] for j in done}
        assert urls == {"http://example.com/ep1", "http://example.com/ep2"}


# ── Config Loader Tests ─────────────────────────────────────────────────────


class TestConfigLoader:
    """Tests for load_config()."""

    def test_load_config_from_file(self, config_file):
        """load_config reads and parses YAML file."""
        import rss_transformer
        from rss_transformer import load_config

        with patch.object(rss_transformer, "CONFIG_PATH", config_file):
            config = load_config()

        assert config["poll_interval_minutes"] == 60
        assert config["notebooklm"]["default_style"] == "deep-dive"
        assert len(config["rss_feeds"]) == 2

    def test_load_config_returns_defaults_when_missing(self, tmp_path):
        """load_config returns defaults when file doesn't exist."""
        non_existent = tmp_path / "nonexistent.yaml"

        import rss_transformer
        from rss_transformer import load_config

        with patch.object(rss_transformer, "CONFIG_PATH", non_existent):
            config = load_config()

        assert config["rss_feeds"] == []
        assert config["poll_interval_minutes"] == 30


# ── RSS Poller Tests ────────────────────────────────────────────────────────


class TestRssPoller:
    """Tests for fetch_episodes and poll_feeds."""

    def test_user_has_configured_feeds_only_for_user_with_entries(self, user_factory):
        user_with_feed = user_factory(name="laurent")
        user_without_feed = user_factory(name="armelle")

        from rss_transformer import _user_has_configured_feeds

        config = {
            "rss_feeds": {
                "laurent": [
                    {"name": "latent-space", "url": "https://example.com/laurent.xml"},
                ],
                "armelle": [],
            }
        }

        assert _user_has_configured_feeds(config, user_with_feed) is True
        assert _user_has_configured_feeds(config, user_without_feed) is False

    def test_user_has_configured_feeds_ignores_invalid_entries(self, user_factory):
        user = user_factory(name="armelle")

        from rss_transformer import _user_has_configured_feeds

        config = {
            "rss_feeds": {
                "armelle": [
                    {"name": "missing-url"},
                    {"url": "https://example.com/no-name.xml"},
                ]
            }
        }

        assert _user_has_configured_feeds(config, user) is False

    def test_fetch_episodes_success(self, tmp_path):
        """fetch_episodes parses RSS feed correctly."""
        # Create a mock RSS feed XML
        rss_xml = """<?xml version="1.0"?>
        <rss version="2.0">
            <channel>
                <title>Test Feed</title>
                <item>
                    <title>Episode 1</title>
                    <link>http://example.com/ep1</link>
                    <enclosure url="http://example.com/audio1.mp3"/>
                </item>
                <item>
                    <title>Episode 2</title>
                    <link>http://example.com/ep2</link>
                    <enclosure url="http://example.com/audio2.mp3"/>
                </item>
            </channel>
        </rss>"""

        # Test the feedparser-based parsing directly
        feed = feedparser.parse(rss_xml)
        f_title = getattr(feed.feed, "title", "")
        results = []
        for entry in feed.entries:
            ep_url = None
            if getattr(entry, "enclosures", None):
                ep_url = entry.enclosures[0].get("url")
            if not ep_url:
                ep_url = getattr(entry, "link", None)
            if ep_url:
                results.append({
                    "url": ep_url,
                    "title": getattr(entry, "title", ""),
                })

        assert f_title == "Test Feed"
        assert len(results) == 2
        assert results[0]["title"] == "Episode 1"
        assert results[0]["url"] == "http://example.com/audio1.mp3"

    @pytest.mark.asyncio
    async def test_poll_feeds_creates_jobs(self, user_factory, config_file):
        """poll_feeds creates jobs for new episodes."""
        user = user_factory()

        from rss_transformer import init_db, poll_feeds

        init_db(user)

        # Mock fetch_episodes
        async def mock_fetch(session, url):
            return "Test Feed", [
                {"url": "http://example.com/ep1.mp3", "title": "Episode 1"},
                {"url": "http://example.com/ep2.mp3", "title": "Episode 2"},
            ]

        config = {
            "rss_feeds": [
                {
                    "name": "test-feed",
                    "url": "http://test.com/feed.xml",
                    "title": "Test Feed",
                },
            ],
            "notebooklm": {},
        }

        with patch("rss_transformer.fetch_episodes", mock_fetch):
            await poll_feeds(user, config)

        # Check jobs were created
        with sqlite3.connect(str(user.db_file)) as conn:
            cursor = conn.execute("SELECT COUNT(*) FROM jobs WHERE status = 'pending'")
            count = cursor.fetchone()[0]
            assert count == 2

    @pytest.mark.asyncio
    async def test_poll_feeds_skips_seen_episodes(self, user_factory, config_file):
        """poll_feeds doesn't create duplicate jobs for seen episodes."""
        user = user_factory()

        from rss_transformer import create_job, episode_seen, init_db, poll_feeds

        init_db(user)

        # Pre-create a job for one episode
        create_job(
            user,
            "test-feed",
            "Test Feed",
            "http://example.com/ep1.mp3",
            "Episode 1",
            "deep-dive",
        )
        assert episode_seen(user, "http://example.com/ep1.mp3") is True

        async def mock_fetch(session, url):
            return "Test Feed", [
                {"url": "http://example.com/ep1.mp3", "title": "Episode 1"},
                {"url": "http://example.com/ep2.mp3", "title": "Episode 2"},
            ]

        config = {
            "rss_feeds": [{"name": "test-feed", "url": "http://test.com/feed.xml"},],
            "notebooklm": {},
        }

        with patch("rss_transformer.fetch_episodes", mock_fetch):
            await poll_feeds(user, config)

        # Only ep2 should be added
        with sqlite3.connect(str(user.db_file)) as conn:
            cursor = conn.execute("SELECT COUNT(*) FROM jobs")
            count = cursor.fetchone()[0]
            assert count == 2  # ep1 was pre-created, ep2 was added

    @pytest.mark.asyncio
    async def test_poll_feeds_uses_user_specific_feeds(self, user_factory):
        """When rss_feeds is keyed by user, only that user's feeds are polled."""
        user = user_factory(name="alice")

        from rss_transformer import init_db, poll_feeds

        init_db(user)

        async def mock_fetch(session, url):
            assert url == "http://alice.com/feed.xml"
            return "Alice Feed", [{"url": "http://alice.com/ep1.mp3", "title": "Alice Ep 1"}]

        config = {
            "rss_feeds": {
                "alice": [{"name": "alice-feed", "url": "http://alice.com/feed.xml"}],
                "bob": [{"name": "bob-feed", "url": "http://bob.com/feed.xml"}],
            },
            "notebooklm": {},
        }

        with patch("rss_transformer.fetch_episodes", mock_fetch):
            await poll_feeds(user, config)

        with sqlite3.connect(str(user.db_file)) as conn:
            conn.row_factory = sqlite3.Row
            jobs = conn.execute("SELECT feed_name, episode_url FROM jobs").fetchall()
            assert len(jobs) == 1
            assert jobs[0]["feed_name"] == "alice-feed"
            assert jobs[0]["episode_url"] == "http://alice.com/ep1.mp3"


# ── Feed Builder Tests ──────────────────────────────────────────────────────


class TestFeedBuilder:
    """Tests for rebuild_feed."""

    def test_rebuild_feed_creates_xml(self, user_factory, monkeypatch):
        """rebuild_feed generates RSS XML file."""
        monkeypatch.setenv("BASE_URL", "http://localhost")
        user = user_factory()

        from rss_transformer import create_job, init_db, rebuild_feed, update_job

        init_db(user)

        # Create a completed job
        job_id = create_job(
            user,
            "test-feed",
            "Test Feed",
            "http://example.com/ep1",
            "Episode 1",
            "deep-dive",
        )
        update_job(user, job_id, status="done", artifact_id="art123")

        # Create the episodes directory and dummy file
        episodes_dir = user.episodes_dir / "test-feed"
        episodes_dir.mkdir(parents=True, exist_ok=True)
        (episodes_dir / "art123.m4a").write_bytes(b"dummy audio")

        rebuild_feed(user, "test-feed", "Test Feed")

        feed_file = user.feed_dir / "test-feed.xml"
        assert feed_file.exists()
        content = feed_file.read_text()
        assert "<title>Episode 1</title>" in content

    def test_rebuild_feed_multi_user_paths(self, user_factory, monkeypatch):
        """Multi-user: feed paths include user name."""
        monkeypatch.setenv("BASE_URL", "http://localhost")
        monkeypatch.setenv("_MULTI_USER", "True")

        user = user_factory(name="alice")

        from rss_transformer import create_job, init_db, rebuild_feed, update_job

        init_db(user)

        job_id = create_job(
            user,
            "test-feed",
            "Test Feed",
            "http://example.com/ep1",
            "Episode 1",
            "deep-dive",
        )
        update_job(user, job_id, status="done", artifact_id="art123")

        episodes_dir = user.episodes_dir / "test-feed"
        episodes_dir.mkdir(parents=True, exist_ok=True)
        (episodes_dir / "art123.m4a").write_bytes(b"dummy audio")

        rebuild_feed(user, "test-feed", "Test Feed")

        feed_file = user.feed_dir / "test-feed.xml"
        assert feed_file.exists()
        content = feed_file.read_text()
        # In multi-user mode, URL should include username
        assert "alice/test-feed" in content


# ── Helper Tests ─────────────────────────────────────────────────────────────


class TestHelpers:
    """Tests for helper functions."""

    def test_get_duration(self, tmp_path):
        """_get_duration extracts duration from media file."""
        # Create a minimal valid MP4/MP3 file would be complex
        # Instead, mock the subprocess call
        from rss_transformer import _get_duration

        mock_result = MagicMock()
        mock_result.stdout = "120.5\n"
        mock_result.stderr = ""
        mock_result.returncode = 0

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            duration = _get_duration(Path("/fake/path.m4a"))
            assert duration == 120

            # Verify ffprobe command
            call_args = mock_run.call_args[0][0]
            assert "ffprobe" in call_args
            assert "duration" in " ".join(call_args)

    def test_get_duration_returns_none_on_error(self, tmp_path):
        """_get_duration returns None on ffprobe error."""
        from rss_transformer import _get_duration

        mock_result = MagicMock()
        mock_result.stdout = ""
        mock_result.stderr = "Error"
        mock_result.returncode = 1

        with patch("subprocess.run", side_effect=Exception("ffprobe failed")):
            duration = _get_duration(Path("/fake/path.m4a"))
            assert duration is None


# ── Style Validation Tests ──────────────────────────────────────────────────


class TestStyleValidation:
    """Tests for style validation in poll_feeds."""

    @pytest.mark.asyncio
    async def test_poll_feeds_uses_default_style_for_invalid(self, user_factory):
        """Invalid style falls back to default."""
        user = user_factory()

        from rss_transformer import init_db, poll_feeds

        init_db(user)

        async def mock_fetch(session, url):
            return "Test Feed", [{"url": "http://example.com/ep1", "title": "Ep 1"}]

        config = {
            "rss_feeds": [
                {
                    "name": "f",
                    "url": "http://test.com/feed.xml",
                    "style": "invalid-style",
                }
            ],
            "notebooklm": {"default_style": "brief"},
        }

        with patch("rss_transformer.fetch_episodes", mock_fetch):
            await poll_feeds(user, config)

        with sqlite3.connect(str(user.db_file)) as conn:
            # Columns: 0:id, 1:user_name, 2:feed_name, 3:feed_title, 4:episode_url, 5:title, 6:status, 7:style
            job = conn.execute("SELECT * FROM jobs").fetchone()
            assert job[7] == "brief"  # style column

    @pytest.mark.asyncio
    async def test_poll_feeds_uses_feed_level_default(self, user_factory):
        """Feed without style uses notebooklm default_style."""
        user = user_factory()

        from rss_transformer import init_db, poll_feeds

        init_db(user)

        async def mock_fetch(session, url):
            return "Test Feed", [{"url": "http://example.com/ep1", "title": "Ep 1"}]

        config = {
            "rss_feeds": [{"name": "f", "url": "http://test.com/feed.xml"},],  # no style
            "notebooklm": {"default_style": "critique"},
        }

        with patch("rss_transformer.fetch_episodes", mock_fetch):
            await poll_feeds(user, config)

        with sqlite3.connect(str(user.db_file)) as conn:
            job = conn.execute("SELECT * FROM jobs").fetchone()
            assert job[7] == "critique"  # style column


# ── Worker Retry Behavior Tests ─────────────────────────────────────────────


class TestWorkerRetryBehavior:
    @pytest.mark.asyncio
    async def test_process_job_keeps_pending_when_auth_storage_missing(self, user_factory):
        user = user_factory(name="alice")

        from rss_transformer import create_job, init_db, process_job

        init_db(user)
        job_id = create_job(
            user,
            "feed-a",
            "Feed A",
            "http://example.com/ep1",
            "Episode 1",
            "deep-dive",
        )

        with sqlite3.connect(str(user.db_file)) as conn:
            conn.row_factory = sqlite3.Row
            job = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()

        with patch(
            "rss_transformer.NotebookLMClient.from_storage",
            side_effect=Exception("Storage file not found: /root/.notebooklm/alice/storage_state.json"),
        ):
            await process_job(user, dict(job), {"notebooklm": {}})

        with sqlite3.connect(str(user.db_file)) as conn:
            conn.row_factory = sqlite3.Row
            updated = conn.execute("SELECT status, retries FROM jobs WHERE id=?", (job_id,)).fetchone()
            assert updated["status"] == "pending"
            assert updated["retries"] == 0