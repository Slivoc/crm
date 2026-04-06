\# Mirakl Seller API integration guide (for Codex agent)



This guide is a \*practical integration blueprint\* for adding Mirakl Seller API connectivity into an existing app (Sproutt). It focuses on clean architecture, safe credential handling, and the “happy path” workflows you’ll actually run in production.



> Source docs: Mirakl “API Integration Guide for Sellers” (June 2024) :contentReference\[oaicite:0]{index=0} and the Mirakl Sellers API (Airbus example) :contentReference\[oaicite:1]{index=1}.



---



\## 0) What we’re integrating (mental model)



Mirakl Seller APIs let a seller automate 4 big areas:  

1\) \*\*Catalog\*\* (products + offers)  

2\) \*\*Customer service\*\* (messaging/threads)  

3\) \*\*Order management \& shipping\*\*  

4\) \*\*Accounting / invoicing\*\* :contentReference\[oaicite:2]{index=2}



Important distinction:

\- \*\*Product\*\* = “what the thing is” (name, attributes, images, taxonomy etc.)

\- \*\*Offer\*\* = seller-specific commercial data (price, stock, condition/state, lead time, etc.) :contentReference\[oaicite:3]{index=3}



---



\## 1) Integration prerequisites (what you must have)



\### Environments

Mirakl operators typically provide:

\- \*\*Test (pre-prod) environment\*\*

\- \*\*Production environment\*\*



API keys are \*\*environment-specific\*\*. You generate one for test and another for prod. :contentReference\[oaicite:4]{index=4}



\### Auth

Mirakl Seller APIs accept authenticated calls using the seller’s API key in the HTTP header:

\- `Authorization: {YOUR\_MIRAKL\_API\_KEY}` :contentReference\[oaicite:5]{index=5}



---



\## 2) Recommended architecture inside the app



\### 2.1 Modules (keep these boundaries)

Create a small integration package with these responsibilities:



\*\*A) `mirakl\_client`\*\*

\- Pure HTTP client (base URL, headers, retries, timeouts, pagination helpers)

\- No business logic



\*\*B) `mirakl\_service`\*\*

\- Business workflows: “sync offers”, “pull new orders”, “confirm shipment”, “fetch error report”

\- Translates between your internal models and Mirakl’s API payloads/files



\*\*C) `mirakl\_storage`\*\*

\- Persistence for: credentials reference, last sync cursors, import IDs, job logs, error reports metadata, etc.



\*\*D) `mirakl\_jobs`\*\*

\- Scheduled/queued tasks (polling imports, order pulls, retry failed operations)

\- Idempotency + backoff



\### 2.2 Data you should store (minimum viable)

For each connected shop/environment:

\- `operator\_base\_url` (test/prod)

\- `shop\_id` (optional; needed if one user can access multiple shops) :contentReference\[oaicite:6]{index=6}

\- credential reference (see secrets section)

\- `last\_orders\_sync\_at`

\- `last\_offers\_export\_at` (if using OF51 “diff export”)

\- `last\_error\_report\_check\_at`

\- import tracking table: `import\_id`, `type` (offers/products), `status`, `has\_error\_report`, counters



---



\## 3) Secrets \& credential handling (don’t be sloppy)



\- Never store API keys in plaintext DB columns.

\- Use your existing secret mechanism (env vars, OS keychain, Vault-like, or encrypted-at-rest table).

\- Keep separate credentials per environment.

\- Add a “test connection” button that calls a harmless endpoint (e.g. account / basic list) to validate auth early.



---



\## 4) Core workflows you’ll likely implement first



\### 4.1 Offers (price/stock) — start here

Mirakl’s recommended offer management path is:

1\. \*\*OF01\*\* Import offers file (CSV/XML/XLSX)  

2\. \*\*OF02\*\* Check import status/statistics  

3\. \*\*OF03\*\* Download error report if any  

