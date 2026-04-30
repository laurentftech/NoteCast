"""Main entry point for the NoteCast application."""
import asyncio
import logging
from pathlib import Path

from notecast.infrastructure.config.settings import Settings
from notecast.infrastructure.database.sqlite_repository import SQLiteJobRepository
from notecast.infrastructure.external.feed_parser import fetch_episodes
from notecast.infrastructure.external.notebooklm_client import NotebookLMClientWrapper
from notecast.infrastructure.external.webhook_client import WebhookClient
from notecast.infrastructure.storage.file_storage import LocalFileStorage
from notecast.services.feed_service import FeedService
from notecast.services.harvester_service import HarvesterService
from notecast.services.job_service import JobService
from notecast.services.poller_service import PollerService
from notecast.services.user_service import UserService
from notecast.workers.harvester_worker import HarvesterWorker
from notecast.workers.transformer_worker import TransformerWorker

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


async def main():
    """Main application entry point."""
    # Load settings
    settings = Settings()
    logger.info("Settings loaded")

    # Infrastructure layer
    storage = LocalFileStorage(settings)
    nb_client = NotebookLMClientWrapper(
        max_retries=settings.max_retries,
        timeout=settings.generation_timeout,
    )
    webhook = WebhookClient(
        webhook_url=settings.webhook_url,
        webhook_headers=settings.webhook_headers,
    )

    # Services
    user_service = UserService(settings)

    # Repository factory - creates repository per user
    def repo_factory(user):
        return SQLiteJobRepository(user.db_file)

    feed_service = FeedService(
        repo_factory=repo_factory,  # Will be called with user
        storage=storage,
        settings=settings,
    )

    job_service = JobService(
        repo_factory=repo_factory,  # Will be called with user
        storage=storage,
        nb_client=nb_client,
        feed_service=feed_service,
    )

    poller_service = PollerService(
        repo_factory=repo_factory,  # Will be called with user
        user_service=user_service,
        settings=settings,
    )

    harvester_service = HarvesterService(
        nb_client=nb_client,
        storage=storage,
        webhook=webhook,
    )

    # Workers
    transformer = TransformerWorker(
        job_service=job_service,
        user_service=user_service,
        settings=settings,
    )

    harvester = HarvesterWorker(
        harvester_service=harvester_service,
        user_service=user_service,
    )

    # Initialize databases for all users
    users = await user_service.get_all()
    for user in users:
        try:
            repo = repo_factory(user)
            repo.init(user)
            logger.info(f"Initialized database for user {user.name}")
        except Exception as e:
            logger.error(f"Failed to initialize database for {user.name}: {e}")

    logger.info("NoteCast application started")
    logger.info(f"Managing {len(users)} users")

    # Run workers concurrently
    await asyncio.gather(
        transformer.run(),
        harvester.run(),
        return_exceptions=True,
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Application stopped by user")
    except Exception as e:
        logger.error(f"Application error: {e}", exc_info=True)
        raise
