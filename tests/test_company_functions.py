"""Tests for shared helpers and mocked company-style responses."""

from __future__ import annotations

import unittest

import pytest

from freshdesk_mcp.server import parse_link_header


class TestParseHeaderFunction(unittest.TestCase):
    def test_parse_link_header(self) -> None:
        header = (
            '<https://example.com/page=2>; rel="next", '
            '<https://example.com/page=1>; rel="prev"'
        )
        result = parse_link_header(header)
        self.assertEqual(result.get("next"), 2)
        self.assertEqual(result.get("prev"), 1)

    def test_parse_link_header_empty(self) -> None:
        result = parse_link_header("")
        self.assertEqual(result, {"next": None, "prev": None})

    def test_parse_link_header_invalid_format(self) -> None:
        result = parse_link_header("invalid format")
        self.assertEqual(result, {"next": None, "prev": None})


async def mock_list_companies(page: int = 1, per_page: int = 30) -> dict:
    companies = [
        {
            "id": 51000641139,
            "name": "Herbert Smith Freehills",
            "domains": ["herbertsmithfreehills.com"],
        },
        {
            "id": 51000979809,
            "name": "Another Company",
            "domains": [],
        },
    ]
    pagination_info = {"next": 2 if page < 3 else None, "prev": page - 1 if page > 1 else None}
    return {
        "companies": companies,
        "pagination": {
            "current_page": page,
            "next_page": pagination_info.get("next"),
            "prev_page": pagination_info.get("prev"),
            "per_page": per_page,
        },
    }


@pytest.mark.asyncio
async def test_mock_list_companies() -> None:
    result = await mock_list_companies(page=1, per_page=10)
    assert "companies" in result
    assert len(result["companies"]) == 2
    assert result["companies"][0]["name"] == "Herbert Smith Freehills"
    assert "pagination" in result


if __name__ == "__main__":
    unittest.main(verbosity=2)
