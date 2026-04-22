# Mirakl / Airbus Marketplace Notes

This file is the repo-specific working note for the Airbus marketplace
integration. It is intentionally practical and should reflect what has actually
been observed in this repo, not generic Mirakl theory.

## Current setup

- The app currently uses Mirakl API access against dev / pre-prod.
- Production uploads are still being done manually through Airbus file uploads.
- Because prod API access is limited, prod product IDs cannot be fetched on
  demand from the app today.
- In practice, prod offer uploads still rely on:
  - a stored marketplace product ID when CRM already knows one
  - otherwise canonicalized `mpnTitle` matching

## Files to keep in mind

- `docs/2026-04-15 10-01-39.srt`
  - Transcript of the April 15, 2026 Airbus call.
  - Best source for operator-side clarifications about the offer errors.
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
  - Earlier offer import error report.
  - Main failure classes were:
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
- `docs/[16.04.26] Masterlist Hardware.xlsx`
  - Newer Airbus hardware master list.
  - Treat this as the current approved hardware universe until replaced by a
    newer Airbus list.
  - Useful as a business filter / candidate set, not as a product-ID source
    unless the sheet explicitly contains Mirakl IDs.

## What the April 15, 2026 call clarified

- Offers-only uploads are valid. The hard part is matching each offer to the
  correct Airbus product.
- Airbus advised using `MPN title` as the matching identifier for this flow.
- The four main offer error classes were discussed:
  - `The product does not exist`
  - `This product is not available for sale`
  - invalid `leadtime-to-ship`
  - `The product linked to the new offer is different from the product linked to the existing offer.`
- `This product is not available for sale` was explained as an Airbus-side
  restriction / HQPL issue, not a CRM export formatting problem.
- `leadtime-to-ship` was clarified as a shipping delay in days. Longer sourcing
  delays belong in procurement lead time, not in `leadtime-to-ship`.
- Airbus said they would investigate suspicious `The product does not exist`
  cases where the product appeared to exist in the master catalog.

## What was confirmed locally

- The product import problem was mostly identifier matching, not missing fields.
- Many aerospace hardware rows are ambiguous when matched directly by
  `mpnTitle`.
- Some offer upload failures happen because the CRM part number is an Airbus
  alternate reference rather than the canonical `mpnTitle`.
- Example:
  - `ASNA0045BC100L` appears in the Airbus hardware references workbook as an
    alternate reference for canonical `ASNA0045-100BCL`.

## Current code behavior

- `routes/marketplace.py`
  - Offer export prefers stored marketplace product IDs when they exist.
  - If no stored product ID exists, offer export falls back to canonicalized
    `mpnTitle`.
  - Product/detail imports store Airbus `id` into
    `part_numbers.mkp_offer_product_id` when the imported file contains it.
  - Offer imports submitted through the app also persist resolved product IDs
    back into `part_numbers` for future use.
  - Offer row generation now normalizes mistaken `product-id-type=SKU` back to
    `mpnTitle` when the product ID is really the part number / resolved MPN
    title.
  - `leadtime-to-ship` is now sanitized before export/import:
    - minimum `1`
    - default cap `30`
    - configurable via `MIRAKL_MAX_LEADTIME_TO_SHIP_DAYS` or portal setting
      `mirakl_max_leadtime_to_ship_days`
- `airbus_marketplace_export.py`
  - Full Airbus template exports apply the same lead-time sanitization.
- `templates/marketplace_export.html`
  - Preview/export uses the same canonicalized `mpnTitle` logic as the backend.
  - Baseline-mode offer export also normalizes mistaken `SKU` matching back to
    `mpnTitle` when appropriate.
  - Load status summarizes how many parts are exact matches, rewritten from alt
    refs, or unresolved.

## Reference handling

- `docs/Copy of All Hardware References sept 2025.xlsx`
  - Use this for alias normalization only.
  - Purpose:
    - exact `mpnTitle` lookup
    - alternate reference -> canonical `mpnTitle` rewrite
- `docs/[16.04.26] Masterlist Hardware.xlsx`
  - Use this as the approved Airbus hardware candidate set.
  - Purpose:
    - decide which CRM parts are worth exporting as Airbus hardware offers
    - flag CRM parts that are outside the current Airbus master list
    - reduce user error by keeping exports focused on known Airbus hardware

These two files solve different problems and should stay separate.

## Error status

- `leadtime-to-ship`
  - Repo-side mitigation is in place.
  - Large lead times should now be clamped before export so they do not hit the
    earlier operator validation error.
- Wrong identifier type / rows using SKU as the matching key
  - Repo-side mitigation is in place.
  - Offer exports should now prefer:
    - stored marketplace product ID when known
    - otherwise canonicalized `mpnTitle`
- `The product does not exist`
  - Airbus has reportedly fixed this on their side.
  - This is not yet verified from a new test upload in this repo.
- `This product is not available for sale`
  - Still expected for HQPL / sale-restriction cases.
  - This is not considered fixed in CRM.
- `The product linked to the new offer is different from the product linked to the existing offer`
  - Still an open case.
  - Likely related to re-linking an existing offer to a different product
    identity.

## Practical workflow for now

1. Keep the hardware reference workbook for alias normalization.
2. Treat the newest Airbus hardware master list as the approved candidate set
   for hardware offer work.
3. Load parts in the export page.
4. Check the load status summary for:
   - exact `mpnTitle`
   - rewritten from alternate refs
   - unresolved
5. For offer uploads:
   - if a stored marketplace product ID exists, use it
   - otherwise use canonicalized `mpnTitle`
6. For product uploads:
   - use them only for parts that appear genuinely new or unresolved in prod
7. After any manual prod product creation, pull a fresh Airbus export if
   possible and import it locally so learned IDs are stored in CRM.
8. Re-test the earlier `product does not exist` cases with a fresh offer export
   before assuming the operator-side fix is complete.

## Recommended next improvement

- Add a dedicated master-list upload flow in the marketplace export page.
- The uploaded master list should become the current Airbus hardware filter used
  by the UI.
- That flow should be separate from the existing Airbus export baseline import:
  - baseline import = preserve operator-side fields from an uploaded Airbus file
  - master-list import = define the approved hardware universe for export

## Known limitation

- Without prod API access or a prod export with populated product IDs, existing
  no-offer products still cannot be resolved perfectly.
- `mpnTitle` remains the main practical matching key for those rows, so alias
  normalization is the mitigation rather than a full solution.
