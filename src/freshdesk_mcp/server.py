import base64
import httpx
import logging
import mimetypes
import os
import re
from enum import Enum, IntEnum
from typing import Any, Dict, List, Optional, Union
from urllib.parse import urlparse

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field
from starlette.requests import Request
from starlette.responses import JSONResponse

from freshdesk_mcp.config import FreshdeskConfig, resolve_freshdesk_config
from freshdesk_mcp.freshdesk_client import (
    TIMEOUT as FD_HTTP_TIMEOUT,
    freshdesk_call,
    freshdesk_exchange,
)

logging.basicConfig(level=logging.INFO)


def _bool_env(name: str, default: bool) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return v.lower().strip() in ("true", "1", "yes")


def _transport_from_env() -> str:
    t = os.getenv("MCP_TRANSPORT", "stdio").lower().strip()
    if t in ("http", "streamable-http", "streamable_http"):
        return "streamable-http"
    return "stdio"


mcp = FastMCP(
    "freshdesk-mcp",
    host="127.0.0.1",
    port=8000,
    streamable_http_path="/mcp",
    stateless_http=_bool_env("FASTMCP_STATELESS_HTTP", False),
    # Railway / public HTTPS use arbitrary Host headers; SDK DNS rebinding check
    # otherwise returns 421 "Invalid Host header" and remote clients cannot connect.
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
)

# MCP listTools annotations (hints for clients; Freshdesk is always external / "open world").
_ANN_READ = ToolAnnotations(
    readOnlyHint=True,
    idempotentHint=True,
    openWorldHint=True,
)
_ANN_WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=True)
_ANN_DELETE = ToolAnnotations(readOnlyHint=False, destructiveHint=True, openWorldHint=True)

MAX_ATTACHMENT_SIZE_BYTES = 20 * 1024 * 1024
INLINE_IMAGE_HOST = "attachment.freshdesk.com"
INLINE_IMAGE_SRC_RE = re.compile(r'<img\b[^>]*\bsrc=["\']([^"\']+)["\']', re.IGNORECASE)


def _require_freshdesk_config(ctx: Context) -> FreshdeskConfig | Dict[str, Any]:
    try:
        rc = ctx.request_context
    except ValueError:
        return resolve_freshdesk_config(query_params=None, env_fallback=True)
    req = getattr(rc, "request", None)
    qp = req.query_params if req is not None else None
    return resolve_freshdesk_config(query_params=qp, env_fallback=True)


def _check_tickets_read_only(cfg: FreshdeskConfig) -> Optional[Dict[str, Any]]:
    if cfg.tickets_read_only:
        return {
            "error": "Operation blocked: tickets read-only mode (freshdesk_tickets_read_only / FRESHDESK_TICKETS_READ_ONLY)",
        }
    return None


async def _fd_call(
    cfg: FreshdeskConfig,
    method: str,
    path: str,
    *,
    params: Optional[Dict[str, Any]] = None,
    json_body: Any = None,
) -> Any:
    async with httpx.AsyncClient(timeout=FD_HTTP_TIMEOUT) as client:
        return await freshdesk_call(client, cfg, method, path, params=params, json=json_body)


@mcp.custom_route("/health", methods=["GET"])
async def _health_check(_request: Request) -> JSONResponse:
    return JSONResponse({"status": "healthy", "service": "freshdesk-mcp"})


def parse_link_header(link_header: str) -> Dict[str, Optional[int]]:
    """Parse the Link header to extract pagination information.

    Args:
        link_header: The Link header string from the response

    Returns:
        Dictionary containing next and prev page numbers
    """
    pagination = {
        "next": None,
        "prev": None
    }

    if not link_header:
        return pagination

    # Split multiple links if present
    links = link_header.split(',')

    for link in links:
        # Extract URL and rel
        match = re.search(r'<(.+?)>;\s*rel="(.+?)"', link)
        if match:
            url, rel = match.groups()
            # Extract page number from URL
            page_match = re.search(r'page=(\d+)', url)
            if page_match:
                page_num = int(page_match.group(1))
                pagination[rel] = page_num

    return pagination


def _extract_inline_image_urls(html: Optional[str]) -> List[str]:
    if not html:
        return []

    urls: List[str] = []
    for src in INLINE_IMAGE_SRC_RE.findall(html):
        parsed = urlparse(src.strip())
        if parsed.scheme not in ("http", "https"):
            continue
        netloc = parsed.netloc.lower()
        if netloc == INLINE_IMAGE_HOST or netloc.endswith(f".{INLINE_IMAGE_HOST}"):
            urls.append(src.strip())
    return urls


def _inline_image_name(index: int, content_type: Optional[str]) -> str:
    normalized_type = (content_type or "").split(";", 1)[0].strip().lower()
    extension = mimetypes.guess_extension(normalized_type) or ""
    return f"inline_image_{index}{extension}"


async def _get_all_ticket_conversations(
    client: httpx.AsyncClient,
    cfg: FreshdeskConfig,
    ticket_id: int,
) -> list[Dict[str, Any]] | Dict[str, Any]:
    all_conversations: list[Dict[str, Any]] = []
    per_page = 100
    page = 1
    path = f"/api/v2/tickets/{ticket_id}/conversations"

    while True:
        params = {"page": page, "per_page": per_page}
        data, hdrs = await freshdesk_exchange(client, cfg, "GET", path, params=params)

        if isinstance(data, dict) and data.get("error"):
            return data

        if not isinstance(data, list) or len(data) == 0:
            break

        all_conversations.extend(data)

        link_info = parse_link_header(hdrs.get("Link", ""))
        if link_info.get("next") is None:
            break

        page += 1

    return all_conversations


