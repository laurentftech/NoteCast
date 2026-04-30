from pydantic_settings import BaseSettings, SettingsConfigDict
from pathlib import Path
from typing import Any, Dict

class Settings(BaseSettings):
    base_url: str = "" # Default to empty string for now, will be populated from .env
    poll_interval: int = 86400
    retention_days: int = 14
    bridge_port: int = 8080
    bridge_api_key: str = ""
    data_base: Path = Path("/data")
    public_dir: Path = Path("./public")
    webhook_url: str = ""
    webhook_headers: Dict = {}
    webhook_link: str = ""
    users: str = ""                     # comma-separated names
    google_client_id: str = ""
    token_expiry_warn_days: int = 7
    feed_image_url: str = ""
    generation_timeout: int = 2700      # 45 min
    max_retries: int = 1

    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=False,
        extra="ignore"  # Ignore extra env vars not defined in the model
    )

# Singleton — import this everywhere instead of os.getenv()
settings = Settings()