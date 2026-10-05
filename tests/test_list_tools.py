import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
from mcp.server.mcpserver.exceptions import ToolError, UnexpectedToolError
from mcp.types import CallToolRequestParams

from freshdesk_mcp import server


TOOLS = ("get_ticket_fields", "get_agents", "list_groups")


class TestListTools(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.client = MagicMock()
        self.client.get = AsyncMock()
        context = AsyncMock()
        context.__aenter__.return_value = self.client
        self.patchers = [
            patch.object(server.httpx, "AsyncClient", return_value=context),
            patch.object(server, "FRESHDESK_DOMAIN", "example.invalid"),
            patch.object(server, "FRESHDESK_API_KEY", "placeholder"),
        ]
        for patcher in self.patchers:
            patcher.start()
            self.addCleanup(patcher.stop)

    def response(self, status, payload):
        return httpx.Response(
            status,
            json=payload,
            request=httpx.Request("GET", "https://example.invalid/api/v2/list"),
        )

    async def test_successful_arrays_pass_real_mcp_output_conversion(self):
        for name in TOOLS:
            with self.subTest(tool=name):
                self.client.get.return_value = self.response(200, [{"name": "synthetic-record"}])
                result = await server.mcp.call_tool(name, {})
                self.assertIn("synthetic-record", str(result))
                self.client.get.assert_awaited()

    async def test_http_errors_are_actionable_tool_errors_and_server_recovers(self):
        for name in TOOLS:
            for status in (401, 403, 429, 500):
                with self.subTest(tool=name, status=status):
                    self.client.get.return_value = self.response(status, {
                        "code": "access_denied",
                        "description": "synthetic private response must not reach the model",
                    })
                    with self.assertLogs(level="ERROR") as logged:
                        with self.assertRaises(ToolError) as raised:
                            await server.mcp.call_tool(name, {})
                    self.assertNotIsInstance(raised.exception, UnexpectedToolError)
                    message = str(raised.exception)
                    self.assertIn(f"HTTP {status}", message)
                    self.assertNotIn("synthetic private response", message)
                    self.assertNotIn("example.invalid", message)
                    self.assertIn("HTTPStatusError", "\n".join(logged.output))
                    self.assertIsInstance(raised.exception.__cause__, ToolError)
                    self.assertIsInstance(raised.exception.__cause__.__cause__, httpx.HTTPStatusError)
                    if status in (401, 403):
                        self.assertIn("authorized agent role", message)
                    self.client.get.return_value = self.response(200, [])
                    await server.mcp.call_tool(name, {})

    async def test_invalid_pagination_refuses_before_http_without_list_validation_crashes(self):
        for name in ("get_agents", "list_groups"):
            for arguments in ({"page": 0}, {"page": None}, {"per_page": 0}, {"per_page": 101}, {"per_page": None}):
                with self.subTest(tool=name, arguments=arguments):
                    self.client.get.reset_mock()
                    with self.assertRaises(ToolError) as raised:
                        await server.mcp.call_tool(name, arguments)
                    self.assertNotIsInstance(raised.exception, UnexpectedToolError)
                    self.assertIn(next(iter(arguments)), str(raised.exception))
                    self.client.get.assert_not_awaited()

    async def test_real_sdk_error_envelope_and_subsequent_success(self):
        for name in TOOLS:
            with self.subTest(tool=name):
                self.client.get.return_value = self.response(403, {"description": "synthetic private body"})
                with self.assertLogs(level="ERROR"):
                    failure = await server.mcp._handle_call_tool(None, CallToolRequestParams(name=name, arguments={}))
                self.assertTrue(failure.is_error)
                text = "".join(part.text for part in failure.content)
                self.assertIn("HTTP 403", text)
                self.assertIn("authorized agent role", text)
                self.assertNotIn("synthetic private body", text)
                self.client.get.return_value = self.response(200, [{"name": "synthetic-record"}])
                accepted = await server.mcp._handle_call_tool(None, CallToolRequestParams(name=name, arguments={}))
                self.assertFalse(accepted.is_error)
                self.assertIn("synthetic-record", "".join(part.text for part in accepted.content))

    async def test_defaults_and_valid_pagination_are_preserved(self):
        for name in ("get_agents", "list_groups"):
            for arguments, expected in (({}, {"page": 1, "per_page": 30}), ({"page": 2, "per_page": 100}, {"page": 2, "per_page": 100})):
                with self.subTest(tool=name, arguments=arguments):
                    self.client.get.return_value = self.response(200, [])
                    await server.mcp.call_tool(name, arguments)
                    self.assertEqual(self.client.get.call_args.kwargs["params"], expected)


if __name__ == "__main__":
    unittest.main()
