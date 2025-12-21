"""
Airbus Marketplace routes
"""

from flask import Blueprint, jsonify, request, send_file, render_template
from datetime import datetime
import logging

from airbus_marketplace_helper import (
    suggest_marketplace_category,
    suggest_categories_batch,
    get_available_categories
)
from airbus_marketplace_export import export_parts_to_airbus_marketplace
from models import get_db

logger = logging.getLogger(__name__)

marketplace_bp = Blueprint('marketplace', __name__)


@marketplace_bp.route('/export-page', methods=['GET'])
def export_page():
    """Render the marketplace export page"""
    from flask import render_template
    return render_template('marketplace_export.html')


@marketplace_bp.route('/categories', methods=['GET'])
def get_marketplace_categories():
    """Get all available Airbus Marketplace categories"""
    try:
        categories = get_available_categories()
        return jsonify({'categories': categories}), 200
    except Exception as e:
        logger.exception("Error getting marketplace categories")
        return jsonify({'error': str(e)}), 500


@marketplace_bp.route('/suggest-category', methods=['POST'])
def suggest_category():
    """
    Suggest category for a single part

    POST body:
    {
        "part_number": "ABC123",
        "description": "Optional description",
        "additional_info": "Optional additional context"
    }
    """
    try:
        data = request.get_json()
        part_number = data.get('part_number')
        description = data.get('description', '')
        additional_info = data.get('additional_info', '')

        if not part_number:
            return jsonify({'error': 'part_number is required'}), 400

        suggestion = suggest_marketplace_category(
            part_number,
            description,
            additional_info
        )

        if not suggestion:
            return jsonify({'error': 'Unable to suggest category'}), 500

        return jsonify(suggestion), 200

    except Exception as e:
        logger.exception("Error suggesting category")
        return jsonify({'error': str(e)}), 500


@marketplace_bp.route('/suggest-categories-batch', methods=['POST'])
def suggest_categories_batch_route():
    """
    Suggest categories for multiple parts

    POST body:
    {
        "parts": [
            {"part_number": "ABC123", "description": "..."},
            {"part_number": "XYZ789", "description": "..."}
        ]
    }
    """
    try:
        data = request.get_json()
        parts = data.get('parts', [])

        if not parts:
            return jsonify({'error': 'parts array is required'}), 400

        results = suggest_categories_batch(parts)

        return jsonify({'results': results}), 200

    except Exception as e:
        logger.exception("Error suggesting categories in batch")
        return jsonify({'error': str(e)}), 500


@marketplace_bp.route('/update-category/<base_part_number>', methods=['POST'])
def update_part_category(base_part_number):
    """
    Update marketplace category for a part

    POST body:
    {
        "mkp_category": "Marketplace Categories/Hardware and Electrical/Bolts"
    }
    """
    try:
        data = request.get_json()
        mkp_category = data.get('mkp_category')

        if not mkp_category:
            return jsonify({'error': 'mkp_category is required'}), 400

        # Validate category is in allowed list
        valid_categories = get_available_categories()
        if mkp_category not in valid_categories:
            return jsonify({'error': 'Invalid category'}), 400

        # Update part in database
        db = get_db()
        cursor = db.cursor()
        cursor.execute(
            "UPDATE part_numbers SET mkp_category = ? WHERE base_part_number = ?",
            (mkp_category, base_part_number)
        )
        db.commit()

        if cursor.rowcount == 0:
            return jsonify({'error': 'Part not found'}), 404

        return jsonify({'success': True, 'message': 'Category updated'}), 200

    except Exception as e:
        logger.exception("Error updating part category")
        if db:
            db.rollback()
        return jsonify({'error': str(e)}), 500


