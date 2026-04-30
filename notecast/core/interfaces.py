from abc import ABC, abstractmethod
from pathlib import Path
from typing import List, Optional, Any # Import Any

from notecast.core.models import User, Job, Episode, Artifact # Assuming these models exist

class JobRepository(ABC):
    @abstractmethod
    def init(self, user: User) -> None: ...
    @abstractmethod
    def create_job(self, user: User, episode: Episode) -> Job: ...
    @abstractmethod
    async def get_next_pending(self, user: User) -> Optional[Job]: ... # Made async
    @abstractmethod
    def update_job(self, user: User, job_id: str, **fields) -> None: ...
    @abstractmethod
    def get_done_jobs(self, user: User, feed_name: str) -> List[Job]: ...
    @abstractmethod
    def episode_seen(self, user: User, episode_url: str) -> bool: ...

class FileStorage(ABC):
    @abstractmethod
    def episode_path(self, user: User, feed_name: str, artifact_id: str) -> Path: ...
    @abstractmethod
    def feed_path(self, user: User, feed_name: str) -> Path: ...
    @abstractmethod
    def write_feed(self, user: User, feed_name: str, content: str) -> None: ...
    @abstractmethod
    def get_duration(self, path: Path) -> Optional[int]: ... # Added
    @abstractmethod
    async def download_and_remux(self, client: Any, user: User, feed_name: str, artifact: Artifact) -> Path: ... # Added and made async