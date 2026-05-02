"""Episodes handler - returns done jobs as playable episodes."""
from aiohttp import web

from notecast.core.models import User
from notecast.infrastructure.config.settings import Settings


async def handle_episodes(request: web.Request) -> web.Response:
    user: User = request["user"]
    repo_factory = request.app["repo_factory"]
    settings: Settings = request.app["settings"]

    repo = repo_factory(user)
    jobs = repo.get_all_done_jobs(user)

    episodes = []
    for job in jobs:
        audio_url = (
            f"/episodes/{user.name}/{job.feed_name}/{job.artifact_id}.m4a"
            if job.artifact_id else None
        )
        if not audio_url:
            continue
        episodes.append({
            "id": job.id,
            "title": job.title,
            "url": audio_url,
            "notebook": job.feed_title or job.feed_name,
            "created_at": job.created_at.isoformat(),
            "duration": job.duration,
        })

    return web.json_response(episodes)