@marketplace_bp.route('/get-parts-for-export', methods=['POST'])
def get_parts_for_export():
    """
    Get filtered parts with stock and pricing data for export preview

    POST body:
    {
        "stock_filter": "all|stock_only|no_stock",
        "category_filter": "all|categorized_only|missing_only",
        "include_sales_activity": true/false,
        "activity_period_days": "30|90|180|365|730|all",
        "pricing_period_days": "90|180|365|730|all",
        "part_category_id": optional category ID,
        "manufacturer": optional manufacturer filter,
        "part_number_search": optional part number search
    }
    """
    try:
        data = request.get_json() or {}

        stock_filter = data.get('stock_filter', 'all')
        category_filter = data.get('category_filter', 'all')
        include_sales_activity = data.get('include_sales_activity', False)
        activity_period_days = data.get('activity_period_days', '365')
        pricing_period_days = data.get('pricing_period_days', '365')  # NEW
        part_category_id = data.get('part_category_id', '')
        manufacturer = data.get('manufacturer', '').strip()
        part_number_search = data.get('part_number_search', '').strip()

        db = get_db()
        cursor = db.cursor()

        # Build WHERE clause for pricing period
        pricing_where = ""
        if pricing_period_days != 'all':
            pricing_where = f"date('now', '-{pricing_period_days} days')"
        else:
            pricing_where = "date('1900-01-01')"  # Effectively all time

        # Build base query with stock info and pricing data
        query = f"""
            SELECT 
                pn.base_part_number,
                pn.part_number,
                pn.mkp_category,
                COALESCE(stock.total_stock, 0) as stock_qty,
                stock.avg_cost as stock_cost,
                sales.avg_price as avg_sale_price,
                sales.last_price as last_sale_price,
                sales.last_sale_date,
                cq.avg_price as avg_cq_price,
                po.avg_price as avg_po_price,
                vq.avg_price as avg_vq_price,
                vq.lowest_price as lowest_vq_price
            FROM part_numbers pn
            LEFT JOIN (
                SELECT 
                    base_part_number,
                    SUM(available_quantity) as total_stock,
                    AVG(cost_per_unit) as avg_cost
                FROM stock_movements
                WHERE movement_type = 'IN' AND available_quantity > 0
                GROUP BY base_part_number
            ) stock ON pn.base_part_number = stock.base_part_number
            LEFT JOIN (
                SELECT 
                    sol.base_part_number,
                    AVG(sol.price) as avg_price,
                    MAX(sol.price) as last_price,
                    MAX(so.date_entered) as last_sale_date
                FROM sales_order_lines sol
                JOIN sales_orders so ON sol.sales_order_id = so.id
        """

        params = []

        # Add date filter for sales if activity period specified
        if include_sales_activity and activity_period_days != 'all':
            query += " WHERE so.date_entered >= date('now', '-' || ? || ' days')"
            params.append(activity_period_days)

        query += f"""
                GROUP BY sol.base_part_number
            ) sales ON pn.base_part_number = sales.base_part_number
            LEFT JOIN (
                SELECT 
                    cl.base_part_number,
                    AVG(cl.unit_price) as avg_price
                FROM cq_lines cl
                JOIN cqs c ON cl.cq_id = c.id
                WHERE c.entry_date >= {pricing_where}
                  AND cl.unit_price IS NOT NULL
                  AND cl.is_no_quote = 0
                GROUP BY cl.base_part_number
            ) cq ON pn.base_part_number = cq.base_part_number
            LEFT JOIN (
                SELECT 
                    pol.base_part_number,
                    AVG(pol.price) as avg_price
                FROM purchase_order_lines pol
                JOIN purchase_orders po ON pol.purchase_order_id = po.id
                WHERE po.date_issued >= {pricing_where}
                  AND pol.price IS NOT NULL
                GROUP BY pol.base_part_number
            ) po ON pn.base_part_number = po.base_part_number
            LEFT JOIN (
                SELECT 
                    vl.base_part_number,
                    AVG(vl.vendor_price) as avg_price,
                    MIN(vl.vendor_price) as lowest_price
                FROM vq_lines vl
                JOIN vqs v ON vl.vq_id = v.id
                WHERE v.entry_date >= {pricing_where}
                GROUP BY vl.base_part_number
            ) vq ON pn.base_part_number = vq.base_part_number
            WHERE 1=1
        """

        # Apply stock filter
        if stock_filter == 'stock_only':
            query += " AND stock.total_stock > 0"
        elif stock_filter == 'no_stock':
            query += " AND (stock.total_stock IS NULL OR stock.total_stock = 0)"

        # Apply category filter
        if category_filter == 'categorized_only':
            query += " AND pn.mkp_category IS NOT NULL AND pn.mkp_category != ''"
        elif category_filter == 'missing_only':
            query += " AND (pn.mkp_category IS NULL OR pn.mkp_category = '')"

        # Apply sales activity filter
        if include_sales_activity:
            query += " AND sales.last_sale_date IS NOT NULL"

        # CRITICAL: Only include parts with stock OR pricing data
        query += """
            AND (
                stock.total_stock > 0 
                OR sales.avg_price IS NOT NULL 
                OR cq.avg_price IS NOT NULL 
                OR po.avg_price IS NOT NULL 
                OR vq.avg_price IS NOT NULL
            )
        """

        # Apply part category filter
        if part_category_id:
            query += " AND pn.category_id = ?"
            params.append(part_category_id)

        # Apply part number search
        if part_number_search:
            query += " AND (pn.part_number LIKE ? OR pn.base_part_number LIKE ?)"
            params.append(f"%{part_number_search}%")
            params.append(f"%{part_number_search}%")

        query += " ORDER BY pn.part_number"

        cursor.execute(query, params)
        rows = cursor.fetchall()

        parts = []
        for row in rows:
            parts.append({
                'base_part_number': row['base_part_number'],
                'part_number': row['part_number'],
                'mkp_category': row['mkp_category'],
                'description': '',  # Not in part_numbers table
                'manufacturer': '',  # Not in part_numbers table
                'stock_qty': row['stock_qty'],
                'stock_cost': row['stock_cost'],
                'avg_sale_price': row['avg_sale_price'],
                'last_sale_price': row['last_sale_price'],
                'last_sale_date': row['last_sale_date'],
                'avg_cq_price': row['avg_cq_price'],
                'avg_po_price': row['avg_po_price'],
                'avg_vq_price': row['avg_vq_price'],
                'lowest_vq_price': row['lowest_vq_price']
            })

        return jsonify({'success': True, 'parts': parts}), 200

    except Exception as e:
        logger.exception("Error getting parts for export")
        return jsonify({'success': False, 'error': str(e)}), 500

