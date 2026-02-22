import csv
import io
from typing import Any, Dict, Iterable, List


REQUIRED_FIELDS = (
    'sku',
    'product-id',
    'product-id-type',
    'price',
    'quantity',
    'state',
)

DEFAULT_FIELDS = REQUIRED_FIELDS + (
    'leadtime-to-ship',
    'description',
)


def _format_decimal(value: Any) -> str:
    if value is None:
        return ''
    if isinstance(value, (int, float)):
        return f"{value:.2f}".rstrip('0').rstrip('.')
    text = str(value).strip()
    return text.replace(',', '.')


def _is_blank(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    return False


def build_offers_csv(
    offers: Iterable[Dict[str, Any]],
    *,
    fieldnames: Iterable[str] = DEFAULT_FIELDS,
) -> bytes:
    rows = list(offers)
    if not rows:
        raise ValueError("offers payload is empty.")

    missing_by_row = []
    for idx, row in enumerate(rows, start=1):
        row_missing = []
        for field in REQUIRED_FIELDS:
            if field not in row or _is_blank(row.get(field)):
                row_missing.append(field)
        if row_missing:
            sku = str(row.get('sku') or '').strip()
            missing_by_row.append(f"row {idx} (sku={sku or 'n/a'}): {', '.join(row_missing)}")

    if missing_by_row:
        preview = '; '.join(missing_by_row[:10])
        suffix = ' ...' if len(missing_by_row) > 10 else ''
        raise ValueError(f"Missing required offer fields: {preview}{suffix}")

    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=list(fieldnames), extrasaction='ignore')
    writer.writeheader()

    for row in rows:
        payload = dict(row)
        if 'price' in payload:
            payload['price'] = _format_decimal(payload.get('price'))
        if 'quantity' in payload:
            payload['quantity'] = _format_decimal(payload.get('quantity'))
        writer.writerow(payload)

    return output.getvalue().encode('utf-8')
