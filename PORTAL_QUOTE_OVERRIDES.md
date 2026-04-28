# Portal Quote Overrides

This note explains how portal quote request line overrides work in the CRM.

## Purpose

Portal quote requests start with the customer's original requested lines. Sales can then override what is shown back to the customer without losing the original request.

The main use cases are:

- force part numbers to uppercase on intake
- match parts independent of punctuation or special characters
- allow sales to change quantity
- allow sales to quote alternate parts
- allow sales to add customer-visible line notes
- allow sales to add extra lines after the original request

## Core Tables

### `portal_quote_requests`

Top-level portal quote request record.

Important fields:

- `reference_number`
- `customer_reference`
- `customer_notes`
- `status`
- `parts_list_id`

### `portal_quote_request_lines`

Customer-facing portal response lines.

Original fields:

- `line_number`
- `part_number`
- `base_part_number`
- `quantity`
- `quoted_price`
- `quoted_currency_id`
- `quoted_lead_days`
- `status`

Override fields added for portal response control:

- `quoted_part_number`
- `line_notes`
- `manufacturer`
- `revision`
- `certs`

Migration:

- `migrations/20260428_add_portal_quote_line_overrides.sql`

### `parts_list_lines`

Linked operational sourcing/costing lines.

Important here:

- `line_number`
- `customer_part_number`
- `base_part_number`
- `quantity`
- `chosen_qty`

### `customer_quote_lines`

Detailed quoting module output. This is the fallback source for quoted part number and notes when the portal line has no explicit override.

Important fields:

- `display_part_number`
- `quoted_part_number`
- `line_notes`
- `quote_price_gbp`
- `quoted_status`

## Matching Model

### Normalized matching

Part matching uses `create_base_part_number(part_number)`.

That function:

- strips non-alphanumeric characters
- uppercases the result

Example:

- `MS28775-4`
- `ms28775/4`
- `MS 28775 4`

All normalize to the same base key.

### Linkage rule

Portal request lines are now linked to `parts_list_lines` by `line_number`, not by `base_part_number`.

Reason:

- sales may quote an alternate part
- the original request line should still stay attached to the same operational line
- quantity changes should update the corresponding parts list line cleanly

## Precedence Rules

### Requested part number

The original customer request always lives in:

- `portal_quote_request_lines.part_number`

This should be treated as immutable customer input for display/reference purposes.

### Quoted part number shown to customer

Effective quoted part number precedence:

1. `portal_quote_request_lines.quoted_part_number`
2. `customer_quote_lines.quoted_part_number`
3. `customer_quote_lines.display_part_number`
4. original requested `portal_quote_request_lines.part_number`

### Manufacturer shown to customer

Effective manufacturer precedence:

1. `portal_quote_request_lines.manufacturer`
2. `customer_quote_lines.manufacturer`
3. empty string

### Revision shown to customer

Effective revision precedence:

1. `portal_quote_request_lines.revision`
2. `parts_list_lines.revision`
3. empty string

### Certifications shown to customer

Effective certs precedence:

1. `portal_quote_request_lines.certs`
2. `customer_quote_lines.standard_certs`
3. empty string

### Customer-visible line notes

Effective line notes precedence:

1. `portal_quote_request_lines.line_notes`
2. `customer_quote_lines.line_notes`
3. empty string

### Quantity shown to customer

Customer-facing quantity is stored directly on:

- `portal_quote_request_lines.quantity`

When sales update quantity on the portal request page, the CRM also updates:

- `parts_list_lines.quantity`
- `parts_list_lines.chosen_qty`

for the linked `line_number`.

## Request Lifecycle

### 1. Intake from customer portal

In `routes/portal_api.py`:

- incoming part numbers are uppercased
- `base_part_number` is derived with `create_base_part_number`
- rows are created in both:
  - `parts_list_lines`
  - `portal_quote_request_lines`

### 2. Sales works request in `/portal-admin/requests/<id>`

Sales can:

- edit quantity
- enter quoted part number
- edit manufacturer
- edit revision
- edit certs
- enter customer-visible notes
- load price/lead from parts list
- load price/lead plus alt/manufacturer/revision/certs/notes from customer quote
- mark line `quoted`
- mark line `no_bid`
- add new lines

### 3. Customer views quote in portal

In `routes/portal_api.py` `GET /quote/requests/<id>`:

- requested part is preserved separately
- effective quoted part is substituted into `part_number`
- `requested_part_number` is returned when quoted part differs
- effective `manufacturer` is returned
- effective `revision` is returned
- effective `certs` are returned
- effective `line_notes` are returned

## Email Update Behavior

On `/portal-admin/requests/<id>`, the update email action sends through the logged-in salesperson's Graph mailbox.

The update email includes:

- salesperson comment
- request metadata
- table of customer-facing lines with status in:
  - `quoted`
  - `no_bid`

The table uses the effective override values described above, including:

- quoted part
- manufacturer
- revision
- certs
- notes

## Important Implementation Notes

- The portal admin and portal API code both guard optional override columns with `_table_has_column(...)` so code can deploy before or after the migration.
- If the migration has not run yet, the UI still works, but explicit portal-line alt part and line-note persistence will not be available.
- Customer quote data is treated as fallback, not the final source of truth, once a portal-line override exists.

## Files To Read

- `routes/portal_api.py`
- `routes/portal_admin.py`
- `templates/portal_request_detail.html`
- `migrations/20260428_add_portal_quote_line_overrides.sql`