@marketplace_bp.route('/export', methods=['POST'])
def export_to_marketplace():
    """
    Export selected parts to Airbus Marketplace format with pricing

    POST body (as form data from JS):
    {
        "export_data": {
            "part_ids": [1, 2, 3],
            "pricing_config": {
                "stock_price_source": "cost|avg_sale|last_sale|manual",
                "stock_margin": 35,
                "manual_stock_price": null,
                "non_stock_price_source": "avg_sale|last_sale|avg_vq|lowest_vq|none",
                "default_lead_time": 14,
                "default_quantity": 1
            }
        }
    }
    """
    try:
        # Get export_data from form (sent by JS)
        import json
        export_data_str = request.form.get('export_data')
        if not export_data_str:
            return jsonify({'error': 'No export data provided'}), 400

        export_data = json.loads(export_data_str)
        base_part_numbers = export_data.get('base_part_numbers', [])
        pricing_config = export_data.get('pricing_config', {})

        if not base_part_numbers:
            return jsonify({'error': 'No parts selected for export'}), 400

        db = get_db()
        cursor = db.cursor()

        # Get parts with all pricing data
        placeholders = ','.join('?' * len(base_part_numbers))
        query = f"""
            SELECT 
                pn.base_part_number,
                pn.part_number,
                pn.mkp_category,
                COALESCE(stock.total_stock, 0) as stock_qty,
                stock.avg_cost as stock_cost,
                sales.avg_price as avg_sale_price,
                sales.last_price as last_sale_price,
                vq.avg_price as avg_vq_price,
                vq.lowest_price as lowest_vq_price
            FROM part_numbers pn
            LEFT JOIN (
                SELECT 
                    base_part_number,
                    SUM(available_quantity) as total_stock,
                    AVG(cost_per_unit) as avg_cost
                FROM stock_movements
                WHERE movement_type = 'IN' AND available_quantity > 0
                GROUP BY base_part_number
            ) stock ON pn.base_part_number = stock.base_part_number
            LEFT JOIN (
                SELECT 
                    sol.base_part_number,
                    AVG(sol.price) as avg_price,
                    MAX(sol.price) as last_price
                FROM sales_order_lines sol
                JOIN sales_orders so ON sol.sales_order_id = so.id
                WHERE so.date_entered >= date('now', '-365 days')
                GROUP BY sol.base_part_number
            ) sales ON pn.base_part_number = sales.base_part_number
            LEFT JOIN (
                SELECT 
                    vl.base_part_number,
                    AVG(vl.vendor_price) as avg_price,
                    MIN(vl.vendor_price) as lowest_price
                FROM vq_lines vl
                JOIN vqs v ON vl.vq_id = v.id
                WHERE v.entry_date >= date('now', '-365 days')
                GROUP BY vl.base_part_number
            ) vq ON pn.base_part_number = vq.base_part_number
            WHERE pn.base_part_number IN ({placeholders})
        """

        cursor.execute(query, base_part_numbers)
        rows = cursor.fetchall()

        if not rows:
            return jsonify({'error': 'No parts found to export'}), 404

        # Calculate prices based on configuration
        parts_data = []
        for row in rows:
            stock_qty = row['stock_qty']

            # Calculate price
            price = None
            if stock_qty > 0:
                # Stock item pricing
                source = pricing_config.get('stock_price_source', 'cost')
                margin = pricing_config.get('stock_margin', 35) / 100

                base_price = 0
                if source == 'cost' and row['stock_cost']:
                    base_price = row['stock_cost']
                elif source == 'avg_sale' and row['avg_sale_price']:
                    base_price = row['avg_sale_price']
                elif source == 'last_sale' and row['last_sale_price']:
                    base_price = row['last_sale_price']
                elif source == 'manual':
                    base_price = pricing_config.get('manual_stock_price') or 0

                if base_price > 0:
                    price = base_price * (1 + margin)
            else:
                # Non-stock item pricing
                source = pricing_config.get('non_stock_price_source', 'avg_sale')

                if source == 'avg_sale' and row['avg_sale_price']:
                    price = row['avg_sale_price']
                elif source == 'last_sale' and row['last_sale_price']:
                    price = row['last_sale_price']
                elif source == 'avg_vq' and row['avg_vq_price']:
                    price = row['avg_vq_price']
                elif source == 'lowest_vq' and row['lowest_vq_price']:
                    price = row['lowest_vq_price']

            # Determine lead time
            lead_time = 7 if stock_qty > 0 else pricing_config.get('default_lead_time', 14)

            # Get quantity
            quantity = pricing_config.get('default_quantity', 1)
            if stock_qty > 0:
                quantity = stock_qty

            parts_data.append({
                'base_part_number': row['base_part_number'],
                'part_number': row['part_number'],
                'mkp_category': row['mkp_category'],
                'description': '',
                'manufacturer': '',
                'quantity': quantity,
                'price': round(price, 2) if price else '',
                'condition': 'New',
                'lead_time_days': lead_time,
            })

        # Generate Excel file
        excel_file = export_parts_to_airbus_marketplace(parts_data)

        # Generate filename with timestamp
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f"AH_Marketplace_Upload_{timestamp}.xlsx"

        return send_file(
            excel_file,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            as_attachment=True,
            download_name=filename
        )

    except Exception as e:
        logger.exception("Error exporting to marketplace")
        return jsonify({'error': str(e)}), 500


