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


def build_offers_csv(
    offers: Iterable[Dict[str, Any]],
    *,
    fieldnames: Iterable[str] = DEFAULT_FIELDS,
) -> bytes:
    rows = list(offers)
    if not rows:
        raise ValueError("offers payload is empty.")

    missing = [field for field in REQUIRED_FIELDS if any(field not in row for row in rows)]
    if missing:
        raise ValueError(f"Missing required offer fields: {', '.join(missing)}")

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
