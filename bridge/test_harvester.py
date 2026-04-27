import json
import pytest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch


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
    with patch.object(harvester, "EPISODES_DIR", tmp_path), \
         patch.object(harvester, "RETENTION_DAYS", 14):
        result = harvester.purge_old_episodes(history)
    assert "a1" in result
    assert mp3.exists()

def test_purge_removes_old_episode(tmp_path):
    import harvester
    mp3 = tmp_path / "old.m4a"
    mp3.write_bytes(b"")
    history = {"a2": make_entry(downloaded_at=iso(days_ago(20)), mp3_filename="old.m4a")}
    with patch.object(harvester, "EPISODES_DIR", tmp_path), \
         patch.object(harvester, "RETENTION_DAYS", 14):
        result = harvester.purge_old_episodes(history)
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
    with patch.object(harvester, "EPISODES_DIR", tmp_path), \
         patch.object(harvester, "RETENTION_DAYS", 14):
        result = harvester.purge_old_episodes(history)
    assert "a3" in result, "Episode downloaded recently must not be purged"

def test_purge_falls_back_to_created_at(tmp_path):
    import harvester
    mp3 = tmp_path / "old2.m4a"
    mp3.write_bytes(b"")
    history = {"a4": make_entry(created_at=iso(days_ago(20)), mp3_filename="old2.m4a")}
    with patch.object(harvester, "EPISODES_DIR", tmp_path), \
         patch.object(harvester, "RETENTION_DAYS", 14):
        result = harvester.purge_old_episodes(history)
    assert "a4" not in result


# ── recover_history_from_disk ───────────────────────────────────────────────

def test_recover_adds_untracked_mp3(tmp_path):
    import harvester
    mp3 = tmp_path / "abc12345.mp3"
    mp3.write_bytes(b"")
    history = {}
    with patch.object(harvester, "EPISODES_DIR", tmp_path):
        harvester.recover_history_from_disk(history)
    assert "abc12345" in history
    assert history["abc12345"]["mp3_filename"] == "abc12345.mp3"

def test_recover_skips_already_tracked(tmp_path):
    import harvester
    mp3 = tmp_path / "known.mp3"
    mp3.write_bytes(b"")
    history = {"x": {"mp3_filename": "known.mp3", "title": "T", "created_at": "", "notebook": ""}}
    with patch.object(harvester, "EPISODES_DIR", tmp_path):
        harvester.recover_history_from_disk(history)
    assert len(history) == 1  # no duplicate added


# ── get_token_expiry ────────────────────────────────────────────────────────

def test_token_expiry_missing_file(tmp_path):
    import harvester
    with patch.object(harvester, "AUTH_FILE", tmp_path / "missing.json"):
        ts, days, iso_str = harvester.get_token_expiry()
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
    with patch.object(harvester, "AUTH_FILE", f):
        ts, days, iso_str = harvester.get_token_expiry()
    assert ts == int(future.timestamp())
    assert 9 <= days <= 10

def test_token_expiry_no_cookies(tmp_path):
    import harvester
    f = tmp_path / "storage_state.json"
    f.write_text(json.dumps({"cookies": []}))
    with patch.object(harvester, "AUTH_FILE", f):
        ts, days, _ = harvester.get_token_expiry()
    assert ts is None
