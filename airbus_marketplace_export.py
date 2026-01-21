"""
Export parts to Airbus Marketplace format
"""
import logging
from datetime import datetime
import csv
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


def _build_airbus_row(part):
    part_number = part.get('part_number', '')
    mkp_category = part.get('mkp_category', '')
    description = part.get('description', '')
    manufacturer = part.get('manufacturer', '')
    quantity = part.get('quantity', '')
    price = part.get('price', '')
    condition = part.get('condition', 'New')
    lead_time_days = part.get('lead_time_days', '')

    return [
        mkp_category,  # mkpCategory
        manufacturer,  # manufacturerBrand
        part_number,  # code
        description,  # description [en]
        "",  # ean
        part_number,  # name
        "",  # alternativePartRefList
        "",  # description [fr]
        "",  # description [de]
        "",  # description [es]
        "",  # description [pt]
        "",  # productSummary [fr]
        description,  # productSummary [en]
        "",  # productSummary [de]
        "",  # productSummary [es]
        "",  # productSummary [pt]
        "",  # productPresentation [fr]
        description,  # productPresentation [en]
        "",  # productPresentation [de]
        "",  # productPresentation [es]
        "",  # productPresentation [pt]
        "",  # natoCode
        "EA",  # productUnit
        1,  # packageContent
        "EA",  # packageContentUnit
        "EA",  # thirdLevel
        "false",  # dangerous
        "",  # cm_code
        "",  # MSDS
        "",  # TDS
        "",  # OEM
        "",  # color
        "",  # size_usi
        "",  # weight_usi
        "",  # size_us
        "",  # weight_us
        "",  # image2
        "",  # image3
        "",  # image4
        "",  # eccn
        "false",  # serialized
        "false",  # logCard
        "false",  # easaf1
        part_number,  # sku
        part_number,  # product-id
        "MPN",  # product-id-type
        description,  # description
        description,  # internal-description
        price if price else "",  # price
        "",  # price-additional-info
        quantity if quantity else "",  # quantity
        "",  # min-quantity-alert
        condition,  # state
        "",  # available-start-date
        "",  # available-end-date
        "",  # logistic-class
        "",  # favorite-rank
        "",  # discount-price
        "",  # discount-start-date
        "",  # discount-end-date
        "true",  # allow-quote-requests
        lead_time_days if lead_time_days else "",  # leadtime-to-ship
        "",  # min-order-quantity
        "",  # package-quantity
        "",  # update-delete
        "ON_COLLECTION",  # commercial-on-collection
        14,  # plt
        "DAY",  # plt-unit
        "",  # shelflife
        "",  # shelflife-unit
        "",  # warranty
        "",  # warranty-unit
        "",  # up-sell
        "",  # cross-sell
        "",  # standards
    ]


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
