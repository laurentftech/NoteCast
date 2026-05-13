"""Webhook handler."""
import logging
from aiohttp import web

from notecast.services.harvester_service import HarvesterService
from notecast.services.user_service import UserService

logger = logging.getLogger(__name__)


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


async def handle_webhook_test(request: web.Request) -> web.Response:
    """Test webhook configuration by sending a sample notification.
    
    Returns:
        JSON response with test result
    """
    user = request.get("user")
    if not user:
        return web.json_response({"error": "Unauthorized"}, status=401)

    harvester_service: HarvesterService = request.app["harvester_service"]
    webhook_client = request.app.get("webhook_client")
    
    if not webhook_client and not user.webhook_url:
        return web.json_response(
            {"error": "No webhook configured for this user"}, status=400
        )

    try:
        # Use per-user webhook if available, otherwise global
        client = webhook_client if webhook_client else (
            await harvester_service._get_webhook_client(user)
        )
        
        if not client:
            return web.json_response(
                {"error": "Failed to initialize webhook client"}, status=500
            )

        # Send test notification using direct post method
        await client.post(
            user,
            title="Webhook Test Notification",
            message="This is a test notification from NoteCast"
        )
        
        logger.info("Webhook test sent successfully for user %s", user.name)
        return web.json_response({
            "status": "ok",
            "message": "Test notification sent successfully"
        })
    except Exception as exc:
        logger.error("Webhook test failed for user %s: %s", user.name, exc)
        return web.json_response(
            {"error": f"Test failed: {str(exc)}"},
            status=500
        )