async def _download_attachment_content(
    client: httpx.AsyncClient,
    url: str,
    *,
    source: str,
    attachment_type: str,
    name: Optional[str] = None,
    content_type: Optional[str] = None,
    size: Optional[int] = None,
    inline_index: Optional[int] = None,
) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "source": source,
        "type": attachment_type,
    }

    if name:
        result["name"] = name
    if content_type:
        result["content_type"] = content_type
    if size is not None:
        result["size"] = size

    if size is not None and size > MAX_ATTACHMENT_SIZE_BYTES:
        result["error"] = (
            f"Skipped attachment larger than 20MB limit ({size} bytes)"
        )
        return result

    if not url:
        result["error"] = "Missing attachment download URL"
        if "name" not in result and attachment_type == "inline_image":
            result["name"] = _inline_image_name(inline_index or 1, content_type)
        return result

    try:
        async with client.stream("GET", url, follow_redirects=True) as response:
            response.raise_for_status()

            response_content_type = response.headers.get("Content-Type")
            normalized_content_type = (
                response_content_type.split(";", 1)[0].strip()
                if response_content_type
                else (content_type or "application/octet-stream")
            )
            result["content_type"] = normalized_content_type

            content_length = response.headers.get("Content-Length")
            if content_length:
                try:
                    content_length_int = int(content_length)
                except ValueError:
                    content_length_int = None
                if content_length_int is not None:
                    if size is None:
                        result["size"] = content_length_int
                    if content_length_int > MAX_ATTACHMENT_SIZE_BYTES:
                        result["error"] = (
                            f"Skipped attachment larger than 20MB limit ({content_length_int} bytes)"
                        )
                        if "name" not in result:
                            result["name"] = _inline_image_name(inline_index or 1, normalized_content_type)
                        return result

            chunks: List[bytes] = []
            total_size = 0
            async for chunk in response.aiter_bytes():
                if not chunk:
                    continue
                total_size += len(chunk)
                if total_size > MAX_ATTACHMENT_SIZE_BYTES:
                    result["size"] = total_size
                    result["error"] = (
                        f"Skipped attachment larger than 20MB limit ({total_size} bytes)"
                    )
                    if "name" not in result:
                        result["name"] = _inline_image_name(inline_index or 1, normalized_content_type)
                    return result
                chunks.append(chunk)

        final_bytes = b"".join(chunks)
        result["size"] = len(final_bytes)
        if "name" not in result:
            result["name"] = _inline_image_name(inline_index or 1, result.get("content_type"))
        result["data_base64"] = base64.b64encode(final_bytes).decode("ascii")
        return result
    except httpx.HTTPStatusError as exc:
        result["error"] = f"Download failed with HTTP {exc.response.status_code}"
        if "name" not in result and attachment_type == "inline_image":
            result["name"] = _inline_image_name(inline_index or 1, content_type)
        return result
    except httpx.TimeoutException as exc:
        result["error"] = f"Download timeout: {exc}"
        if "name" not in result and attachment_type == "inline_image":
            result["name"] = _inline_image_name(inline_index or 1, content_type)
        return result
    except httpx.RequestError as exc:
        result["error"] = f"Download network error: {exc}"
        if "name" not in result and attachment_type == "inline_image":
            result["name"] = _inline_image_name(inline_index or 1, content_type)
        return result

# enums of ticket properties
class TicketSource(IntEnum):
    EMAIL = 1
    PORTAL = 2
    PHONE = 3
    CHAT = 7
    FEEDBACK_WIDGET = 9
    OUTBOUND_EMAIL = 10

class TicketStatus(IntEnum):
    OPEN = 2
    PENDING = 3
    RESOLVED = 4
    CLOSED = 5

class TicketPriority(IntEnum):
    LOW = 1
    MEDIUM = 2
    HIGH = 3
    URGENT = 4
class AgentTicketScope(IntEnum):
    GLOBAL_ACCESS = 1
    GROUP_ACCESS = 2
    RESTRICTED_ACCESS = 3

class UnassignedForOptions(str, Enum):
    THIRTY_MIN = "30m"
    ONE_HOUR = "1h"
    TWO_HOURS = "2h"
    FOUR_HOURS = "4h"
    EIGHT_HOURS = "8h"
    TWELVE_HOURS = "12h"
    ONE_DAY = "1d"
    TWO_DAYS = "2d"
    THREE_DAYS = "3d"

class GroupCreate(BaseModel):
    name: str = Field(..., description="Name of the group")
    description: Optional[str] = Field(None, description="Description of the group")
    agent_ids: Optional[List[int]] = Field(
        default=None,
        description="Array of agent user ids"
    )
    auto_ticket_assign: Optional[int] = Field(
        default=0,
        ge=0,
        le=1,
        description="Automatic ticket assignment type (0 or 1)"
    )
    escalate_to: Optional[int] = Field(
        None,
        description="User ID to whom escalation email is sent if ticket is unassigned"
    )
    unassigned_for: Optional[UnassignedForOptions] = Field(
        default=UnassignedForOptions.THIRTY_MIN,
        description="Time after which escalation email will be sent"
    )

class ContactFieldCreate(BaseModel):
    label: str = Field(..., description="Display name for the field (as seen by agents)")
    label_for_customers: str = Field(..., description="Display name for the field (as seen by customers)")
    type: str = Field(
        ...,
        description="Type of the field",
        pattern="^(custom_text|custom_paragraph|custom_checkbox|custom_number|custom_dropdown|custom_phone_number|custom_url|custom_date)$"
    )
    editable_in_signup: bool = Field(
        default=False,
        description="Set to true if the field can be updated by customers during signup"
    )
    position: int = Field(
        default=1,
        description="Position of the company field"
    )
    required_for_agents: bool = Field(
        default=False,
        description="Set to true if the field is mandatory for agents"
    )
    customers_can_edit: bool = Field(
        default=False,
        description="Set to true if the customer can edit the fields in the customer portal"
    )
    required_for_customers: bool = Field(
        default=False,
        description="Set to true if the field is mandatory in the customer portal"
    )
    displayed_for_customers: bool = Field(
        default=False,
        description="Set to true if the customers can see the field in the customer portal"
    )
    choices: Optional[List[Dict[str, Union[str, int]]]] = Field(
        default=None,
        description="Array of objects in format {'value': 'Choice text', 'position': 1} for dropdown choices"
    )

class CannedResponseCreate(BaseModel):
    title: str = Field(..., description="Title of the canned response")
    content_html: str = Field(..., description="HTML version of the canned response content")
    folder_id: int = Field(..., description="Folder where the canned response gets added")
    visibility: int = Field(
        ...,
        description="Visibility of the canned response (0=all agents, 1=personal, 2=select groups)",
        ge=0,
        le=2
    )
    group_ids: Optional[List[int]] = Field(
        None,
        description="Groups for which the canned response is visible. Required if visibility=2"
    )

@mcp.tool(annotations=_ANN_READ)
async def get_ticket_fields(ctx: Context) -> Dict[str, Any]:
    """Get ticket fields from Freshdesk."""
    cfg_r = _require_freshdesk_config(ctx)
    if isinstance(cfg_r, dict):
        return cfg_r
    cfg: FreshdeskConfig = cfg_r
    return await _fd_call(cfg, "GET", "/api/v2/ticket_fields")


@mcp.tool(annotations=_ANN_READ)
async def get_tickets(ctx: Context, page: Optional[int] = 1, per_page: Optional[int] = 30) -> Dict[str, Any]:
    """Get tickets from Freshdesk with pagination support."""
    cfg_r = _require_freshdesk_config(ctx)
    if isinstance(cfg_r, dict):
        return cfg_r
    cfg: FreshdeskConfig = cfg_r
    # Validate input parameters
    if page < 1:
        return {"error": "Page number must be greater than 0"}

    if per_page < 1 or per_page > 100:
        return {"error": "Page size must be between 1 and 100"}

    params = {"page": page, "per_page": per_page}
    async with httpx.AsyncClient(timeout=FD_HTTP_TIMEOUT) as client:
        tickets, hdrs = await freshdesk_exchange(client, cfg, "GET", "/api/v2/tickets", params=params)
        if isinstance(tickets, dict) and tickets.get("error"):
            return tickets
        link_header = hdrs.get("Link", "")
        pagination_info = parse_link_header(link_header)
        return {
            "tickets": tickets,
            "pagination": {
                "current_page": page,
                "next_page": pagination_info.get("next"),
                "prev_page": pagination_info.get("prev"),
                "per_page": per_page,
            },
        }

