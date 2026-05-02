"""Status handler."""
import json
import math
import time
from aiohttp import web


async def handle_status(request: web.Request) -> web.Response:
    user = request.get("user")
    if not user:
        return web.json_response({"error": "Unauthorized"}, status=401)

    repo_factory = request.app["repo_factory"]
    settings = request.app["settings"]

    repo = repo_factory(user)
    done_jobs = repo.get_all_done_jobs(user)
    last_updated = done_jobs[0].created_at.isoformat() if done_jobs else None

    feed_url = (
        f"{settings.base_url}/feed/{user.feed_token}.xml"
        if settings.users
        else f"{settings.base_url}/feed.xml"
    )

    payload = {
        "episodes": len(done_jobs),
        "next_poll_in": None,
        "last_updated": last_updated,
        "feed_url": feed_url,
        "webhook_enabled": bool(settings.webhook_url),
        "version": None,
    }

    expires = _auth_expires_in_days(user)
    if expires is not None:
        payload["token_expires_in_days"] = expires

    return web.json_response(payload)


def _auth_expires_in_days(user) -> "int | None":
    if not user.auth_file.exists():
        return None
    try:
        data = json.loads(user.auth_file.read_bytes())
        expiries = [c["expires"] for c in data.get("cookies", []) if c.get("expires", -1) > 0]
        if not expiries:
            return None
        return math.floor((min(expiries) - time.time()) / 86400)
    except Exception:
        return None
