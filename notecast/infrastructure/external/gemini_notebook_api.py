"""REST client for the public Gemini Notebook API preview.

This adapter intentionally stays separate from the production NotebookLM
client: the public preview currently cannot generate or download audio
artifacts, and its notebook IDs have not yet been verified against the legacy
audio API.
"""
import asyncio
import time
from typing import Any

import httpx

from notecast.core.exceptions import GeminiNotebookApiError

API_BASE_URL = "https://notebook.googleapis.com/v1"
SOURCE_TERMINAL_STATES = {"ACTIVE", "FAILED", "DELETING"}


class GeminiNotebookApiClient:
    """Async REST adapter using Google OAuth credentials and a quota project.

    Pass a ``google.auth.credentials.Credentials`` instance authorized for
    ``https://www.googleapis.com/auth/notebook``. The caller owns credential
    acquisition and persistence; this client refreshes expired credentials.
    """

    def __init__(
        self,
        credentials: Any,
        project_id: str,
        *,
        base_url: str = API_BASE_URL,
        timeout: float = 30.0,
    ) -> None:
        if not project_id.strip():
            raise ValueError("A Google Cloud project ID is required")
        self._credentials = credentials
        self._project_id = project_id
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._http: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "GeminiNotebookApiClient":
        self._http = httpx.AsyncClient(timeout=self._timeout)
        return self

    async def __aexit__(self, exc_type, exc_value, traceback) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    async def create_notebook(self, display_name: str) -> dict[str, Any]:
        """Create a notebook and return its API resource representation."""
        return await self._request("POST", "/notebooks", json={"display_name": display_name})

    async def list_notebooks(self, *, page_size: int = 100) -> list[dict[str, Any]]:
        """List all notebooks, following API pagination tokens."""
        notebooks: list[dict[str, Any]] = []
        page_token: str | None = None
        while True:
            params: dict[str, Any] = {"pageSize": min(max(page_size, 1), 100)}
            if page_token:
                params["pageToken"] = page_token
            response = await self._request("GET", "/notebooks", params=params)
            notebooks.extend(response.get("notebooks", []))
            page_token = response.get("nextPageToken")
            if not page_token:
                return notebooks

    async def get_notebook(self, notebook_name: str) -> dict[str, Any]:
        """Fetch a notebook by resource name, such as ``notebooks/{id}``."""
        path = self._resource_path(notebook_name, source=False)
        return await self._request("GET", f"/{path}")

    async def rename_notebook(self, notebook_name: str, display_name: str) -> dict[str, Any]:
        """Rename a notebook using the API's display_name update mask."""
        path = self._resource_path(notebook_name, source=False)
        return await self._request(
            "PATCH",
            f"/{path}",
            params={"updateMask": "displayName"},
            json={"display_name": display_name},
        )

    async def create_url_source(
        self, notebook_name: str, url: str, *, display_name: str | None = None
    ) -> dict[str, Any]:
        """Add a public web page or supported YouTube URL to a notebook."""
        body: dict[str, Any] = {"web_content": {"url": url}}
        if display_name:
            body["display_name"] = display_name
        parent = self._resource_path(notebook_name, source=False)
        return await self._request(
            "POST", f"/{parent}/sources", json=body
        )

    async def create_text_source(
        self,
        notebook_name: str,
        content: str,
        *,
        display_name: str | None = None,
        markdown: bool = False,
    ) -> dict[str, Any]:
        """Add plain text or Markdown (up to 1 MB) as a source."""
        if not content.strip():
            raise ValueError("Text source content must not be empty")
        if len(content.encode("utf-8")) > 1_000_000 or len(content.split()) > 500_000:
            raise ValueError("Text source content exceeds the Gemini Notebook API limits")
        body: dict[str, Any] = {
            "mime_type": "text/markdown" if markdown else "text/plain",
            "text_content": {"content": content},
        }
        if display_name:
            body["display_name"] = display_name
        parent = self._resource_path(notebook_name, source=False)
        return await self._request(
            "POST", f"/{parent}/sources", json=body
        )

    async def list_sources(self, notebook_name: str) -> list[dict[str, Any]]:
        """List all sources in a notebook, following API pagination tokens."""
        sources: list[dict[str, Any]] = []
        page_token: str | None = None
        while True:
            params: dict[str, Any] = {"pageSize": 100}
            if page_token:
                params["pageToken"] = page_token
            parent = self._resource_path(notebook_name, source=False)
            response = await self._request(
                "GET", f"/{parent}/sources", params=params
            )
            sources.extend(response.get("sources", []))
            page_token = response.get("nextPageToken")
            if not page_token:
                return sources

    async def get_source(self, source_name: str) -> dict[str, Any]:
        """Fetch source metadata and processing state."""
        path = self._resource_path(source_name, source=True)
        return await self._request("GET", f"/{path}")

    async def wait_for_source(
        self,
        source_name: str,
        *,
        timeout: float = 300.0,
        initial_interval: float = 2.0,
        max_interval: float = 10.0,
    ) -> dict[str, Any]:
        """Poll until a source becomes ACTIVE or FAILED."""
        deadline = time.monotonic() + timeout
        interval = initial_interval
        while True:
            source = await self.get_source(source_name)
            state = source.get("state")
            if state in SOURCE_TERMINAL_STATES:
                if state != "ACTIVE":
                    error = source.get("error") or {}
                    detail = (
                        error.get("message", f"source entered {state} state")
                        if isinstance(error, dict)
                        else str(error)
                    )
                    raise GeminiNotebookApiError(f"Source processing failed: {detail}")
                return source
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise GeminiNotebookApiError(
                    f"Source {source_name} was still {state or 'processing'} after {timeout:g}s"
                )
            await asyncio.sleep(min(interval, remaining))
            interval = min(interval * 1.5, max_interval)

    async def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        if self._http is None:
            raise RuntimeError("Use GeminiNotebookApiClient as an async context manager")

        token = await self._get_access_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "x-goog-user-project": self._project_id,
        }
        try:
            response = await self._http.request(
                method, f"{self._base_url}{path}", headers=headers, **kwargs
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            try:
                detail = exc.response.json().get("error", {}).get("message")
            except (ValueError, AttributeError):
                detail = None
            detail = detail or exc.response.text or str(exc)
            raise GeminiNotebookApiError(
                f"Gemini Notebook API {method} {path} failed "
                f"with HTTP {exc.response.status_code}: {detail}"
            ) from exc
        except httpx.HTTPError as exc:
            raise GeminiNotebookApiError(f"Gemini Notebook API request failed: {exc}") from exc

        if response.status_code == httpx.codes.NO_CONTENT or not response.content:
            return {}
        try:
            return response.json()
        except ValueError as exc:
            raise GeminiNotebookApiError("Gemini Notebook API returned invalid JSON") from exc

    async def _get_access_token(self) -> str:
        if not getattr(self._credentials, "valid", False):
            try:
                from google.auth.transport.requests import Request as GoogleAuthRequest

                await asyncio.to_thread(self._credentials.refresh, GoogleAuthRequest())
            except Exception as exc:
                raise GeminiNotebookApiError(
                    f"Could not refresh Google OAuth credentials: {exc}"
                ) from exc
        token = getattr(self._credentials, "token", None)
        if not token:
            raise GeminiNotebookApiError("Google OAuth credentials did not provide an access token")
        return token

    @staticmethod
    def _resource_path(name: str, *, source: bool) -> str:
        """Validate and normalize a Notebook API resource name for its URL path."""
        path = name.strip("/")
        parts = path.split("/")
        expected_shape = (
            len(parts) == 4 and parts[0] == "notebooks" and parts[2] == "sources"
            if source
            else len(parts) == 2 and parts[0] == "notebooks"
        )
        if not expected_shape or any(not part or part in {".", ".."} for part in parts):
            raise ValueError(f"Invalid Gemini Notebook resource name: {name!r}")
        return path
