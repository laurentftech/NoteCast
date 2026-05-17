"""Transformer worker - processes pending jobs."""
import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

from notecast.core.models import User
from notecast.services.job_service import JobService
from notecast.services.user_service import UserService
from notecast.infrastructure.config.settings import Settings


logger = logging.getLogger(__name__)

QUOTA_BACKOFF_SECONDS = 1800


class TransformerWorker:
    """Worker that processes pending transformation jobs."""

    def __init__(
        self,
        job_service: JobService,
        user_service: UserService,
        settings: Settings,
        poll_interval: Optional[int] = None,
    ):
        self._job_service = job_service
        self._user_service = user_service
        self._settings = settings
        self._poll_interval = poll_interval or 10  # seconds between job queue checks
        self._running = False
        self._quota_until: dict[str, datetime] = {}  # pauses submission when quota hit

    async def run(self) -> None:
        """Run the worker loop.
        
        Continuously polls for pending jobs and processes them.
        """
        self._running = True
        logger.info("Transformer worker started")

        while self._running:
            try:
                await self._process_pending_jobs()
                await asyncio.sleep(self._poll_interval)
            except asyncio.CancelledError:
                logger.info("Transformer worker cancelled")
                break
            except Exception as e:
                logger.error(f"Error in transformer worker: {e}", exc_info=True)
                await asyncio.sleep(60)  # Wait before retrying

        logger.info("Transformer worker stopped")

    async def stop(self) -> None:
        """Stop the worker."""
        self._running = False

    async def _process_pending_jobs(self) -> None:
        """Process all pending jobs for all users."""
        users = await self._user_service.get_all()

        for user in users:
            try:
                await self._process_user_jobs(user)
            except Exception as e:
                logger.error("Error processing jobs for user %s: %s", user.name, e)

    async def _process_user_jobs(self, user: User) -> None:
        """Submit one pending job only when no generation is already in flight."""
        until = self._quota_until.get(user.name)
        if until and datetime.now(timezone.utc) < until:
            return  # quota backoff active — harvester handles downloads independently

        repo = self._job_service._repo_factory(user)
        if repo.get_generating_jobs(user):
            return  # wait for in-flight generation to complete before submitting next

        job = await self._job_service.get_next_pending(user)
        if not job:
            return

        try:
            logger.info(
                "Processing job %s for user %s, feed %s",
                job.id, user.name, job.feed_name,
            )
            config = {
                "storage": self._job_service._storage,
                "job_service": self._job_service,
            }
            await self._job_service.process_job(user, job, config)
            logger.info("Generation started for job %s (feed=%s)", job.id, job.feed_name)
        except Exception as e:
            if "quota" in str(e).lower():
                self._quota_until[user.name] = datetime.now(timezone.utc) + timedelta(seconds=QUOTA_BACKOFF_SECONDS)
                logger.warning("Quota hit for user %s — pausing submission for 30 min", user.name)
            logger.error("Failed job %s: %s", job.id, e)