@mcp.tool(annotations=_ANN_WRITE)
async def create_ticket(
    ctx: Context,
    subject: str,
    description: str,
    source: Union[int, str],
    priority: Union[int, str],
    status: Union[int, str],
    email: Optional[str] = None,
    requester_id: Optional[int] = None,
    custom_fields: Optional[Dict[str, Any]] = None,
    additional_fields: Optional[Dict[str, Any]] = None,
) -> str:
    """Create a ticket in Freshdesk"""
    cfg_r = _require_freshdesk_config(ctx)
    if isinstance(cfg_r, dict):
        return cfg_r
    cfg: FreshdeskConfig = cfg_r
    if (ro := _check_tickets_read_only(cfg)):
        return ro
    # Validate requester information
    if not email and not requester_id:
        return "Error: Either email or requester_id must be provided"

    # Convert string inputs to integers if necessary
    try:
        source_val = int(source)
        priority_val = int(priority)
        status_val = int(status)
    except ValueError:
        return "Error: Invalid value for source, priority, or status"

    # Validate enum values
    if (source_val not in [e.value for e in TicketSource] or
        priority_val not in [e.value for e in TicketPriority] or
        status_val not in [e.value for e in TicketStatus]):
        return "Error: Invalid value for source, priority, or status"

    # Prepare the request data
    data = {
        "subject": subject,
        "description": description,
        "source": source_val,
        "priority": priority_val,
        "status": status_val
    }

    # Add requester information
    if email:
        data["email"] = email
    if requester_id:
        data["requester_id"] = requester_id

    # Add custom fields if provided
    if custom_fields:
        data["custom_fields"] = custom_fields

     # Add any other top-level fields
    if additional_fields:
        data.update(additional_fields)

    async with httpx.AsyncClient(timeout=FD_HTTP_TIMEOUT) as client:
        out = await freshdesk_call(client, cfg, "POST", "/api/v2/tickets", json=data)
    if isinstance(out, dict) and out.get("error"):
        det = out.get("details")
        if isinstance(det, dict) and "errors" in det:
            return f"Validation Error: {det['errors']}"
        return f"Error: Failed to create ticket - {out.get('error')}"
    return "Ticket created successfully" if isinstance(out, dict) and "id" in out else f"Success: {out}"

