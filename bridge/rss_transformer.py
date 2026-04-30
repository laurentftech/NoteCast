# This is a shim file to re-export from the new notecast services.
from notecast.services.job_service import JobService
from notecast.services.feed_service import FeedService
from notecast.services.user_service import UserService
from notecast.infrastructure.external.notebooklm_client import NotebookLMClientWrapper
from notecast.infrastructure.storage.file_storage import LocalFileStorage
from notecast.infrastructure.config.settings import Settings
from notecast.core.models import User, Job, Episode, Artifact
from notecast.core.interfaces import JobRepository, FileStorage

# Re-exporting classes and functions that are expected by bridge/rss_transformer.py
# This will be refined as the refactoring progresses.

# Dummy/placeholder classes for initial import resolution
class NotebooksAPI:
    def add_source(self, *args, **kwargs):
        pass
    def generate_audio(self, *args, **kwargs):
        pass

# The functions below will eventually be replaced by calls to the new services.
# For now, these are minimal implementations to resolve import errors.

def process_job(*args, **kwargs):
    # Placeholder for the old process_job function
    pass

def rebuild_feed(*args, **kwargs):
    # Placeholder for the old rebuild_feed function
    pass

def get_duration(*args, **kwargs):
    # Placeholder for the old get_duration function
    return 0

# Expose necessary symbols for test_rss_e2e.py
# You will need to map these to the new architecture as you refactor the tests.
__all__ = [
    "JobService", "FeedService", "UserService", "NotebookLMClientWrapper",
    "LocalFileStorage", "Settings", "User", "Job", "Episode", "Artifact",
    "JobRepository", "FileStorage", "NotebooksAPI",
    "process_job", "rebuild_feed", "get_duration",
]
