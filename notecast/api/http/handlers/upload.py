"""Handler for uploading NotebookLM storage_state credentials."""
import json
import logging

from aiohttp import web

logger = logging.getLogger(__name__)


async def handle_browser_cookies(request: web.Request) -> web.Response:
    user = request.get("user")
    if not user:
        return web.json_response({"error": "Unauthorized"}, status=401)

    try:
        body = await request.json()
        browser = body.get("browser", "chrome")
    except Exception:
        return web.json_response({"error": "Invalid JSON body"}, status=400)

    try:
        import rookiepy
        from notebooklm.auth import convert_rookiepy_cookies_to_storage_state
    except ImportError:
        return web.json_response(
            {"error": 'rookiepy not installed — add notebooklm-py[cookies] to dependencies'},
            status=400,
        )

    _BROWSER_NAMES = {"chrome", "chromium", "firefox", "brave", "edge", "safari", "opera", "vivaldi"}
    if browser not in _BROWSER_NAMES:
        return web.json_response({"error": f"Unsupported browser: {browser}"}, status=400)
    browser_fn = getattr(rookiepy, browser, None)
    if not browser_fn:
        return web.json_response({"error": f"Unsupported browser: {browser}"}, status=400)

    try:
        raw_cookies = browser_fn(domains=[".google.com", "google.com", ".notebooklm.google.com"])
        storage_state = convert_rookiepy_cookies_to_storage_state(raw_cookies)
        user.auth_file.parent.mkdir(parents=True, exist_ok=True)
        user.auth_file.write_text(json.dumps(storage_state))
        logger.info("Credentials updated for user %s via %s browser cookies", user.name, browser)
        return web.json_response({"ok": True})
    except Exception as exc:
        logger.warning("Browser cookie import failed for %s (%s): %s", user.name, browser, exc)
        return web.json_response({"error": str(exc)}, status=500)


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
