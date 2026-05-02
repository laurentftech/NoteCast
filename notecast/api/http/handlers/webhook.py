"""Webhook handler."""
from aiohttp import web

from notecast.services.harvester_service import HarvesterService
from notecast.services.user_service import UserService


async def handle_webhook(request: web.Request) -> web.Response:
    """Handle incoming webhook notifications.
    
    Returns:
        JSON response acknowledging receipt
    """
    harvester_service: HarvesterService = request.app["harvester_service"]
    user_service: UserService = request.app["user_service"]

    data = await request.json()
    user_name = data.get("user")
    event_type = data.get("event")

    if not user_name:
        return web.json_response({"error": "Missing user"}, status=400)

    user = await user_service.get_by_name(user_name)
    if not user:
        return web.json_response(
            {"error": f"User '{user_name}' not found"}, status=404
        )

    # Process webhook based on event type
    if event_type == "artifact_ready":
        await harvester_service.harvest_user(user)

    return web.json_response({"status": "ok"})
