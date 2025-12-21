from flask import Blueprint, jsonify, request, render_template, flash, redirect, url_for
from db import execute as db_execute, db_cursor
from models import create_invoice, get_invoice_by_id, get_all_invoices, update_invoice_status, delete_invoice, recalculate_invoice_taxes
from datetime import date
import os

invoices_bp = Blueprint('invoices', __name__, url_prefix='/api/invoices')


def _using_postgres():
    return bool(os.getenv('DATABASE_URL', '').startswith(('postgres://', 'postgresql://')))


def _prepare_query(query):
    return query.replace('?', '%s') if _using_postgres() else query


def _execute_with_cursor(cur, query, params=None):
    cur.execute(_prepare_query(query), params or [])
    return cur


def _insert_and_get_id(cur, query, params=None):
    prepared_query = _prepare_query(query)
    if _using_postgres():
        prepared_query = prepared_query.rstrip().rstrip(';')
        prepared_query = f"{prepared_query} RETURNING id"
        cur.execute(prepared_query, params or [])
        row = cur.fetchone()
        return row['id'] if row else None
    else:
        cur.execute(prepared_query, params or [])
        return getattr(cur, 'lastrowid', None)


@invoices_bp.route('/', methods=['GET'])
def list_invoices():
    """Renders the invoices page with a list of invoices."""
    invoices = get_all_invoices()  # Fetch invoices from the database
    return render_template('invoices.html', invoices=invoices)


@invoices_bp.route('/', methods=['POST'])
def add_invoice():
    """Creates a new invoice."""
    data = request.json
    required_fields = ['sales_order_id', 'customer_id', 'billing_address_id', 'invoice_date', 'due_date', 'currency_id',
                       'total_amount', 'status']

    if not all(field in data for field in required_fields):
        return jsonify({'error': 'Missing required fields'}), 400

    invoice_id = create_invoice(
        sales_order_id=data['sales_order_id'],
        customer_id=data['customer_id'],
        billing_address_id=data['billing_address_id'],
        invoice_date=data['invoice_date'],
        due_date=data['due_date'],
        currency_id=data['currency_id'],
        total_amount=data['total_amount'],
        status=data['status']
    )

    return jsonify({'message': 'Invoice created successfully', 'invoice_id': invoice_id}), 201


@invoices_bp.route('/<int:invoice_id>/status', methods=['PUT'])
def update_invoice(invoice_id):
    """Updates the status of an invoice."""
    data = request.json
    if 'status' not in data:
        return jsonify({'error': 'Missing status field'}), 400

    update_invoice_status(invoice_id, data['status'])
    return jsonify({'message': 'Invoice status updated successfully'})


@invoices_bp.route('/<int:invoice_id>', methods=['DELETE'])
def remove_invoice(invoice_id):
    """Deletes an invoice."""
    delete_invoice(invoice_id)
    return jsonify({'message': 'Invoice deleted successfully'})


@invoices_bp.route('/generate_suggestions', methods=['GET'])
def generate_suggestions():
    """Suggest invoices based on unbilled sales order lines (sales_status_id = 3)."""
    query = """
        SELECT sol.id AS sales_order_line_id, sol.sales_order_id, sol.base_part_number, sol.quantity, sol.price, 
               so.customer_id, c.name AS customer_name
        FROM sales_order_lines sol
        JOIN sales_orders so ON sol.sales_order_id = so.id
        JOIN customers c ON so.customer_id = c.id
        WHERE sol.sales_status_id = 3
        ORDER BY so.customer_id
    """

    sales_order_lines = db_execute(query, fetch='all') or []

    if not sales_order_lines:
        return render_template('invoice_suggestions.html', invoices={})

    grouped_invoices = {}
    for line in sales_order_lines:
        customer_id = line['customer_id']
        grouped_invoices.setdefault(customer_id, {
            'customer_name': line['customer_name'],
            'lines': []
        })
        grouped_invoices[customer_id]['lines'].append({
            'sales_order_line_id': line['sales_order_line_id'],
            'base_part_number': line['base_part_number'],
            'quantity': line['quantity'],
            'price': line['price']
        })

    return render_template('invoice_suggestions.html', invoices=grouped_invoices)


