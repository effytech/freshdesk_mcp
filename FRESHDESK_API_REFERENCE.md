# Freshdesk API v2 Reference

## General Pagination Rules

- Default: 30 objects per page
- Max per_page: 100
- Use `page` parameter to paginate (starts at 1)
- Link header contains next page URL
- **Deep pagination warning**: Avoid page numbers over 500 - extremely slow response times

## Autocomplete API Limitations

All autocomplete endpoints (`/autocomplete`) have these limitations:
- Returns LIMITED results (not paginated, typically ~10-20 matches)
- Searches by PREFIX only (case insensitive)
- Cannot search with substrings: "John" works, "ohn" does NOT
- Returns minimal fields (usually just id and name)

## Tickets API

### GET /api/v2/tickets (List Tickets)

**CRITICAL: 30-day default limit** - Only tickets created in past 30 days returned by default.
Use `updated_since` for older tickets.

Parameters:
- `filter`: 'new_and_my_open', 'watching', 'spam', 'deleted'
- `requester_id`: Filter by requester
- `email`: Filter by requester email
- `company_id`: Filter by company
- `updated_since`: ISO 8601 timestamp (e.g., '2024-01-01T00:00:00Z')
- `order_by`: 'created_at', 'due_by', 'updated_at', 'status'
- `order_type`: 'asc' or 'desc' (default: desc)
- `include`: 'stats', 'requester', 'description', 'company' (costs +1 API credit each)
- `page`: Page number (default: 1)
- `per_page`: Results per page (1-100, default: 30)

Limits:
- Max 300 pages (30,000 tickets)
- Description requires `include=description` for accounts created after 2018-11-30

### GET /api/v2/search/tickets (Filter/Search Tickets)

**Query string max: 512 characters**
**Max results: 300 (30 per page × 10 pages)**

Supported query fields:
- status, priority, group_id, requester_id, type, tag
- created_at, updated_at, due_by (with :> and :< operators)
- Custom fields with cf_ prefix

NOT supported: company_id, description, subject, responder_id

### GET /api/v2/tickets/{id}

Parameters:
- `include`: 'stats', 'conversations', 'requester', 'company'

**CRITICAL LIMITATION for include=conversations:**
- Returns only the **OLDEST 10 conversations**, sorted by created_at ascending
- This is NOT the full history - use GET /api/v2/tickets/{id}/conversations for complete data

## Conversations API

### GET /api/v2/tickets/{id}/conversations

Lists ALL conversations (notes, replies) for a ticket with pagination.

Parameters:
- `page`: Page number (default: 1)
- `per_page`: Results per page (1-100, default: 30)

Returns: Array of conversation objects containing:
- `id`: Conversation ID
- `body`: HTML body
- `body_text`: Plain text body
- `incoming`: Boolean, true if from customer
- `private`: Boolean, true if private note
- `user_id`: ID of user who created it
- `support_email`: Support email used
- `source`: Source type (0=Reply, 2=Note, etc.)
- `to_emails`, `from_email`, `cc_emails`, `bcc_emails`: Email addresses
- `created_at`, `updated_at`: Timestamps
- `attachments`: Array of attachment objects
- `ticket_id`: Parent ticket ID

Pagination: Standard Link header pagination. Use page parameter to iterate.

## Contacts API

### GET /api/v2/contacts

Filter parameters:
- `email`: Filter by email
- `mobile`: Filter by mobile
- `phone`: Filter by phone
- `state`: 'blocked', 'deleted', 'unverified', 'verified'
- `company_id`: Filter by company
- `_updated_since`: ISO 8601 timestamp

Pagination: page, per_page (max 100)

### GET /api/v2/search/contacts

Query max: 512 characters
Max results: 300 (30 per page × 10 pages)

### GET /api/v2/contacts/autocomplete

Parameter: `term` - search term for autocomplete

## Companies API

### GET /api/v2/companies

Pagination: page, per_page (max 100)

### GET /api/v2/search/companies

Query max: 512 characters
Max results: 300 (30 per page × 10 pages)

### GET /api/v2/companies/autocomplete

Parameter: `name` - company name to search

## Agents API

### GET /api/v2/agents

Filter parameters:
- `email`: Filter by email
- `mobile`: Filter by mobile
- `phone`: Filter by phone
- `state`: Filter by state

Pagination: page, per_page (max 100)

### GET /api/v2/agents/autocomplete

Parameter: `term` - search term

## Groups API

### GET /api/v2/groups

Pagination: page, per_page (max 100)

## Solutions API

### Categories: GET /api/v2/solutions/categories
No pagination - returns all

### Folders: GET /api/v2/solutions/categories/{id}/folders
No pagination - returns all in category

### Articles: GET /api/v2/solutions/folders/{id}/articles
No pagination - returns all in folder

### Search: GET /api/v2/search/solutions?term={keyword}

## Canned Responses API

### GET /api/v2/canned_response_folders
Returns all folders

### GET /api/v2/canned_response_folders/{id}/responses
Returns all responses in folder

## Error Codes

- 400: Bad Request (validation error)
- 401: Unauthorized (invalid API key)
- 403: Access Denied (insufficient permissions)
- 404: Not Found
- 429: Rate Limited (check Retry-After header)
- 5xx: Server Error
