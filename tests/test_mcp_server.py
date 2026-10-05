import asyncio
import os
import unittest

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


EXPECTED_TOOL_COUNT = 59
EXPECTED_TOOLS = {
    "create_ticket_reply",
    "get_ticket_conversation",
    "search_tickets",
}


async def exercise_server(environment):
    parameters = StdioServerParameters(
        command="freshdesk-mcp",
        env=environment,
    )

    async with stdio_client(parameters) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            initialized = await session.initialize()
            listed = await session.list_tools()

    return initialized, listed


class TestMCPServer(unittest.IsolatedAsyncioTestCase):
    async def test_server_initializes_and_lists_tools(self):
        environment = os.environ.copy()
        environment.update(
            {
                "FRESHDESK_API_KEY": "placeholder",
                "FRESHDESK_DOMAIN": "example.freshdesk.com",
            }
        )

        initialized, listed = await asyncio.wait_for(
            exercise_server(environment),
            timeout=30,
        )

        self.assertEqual(initialized.server_info.name, "freshdesk-mcp")
        self.assertEqual(len(listed.tools), EXPECTED_TOOL_COUNT)
        tool_names = {tool.name for tool in listed.tools}
        self.assertLessEqual(EXPECTED_TOOLS, tool_names)


if __name__ == "__main__":
    unittest.main()
