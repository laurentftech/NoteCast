# This is a shim file to re-export from the new notecast services.
from notecast.services.harvester_service import HarvesterService
from notecast.services.user_service import UserService
from notecast.infrastructure.external.notebooklm_client import NotebookLMClientWrapper
from notecast.infrastructure.external.webhook_client import WebhookClient
from notecast.infrastructure.storage.file_storage import LocalFileStorage
from notecast.infrastructure.config.settings import Settings
from notecast.core.models import User, Job, Episode, Artifact
from notecast.core.interfaces import JobRepository, FileStorage

# Re-exporting classes and functions that are expected by bridge/harvester.py
# This will be refined as the refactoring progresses.

# The functions below will eventually be replaced by calls to the new services.
# For now, these are minimal implementations to resolve import errors.

def harvest_user(*args, **kwargs):
    # Placeholder for the old harvest_user function
    pass

def from_storage(*args, **kwargs):
    # Placeholder for the old from_storage function
    pass

__all__ = [
    "HarvesterService", "UserService", "NotebookLMClientWrapper", "WebhookClient",
    "LocalFileStorage", "Settings", "User", "Job", "Episode", "Artifact",
    "JobRepository", "FileStorage",
    "harvest_user", "from_storage",
]