(Then optionally OF51/OF21/OF22/OF24/OF61 for export/list/detail/manual update/conditions) :contentReference\[oaicite:7]{index=7}



\*\*Implementation notes\*\*

\- OF01 returns an `import\_id`; your job runner should poll OF02 until terminal status, then pull OF03 if `has\_error\_report=true`. :contentReference\[oaicite:8]{index=8}

\- Offer file has mandatory fields when creating offers (sku/product-id/product-id-type/price/state/quantity etc.), but \*\*“sku” alone can be sufficient when updating\*\* (depends on operation). :contentReference\[oaicite:9]{index=9}

\- Validate and normalize decimals (dot separator) and prevent forbidden characters (e.g. `/` in some identifiers). :contentReference\[oaicite:10]{index=10}



\*\*How this maps into the app\*\*

\- Internal: “Offer” records (SKU, product reference, price, qty, lead time, condition/state).

\- Export pipeline: build a CSV exactly as Mirakl expects.

\- Import pipeline: push OF01 → track import job → attach error report to a job log that is visible in UI.



\### 4.2 Catalog (products) — only if you need it

If you need to create products (not only offers), you’ll generally:

\- Pull the operator taxonomy/config (categories, attributes, value lists)

\- Map your internal fields to operator requirements

\- Import products, then offers



The doc calls out:

\- \*\*H11\*\* category tree

\- \*\*PM11\*\* attribute configuration

\- \*\*VL11\*\* value lists :contentReference\[oaicite:11]{index=11}



Mirakl also supports doing the mapping through their back office “Mapping Wizard” (one-time, unless new categories are introduced). :contentReference\[oaicite:12]{index=12}



\*\*Practical approach\*\*

\- If you don’t need full product onboarding, skip this initially and focus on offers + orders.

\- If you do, model the mapping layer as a first-class object in your system (so you can update it without rewriting code).



---



\## 5) Order lifecycle automation



Mirakl order lifecycle (single shipment) is commonly:

\- Get orders (OR11)

\- Accept/refuse (OR21/OR23 depending on flow)

\- Update tracking (OR24)

\- Confirm shipment

\- Upload documents (OR73/OR74 depending on doc type)

\- Customer care threads (OR43 + messaging APIs) :contentReference\[oaicite:13]{index=13}



APIs commonly referenced for order management include:

\- OR11 (list orders)

\- OR21/OR23 (accept/refuse)

\- OR24 (tracking update)

\- OR74 (order evaluation)

\- OR73/OR74 (documents)

\- ST01 and ST23/24 if multi-shipment is enabled :contentReference\[oaicite:14]{index=14}



\*\*Implementation notes\*\*

\- Make “order pull” incremental (store last sync time / cursor).

\- Ensure idempotency on shipment confirmation/tracking updates (don’t double-confirm).

\- Store raw Mirakl payloads for traceability and debugging.



---



\## 6) Messaging / customer service



Messaging APIs support:

\- listing threads, replying, attachments

\- order-related discussions can be created (OR43) and answered via messaging endpoints :contentReference\[oaicite:15]{index=15}



In your app:

\- Treat threads as a conversation object linked to orders (and optionally offers).

\- Store attachment metadata and provide a “download” proxy route if needed.



---



\## 7) Invoicing \& accounting



Two common situations are described:



\### 7.1 “Invoice as message attachment” (if order invoicing is disabled)

\- PDFs submitted via OR74 (or via back office)

\- Filenames must be US-ASCII characters :contentReference\[oaicite:16]{index=16}



\### 7.2 If invoicing is enabled (document requests)

Workflow includes:

\- Listing document requests (DR11)

\- Listing request lines (DR12)

\- Generating invoice data and issuing the document (DR74) :contentReference\[oaicite:17]{index=17}



Accounting documents/transactions:

\- IV01/IV02 and TL02 exist for accounting docs and transaction lines :contentReference\[oaicite:18]{index=18}



