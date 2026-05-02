"""Poller worker - periodically polls RSS feeds and queues new episodes."""
import asyncio
import logging

from notecast.infrastructure.config.settings import Settings
from notecast.services.job_service import JobService
from notecast.services.poller_service import PollerService
from notecast.services.user_service import UserService

logger = logging.getLogger(__name__)


class PollerWorker:
    def __init__(
        self,
        poller_service: PollerService,
        user_service: UserService,
        job_service: JobService,
        settings: Settings,
    ):
        self._poller_service = poller_service
        self._user_service = user_service
        self._job_service = job_service
        self._settings = settings
        self._running = False

    async def run(self) -> None:
        self._running = True
        logger.info("Poller worker started (interval=%ds)", self._settings.poll_interval)

        while self._running:
            try:
                await self._poll_all_users()
            except asyncio.CancelledError:
                logger.info("Poller worker cancelled")
                break
            except Exception as exc:
                logger.error("Error in poller worker: %s", exc, exc_info=True)

            try:
                await asyncio.sleep(self._settings.poll_interval)
            except asyncio.CancelledError:
                break

        logger.info("Poller worker stopped")

    async def stop(self) -> None:
        self._running = False

    async def _poll_all_users(self) -> None:
        users = await self._user_service.get_all()
        config = {"job_service": self._job_service}
        for user in users:
            try:
                n = await self._poller_service.poll_feeds(user, config)
                if n:
                    logger.info("Queued %d new job(s) for user %s", n, user.name)
            except Exception as exc:
                logger.error("Poll failed for user %s: %s", user.name, exc, exc_info=True)
