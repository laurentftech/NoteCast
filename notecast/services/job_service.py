import asyncio
import logging
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

from notecast.core.interfaces import JobRepository, FileStorage
from notecast.infrastructure.external.notebooklm_client import NotebookLMClientWrapper
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
    ):
        self._repo_factory = repo_factory
        self._storage = storage
        self._nb_client = nb_client
        self._feed_service = feed_service
        self._whisper_model = whisper_model

    async def process_job(self, user: User, job: Job, config: dict) -> None:
        repo = self._repo_factory(user)
        repo.update_job(user, job.id, status="processing")
        nb_id: str | None = None
        transcript_path: Path | None = None
        try:
            async with await self._nb_client.session(user) as client:
                nb = await client.create_notebook(job.title)
                nb_id = nb.id
                repo.update_job(user, job.id, notebook_id=nb.id)

                source = job.source_url or job.episode_url
                if _is_youtube(source):
                    logger.info("Job %s: adding YouTube source", job.id)
                    await client.add_source(nb.id, url=source)
                elif _is_audio(job.episode_url):
                    logger.info("Job %s: transcribing audio via Whisper", job.id)
                    from notecast.infrastructure.external.transcriber import transcribe_url
                    transcript_path = await transcribe_url(
                        job.episode_url, self._whisper_model
                    )
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
