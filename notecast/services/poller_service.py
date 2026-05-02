"""Poller service - polls feeds and creates jobs."""
import logging
from typing import Callable

from notecast.infrastructure.config.settings import Settings
from notecast.infrastructure.external.feed_parser import fetch_episodes
from notecast.core.interfaces import JobRepository
from notecast.services.user_service import UserService
from notecast.core.models import User, Episode

logger = logging.getLogger(__name__)


class PollerService:
    """Service for polling feeds and creating jobs."""

    def __init__(
        self,
        repo_factory: Callable[[User], JobRepository],
        user_service: UserService,
        settings: Settings,
    ):
        self._repo_factory = repo_factory
        self._user_service = user_service
        self._settings = settings

    async def poll_feeds(self, user: User, config: dict) -> int:
        """Poll feeds for a user and create jobs for new episodes.
        
        Args:
            user: User to poll feeds for
            config: Configuration dict with feeds
            
        Returns:
            Number of new jobs created
        """
        repo = self._repo_factory(user)
        new_jobs = 0

        # Load user config (feeds)
        from notecast.infrastructure.config.user_config import load_user_config
        try:
            feeds = load_user_config(user)
        except Exception as e:
            logger.warning(f"[{user.name}] Failed to load config: {e}")
            return 0

        # Get default style from settings
        nb_cfg = config.get('notebooklm', {})
        default_style = nb_cfg.get('default_style', 'deep-dive')

        # Poll each feed
        for feed in feeds:
            try:
                logger.info(f"[{user.name}] Polling feed: {feed.name}")
                
                # Fetch episodes from feed URL
                feed_title, episodes = fetch_episodes(feed.url)
                
                # Use configured title or fetched title
                feed_title = feed.title or feed_title or feed.name
                style = feed.style or default_style
                
                # Create jobs for new episodes, most recent first, up to max_episodes
                queued_this_feed = 0
                for episode in episodes:
                    if queued_this_feed >= feed.max_episodes:
                        break
                    if not repo.episode_seen(user, episode.url):
                        repo.create_job(user, Episode(
                            url=episode.url,
                            title=episode.title,
                            feed_name=feed.name,
                            feed_title=feed_title,
                            style=style,
                        ))
                        logger.info(f"[{user.name}:{feed.name}] Queued: {episode.title[:70]}")
                        new_jobs += 1
                        queued_this_feed += 1
                        
            except Exception as e:
                logger.error(f"[{user.name}] Failed to poll feed {feed.name}: {e}", exc_info=True)

        if new_jobs:
            logger.info(f"[{user.name}] Queued {new_jobs} new job(s)")

        return new_jobs
