"""Tests for ticket attachment extraction and aggregation."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mcp.server.fastmcp import Context


def _build_ctx() -> MagicMock:
    ctx = MagicMock(spec=Context)
    rc = MagicMock()
    rc.request = None
    ctx.request_context = rc
    return ctx


def test_extract_inline_image_urls_filters_only_freshdesk_hosts() -> None:
    from freshdesk_mcp.server import _extract_inline_image_urls

    html = (
        '<p>hello</p>'
        '<img src="https://attachment.freshdesk.com/inline/attachment?token=abc" />'
        '<img src="https://cdn.example.com/image.png" />'
        '<img src="/relative/image.png" />'
    )

    assert _extract_inline_image_urls(html) == [
        "https://attachment.freshdesk.com/inline/attachment?token=abc"
    ]


@pytest.mark.asyncio
async def test_download_attachment_content_skips_oversized_known_file() -> None:
    from freshdesk_mcp.server import MAX_ATTACHMENT_SIZE_BYTES, _download_attachment_content

    result = await _download_attachment_content(
        MagicMock(),
        "https://cdn.example.com/big.bin",
        source="ticket",
        attachment_type="file",
        name="big.bin",
        content_type="application/octet-stream",
        size=MAX_ATTACHMENT_SIZE_BYTES + 1,
    )

    assert result["source"] == "ticket"
    assert result["type"] == "file"
    assert result["name"] == "big.bin"
    assert "error" in result
    assert "20MB" in result["error"]
    assert "data_base64" not in result


@pytest.mark.asyncio
async def test_get_ticket_attachments_aggregates_ticket_and_conversation_assets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FRESHDESK_DOMAIN", "acme.freshdesk.com")
    monkeypatch.setenv("FRESHDESK_API_KEY", "fake")

    from freshdesk_mcp.server import get_ticket_attachments

    ctx = _build_ctx()

    ticket_payload = {
        "id": 123,
        "description": '<img src="https://attachment.freshdesk.com/inline/attachment?token=ticket-inline" />',
        "attachments": [
            {
                "name": "ticket.csv",
                "content_type": "text/csv",
                "size": 12,
                "attachment_url": "https://cdn.example.com/ticket.csv",
            }
        ],
    }
    first_page = [
        {
            "id": 1,
            "body": '<div><img src="https://attachment.freshdesk.com/inline/attachment?token=conv-inline" /></div>',
            "attachments": [
                {
                    "name": "reply.txt",
                    "content_type": "text/plain",
                    "size": 5,
                    "attachment_url": "https://cdn.example.com/reply.txt",
                }
            ],
        }
    ]
    second_page = [
        {
            "id": 2,
            "body": "<p>No images</p>",
            "attachments": [],
        }
    ]

    freshdesk_call_mock = AsyncMock(return_value=ticket_payload)
    freshdesk_exchange_mock = AsyncMock(
        side_effect=[
            (
                first_page,
                {"Link": '<https://acme.freshdesk.com/api/v2/tickets/123/conversations?page=2>; rel="next"'},
            ),
            (second_page, {"Link": ""}),
        ]
    )
    download_mock = AsyncMock(
        side_effect=[
            {
                "source": "ticket",
                "type": "file",
                "name": "ticket.csv",
                "content_type": "text/csv",
                "size": 12,
                "data_base64": "dGlja2V0",
            },
            {
                "source": "ticket",
                "type": "inline_image",
                "name": "inline_image_1.png",
                "content_type": "image/png",
                "size": 4,
                "data_base64": "aW1nMQ==",
            },
            {
                "source": "conversation",
                "type": "file",
                "name": "reply.txt",
                "content_type": "text/plain",
                "size": 5,
                "error": "Download failed with HTTP 500",
            },
            {
                "source": "conversation",
                "type": "inline_image",
                "name": "inline_image_2.jpg",
                "content_type": "image/jpeg",
                "size": 7,
                "data_base64": "aW1nMg==",
            },
        ]
    )

    with patch("freshdesk_mcp.server.httpx.AsyncClient") as client_cls, patch(
        "freshdesk_mcp.server.freshdesk_call", freshdesk_call_mock
    ), patch("freshdesk_mcp.server.freshdesk_exchange", freshdesk_exchange_mock), patch(
        "freshdesk_mcp.server._download_attachment_content", download_mock
    ):
        client_instance = MagicMock()
        client_instance.__aenter__.return_value = client_instance
        client_instance.__aexit__.return_value = False
        client_cls.return_value = client_instance

        result = await get_ticket_attachments(123, ctx)

    assert result["summary"] == "Trovati 2 allegati file e 2 immagini inline"
    assert len(result["attachments"]) == 4
    assert result["attachments"][0]["name"] == "ticket.csv"
    assert result["attachments"][1]["type"] == "inline_image"
    assert result["attachments"][2]["error"] == "Download failed with HTTP 500"
    assert result["attachments"][3]["name"] == "inline_image_2.jpg"
    assert freshdesk_call_mock.await_count == 1
    assert freshdesk_exchange_mock.await_count == 2
    assert download_mock.await_count == 4