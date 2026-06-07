"""Poller service - polls feeds and creates jobs."""
import logging
from datetime import datetime, timezone, timedelta
from typing import Callable, Optional

from notecast.core.auth_utils import auth_expires_in_days
from notecast.infrastructure.config.settings import Settings
from notecast.infrastructure.external.feed_parser import fetch_episodes
from notecast.infrastructure.external.webhook_client import WebhookClient
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
        webhook: Optional[WebhookClient] = None,
    ):
        self._repo_factory = repo_factory
        self._user_service = user_service
        self._settings = settings
        self._webhook = webhook
        self._last_expiry_notif: dict[str, datetime] = {}

    async def poll_feeds(self, user: User, config: dict) -> int:
        """Poll feeds for a user and create jobs for new episodes.

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

        if not feeds:
            logger.warning("[%s] No feeds configured — create config/transformer.yaml", user.name)
            return 0

        # Poll each feed
        for feed in feeds:
            try:
                logger.info(f"[{user.name}] Polling feed: {feed.name}")

                feed_title, episodes = fetch_episodes(feed.url)
                episodes.sort(key=lambda e: e.published_at or datetime.min, reverse=True)

                feed_title = feed.title or feed_title or feed.name
                style = feed.style or default_style

                active = repo.count_active_jobs(user, feed.name)
                if active >= feed.max_episodes:
                    logger.debug("[%s:%s] %d active job(s), max=%d — skipping", user.name, feed.name, active, feed.max_episodes)
                    continue
                queued_this_feed = active
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
                            instructions=feed.instructions,
                            language=feed.language,
                        ))
                        logger.info(f"[{user.name}:{feed.name}] Queued: {episode.title[:70]}")
                        new_jobs += 1
                        queued_this_feed += 1

            except Exception as e:
                logger.error(f"[{user.name}] Failed to poll feed {feed.name}: {e}", exc_info=True)

        if new_jobs:
            logger.info(f"[{user.name}] Queued {new_jobs} new job(s)")

        await self._check_token_expiry(user)

        return new_jobs

    async def _check_token_expiry(self, user: User) -> None:
        """Send a webhook warning if the auth token is nearing expiry (once per 24h)."""
        webhook = self._get_webhook(user)
        if not webhook:
            return

        days = auth_expires_in_days(user)
        if days is None:
            return

        warn_days = self._settings.token_expiry_warn_days
        if days > warn_days:
            return

        now = datetime.now(timezone.utc)
        last = self._last_expiry_notif.get(user.name)
        if last and (now - last) < timedelta(hours=24):
            return

        self._last_expiry_notif[user.name] = now
        try:
            await webhook.notify_token_expiry(user, days)
            logger.warning("[%s] Token expiry warning sent: %d day(s) remaining", user.name, days)
        except Exception as exc:
            logger.error("[%s] Failed to send token expiry notification: %s", user.name, exc)

    def _get_webhook(self, user: User) -> Optional[WebhookClient]:
        if self._webhook and self._webhook._webhook_url:
            return self._webhook
        if user.webhook_url:
            return WebhookClient(
                webhook_url=user.webhook_url,
                webhook_headers=user.webhook_headers,
            )
        return None
