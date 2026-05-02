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
    return feeds
