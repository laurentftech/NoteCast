"""Per-user OAuth credentials for background Gemini Notebook API calls."""
import asyncio
import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2.credentials import Credentials

from notecast.core.models import User
from notecast.core.exceptions import GeminiNotebookApiError

NOTEBOOK_SCOPE = "https://www.googleapis.com/auth/notebook"


class GeminiNotebookCredentialStore:
    """Persist refreshable OAuth credentials in the private per-user data dir."""

    def __init__(self, storage_dir: Path, client_id: str, client_secret: str) -> None:
        self._storage_dir = storage_dir
        self._client_id = client_id
        self._client_secret = client_secret

    def path_for(self, user: User) -> Path:
        return self._storage_dir / user.name / "credentials.json"

    def has_credentials(self, user: User) -> bool:
        """Return whether this user has completed the Notebook OAuth connection."""
        return self.path_for(user).is_file()

    async def save_authorization(self, user: User, token_response: dict[str, Any]) -> None:
        refresh_token = token_response.get("refresh_token")
        access_token = token_response.get("access_token")
        if not refresh_token or not access_token:
            raise GeminiNotebookApiError(
                "Google did not return a refresh token. Reconnect and grant offline access."
            )
        credentials = {
            "token": access_token,
            "refresh_token": refresh_token,
            "token_uri": "https://oauth2.googleapis.com/token",
            "scopes": [NOTEBOOK_SCOPE],
            "expiry": (
                datetime.now(timezone.utc)
                + timedelta(seconds=max(int(token_response.get("expires_in", 3600)) - 60, 60))
            ).replace(tzinfo=None).isoformat() + "Z",
        }
        await asyncio.to_thread(
            self._write_credentials,
            self.path_for(user),
            {"email": user.email, "credentials": credentials},
        )

    async def get_credentials(self, user: User) -> Credentials:
        path = self.path_for(user)
        try:
            saved = await asyncio.to_thread(self._read_credentials, path)
            if str(saved.get("email", "")).casefold() != user.email.casefold():
                raise GeminiNotebookApiError(
                    "The saved Gemini Notebook authorization belongs to another Google account"
                )
            credential_info = dict(saved["credentials"])
            credential_info["client_id"] = self._client_id
            credential_info["client_secret"] = self._client_secret
            credentials = Credentials.from_authorized_user_info(
                credential_info, scopes=[NOTEBOOK_SCOPE]
            )
            if not credentials.valid:
                await asyncio.to_thread(credentials.refresh, GoogleAuthRequest())
                refreshed_info = json.loads(credentials.to_json())
                refreshed_info.pop("client_id", None)
                refreshed_info.pop("client_secret", None)
                await asyncio.to_thread(
                    self._write_credentials,
                    path,
                    {"email": user.email, "credentials": refreshed_info},
                )
            if not credentials.token:
                raise GeminiNotebookApiError("Google did not provide an access token")
            return credentials
        except GeminiNotebookApiError:
            raise
        except Exception as exc:
            raise GeminiNotebookApiError(
                "No valid background Gemini Notebook authorization is saved for this user"
            ) from exc

    @staticmethod
    def _read_credentials(path: Path) -> dict[str, Any]:
        return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def _write_credentials(path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(path.parent, 0o700)
        fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as target:
                json.dump(payload, target)
                target.flush()
                os.fsync(target.fileno())
            os.replace(temp_name, path)
            os.chmod(path, 0o600)
        finally:
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass
