# AGENTS.md

This repo recently migrated to PostgreSQL and the costing page was slow.
Below is a concise summary of fixes applied so future sessions can pick up
context quickly.

## What was slow
- Costing page quote indicators were loaded with one request per line, which
  created lots of DB connections and made the page feel slow even when the DB
  was not.
- The per-line quotes modal occasionally crashed because `unit_price` arrived
  as a string, so `.toFixed()` failed.

## What was changed
- `routes/parts_list.py`
  - The line quotes endpoint now runs on a single cursor per request.
  - Added a bulk endpoint:
    `/parts-lists/<int:list_id>/lines/quote-availability`
    This returns per-line quote counts for the costing page in one query,
    instead of one request per line.
- `static/js/parts_list_costing.js`
  - The costing page uses the bulk quote-availability endpoint.
  - Price formatting now parses numbers safely so strings or NULLs do not
    throw in `.toFixed()`.
- `migrations/20251220_add_parts_list_quote_indexes.sql`
  - Added indexes on `parts_list_supplier_quote_lines`, `parts_list_lines`,
    and `parts_list_supplier_quotes` to support the new access patterns.

## Files touched
- `routes/parts_list.py`
- `static/js/parts_list_costing.js`
- `migrations/20251220_add_parts_list_quote_indexes.sql`

## Notes for future sessions
- If the costing page is still slow, the next step is to profile the
  `parts_list_costing` line query (it has multiple subqueries) and consider
  collapsing them into joins or caching.
- Consider running `EXPLAIN ANALYZE` for the bulk endpoint to confirm index
  usage once data volume grows.

## UI notes (Jan 2026)
### Sticky headers/columns fixes (customer quote simple)
- Sticky headers were being overridden by global table header styles in
  `static/solid-colors.css`. Targeting `thead th` in
  `templates/customer_quote_simple.html` keeps the headers sticky even with
  global `.table thead th` rules.
- The right-side sticky columns use `position: sticky` + `right` offsets, so
  their cells need explicit backgrounds (and `background-clip`) to avoid
  bleed-through when they overlap the scrolling table.
- Horizontal page scroll was fixed by allowing the main content flex item to
  shrink (`min-width: 0` in `static/solid-colors.css`). This keeps overflow
  inside the table container instead of the body.

## Postgres migration follow-ups (Dec 2025)
### What was slow
- All pages stalled, including static assets (304s), after the Postgres migration.
- Base template customer search was slow due to N+1 association requests.
- Salespeople contacts page was slow and error-prone with Postgres.
- Planner page did heavy cross-customer queries (first order + 24-month history).

### Root causes found
- Flask-Login `user_loader` and `@before_request` hit the DB on every request,
  including static assets, which magnified Postgres connection/queue latency.
- Customer search fetched `/customers/<id>/associations/api` per result.
- Contacts list used multiple per-row subqueries for communications.
- Planner preloaded first-order dates for all customers, not just relevant IDs,
  and `unassigned_customers` recomputed planner data each call.

### Fixes applied
- `app.py`
  - Skip DB work for static requests in `load_user` and `before_request`.
  - Cache `get_salespeople()` results for 60s to reduce per-request load.
- `routes/customers.py`
  - Added `/customers/associations/bulk` to return association counts in one request.
- `templates/base.html`
  - Customer search now calls the bulk association endpoint once per search.
- `models/part_3.py`
  - Contacts list uses CTEs for communication counts/latest updates in Postgres.
  - Casted `contact_communications.date` to timestamp to avoid type mismatch.
- `db.py`
  - Fixed pooled connection handling when a pooled connection is closed.
- `models/part_4.py`
  - Fixed duplicate GROUP BY in `get_overdue_contacts_count` for Postgres.
- `routes/salespeople.py` / `templates/salespeople/planner.html`
  - Planner now limits first-order lookup to relevant customers and passes
    excluded IDs to `unassigned_customers` to avoid recomputing planner data.
