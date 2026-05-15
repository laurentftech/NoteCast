"""Handler for reading and writing transformer.yaml via the admin UI."""
import logging
from aiohttp import web
from notecast.core.models import Feed
from notecast.infrastructure.config.user_config import load_user_config, save_user_config

logger = logging.getLogger(__name__)


async def handle_get_transformer_config(request: web.Request) -> web.Response:
    user = request.get("user")
    if not user:
        return web.json_response({"error": "Unauthorized"}, status=401)
    try:
        feeds = load_user_config(user)
    except Exception as exc:
        logger.error("[%s] Failed to load transformer config: %s", user.name, exc)
        return web.json_response({"error": f"Failed to load: {exc}"}, status=500)
    return web.json_response([f.model_dump() for f in feeds])


async def handle_put_transformer_config(request: web.Request) -> web.Response:
    user = request.get("user")
    if not user:
        return web.json_response({"error": "Unauthorized"}, status=401)
    try:
        body = await request.json()
        if not isinstance(body, list):
            return web.json_response({"error": "Expected a JSON array"}, status=400)
        feeds = [Feed(**item) for item in body]
    except Exception as exc:
        return web.json_response({"error": f"Invalid payload: {exc}"}, status=400)
    try:
        save_user_config(user, feeds)
    except Exception as exc:
        logger.error("[%s] Failed to save transformer config: %s", user.name, exc)
        return web.json_response({"error": f"Failed to save: {exc}"}, status=500)
    logger.info("[%s] transformer.yaml updated via admin UI (%d feeds)", user.name, len(feeds))
    return web.json_response({"ok": True})