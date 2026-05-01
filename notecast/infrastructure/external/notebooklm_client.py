"""NotebookLM client wrapper using notebooklm-py."""
import logging
from pathlib import Path
from typing import Any

from notecast.core.models import Artifact, User
from notecast.core.exceptions import NotebookLMError

logger = logging.getLogger(__name__)


class NotebookLMClientWrapper:
    """Thin async wrapper around NotebookLMClient."""

    def __init__(
        self,
        auth_file: Path | None = None,
        max_retries: int = 3,
        timeout: int = 2700,
    ):
        self._auth_file = auth_file
        self._max_retries = max_retries
        self._timeout = timeout
        self._client: Any = None
        # notebook_id -> task_id from the most recent generate_audio call
        self._pending_tasks: dict[str, str] = {}

    async def session(self, user: User) -> "NotebookLMClientWrapper":
        """Return a user-scoped wrapper (used as async context manager)."""
        return NotebookLMClientWrapper(user.auth_file, self._max_retries, self._timeout)

    async def __aenter__(self) -> "NotebookLMClientWrapper":
        from notebooklm import NotebookLMClient

        path = str(self._auth_file) if self._auth_file else None
        self._client = await NotebookLMClient.from_storage(path, timeout=30.0)
        await self._client.__aenter__()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        if self._client:
            await self._client.__aexit__(exc_type, exc_val, exc_tb)
            self._client = None

    async def close(self) -> None:
        if self._client:
            await self._client.__aexit__(None, None, None)
            self._client = None

    async def create_notebook(self, title: str) -> Any:
        """Create a notebook; returns object with .id."""
        try:
            return await self._client.notebooks.create(title)
        except Exception as exc:
            raise NotebookLMError(f"Failed to create notebook: {exc}") from exc

    async def add_source(self, notebook_id: str, url: str) -> None:
        """Add a URL source to a notebook (waits for indexing)."""
        try:
            await self._client.sources.add_url(notebook_id, url, wait=True)
        except Exception as exc:
            raise NotebookLMError(f"Failed to add source: {exc}") from exc

    async def generate_audio(self, notebook_id: str, style: str = "deep-dive") -> None:
        """Kick off audio generation; task_id stored for wait_for_audio."""
        try:
            status = await self._client.artifacts.generate_audio(notebook_id)
            self._pending_tasks[notebook_id] = status.task_id
            logger.info("Audio generation started: notebook=%s task=%s", notebook_id, status.task_id)
        except Exception as exc:
            raise NotebookLMError(f"Failed to start audio generation: {exc}") from exc

    async def wait_for_audio(self, notebook_id: str, job_id: str, timeout: int | None = None) -> Artifact:
        """Poll until audio is ready; returns Artifact with id and notebook_id."""
        task_id = self._pending_tasks.pop(notebook_id, None)
        if not task_id:
            raise NotebookLMError(f"No pending audio task for notebook {notebook_id}")

        try:
            status = await self._client.artifacts.wait_for_completion(
                notebook_id,
                task_id,
                timeout=float(timeout or self._timeout),
            )
        except Exception as exc:
            raise NotebookLMError(f"Audio generation polling failed: {exc}") from exc

        if status.is_failed:
            raise NotebookLMError(f"Audio generation failed: {status.error}")

        return Artifact(id=status.task_id, notebook_id=notebook_id)

    async def download_audio(
        self, notebook_id: str, output_path: str, artifact_id: str | None = None
    ) -> str:
        """Download audio to output_path; returns the resolved path."""
        try:
            return await self._client.artifacts.download_audio(
                notebook_id, output_path, artifact_id=artifact_id
            )
        except Exception as exc:
            raise NotebookLMError(f"Failed to download audio: {exc}") from exc

    async def delete_notebook(self, notebook_id: str) -> None:
        """Delete a notebook (best-effort, logs on failure)."""
        try:
            await self._client.notebooks.delete(notebook_id)
        except Exception as exc:
            logger.warning("Failed to delete notebook %s: %s", notebook_id, exc)
