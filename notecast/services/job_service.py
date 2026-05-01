import logging
from typing import Callable

import asyncio

from notecast.core.interfaces import JobRepository, FileStorage
from notecast.infrastructure.external.notebooklm_client import NotebookLMClientWrapper
from notecast.services.feed_service import FeedService
from notecast.core.models import User, Job, Episode

logger = logging.getLogger(__name__)


class JobService:
    def __init__(
        self,
        repo_factory: Callable[[User], JobRepository],
        storage: FileStorage,
        nb_client: NotebookLMClientWrapper,
        feed_service: FeedService,
    ):
        self._repo_factory = repo_factory
        self._storage = storage
        self._nb_client = nb_client
        self._feed_service = feed_service

    async def process_job(self, user: User, job: Job, config: dict) -> None:
        repo = self._repo_factory(user)
        repo.update_job(user, job.id, status="processing")
        nb_id: str | None = None
        try:
            async with await self._nb_client.session(user) as client:
                nb = await client.create_notebook(job.title)
                nb_id = nb.id
                repo.update_job(user, job.id, notebook_id=nb.id)

                await client.add_source(nb.id, url=job.episode_url)
                await client.generate_audio(nb.id, style=job.style)
                repo.update_job(user, job.id, status="generating")
                # Harvester worker polls and completes download

        except Exception as exc:
            await self._handle_failure(user, job, exc, nb_id=nb_id)
            raise

    async def _handle_failure(
        self, user: User, job: Job, exc: Exception, nb_id: str | None = None
    ) -> None:
        repo = self._repo_factory(user)
        repo.update_job(user, job.id, status="failed", error_message=str(exc))
        if nb_id:
            try:
                async with await self._nb_client.session(user) as client:
                    await client.delete_notebook(nb_id)
            except Exception as del_exc:
                logger.warning("Could not delete orphaned notebook %s: %s", nb_id, del_exc)
        await asyncio.sleep(0)

    async def get_next_pending(self, user: User) -> Job | None:
        repo = self._repo_factory(user)
        return await repo.get_next_pending(user)

    def create_job(self, user: User, episode: Episode) -> Job:
        repo = self._repo_factory(user)
        return repo.create_job(user, episode)