\*\*Implementation notes\*\*

\- Model invoicing as a separate job pipeline (generate → upload → mark issued).

\- Store document request state and your own invoice number sequencing if required.



---



\## 8) Error handling \& observability (non-negotiable)



\### 8.1 Import error reports

For any file import workflow (offers/products/pricing), build a consistent “Import Job” pattern:

\- submit → store `import\_id` → poll status → fetch error report if present :contentReference\[oaicite:19]{index=19}



\### 8.2 Logging

Log at three levels:

\- integration job summary (counts, durations)

\- request/response metadata (status codes, endpoint, correlation IDs)

\- payload snapshots (only where safe; redact secrets)



\### 8.3 UI/UX expectation

Add a simple “Integration Health” view:

\- last successful sync per workflow

\- last errors + downloadable report

\- key config (test/prod base URL, shop id if applicable)



---



\## 9) Testing strategy (fast feedback loop)



\### 9.1 Postman “known good” baseline

Mirakl explicitly recommends using Postman for early integration testing. Configure:

\- `SHOP\_KEY` (API key)

\- `URL` (operator test environment base URL) :contentReference\[oaicite:20]{index=20}



\### 9.2 Automated tests

\- Unit tests: mapping transforms, CSV generation, validation rules

\- Integration tests (against test env): smoke test auth + one offer import + poll + error report fetch

\- Contract tests: assert you send required headers and content types



---



\## 10) Suggested build order (keep scope under control)



1\) \*\*Connection \& auth\*\* (test env first)  

2\) \*\*Offers pipeline\*\* (OF01 → OF02 → OF03)  

3\) \*\*Offers export/list\*\* (OF51 + optionally OF21/OF22)  

4\) \*\*Orders pull + accept/reject\*\* (OR11 + accept/refuse)  

5\) \*\*Shipping updates\*\* (tracking + confirm)  

6\) \*\*Messaging threads\*\* (optional but valuable)  

7\) \*\*Invoicing\*\* (only if required by marketplace setup)



---



\## 11) “Gotchas” checklist



\- Separate API keys per environment (test vs prod). :contentReference\[oaicite:21]{index=21}

\- Treat file imports as async: you \*must\* poll status and handle error reports. :contentReference\[oaicite:22]{index=22}

\- Don’t assume product and offer are the same thing; they’re managed differently. :contentReference\[oaicite:23]{index=23}

\- Keep imports idempotent (repeatable) and track what you’ve already pushed.

\- Watch encoding/format rules (e.g., invoice filenames must be US-ASCII). :contentReference\[oaicite:24]{index=24}



---



\## 12) Deliverables the agent should produce in the codebase



\- `integrations/mirakl/`

&nbsp; - `client.py` (HTTP client)

&nbsp; - `schemas.py` or `models.py` (typed request/response shapes)

&nbsp; - `services/offers.py`, `services/orders.py`, `services/invoicing.py`

&nbsp; - `jobs/` (pollers, sync tasks)

&nbsp; - `migrations/` (integration tables)

&nbsp; - `admin\_ui/` hooks (status + logs)

\- Docs:

&nbsp; - “How to connect a Mirakl shop”

&nbsp; - “How to run test sync”

&nbsp; - “How to read error reports”

## 13) Next jobs (post-initial wiring)

1) Set `MIRAKL_BASE_URL`, `MIRAKL_API_KEY` (and optionally `MIRAKL_SHOP_ID`) and hit `/marketplace/mirakl/health`.

2) Confirm the Mirakl offer CSV field names match your operator's requirements and adjust `DEFAULT_FIELDS` in `integrations/mirakl/services/offers.py`.

3) Add background polling jobs + persistence for import IDs if you want full OF01/OF02/OF03 tracking.

---

## 14) CRM Airbus Marketplace Notes (Apr 2026)

