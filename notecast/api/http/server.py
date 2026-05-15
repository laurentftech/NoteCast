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
from notecast.api.http.handlers.feeds import handle_feeds
from notecast.api.http.handlers.health import handle_health
from notecast.api.http.handlers.poll import handle_poll
from notecast.api.http.handlers.status import handle_status
from notecast.api.http.handlers.transformer_config import handle_get_transformer_config, handle_put_transformer_config
from notecast.api.http.handlers.delete_episode import handle_delete_episode
from notecast.api.http.handlers.upload import handle_browser_cookies, handle_upload
from notecast.api.http.handlers.webhook import handle_webhook, handle_webhook_test
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
    app.router.add_delete("/api/episodes/{job_id}", handle_delete_episode)
    app.router.add_get("/api/feeds", handle_feeds)
    app.router.add_post("/api/poll", handle_poll)
    app.router.add_post("/api/auth", handle_auth)
    app.router.add_post("/api/auth/upload", handle_upload)
    app.router.add_post("/api/auth/browser-cookies", handle_browser_cookies)
    app.router.add_post("/api/webhook", handle_webhook)
    app.router.add_post("/api/webhook/test", handle_webhook_test)
    app.router.add_get("/api/transformer-config", handle_get_transformer_config)
    app.router.add_put("/api/transformer-config", handle_put_transformer_config)

    # Static files (index.html + audio episodes)
    static_path = Path(settings.public_dir)
    if static_path.exists():
        index = settings.index_html if settings.index_html.exists() else static_path / "index.html"
        app_js = settings.app_js if settings.app_js.exists() else static_path / "app.js"
        logger.info("Serving index.html from %s, app.js from %s", index, app_js)

        async def serve_index(_request: web.Request) -> web.StreamResponse:
            return web.FileResponse(index, headers={"Cache-Control": "no-cache"})

        async def serve_app_js(_request: web.Request) -> web.StreamResponse:
            return web.FileResponse(app_js, headers={"Cache-Control": "no-cache"})

        app.router.add_get("/", serve_index)
        app.router.add_get("/app.js", serve_app_js)
        app.router.add_static("/", static_path, name="static", follow_symlinks=True)

    logger.info("HTTP server application created")
    return app