@mcp.tool(annotations=_ANN_WRITE)
async def update_ticket(ticket_id: int, ticket_fields: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
    """Update a ticket in Freshdesk."""
    cfg_r = _require_freshdesk_config(ctx)
    if isinstance(cfg_r, dict):
        return cfg_r
    cfg: FreshdeskConfig = cfg_r
    if (ro := _check_tickets_read_only(cfg)):
        return ro
    if not ticket_fields:
        return {"error": "No fields provided for update"}

    # Separate custom fields from standard fields
    custom_fields = ticket_fields.pop('custom_fields', {})

    # Prepare the update data
    update_data = {}

    # Add standard fields if they are provided
    for field, value in ticket_fields.items():
        update_data[field] = value

    # Add custom fields if they exist
    if custom_fields:
        update_data['custom_fields'] = custom_fields

    async with httpx.AsyncClient(timeout=FD_HTTP_TIMEOUT) as client:
        out = await freshdesk_call(
            client, cfg, "PUT", f"/api/v2/tickets/{ticket_id}", json=update_data
        )
    if isinstance(out, dict) and out.get("error"):
        det = out.get("details")
        if isinstance(det, dict) and "errors" in det:
            return {"success": False, "error": f"Validation errors: {det['errors']}"}
        return {"success": False, "error": out.get("error")}
    return {"success": True, "message": "Ticket updated successfully", "ticket": out}

@mcp.tool(annotations=_ANN_DELETE)
async def delete_ticket(ticket_id: int, ctx: Context) -> Dict[str, Any]:
    """Delete a ticket in Freshdesk."""
    cfg_r = _require_freshdesk_config(ctx)
    if isinstance(cfg_r, dict):
        return cfg_r
    cfg: FreshdeskConfig = cfg_r
    if (ro := _check_tickets_read_only(cfg)):
        return ro
    async with httpx.AsyncClient(timeout=FD_HTTP_TIMEOUT) as client:
        out = await freshdesk_call(client, cfg, "DELETE", f"/api/v2/tickets/{ticket_id}")
    if isinstance(out, dict) and out.get("error"):
        return out
    return {"success": True, "message": "Ticket deleted successfully"}

@mcp.tool(annotations=_ANN_READ)
async def get_ticket(ticket_id: int, ctx: Context):
    """Get a ticket in Freshdesk."""
    cfg_r = _require_freshdesk_config(ctx)
    if isinstance(cfg_r, dict):
        return cfg_r
    cfg: FreshdeskConfig = cfg_r
    return await _fd_call(cfg, "GET", f"/api/v2/tickets/{ticket_id}")


@mcp.tool(annotations=_ANN_READ)
async def get_ticket_attachments(ticket_id: int, ctx: Context) -> Dict[str, Any]:
    """Get all file attachments and inline images for a Freshdesk ticket and its conversations."""
    cfg_r = _require_freshdesk_config(ctx)
    if isinstance(cfg_r, dict):
        return cfg_r
    cfg: FreshdeskConfig = cfg_r

    attachments: List[Dict[str, Any]] = []
    file_count = 0
    inline_image_count = 0
    inline_index = 1

    async with httpx.AsyncClient(timeout=FD_HTTP_TIMEOUT) as client:
        ticket = await freshdesk_call(client, cfg, "GET", f"/api/v2/tickets/{ticket_id}")
        if isinstance(ticket, dict) and ticket.get("error"):
            return ticket

        conversations = await _get_all_ticket_conversations(client, cfg, ticket_id)
        if isinstance(conversations, dict) and conversations.get("error"):
            return conversations

        sources: List[tuple[str, Dict[str, Any], str]] = [("ticket", ticket, "description")]
        sources.extend(("conversation", conversation, "body") for conversation in conversations)

        for source_name, payload, html_field in sources:
            for attachment in payload.get("attachments") or []:
                file_count += 1
                attachment_result = await _download_attachment_content(
                    client,
                    attachment.get("attachment_url", ""),
                    source=source_name,
                    attachment_type="file",
                    name=attachment.get("name"),
                    content_type=attachment.get("content_type"),
                    size=attachment.get("size"),
                )
                attachments.append(attachment_result)

            for image_url in _extract_inline_image_urls(payload.get(html_field)):
                inline_image_count += 1
                image_result = await _download_attachment_content(
                    client,
                    image_url,
                    source=source_name,
                    attachment_type="inline_image",
                    inline_index=inline_index,
                )
                attachments.append(image_result)
                inline_index += 1

    return {
        "attachments": attachments,
        "summary": f"Trovati {file_count} allegati file e {inline_image_count} immagini inline",
    }


@mcp.tool(annotations=_ANN_READ)
async def search_tickets(query: str, ctx: Context) -> Dict[str, Any]:
    """Search for tickets in Freshdesk."""
    cfg_r = _require_freshdesk_config(ctx)
    if isinstance(cfg_r, dict):
        return cfg_r
    cfg: FreshdeskConfig = cfg_r
    return await _fd_call(cfg, "GET", "/api/v2/search/tickets", params={"query": query})


@mcp.tool(annotations=_ANN_READ)
async def get_ticket_conversation(ticket_id: int, ctx: Context) -> list[Dict[str, Any]]:
    """Get a ticket conversation in Freshdesk."""
    cfg_r = _require_freshdesk_config(ctx)
    if isinstance(cfg_r, dict):
        return cfg_r
    cfg: FreshdeskConfig = cfg_r

    async with httpx.AsyncClient(timeout=FD_HTTP_TIMEOUT) as client:
        return await _get_all_ticket_conversations(client, cfg, ticket_id)


@mcp.tool(annotations=_ANN_WRITE)
async def create_ticket_reply(ticket_id: int,body: str, ctx: Context) -> Dict[str, Any]:
    """Create a reply to a ticket in Freshdesk."""
    cfg_r = _require_freshdesk_config(ctx)
    if isinstance(cfg_r, dict):
        return cfg_r
    cfg: FreshdeskConfig = cfg_r
    if (ro := _check_tickets_read_only(cfg)):
        return ro
    data = {"body": body}
    async with httpx.AsyncClient(timeout=FD_HTTP_TIMEOUT) as client:
        return await freshdesk_call(
            client, cfg, "POST", f"/api/v2/tickets/{ticket_id}/reply", json=data
        )

@mcp.tool(annotations=_ANN_WRITE)
async def create_ticket_note(ticket_id: int,body: str, ctx: Context) -> Dict[str, Any]:
    """Create a note for a ticket in Freshdesk."""
    cfg_r = _require_freshdesk_config(ctx)
    if isinstance(cfg_r, dict):
        return cfg_r
    cfg: FreshdeskConfig = cfg_r
    if (ro := _check_tickets_read_only(cfg)):
        return ro
    data = {"body": body}
    async with httpx.AsyncClient(timeout=FD_HTTP_TIMEOUT) as client:
        return await freshdesk_call(
            client, cfg, "POST", f"/api/v2/tickets/{ticket_id}/notes", json=data
        )

@mcp.tool(annotations=_ANN_WRITE)
async def update_ticket_conversation(conversation_id: int,body: str, ctx: Context) -> Dict[str, Any]:
    """Update a conversation for a ticket in Freshdesk."""
    cfg_r = _require_freshdesk_config(ctx)
    if isinstance(cfg_r, dict):
        return cfg_r
    cfg: FreshdeskConfig = cfg_r
    if (ro := _check_tickets_read_only(cfg)):
        return ro
    data = {"body": body}
    async with httpx.AsyncClient(timeout=FD_HTTP_TIMEOUT) as client:
        out = await freshdesk_call(
            client, cfg, "PUT", f"/api/v2/conversations/{conversation_id}", json=data
        )
    if isinstance(out, dict) and out.get("error"):
        return out
    return out

@mcp.tool(annotations=_ANN_READ)
async def get_agents(ctx: Context, page: Optional[int] = 1, per_page: Optional[int] = 30) -> list[Dict[str, Any]]:
    """Get all agents in Freshdesk with pagination support."""
    cfg_r = _require_freshdesk_config(ctx)
    if isinstance(cfg_r, dict):
        return cfg_r
    cfg: FreshdeskConfig = cfg_r
    # Validate input parameters
    if page < 1:
        return {"error": "Page number must be greater than 0"}

    if per_page < 1 or per_page > 100:
        return {"error": "Page size must be between 1 and 100"}
    params = {"page": page, "per_page": per_page}
    return await _fd_call(cfg, "GET", "/api/v2/agents", params=params)

@mcp.tool(annotations=_ANN_READ)
async def list_contacts(ctx: Context, page: Optional[int] = 1, per_page: Optional[int] = 30) -> list[Dict[str, Any]]:
    """List all contacts in Freshdesk with pagination support."""
    cfg_r = _require_freshdesk_config(ctx)
    if isinstance(cfg_r, dict):
        return cfg_r
    cfg: FreshdeskConfig = cfg_r
    params = {"page": page, "per_page": per_page}
    return await _fd_call(cfg, "GET", "/api/v2/contacts", params=params)

@mcp.tool(annotations=_ANN_READ)
async def get_contact(contact_id: int, ctx: Context) -> Dict[str, Any]:
    """Get a contact in Freshdesk."""
    cfg_r = _require_freshdesk_config(ctx)
    if isinstance(cfg_r, dict):
        return cfg_r
    cfg: FreshdeskConfig = cfg_r
    return await _fd_call(cfg, "GET", f"/api/v2/contacts/{contact_id}")


@mcp.tool(annotations=_ANN_READ)
async def search_contacts(query: str, ctx: Context) -> list[Dict[str, Any]]:
    """Search for contacts in Freshdesk."""
    cfg_r = _require_freshdesk_config(ctx)
    if isinstance(cfg_r, dict):
        return cfg_r
    cfg: FreshdeskConfig = cfg_r
    return await _fd_call(cfg, "GET", "/api/v2/contacts/autocomplete", params={"term": query})

@mcp.tool(annotations=_ANN_WRITE)
async def update_contact(contact_id: int, contact_fields: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
    """Update a contact in Freshdesk."""
    cfg_r = _require_freshdesk_config(ctx)
    if isinstance(cfg_r, dict):
        return cfg_r
    cfg: FreshdeskConfig = cfg_r
    data = {k: v for k, v in contact_fields.items()}
    async with httpx.AsyncClient(timeout=FD_HTTP_TIMEOUT) as client:
        return await freshdesk_call(
            client, cfg, "PUT", f"/api/v2/contacts/{contact_id}", json=data
        )


@mcp.tool(annotations=_ANN_READ)
async def list_canned_responses(folder_id: int, ctx: Context) -> list[Dict[str, Any]]:
    """List all canned responses in Freshdesk."""
    cfg_r = _require_freshdesk_config(ctx)
    if isinstance(cfg_r, dict):
        return cfg_r
    cfg: FreshdeskConfig = cfg_r
    out = await _fd_call(cfg, "GET", f"/api/v2/canned_response_folders/{folder_id}/responses")
    if isinstance(out, dict) and out.get("error"):
        return out
    return out if isinstance(out, list) else []

@mcp.tool(annotations=_ANN_READ)
async def list_canned_response_folders(ctx: Context) -> list[Dict[str, Any]]:
    """List all canned response folders in Freshdesk."""
    cfg_r = _require_freshdesk_config(ctx)
    if isinstance(cfg_r, dict):
        return cfg_r
    cfg: FreshdeskConfig = cfg_r
    return await _fd_call(cfg, "GET", "/api/v2/canned_response_folders")


@mcp.tool(annotations=_ANN_READ)
async def view_canned_response(canned_response_id: int, ctx: Context) -> Dict[str, Any]:
    """View a canned response in Freshdesk."""
    cfg_r = _require_freshdesk_config(ctx)
    if isinstance(cfg_r, dict):
        return cfg_r
    cfg: FreshdeskConfig = cfg_r
    return await _fd_call(cfg, "GET", f"/api/v2/canned_responses/{canned_response_id}")


@mcp.tool(annotations=_ANN_WRITE)
async def create_canned_response(canned_response_fields: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
    """Create a canned response in Freshdesk."""
    cfg_r = _require_freshdesk_config(ctx)
    if isinstance(cfg_r, dict):
        return cfg_r
    cfg: FreshdeskConfig = cfg_r
    # Validate input using Pydantic model
    try:
        validated_fields = CannedResponseCreate(**canned_response_fields)
        # Convert to dict for API request
        canned_response_data = validated_fields.model_dump(exclude_none=True)
    except Exception as e:
        return {"error": f"Validation error: {str(e)}"}

    async with httpx.AsyncClient(timeout=FD_HTTP_TIMEOUT) as client:
        return await freshdesk_call(
            client, cfg, "POST", "/api/v2/canned_responses", json=canned_response_data
        )

@mcp.tool(annotations=_ANN_WRITE)
async def update_canned_response(canned_response_id: int, canned_response_fields: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
    """Update a canned response in Freshdesk."""
    cfg_r = _require_freshdesk_config(ctx)
    if isinstance(cfg_r, dict):
        return cfg_r
    cfg: FreshdeskConfig = cfg_r
    async with httpx.AsyncClient(timeout=FD_HTTP_TIMEOUT) as client:
        return await freshdesk_call(
            client,
            cfg,
            "PUT",
            f"/api/v2/canned_responses/{canned_response_id}",
            json=canned_response_fields,
        )


@mcp.tool(annotations=_ANN_WRITE)
async def create_canned_response_folder(name: str, ctx: Context) -> Dict[str, Any]:
    """Create a canned response folder in Freshdesk."""
    cfg_r = _require_freshdesk_config(ctx)
    if isinstance(cfg_r, dict):
        return cfg_r
    cfg: FreshdeskConfig = cfg_r
    data = {"name": name}
    async with httpx.AsyncClient(timeout=FD_HTTP_TIMEOUT) as client:
        return await freshdesk_call(client, cfg, "POST", "/api/v2/canned_response_folders", json=data)


@mcp.tool(annotations=_ANN_WRITE)
async def update_canned_response_folder(folder_id: int, name: str, ctx: Context) -> Dict[str, Any]:
    """Update a canned response folder in Freshdesk."""
    cfg_r = _require_freshdesk_config(ctx)
    if isinstance(cfg_r, dict):
        return cfg_r
    cfg: FreshdeskConfig = cfg_r
    data = {"name": name}
    async with httpx.AsyncClient(timeout=FD_HTTP_TIMEOUT) as client:
        return await freshdesk_call(
            client, cfg, "PUT", f"/api/v2/canned_response_folders/{folder_id}", json=data
        )

@mcp.tool(annotations=_ANN_READ)
async def list_solution_articles(folder_id: int, ctx: Context) -> list[Dict[str, Any]]:
    """List all solution articles in Freshdesk."""
    cfg_r = _require_freshdesk_config(ctx)
    if isinstance(cfg_r, dict):
        return cfg_r
    cfg: FreshdeskConfig = cfg_r
    out = await _fd_call(cfg, "GET", f"/api/v2/solutions/folders/{folder_id}/articles")
    if isinstance(out, dict) and out.get("error"):
        return out
    return out if isinstance(out, list) else []

@mcp.tool(annotations=_ANN_READ)
async def list_solution_folders(category_id: int, ctx: Context) -> list[Dict[str, Any]]:
    """List all solution folders in Freshdesk."""
    cfg_r = _require_freshdesk_config(ctx)
    if isinstance(cfg_r, dict):
        return cfg_r
    cfg: FreshdeskConfig = cfg_r
    if not category_id:
        return {"error": "Category ID is required"}
    return await _fd_call(cfg, "GET", f"/api/v2/solutions/categories/{category_id}/folders")


@mcp.tool(annotations=_ANN_READ)
async def list_solution_categories(ctx: Context) -> list[Dict[str, Any]]:
    """List all solution categories in Freshdesk."""
    cfg_r = _require_freshdesk_config(ctx)
    if isinstance(cfg_r, dict):
        return cfg_r
    cfg: FreshdeskConfig = cfg_r
    return await _fd_call(cfg, "GET", "/api/v2/solutions/categories")


@mcp.tool(annotations=_ANN_READ)
async def view_solution_category(category_id: int, ctx: Context) -> Dict[str, Any]:
    """View a solution category in Freshdesk."""
    cfg_r = _require_freshdesk_config(ctx)
    if isinstance(cfg_r, dict):
        return cfg_r
    cfg: FreshdeskConfig = cfg_r
    return await _fd_call(cfg, "GET", f"/api/v2/solutions/categories/{category_id}")


@mcp.tool(annotations=_ANN_WRITE)
async def create_solution_category(category_fields: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
    """Create a solution category in Freshdesk."""
    cfg_r = _require_freshdesk_config(ctx)
    if isinstance(cfg_r, dict):
        return cfg_r
    cfg: FreshdeskConfig = cfg_r
    if not category_fields.get("name"):
        return {"error": "Name is required"}

    async with httpx.AsyncClient(timeout=FD_HTTP_TIMEOUT) as client:
        return await freshdesk_call(
            client, cfg, "POST", "/api/v2/solutions/categories", json=category_fields
        )

@mcp.tool(annotations=_ANN_WRITE)
async def update_solution_category(category_id: int, category_fields: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
    """Update a solution category in Freshdesk."""
    cfg_r = _require_freshdesk_config(ctx)
    if isinstance(cfg_r, dict):
        return cfg_r
    cfg: FreshdeskConfig = cfg_r
    if not category_fields.get("name"):
        return {"error": "Name is required"}

    async with httpx.AsyncClient(timeout=FD_HTTP_TIMEOUT) as client:
        return await freshdesk_call(
            client,
            cfg,
            "PUT",
            f"/api/v2/solutions/categories/{category_id}",
            json=category_fields,
        )

@mcp.tool(annotations=_ANN_WRITE)
async def create_solution_category_folder(category_id: int, folder_fields: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
    """Create a solution category folder in Freshdesk."""
    cfg_r = _require_freshdesk_config(ctx)
    if isinstance(cfg_r, dict):
        return cfg_r
    cfg: FreshdeskConfig = cfg_r
    if not folder_fields.get("name"):
        return {"error": "Name is required"}
    async with httpx.AsyncClient(timeout=FD_HTTP_TIMEOUT) as client:
        return await freshdesk_call(
            client,
            cfg,
            "POST",
            f"/api/v2/solutions/categories/{category_id}/folders",
            json=folder_fields,
        )

@mcp.tool(annotations=_ANN_READ)
async def view_solution_category_folder(folder_id: int, ctx: Context) -> Dict[str, Any]:
    """View a solution category folder in Freshdesk."""
    cfg_r = _require_freshdesk_config(ctx)
    if isinstance(cfg_r, dict):
        return cfg_r
    cfg: FreshdeskConfig = cfg_r
    return await _fd_call(cfg, "GET", f"/api/v2/solutions/folders/{folder_id}")


@mcp.tool(annotations=_ANN_WRITE)
async def update_solution_category_folder(folder_id: int, folder_fields: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
    """Update a solution category folder in Freshdesk."""
    cfg_r = _require_freshdesk_config(ctx)
    if isinstance(cfg_r, dict):
        return cfg_r
    cfg: FreshdeskConfig = cfg_r
    if not folder_fields.get("name"):
        return {"error": "Name is required"}
    async with httpx.AsyncClient(timeout=FD_HTTP_TIMEOUT) as client:
        return await freshdesk_call(
            client, cfg, "PUT", f"/api/v2/solutions/folders/{folder_id}", json=folder_fields
        )


@mcp.tool(annotations=_ANN_WRITE)
async def create_solution_article(folder_id: int, article_fields: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
    """Create a solution article in Freshdesk."""
    cfg_r = _require_freshdesk_config(ctx)
    if isinstance(cfg_r, dict):
        return cfg_r
    cfg: FreshdeskConfig = cfg_r
    if not article_fields.get("title") or not article_fields.get("status") or not article_fields.get("description"):
        return {"error": "Title, status and description are required"}
    async with httpx.AsyncClient(timeout=FD_HTTP_TIMEOUT) as client:
        return await freshdesk_call(
            client,
            cfg,
            "POST",
            f"/api/v2/solutions/folders/{folder_id}/articles",
            json=article_fields,
        )

@mcp.tool(annotations=_ANN_READ)
async def view_solution_article(article_id: int, ctx: Context) -> Dict[str, Any]:
    """View a solution article in Freshdesk."""
    cfg_r = _require_freshdesk_config(ctx)
    if isinstance(cfg_r, dict):
        return cfg_r
    cfg: FreshdeskConfig = cfg_r
    return await _fd_call(cfg, "GET", f"/api/v2/solutions/articles/{article_id}")


@mcp.tool(annotations=_ANN_WRITE)
async def update_solution_article(article_id: int, article_fields: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
    """Update a solution article in Freshdesk."""
    cfg_r = _require_freshdesk_config(ctx)
    if isinstance(cfg_r, dict):
        return cfg_r
    cfg: FreshdeskConfig = cfg_r
    async with httpx.AsyncClient(timeout=FD_HTTP_TIMEOUT) as client:
        return await freshdesk_call(
            client, cfg, "PUT", f"/api/v2/solutions/articles/{article_id}", json=article_fields
        )

@mcp.tool(annotations=_ANN_READ)
async def view_agent(agent_id: int, ctx: Context) -> Dict[str, Any]:
    """View an agent in Freshdesk."""
    cfg_r = _require_freshdesk_config(ctx)
    if isinstance(cfg_r, dict):
        return cfg_r
    cfg: FreshdeskConfig = cfg_r
    return await _fd_call(cfg, "GET", f"/api/v2/agents/{agent_id}")


@mcp.tool(annotations=_ANN_WRITE)
async def create_agent(agent_fields: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
    """Create an agent in Freshdesk."""
    cfg_r = _require_freshdesk_config(ctx)
    if isinstance(cfg_r, dict):
        return cfg_r
    cfg: FreshdeskConfig = cfg_r
    # Validate mandatory fields
    if not agent_fields.get("email") or not agent_fields.get("ticket_scope"):
        return {
            "error": "Missing mandatory fields. Both 'email' and 'ticket_scope' are required."
        }
    if agent_fields.get("ticket_scope") not in [e.value for e in AgentTicketScope]:
        return {
            "error": "Invalid value for ticket_scope. Must be one of: " + ", ".join([e.name for e in AgentTicketScope])
        }

    async with httpx.AsyncClient(timeout=FD_HTTP_TIMEOUT) as client:
        out = await freshdesk_call(client, cfg, "POST", "/api/v2/agents", json=agent_fields)
    if isinstance(out, dict) and out.get("error"):
        return out
    return out

@mcp.tool(annotations=_ANN_WRITE)
async def update_agent(agent_id: int, agent_fields: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
    """Update an agent in Freshdesk."""
    cfg_r = _require_freshdesk_config(ctx)
    if isinstance(cfg_r, dict):
        return cfg_r
    cfg: FreshdeskConfig = cfg_r
    async with httpx.AsyncClient(timeout=FD_HTTP_TIMEOUT) as client:
        return await freshdesk_call(
            client, cfg, "PUT", f"/api/v2/agents/{agent_id}", json=agent_fields
        )

@mcp.tool(annotations=_ANN_READ)
async def search_agents(query: str, ctx: Context) -> list[Dict[str, Any]]:
    """Search for agents in Freshdesk."""
    cfg_r = _require_freshdesk_config(ctx)
    if isinstance(cfg_r, dict):
        return cfg_r
    cfg: FreshdeskConfig = cfg_r
    return await _fd_call(cfg, "GET", "/api/v2/agents/autocomplete", params={"term": query})


@mcp.tool(annotations=_ANN_READ)
async def list_groups(ctx: Context, page: Optional[int] = 1, per_page: Optional[int] = 30) -> list[Dict[str, Any]]:
    """List all groups in Freshdesk."""
    cfg_r = _require_freshdesk_config(ctx)
    if isinstance(cfg_r, dict):
        return cfg_r
    cfg: FreshdeskConfig = cfg_r
    params = {"page": page, "per_page": per_page}
    return await _fd_call(cfg, "GET", "/api/v2/groups", params=params)

@mcp.tool(annotations=_ANN_WRITE)
async def create_group(group_fields: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
    """Create a group in Freshdesk."""
    cfg_r = _require_freshdesk_config(ctx)
    if isinstance(cfg_r, dict):
        return cfg_r
    cfg: FreshdeskConfig = cfg_r
    # Validate input using Pydantic model
    try:
        validated_fields = GroupCreate(**group_fields)
        # Convert to dict for API request
        group_data = validated_fields.model_dump(exclude_none=True)
    except Exception as e:
        return {"error": f"Validation error: {str(e)}"}

    async with httpx.AsyncClient(timeout=FD_HTTP_TIMEOUT) as client:
        out = await freshdesk_call(client, cfg, "POST", "/api/v2/groups", json=group_data)
    if isinstance(out, dict) and out.get("error"):
        return out
    return out

@mcp.tool(annotations=_ANN_READ)
async def view_group(group_id: int, ctx: Context) -> Dict[str, Any]:
    """View a group in Freshdesk."""
    cfg_r = _require_freshdesk_config(ctx)
    if isinstance(cfg_r, dict):
        return cfg_r
    cfg: FreshdeskConfig = cfg_r
    return await _fd_call(cfg, "GET", f"/api/v2/groups/{group_id}")


@mcp.tool(annotations=_ANN_WRITE)
async def create_ticket_field(ticket_field_fields: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
    """Create a ticket field in Freshdesk."""
    cfg_r = _require_freshdesk_config(ctx)
    if isinstance(cfg_r, dict):
        return cfg_r
    cfg: FreshdeskConfig = cfg_r
    async with httpx.AsyncClient(timeout=FD_HTTP_TIMEOUT) as client:
        return await freshdesk_call(
            client, cfg, "POST", "/api/v2/admin/ticket_fields", json=ticket_field_fields
        )


@mcp.tool(annotations=_ANN_READ)
async def view_ticket_field(ticket_field_id: int, ctx: Context) -> Dict[str, Any]:
    """View a ticket field in Freshdesk."""
    cfg_r = _require_freshdesk_config(ctx)
    if isinstance(cfg_r, dict):
        return cfg_r
    cfg: FreshdeskConfig = cfg_r
    return await _fd_call(cfg, "GET", f"/api/v2/admin/ticket_fields/{ticket_field_id}")


@mcp.tool(annotations=_ANN_WRITE)
async def update_ticket_field(ticket_field_id: int, ticket_field_fields: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
    """Update a ticket field in Freshdesk."""
    cfg_r = _require_freshdesk_config(ctx)
    if isinstance(cfg_r, dict):
        return cfg_r
    cfg: FreshdeskConfig = cfg_r
    async with httpx.AsyncClient(timeout=FD_HTTP_TIMEOUT) as client:
        return await freshdesk_call(
            client,
            cfg,
            "PUT",
            f"/api/v2/admin/ticket_fields/{ticket_field_id}",
            json=ticket_field_fields,
        )

@mcp.tool(annotations=_ANN_WRITE)
async def update_group(group_id: int, group_fields: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
    """Update a group in Freshdesk."""
    cfg_r = _require_freshdesk_config(ctx)
    if isinstance(cfg_r, dict):
        return cfg_r
    cfg: FreshdeskConfig = cfg_r
    try:
        validated_fields = GroupCreate(**group_fields)
        # Convert to dict for API request
        group_data = validated_fields.model_dump(exclude_none=True)
    except Exception as e:
        return {"error": f"Validation error: {str(e)}"}
    async with httpx.AsyncClient(timeout=FD_HTTP_TIMEOUT) as client:
        out = await freshdesk_call(
            client, cfg, "PUT", f"/api/v2/groups/{group_id}", json=group_data
        )
    if isinstance(out, dict) and out.get("error"):
        return out
    return out

@mcp.tool(annotations=_ANN_READ)
async def list_contact_fields(ctx: Context) -> list[Dict[str, Any]]:
    """List all contact fields in Freshdesk."""
    cfg_r = _require_freshdesk_config(ctx)
    if isinstance(cfg_r, dict):
        return cfg_r
    cfg: FreshdeskConfig = cfg_r
    return await _fd_call(cfg, "GET", "/api/v2/contact_fields")


@mcp.tool(annotations=_ANN_READ)
async def view_contact_field(contact_field_id: int, ctx: Context) -> Dict[str, Any]:
    """View a contact field in Freshdesk."""
    cfg_r = _require_freshdesk_config(ctx)
    if isinstance(cfg_r, dict):
        return cfg_r
    cfg: FreshdeskConfig = cfg_r
    return await _fd_call(cfg, "GET", f"/api/v2/contact_fields/{contact_field_id}")


@mcp.tool(annotations=_ANN_WRITE)
async def create_contact_field(contact_field_fields: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
    """Create a contact field in Freshdesk."""
    cfg_r = _require_freshdesk_config(ctx)
    if isinstance(cfg_r, dict):
        return cfg_r
    cfg: FreshdeskConfig = cfg_r
    # Validate input using Pydantic model
    try:
        validated_fields = ContactFieldCreate(**contact_field_fields)
        # Convert to dict for API request
        contact_field_data = validated_fields.model_dump(exclude_none=True)
    except Exception as e:
        return {"error": f"Validation error: {str(e)}"}
    async with httpx.AsyncClient(timeout=FD_HTTP_TIMEOUT) as client:
        return await freshdesk_call(
            client, cfg, "POST", "/api/v2/contact_fields", json=contact_field_data
        )

@mcp.tool(annotations=_ANN_WRITE)
async def update_contact_field(contact_field_id: int, contact_field_fields: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
    """Update a contact field in Freshdesk."""
    cfg_r = _require_freshdesk_config(ctx)
    if isinstance(cfg_r, dict):
        return cfg_r
    cfg: FreshdeskConfig = cfg_r
    async with httpx.AsyncClient(timeout=FD_HTTP_TIMEOUT) as client:
        return await freshdesk_call(
            client,
            cfg,
            "PUT",
            f"/api/v2/contact_fields/{contact_field_id}",
            json=contact_field_fields,
        )


@mcp.tool(annotations=_ANN_READ)
async def get_field_properties(field_name: str, ctx: Context):
    """Get properties of a specific field by name."""
    cfg_r = _require_freshdesk_config(ctx)
    if isinstance(cfg_r, dict):
        return cfg_r
    cfg: FreshdeskConfig = cfg_r
    actual_field_name = "ticket_type" if field_name == "type" else field_name
    async with httpx.AsyncClient(timeout=FD_HTTP_TIMEOUT) as client:
        fields = await freshdesk_call(client, cfg, "GET", "/api/v2/ticket_fields")
    if isinstance(fields, dict) and fields.get("error"):
        return fields
    if not isinstance(fields, list):
        return {"error": "Unexpected response from ticket_fields endpoint"}
    return next((field for field in fields if field["name"] == actual_field_name), None)


@mcp.prompt(name="create_ticket")
def create_ticket_prompt(
    subject: str,
    description: str,
    source: str,
    priority: str,
    status: str,
    email: str
) -> str:
    """Create a ticket in Freshdesk"""
    payload = {
        "subject": subject,
        "description": description,
        "source": source,
        "priority": priority,
        "status": status,
        "email": email,
    }
    return f"""
Kindly create a ticket in Freshdesk using the following payload:

{payload}

If you need to retrieve information about any fields (such as allowed values or internal keys), please use the `get_field_properties()` function.

Notes:
- The "type" field is **not** a custom field; it is a standard system field.
- The "type" field is required but should be passed as a top-level parameter, not within custom_fields.
Make sure to reference the correct keys from `get_field_properties()` when constructing the payload.
"""

@mcp.prompt(name="create_reply")
def create_reply_prompt(
    ticket_id:int,
    reply_message: str,
) -> str:
    """Create a reply in Freshdesk"""
    payload = {
        "body":reply_message,
    }
    return f"""
Kindly create a ticket reply in Freshdesk for ticket ID {ticket_id} using the following payload:

{payload}

Notes:
- The "body" field must be in **HTML format** and should be **brief yet contextually complete**.
- When composing the "body", please **review the previous conversation** in the ticket.
- Ensure the tone and style **match the prior replies**, and that the message provides **full context** so the recipient can understand the issue without needing to re-read earlier messages.
"""

@mcp.tool(annotations=_ANN_READ)
async def list_companies(ctx: Context, page: Optional[int] = 1, per_page: Optional[int] = 30) -> Dict[str, Any]:
    """List all companies in Freshdesk with pagination support."""
    cfg_r = _require_freshdesk_config(ctx)
    if isinstance(cfg_r, dict):
        return cfg_r
    cfg: FreshdeskConfig = cfg_r
    # Validate input parameters
    if page < 1:
        return {"error": "Page number must be greater than 0"}

    if per_page < 1 or per_page > 100:
        return {"error": "Page size must be between 1 and 100"}

    params = {"page": page, "per_page": per_page}
    async with httpx.AsyncClient(timeout=FD_HTTP_TIMEOUT) as client:
        companies, hdrs = await freshdesk_exchange(
            client, cfg, "GET", "/api/v2/companies", params=params
        )
        if isinstance(companies, dict) and companies.get("error"):
            return companies
        link_header = hdrs.get("Link", "")
        pagination_info = parse_link_header(link_header)
        return {
            "companies": companies,
            "pagination": {
                "current_page": page,
                "next_page": pagination_info.get("next"),
                "prev_page": pagination_info.get("prev"),
                "per_page": per_page,
            },
        }

@mcp.tool(annotations=_ANN_READ)
async def view_company(company_id: int, ctx: Context) -> Dict[str, Any]:
    """Get a company in Freshdesk."""
    cfg_r = _require_freshdesk_config(ctx)
    if isinstance(cfg_r, dict):
        return cfg_r
    cfg: FreshdeskConfig = cfg_r
    return await _fd_call(cfg, "GET", f"/api/v2/companies/{company_id}")

@mcp.tool(annotations=_ANN_READ)
async def search_companies(query: str, ctx: Context) -> Dict[str, Any]:
    """Search for companies in Freshdesk."""
    cfg_r = _require_freshdesk_config(ctx)
    if isinstance(cfg_r, dict):
        return cfg_r
    cfg: FreshdeskConfig = cfg_r
    return await _fd_call(
        cfg, "GET", "/api/v2/companies/autocomplete", params={"name": query}
    )

@mcp.tool(annotations=_ANN_READ)
async def find_company_by_name(name: str, ctx: Context) -> Dict[str, Any]:
    """Find a company by name in Freshdesk."""
    cfg_r = _require_freshdesk_config(ctx)
    if isinstance(cfg_r, dict):
        return cfg_r
    cfg: FreshdeskConfig = cfg_r
    return await _fd_call(
        cfg, "GET", "/api/v2/companies/autocomplete", params={"name": name}
    )

@mcp.tool(annotations=_ANN_READ)
async def list_company_fields(ctx: Context) -> List[Dict[str, Any]]:
    """List all company fields in Freshdesk."""
    cfg_r = _require_freshdesk_config(ctx)
    if isinstance(cfg_r, dict):
        return cfg_r
    cfg: FreshdeskConfig = cfg_r
    return await _fd_call(cfg, "GET", "/api/v2/company_fields")

@mcp.tool(annotations=_ANN_READ)
async def view_ticket_summary(ticket_id: int, ctx: Context) -> Dict[str, Any]:
    """Get the summary of a ticket in Freshdesk."""
    cfg_r = _require_freshdesk_config(ctx)
    if isinstance(cfg_r, dict):
        return cfg_r
    cfg: FreshdeskConfig = cfg_r
    return await _fd_call(cfg, "GET", f"/api/v2/tickets/{ticket_id}/summary")

@mcp.tool(annotations=_ANN_WRITE)
async def update_ticket_summary(ticket_id: int, body: str, ctx: Context) -> Dict[str, Any]:
    """Update the summary of a ticket in Freshdesk."""
    cfg_r = _require_freshdesk_config(ctx)
    if isinstance(cfg_r, dict):
        return cfg_r
    cfg: FreshdeskConfig = cfg_r
    if (ro := _check_tickets_read_only(cfg)):
        return ro
    data = {"body": body}
    async with httpx.AsyncClient(timeout=FD_HTTP_TIMEOUT) as client:
        return await freshdesk_call(
            client, cfg, "PUT", f"/api/v2/tickets/{ticket_id}/summary", json=data
        )

@mcp.tool(annotations=_ANN_DELETE)
async def delete_ticket_summary(ticket_id: int, ctx: Context) -> Dict[str, Any]:
    """Delete the summary of a ticket in Freshdesk."""
    cfg_r = _require_freshdesk_config(ctx)
    if isinstance(cfg_r, dict):
        return cfg_r
    cfg: FreshdeskConfig = cfg_r
    if (ro := _check_tickets_read_only(cfg)):
        return ro
    async with httpx.AsyncClient(timeout=FD_HTTP_TIMEOUT) as client:
        out = await freshdesk_call(
            client, cfg, "DELETE", f"/api/v2/tickets/{ticket_id}/summary"
        )
    if isinstance(out, dict) and out.get("error"):
        return out
    if isinstance(out, dict) and out.get("success"):
        return {"success": True, "message": "Ticket summary deleted successfully"}
    return out


def main() -> None:
    logging.info("Starting Freshdesk MCP server")
    transport = _transport_from_env()
    if transport == "streamable-http":
        mcp.settings.host = os.getenv("MCP_HTTP_HOST", "0.0.0.0")
        mcp.settings.port = int(os.getenv("PORT", os.getenv("FASTMCP_PORT", "8000")))
        mcp.settings.stateless_http = _bool_env("FASTMCP_STATELESS_HTTP", True)
        logging.info(
            "Streamable HTTP on %s:%s (path %s)",
            mcp.settings.host,
            mcp.settings.port,
            mcp.settings.streamable_http_path,
        )
    mcp.run(transport=transport)  # type: ignore[arg-type]

if __name__ == "__main__":
    main()
