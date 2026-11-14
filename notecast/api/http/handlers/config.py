"""Configuration handler."""
from aiohttp import web

from notecast.infrastructure.config.settings import settings as default_settings


async def handle_config(request: web.Request) -> web.Response:
    """Public configuration endpoint.
    
    Returns client-side configuration without sensitive data.
    
    Returns:
        JSON response with public configuration
    """
    settings = request.app.get("settings", default_settings)
    config = {
        "base_url": settings.base_url,
        "poll_interval": settings.poll_interval,
        "bridge_port": settings.bridge_port,
        "feed_image_url": settings.feed_image_url,
        "token_expiry_warn_days": settings.token_expiry_warn_days,
        "generation_timeout": settings.generation_timeout,
        "google_client_id": settings.google_client_id or None,
        "gemini_notebook_enabled": bool(
            settings.google_client_id and settings.gemini_notebook_project_id
        ),
        "gemini_notebook_background_auth_enabled": bool(
            settings.google_client_id and settings.google_client_secret
        ),
    }
    return web.json_response(config)
