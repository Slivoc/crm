import csv
import io
from typing import Any, Dict, Iterable, List


OFFER_IMPORT_FIELDS = (
    'sku',
    'product-id',
    'product-id-type',
    'description',
    'internal-description',
    'price',
    'price-additional-info',
    'quantity',
    'min-quantity-alert',
    'state',
    'available-start-date',
    'available-end-date',
    'logistic-class',
    'favorite-rank',
    'discount-start-date',
    'discount-end-date',
    'discount-price',
    'update-delete',
    'allow-quote-requests',
    'leadtime-to-ship',
    'min-order-quantity',
    'max-order-quantity',
    'package-quantity',
    'price[channel=0100]',
    'discount-start-date[channel=0100]',
    'discount-end-date[channel=0100]',
    'discount-price[channel=0100]',
    'price[channel=1000]',
    'discount-start-date[channel=1000]',
    'discount-end-date[channel=1000]',
    'discount-price[channel=1000]',
    'price[channel=2000]',
    'discount-start-date[channel=2000]',
    'discount-end-date[channel=2000]',
    'discount-price[channel=2000]',
    'price[channel=5000]',
    'discount-start-date[channel=5000]',
    'discount-end-date[channel=5000]',
    'discount-price[channel=5000]',
    'price[channel=7000]',
    'discount-start-date[channel=7000]',
    'discount-end-date[channel=7000]',
    'discount-price[channel=7000]',
    'commercial-on-collection',
    'plt',
    'plt-unit',
    'shelflife',
    'shelflife-unit',
    'warranty',
    'warranty-unit',
    'up-sell',
    'cross-sell',
    'vendor-reference',
)

REQUIRED_FIELDS = (
    'sku',
    'product-id',
    'product-id-type',
    'price',
    'quantity',
    'state',
)

DEFAULT_FIELDS = OFFER_IMPORT_FIELDS


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


def _coerce_positive_number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    else:
        text = str(value).strip()
        if not text:
            return None
        try:
            number = float(text.replace(',', '.'))
        except ValueError:
            return None
    return number if number > 0 else None


def _coerce_number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text.replace(',', '.'))
    except ValueError:
        return None


def _is_on_demand_without_price(row: Dict[str, Any]) -> bool:
    commercial_mode = str(row.get('commercial-on-collection') or '').strip().upper()
    return commercial_mode == 'ON_DEMAND' and _coerce_positive_number(row.get('price')) is None


def build_offers_csv(
    offers: Iterable[Dict[str, Any]],
    *,
    fieldnames: Iterable[str] = DEFAULT_FIELDS,
    delimiter: str = ';',
) -> bytes:
    rows = list(offers)
    if not rows:
        raise ValueError("offers payload is empty.")

    missing_by_row = []
    for idx, row in enumerate(rows, start=1):
        row_missing = []
        for field in REQUIRED_FIELDS:
            if field == 'price':
                if _coerce_number(row.get(field)) is None:
                    row_missing.append(field)
                continue
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
    resolved_fieldnames = list(fieldnames)
    writer = csv.DictWriter(
        output,
        fieldnames=resolved_fieldnames,
        extrasaction='ignore',
        delimiter=delimiter,
        lineterminator='\n',
    )
    writer.writeheader()

    for row in rows:
        payload = {field: '' for field in resolved_fieldnames}
        payload.update(dict(row))
        if 'price' in payload:
            payload['price'] = _format_decimal(payload.get('price'))
        if 'quantity' in payload:
            payload['quantity'] = _format_decimal(payload.get('quantity'))
        writer.writerow(payload)

    return output.getvalue().encode('utf-8')
