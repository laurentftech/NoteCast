"""Status handler."""
from aiohttp import web

from notecast.services.job_service import JobService
from notecast.services.user_service import UserService


async def handle_status(request: web.Request) -> web.Response:
    """Status endpoint.
    
    Returns:
        JSON response with system status
    """
    job_service: JobService = request.app["job_service"]
    user_service: UserService = request.app["user_service"]

    users = await user_service.get_all()

    status = {
        "users": len(users),
        "user_status": [],
    }

    for user in users:
        pending_jobs = await job_service.get_next_pending(user)
        has_pending = pending_jobs is not None

        status["user_status"].append(
            {
                "name": user.name,
                "has_pending_jobs": has_pending,
                "feed_token_configured": bool(user.feed_token),
            }
        )

    return web.json_response(status)
