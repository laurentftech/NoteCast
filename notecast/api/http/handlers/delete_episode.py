"""Handler for deleting an episode."""
import logging

from aiohttp import web

logger = logging.getLogger(__name__)


async def handle_delete_episode(request: web.Request) -> web.Response:
    user = request["user"]
    job_id = request.match_info["job_id"]

    repo_factory = request.app["repo_factory"]
    storage = request.app["storage"]
    feed_service = request.app.get("feed_service")

    repo = repo_factory(user)
    job = repo.get_job(user, job_id)
    if not job:
        return web.json_response({"error": "Episode not found"}, status=404)

    if job.artifact_id:
        audio_path = storage.episode_path(user, job.feed_name, job.artifact_id)
        audio_path.unlink(missing_ok=True)

    repo.update_job(user, job_id, status="deleted")
    logger.info("Deleted episode %s (%s) for user %s", job_id, job.title, user.name)

    if feed_service:
        try:
            await feed_service.rebuild_feed(user, job.feed_name, job.feed_title)
        except Exception as exc:
            logger.warning("Feed rebuild failed after delete: %s", exc)

    return web.json_response({"ok": True})