@invoices_bp.route('/generate', methods=['POST'])
def generate_invoice():
    """Generates invoices from suggested sales order lines with currency support."""
    data = request.json
    if not data or 'sales_order_line_ids' not in data:
        return jsonify({'error': 'No sales order lines selected'}), 400

    sales_order_line_ids = data['sales_order_line_ids']

    if not sales_order_line_ids:
        return jsonify({'error': 'Empty sales order line list'}), 400

    ids_clause = ",".join(["?"] * len(sales_order_line_ids))

    customers_query = f"""
        SELECT DISTINCT so.customer_id, c.name AS customer_name, c.currency_id
        FROM sales_order_lines sol
        JOIN sales_orders so ON sol.sales_order_id = so.id
        JOIN customers c ON so.customer_id = c.id
        WHERE sol.id IN ({ids_clause})
    """

    generated_invoices = []
    with db_cursor(commit=True) as cur:
        _execute_with_cursor(cur, customers_query, sales_order_line_ids)
        customers = cur.fetchall() or []

        for customer in customers:
            customer_id = customer['customer_id']
            customer_currency_id = customer['currency_id']

            lines_query = f"""
                SELECT sol.id, sol.sales_order_id, sol.base_part_number, sol.quantity, 
                       sol.price, so.currency_id
                FROM sales_order_lines sol
                JOIN sales_orders so ON sol.sales_order_id = so.id
                WHERE sol.id IN ({ids_clause})
                AND so.customer_id = ?
            """
            _execute_with_cursor(cur, lines_query, sales_order_line_ids + [customer_id])
            lines = cur.fetchall() or []

            if not lines:
                continue

            _execute_with_cursor(cur, "SELECT COUNT(*) as count FROM invoices")
            cursor_count_row = cur.fetchone() or {}
            cursor_count = cursor_count_row.get('count', 0)
            invoice_number = f"INV-{cursor_count + 1:04d}"

            total_amount = 0
            for line in lines:
                line_amount = line['quantity'] * line['price']
                if line['currency_id'] == customer_currency_id:
                    converted_amount = line_amount
                else:
                    _execute_with_cursor(cur,
                        "SELECT exchange_rate_to_eur FROM currencies WHERE id = ?",
                        (line['currency_id'],))
                    line_currency_rate = cur.fetchone()
                    line_currency_rate = line_currency_rate.get('exchange_rate_to_eur', 1) if line_currency_rate else 1
                    _execute_with_cursor(cur,
                        "SELECT exchange_rate_to_eur FROM currencies WHERE id = ?",
                        (customer_currency_id,))
                    customer_currency_rate_row = cur.fetchone()
                    customer_currency_rate = customer_currency_rate_row.get('exchange_rate_to_eur', 1) if customer_currency_rate_row else 1
                    eur_amount = line_amount * line_currency_rate
                    converted_amount = eur_amount / customer_currency_rate if customer_currency_rate else eur_amount

                total_amount += round(converted_amount, 2)

            invoice_id = _insert_and_get_id(cur, """
                INSERT INTO invoices (
                    invoice_number, sales_order_id, customer_id, billing_address_id, 
                    invoice_date, due_date, currency_id, total_amount, status, 
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, DATE('now'), DATE('now', '+30 days'), ?, ?, 'Draft', 
                         CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """, (
                invoice_number, lines[0]['sales_order_id'], customer_id, 1,
                customer_currency_id, total_amount
            ))

            for line in lines:
                _execute_with_cursor(cur, """
                    INSERT INTO invoice_lines (
                        invoice_id, sales_order_line_id, base_part_number, 
                        quantity, unit_price, line_total, currency_id, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """, (
                    invoice_id, line['id'], line['base_part_number'],
                    line['quantity'], line['price'], line['quantity'] * line['price'],
                    line['currency_id']
                ))

            update_query = f"""
                UPDATE sales_order_lines SET sales_status_id = 4 
                WHERE id IN ({ids_clause})
            """
            _execute_with_cursor(cur, update_query, sales_order_line_ids)

            _execute_with_cursor(cur, "SELECT currency_code FROM currencies WHERE id = ?", (customer_currency_id,))
            currency_row = cur.fetchone() or {}
            currency_code = currency_row.get('currency_code', '')

            generated_invoices.append({
                'invoice_id': invoice_id,
                'invoice_number': invoice_number,
                'customer_name': customer['customer_name'],
                'currency': currency_code,
                'total_amount': total_amount
            })

    return jsonify({'message': 'Invoices generated', 'invoices': generated_invoices})


@invoices_bp.route('/view_invoice/<int:invoice_id>', methods=['GET'])
def view_invoice(invoice_id):
    """Retrieve a specific invoice and display it on the invoice page with currency conversion."""
    with db_cursor() as cur:
        _execute_with_cursor(cur, """
            SELECT i.id, i.invoice_number, i.invoice_date, i.due_date, 
                   i.total_amount, i.status, i.currency_id,
                   c.name AS customer_name,
                   cur.currency_code, cur.symbol AS currency_symbol
            FROM invoices i
            JOIN customers c ON i.customer_id = c.id
            JOIN currencies cur ON i.currency_id = cur.id
            WHERE i.id = ?
        """, (invoice_id,))
        invoice = cur.fetchone()

        if not invoice:
            return "Invoice not found", 404

        _execute_with_cursor(cur, """
            SELECT il.id, il.base_part_number, il.quantity, il.unit_price, 
                   il.line_total, il.currency_id,
                   cur.currency_code, cur.symbol AS currency_symbol,
                   cur.exchange_rate_to_eur
            FROM invoice_lines il
            JOIN currencies cur ON il.currency_id = cur.id
            WHERE il.invoice_id = ?
        """, (invoice_id,))
        invoice_lines_raw = cur.fetchall()

        _execute_with_cursor(cur,
                             "SELECT exchange_rate_to_eur FROM currencies WHERE id = ?",
                             (invoice['currency_id'],))
        invoice_currency = cur.fetchone() or {'exchange_rate_to_eur': 1}

        _execute_with_cursor(cur, "SELECT id, currency_code, symbol FROM currencies")
        currencies = cur.fetchall()

        _execute_with_cursor(cur, "SELECT id, tax_name, tax_percentage, country FROM tax_rates")
        tax_rates = cur.fetchall()

        _execute_with_cursor(cur, """
            SELECT it.id, it.tax_amount, tr.tax_name, tr.tax_percentage
            FROM invoice_taxes it
            JOIN tax_rates tr ON it.tax_rate_id = tr.id
            WHERE it.invoice_id = ?
        """, (invoice_id,))
        invoice_taxes = cur.fetchall()

        _execute_with_cursor(cur, """
            SELECT id, discount_type, discount_value
            FROM invoice_discounts
            WHERE invoice_id = ?
        """, (invoice_id,))
        discount_raw = cur.fetchall()

        invoice_discounts = []
        for discount in discount_raw:
            if discount['discount_type'] == 'percentage':
                calculated_amount = round(invoice['total_amount'] * (discount['discount_value'] / 100), 2)
            else:
                calculated_amount = discount['discount_value']

            discount_dict = dict(discount)
            discount_dict['calculated_amount'] = calculated_amount
            invoice_discounts.append(discount_dict)

        _execute_with_cursor(cur, """
            SELECT id, payment_date, payment_method, amount_paid, reference
            FROM invoice_payments
            WHERE invoice_id = ?
            ORDER BY payment_date
        """, (invoice_id,))
        payments = cur.fetchall()

        amount_paid = sum(payment['amount_paid'] for payment in payments)
        subtotal = invoice['total_amount']
        tax_total = sum(tax['tax_amount'] for tax in invoice_taxes)
        discount_total = sum(discount['calculated_amount'] for discount in invoice_discounts)

        invoice_total = subtotal + tax_total - discount_total
        balance_due = invoice_total - amount_paid

        invoice_lines = []
        for line in invoice_lines_raw:
            original_in_eur = line['line_total'] * line['exchange_rate_to_eur']
            converted_total = original_in_eur / invoice_currency['exchange_rate_to_eur']

            line_dict = dict(line)
            line_dict['converted_total'] = round(converted_total, 2)
            invoice_lines.append(line_dict)

    return render_template("invoice.html",
                           invoice=invoice,
                           invoice_lines=invoice_lines,
                           currencies=currencies,
                           tax_rates=tax_rates,
                           invoice_taxes=invoice_taxes,
                           invoice_discounts=invoice_discounts,
                           payments=payments,
                           amount_paid=amount_paid,
                           balance_due=balance_due,
                           tax_total=tax_total,
                           invoice_total=invoice_total,
                           today_date=date.today().isoformat())


