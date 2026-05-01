"""Main entry point for the NoteCast application."""
import asyncio
import logging
import signal
from pathlib import Path

from aiohttp import web

from notecast.api.http.server import create_app
from notecast.infrastructure.config.settings import Settings
from notecast.infrastructure.database.sqlite_repository import SQLiteJobRepository
from notecast.infrastructure.external.notebooklm_client import NotebookLMClientWrapper
from notecast.infrastructure.external.webhook_client import WebhookClient
from notecast.infrastructure.storage.file_storage import LocalFileStorage
from notecast.services.feed_service import FeedService
from notecast.services.harvester_service import HarvesterService
from notecast.services.job_service import JobService
from notecast.services.poller_service import PollerService
from notecast.services.user_service import UserService
from notecast.workers.harvester_worker import HarvesterWorker
from notecast.workers.poller_worker import PollerWorker
from notecast.workers.transformer_worker import TransformerWorker

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


async def main():
    import os
    version = os.getenv("APP_VERSION", "dev")
    logger.info("NoteCast %s starting", version)

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
        repo_factory=repo_factory,
        storage=storage,
        feed_service=feed_service,
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

    poller = PollerWorker(
        poller_service=poller_service,
        user_service=user_service,
        job_service=job_service,
        settings=settings,
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

    # HTTP server
    app = create_app(
        settings=settings,
        job_service=job_service,
        feed_service=feed_service,
        poller_service=poller_service,
        user_service=user_service,
        storage=storage,
        harvester_service=harvester_service,
    )
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", settings.bridge_port)
    await site.start()
    logger.info(f"HTTP server listening on port {settings.bridge_port}")

    logger.info("NoteCast application started")
    logger.info(f"Managing {len(users)} users")

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            pass

    worker_tasks = [
        asyncio.create_task(transformer.run(), name="transformer"),
        asyncio.create_task(harvester.run(), name="harvester"),
        asyncio.create_task(poller.run(), name="poller"),
    ]

    await stop_event.wait()
    logger.info("Shutdown signal received")

    for task in worker_tasks:
        task.cancel()
    await asyncio.gather(*worker_tasks, return_exceptions=True)
    await runner.cleanup()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Application stopped by user")
    except Exception as e:
        logger.error(f"Application error: {e}", exc_info=True)
        raise
