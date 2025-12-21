"""
Export parts to Airbus Marketplace format
"""
import logging
from datetime import datetime
from openpyxl import Workbook
from openpyxl.styles import Font, Alignment
import io

logger = logging.getLogger(__name__)


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
        headers = [
            "Category",  # Column A
            "Internal part reference",  # Column B
            "Description [en]",  # Column C
            "Manufacturer",  # Column D
            "EAN or GTIN code",  # Column E
            "Manufacturer Part Number",  # Column F
            "Alternative Part References",  # Column G
            "Description [fr]",  # Column H
            "Description [de]",  # Column I
            "Description [es]",  # Column J
            "Description [pt]",  # Column K
            "Product Summary [fr]",  # Column L
            "Product Summary [en]",  # Column M
            "Product Summary [de]",  # Column N
            "Product Summary [es]",  # Column O
            "Product Summary [pt]",  # Column P
            "Product Presentation [fr]",  # Column Q
            "Product Presentation [en]",  # Column R
            "Product Presentation [de]",  # Column S
            "Product Presentation [es]",  # Column T
            "Product Presentation [pt]",  # Column U
            "NATO Code",  # Column V
            "Product Unit",  # Column W
            "Package Content Quantity",  # Column X
            "Package Content Unit",  # Column Y
            "Third Level",  # Column Z
            "Hazardous",  # Column AA
            "Consumable Material Code (CMXXXX)",  # Column AB
            "MSDS",  # Column AC
            "TDS",  # Column AD
            "OEM code",  # Column AE
            "Color",  # Column AF
            "Size USI (cm)",  # Column AG
            "Weight USI (kg)",  # Column AH
            "Size US (ft)",  # Column AI
            "Weight US (lbs)",  # Column AJ
            "Main media",  # Column AK
            "Additional media",  # Column AL
            "Additional media",  # Column AM
            "Export Control Classification Number (ECCN)",  # Column AN
            "Serialized",  # Column AO
            "Log Card",  # Column AP
            "EASA Form 1",  # Column AQ
            "Internal Unit",  # Column AR
            "Offer SKU",  # Column AS
            "Product ID",  # Column AT
            "Product ID Type",  # Column AU
            "Offer Description",  # Column AV
            "Offer Internal Description",  # Column AW
            "Offer Price",  # Column AX
            "Offer Price Additional Info",  # Column AY
            "Offer Quantity",  # Column AZ
            "Minimum Quantity Alert",  # Column BA
            "Offer State",  # Column BB
            "Availability Start Date",  # Column BC
            "Availability End Date",  # Column BD
            "Logistic Class",  # Column BE
            "Favorite Rank",  # Column BF
            "Discount Price",  # Column BG
            "Discount Start Date",  # Column BH
            "Discount End Date",  # Column BI
            "Quote Enabled",  # Column BJ
            "Lead Time to Ship (in days)",  # Column BK
            "Min Order Quantity",  # Column BL
            "Order quantity increment",  # Column BM
            "Update/Delete",  # Column BN
            "On collection or On demand ",  # Column BO
            "Procurement Lead Time",  # Column BP
            "Procurement Lead Time Unit",  # Column BQ
            "Shelf Life",  # Column BR
            "Shelf Life Unit",  # Column BS
            "Warranty",  # Column BT
            "Warranty Unit",  # Column BU
            "Up Selling",  # Column BV
            "Cross Selling",  # Column BW
            "Standards",  # Column BX
        ]

        # Write headers
        ws.append(headers)

        # Style headers
        for cell in ws[1]:
            cell.font = Font(bold=True)
            cell.alignment = Alignment(horizontal='left')

        # Process each part
        for part in parts_data:
            part_number = part.get('part_number', '')
            mkp_category = part.get('mkp_category', '')
            description = part.get('description', '')
            manufacturer = part.get('manufacturer', '')
            quantity = part.get('quantity', '')
            price = part.get('price', '')
            condition = part.get('condition', 'New')
            lead_time_days = part.get('lead_time_days', '')

            # Build row matching the header structure
            row = [
                mkp_category,  # Category
                part_number,  # Internal part reference
                description,  # Description [en]
                manufacturer,  # Manufacturer
                "",  # EAN or GTIN code
                part_number,  # Manufacturer Part Number
                "",  # Alternative Part References
                "",  # Description [fr]
                "",  # Description [de]
                "",  # Description [es]
                "",  # Description [pt]
                "",  # Product Summary [fr]
                description,  # Product Summary [en]
                "",  # Product Summary [de]
                "",  # Product Summary [es]
                "",  # Product Summary [pt]
                "",  # Product Presentation [fr]
                description,  # Product Presentation [en]
                "",  # Product Presentation [de]
                "",  # Product Presentation [es]
                "",  # Product Presentation [pt]
                "",  # NATO Code
                "EA",  # Product Unit
                1,  # Package Content Quantity
                "EA",  # Package Content Unit
                "EA",  # Third Level
                "false",  # Hazardous
                "",  # Consumable Material Code
                "",  # MSDS
                "",  # TDS
                "",  # OEM code
                "",  # Color
                "",  # Size USI (cm)
                "",  # Weight USI (kg)
                "",  # Size US (ft)
                "",  # Weight US (lbs)
                "",  # Main media
                "",  # Additional media
                "",  # Additional media
                "",  # ECCN
                "false",  # Serialized
                "false",  # Log Card
                "false",  # EASA Form 1
                "",  # Internal Unit
                part_number,  # Offer SKU
                part_number,  # Product ID
                "MPN",  # Product ID Type
                description,  # Offer Description
                description,  # Offer Internal Description
                price if price else "",  # Offer Price
                "",  # Offer Price Additional Info
                quantity if quantity else "",  # Offer Quantity
                "",  # Minimum Quantity Alert
                condition,  # Offer State
                "",  # Availability Start Date
                "",  # Availability End Date
                "",  # Logistic Class
                "",  # Favorite Rank
                "",  # Discount Price
                "",  # Discount Start Date
                "",  # Discount End Date
                "true",  # Quote Enabled
                lead_time_days if lead_time_days else "",  # Lead Time to Ship
                "",  # Min Order Quantity
                "",  # Order quantity increment
                "",  # Update/Delete
                "ON_COLLECTION",  # On collection or On demand
                14,  # Procurement Lead Time
                "DAY",  # Procurement Lead Time Unit
                "",  # Shelf Life
                "",  # Shelf Life Unit
                "",  # Warranty
                "",  # Warranty Unit
                "",  # Up Selling
                "",  # Cross Selling
                "",  # Standards
            ]

            ws.append(row)

        # Save to BytesIO
        output = io.BytesIO()
        wb.save(output)
        output.seek(0)

        logger.info("Airbus Marketplace export completed successfully")
        return output

    except Exception as e:
        logger.exception(f"Error exporting to Airbus Marketplace format: {e}")
        raise