#!/usr/bin/env python3
"""
End-to-end test for RSS transformation using the provided Who_am_I.mp3 file.
"""

import json
import os
import tempfile
import yaml
from pathlib import Path
from unittest.mock import patch, MagicMock
import asyncio
import sqlite3

# Import the modules we need to test
import sys
sys.path.insert(0, 'bridge')

from rss_transformer import (
    _build_users, 
    init_db, 
    create_job, 
    get_next_pending, 
    update_job,
    process_job,
    rebuild_feed,
    User
)
import rss_transformer


def test_rss_transformation_e2e():
    """End-to-end test for RSS transformation using Who_am_I.mp3."""
    
    # Create temporary directories for isolated testing
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        data_dir = tmp_path / "data"
        public_dir = tmp_path / "public"
        data_dir.mkdir()
        public_dir.mkdir()
        
        # Set up environment variables for isolated test
        env_vars = {
            "TRANSFORMER_CONFIG": str(data_dir / "transformer.yaml"),
            "BASE_URL": "http://localhost",
            "USERS": "testuser",
            "DATA_BASE": str(data_dir),
            "PUBLIC_DIR": str(public_dir)
        }
        
        with patch.dict(os.environ, env_vars, clear=False):
            # Force reimport to pick up new env vars
            import importlib
            importlib.reload(rss_transformer)
            
            # Create test user
            users = rss_transformer._build_users()
            user = users[0]
            
            # Initialize database
            init_db(user)
            
            # Create transformer config with our test MP3 file
            # We'll create a fake RSS feed that points to our MP3 file
            mp3_path = Path("test/Who_am_I.mp3")
            if not mp3_path.exists():
                # Fallback: create a dummy MP3 file for testing
                mp3_path = tmp_path / "test.mp3"
                mp3_path.write_bytes(b"ID3\x03\x00\x00\x00\x00\x00\x00\x00" + b"dummy mp3 data" * 100)
            
            config = {
                "rss_feeds": {
                    "testuser": [
                        {
                            "name": "test-podcast",
                            "url": f"file://{tmp_path.absolute()}/test.rss",
                            "title": "Test Podcast",
                        }
                    ]
                },
                "poll_interval_minutes": 30,
                "notebooklm": {
                    "default_style": "deep-dive",
                    "instructions": "Create a clear spoken summary"
                }
            }
            
            config_path = data_dir / "transformer.yaml"
            config_path.write_text(yaml.dump(config))
            
            # Create fake RSS feed pointing to our MP3 file
            rss_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">
    <channel>
        <title>Test Podcast</title>
        <link>http://example.com</link>
        <description>Test podcast for E2E testing</description>
        <item>
            <title>Who Am I?</title>
            <description>A test episode about identity</description>
            <enclosure url="file://{mp3_path.absolute()}" length="{mp3_path.stat().st_size}" type="audio/mpeg"/>
            <guid>test-guid-123</guid>
            <pubDate>Mon, 29 Apr 2026 10:00:00 GMT</pubDate>
        </item>
    </channel>
</rss>"""
            
            rss_path = tmp_path / "test.rss"
            rss_path.write_text(rss_content)
            
            # Test 1: Verify we can create a job from the RSS feed
            from rss_transformer import fetch_episodes
            import aiohttp
            
            async def test_fetch():
                # Create a mock session that returns our RSS
                class MockResponse:
                    def __init__(self, text):
                        self._text = text
                    
                    async def text(self):
                        return self._text
                    
                    async def __aenter__(self):
                        return self
                    
                    async def __aexit__(self, *args):
                        pass
                
                class MockSession:
                    def __init__(self, rss_text):
                        self.rss_text = rss_text
                    
                    def get(self, url, **kwargs):
                        # Ignore timeout and other parameters, just return our mock response
                        return MockResponse(self.rss_text)
                    
                    async def __aenter__(self):
                        return self
                    
                    async def __aexit__(self, *args):
                        pass
                
                # Test fetching episodes
                session = MockSession(rss_content)
                feed_title, episodes = await fetch_episodes(session, str(rss_path))
                
                assert feed_title == "Test Podcast"
                assert len(episodes) == 1
                assert episodes[0]["title"] == "Who Am I?"
                assert str(mp3_path.absolute()) in episodes[0]["url"]
                print("✓ RSS feed parsing works correctly")
                
                # Test 2: Create job from episode
                job_id = create_job(
                    user=user,
                    feed_name="test-podcast",
                    feed_title="Test Podcast",
                    episode_url=episodes[0]["url"],
                    title=episodes[0]["title"],
                    style="deep-dive"
                )
                
                assert job_id is not None
                print("✓ Job creation works correctly")
                
                # Test 3: Verify job is in pending state
                job = get_next_pending(user)
                assert job is not None
                assert job["id"] == job_id
                assert job["status"] == "pending"
                print("✓ Job retrieval works correctly")
                
                # Test 4: Test feed rebuilding (without actual processing)
                # Simulate a completed job
                update_job(user, job_id, status="done", artifact_id="test-artifact-123", duration=120)
                
                # Rebuild feed
                rebuild_feed(user, "test-podcast", "Test Podcast")
                
                # Verify feed was created
                feed_path = public_dir / "feed" / "testuser" / "test-podcast.xml"
                assert feed_path.exists(), f"Feed not found at {feed_path}"
                
                feed_content = feed_path.read_text()
                assert "<title>Test Podcast</title>" in feed_content
                assert "<title>Who Am I?</title>" in feed_content
                assert "test-artifact-123.m4a" in feed_content
                print("✓ Feed rebuilding works correctly")
                
                print("\n✅ All E2E tests passed!")
                return True
            
            # Run the async test
            result = asyncio.run(test_fetch())
            
            # Reload the module to clean up
            importlib.reload(rss_transformer)
            return result


if __name__ == "__main__":
    print("Running RSS Transformation E2E Test...")
    print("=" * 50)
    
    try:
        success = test_rss_transformation_e2e()
        if success:
            print("\n🎉 E2E test completed successfully!")
            exit(0)
        else:
            print("\n❌ E2E test failed!")
            exit(1)
    except Exception as e:
        print(f"\n💥 E2E test failed with exception: {e}")
        import traceback
        traceback.print_exc()
        exit(1)