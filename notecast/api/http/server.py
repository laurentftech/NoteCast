"""HTTP server setup and configuration."""
import logging
from pathlib import Path

from aiohttp import web
from aiohttp.typedefs import Handler
from aiohttp.web_middlewares import middleware
from aiohttp.web_request import Request
from aiohttp.web_response import StreamResponse

from notecast.api.http.handlers.auth import handle_auth
from notecast.api.http.handlers.config import handle_config
from notecast.api.http.handlers.health import handle_health
from notecast.api.http.handlers.poll import handle_poll
from notecast.api.http.handlers.status import handle_status
from notecast.api.http.handlers.webhook import handle_webhook
from notecast.api.http.middleware import auth_middleware, error_middleware
from notecast.services.feed_service import FeedService
from notecast.services.harvester_service import HarvesterService
from notecast.services.job_service import JobService
from notecast.services.poller_service import PollerService
from notecast.services.user_service import UserService
from notecast.infrastructure.config.settings import Settings

logger = logging.getLogger(__name__)


@middleware
async def error_middleware_handler(request: Request, handler: Handler) -> StreamResponse:
    """Error handling middleware."""
    return await error_middleware(request, handler)


@middleware
async def auth_middleware_handler(request: Request, handler: Handler) -> StreamResponse:
    """Authentication middleware."""
    return await auth_middleware(request, handler)


def create_app(
    settings: Settings,
    job_service: JobService,
    feed_service: FeedService,
    poller_service: PollerService,
    user_service: UserService,
    storage,
    harvester_service: HarvesterService | None = None,
) -> web.Application:
    """Create and configure the aiohttp application.
    
    Args:
        settings: Application settings
        job_service: Job service instance
        feed_service: Feed service instance
        poller_service: Poller service instance
        user_service: User service instance
        storage: File storage instance
        
    Returns:
        Configured aiohttp application
    """
    app = web.Application(
        middlewares=[error_middleware_handler, auth_middleware_handler]
    )

    # Store services in app for access by handlers and middleware
    app["settings"] = settings
    app["job_service"] = job_service
    app["feed_service"] = feed_service
    app["poller_service"] = poller_service
    app["user_service"] = user_service
    app["storage"] = storage
    app["harvester_service"] = harvester_service

    # Configure routes
    app.router.add_get("/health", handle_health)
    app.router.add_get("/config", handle_config)
    app.router.add_get("/status", handle_status)
    app.router.add_post("/poll", handle_poll)
    app.router.add_post("/auth", handle_auth)
    app.router.add_post("/webhook", handle_webhook)

    # Static files
    static_path = Path(settings.public_dir)
    if static_path.exists():
        app.router.add_static("/", static_path, name="static")

    logger.info("HTTP server application created")
    return app
