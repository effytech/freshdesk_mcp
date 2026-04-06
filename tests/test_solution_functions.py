import unittest
from unittest.mock import patch, MagicMock, AsyncMock
import json
import os
import sys
import asyncio

# Add the parent directory to the path so we can import the module
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import src.freshdesk_mcp.server as server_module
from src.freshdesk_mcp.server import (
    delete_solution_article,
    delete_solution_folder,
    delete_solution_category,
    search_solution_articles,
)


def _make_async_client_mock(mock_response):
    """Return an AsyncClient context-manager mock that yields mock_response."""
    mock_client = AsyncMock()
    mock_client.delete = AsyncMock(return_value=mock_response)
    mock_client.get = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    return mock_client


class TestDeleteSolutionArticle(unittest.TestCase):
    def setUp(self):
        server_module.FRESHDESK_DOMAIN = "testdomain.freshdesk.com"
        server_module.FRESHDESK_API_KEY = "test-api-key"

    def test_delete_returns_success_on_204(self):
        mock_response = MagicMock()
        mock_response.status_code = 204
        mock_response.raise_for_status = MagicMock()

        mock_client = _make_async_client_mock(mock_response)
        with patch("src.freshdesk_mcp.server.httpx.AsyncClient", return_value=mock_client):
            result = asyncio.run(delete_solution_article(42))

        self.assertTrue(result.get("success"))
        self.assertIn("message", result)

    def test_delete_propagates_http_error(self):
        import httpx

        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_response.raise_for_status = MagicMock(
            side_effect=httpx.HTTPStatusError(
                "Not Found", request=MagicMock(), response=mock_response
            )
        )

        mock_client = _make_async_client_mock(mock_response)
        with patch("src.freshdesk_mcp.server.httpx.AsyncClient", return_value=mock_client):
            result = asyncio.run(delete_solution_article(999))

        self.assertIn("error", result)


class TestDeleteSolutionFolder(unittest.TestCase):
    def setUp(self):
        server_module.FRESHDESK_DOMAIN = "testdomain.freshdesk.com"
        server_module.FRESHDESK_API_KEY = "test-api-key"

    def test_delete_returns_success_on_204(self):
        mock_response = MagicMock()
        mock_response.status_code = 204
        mock_response.raise_for_status = MagicMock()

        mock_client = _make_async_client_mock(mock_response)
        with patch("src.freshdesk_mcp.server.httpx.AsyncClient", return_value=mock_client):
            result = asyncio.run(delete_solution_folder(10))

        self.assertTrue(result.get("success"))
        self.assertIn("message", result)

    def test_delete_propagates_http_error(self):
        import httpx

        mock_response = MagicMock()
        mock_response.status_code = 403
        mock_response.raise_for_status = MagicMock(
            side_effect=httpx.HTTPStatusError(
                "Forbidden", request=MagicMock(), response=mock_response
            )
        )

        mock_client = _make_async_client_mock(mock_response)
        with patch("src.freshdesk_mcp.server.httpx.AsyncClient", return_value=mock_client):
            result = asyncio.run(delete_solution_folder(999))

        self.assertIn("error", result)


class TestDeleteSolutionCategory(unittest.TestCase):
    def setUp(self):
        server_module.FRESHDESK_DOMAIN = "testdomain.freshdesk.com"
        server_module.FRESHDESK_API_KEY = "test-api-key"

    def test_delete_returns_success_on_204(self):
        mock_response = MagicMock()
        mock_response.status_code = 204
        mock_response.raise_for_status = MagicMock()

        mock_client = _make_async_client_mock(mock_response)
        with patch("src.freshdesk_mcp.server.httpx.AsyncClient", return_value=mock_client):
            result = asyncio.run(delete_solution_category(5))

        self.assertTrue(result.get("success"))
        self.assertIn("message", result)

    def test_delete_propagates_http_error(self):
        import httpx

        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.raise_for_status = MagicMock(
            side_effect=httpx.HTTPStatusError(
                "Server Error", request=MagicMock(), response=mock_response
            )
        )

        mock_client = _make_async_client_mock(mock_response)
        with patch("src.freshdesk_mcp.server.httpx.AsyncClient", return_value=mock_client):
            result = asyncio.run(delete_solution_category(999))

        self.assertIn("error", result)


class TestSearchSolutionArticles(unittest.TestCase):
    def setUp(self):
        server_module.FRESHDESK_DOMAIN = "testdomain.freshdesk.com"
        server_module.FRESHDESK_API_KEY = "test-api-key"

    def test_search_returns_articles_and_pagination_envelope(self):
        sample_articles = [{"id": 1, "title": "How to reset password"}]
        link_header = (
            '<https://testdomain.freshdesk.com/api/v2/solutions/articles/search?page=2>; rel="next"'
        )

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.headers = {"Link": link_header}
        mock_response.json = MagicMock(return_value=sample_articles)

        mock_client = _make_async_client_mock(mock_response)
        with patch("src.freshdesk_mcp.server.httpx.AsyncClient", return_value=mock_client):
            result = asyncio.run(search_solution_articles(term="reset password", page=1, per_page=10))

        self.assertIn("articles", result)
        self.assertIn("pagination", result)
        self.assertEqual(result["articles"], sample_articles)
        self.assertEqual(result["pagination"]["current_page"], 1)
        self.assertEqual(result["pagination"]["next_page"], 2)
        self.assertIsNone(result["pagination"]["prev_page"])
        self.assertEqual(result["pagination"]["per_page"], 10)

    def test_search_pagination_with_prev_and_next(self):
        link_header = (
            '<https://testdomain.freshdesk.com/api/v2/solutions/articles/search?page=3>; rel="next", '
            '<https://testdomain.freshdesk.com/api/v2/solutions/articles/search?page=1>; rel="prev"'
        )

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.headers = {"Link": link_header}
        mock_response.json = MagicMock(return_value=[])

        mock_client = _make_async_client_mock(mock_response)
        with patch("src.freshdesk_mcp.server.httpx.AsyncClient", return_value=mock_client):
            result = asyncio.run(search_solution_articles(term="test", page=2, per_page=30))

        self.assertEqual(result["pagination"]["current_page"], 2)
        self.assertEqual(result["pagination"]["next_page"], 3)
        self.assertEqual(result["pagination"]["prev_page"], 1)

    def test_search_validates_page_bounds(self):
        result = asyncio.run(search_solution_articles(term="test", page=0))
        self.assertIn("error", result)

    def test_search_validates_per_page_bounds(self):
        result = asyncio.run(search_solution_articles(term="test", per_page=101))
        self.assertIn("error", result)

    def test_search_propagates_http_error(self):
        import httpx

        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_response.raise_for_status = MagicMock(
            side_effect=httpx.HTTPStatusError(
                "Unauthorized", request=MagicMock(), response=mock_response
            )
        )
        mock_response.headers = {}

        mock_client = _make_async_client_mock(mock_response)
        with patch("src.freshdesk_mcp.server.httpx.AsyncClient", return_value=mock_client):
            result = asyncio.run(search_solution_articles(term="test"))

        self.assertIn("error", result)


if __name__ == "__main__":
    unittest.main()