from io import BytesIO
import os
from flask import make_response
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_RIGHT
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image


@invoices_bp.route('/generate_pdf/<int:invoice_id>', methods=['GET'])
def generate_pdf(invoice_id):
    """Generate a professional PDF for a specific invoice using ReportLab with multi-currency support."""
    with db_cursor() as cursor:
        _execute_with_cursor(cursor, """
            SELECT i.id, i.invoice_number, i.invoice_date, i.due_date, 
                   i.total_amount, i.status, i.currency_id,
                   c.name AS customer_name,
                   cur.currency_code, cur.symbol AS currency_symbol
            FROM invoices i
            JOIN customers c ON i.customer_id = c.id
            JOIN currencies cur ON i.currency_id = cur.id
            WHERE i.id = ?
        """, (invoice_id,))
        invoice = cursor.fetchone()

        if not invoice:
            return "Invoice not found", 404

        _execute_with_cursor(cursor, """
            SELECT il.base_part_number, il.quantity, il.unit_price, 
                   il.line_total, il.currency_id,
                   cur.currency_code, cur.symbol AS currency_symbol,
                   cur.exchange_rate_to_eur
            FROM invoice_lines il
            JOIN currencies cur ON il.currency_id = cur.id
            WHERE il.invoice_id = ?
        """, (invoice_id,))
        invoice_lines_raw = cursor.fetchall()

        _execute_with_cursor(cursor,
                             "SELECT exchange_rate_to_eur FROM currencies WHERE id = ?",
                             (invoice['currency_id'],))
        invoice_currency = cursor.fetchone() or {'exchange_rate_to_eur': 1}

        _execute_with_cursor(cursor, """
            SELECT it.tax_amount, tr.tax_name, tr.tax_percentage
            FROM invoice_taxes it
            JOIN tax_rates tr ON it.tax_rate_id = tr.id
            WHERE it.invoice_id = ?
        """, (invoice_id,))
        invoice_taxes = cursor.fetchall()

        _execute_with_cursor(cursor, """
            SELECT discount_type, discount_value
            FROM invoice_discounts
            WHERE invoice_id = ?
        """, (invoice_id,))
        discount_raw = cursor.fetchall()

        invoice_discounts = []
        for discount in discount_raw:
            if discount['discount_type'] == 'percentage':
                calculated_amount = round(invoice['total_amount'] * (discount['discount_value'] / 100), 2)
            else:
                calculated_amount = discount['discount_value']

            discount_dict = dict(discount)
            discount_dict['calculated_amount'] = calculated_amount
            invoice_discounts.append(discount_dict)

        subtotal = invoice['total_amount']
        tax_total = sum(tax['tax_amount'] for tax in invoice_taxes)
        discount_total = sum(discount['calculated_amount'] for discount in invoice_discounts)
        invoice_total = subtotal + tax_total - discount_total

        invoice_lines = []
        for line in invoice_lines_raw:
            original_in_eur = line['line_total'] * line['exchange_rate_to_eur']
            converted_total = original_in_eur / invoice_currency['exchange_rate_to_eur']
            line_dict = dict(line)
            line_dict['converted_total'] = round(converted_total, 2)
            invoice_lines.append(line_dict)

    # Define custom colors
    primary_color = colors.HexColor('#336699')  # Professional blue
    secondary_color = colors.HexColor('#F8F8F8')  # Light gray for alternate rows
    accent_color = colors.HexColor('#E8E8E8')  # Slightly darker gray for headers

    # Create a PDF in memory with custom page size for invoices
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        leftMargin=36,
        rightMargin=36,
        topMargin=36,
        bottomMargin=36
    )

    # Create styles
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(
        name='InvoiceTitle',
        parent=styles['Title'],
        fontSize=18,
        textColor=primary_color,
        spaceAfter=12
    ))
    styles.add(ParagraphStyle(
        name='SectionHeader',
        parent=styles['Heading2'],
        fontSize=14,
        textColor=primary_color,
        spaceAfter=6
    ))
    normal_style = styles['Normal']
    styles.add(ParagraphStyle(
        name='NormalRight',
        parent=styles['Normal'],
        fontSize=10,
        alignment=TA_RIGHT
    ))
    styles.add(ParagraphStyle(
        name='Bold',
        parent=styles['Normal'],
        fontName='Helvetica-Bold'
    ))

    elements = []

    # Create header with company name placeholder
    company_name = "R.E.C. Srl"

    # Logo text as placeholder
    logo = Paragraph(f"<font color='{primary_color.hexval()[2:]}' size='16'><b>{company_name}</b></font>",
                     styles['Normal'])

    # Company info section
    company_info = [
        Paragraph(f"<b>{company_name}</b>", styles['Normal']),
        Paragraph("Viale Alcide De Gasperi, 101/103, 20017 Mazzo Di Rho, Milano (IT)", styles['Normal']),
        Paragraph("Phone: +39 0293901089", styles['Normal']),
        Paragraph("Email: sales@recitalia.it", styles['Normal']),
        Paragraph("Website: www.rec-connectors.com", styles['Normal']),
        Paragraph("Tax ID: IT10881270150", styles['Normal'])
    ]

    # Invoice header section
    invoice_header = [
        Paragraph(f"<font color='{primary_color.hexval()[2:]}' size='16'><b>INVOICE</b></font>", styles['NormalRight']),
        Paragraph(f"<b>Invoice #:</b> {invoice['invoice_number']}", styles['NormalRight']),
        Paragraph(f"<b>Date:</b> {invoice['invoice_date']}", styles['NormalRight']),
        Paragraph(f"<b>Due Date:</b> {invoice['due_date']}", styles['NormalRight']),
        Paragraph(f"<b>Status:</b> {invoice['status']}", styles['NormalRight']),
        Paragraph(f"<b>Currency:</b> {invoice['currency_code']}", styles['NormalRight'])
    ]

    # Create header table
    header_data = [[logo, company_info, invoice_header]]
    header_table = Table(header_data, colWidths=[100, 240, 160])
    header_table.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('TOPPADDING', (0, 0), (-1, -1), 0),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
        ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
    ]))
    elements.append(header_table)
    elements.append(Spacer(1, 20))

    # Add customer name only
    elements.append(Paragraph("BILL TO:", styles['SectionHeader']))
    elements.append(Paragraph(f"<b>{invoice['customer_name']}</b>", styles['Normal']))
    elements.append(Spacer(1, 20))

    # Create table for invoice items
    data = [
        ["Part #", "Qty", "Unit Price", "Currency", "Original Total", f"Total ({invoice['currency_code']})"]]

    # Add invoice lines to table
    for line in invoice_lines:
        data.append([
            line['base_part_number'],
            line['quantity'],
            f"{line['currency_symbol']}{line['unit_price']}",
            line['currency_code'],
            f"{line['currency_symbol']}{line['line_total']}",
            f"{invoice['currency_symbol']}{line['converted_total']}"
        ])

    # Create the table with appropriate column widths
    column_widths = [80, 70, 70, 80, 90, 90]
    items_table = Table(data, colWidths=column_widths)

    # Style the table
    items_table.setStyle(TableStyle([
        # Header row styling
        ('BACKGROUND', (0, 0), (-1, 0), primary_color),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 10),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 8),
        ('TOPPADDING', (0, 0), (-1, 0), 8),

        # Content styling
        ('FONTSIZE', (0, 1), (-1, -1), 9),
        ('BOTTOMPADDING', (0, 1), (-1, -1), 6),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.lightgrey),
        ('ALIGN', (2, 1), (-1, -1), 'RIGHT'),  # Right align numbers
        ('ALIGN', (1, 1), (1, -1), 'LEFT'),  # Left align descriptions
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),

        # Zebra striping for rows
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, secondary_color])
    ]))
    elements.append(items_table)

    # Create a table for summary (subtotal, taxes, discounts, total)
    summary_data = []

    # Add subtotal row
    summary_data.append(["Subtotal:", f"{invoice['currency_symbol']}{subtotal:.2f}"])

    # Add tax rows
    for tax in invoice_taxes:
        summary_data.append([
            f"{tax['tax_name']} ({tax['tax_percentage']}%):",
            f"{invoice['currency_symbol']}{tax['tax_amount']:.2f}"
        ])

    # Add discount rows
    for discount in invoice_discounts:
        discount_desc = "Discount"
        if discount['discount_type'] == 'percentage':
            discount_desc += f" ({discount['discount_value']}%)"
        summary_data.append([
            discount_desc + ":",
            f"-{invoice['currency_symbol']}{discount['calculated_amount']:.2f}"
        ])

    # Add final total row
    summary_data.append(["Total Due:", f"{invoice['currency_symbol']}{invoice_total:.2f}"])

    # Create the summary table
    summary_table = Table(summary_data, colWidths=[350, 70], hAlign='RIGHT')

    # Style the summary table
    summary_table.setStyle(TableStyle([
        ('ALIGN', (0, 0), (0, -1), 'RIGHT'),
        ('ALIGN', (1, 0), (1, -1), 'RIGHT'),
        ('FONTNAME', (0, -1), (1, -1), 'Helvetica-Bold'),  # Bold for total
        ('LINEABOVE', (0, -1), (1, -1), 1, colors.black),  # Line above total
        ('BACKGROUND', (0, -1), (1, -1), accent_color),  # Background for total
        ('TOPPADDING', (0, -1), (1, -1), 6),
        ('BOTTOMPADDING', (0, -1), (1, -1), 6),
    ]))
    elements.append(Spacer(1, 12))
    elements.append(summary_table)

    # No notes in original, so removing this section

    # Add payment information and terms
    elements.append(Spacer(1, 20))
    elements.append(Paragraph("Payment Information:", styles['SectionHeader']))
    elements.append(Paragraph("Please include the invoice number in your payment reference.", styles['Normal']))
    elements.append(Paragraph("Bank transfers accepted to: IBAN: XX00 0000 0000 0000 / BIC: XXXXXX", styles['Normal']))

    # Add footer
    def add_page_number(canvas, doc):
        canvas.saveState()
        canvas.setFont('Helvetica', 9)
        footer_text = f"Invoice #{invoice['invoice_number']} - Page {canvas.getPageNumber()}"
        canvas.drawRightString(letter[0] - 36, 36, footer_text)

        # Add a thin line above the footer
        canvas.setStrokeColor(primary_color)
        canvas.line(36, 50, letter[0] - 36, 50)

        # Add thank you message at the bottom
        canvas.setFont('Helvetica-Bold', 10)
        canvas.setFillColor(primary_color)
        canvas.drawCentredString(letter[0] / 2, 30, "Thank you for your business!")
        canvas.restoreState()

    # Build the PDF with the footer function
    doc.build(elements, onFirstPage=add_page_number, onLaterPages=add_page_number)

    # Get the value from the BytesIO buffer
    pdf_data = buffer.getvalue()
    buffer.close()

    # Create response
    response = make_response(pdf_data)
    response.headers['Content-Type'] = 'application/pdf'
    response.headers['Content-Disposition'] = f'inline; filename=invoice_{invoice["invoice_number"]}.pdf'

    return response


