"""Poll handler."""
from aiohttp import web

from notecast.services.poller_service import PollerService
from notecast.services.user_service import UserService


async def handle_poll(request: web.Request) -> web.Response:
    """Poll for new episodes.
    
    Returns:
        JSON response with number of new jobs created
    """
    poller_service: PollerService = request.app["poller_service"]
    user_service: UserService = request.app["user_service"]

    # Get specific user if requested
    user_name = request.query.get("user")
    
    if user_name:
        user = await user_service.get_by_name(user_name)
        if not user:
            return web.json_response(
                {"error": f"User '{user_name}' not found"}, status=404
            )
        users = [user]
    else:
        users = await user_service.get_all()

    total_new_jobs = 0
    for user in users:
        config = {
            "storage": request.app["storage"],
            "job_service": request.app["job_service"],
        }
        new_jobs = await poller_service.poll_feeds(user, config)
        total_new_jobs += new_jobs

    return web.json_response({"queued": total_new_jobs})
