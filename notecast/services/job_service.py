from notecast.core.interfaces import JobRepository, FileStorage
from notecast.infrastructure.external.notebooklm_client import NotebookLMClientWrapper
from notecast.services.feed_service import FeedService
from typing import Callable
from notecast.core.models import User, Job, Episode, Artifact
import asyncio # Assuming async operations

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
        try:
            async with await self._nb_client.session(user) as client:
                nb = await client.create_notebook(job.title)
                repo.update_job(user, job.id, notebook_id=nb.id)

                await client.add_source(nb.id, url=job.episode_url)
                await client.generate_audio(nb.id, style=job.style)
                repo.update_job(user, job.id, status="generating")

                artifact = await client.wait_for_audio(nb.id, job.id)
                repo.update_job(user, job.id, artifact_id=artifact.id)

                path = await self._storage.download_and_remux(
                    client, user, job.feed_name, artifact
                )
                duration = self._storage.get_duration(path)
                repo.update_job(user, job.id, status="done", duration=duration)

                await client.delete_notebook(nb.id)
                await self._feed_service.rebuild_feed(user, job.feed_name, job.feed_title)

        except Exception as exc:
            await self._handle_failure(user, job, exc)

    async def _handle_failure(self, user: User, job: Job, exc: Exception) -> None:
        repo = self._repo_factory(user)
        # Placeholder for failure handling logic
        repo.update_job(user, job.id, status="failed", error_message=str(exc))
        await asyncio.sleep(0)

    async def get_next_pending(self, user: User) -> Job | None:
        repo = self._repo_factory(user)
        return await repo.get_next_pending(user)

    def create_job(self, user: User, episode: Episode) -> Job:
        repo = self._repo_factory(user)
        return repo.create_job(user, episode)