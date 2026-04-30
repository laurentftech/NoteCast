"""Harvester worker - downloads artifacts from NotebookLM."""
import asyncio
import logging
from typing import Optional

from notecast.core.models import User
from notecast.services.harvester_service import HarvesterService
from notecast.services.user_service import UserService


logger = logging.getLogger(__name__)


class HarvesterWorker:
    """Worker that harvests artifacts from NotebookLM."""

    HARVEST_INTERVAL = 3600  # 1 hour

    def __init__(
        self,
        harvester_service: HarvesterService,
        user_service: UserService,
    ):
        self._harvester_service = harvester_service
        self._user_service = user_service
        self._running = False

    async def run(self) -> None:
        """Run the harvester worker loop.
        
        Periodically harvests artifacts for all users.
        """
        self._running = True
        logger.info("Harvester worker started")

        while self._running:
            try:
                await self._harvest_all_users()
                await asyncio.sleep(self.HARVEST_INTERVAL)
            except asyncio.CancelledError:
                logger.info("Harvester worker cancelled")
                break
            except Exception as e:
                logger.error(f"Error in harvester worker: {e}", exc_info=True)
                await asyncio.sleep(300)  # Wait 5 minutes before retrying

        logger.info("Harvester worker stopped")

    async def stop(self) -> None:
        """Stop the worker."""
        self._running = False

    async def _harvest_all_users(self) -> None:
        """Harvest artifacts for all users."""
        users = await self._user_service.get_all()

        for user in users:
            try:
                await self._harvester_service.harvest_user(user)
            except Exception as e:
                logger.error(
                    f"Error harvesting for user {user.name}: {e}",
                    exc_info=True,
                )
