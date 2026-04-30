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


async def auth_middleware(
    request: Request, handler: Handler
) -> StreamResponse:
    """Authentication middleware.

    Validates authentication tokens for protected routes.
    Public routes (like health checks) are exempt.
    """
    # Skip auth for public routes
    if request.path in ["/health", "/config"]:
        return await handler(request)

    # Extract token from Authorization header
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return web.json_response(
            {"error": "Missing or invalid Authorization header"}, status=401
        )

    token = auth_header[7:]  # Remove "Bearer " prefix
    user_service: "UserService" = request.app["user_service"]

    # Validate token
    user = await _validate_token(token, user_service)
    if not user:
        return web.json_response({"error": "Invalid token"}, status=401)

    # Attach user to request
    request["user"] = user
    return await handler(request)


async def error_middleware(
    request: Request, handler: Handler
) -> StreamResponse:
    """Error handling middleware.

    Catches exceptions and returns appropriate error responses.
    """
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


async def _validate_token(token: str, user_service: "UserService") -> "User | None":
    """Validate authentication token.

    Args:
        token: Authentication token
        user_service: User service instance

    Returns:
        User if token is valid, None otherwise
    """
    # Placeholder for actual token validation logic
    # In production, this would validate against Google OAuth tokens
    users = await user_service.get_all()
    for user in users:
        if user.feed_token == token:
            return user
    return None
