"""Harvester service - recovers stuck generating jobs from NotebookLM."""
import logging
from typing import Optional

from notecast.core.interfaces import JobRepository
from notecast.core.models import User, Artifact
from notecast.infrastructure.external.notebooklm_client import NotebookLMClientWrapper
from notecast.infrastructure.external.webhook_client import WebhookClient
from notecast.infrastructure.storage.file_storage import LocalFileStorage
from notecast.services.feed_service import FeedService

logger = logging.getLogger(__name__)


class HarvesterService:
    """Recovers jobs stuck in 'generating' state after a service restart."""

    def __init__(
        self,
        nb_client: NotebookLMClientWrapper,
        repo_factory,
        storage: LocalFileStorage,
        feed_service: FeedService,
        webhook: Optional[WebhookClient] = None,
    ):
        self._nb_client = nb_client
        self._repo_factory = repo_factory
        self._storage = storage
        self._feed_service = feed_service
        self._webhook = webhook

    async def harvest_user(self, user: User) -> None:
        """Check for stuck generating jobs and scan for orphaned NotebookLM audio."""
        if not user.auth_file.exists():
            return

        repo: JobRepository = self._repo_factory(user)
        stuck_jobs = repo.get_generating_jobs(user)

        async with await self._nb_client.session(user) as client:
            if stuck_jobs:
                logger.info("Recovering %d stuck job(s) for user %s", len(stuck_jobs), user.name)
                for job in stuck_jobs:
                    try:
                        await self._recover_job(client, repo, user, job)
                    except Exception as exc:
                        logger.error("Failed to recover job %s: %s", job.id, exc)
                        repo.update_job(user, job.id, status="failed")

            await self._scan_orphaned_notebooks(client, repo, user)

    async def _scan_orphaned_notebooks(self, client, repo: JobRepository, user: User) -> None:
        """Import NotebookLM notebooks that have audio but no DB record."""
        from notecast.core.models import Episode as EpisodeModel, Artifact as ArtifactModel
        try:
            notebooks = await client._client.notebooks.list()
        except Exception as exc:
            logger.warning("Could not list notebooks for user %s: %s", user.name, exc)
            return

        known_ids = repo.get_known_notebook_ids(user)
        imported = 0

        for nb in notebooks:
            if nb.id in known_ids:
                continue
            try:
                audio_list = await client._client.artifacts.list_audio(nb.id)
            except Exception:
                continue
            if not audio_list:
                continue

            artifact_id = audio_list[0].id
            artifact = ArtifactModel(id=artifact_id, notebook_id=nb.id)
            try:
                path = await self._storage.download_and_remux(client, user, "imported", artifact)
                duration = self._storage.get_duration(path)
            except Exception as exc:
                logger.error("Failed to download orphaned notebook %s: %s", nb.id, exc)
                continue

            episode = EpisodeModel(
                url=f"notebooklm://{nb.id}",
                title=nb.title or nb.id,
                feed_name="imported",
                feed_title="Imported",
                style="deep-dive",
            )
            job = repo.create_job(user, episode)
            repo.update_job(user, job.id,
                            status="done",
                            notebook_id=nb.id,
                            artifact_id=artifact_id,
                            duration=duration)

            logger.info("Imported orphaned notebook '%s' (%s)", nb.title, nb.id)
            imported += 1
            await self._feed_service.rebuild_feed(user, "imported", "Imported")

        if imported:
            logger.info("Imported %d orphaned notebook(s) for user %s", imported, user.name)

    async def _recover_job(self, client, repo: JobRepository, user: User, job) -> None:
        # Check if audio artifact exists on NotebookLM
        audio_list = await client._client.artifacts.list_audio(job.notebook_id)
        if not audio_list:
            logger.debug("Job %s: audio not ready yet (notebook=%s)", job.id, job.notebook_id)
            return

        artifact_id = job.artifact_id or audio_list[0].id
        artifact = Artifact(id=artifact_id, notebook_id=job.notebook_id)

        path = await self._storage.download_and_remux(client, user, job.feed_name, artifact)
        duration = self._storage.get_duration(path)

        repo.update_job(user, job.id, status="done", artifact_id=artifact_id, duration=duration)
        logger.info("Recovered job %s -> %s", job.id, path)

        await self._feed_service.rebuild_feed(user, job.feed_name, job.feed_title)

        try:
            await client._client.notebooks.delete(job.notebook_id)
        except Exception as exc:
            logger.warning("Could not delete notebook %s: %s", job.notebook_id, exc)

    async def download_artifact(
        self, client, notebook_id: str, artifact_id: str, user: User, output_path: str
    ) -> Optional[Artifact]:
        """Download a single artifact to output_path."""
        try:
            await client.download_audio(notebook_id, output_path, artifact_id)
            artifact = Artifact(id=artifact_id, notebook_id=notebook_id)
            if self._webhook:
                await self._webhook.notify_job_completed(user, artifact_id, "harvest")
            return artifact
        except Exception as exc:
            if self._webhook:
                await self._webhook.notify_job_failed(user, artifact_id, "harvest", str(exc))
            return None
