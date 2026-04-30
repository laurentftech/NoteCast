"""Per-user configuration loading and validation."""
from pathlib import Path

import yaml

from notecast.core.models import Feed
from notecast.infrastructure.config.settings import settings


def load_user_config(user) -> list[Feed]:
    """Load per-user transformer.yaml and return validated Feed objects.
    
    Args:
        user: User object with name attribute
        
    Returns:
        List of validated Feed objects
        
    Raises:
        ConfigError: If config file is missing or invalid
    """
    path: Path = settings.data_base / user.name / "transformer.yaml"
    try:
        raw = yaml.safe_load(path.read_text())
    except FileNotFoundError:
        from notecast.core.exceptions import ConfigError
        raise ConfigError(f"Config file not found: {path}")
    except yaml.YAMLError as e:
        from notecast.core.exceptions import ConfigError
        raise ConfigError(f"Invalid YAML in {path}: {e}")
    
    feeds_data = raw.get("feeds", [])
    return [Feed(**f) for f in feeds_data]
