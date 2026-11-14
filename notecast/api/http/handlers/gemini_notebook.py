"""Authenticated HTTP routes for the Gemini Notebook API preview pilot."""
import logging
from urllib.parse import urlsplit
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
from aiohttp import web
from google.oauth2.credentials import Credentials

from notecast.core.exceptions import GeminiNotebookApiError
from notecast.infrastructure.external.gemini_notebook_api import GeminiNotebookApiClient
from notecast.infrastructure.external.gemini_notebook_credentials import (
    GeminiNotebookCredentialStore,
)

logger = logging.getLogger(__name__)
NOTEBOOK_SCOPE = "https://www.googleapis.com/auth/notebook"
USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"
TOKEN_URL = "https://oauth2.googleapis.com/token"


def _credential_store(request: web.Request) -> GeminiNotebookCredentialStore:
    settings = request.app["settings"]
    return GeminiNotebookCredentialStore(
        settings.data_base / "gemini_notebook_oauth",
        settings.google_client_id,
        settings.google_client_secret,
    )


async def get_gemini_notebook_oauth_status(request: web.Request) -> web.Response:
    """Report whether the current user has a saved background OAuth credential."""
    user = request["user"]
    connected = _credential_store(request).has_credentials(user)
    return web.json_response({
        "connected": connected,
        "email": user.email if connected else "",
    })


async def exchange_gemini_notebook_code(request: web.Request) -> web.Response:
    """Exchange a GIS authorization code and persist its refresh token."""
    settings = request.app["settings"]
    user = request["user"]
    if not settings.google_client_id or not settings.google_client_secret:
        raise web.HTTPServiceUnavailable(
            reason="Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET to enable background Notebook access"
        )

    origin = request.headers.get("Origin", "")
    configured_origin = ""
    if settings.base_url:
        parsed = urlsplit(settings.base_url)
        configured_origin = f"{parsed.scheme}://{parsed.netloc}"
    request_origin = f"{request.scheme}://{request.host}"
    allowed_origins = {request_origin}
    if configured_origin:
        allowed_origins.add(configured_origin)
    if origin not in allowed_origins or request.headers.get("X-Requested-With") != "XmlHttpRequest":
        raise web.HTTPForbidden(reason="Invalid OAuth code exchange origin")

    body = await _read_json(request)
    code = body.get("code")
    if not isinstance(code, str) or not code.strip():
        raise web.HTTPBadRequest(reason="Authorization code is required")
    requested_scopes = set(str(body.get("scope", "")).split())
    if NOTEBOOK_SCOPE not in requested_scopes:
        raise web.HTTPForbidden(reason="The Gemini Notebook permission was not granted")
    if not user.email:
        raise web.HTTPForbidden(reason="Set an email address on the NoteCast user first")

    # GIS popup code flow uses the authorized JavaScript origin as redirect_uri.
    redirect_uri = origin
    async with httpx.AsyncClient(timeout=15.0) as http:
        try:
            response = await http.post(TOKEN_URL, data={
                "code": code,
                "client_id": settings.google_client_id,
                "client_secret": settings.google_client_secret,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            })
            response.raise_for_status()
            tokens = response.json()
            identity = await http.get(
                USERINFO_URL,
                headers={"Authorization": f"Bearer {tokens.get('access_token', '')}"},
            )
            identity.raise_for_status()
            identity_json = identity.json()
        except (httpx.HTTPError, ValueError) as exc:
            detail = "Google OAuth code exchange failed"
            if isinstance(exc, httpx.HTTPStatusError):
                try:
                    detail = exc.response.json().get("error_description") or detail
                except ValueError:
                    pass
            raise web.HTTPBadGateway(reason=detail) from exc

    account_email = str(identity_json.get("email", "")).casefold()
    if not identity_json.get("email_verified") or account_email != user.email.casefold():
        raise web.HTTPForbidden(
            reason="Authorize the same verified Google account used to sign in to NoteCast"
        )
    granted_scope_value = tokens.get("scope")
    granted_scopes = set(str(granted_scope_value).split())
    if granted_scope_value and NOTEBOOK_SCOPE not in granted_scopes:
        raise web.HTTPForbidden(reason="The Gemini Notebook permission was not granted")

    credential_store = _credential_store(request)
    try:
        await credential_store.save_authorization(user, tokens)
    except GeminiNotebookApiError as exc:
        raise web.HTTPBadGateway(reason=str(exc)) from exc
    return web.json_response({
        "access_token": tokens["access_token"],
        "expires_in": tokens.get("expires_in", 3600),
        "email": identity_json["email"],
    })


async def list_gemini_notebooks(request: web.Request) -> web.Response:
    return await _run(request, lambda client: client.list_notebooks())


async def create_gemini_notebook(request: web.Request) -> web.Response:
    body = await _read_json(request)
    display_name = body.get("display_name", "")
    if not isinstance(display_name, str):
        raise web.HTTPBadRequest(text="display_name must be a string")
    display_name = display_name.strip()
    if not display_name:
        raise web.HTTPBadRequest(text="display_name is required")
    return await _run(request, lambda client: client.create_notebook(display_name))


async def get_gemini_notebook(request: web.Request) -> web.Response:
    notebook_name = f"notebooks/{request.match_info['notebook_id']}"
    return await _run(request, lambda client: client.get_notebook(notebook_name))


