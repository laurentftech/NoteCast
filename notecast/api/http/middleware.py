"""HTTP middleware for authentication and error handling."""
import logging
from typing import TYPE_CHECKING

from aiohttp import web
from aiohttp.typedefs import Handler
from aiohttp.web_request import Request
from aiohttp.web_response import StreamResponse

if TYPE_CHECKING:
    from notecast.core.models import User
    from notecast.services.user_service import UserService

logger = logging.getLogger(__name__)

_PUBLIC_API_ROUTES = {"/api/health", "/api/config"}


async def auth_middleware(
    request: Request, handler: Handler
) -> StreamResponse:
    # Static files and non-API routes pass through without auth
    if not request.path.startswith("/api/"):
        return await handler(request)

    if request.path in _PUBLIC_API_ROUTES:
        return await handler(request)

    user_service: "UserService" = request.app["user_service"]
    settings = request.app["settings"]

    # No Google auth configured → single-user mode, use first user
    if not settings.google_client_id:
        users = await user_service.get_all()
        if users:
            request["user"] = users[0]
            return await handler(request)
        return web.json_response({"error": "No users configured"}, status=503)

    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return web.json_response(
            {"error": "Missing or invalid Authorization header"}, status=401
        )

    token = auth_header[7:]
    user = await _validate_token(token, user_service, settings.google_client_id)
    if not user:
        return web.json_response({"error": "Invalid token"}, status=401)

    request["user"] = user
    return await handler(request)


async def error_middleware(
    request: Request, handler: Handler
) -> StreamResponse:
    try:
        response = await handler(request)
        return response
    except web.HTTPException as ex:
        logger.error(f"HTTP error: {ex.status} - {ex.reason}")
        return web.json_response({"error": ex.reason}, status=ex.status)
    except Exception as ex:
        logger.exception("Unhandled exception in request handler")
        return web.json_response(
            {"error": "Internal server error"}, status=500
        )


async def _validate_token(
    token: str, user_service: "UserService", google_client_id: str
) -> "User | None":
    # Try feed token first (fast, no network)
    users = await user_service.get_all()
    for user in users:
        if user.feed_token == token:
            return user

    # Try Google ID token
    if google_client_id:
        email = _verify_google_id_token(token, google_client_id)
        if email:
            return user_service.get_by_email(email)

    return None


def _verify_google_id_token(token: str, client_id: str) -> str | None:
    """Verify a Google ID token and return the associated email, or None."""
    try:
        from google.auth.transport import requests as google_requests
        from google.oauth2 import id_token

        request = google_requests.Request()
        payload = id_token.verify_oauth2_token(token, request, client_id)
        return payload.get("email")
    except Exception as exc:
        logger.debug("Google token verification failed: %s", exc)
        return None