These notes are repo-specific and capture what was learned while debugging the
current Airbus marketplace uploads.

### Environment constraints

- The app currently has Mirakl API access to dev/pre-prod only.
- Production uploads are being done manually through Airbus file uploads.
- Because prod API access is not available, prod product IDs cannot be fetched
  on demand.
- That means prod offer uploads must usually rely on `mpnTitle` matching unless
  a product ID was learned from a previously exported Airbus file.

### Local files that matter

- `docs/export-products-20260403203228.xlsx`
  - Airbus product export.
  - Uses the Airbus product template format and includes a mapping row on row 2.
  - Has an `id` column, but many rows have blank `id`.
- `docs/product-import-errors-file-20260403190000.csv`
  - Product import error report.
  - 627 failed rows collapsed into two repeated errors:
    - `MCM-04031|The product identifiers match multiple products from the same provider.`
    - `MCM-04000|MPN Title with multiple values : [NAS9305B-4-02 NSA53114-32ADL]`
- `docs/offers-import-error-report-1162-9931.csv`
  - Offer import error report.
  - Main failure classes:
    - `The product does not exist`
    - `This product is not available for sale`
    - invalid `leadtime-to-ship`
    - `The product linked to the new offer is different from the product linked to the existing offer.`
- `docs/Copy of All Hardware References sept 2025.xlsx`
  - Airbus hardware reference dictionary with:
    - `mpnTitle`
    - `alternativePartRefList`
  - Useful for canonicalizing aliases.
  - Does not contain Airbus product IDs.

### What was confirmed

- The current product import problem is mostly identifier matching, not missing
  fields.
- Many aerospace hardware rows are ambiguous when matched directly by
  `mpnTitle`.
- Some offer upload failures happen because the CRM part number is an Airbus
  alternate reference rather than the canonical `mpnTitle`.
- Example:
  - `ASNA0045BC100L` appears in the Airbus hardware references workbook as an
    alternate reference for canonical `ASNA0045-100BCL`.

### Current code behavior

- `routes/marketplace.py`
  - Offer export prefers stored marketplace product IDs when they exist.
  - If no stored product ID exists, offer export falls back to `mpnTitle`.
  - A local canonicalization layer now resolves `mpnTitle` from
    `docs/Copy of All Hardware References sept 2025.xlsx`:
    - exact `mpnTitle` match -> use as-is
    - alternate reference match -> rewrite to canonical `mpnTitle`
    - no match -> keep raw part number and mark unresolved
  - Product/detail imports store Airbus `id` into
    `part_numbers.mkp_offer_product_id` when the imported file contains it.
  - Offer imports submitted through the app also persist resolved product IDs
    back into `part_numbers` for future use.
  - `leadtime-to-ship` is now clamped to a positive integer (minimum `1`).
- `templates/marketplace_export.html`
  - Preview/export now uses the same canonicalized `mpnTitle` logic as the
    backend.
  - Load status summarizes how many parts are exact matches, rewritten from alt
    refs, or unresolved.

### Practical workflow for now

1. Load parts in the export page.
2. Check the load status summary for:
   - exact `mpnTitle`
   - rewritten from alternate refs
   - unresolved
3. For offer uploads:
   - if a stored marketplace product ID exists, use it
   - otherwise use canonicalized `mpnTitle`
4. For product uploads:
   - use them only for parts that appear genuinely new or unresolved in prod
5. After any manual prod product creation, pull a fresh Airbus product export
   if possible and import it locally so learned IDs are stored in CRM.

### Known limitation

- Without prod API access or a prod export with populated product IDs, existing
  no-offer products still cannot be resolved perfectly.
- `mpnTitle` remains the only practical matching key for those rows, so alias
  normalization is the main mitigation rather than a full fix.



---



If you want, paste your existing `agents.md` style (or the conventions you want the agent to follow), and I’ll adapt this guide to match your repo’s tone/structure without making it Sproutt-specific.



