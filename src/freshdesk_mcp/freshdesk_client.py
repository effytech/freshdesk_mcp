"""HTTP client helpers for Freshdesk API (timeouts, auth, uniform errors)."""

from __future__ import annotations

import base64
from typing import Any, Dict, Optional

import httpx

from freshdesk_mcp.config import FreshdeskConfig

TIMEOUT = httpx.Timeout(30.0, connect=10.0)


def basic_auth_header(api_key: str) -> Dict[str, str]:
    token = base64.b64encode(f"{api_key}:X".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def freshdesk_headers(api_key: str, *, json_body: bool = False) -> Dict[str, str]:
    h = basic_auth_header(api_key)
    if json_body:
        h["Content-Type"] = "application/json"
    return h


def freshdesk_url(domain: str, path: str) -> str:
    p = path if path.startswith("/") else f"/{path}"
    return f"https://{domain}{p}"


def _error_payload(status_code: int, body: Any) -> Dict[str, Any]:
    if isinstance(body, dict):
        if "errors" in body:
            return {"error": f"Freshdesk API error ({status_code}): {body.get('errors')}", "status_code": status_code, "details": body}
        if "message" in body:
            return {"error": f"Freshdesk API error ({status_code}): {body.get('message')}", "status_code": status_code, "details": body}
    return {"error": f"Freshdesk API HTTP {status_code}", "status_code": status_code, "details": body}


async def freshdesk_exchange(
    client: httpx.AsyncClient,
    config: FreshdeskConfig,
    method: str,
    path: str,
    *,
    params: Optional[Dict[str, Any]] = None,
    json: Any = None,
) -> tuple[Any, httpx.Headers]:
    """Like freshdesk_call but always returns (body_or_error_dict, response_headers)."""
    url = freshdesk_url(config.domain, path)
    use_json = json is not None
    headers = freshdesk_headers(config.api_key, json_body=use_json)
    try:
        response = await client.request(method, url, headers=headers, params=params, json=json)
        rh = response.headers
        if response.status_code == 204:
            return ({"success": True, "message": "No content"}, rh)

        try:
            body = response.json()
        except Exception:
            body = {"raw": (response.text or "")[:2000]}

        if response.is_error:
            return (_error_payload(response.status_code, body), rh)

        return (body, rh)
    except httpx.TimeoutException as e:
        return ({"error": f"Request timeout: {e}"}, httpx.Headers())
    except httpx.RequestError as e:
        return ({"error": f"Network error: {e}"}, httpx.Headers())


async def freshdesk_call(
    client: httpx.AsyncClient,
    config: FreshdeskConfig,
    method: str,
    path: str,
    *,
    params: Optional[Dict[str, Any]] = None,
    json: Any = None,
) -> Any:
    """
    Perform one Freshdesk request. On failure returns dict with 'error'.
    On success returns parsed JSON (dict or list), or {'success': True} for 204.
    """
    url = freshdesk_url(config.domain, path)
    use_json = json is not None
    headers = freshdesk_headers(config.api_key, json_body=use_json)
    try:
        response = await client.request(method, url, headers=headers, params=params, json=json)
        if response.status_code == 204:
            return {"success": True, "message": "No content"}

        try:
            body = response.json()
        except Exception:
            body = {"raw": (response.text or "")[:2000]}

        if response.is_error:
            return _error_payload(response.status_code, body)

        return body
    except httpx.TimeoutException as e:
        return {"error": f"Request timeout: {e}"}
    except httpx.RequestError as e:
        return {"error": f"Network error: {e}"}
