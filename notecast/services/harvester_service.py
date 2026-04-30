"""Harvester service - downloads artifacts from NotebookLM."""
from typing import Optional

from notecast.core.models import User, Artifact
from notecast.infrastructure.external.notebooklm_client import NotebookLMClientWrapper
from notecast.infrastructure.external.webhook_client import WebhookClient
from notecast.infrastructure.storage.file_storage import LocalFileStorage


class HarvesterService:
    """Service for harvesting artifacts from NotebookLM."""

    def __init__(
        self,
        nb_client: NotebookLMClientWrapper,
        storage: LocalFileStorage,
        webhook: Optional[WebhookClient] = None,
    ):
        self._nb_client = nb_client
        self._storage = storage
        self._webhook = webhook

    async def harvest_user(self, user: User) -> None:
        """Harvest all artifacts for a user.
        
        Args:
            user: User to harvest artifacts for
        """
        # This would typically query NotebookLM for all artifacts
        # For now, this is a placeholder for the harvesting logic
        pass

    async def download_artifact(
        self, client, artifact_id: str, user: User
    ) -> Optional[Artifact]:
        """Download a single artifact.
        
        Args:
            client: NotebookLM client
            artifact_id: Artifact identifier
            user: User who owns the artifact
            
        Returns:
            Downloaded artifact, or None if download failed
        """
        try:
            # Download audio data
            audio_data = await client.download_audio(artifact_id)
            
            # Create artifact object
            artifact = Artifact(
                id=artifact_id,
                notebook_id=f"notebook_for_{artifact_id}",
                local_path=None,
                duration=None,
            )
            
            # Notify via webhook if configured
            if self._webhook:
                await self._webhook.notify_job_completed(
                    user, artifact_id, "harvest"
                )
            
            return artifact
            
        except Exception as e:
            if self._webhook:
                await self._webhook.notify_job_failed(
                    user, artifact_id, "harvest", str(e)
                )
            return None

    async def process_artifact(
        self, user: User, feed_name: str, artifact_id: str
    ) -> Optional[str]:
        """Process a single artifact.
        
        Args:
            user: User who owns the artifact
            feed_name: Feed name
            artifact_id: Artifact identifier
            
        Returns:
            Path to processed artifact, or None if processing failed
        """
        try:
            # Get episode path
            episode_path = self._storage.episode_path(
                user, feed_name, artifact_id
            )
            
            # Ensure directory exists
            episode_path.parent.mkdir(parents=True, exist_ok=True)
            
            return str(episode_path)
            
        except Exception as e:
            print(f"Failed to process artifact {artifact_id}: {e}")
            return None
