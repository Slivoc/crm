"""
Export parts to Airbus Marketplace format
"""
import logging
import os
from datetime import datetime
import csv
import re
from openpyxl import Workbook
from openpyxl.styles import Font, Alignment
import io

logger = logging.getLogger(__name__)


AIRBUS_MARKETPLACE_HEADERS = [
    "mkpCategory",
    "manufacturerBrand",
    "code",
    "description [en]",
    "ean",
    "name",
    "alternativePartRefList",
    "description [fr]",
    "description [de]",
    "description [es]",
    "description [pt]",
    "productSummary [fr]",
    "productSummary [en]",
    "productSummary [de]",
    "productSummary [es]",
    "productSummary [pt]",
    "productPresentation [fr]",
    "productPresentation [en]",
    "productPresentation [de]",
    "productPresentation [es]",
    "productPresentation [pt]",
    "natoCode",
    "productUnit",
    "packageContent",
    "packageContentUnit",
    "thirdLevel",
    "dangerous",
    "cm_code",
    "MSDS",
    "TDS",
    "OEM",
    "color",
    "size_usi",
    "weight_usi",
    "size_us",
    "weight_us",
    "image2",
    "image3",
    "image4",
    "eccn",
    "serialized",
    "logCard",
    "easaf1",
    "sku",
    "product-id",
    "product-id-type",
    "description",
    "internal-description",
    "price",
    "price-additional-info",
    "quantity",
    "min-quantity-alert",
    "state",
    "available-start-date",
    "available-end-date",
    "logistic-class",
    "favorite-rank",
    "discount-price",
    "discount-start-date",
    "discount-end-date",
    "allow-quote-requests",
    "leadtime-to-ship",
    "min-order-quantity",
    "package-quantity",
    "update-delete",
    "commercial-on-collection",
    "plt",
    "plt-unit",
    "shelflife",
    "shelflife-unit",
    "warranty",
    "warranty-unit",
    "up-sell",
    "cross-sell",
    "standards",
]


def _airbus_bool(value):
    return 'TRUE' if bool(value) else 'FALSE'


def _normalize_header(header):
    return re.sub(r'[^a-z0-9]+', '', str(header or '').strip().lower())


def _get_baseline_header_map(baseline_row):
    header_map = {}
    for raw_header, raw_value in (baseline_row or {}).items():
        header = str(raw_header or '').strip()
        if not header:
            continue
        header_map[_normalize_header(header)] = raw_value
    return header_map


def _build_generated_airbus_payload(part):
    part_number = part.get('part_number', '')
    mkp_category = part.get('mkp_category', '')
    description = part.get('description', '')
    manufacturer = part.get('manufacturer', '')
    quantity = part.get('quantity', '')
    price = part.get('price', '')
    condition = part.get('condition', 'New')
    lead_time_days = part.get('lead_time_days', '')

    # Marketplace-specific fields (use database values or defaults)
    mkp_description = part.get('mkp_description') or description or part_number
    mkp_name = part.get('mkp_name') or part_number
    # productSummary and productPresentation are required - use part_number as fallback
    mkp_product_summary = part.get('mkp_product_summary') or description or part_number
    mkp_product_presentation = part.get('mkp_product_presentation') or description or part_number
    mkp_product_unit = part.get('mkp_product_unit') or 'EA'
    mkp_package_content = part.get('mkp_package_content') if part.get('mkp_package_content') is not None else 1
    mkp_package_content_unit = part.get('mkp_package_content_unit') or 'EA'
    mkp_third_level = part.get('mkp_third_level') or 'PC'  # PC = piece, valid value
    mkp_dangerous = _airbus_bool(part.get('mkp_dangerous'))
    mkp_eccn = part.get('mkp_eccn') or 'EAR'  # EAR = Export Administration Regulations (default for commercial)
    mkp_serialized = _airbus_bool(part.get('mkp_serialized'))
    mkp_log_card = _airbus_bool(part.get('mkp_log_card'))
    # easaf1 is operator-specific in Airbus/Mirakl and may be a constrained value-list,
    # not a generic boolean. Use configured tokens when provided; otherwise leave blank.
    easaf1_true_value = (os.getenv('MIRAKL_EASAF1_TRUE_VALUE') or '').strip()
    easaf1_false_value = (os.getenv('MIRAKL_EASAF1_FALSE_VALUE') or '').strip()
    if easaf1_true_value or easaf1_false_value:
        mkp_easaf1 = easaf1_true_value if part.get('mkp_easaf1') else easaf1_false_value
    else:
        mkp_easaf1 = _airbus_bool(part.get('mkp_easaf1'))
    has_price = False
    if isinstance(price, (int, float)):
        has_price = float(price) > 0
    elif isinstance(price, str):
        try:
            has_price = float(price.strip().replace(',', '.')) > 0 if price.strip() else False
        except ValueError:
            has_price = False
    commercial_mode = part.get('commercial-on-collection') or ('ON_COLLECTION' if has_price else 'ON_DEMAND')

    return {
        "mkpCategory": mkp_category,
        "manufacturerBrand": manufacturer,
        "code": part_number,
        "description [en]": mkp_description,
        "ean": "",
        "name": mkp_name,
        "alternativePartRefList": "",
        "description [fr]": "",
        "description [de]": "",
        "description [es]": "",
        "description [pt]": "",
        "productSummary [fr]": "",
        "productSummary [en]": mkp_product_summary,
        "productSummary [de]": "",
        "productSummary [es]": "",
        "productSummary [pt]": "",
        "productPresentation [fr]": "",
        "productPresentation [en]": mkp_product_presentation,
        "productPresentation [de]": "",
        "productPresentation [es]": "",
        "productPresentation [pt]": "",
        "natoCode": "",
        "productUnit": mkp_product_unit,
        "packageContent": mkp_package_content,
        "packageContentUnit": mkp_package_content_unit,
        "thirdLevel": mkp_third_level,
        "dangerous": mkp_dangerous,
        "cm_code": "",
        "MSDS": "",
        "TDS": "",
        "OEM": "",
        "color": "",
        "size_usi": "",
        "weight_usi": "",
        "size_us": "",
        "weight_us": "",
        "image2": "",
        "image3": "",
        "image4": "",
        "eccn": mkp_eccn,
        "serialized": mkp_serialized,
        "logCard": mkp_log_card,
        "easaf1": mkp_easaf1,
        "sku": part_number,
        "product-id": part_number,
        "product-id-type": "MPN",
        "description": mkp_description,
        "internal-description": mkp_description,
        "price": price if price else "",
        "price-additional-info": "",
        "quantity": quantity if quantity else "",
        "min-quantity-alert": "",
        "state": condition,
        "available-start-date": "",
        "available-end-date": "",
        "logistic-class": "",
        "favorite-rank": "",
        "discount-price": "",
        "discount-start-date": "",
        "discount-end-date": "",
        "allow-quote-requests": "true",
        "leadtime-to-ship": lead_time_days if lead_time_days else "",
        "min-order-quantity": "",
        "package-quantity": "",
        "update-delete": "",
        "commercial-on-collection": commercial_mode,
        "plt": 14,
        "plt-unit": "DAY",
        "shelflife": "",
        "shelflife-unit": "",
        "warranty": "",
        "warranty-unit": "",
        "up-sell": "",
        "cross-sell": "",
        "standards": "",
    }


