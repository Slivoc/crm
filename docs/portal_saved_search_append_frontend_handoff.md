# Portal Frontend Handoff: "Add Search to Existing RFQ"

This backend change is now live in CRM at:

- `POST /api/portal/quote/submit`

## What changed

The endpoint still supports the **existing submit flow** (new RFQ), and now also supports an **append flow** to add current search lines to an existing RFQ that the same portal user owns.

## Request contract

### 1) Existing submit flow (unchanged)

Send:

```json
{
  "customer_reference": "Optional",
  "notes": "Optional",
  "parts": [
    { "part_number": "ABC-123", "quantity": 2, "target_price_gbp": 11.5 }
  ]
}
```

- Response message: `"Quote request submitted successfully"`.

### 2) New append-to-existing-RFQ flow

Send:

```json
{
  "existing_request_id": 456,
  "parts": [
    { "part_number": "ABC-123", "quantity": 2, "target_price_gbp": 11.5 }
  ]
}
```

- `existing_request_id` enables append mode.
- Response message: `"Quote request updated successfully"`.

## Validation/behavior notes

- `parts` is required in both modes.
- `existing_request_id` must belong to the logged-in portal user/customer.
- RFQs in `quoted`, `completed`, or `cancelled` status are rejected for append.
- Appended lines are added after the current max `line_number` on the RFQ.
- `target_price_gbp` is still carried into `customer_quote_lines.target_price_gbp` for pricing context.

## Frontend implementation suggestion

On the Portal quote/search page:

1. Keep current "Submit RFQ" UX for creating a new RFQ.
2. Add a second CTA like "Add to Existing RFQ".
3. Let user pick an existing RFQ id (`existing_request_id`) from their RFQ list.
4. Reuse the exact same `parts` payload from current flow.
5. Call the same endpoint (`/api/portal/quote/submit`) with `existing_request_id`.

If useful, the UI can branch on response message:

- `"Quote request submitted successfully"` => created new
- `"Quote request updated successfully"` => appended existing
