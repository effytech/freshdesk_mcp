# Design: `agent_id` filtering on `get_tickets`

**Date:** 2026-06-04
**Status:** Approved

## Problem

Callers wanting tickets assigned to a specific agent currently page through
`get_tickets` and filter client-side. Freshdesk's List Tickets endpoint
(`/api/v2/tickets`) does not support filtering by agent — only the Filter/Search
endpoint (`/api/v2/search/tickets`) does, via the query DSL already exposed by
`search_tickets`. That DSL is hard for calling LLMs to discover reliably.

## Solution

Add `agent_id: Optional[int] = None` to `get_tickets` in
`src/freshdesk_mcp/server.py`.

- `agent_id` not set → existing behavior, unchanged (`GET /api/v2/tickets`).
- `agent_id` set → route to `GET /api/v2/search/tickets?query="agent_id:<id>"`,
  then normalize the response to the same `{tickets, pagination}` shape
  `get_tickets` already returns, plus a `total` count from the search response.

## Validation (fail-fast errors)

When `agent_id` is set, return a `{"error": ...}` dict (matching existing error
style) if any incompatible param is also set:

- `filter`, `email`, `requester_id`, `company_id`, `updated_since`, `order_by`,
  `order_type`, `include` — not supported by the search endpoint.
- `per_page` ≠ 30 — search endpoint is fixed at 30 results/page.
- `page` > 10 — search endpoint caps at 10 pages.

The error message names the offending params and points to `search_tickets`
for complex queries. No HTTP call is made on validation failure.

## Pagination normalization

Search returns `{"total": N, "results": [...]}`. Compute:

- `next_page = page + 1` if `page * 30 < total` and `page < 10`, else `None`
- `prev_page = page - 1` if `page > 1`, else `None`

Return shape: `{"tickets": results, "total": N, "pagination": {current_page,
next_page, prev_page, per_page: 30}}`.

## Docstring

Document `agent_id` in the `get_tickets` docstring: what it does, its
constraints (30/page fixed, max 10 pages, list of incompatible params), so
calling LLMs use it correctly without trial and error.

## Testing

New file `tests/test_get_tickets_agent_filter.py` using `unittest` with a
mocked `httpx.AsyncClient`, testing the real `get_tickets` function:

1. `agent_id=123` → request goes to `/api/v2/search/tickets` with query
   `"agent_id:123"`.
2. Search response is normalized to `{tickets, total, pagination}` with correct
   `next_page`/`prev_page` computation (including the `page * 30 < total` and
   10-page-cap boundaries).
3. Each incompatible param combined with `agent_id` → clear error, no HTTP
   call made.
4. Without `agent_id` → request still goes to `/api/v2/tickets` (regression
   guard).

## Out of scope (YAGNI)

- No other structured search params (status, group_id, priority, ...).
- No `agent_id:null` / unassigned-ticket support.
- No changes to `search_tickets`.