@invoices_bp.route('/add_invoice_line/<int:invoice_id>', methods=['POST'])
def add_invoice_line(invoice_id):
    """Add a new line to an existing invoice."""
    data = request.get_json() if request.is_json else request.form
    base_part_number = data.get('base_part_number')
    quantity = float(data.get('quantity', 0))
    unit_price = float(data.get('unit_price', 0))
    currency_id = int(data.get('currency_id', 1))
    line_total = quantity * unit_price

    try:
        if not base_part_number:
            raise ValueError("Part number is required")

        with db_cursor(commit=True) as cursor:
            _execute_with_cursor(cursor,
                                 "SELECT currency_id FROM invoices WHERE id = ?",
                                 (invoice_id,))
            invoice = cursor.fetchone()

            if not invoice:
                return jsonify(success=False, error="Invoice not found"), 404

            invoice_currency_id = invoice['currency_id']

            line_id = _insert_and_get_id(cursor, """
                INSERT INTO invoice_lines (
                    invoice_id, base_part_number, quantity, unit_price, 
                    line_total, currency_id, sales_order_line_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, 0, CURRENT_TIMESTAMP)
            """, (
                invoice_id, base_part_number, quantity, unit_price,
                line_total, currency_id
            ))

            _execute_with_cursor(cursor, """
                SELECT il.line_total, il.currency_id, 
                       c1.exchange_rate_to_eur as line_rate, 
                       c2.exchange_rate_to_eur as invoice_rate
                FROM invoice_lines il
                JOIN currencies c1 ON il.currency_id = c1.id
                JOIN currencies c2 ON c2.id = ?
                WHERE il.invoice_id = ?
            """, (invoice_currency_id, invoice_id))

            lines = cursor.fetchall()
            new_total = 0
            for line in lines:
                if line['currency_id'] == invoice_currency_id:
                    new_total += line['line_total']
                else:
                    eur_amount = line['line_total'] / line['line_rate']
                    converted_amount = eur_amount * line['invoice_rate']
                    new_total += round(converted_amount, 2)

            _execute_with_cursor(cursor,
                                 "UPDATE invoices SET total_amount = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                                 (new_total, invoice_id))

            recalculate_invoice_taxes(invoice_id, cursor.connection, cursor)

            _execute_with_cursor(cursor,
                                 "SELECT currency_code, symbol FROM currencies WHERE id = ?",
                                 (currency_id,))
            currency = cursor.fetchone() or {}

            _execute_with_cursor(cursor,
                                 "SELECT symbol FROM currencies WHERE id = ?",
                                 (invoice_currency_id,))
            currency_symbol_row = cursor.fetchone() or {}
            invoice_currency_symbol = currency_symbol_row.get('symbol', '')

            converted_total = line_total
            if currency_id != invoice_currency_id:
                _execute_with_cursor(cursor,
                                     "SELECT exchange_rate_to_eur FROM currencies WHERE id = ?",
                                     (currency_id,))
                line_currency = cursor.fetchone() or {'exchange_rate_to_eur': 1}
                _execute_with_cursor(cursor,
                                     "SELECT exchange_rate_to_eur FROM currencies WHERE id = ?",
                                     (invoice_currency_id,))
                inv_currency_rate = cursor.fetchone() or {'exchange_rate_to_eur': 1}
                eur_amount = line_total * line_currency['exchange_rate_to_eur']
                converted_total = round(eur_amount / inv_currency_rate['exchange_rate_to_eur'], 2)

        if request.is_json:
            return jsonify({
                'success': True,
                'line_id': line_id,
                'base_part_number': base_part_number,
                'quantity': quantity,
                'unit_price': unit_price,
                'line_total': line_total,
                'currency_code': currency.get('currency_code'),
                'currency_symbol': currency.get('symbol'),
                'converted_total': converted_total,
                'new_invoice_total': new_total
            })
        else:
            flash('Invoice line added successfully!', 'success')
            return redirect(url_for('invoices.view_invoice', invoice_id=invoice_id))

    except Exception as e:
        print("Error adding invoice line:", str(e))
        if request.is_json:
            return jsonify(success=False, error=str(e)), 400
        else:
            flash(f'Error adding invoice line: {str(e)}', 'error')
            return redirect(url_for('invoices.view_invoice', invoice_id=invoice_id))


@invoices_bp.route('/api/invoices/line/<int:line_id>', methods=['DELETE'])
def delete_invoice_line(line_id):
    """Delete an invoice line and update the invoice total."""
    try:
        with db_cursor(commit=True) as cursor:
            _execute_with_cursor(cursor, """
                SELECT il.invoice_id, i.currency_id, cur.symbol AS currency_symbol
                FROM invoice_lines il
                JOIN invoices i ON il.invoice_id = i.id
                JOIN currencies cur ON i.currency_id = cur.id
                WHERE il.id = ?
            """, (line_id,))
            line_info = cursor.fetchone()

            if not line_info:
                return jsonify(success=False, error="Invoice line not found"), 404

            invoice_id = line_info['invoice_id']
            invoice_currency_id = line_info['currency_id']
            currency_symbol = line_info['currency_symbol']

            _execute_with_cursor(cursor, "DELETE FROM invoice_lines WHERE id = ?", (line_id,))

            _execute_with_cursor(cursor, """
                SELECT il.line_total, il.currency_id, 
                       c1.exchange_rate_to_eur as line_rate, 
                       c2.exchange_rate_to_eur as invoice_rate
                FROM invoice_lines il
                JOIN currencies c1 ON il.currency_id = c1.id
                JOIN currencies c2 ON c2.id = ?
                WHERE il.invoice_id = ?
            """, (invoice_currency_id, invoice_id))

            lines = cursor.fetchall()
            new_total = 0
            if lines:
                for line in lines:
                    if line['currency_id'] == invoice_currency_id:
                        new_total += line['line_total']
                    else:
                        eur_amount = line['line_total'] * line['line_rate']
                        converted_amount = eur_amount / line['invoice_rate']
                        new_total += round(converted_amount, 2)

            _execute_with_cursor(cursor,
                                 "UPDATE invoices SET total_amount = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                                 (new_total, invoice_id))

            recalculate_invoice_taxes(invoice_id, cursor.connection, cursor)

        return jsonify(success=True,
                       new_total=new_total,
                       currency_symbol=currency_symbol)

    except Exception as e:
        print("Error deleting invoice line:", str(e))
        return jsonify(success=False, error=str(e)), 500


@invoices_bp.route('/add_invoice_tax/<int:invoice_id>', methods=['POST'])
def add_invoice_tax(invoice_id):
    """Add tax to an invoice and update invoice total in payment summary."""
    data = request.get_json() if request.is_json else request.form
    tax_rate_id = int(data.get('tax_rate_id', 0))

    try:
        if not tax_rate_id:
            raise ValueError("Tax rate is required")

        with db_cursor(commit=True) as cursor:
            _execute_with_cursor(cursor, """
                SELECT i.id, i.total_amount, i.currency_id
                FROM invoices i 
                WHERE i.id = ?
            """, (invoice_id,))
            invoice = cursor.fetchone()
            if not invoice:
                return jsonify(success=False, error="Invoice not found"), 404

            _execute_with_cursor(cursor,
                                 "SELECT tax_percentage, tax_name FROM tax_rates WHERE id = ?",
                                 (tax_rate_id,))
            tax_rate = cursor.fetchone()
            if not tax_rate:
                return jsonify(success=False, error="Tax rate not found"), 404

            tax_amount = round(invoice['total_amount'] * (tax_rate['tax_percentage'] / 100), 2)

            tax_id = _insert_and_get_id(cursor, """
                INSERT INTO invoice_taxes (
                    invoice_id, tax_rate_id, tax_amount, created_at
                ) VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            """, (
                invoice_id, tax_rate_id, tax_amount
            ))

            _execute_with_cursor(cursor, """
                SELECT SUM(tax_amount) as total_taxes
                FROM invoice_taxes
                WHERE invoice_id = ?
            """, (invoice_id,))
            total_taxes_row = cursor.fetchone() or {}
            total_taxes = total_taxes_row.get('total_taxes', 0)

            _execute_with_cursor(cursor, """
                SELECT discount_type, discount_value
                FROM invoice_discounts
                WHERE invoice_id = ?
            """, (invoice_id,))
            discounts = cursor.fetchall()
            total_discount = 0
            for discount in discounts:
                if discount['discount_type'] == 'percentage':
                    discount_amount = round(invoice['total_amount'] * (discount['discount_value'] / 100), 2)
                else:
                    discount_amount = discount['discount_value']
                total_discount += discount_amount

            new_invoice_total = invoice['total_amount'] + total_taxes - total_discount

            _execute_with_cursor(cursor, """
                UPDATE invoices 
                SET updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (invoice_id,))

            _execute_with_cursor(cursor,
                                 "SELECT currency_code, symbol FROM currencies WHERE id = ?",
                                 (invoice['currency_id'],))
            currency = cursor.fetchone() or {}

        return jsonify({
            'success': True,
            'tax_id': tax_id,
            'tax_name': tax_rate['tax_name'],
            'tax_percentage': tax_rate['tax_percentage'],
            'tax_amount': tax_amount,
            'currency_symbol': currency.get('symbol'),
            'currency_code': currency.get('currency_code'),
            'new_invoice_total': new_invoice_total
        })

    except Exception as e:
        print("Error adding tax:", str(e))
        return jsonify(success=False, error=str(e)), 400

@invoices_bp.route('/api/invoices/tax/<int:tax_id>', methods=['DELETE'])
def delete_invoice_tax(tax_id):
    """Remove a tax from an invoice and update invoice total."""
    try:
        with db_cursor(commit=True) as cursor:
            _execute_with_cursor(cursor, """
                SELECT it.invoice_id, it.tax_amount, i.currency_id, i.total_amount 
                FROM invoice_taxes it
                JOIN invoices i ON it.invoice_id = i.id
                WHERE it.id = ?
            """, (tax_id,))
            tax_info = cursor.fetchone()

            if not tax_info:
                return jsonify(success=False, error="Tax not found"), 404

            invoice_id = tax_info['invoice_id']

            _execute_with_cursor(cursor,
                                 "SELECT currency_code, symbol FROM currencies WHERE id = ?",
                                 (tax_info['currency_id'],))
            currency = cursor.fetchone() or {}

            _execute_with_cursor(cursor, "DELETE FROM invoice_taxes WHERE id = ?", (tax_id,))

            _execute_with_cursor(cursor, """
                SELECT SUM(tax_amount) as total_taxes
                FROM invoice_taxes
                WHERE invoice_id = ?
            """, (invoice_id,))
            total_taxes = (cursor.fetchone() or {}).get('total_taxes', 0)

            _execute_with_cursor(cursor, """
                SELECT discount_type, discount_value
                FROM invoice_discounts
                WHERE invoice_id = ?
            """, (invoice_id,))
            discounts = cursor.fetchall()
            total_discount = 0
            for discount in discounts:
                if discount['discount_type'] == 'percentage':
                    discount_amount = round(tax_info['total_amount'] * (discount['discount_value'] / 100), 2)
                else:
                    discount_amount = discount['discount_value']
                total_discount += discount_amount

            new_invoice_total = tax_info['total_amount'] + total_taxes - total_discount

        return jsonify({
            'success': True,
            'new_total': new_invoice_total,
            'currency_symbol': currency.get('symbol'),
            'currency_code': currency.get('currency_code')
        })

    except Exception as e:
        print("Error deleting tax:", str(e))
        return jsonify(success=False, error=str(e)), 500

@invoices_bp.route('/add_invoice_discount/<int:invoice_id>', methods=['POST'])
def add_invoice_discount(invoice_id):
    """Add discount to an invoice."""
    data = request.get_json() if request.is_json else request.form
    discount_type = data.get('discount_type')
    discount_value = float(data.get('discount_value', 0))

    try:
        if not discount_type or discount_value <= 0:
            raise ValueError("Invalid discount details")

        if discount_type not in ['percentage', 'fixed']:
            raise ValueError("Invalid discount type")

        if discount_type == 'percentage' and discount_value > 100:
            raise ValueError("Percentage discount cannot exceed 100%")

        with db_cursor(commit=True) as cursor:
            _execute_with_cursor(cursor, "SELECT id FROM invoices WHERE id = ?", (invoice_id,))
            if not cursor.fetchone():
                return jsonify(success=False, error="Invoice not found"), 404

            _execute_with_cursor(cursor, """
                INSERT INTO invoice_discounts (
                    invoice_id, discount_type, discount_value, created_at
                ) VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            """, (
                invoice_id, discount_type, discount_value
            ))

        return jsonify(success=True)

    except Exception as e:
        print("Error adding discount:", str(e))
        return jsonify(success=False, error=str(e)), 400


@invoices_bp.route('/api/invoices/discount/<int:discount_id>', methods=['DELETE'])
def delete_invoice_discount(discount_id):
    """Remove a discount from an invoice."""
    try:
        with db_cursor(commit=True) as cursor:
            _execute_with_cursor(cursor, "SELECT invoice_id FROM invoice_discounts WHERE id = ?", (discount_id,))
            discount = cursor.fetchone()
            if not discount:
                return jsonify(success=False, error="Discount not found"), 404

            _execute_with_cursor(cursor, "DELETE FROM invoice_discounts WHERE id = ?", (discount_id,))

        return jsonify(success=True)

    except Exception as e:
        print("Error deleting discount:", str(e))
        return jsonify(success=False, error=str(e)), 500


@invoices_bp.route('/add_payment/<int:invoice_id>', methods=['POST'])
def add_payment(invoice_id):
    """Record a payment for an invoice."""
    data = request.get_json() if request.is_json else request.form
    payment_date = data.get('payment_date')
    payment_method = data.get('payment_method')
    amount_paid = float(data.get('amount_paid', 0))
    reference = data.get('reference', '')

    try:
        if not payment_date or not payment_method or amount_paid <= 0:
            raise ValueError("Missing or invalid payment details")

        with db_cursor(commit=True) as cursor:
            _execute_with_cursor(cursor, "SELECT id FROM invoices WHERE id = ?", (invoice_id,))
            if not cursor.fetchone():
                return jsonify(success=False, error="Invoice not found"), 404

            _execute_with_cursor(cursor, """
                INSERT INTO invoice_payments (
                    invoice_id, payment_date, payment_method, amount_paid, reference, created_at
                ) VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """, (
                invoice_id, payment_date, payment_method, amount_paid, reference
            ))

            _execute_with_cursor(cursor, """
                SELECT 
                    (SELECT COALESCE(SUM(amount_paid), 0) FROM invoice_payments WHERE invoice_id = ?) as total_paid,
                    (SELECT total_amount + COALESCE(SUM(tax_amount), 0) - 
                      (SELECT COALESCE(SUM(
                        CASE 
                          WHEN discount_type = 'percentage' THEN (discount_value / 100) * total_amount
                          ELSE discount_value 
                        END), 0) 
                       FROM invoice_discounts WHERE invoice_id = i.id)
                     FROM invoices i 
                     LEFT JOIN invoice_taxes it ON i.id = it.invoice_id
                     WHERE i.id = ?) as total_due
                FROM invoices
                WHERE id = ?
            """, (invoice_id, invoice_id, invoice_id))

            payment_status = cursor.fetchone() or {}
            total_paid = payment_status.get('total_paid', 0)
            total_due = payment_status.get('total_due', 0)

            new_status = None
            if total_paid >= total_due:
                new_status = 'Paid'
            elif total_paid > 0:
                new_status = 'Partially Paid'

            if new_status:
                _execute_with_cursor(cursor,
                                     "UPDATE invoices SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                                     (new_status, invoice_id))

        return jsonify(success=True)

    except Exception as e:
        print("Error adding payment:", str(e))
        return jsonify(success=False, error=str(e)), 400


@invoices_bp.route('/api/invoices/payment/<int:payment_id>', methods=['DELETE'])
def delete_payment(payment_id):
    """Delete a payment and update invoice status."""
    try:
        with db_cursor(commit=True) as cursor:
            _execute_with_cursor(cursor, "SELECT invoice_id FROM invoice_payments WHERE id = ?", (payment_id,))
            payment = cursor.fetchone()

            if not payment:
                return jsonify(success=False, error="Payment not found"), 404

            invoice_id = payment['invoice_id']

            _execute_with_cursor(cursor, "DELETE FROM invoice_payments WHERE id = ?", (payment_id,))

            _execute_with_cursor(cursor, """
                SELECT COALESCE(SUM(amount_paid), 0) as total_paid
                FROM invoice_payments 
                WHERE invoice_id = ?
            """, (invoice_id,))
            new_total_paid = (cursor.fetchone() or {}).get('total_paid', 0)

            new_status = 'Draft'
            if new_total_paid > 0:
                new_status = 'Partially Paid'

            _execute_with_cursor(cursor, """
                SELECT total_amount + COALESCE(SUM(tax_amount), 0) - 
                (SELECT COALESCE(SUM(
                    CASE 
                        WHEN discount_type = 'percentage' THEN (discount_value / 100) * total_amount
                        ELSE discount_value 
                    END), 0) 
                 FROM invoice_discounts WHERE invoice_id = i.id) as total_due
                FROM invoices i 
                LEFT JOIN invoice_taxes it ON i.id = it.invoice_id
                WHERE i.id = ?
            """, (invoice_id,))
            total_due = (cursor.fetchone() or {}).get('total_due', 0)

            if new_total_paid >= total_due:
                new_status = 'Paid'

            _execute_with_cursor(cursor,
                                 "UPDATE invoices SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                                 (new_status, invoice_id))

        return jsonify(success=True)

    except Exception as e:
        print("Error deleting payment:", str(e))
        return jsonify(success=False, error=str(e)), 500


# Add these endpoints to your invoices_bp.py file

@invoices_bp.route('/api/tax_rates', methods=['GET'])
def get_tax_rates():
    """Get all tax rates."""
    try:
        tax_rates = db_execute(
            "SELECT id, tax_name, tax_percentage, country FROM tax_rates ORDER BY tax_name",
            fetch='all'
        ) or []
        return jsonify({
            'success': True,
            'tax_rates': tax_rates
        })
    except Exception as e:
        print("Error fetching tax rates:", str(e))
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@invoices_bp.route('/api/tax_rates', methods=['POST'])
def create_tax_rate():
    """Create a new tax rate."""
    data = request.json

    # Validate required fields
    required_fields = ['tax_name', 'tax_percentage', 'country']
    if not all(field in data for field in required_fields):
        return jsonify({
            'success': False,
            'error': 'Missing required fields'
        }), 400

    try:
        with db_cursor(commit=True) as cursor:
            tax_rate_id = _insert_and_get_id(cursor, """
                INSERT INTO tax_rates (tax_name, tax_percentage, country, created_at)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            """, (
                data['tax_name'],
                data['tax_percentage'],
                data['country']
            ))

        return jsonify({
            'success': True,
            'tax_rate_id': tax_rate_id,
            'message': 'Tax rate created successfully'
        })
    except Exception as e:
        print("Error creating tax rate:", str(e))
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500
