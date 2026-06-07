"""Status handler."""
import logging
import os
from aiohttp import web

from notecast.core.auth_utils import auth_expires_in_days

logger = logging.getLogger(__name__)


async def handle_status(request: web.Request) -> web.Response:
    user = request.get("user")
    if not user:
        return web.json_response({"error": "Unauthorized"}, status=401)

    repo_factory = request.app["repo_factory"]
    settings = request.app["settings"]

    repo = repo_factory(user)
    done_jobs = repo.get_all_done_jobs(user)
    last_updated = done_jobs[0].created_at.isoformat() if done_jobs else None
    queue = repo.get_queue_counts(user)
    active_jobs = repo.get_active_jobs(user) if hasattr(repo, "get_active_jobs") else []

    feed_url = (
        f"{settings.base_url}/feed/{user.feed_token}.xml"
        if settings.users
        else f"{settings.base_url}/feed.xml"
    )

    # Check webhook: per-user first, fallback to global
    webhook_enabled = bool(user.webhook_url) if user else bool(settings.webhook_url)
    logger.info("Webhook status - user: %s, per-user: %s, global: %s, enabled: %s",
                user.name if user else "none",
                bool(user.webhook_url) if user else False,
                bool(settings.webhook_url),
                webhook_enabled)

    payload = {
        "episodes": len(done_jobs),
        "pending": queue["pending"],
        "generating": queue["generating"],
        "queue_jobs": [
            {
                "id": j.id,
                "title": j.title,
                "feed_name": j.feed_name,
                "status": j.status,
                "created_at": j.created_at.isoformat(),
                "updated_at": j.updated_at.isoformat(),
            }
            for j in active_jobs
        ],
        "next_poll_in": None,
        "last_updated": last_updated,
        "feed_url": feed_url,
        "feed_token": user.feed_token,
        "webhook_enabled": webhook_enabled,
        "version": os.environ.get("APP_VERSION", "dev"),
    }

    expires = auth_expires_in_days(user)
    if expires is not None:
        payload["token_expires_in_days"] = expires

    return web.json_response(payload)
