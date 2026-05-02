"""Handler for uploading NotebookLM storage_state credentials."""
import json
import logging

from aiohttp import web

logger = logging.getLogger(__name__)


async def handle_upload(request: web.Request) -> web.Response:
    user = request.get("user")
    if not user:
        return web.json_response({"error": "Unauthorized"}, status=401)

    reader = await request.multipart()
    field = await reader.next()
    if field is None or field.name != "file":
        return web.json_response({"error": "Missing file field"}, status=400)

    data = await field.read(decode=True)

    try:
        json.loads(data)
    except json.JSONDecodeError:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    user.auth_file.parent.mkdir(parents=True, exist_ok=True)
    user.auth_file.write_bytes(data)
    logger.info("Credentials updated for user %s", user.name)

    return web.json_response({"ok": True})
