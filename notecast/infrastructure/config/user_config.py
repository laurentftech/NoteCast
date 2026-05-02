"""Per-user configuration loading."""
import logging
import yaml

from notecast.core.models import Feed
from notecast.infrastructure.config.settings import settings as global_settings


logger = logging.getLogger(__name__)


def load_user_config(user) -> list[Feed]:
    """Load feed list from {config_dir}/{user.name}/transformer.yaml."""
    user_path = global_settings.config_dir / user.name / "transformer.yaml"
    shared_path = global_settings.config_dir / "transformer.yaml"
    path = user_path if user_path.exists() else shared_path
    try:
        raw = yaml.safe_load(path.read_text()) or {}
    except FileNotFoundError:
        logger.warning(
            "[%s] No transformer.yaml found (tried %s, %s)",
            user.name, user_path, shared_path,
        )
        return []
    feeds = [Feed(**f) for f in raw.get("feeds", [])]
    logger.info("[%s] Loaded %d feed(s) from %s", user.name, len(feeds), path)
    for feed in feeds:
        _warn_bad_url(feed)
    return feeds


def _warn_bad_url(feed: Feed) -> None:
    if "youtube.com/playlist?list=" in feed.url:
        playlist_id = feed.url.split("list=")[-1].split("&")[0]
        logger.warning(
            "Feed '%s': YouTube playlist URL is not RSS — use: "
            "https://www.youtube.com/feeds/videos.xml?playlist_id=%s",
            feed.name, playlist_id,
        )
    elif "youtube.com/watch" in feed.url or "youtu.be/" in feed.url:
        logger.warning(
            "Feed '%s': single YouTube video URL — use a channel or playlist feed URL instead",
            feed.name,
        )
