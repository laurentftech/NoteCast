"""Harvester service - recovers stuck generating jobs from NotebookLM."""

import logging
from typing import Optional

from notecast.core.interfaces import JobRepository
from notecast.core.models import Artifact, User
from notecast.infrastructure.config.settings import Settings
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
        settings: Settings,
        webhook: Optional[WebhookClient] = None,
    ):
        self._nb_client = nb_client
        self._repo_factory = repo_factory
        self._storage = storage
        self._feed_service = feed_service
        self._settings = settings
        self._webhook = webhook

    async def harvest_user(self, user: User) -> None:
        """Check for stuck generating jobs and scan for orphaned NotebookLM audio."""
        logger.info("Starting harvest for user %s", user.name)
        
        if not user.auth_file.exists():
            logger.debug("No auth file for user %s, skipping harvest", user.name)
            return

        repo: JobRepository = self._repo_factory(user)
        stuck_jobs = repo.get_generating_jobs(user)
        logger.info("Found %d stuck generating jobs for user %s", len(stuck_jobs), user.name)

        async with await self._nb_client.session(user) as client:
            if stuck_jobs:
                logger.info(
                    "Recovering %d stuck job(s) for user %s", len(stuck_jobs), user.name
                )
                for job in stuck_jobs:
                    try:
                        await self._recover_job(client, repo, user, job)
                    except Exception as exc:
                        logger.error("Failed to recover job %s: %s", job.id, exc)
                        repo.update_job(user, job.id, status="failed")

            await self._scan_orphaned_notebooks(client, repo, user)

    async def _scan_orphaned_notebooks(
        self, client, repo: JobRepository, user: User
    ) -> None:
        """Import NotebookLM notebooks that have audio but no DB record."""
        from notecast.core.models import Artifact as ArtifactModel
        from notecast.core.models import Episode as EpisodeModel

        try:
            notebooks = await client._client.notebooks.list()
            logger.info("Found %d notebooks for user %s", len(notebooks), user.name)
        except Exception as exc:
            logger.warning("Could not list notebooks for user %s: %s", user.name, exc)
            return

        known_ids = repo.get_known_notebook_ids(user)
        logger.info("User %s has %d known notebooks in database", user.name, len(known_ids))
        imported = 0

        for nb in notebooks:
            logger.info("Processing notebook %s: %s", nb.id, nb.title)
            if nb.id in known_ids:
                logger.info("Skipping known notebook %s: %s", nb.id, nb.title)
                continue
            try:
                audio_list = await client._client.artifacts.list_audio(nb.id)
                if not audio_list:
                    logger.info("Skipping notebook %s: no audio artifacts found", nb.id)
                    continue
            except Exception as exc:
                logger.warning("Failed to get audio list for notebook %s: %s", nb.id, exc)
                continue

            artifact_id = audio_list[0].id
            artifact = ArtifactModel(id=artifact_id, notebook_id=nb.id)
            logger.info("Found audio artifact %s for notebook %s: %s", artifact_id, nb.id, nb.title)
            try:
                path = await self._storage.download_and_remux(
                    client, user, "imported", artifact
                )
                duration = self._storage.get_duration(path)
            except Exception as exc:
                logger.error("Failed to download orphaned notebook %s: %s", nb.id, exc)
                continue

            imported_title = self._settings.imported_feed_title
            episode = EpisodeModel(
                url=f"notebooklm://{nb.id}",
                title=nb.title or nb.id,
                feed_name="imported",
                feed_title=imported_title,
                style="deep-dive",
            )
            job = repo.create_job(user, episode)
            update = dict(
                status="done",
                notebook_id=nb.id,
                artifact_id=artifact_id,
                duration=duration,
            )
            if nb.created_at:
                update["created_at"] = nb.created_at.isoformat()
            repo.update_job(user, job.id, **update)

            logger.info("Imported orphaned notebook '%s' (%s)", nb.title, nb.id)
            
            # Send webhook notification
            webhook = await self._get_webhook_client(user)
            if webhook:
                try:
                    await webhook.notify_job_completed(
                        user, job.id, "imported", nb.title or nb.id
                    )
                except Exception as exc:
                    logger.error("Failed to send webhook for imported notebook %s: %s", nb.id, exc)
            
            imported += 1
            await self._feed_service.rebuild_feed(user, "imported", imported_title)

        if imported:
            logger.info(
                "Imported %d orphaned notebook(s) for user %s", imported, user.name
            )

    async def _recover_job(self, client, repo: JobRepository, user: User, job) -> None:
        # Check if audio artifact exists on NotebookLM
        audio_list = await client._client.artifacts.list_audio(job.notebook_id)
        if not audio_list:
            logger.debug(
                "Job %s: audio not ready yet (notebook=%s)", job.id, job.notebook_id
            )
            return

        artifact_id = job.artifact_id or audio_list[0].id
        artifact = Artifact(id=artifact_id, notebook_id=job.notebook_id)

        path = await self._storage.download_and_remux(
            client, user, job.feed_name, artifact
        )
        duration = self._storage.get_duration(path)

        repo.update_job(
            user, job.id, status="done", artifact_id=artifact_id, duration=duration
        )
        logger.info("Recovered job %s -> %s", job.id, path)

        # Send webhook notification
        webhook = await self._get_webhook_client(user)
        if webhook:
            try:
                await webhook.notify_job_completed(
                    user, job.id, job.feed_name, job.title
                )
            except Exception as exc:
                logger.error("Failed to send webhook for job %s: %s", job.id, exc)

        await self._feed_service.rebuild_feed(user, job.feed_name, job.feed_title)

        try:
            await client._client.notebooks.delete(job.notebook_id)
        except Exception as exc:
            logger.warning("Could not delete notebook %s: %s", job.notebook_id, exc)

    async def _get_webhook_client(self, user: User) -> Optional[WebhookClient]:
        """Get webhook client (global or per-user)."""
        logger.debug("_get_webhook_client called for user %s, user.webhook_url=%s", user.name, user.webhook_url)
        # Only use global webhook if it has a URL (prefer per-user if available)
        if self._webhook and self._webhook._webhook_url:
            logger.debug("Using global webhook client")
            return self._webhook
        if user.webhook_url:
            try:
                logger.debug("Creating per-user webhook client with URL: %s", user.webhook_url)
                return WebhookClient(
                    webhook_url=user.webhook_url,
                    webhook_headers=user.webhook_headers,
                )
            except Exception as exc:
                logger.error("Failed to create webhook client for user %s: %s", user.name, exc)
        logger.debug("No webhook client available for user %s", user.name)
        return None

    async def download_artifact(
        self, client, notebook_id: str, artifact_id: str, user: User, output_path: str
    ) -> Optional[Artifact]:
        """Download a single artifact to output_path."""
        webhook = await self._get_webhook_client(user)
        try:
            await client.download_audio(notebook_id, output_path, artifact_id)
            artifact = Artifact(id=artifact_id, notebook_id=notebook_id)
            if webhook:
                await webhook.notify_job_completed(user, artifact_id, "harvest")
            return artifact
        except Exception as exc:
            if webhook:
                await webhook.notify_job_failed(user, artifact_id, "harvest", str(exc))
            return None
