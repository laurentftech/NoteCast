"""User service for loading and managing users from configuration."""
import json
import logging
import secrets
from pathlib import Path
from typing import Dict, List, Optional

from notecast.core.models import User
from notecast.infrastructure.config.settings import Settings, get_env_or_default

logger = logging.getLogger(__name__)

_DEFAULT_AUTH_FILE = Path('/root/.notebooklm/storage_state.json')


class UserService:
    def __init__(self, settings: Settings):
        self._settings = settings
        self._users_cache: Optional[List[User]] = None

    async def get_all(self) -> List[User]:
        """Get all configured users.
        
        Returns:
            List of User objects from USERS config variable
        """
        if self._users_cache is None:
            self._users_cache = self._build_users()
        return self._users_cache

    def get_by_email(self, email: str) -> Optional[User]:
        """Get a user by email address.
        
        Args:
            email: User email to search for
            
        Returns:
            User object if found, None otherwise
        """
        for user in self._users_cache or []:
            if user.email == email:
                return user
        return None

    async def get_by_name(self, name: str) -> Optional[User]:
        """Get a user by name.
        
        Args:
            name: User name to search for
            
        Returns:
            User object if found, None otherwise
        """
        users = await self.get_all()
        for user in users:
            if user.name == name:
                return user
        return None

    def get_default(self) -> User:
        """Get the default user (for single-user backward compat).
        
        Returns:
            First user in list or raises ValueError if no users configured
        """
        if not self._users_cache:
            self._users_cache = self._build_users()
        if not self._users_cache:
            raise ValueError("No users configured in USERS environment variable")
        return self._users_cache[0]

    def _build_users(self) -> List[User]:
        """Build User objects from environment configuration.
        
        Returns:
            List of configured User objects
        """
        names_raw = self._settings.users
        names = [n.strip() for n in names_raw.split(',') if n.strip()]

        if not names:
            # Single-user backward compat — existing paths, no auth required
            logger.info("No USERS configured, using single-user mode")
            token = self._load_or_generate_feed_token(
                self._settings.data_base / '.feed_token'
            )
            return [User(
                name='default',
                email='',
                auth_file=_DEFAULT_AUTH_FILE,
                db_file=self._settings.data_base / 'jobs.db',
                history_file=self._settings.data_base / 'history.json',
                episodes_dir=self._settings.public_dir / 'episodes' / 'default',
                feed_dir=self._settings.public_dir / 'feed',
                feed_token=token,
                webhook_url=self._settings.webhook_url,
                webhook_headers=self._settings.webhook_headers,
                webhook_link=self._settings.webhook_link,
            )]

        users = []
        for name in names:
            key = name.upper()
            email = self._get_env_or_default(f'USER_{key}_EMAIL', '')
            token = self._load_or_generate_feed_token(
                self._settings.data_base / f'{name}/.feed_token'
            )
            # Per-user webhook, fallback to global
            wh_url = self._get_env_or_default(
                f'USER_{key}_WEBHOOK_URL',
                self._settings.webhook_url
            )
            wh_headers = self._parse_webhook_headers(
                self._get_env_or_default(
                    f'USER_{key}_WEBHOOK_HEADERS',
                    json.dumps(self._settings.webhook_headers)
                )
            )
            wh_link = self._get_env_or_default(
                f'USER_{key}_WEBHOOK_LINK',
                self._settings.webhook_link
            )
            users.append(User(
                name=name,
                email=email,
                auth_file=_DEFAULT_AUTH_FILE.parent / name / 'storage_state.json',
                db_file=self._settings.data_base / f'{name}/jobs.db',
                history_file=self._settings.data_base / f'{name}/history.json',
                episodes_dir=self._settings.public_dir / 'episodes' / name,
                feed_dir=self._settings.public_dir / 'feed',
                feed_token=token,
                webhook_url=wh_url,
                webhook_headers=wh_headers,
                webhook_link=wh_link,
            ))
            logger.info(f"Loaded user: {name} ({email})")

        return users

    @staticmethod
    def _load_or_generate_feed_token(token_file: Path) -> str:
        """Load existing feed token or generate a new one.
        
        Args:
            token_file: Path to token file
            
        Returns:
            Feed token string
        """
        token_file.parent.mkdir(parents=True, exist_ok=True)
        if token_file.exists():
            return token_file.read_text().strip()
        token = secrets.token_urlsafe(24)
        token_file.write_text(token)
        logger.info(f"Generated new feed token: {token_file}")
        return token

    @staticmethod
    def _parse_webhook_headers(raw: str) -> Dict:
        """Parse webhook headers from JSON string.
        
        Args:
            raw: JSON string with headers
            
        Returns:
            Dictionary of headers
        """
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("WEBHOOK_HEADERS is not valid JSON, ignoring")
            return {}

    @staticmethod
    def _get_env_or_default(key: str, default: str) -> str:
        return get_env_or_default(key, default)