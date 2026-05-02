"""Configuration handler."""
from aiohttp import web

from notecast.infrastructure.config.settings import settings


async def handle_config(request: web.Request) -> web.Response:
    """Public configuration endpoint.
    
    Returns client-side configuration without sensitive data.
    
    Returns:
        JSON response with public configuration
    """
    config = {
        "base_url": settings.base_url,
        "poll_interval": settings.poll_interval,
        "bridge_port": settings.bridge_port,
        "feed_image_url": settings.feed_image_url,
        "token_expiry_warn_days": settings.token_expiry_warn_days,
        "generation_timeout": settings.generation_timeout,
    }
    return web.json_response(config)
