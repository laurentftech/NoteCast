"""Keepalive worker — holds a persistent NotebookLM session per user so the
notebooklm-py background task can rotate __Secure-1PSIDTS every keepalive_interval
seconds and write the refreshed token back to storage_state.json."""
import asyncio
import logging

from notecast.core.models import User
from notecast.infrastructure.external.notebooklm_client import NotebookLMClientWrapper
from notecast.services.user_service import UserService

logger = logging.getLogger(__name__)


class KeepaliveWorker:
    def __init__(
        self,
        nb_client: NotebookLMClientWrapper,
        user_service: UserService,
    ):
        self._nb_client = nb_client
        self._user_service = user_service
        self._running = False

    async def run(self) -> None:
        self._running = True
        users = await self._user_service.get_all()
        active_users = [u for u in users if u.auth_file.exists()]

        if not active_users:
            logger.info("Keepalive: no authenticated users, worker idle")
            return

        logger.info("Keepalive: holding persistent sessions for %d user(s)", len(active_users))
        tasks = [
            asyncio.create_task(self._hold_session(u), name=f"keepalive-{u.name}")
            for u in active_users
        ]
        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            logger.info("Keepalive worker stopped")

    async def _hold_session(self, user: User) -> None:
        """Open a session for *user* and keep it alive until cancelled."""
        while True:
            try:
                async with await self._nb_client.session(user) as _client:
                    logger.info("Keepalive: session open for user %s", user.name)
                    # Stay open indefinitely; keepalive rotates token in background.
                    await asyncio.Event().wait()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(
                    "Keepalive: session lost for user %s (%s) — retrying in 60s",
                    user.name, exc,
                )
                await asyncio.sleep(60)
