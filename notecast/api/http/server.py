"""HTTP server setup and configuration."""
import logging
from pathlib import Path
from typing import Callable

from aiohttp import web
from aiohttp.typedefs import Handler
from aiohttp.web_middlewares import middleware
from aiohttp.web_request import Request
from aiohttp.web_response import StreamResponse

from notecast.api.http.handlers.auth import handle_auth
from notecast.api.http.handlers.config import handle_config
from notecast.api.http.handlers.episodes import handle_episodes
from notecast.api.http.handlers.health import handle_health
from notecast.api.http.handlers.poll import handle_poll
from notecast.api.http.handlers.status import handle_status
from notecast.api.http.handlers.webhook import handle_webhook
from notecast.api.http.middleware import auth_middleware, error_middleware
from notecast.infrastructure.config.settings import Settings
from notecast.services.feed_service import FeedService
from notecast.services.harvester_service import HarvesterService
from notecast.services.job_service import JobService
from notecast.services.poller_service import PollerService
from notecast.services.user_service import UserService

logger = logging.getLogger(__name__)


@middleware
async def error_middleware_handler(request: Request, handler: Handler) -> StreamResponse:
    return await error_middleware(request, handler)


@middleware
async def auth_middleware_handler(request: Request, handler: Handler) -> StreamResponse:
    return await auth_middleware(request, handler)


async def handle_webhook_test(request: web.Request) -> web.Response:
    return web.json_response({"status": "ok", "message": "webhook test not configured"})


def create_app(
    settings: Settings,
    job_service: JobService,
    feed_service: FeedService,
    poller_service: PollerService,
    user_service: UserService,
    storage,
    harvester_service: HarvesterService | None = None,
    repo_factory: Callable | None = None,
) -> web.Application:
    app = web.Application(
        middlewares=[error_middleware_handler, auth_middleware_handler]
    )

    app["settings"] = settings
    app["job_service"] = job_service
    app["feed_service"] = feed_service
    app["poller_service"] = poller_service
    app["user_service"] = user_service
    app["storage"] = storage
    app["harvester_service"] = harvester_service
    app["repo_factory"] = repo_factory

    # Public routes (no /api prefix, no auth)
    app.router.add_get("/api/health", handle_health)
    app.router.add_get("/api/config", handle_config)

    # Authenticated API routes
    app.router.add_get("/api/status", handle_status)
    app.router.add_get("/api/episodes", handle_episodes)
    app.router.add_post("/api/poll", handle_poll)
    app.router.add_post("/api/auth", handle_auth)
    app.router.add_post("/api/webhook", handle_webhook)
    app.router.add_post("/api/webhook/test", handle_webhook_test)

    # Static files (index.html + audio episodes)
    static_path = Path(settings.public_dir)
    if static_path.exists():
        index = static_path / "index.html"

        async def serve_index(request: web.Request) -> web.Response:
            return web.FileResponse(index)

        app.router.add_get("/", serve_index)
        app.router.add_static("/", static_path, name="static", follow_symlinks=True)

    logger.info("HTTP server application created")
    return app
