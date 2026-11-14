import asyncio
import logging
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

from notecast.core.interfaces import JobRepository, FileStorage
from notecast.infrastructure.external.notebooklm_client import NotebookLMClientWrapper
from notecast.infrastructure.external.gemini_notebook_api import GeminiNotebookApiClient
from notecast.infrastructure.external.gemini_notebook_credentials import GeminiNotebookCredentialStore
from notecast.services.feed_service import FeedService
from notecast.core.models import User, Job, Episode

logger = logging.getLogger(__name__)

_AUDIO_EXTS = {".mp3", ".m4a", ".ogg", ".wav", ".flac", ".aac", ".opus"}


_YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"}

def _is_youtube(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    return host in _YOUTUBE_HOSTS or host.endswith(".youtube.com")


def _is_audio(url: str) -> bool:
    path = urlparse(url).path.lower().split("?")[0]
    return any(path.endswith(ext) for ext in _AUDIO_EXTS)


class JobService:
    def __init__(
        self,
        repo_factory: Callable[[User], JobRepository],
        storage: FileStorage,
        nb_client: NotebookLMClientWrapper,
        feed_service: FeedService,
        whisper_model: str = "base",
        gemini_credentials: GeminiNotebookCredentialStore | None = None,
        gemini_project_id: str = "",
        gemini_pipeline_enabled: bool = False,
    ):
        self._repo_factory = repo_factory
        self._storage = storage
        self._nb_client = nb_client
        self._feed_service = feed_service
        self._whisper_model = whisper_model
        self._gemini_credentials = gemini_credentials
        self._gemini_project_id = gemini_project_id
        self._gemini_pipeline_enabled = gemini_pipeline_enabled

    async def process_job(self, user: User, job: Job, config: dict) -> None:
        repo = self._repo_factory(user)
        repo.update_job(user, job.id, status="processing")
        nb_id: str | None = None
        transcript_path: Path | None = None
        hybrid_notebook = self._gemini_pipeline_enabled
        if hybrid_notebook and self._gemini_credentials and self._gemini_project_id:
            if not self._gemini_credentials.has_credentials(user):
                logger.info(
                    "Job %s: no Gemini Notebook OAuth connection for %s; using legacy pipeline",
                    job.id,
                    user.name,
                )
                hybrid_notebook = False
        try:
            source = job.source_url or job.episode_url
            if not _is_youtube(source) and _is_audio(job.episode_url):
                logger.info("Job %s: transcribing audio via Whisper", job.id)
                from notecast.infrastructure.external.transcriber import transcribe_url
                transcript_path = await transcribe_url(
                    job.episode_url, self._whisper_model
                )

            if hybrid_notebook:
                if not self._gemini_credentials or not self._gemini_project_id:
                    raise RuntimeError(
                        "Hybrid Gemini Notebook pipeline is enabled but OAuth credentials or project ID are missing"
                    )
                if job.notebook_id:
                    async with await self._nb_client.session(user) as client:
                        await client.delete_notebook(job.notebook_id)
                credentials = await self._gemini_credentials.get_credentials(user)
                async with GeminiNotebookApiClient(
                    credentials, self._gemini_project_id
                ) as public_client:
                    notebook = await public_client.create_notebook(job.title)
                    resource_name = notebook.get("name", "")
                    if not resource_name.startswith("notebooks/"):
                        raise RuntimeError("Gemini Notebook API returned no notebook resource name")
                    hybrid_nb_id = resource_name.removeprefix("notebooks/")
                    nb_id = hybrid_nb_id
                    repo.update_job(user, job.id, notebook_id=hybrid_nb_id)
                    # Verify cross-surface identity before uploading any source.
                    async with await self._nb_client.session(user) as client:
                        await client.get_notebook(hybrid_nb_id)
                    if transcript_path:
                        source_result = await public_client.create_text_source(
                            resource_name,
                            transcript_path.read_text(encoding="utf-8"),
                            display_name=job.title,
                        )
                    else:
                        source_result = await public_client.create_url_source(
                            resource_name, source, display_name=job.title
                        )
                    if source_result.get("name"):
                        await public_client.wait_for_source(source_result["name"])

                    # The public API and legacy audio RPC are separate systems.
                    # The identity check above must pass before generation proceeds.
                    async with await self._nb_client.session(user) as client:
                        await client.generate_audio(
                            hybrid_nb_id,
                            style=job.style,
                            instructions=job.instructions,
                            language=job.language,
                        )
            else:
                async with await self._nb_client.session(user) as client:
                    if job.notebook_id:
                        logger.info(
                            "Job %s: deleting superseded notebook %s before retry",
                            job.id, job.notebook_id,
                        )
                        await client.delete_notebook(job.notebook_id)
                    nb = await client.create_notebook(job.title)
                    nb_id = nb.id
                    repo.update_job(user, job.id, notebook_id=nb.id)

                    if _is_youtube(source):
                        logger.info("Job %s: adding YouTube source", job.id)
                        await client.add_source(nb.id, url=source)
                    elif transcript_path:
                        await client.add_source_file(nb.id, transcript_path)
                    else:
                        logger.info("Job %s: adding URL source", job.id)
                        await client.add_source(nb.id, url=source)

                    await client.generate_audio(
                        nb.id,
                        style=job.style,
                        instructions=job.instructions,
                        language=job.language,
                    )
            repo.update_job(user, job.id, status="generating")
            # Harvester worker polls and completes download

        except Exception as exc:
            await self._handle_failure(user, job, exc, nb_id=nb_id)
            raise
        finally:
            if transcript_path:
                transcript_path.unlink(missing_ok=True)

    async def _handle_failure(
        self,
        user: User,
        job: Job,
        exc: Exception,
        nb_id: str | None = None,
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
