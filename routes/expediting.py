from flask import Blueprint, render_template, request, url_for
from datetime import datetime, timedelta
from db import execute as db_execute

expediting_bp = Blueprint('expediting', __name__)


def _parse_date(value):
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
                try:
                    return datetime.strptime(value, fmt)
                except ValueError:
                    continue
    return None


def _determine_expedite_reason(row, threshold, cutoff_dt, ship_dt=None):
    current_stock = row.get('current_stock')
    quantity = row.get('quantity')
    po_date = _parse_date(row.get('po_delivery_date'))
    ship_dt = ship_dt or _parse_date(row.get('ship_date'))

    try:
        if current_stock is not None and quantity is not None and float(quantity) > 0:
            if float(current_stock) < float(quantity) and (po_date is None or (ship_dt and po_date > ship_dt)):
                return 'Stock Shortage'
    except (TypeError, ValueError):
        pass

    if ship_dt and po_date:
        delta_days = (po_date - ship_dt).days
        if delta_days > 0:
            return 'PO After Ship Date'
        if 0 <= delta_days <= threshold:
            return 'Tight Timeline'

    if ship_dt and cutoff_dt and ship_dt.date() <= cutoff_dt.date():
        return 'Due Soon'

    return None


_EXPEDITE_PRIORITY = {
    'Stock Shortage': 1,
    'PO After Ship Date': 2,
    'Tight Timeline': 3,
    'Due Soon': 4
}


def generate_breadcrumbs(*crumbs):
    breadcrumbs = []
    for crumb, path in crumbs:
        breadcrumbs.append((crumb, path))
    return breadcrumbs


@expediting_bp.route('/', methods=['GET'])
def expedite_dashboard():
    """
    Main expediting dashboard showing overview of orders that need attention
    """
    # Get filter parameters with defaults
    days_to_delivery = request.args.get('days', 14, type=int)
    customer_id = request.args.get('customer_id')
    threshold = request.args.get('threshold', 5, type=int)  # Days threshold for "tight timeline"

    # Calculate the cutoff date for expediting (orders due within X days)
    cutoff_date = (datetime.now() + timedelta(days=days_to_delivery)).strftime('%Y-%m-%d')

    # Fetch expedite candidates:
    # 1. Sales orders with delivery date within X days
    # 2. Orders with PO deliveries scheduled within threshold days of SO ship date
    # 3. Orders with insufficient stock to fulfill

    query = """
        SELECT 
            so.id AS sales_order_id,
            so.sales_order_ref,
            c.name AS customer_name,
            sol.id AS line_id,
            sol.line_number,
            sol.base_part_number,
            sol.quantity,
            sol.ship_date,
            sol.delivery_date,
            sol.promise_date,
            (SELECT SUM(available_quantity) FROM stock_movements 
             WHERE base_part_number = sol.base_part_number 
               AND movement_type = 'IN' 
               AND available_quantity > 0) AS current_stock,
            pol.id AS po_line_id,
            pol.purchase_order_id,
            po.purchase_order_ref,
            pol.promised_date AS po_delivery_date,
            s.name AS supplier_name,
        FROM sales_order_lines sol
        JOIN sales_orders so ON sol.sales_order_id = so.id
        JOIN customers c ON so.customer_id = c.id
        LEFT JOIN purchase_order_lines pol ON pol.sales_order_line_id = sol.id
        LEFT JOIN purchase_orders po ON pol.purchase_order_id = po.id
        LEFT JOIN suppliers s ON po.supplier_id = s.id
        WHERE 
            (sol.delivery_date <= ? OR sol.ship_date <= ?)
        AND sol.sales_status_id < 3  -- Not shipped yet
    """

    params = [cutoff_date, cutoff_date]

    if customer_id:
        query += " AND so.customer_id = ?"
        params.append(customer_id)

    query += " ORDER BY sol.ship_date"

    raw_orders = db_execute(query, params, fetch='all') or []
    cutoff_dt = datetime.strptime(cutoff_date, '%Y-%m-%d')

    processed_orders = []
    for row in raw_orders:
        order = dict(row)
        ship_dt = _parse_date(order.get('ship_date'))
        order['expedite_reason'] = _determine_expedite_reason(order, threshold, cutoff_dt, ship_dt)
        order['_ship_datetime'] = ship_dt
        processed_orders.append(order)

    expedite_orders = sorted(
        processed_orders,
        key=lambda o: (_EXPEDITE_PRIORITY.get(o.get('expedite_reason'), 5), o.get('_ship_datetime') or datetime.max)
    )

    status_counts = {
        'Stock Shortage': sum(1 for o in expedite_orders if o.get('expedite_reason') == 'Stock Shortage'),
        'PO After Ship Date': sum(1 for o in expedite_orders if o.get('expedite_reason') == 'PO After Ship Date'),
        'Tight Timeline': sum(1 for o in expedite_orders if o.get('expedite_reason') == 'Tight Timeline'),
        'Due Soon': sum(1 for o in expedite_orders if o.get('expedite_reason') == 'Due Soon')
    }

    customers = db_execute("""
        SELECT id, name FROM customers
        ORDER BY name
    """, fetch='all') or []
    customers = [dict(row) for row in customers]

    # Generate breadcrumbs
    breadcrumbs = generate_breadcrumbs(
        ('Home', url_for('index')),
        ('Expediting', url_for('expediting.expedite_dashboard'))
    )

    return render_template(
        'expediting/dashboard.html',
        expedite_orders=expedite_orders,
        status_counts=status_counts,
        customers=customers,
        days=days_to_delivery,
        customer_id=customer_id,
        threshold=threshold,
        breadcrumbs=breadcrumbs
    )


