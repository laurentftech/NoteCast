"""Authentication handler."""
from aiohttp import web


async def handle_auth(request: web.Request) -> web.Response:
    user = request.get("user")
    if not user:
        return web.json_response({"error": "Unauthorized"}, status=401)

    return web.json_response(
        {
            "authenticated": True,
            "user": user.name,
            "email": user.email,
        }
    )
