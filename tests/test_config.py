"""Unit tests for Freshdesk config resolution."""

from __future__ import annotations

import os
from unittest.mock import MagicMock

import pytest
from starlette.datastructures import QueryParams

from freshdesk_mcp.config import (
    FreshdeskConfig,
    normalize_freshdesk_domain,
    parse_bool_param,
    resolve_freshdesk_config,
)
from freshdesk_mcp.freshdesk_client import basic_auth_header


def test_normalize_domain_host_only() -> None:
    host, err = normalize_freshdesk_domain("acme.freshdesk.com")
    assert err is None
    assert host == "acme.freshdesk.com"


def test_normalize_domain_with_scheme() -> None:
    host, err = normalize_freshdesk_domain("https://acme.freshdesk.com/path")
    assert err is None
    assert host == "acme.freshdesk.com"


def test_normalize_domain_rejects_non_freshdesk() -> None:
    host, err = normalize_freshdesk_domain("evil.com")
    assert host is None
    assert err is not None


def test_resolve_missing_domain() -> None:
    out = resolve_freshdesk_config(query_params=QueryParams(""), env_fallback=False)
    assert isinstance(out, dict) and "error" in out


def test_resolve_missing_api_key() -> None:
    out = resolve_freshdesk_config(
        query_params=QueryParams("freshdesk_domain=acme.freshdesk.com"),
        env_fallback=False,
    )
    assert isinstance(out, dict) and out.get("error")


def test_resolve_full_query(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FRESHDESK_TICKETS_READ_ONLY", raising=False)
    qp = QueryParams(
        "freshdesk_domain=acme.freshdesk.com&freshdesk_api_key=secret&freshdesk_tickets_read_only=true"
    )
    out = resolve_freshdesk_config(query_params=qp, env_fallback=False)
    assert isinstance(out, FreshdeskConfig)
    assert out.domain == "acme.freshdesk.com"
    assert out.api_key == "secret"
    assert out.tickets_read_only is True


def test_tickets_read_only_bool_variants(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FRESHDESK_TICKETS_READ_ONLY", raising=False)
    for raw, expected in [("1", True), ("yes", True), ("false", False), ("0", False)]:
        qp = QueryParams(
            f"freshdesk_domain=t.freshdesk.com&freshdesk_api_key=k&freshdesk_tickets_read_only={raw}"
        )
        out = resolve_freshdesk_config(query_params=qp, env_fallback=False)
        assert isinstance(out, FreshdeskConfig)
        assert out.tickets_read_only is expected


def test_env_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FRESHDESK_DOMAIN", "envco.freshdesk.com")
    monkeypatch.setenv("FRESHDESK_API_KEY", "envkey")
    monkeypatch.setenv("FRESHDESK_TICKETS_READ_ONLY", "false")
    out = resolve_freshdesk_config(query_params=None, env_fallback=True)
    assert isinstance(out, FreshdeskConfig)
    assert out.domain == "envco.freshdesk.com"
    assert out.api_key == "envkey"


def test_basic_auth_header_value() -> None:
    h = basic_auth_header("mykey")
    assert h["Authorization"].startswith("Basic ")


def test_parse_bool_param() -> None:
    assert parse_bool_param(None) is False
    assert parse_bool_param("true") is True
    assert parse_bool_param("YES") is True