async def rename_gemini_notebook(request: web.Request) -> web.Response:
    body = await _read_json(request)
    display_name = body.get("display_name", "")
    if not isinstance(display_name, str):
        raise web.HTTPBadRequest(text="display_name must be a string")
    display_name = display_name.strip()
    if not display_name:
        raise web.HTTPBadRequest(text="display_name is required")
    notebook_name = f"notebooks/{request.match_info['notebook_id']}"
    return await _run(
        request, lambda client: client.rename_notebook(notebook_name, display_name)
    )


async def list_gemini_notebook_sources(request: web.Request) -> web.Response:
    notebook_name = f"notebooks/{request.match_info['notebook_id']}"
    return await _run(
        request,
        lambda client: client.list_sources(notebook_name),
        response_envelope="sources",
    )


async def create_gemini_notebook_source(request: web.Request) -> web.Response:
    body = await _read_json(request)
    notebook_name = f"notebooks/{request.match_info['notebook_id']}"
    display_name = body.get("display_name")
    url = body.get("url")
    content = body.get("content")
    if bool(url) == bool(content):
        raise web.HTTPBadRequest(text="Provide exactly one of url or content")
    if url is not None and not isinstance(url, str):
        raise web.HTTPBadRequest(text="url must be a string")
    if content is not None and not isinstance(content, str):
        raise web.HTTPBadRequest(text="content must be a string")
    if display_name is not None and not isinstance(display_name, str):
        raise web.HTTPBadRequest(text="display_name must be a string")

    if url:
        source = await _run(
            request,
            lambda client: client.create_url_source(
                notebook_name, url, display_name=display_name
            ),
        )
    else:
        source = await _run(
            request,
            lambda client: client.create_text_source(
                notebook_name,
                content,
                display_name=display_name,
                markdown=body.get("markdown") is True,
            ),
        )

    # Source creation is asynchronous in Google's API. Wait here so the pilot UI
    # can report a useful ACTIVE/FAILED result without a second polling workflow.
    source_name = source.get("name")
    if source_name:
        source = await _run(request, lambda client: client.wait_for_source(source_name))
    return web.json_response(source, status=201)


async def get_gemini_notebook_source(request: web.Request) -> web.Response:
    # The source resource name contains its parent notebook ID. Keep it intact in
    # the URL by accepting both components as route parameters.
    notebook_id = request.match_info["notebook_id"]
    source_id = request.match_info["source_id"]
    source_name = f"notebooks/{notebook_id}/sources/{source_id}"
    return await _run(request, lambda client: client.get_source(source_name))


async def _run(
    request: web.Request,
    operation: Callable[[GeminiNotebookApiClient], Awaitable[Any]],
    *,
    response_envelope: str | None = None,
) -> web.Response:
    settings = request.app["settings"]
    if not settings.google_client_id or not settings.gemini_notebook_project_id:
        raise web.HTTPServiceUnavailable(reason="Gemini Notebook API is not configured")

    token = request.headers.get("X-Gemini-Notebook-Access-Token", "").strip()
    if not token:
        raise web.HTTPUnauthorized(reason="Connect a Google account with the Notebook scope")

    # OAuth access tokens are short-lived and remain in browser memory. Resolve
    # the authorized Google identity on each request so one NoteCast user cannot
    # accidentally operate on a different Google account's notebooks.
    try:
        async with httpx.AsyncClient(timeout=10.0) as http:
            identity = await http.get(
                USERINFO_URL, headers={"Authorization": f"Bearer {token}"}
            )
            identity.raise_for_status()
            identity_json = identity.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise web.HTTPUnauthorized(
            reason="Google Notebook authorization expired or is invalid"
        ) from exc

    user = request.get("user")
    account_email = str(identity_json.get("email", "")).casefold()
    expected_email = str(getattr(user, "email", "")).casefold()
    if not identity_json.get("email_verified") or not account_email:
        raise web.HTTPForbidden(reason="Google did not confirm the Notebook account email")
    if not expected_email:
        raise web.HTTPForbidden(
            reason="Set an email address on the NoteCast user before connecting Notebook"
        )
    if account_email != expected_email:
        raise web.HTTPForbidden(
            reason="Connect the same Google account used to sign in to NoteCast"
        )

    credentials = Credentials(token=token, scopes=[NOTEBOOK_SCOPE])
    try:
        async with GeminiNotebookApiClient(
            credentials, settings.gemini_notebook_project_id
        ) as client:
            result = await operation(client)
    except ValueError as exc:
        raise web.HTTPBadRequest(text=str(exc)) from exc
    except Exception as exc:
        if isinstance(exc, GeminiNotebookApiError):
            logger.info("Gemini Notebook API request failed: %s", exc)
            raise web.HTTPBadGateway(reason=str(exc)) from exc
        raise
    if response_envelope:
        result = {response_envelope: result}
    return web.json_response(result)


async def _read_json(request: web.Request) -> dict[str, Any]:
    try:
        body = await request.json()
    except (ValueError, TypeError) as exc:
        raise web.HTTPBadRequest(text="Request body must be valid JSON") from exc
    if not isinstance(body, dict):
        raise web.HTTPBadRequest(text="Request body must be a JSON object")
    return body
