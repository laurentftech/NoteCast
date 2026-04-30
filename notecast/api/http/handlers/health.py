"""Health check handler."""
from aiohttp import web


async def handle_health(request: web.Request) -> web.Response:
    """Health check endpoint.
    
    Returns:
        JSON response with status
    """
    return web.json_response({"status": "ok", "service": "notecast"})
