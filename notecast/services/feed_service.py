"""Feed service for managing podcast feeds."""
import logging
from typing import Callable
from pathlib import Path
from datetime import timezone

from podgen import Podcast, Episode, Media

from notecast.core.interfaces import JobRepository, FileStorage
from notecast.infrastructure.config.settings import Settings
from notecast.core.models import Job, User

logger = logging.getLogger(__name__)


class FeedService:
    def __init__(self, repo_factory: Callable[[User], JobRepository], storage: FileStorage, settings: Settings):
        self._repo_factory = repo_factory
        self._storage = storage
        self._settings = settings

    async def rebuild_feed(self, user: User, feed_name: str, feed_title: str) -> None:
        """Rebuild RSS feed from completed jobs.
        
        Args:
            user: User object
            feed_name: Name of the feed
            feed_title: Title of the feed
        """
        base_url = self._settings.base_url.rstrip('/') if self._settings.base_url else None
        if not base_url:
            logger.warning(f"[{user.name}] BASE_URL not set — skipping feed rebuild")
            return

        repo = self._repo_factory(user)
        jobs = repo.get_done_jobs(user, feed_name)
        
        # Build podcast RSS
        podcast = self._build_podcast(user, feed_name, feed_title, jobs, base_url)
        
        # Write feed file
        self._storage.write_feed(user, feed_name, podcast.rss_str())
        logger.info(f"[{user.name}] Rebuilt feed: {feed_name} with {len(jobs)} episodes")

    def _build_podcast(
        self,
        user: User,
        feed_name: str,
        feed_title: str,
        jobs: list[Job],
        base_url: str,
    ) -> Podcast:
        """Build a Podcast object from completed jobs.
        
        Args:
            user: User object
            feed_name: Name of feed
            feed_title: Title of feed
            jobs: List of completed Job objects
            base_url: Base URL for feed links
            
        Returns:
            Podcast object ready for RSS generation
        """
        feed_url = f"{base_url}/feed/{user.name}/{feed_name}.xml?token={user.feed_token}"
        ep_base = f"{base_url}/episodes/{user.name}/{feed_name}/"

        podcast = Podcast(
            name=feed_title,
            description=f"NoteCast audio overviews — {feed_title}",
            website=feed_url,
            explicit=False,
        )

        for job in jobs:
            if not job.artifact_id:
                continue
                
            m4a_filename = f"{job.artifact_id}.m4a"
            m4a_path = self._storage.episode_path(user, feed_name, job.artifact_id)
            
            # Get file size
            file_size = 0
            try:
                file_size = m4a_path.stat().st_size
            except FileNotFoundError:
                logger.warning(f"Episode file not found: {m4a_path}")
                continue
            
            # Parse publication date
            try:
                pub_date = job.created_at
                if pub_date.tzinfo is None:
                    pub_date = pub_date.replace(tzinfo=timezone.utc)
            except Exception as e:
                logger.warning(f"Failed to parse date for job {job.id}: {e}")
                pub_date = None

            podcast.add_episode(Episode(
                title=job.title,
                media=Media(
                    f"{ep_base}{m4a_filename}",
                    file_size,
                    type='audio/mp4'
                ),
                publication_date=pub_date,
            ))

        return podcast

    def get_feed_url(self, user: User, feed_name: str) -> str:
        """Get the public feed URL for a user's feed.
        
        Args:
            user: User object
            feed_name: Name of the feed
            
        Returns:
            Full URL to the feed XML
        """
        base_url = self._settings.base_url.rstrip('/') if self._settings.base_url else ''
        if not base_url:
            return f"/feed/{user.name}/{feed_name}.xml"
        return f"{base_url}/feed/{user.name}/{feed_name}.xml?token={user.feed_token}"