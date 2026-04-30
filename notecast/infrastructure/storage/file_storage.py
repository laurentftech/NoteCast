from pathlib import Path
import subprocess
import logging
from typing import Optional

from notecast.core.interfaces import FileStorage
from notecast.infrastructure.config.settings import Settings
from notecast.core.models import User, Artifact
from notecast.infrastructure.external.notebooklm_client import NotebookLMClientWrapper # For type hinting client

logger = logging.getLogger(__name__)

class LocalFileStorage(FileStorage):
    def __init__(self, settings: Settings):
        self._settings = settings

    def episode_path(self, user: User, feed_name: str, artifact_id: str) -> Path:
        # Placeholder implementation
        return self._settings.public_dir / "episodes" / user.name / feed_name / f"{artifact_id}.m4a"

    def feed_path(self, user: User, feed_name: str) -> Path:
        # Placeholder implementation
        return self._settings.public_dir / "feed" / user.name / f"{feed_name}.xml"

    def write_feed(self, user: User, feed_name: str, content: str) -> None:
        path = self.feed_path(user, feed_name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)

    async def download_and_remux(self, client: NotebookLMClientWrapper, user: User, feed_name: str, artifact: Artifact) -> Path:
        # Placeholder implementation
        audio_bytes = await client.download_audio(artifact.id)
        
        # Create temp file, remux, then move to final destination
        temp_dir = Path("/tmp") # Placeholder for actual temp directory management
        temp_file = temp_dir / f"{artifact.id}_temp.mp3"
        temp_file.write_bytes(audio_bytes)

        output_path = self.episode_path(user, feed_name, artifact.id)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        self.remux_to_m4a(temp_file, output_path)

        temp_file.unlink() # Clean up temp file
        return output_path

    def remux_to_m4a(self, src: Path, dest: Path) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(src), "-c", "copy", str(dest)],
            check=True, capture_output=True,
        )

    def get_duration(self, path: Path) -> Optional[int]:
        """Get audio duration in seconds using ffprobe.
        
        Args:
            path: Path to audio file
            
        Returns:
            Duration in seconds, or None if extraction fails
        """
        try:
            result = subprocess.run(
                [
                    "ffprobe", "-v", "error",
                    "-show_entries", "format=duration",
                    "-of", "default=noprint_wrappers=1:nokey=1:noprint_names=1",
                    str(path)
                ],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0 and result.stdout.strip():
                return int(float(result.stdout.strip()))
        except (subprocess.TimeoutExpired, FileNotFoundError, ValueError) as e:
            logger.warning(f"Failed to get duration for {path}: {e}")
        return None