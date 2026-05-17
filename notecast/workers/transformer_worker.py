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
        self._quota_until: dict[str, datetime] = {}  # user_id -> backoff expiry

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
            return  # quota backoff still active for this user

        repo = self._job_service._repo_factory(user)
        if repo.get_generating_jobs(user):
            return  # wait for in-flight generation to complete before submitting next

        # Check for failed jobs and retry them (one per cycle to respect API limits)
        failed_jobs = repo.get_failed_jobs(user)
        if failed_jobs:
            failed_job = failed_jobs[0]
            current_retries = failed_job.retries or 0
            max_retries = failed_job.max_retries if failed_job.max_retries is not None else 3

            is_quota = bool(failed_job.error_message and "quota" in failed_job.error_message.lower())

            if is_quota:
                user_id = user.name
                now = datetime.now(timezone.utc)
                until = self._quota_until.get(user_id)
                if until is None or now >= until:
                    self._quota_until[user_id] = now + timedelta(seconds=QUOTA_BACKOFF_SECONDS)
                    logger.warning(
                        "Job %s failed due to quota limits; suppressing retries for 30 min",
                        failed_job.id,
                    )
                # fall through — don't block pending job submission
            elif current_retries >= max_retries:
                logger.warning(
                    "Job %s exhausted retries (%d/%d), leaving as failed: %s",
                    failed_job.id, current_retries, max_retries, failed_job.title,
                )
                # fall through — don't block pending job submission
            else:
                logger.info(
                    "Retrying failed transformer job %s: %s (feed=%s, attempt %d/%d)",
                    failed_job.id, failed_job.title, failed_job.feed_name,
                    current_retries + 1, max_retries,
                )
                try:
                    repo.update_job(
                        user,
                        failed_job.id,
                        status="pending",
                        error_message="",
                        retries=current_retries + 1,
                    )
                    logger.info("Successfully reset job %s for retry", failed_job.id)
                    backoff_seconds = 2 ** current_retries
                    logger.info("Backoff %ds before next retry for job %s", backoff_seconds, failed_job.id)
                    await asyncio.sleep(backoff_seconds)
                except Exception as exc:
                    logger.error("Failed to reset job %s for retry: %s", failed_job.id, exc)
                # Don't process in this same cycle — pick up on next poll
                return

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
            logger.error("Failed job %s: %s", job.id, e)