@marketplace_bp.route('/auto-categorize', methods=['POST'])
def auto_categorize_parts():
    """
    Auto-categorize parts in batches with progress tracking

    POST body:
    {
        "overwrite": false,
        "base_part_numbers": [],
        "batch_size": 10,  // Process this many at a time
        "offset": 0  // Start from this position
    }
    """
    try:
        data = request.get_json() or {}
        overwrite = data.get('overwrite', False)
        base_part_numbers = data.get('base_part_numbers', [])
        batch_size = data.get('batch_size', 10)
        offset = data.get('offset', 0)

        db = get_db()
        cursor = db.cursor()

        # Build query
        if base_part_numbers:
            placeholders = ','.join('?' * len(base_part_numbers))
            query = f"""
                SELECT base_part_number, part_number
                FROM part_numbers 
                WHERE base_part_number IN ({placeholders})
            """
            params = base_part_numbers
        else:
            query = """
                SELECT base_part_number, part_number
                FROM part_numbers
            """
            if not overwrite:
                query += " WHERE mkp_category IS NULL OR mkp_category = ''"
            params = []

        query += f" LIMIT {batch_size} OFFSET {offset}"

        cursor.execute(query, params)
        rows = cursor.fetchall()

        if not rows:
            return jsonify({
                'success': True,
                'message': 'All parts processed',
                'updated_count': 0,
                'total_in_batch': 0,
                'has_more': False
            }), 200

        # Process this batch
        parts_list = [
            {
                'part_number': row[1],
                'description': '',
                'additional_info': ''
            }
            for row in rows
        ]

        suggestions = suggest_categories_batch(parts_list)

        # Update database
        updated_count = 0
        for row, suggestion in zip(rows, suggestions):
            base_part_number = row[0]
            suggested_category = suggestion.get('suggested_category')

            if suggested_category:
                cursor.execute(
                    "UPDATE part_numbers SET mkp_category = ? WHERE base_part_number = ?",
                    (suggested_category, base_part_number)
                )
                updated_count += 1

        db.commit()

        # Check if there are more parts to process
        cursor.execute(query.replace(f"LIMIT {batch_size} OFFSET {offset}", f"LIMIT 1 OFFSET {offset + batch_size}"), params)
        has_more = cursor.fetchone() is not None

        return jsonify({
            'success': True,
            'message': f'Processed batch of {len(rows)} parts',
            'updated_count': updated_count,
            'total_in_batch': len(rows),
            'has_more': has_more,
            'next_offset': offset + batch_size
        }), 200

    except Exception as e:
        logger.exception("Error auto-categorizing parts")
        if db:
            db.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500