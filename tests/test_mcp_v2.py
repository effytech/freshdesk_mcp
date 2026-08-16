import asyncio
import sys

from mcp import (
    ClientCapabilities,
    ClientSession,
    Implementation,
    InitializedNotification,
    InitializeRequest,
    InitializeResult,
    StdioServerParameters,
    stdio_client,
)
from mcp.server import MCPServer
from mcp_types import InitializeRequestParams

from freshdesk_mcp import __version__
from freshdesk_mcp.server import mcp


def test_server_uses_mcp_v2_api() -> None:
    assert isinstance(mcp, MCPServer)
    assert mcp.name == "freshdesk-mcp"
    assert mcp.version == __version__


def test_server_exposes_expected_tools_without_freshdesk_calls() -> None:
    tools = asyncio.run(mcp.list_tools())
    tool_names = {tool.name for tool in tools}

    assert len(tools) >= 59
    assert {
        "create_ticket",
        "get_ticket",
        "search_tickets",
        "list_contacts",
        "list_companies",
    } <= tool_names


def test_stdio_initialize_and_tools_list() -> None:
    async def exercise_server() -> None:
        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-c", "from freshdesk_mcp.server import main; main()"],
        )

        async with stdio_client(parameters) as (read, write):
            async with ClientSession(read, write, read_timeout_seconds=10) as session:
                initialized = await session.send_request(
                    InitializeRequest(
                        params=InitializeRequestParams(
                            protocol_version="2025-06-18",
                            capabilities=ClientCapabilities(),
                            client_info=Implementation(name="test", version="1"),
                        )
                    ),
                    InitializeResult,
                )
                session.adopt(initialized)
                await session.send_notification(InitializedNotification())
                tools = await session.list_tools()
                prompts = await session.list_prompts()

        assert initialized.protocol_version == "2025-06-18"
        assert initialized.server_info.name == "freshdesk-mcp"
        assert initialized.server_info.version == __version__
        assert len(tools.tools) >= 59
        assert len(prompts.prompts) >= 2
        assert "create_ticket" in {tool.name for tool in tools.tools}
        assert "create_ticket" in {prompt.name for prompt in prompts.prompts}

    asyncio.run(exercise_server())