@expediting_bp.route('/details/<int:sales_order_id>', methods=['GET'])
def expedite_details(sales_order_id):
    """
    Detailed view of a specific sales order that needs expediting
    """
    # Fetch basic sales order information
    sales_order = db_execute("""
        SELECT 
            so.id, 
            so.sales_order_ref,
            so.customer_po_ref,
            so.date_entered,
            c.name AS customer_name,
            c.id AS customer_id,
            sp.name AS salesperson_name
        FROM sales_orders so
        JOIN customers c ON so.customer_id = c.id
        LEFT JOIN salespeople sp ON so.salesperson_id = sp.id
        WHERE so.id = ?
    """, (sales_order_id,)).fetchone()

    if not sales_order:
        return "Sales order not found", 404
    sales_order = dict(sales_order)

    # Fetch sales order lines with expedite information
    lines = db_execute("""
        SELECT 
            sol.id,
            sol.line_number,
            sol.base_part_number,
            pn.part_number,
            sol.quantity,
            sol.price,
            sol.ship_date,
            sol.delivery_date,
            sol.promise_date,
            sol.requested_date,
            (SELECT SUM(available_quantity) FROM stock_movements 
             WHERE base_part_number = sol.base_part_number 
             AND movement_type = 'IN' 
             AND available_quantity > 0) AS current_stock,
            pol.id AS po_line_id,
            pol.purchase_order_id,
            po.purchase_order_ref,
            pol.promised_date AS po_delivery_date,
            s.name AS supplier_name
        FROM sales_order_lines sol
        LEFT JOIN part_numbers pn ON sol.base_part_number = pn.base_part_number
        LEFT JOIN purchase_order_lines pol ON pol.sales_order_line_id = sol.id
        LEFT JOIN purchase_orders po ON pol.purchase_order_id = po.id
        LEFT JOIN suppliers s ON po.supplier_id = s.id
        WHERE sol.sales_order_id = ?
        ORDER BY sol.line_number
    """, (sales_order_id,)).fetchall()

    line_rows = [dict(line) for line in lines]
    lines_with_timeline = []
    stock_shortage_lines = []
    late_po_lines = []
    tight_timeline_lines = []

    for line in line_rows:
        ship_dt = _parse_date(line.get('ship_date'))
        po_dt = _parse_date(line.get('po_delivery_date'))
        timeline_days = None
        if ship_dt and po_dt:
            timeline_days = (po_dt - ship_dt).days

        line['timeline_days'] = timeline_days
        lines_with_timeline.append(line)

        if line['current_stock'] is not None and line['quantity'] is not None:
            try:
                if float(line['current_stock']) < float(line['quantity']):
                    stock_shortage_lines.append(line)
            except (TypeError, ValueError):
                pass

        if timeline_days is not None:
            if timeline_days > 0:
                late_po_lines.append(line)
            elif 0 <= timeline_days <= 5:
                tight_timeline_lines.append(line)

    breadcrumbs = generate_breadcrumbs(
        ('Home', url_for('index')),
        ('Expediting', url_for('expediting.expedite_dashboard')),
        (f'SO {sales_order["sales_order_ref"]}', None)
    )

    # Then pass these to the template
    return render_template(
        'expediting/details.html',
        sales_order=sales_order,
        lines=lines_with_timeline,
        stock_shortage_lines=stock_shortage_lines,
        late_po_lines=late_po_lines,
        tight_timeline_lines=tight_timeline_lines,
        breadcrumbs=breadcrumbs
    )
