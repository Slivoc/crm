# Customer Portal: "Your Recent Searches" Backend Handoff

This document describes the backend that now exists in CRM so the portal frontend can implement a **Your recent searches** view.

## What was added

## 1) New data model for per-line snapshots
A new table stores what the customer saw at search time per line:

- **Table:** `portal_search_history_lines`
- **Migration:** `migrations/20260501_add_portal_search_history_lines.sql`

Columns captured:
- `search_history_id` (links to `portal_search_history.id`)
- `customer_id`
- `line_number`
- `requested_part_number`
- `base_part_number`
- `quantity`
- `estimated_price`
- `estimated_currency`
- `price_source`
- `has_price` (true/false at search time)
- `created_at`

Indexes included:
- `idx_portal_search_history_lines_search_history_id`
- `idx_portal_search_history_lines_customer_base_created`

## 2) Snapshot capture in quote analysis flow
In `routes/portal_api.py`:

- `analyze_quote` now stores per-line snapshot records for **manual quote searches** (`source == 'manual_quote_search'`) after results are computed.
- Snapshot writer helper:
  - `log_search_result_snapshot(search_history_id, customer_id, results)`

This preserves the exact estimate that was shown to the customer at that moment.

## 3) New API endpoint for recent searches

### Endpoint
`GET /api/portal/search/recent`

### Auth
Same portal auth as other `/api/portal/*` endpoints (JWT + API key flow via existing decorator).

### Query params
- `limit` (optional, int): defaults to `20`, clamped to `1..100`

### Behavior
- Returns recent `quote_analysis` searches for the logged-in portal user.
- Prioritizes lines where:
  - there was **no price when searched** (`had_price_when_searched = false`), and
  - a **current price now exists** (`current_estimated_price != null`)
- Sort order:
  1. `newly_priced DESC`
  2. `date_searched DESC`
  3. `line_number ASC`

### Response shape
```json
{
  "success": true,
  "searches": [
    {
      "search_history_id": 123,
      "date_searched": "2026-05-01T10:11:12Z",
      "parts_count": 4,
      "line_number": 1,
      "requested_part_number": "ABC-123",
      "base_part_number": "ABC123",
      "quantity": 2,
      "searched_estimated_price": null,
      "searched_estimated_currency": "USD",
      "searched_price_source": null,
      "had_price_when_searched": 0,
      "newly_priced": 1,
      "current_estimated_price": 12.34,
      "current_estimated_currency": "USD",
      "current_price_source": "stock"
    }
  ]
}
```

Notes:
- `had_price_when_searched` and `newly_priced` may come back as numeric booleans depending on DB driver.
- `current_*` values are derived from the latest snapshot for the same `customer_id + base_part_number`.

---

## Frontend integration guidance (portal app)

1. Call `GET /api/portal/search/recent?limit=50` on the Recent Searches page.
2. Group rows by `search_history_id` for display cards/sections.
3. Highlight rows with `newly_priced = 1`.
4. Show both:
   - **Price at search time**: `searched_estimated_price` (+ currency/source)
   - **Current price now**: `current_estimated_price` (+ currency/source)
5. If `searched_estimated_price` is null and `current_estimated_price` is not null, render a “Now priced” badge.

---

## Caveats / TODOs for portal agent

- Backend currently returns line-level rows (not nested by search). If preferred, frontend should group client-side.
- If you want a fully nested response (`search -> lines[]`) for simpler UI mapping, we can add that in CRM API.
- This backend does not yet include paging tokens; only `limit` is supported.

---

## Files touched in CRM

- `routes/portal_api.py`
- `migrations/20260501_add_portal_search_history_lines.sql`
- `docs/portal_recent_searches_backend_handoff.md` (this doc)