def _build_airbus_row(part):
    generated_payload = _build_generated_airbus_payload(part)
    payload = dict(generated_payload)
    baseline_headers = _get_baseline_header_map(part.get('baseline_row'))

    for header in AIRBUS_MARKETPLACE_HEADERS:
        normalized = _normalize_header(header)
        if normalized in baseline_headers:
            payload[header] = baseline_headers[normalized]

    payload["price"] = generated_payload["price"]
    payload["quantity"] = generated_payload["quantity"]
    payload["commercial-on-collection"] = generated_payload["commercial-on-collection"]

    if not payload["price"]:
        payload["price-additional-info"] = ""
        payload["discount-price"] = ""

    return [payload.get(header, '') for header in AIRBUS_MARKETPLACE_HEADERS]


def export_parts_to_airbus_marketplace(parts_data):
    """
    Export parts to Airbus Marketplace Excel format

    Args:
        parts_data: List of dicts with part information including:
            - part_number (required)
            - mkp_category (required)
            - description (optional)
            - manufacturer (optional)
            - quantity (optional)
            - price (optional)
            - condition (optional)
            - lead_time_days (optional)
            etc.

    Returns:
        BytesIO object containing the Excel file
    """
    try:
        logger.info(f"Exporting {len(parts_data)} parts to Airbus Marketplace format")

        # Create workbook
        wb = Workbook()
        ws = wb.active
        ws.title = "Data"

        # Define headers matching Airbus format
        headers = AIRBUS_MARKETPLACE_HEADERS

        # Write headers
        ws.append(headers)

        # Style headers
        for cell in ws[1]:
            cell.font = Font(bold=True)
            cell.alignment = Alignment(horizontal='left')

        # Process each part
        for part in parts_data:
            ws.append(_build_airbus_row(part))

        # Save to BytesIO
        output = io.BytesIO()
        wb.save(output)
        output.seek(0)

        logger.info("Airbus Marketplace export completed successfully")
        return output

    except Exception as e:
        logger.exception(f"Error exporting to Airbus Marketplace format: {e}")
        raise


def export_parts_to_airbus_marketplace_csv(parts_data):
    """
    Export parts to Airbus Marketplace CSV format.
    """
    try:
        logger.info(f"Exporting {len(parts_data)} parts to Airbus Marketplace CSV format")

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(AIRBUS_MARKETPLACE_HEADERS)
        for part in parts_data:
            writer.writerow(_build_airbus_row(part))

        payload = io.BytesIO(output.getvalue().encode('utf-8'))
        payload.seek(0)
        logger.info("Airbus Marketplace CSV export completed successfully")
        return payload
    except Exception as e:
        logger.exception(f"Error exporting to Airbus Marketplace CSV format: {e}")
        raise
