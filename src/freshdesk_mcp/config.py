"""Per-request Freshdesk configuration (query string + env fallback)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Dict, Union
from urllib.parse import urlparse


@dataclass(frozen=True)
class FreshdeskConfig:
    domain: str
    api_key: str
    tickets_read_only: bool


def parse_bool_param(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return str(value).lower().strip() in ("true", "1", "yes")


def normalize_freshdesk_domain(raw: str | None) -> tuple[str | None, str | None]:
    """Return (hostname, error_message). Host is lowercase, no scheme or path."""
    if not raw or not str(raw).strip():
        return None, "freshdesk_domain is required"
    s = str(raw).strip()
    if "://" in s:
        parsed = urlparse(s if "://" in s else f"https://{s}")
        host = (parsed.hostname or "").lower()
    else:
        host = s.split("/")[0].strip().lower()
    if not host:
        return None, "Invalid freshdesk_domain"
    if not host.endswith(".freshdesk.com"):
        return None, "freshdesk_domain must be a *.freshdesk.com host"
    return host, None


def resolve_freshdesk_config(
    *,
    query_params: Any | None,
    env_fallback: bool = True,
) -> Union[FreshdeskConfig, Dict[str, Any]]:
    """Build config from HTTP query params with optional env fallback (local stdio)."""
    env_ro = parse_bool_param(os.getenv("FRESHDESK_TICKETS_READ_ONLY"), default=False)

    domain: str | None = None
    api_key: str | None = None
    tickets_read_only = env_ro

    if query_params is not None:
        d = query_params.get("freshdesk_domain")
        k = query_params.get("freshdesk_api_key")
        domain = (d or "").strip() or None
        api_key = (k or "").strip() or None
        ro_q = query_params.get("freshdesk_tickets_read_only")
        tickets_read_only = parse_bool_param(str(ro_q) if ro_q is not None else None, default=env_ro)

    if env_fallback:
        if not domain:
            ev = os.getenv("FRESHDESK_DOMAIN")
            if ev:
                domain = ev.strip()
        if not api_key:
            ek = os.getenv("FRESHDESK_API_KEY")
            if ek:
                api_key = ek.strip()

    host, derr = normalize_freshdesk_domain(domain)
    if derr:
        return {"error": derr}
    if not api_key:
        return {"error": "freshdesk_api_key is required"}

    return FreshdeskConfig(domain=host, api_key=api_key, tickets_read_only=tickets_read_only)
