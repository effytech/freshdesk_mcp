# Freshdesk MCP Server

Fork of [effytech/freshdesk_mcp](https://github.com/effytech/freshdesk_mcp) with improvements including full conversation pagination and streamlined deployment.

An MCP server that integrates with Freshdesk, enabling any AI model or MCP-compatible client to manage tickets, contacts, companies, knowledge base articles, canned responses, and more.

## Features

- **59 Tools** across tickets, agents, contacts, companies, groups, canned responses, solutions, and field management
- **2 Prompts** (`create_ticket`, `create_reply`) to guide AI models through Freshdesk payloads
- **MCP ToolAnnotations** on every tool for safe AI-driven automation
- **Multi-tenant HTTP**: one deployment serves many Freshdesk accounts via per-connection query string credentials
- **Read-only mode** via `FRESHDESK_TICKETS_READ_ONLY`
- **Full conversation pagination**: `get_ticket_conversation` fetches all pages transparently

## Prerequisites

- Python 3.10+
- Freshdesk API key (from **Profile Settings > API Key** in Freshdesk)
- [`uv`](https://docs.astral.sh/uv/) (`pip install uv` or `brew install uv`)

## Setup as Local MCP Server (stdio)

For clients that launch the server locally (Claude Desktop, Cursor, Windsurf, etc.), add this to the client's MCP config file:

**Claude Desktop** -- edit `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "freshdesk-mcp": {
      "command": "uvx",
      "args": ["freshdesk-mcp"],
      "env": {
        "FRESHDESK_API_KEY": "<YOUR_API_KEY>",
        "FRESHDESK_DOMAIN": "yourcompany.freshdesk.com",
        "FRESHDESK_TICKETS_READ_ONLY": "false"
      }
    }
  }
}
```

**Cursor** -- edit `.cursor/mcp.json` in your project root (or global settings):

```json
{
  "mcpServers": {
    "freshdesk-mcp": {
      "command": "uvx",
      "args": ["freshdesk-mcp"],
      "env": {
        "FRESHDESK_API_KEY": "<YOUR_API_KEY>",
        "FRESHDESK_DOMAIN": "yourcompany.freshdesk.com",
        "FRESHDESK_TICKETS_READ_ONLY": "false"
      }
    }
  }
}
```

The format is the same for any MCP client that supports stdio. Replace `<YOUR_API_KEY>` with your Freshdesk API key and `yourcompany.freshdesk.com` with your actual domain.

## Setup as Remote Connector (HTTP)

For clients that connect to a remote URL (Claude.ai, or any HTTP MCP client), deploy the server and connect via URL.

### Deploy on Railway

1. Deploy this repo with the root `Dockerfile`
2. Set environment variables:
   - `MCP_TRANSPORT=http`
   - `FASTMCP_STATELESS_HTTP=true`
   - Do **not** set `PORT` (Railway injects it automatically)
3. Set health check path to `/health`

### Connect from Claude.ai

In Claude.ai, add a custom MCP connector with this URL:

```
https://<your-railway-host>/mcp?freshdesk_domain=yourcompany.freshdesk.com&freshdesk_api_key=<YOUR_API_KEY>
```

The same URL format works with any remote MCP client that supports Streamable HTTP.

> **Security**: API key in query string can appear in logs and browser history. Prefer env-based auth where possible.

## Tools

59 tools organized by module:

| Module | Tools | Operations |
|--------|-------|------------|
| Tickets | 17 | CRUD, search, conversations (auto-paginated), replies, notes, summaries, fields |
| Agents | 5 | List, view, create, update, search |
| Contacts | 5 | List, view, search, update, field properties |
| Contact Fields | 4 | List, view, create, update |
| Companies | 5 | List, view, search, find by name, fields |
| Groups | 4 | List, view, create, update |
| Canned Responses | 7 | Folders + responses: list, view, create, update |
| Solutions / KB | 12 | Categories, folders, articles: list, view, create, update |

## Usage

Once the server is connected, you can interact with Freshdesk in natural language. Examples:

- "Show me ticket #12345 and its full conversation"
- "Create a high-priority ticket for customer support@acme.com about a billing issue"
- "Reply to ticket #12345 saying the issue has been resolved"
- "Search for all open tickets assigned to the Support group"
- "List all agents and find who is in the Sales group"
- "Find the company named Acme Corp and show their contact details"
- "Add a private note to ticket #12345 with the investigation results"
- "Show me all canned responses in the Billing folder"
- "Create a knowledge base article about password reset in the FAQ category"
- "What custom fields are available on tickets?"

The AI model will automatically select the right tool based on your request. All 59 tools are discoverable via the MCP protocol.

## Environment Variables

| Variable | Purpose |
|----------|---------|
| `MCP_TRANSPORT` | `stdio` (default) or `http` |
| `PORT` | HTTP listen port (auto-set by Railway) |
| `FASTMCP_STATELESS_HTTP` | `true` recommended for Railway |
| `FRESHDESK_DOMAIN` | Freshdesk host (stdio / fallback) |
| `FRESHDESK_API_KEY` | API key (stdio / fallback) |
| `FRESHDESK_TICKETS_READ_ONLY` | Block ticket mutations when `true` |

## Testing

```bash
pip install -e ".[dev]"
pytest -q
```

## Troubleshooting

- **Auth errors**: verify API key and domain; for HTTP mode check URL-encoding of query params
- **404 on `/`**: expected — MCP lives at `/mcp`, health at `/health`
- **Railway unhealthy**: confirm the process binds `0.0.0.0:$PORT` and `/health` returns 200

## License

MIT License. See [LICENSE](LICENSE).
