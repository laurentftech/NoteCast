"""Per-user configuration loading."""
import yaml

from notecast.core.models import Feed
from notecast.infrastructure.config.settings import settings as global_settings


def load_user_config(user) -> list[Feed]:
    """Load feed list from {config_dir}/{user.name}/transformer.yaml.

    Format:
        feeds:
          - name: tech-news
            url: https://example.com/feed.xml
            style: deep-dive
    """
    path = global_settings.config_dir / user.name / "transformer.yaml"
    try:
        raw = yaml.safe_load(path.read_text()) or {}
    except FileNotFoundError:
        return []
    return [Feed(**f) for f in raw.get("feeds", [])]
