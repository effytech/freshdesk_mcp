"""Integration-style tests with mocked Freshdesk HTTP (no real API)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mcp.server.fastmcp import Context


@pytest.mark.asyncio
async def test_get_ticket_fields_returns_freshdesk_error_dict(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FRESHDESK_DOMAIN", "acme.freshdesk.com")
    monkeypatch.setenv("FRESHDESK_API_KEY", "fake")

    ctx = MagicMock(spec=Context)
    rc = MagicMock()
    rc.request = None
    ctx.request_context = rc

    from freshdesk_mcp.server import get_ticket_fields

    with patch(
        "freshdesk_mcp.server.freshdesk_call",
        new_callable=AsyncMock,
        return_value={"error": "Freshdesk API HTTP 401", "status_code": 401},
    ):
        out = await get_ticket_fields(ctx)

    assert isinstance(out, dict)
    assert out.get("error")
