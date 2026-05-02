"""Feeds handler - lists published RSS feeds."""
from aiohttp import web


async def handle_feeds(request: web.Request) -> web.Response:
    user = request.get("user")
    if not user:
        return web.json_response({"error": "Unauthorized"}, status=401)

    repo_factory = request.app["repo_factory"]
    settings = request.app["settings"]

    repo = repo_factory(user)
    done_jobs = repo.get_all_done_jobs(user)

    # Collect distinct feeds preserving order of most-recent episode
    seen = {}
    for job in done_jobs:  # already sorted newest-first
        if job.feed_name not in seen:
            seen[job.feed_name] = {"title": job.feed_title or job.feed_name, "count": 0}
        seen[job.feed_name]["count"] += 1

    base_url = settings.base_url.rstrip("/") if settings.base_url else ""
    feeds = []
    for name, meta in seen.items():
        feeds.append({
            "name": name,
            "title": meta["title"],
            "episode_count": meta["count"],
            "url": f"{base_url}/feed/{user.name}/{name}.xml?token={user.feed_token}",
        })

    return web.json_response(feeds)
