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
    from urllib.parse import urlparse, parse_qs
    parsed = urlparse(feed.url)
    host = parsed.netloc.lower()
    path = parsed.path
    qs = parse_qs(parsed.query)
    is_youtube = host in ("youtube.com", "www.youtube.com") or host.endswith(".youtube.com")
    if is_youtube and "list" in qs and "/feeds/" not in path:
        playlist_id = qs["list"][0]
        logger.warning(
            "Feed '%s': YouTube playlist URL is not RSS — use: "
            "https://www.youtube.com/feeds/videos.xml?playlist_id=%s",
            feed.name, playlist_id,
        )
    elif is_youtube and path.startswith("/watch"):
        logger.warning(
            "Feed '%s': single YouTube video URL — use a channel or playlist feed URL instead",
            feed.name,
        )
