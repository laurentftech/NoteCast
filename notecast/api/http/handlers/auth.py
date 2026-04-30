"""Authentication handler."""
from typing import TYPE_CHECKING

from aiohttp import web

from notecast.services.user_service import UserService

if TYPE_CHECKING:
    from notecast.core.models import User


async def handle_auth(request: web.Request) -> web.Response:
    """Authenticate a user.

    Returns:
        JSON response with authentication status
    """
    user_service: UserService = request.app["user_service"]

    # Extract token from Authorization header
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return web.json_response(
            {"error": "Missing or invalid Authorization header"}, status=401
        )

    token = auth_header[7:]  # Remove "Bearer " prefix

    # Validate token (placeholder - implement actual validation)
    user = await _validate_token(token, user_service)
    if not user:
        return web.json_response({"error": "Invalid token"}, status=401)

    return web.json_response(
        {
            "authenticated": True,
            "user": user.name,
            "email": user.email,
        }
    )


async def _validate_token(token: str, user_service: UserService) -> "User | None":
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
