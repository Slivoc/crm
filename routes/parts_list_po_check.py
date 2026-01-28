"""
Parts List PO Check - Customer Purchase Order verification tool

This module provides functionality to:
1. Upload a customer PO PDF
2. Extract line items and terms via AI
3. Match PO lines to existing parts list quotes
4. Highlight discrepancies and allow marking parts lists as "Won"
"""

from flask import Blueprint, render_template, request, jsonify, session, redirect, url_for
from db import execute as db_execute, db_cursor
import logging
import json
import re
import os
import base64
import requests
from urllib.parse import quote
from openai import OpenAI
from flask_login import login_required

logger = logging.getLogger(__name__)

parts_list_po_check_bp = Blueprint('parts_list_po_check', __name__)

# Initialize OpenAI client
client = OpenAI()

AI_MAX_CHARS = 20000


def _execute_with_cursor(cur, query, params=None):
    """Execute a query on the given cursor with Postgres placeholder translation."""
    prepared = query.replace('?', '%s') if os.getenv('DATABASE_URL', '').startswith(('postgres://', 'postgresql://')) else query
    cur.execute(prepared, params or [])
    return cur


def _normalize_part_number(pn):
    """Normalize a part number for comparison by removing non-alphanumeric characters."""
    if not pn:
        return ''
    return re.sub(r'[^A-Z0-9]', '', str(pn).upper())


def _safe_float(val):
    """Safely convert a value to float."""
    if val is None:
        return None
    try:
        if isinstance(val, str):
            val = val.replace(',', '').replace('$', '').replace('£', '').replace('€', '').strip()
        return float(val)
    except (ValueError, TypeError):
        return None


def _safe_int(val):
    """Safely convert a value to int."""
    if val is None:
        return None
    try:
        return int(float(val))
    except (ValueError, TypeError):
        return None


def extract_customer_po_data(po_text):
    """
    Use OpenAI to extract customer purchase order information from text.

    Returns a dict with:
    - customer_name: Detected customer/company name
    - po_reference: PO number
    - po_date: Date on the PO
    - currency: Currency (USD, GBP, EUR, etc.)
    - incoterms: Incoterms if stated (EXW, FOB, DDP, etc.)
    - payment_terms: Payment terms if stated
    - lines: List of line items with part_number, description, quantity, unit_price, total_price
    """
    try:
        logger.info("extract_customer_po_data: starting extraction")

        # Truncate if too long
        if len(po_text) > AI_MAX_CHARS:
            logger.info(f"Truncating PO text from {len(po_text)} to {AI_MAX_CHARS} chars")
            po_text = po_text[:AI_MAX_CHARS]

        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {
                    "role": "system",
                    "content": """You are an assistant that extracts purchase order information from customer PO documents.
We are in the aerospace hardware industry.

Output ONLY a valid JSON object with DOUBLE QUOTES for all keys and string values.
Do NOT use markdown formatting like ```json or any wrappers. Output raw JSON only.

The JSON object should have these fields:
- customer_name: The customer/company name placing the order (string, null if not found)
- po_reference: The purchase order number/reference (string, null if not found)
- po_date: The date on the PO in YYYY-MM-DD format (string, null if not found)
- currency: The currency code like "USD", "GBP", "EUR" (string, null if not stated)
- incoterms: Incoterms like "EXW", "FOB", "DDP", "CIF", "DAP" (string, null if not stated)
- payment_terms: Payment terms like "Net 30", "30 days", "COD" (string, null if not stated)
- lines: Array of line items, each with:
  - line_number: Line number on the PO (integer, starting from 1 if not specified)
  - part_number: The part number being ordered (string)
  - description: Part description if provided (string, null if not found)
  - quantity: Quantity ordered (integer)
  - unit_price: Unit price (decimal number, null if not stated)
  - total_price: Line total (decimal number, null if not stated)

Important notes:
- Extract ALL line items from the PO
- Part numbers in aerospace often contain letters, numbers, and hyphens
- If unit_price is given but not total, calculate total = unit_price * quantity
- If total is given but not unit, calculate unit_price = total / quantity
- Currency symbols ($, £, €) should be converted to currency codes (USD, GBP, EUR)
- Look for common PO header fields: "Purchase Order", "PO Number", "Order No", "Ship To", "Bill To"
- Payment terms might appear as "Terms:", "Payment:", "Net 30", etc."""
                },
                {
                    "role": "user",
                    "content": f"""Extract all purchase order information from this document:

{po_text}

Return a JSON object with customer_name, po_reference, po_date, currency, incoterms, payment_terms, and lines array."""
                }
            ],
            max_tokens=6000,
            temperature=0.2,
        )

        raw_content = response.choices[0].message.content.strip()
        logger.debug("Raw AI response (first 1000 chars): %r", raw_content[:1000])

        # Strip markdown fences if present
        if raw_content.startswith('```json'):
            raw_content = raw_content[7:]
        if raw_content.startswith('```'):
            raw_content = raw_content[3:]
        if raw_content.endswith('```'):
            raw_content = raw_content[:-3]
        raw_content = raw_content.strip()

        # Find the JSON object
        start = raw_content.find('{')
        end = raw_content.rfind('}')
        if start != -1 and end != -1 and start < end:
            raw_content = raw_content[start:end+1]

        # Parse JSON
        try:
            data = json.loads(raw_content)
        except json.JSONDecodeError as e:
            logger.error("JSON parsing failed: %s", e)
            logger.error("Content that failed: %r", raw_content[:2000])
            return {
                'error': f'Failed to parse AI response: {str(e)}',
                'customer_name': None,
                'po_reference': None,
                'po_date': None,
                'currency': None,
                'incoterms': None,
                'payment_terms': None,
                'lines': []
            }

        # Ensure all expected fields exist
        result = {
            'customer_name': data.get('customer_name'),
            'po_reference': data.get('po_reference'),
            'po_date': data.get('po_date'),
            'currency': data.get('currency'),
            'incoterms': data.get('incoterms'),
            'payment_terms': data.get('payment_terms'),
            'lines': []
        }

        # Process lines
        for idx, line in enumerate(data.get('lines', []), start=1):
            if not isinstance(line, dict):
                continue

            pn = str(line.get('part_number', '')).strip()
            if not pn:
                continue

            qty = _safe_int(line.get('quantity')) or 1
            unit_price = _safe_float(line.get('unit_price'))
            total_price = _safe_float(line.get('total_price'))

            # Calculate missing prices
            if unit_price and not total_price and qty:
                total_price = unit_price * qty
            elif total_price and not unit_price and qty:
                unit_price = total_price / qty

            result['lines'].append({
                'line_number': line.get('line_number') or idx,
                'part_number': pn,
                'base_part_number': _normalize_part_number(pn),
                'description': line.get('description'),
                'quantity': qty,
                'unit_price': unit_price,
                'total_price': total_price
            })

        logger.info("Extracted %d lines from PO", len(result['lines']))
        return result

    except Exception as e:
        logger.exception("PO extraction failed")
        return {
            'error': str(e),
            'customer_name': None,
            'po_reference': None,
            'po_date': None,
            'currency': None,
            'incoterms': None,
            'payment_terms': None,
            'lines': []
        }


def match_po_lines_to_parts_lists(customer_id, po_lines):
    """
    Match PO lines to existing parts list lines for the given customer.

    Returns a list of match results, each containing:
    - po_line: The original PO line data
    - match: The matched parts list line (or None)
    - parts_list: The parts list header info (or None)
    - supplier_info: Supplier-side information (or None)
    - discrepancies: List of discrepancy descriptions
    - match_confidence: 'exact', 'partial', or 'none'
    """
    results = []

    if not po_lines:
        return results

    # Get all quoted parts list lines for this customer, ordered by recency
    # We look at parts lists with status "Quoted" or similar active statuses
    # Join customer_quote_lines to get the actual quoted price and lead time
    parts_list_lines = db_execute(
        """
        SELECT
            pll.id as line_id,
            pll.parts_list_id,
            pll.line_number,
            pll.customer_part_number,
            pll.base_part_number,
            pll.quantity,
            pll.chosen_cost,
            pll.chosen_lead_days,
            pll.chosen_supplier_id,
            pll.chosen_qty,
            pl.name as parts_list_name,
            pl.date_created as parts_list_date,
            pl.status_id,
            pls.name as status_name,
            s.name as supplier_name,
            cur.currency_code as cost_currency,
            -- Customer quote line data (the actual quoted price/lead time to customer)
            cql.id as quote_line_id,
            cql.quote_price_gbp,
            cql.lead_days as quoted_lead_days,
            cql.base_cost_gbp,
            cql.margin_percent,
            cql.quoted_status,
            cql.is_no_bid
        FROM parts_list_lines pll
        JOIN parts_lists pl ON pl.id = pll.parts_list_id
        LEFT JOIN parts_list_statuses pls ON pls.id = pl.status_id
        LEFT JOIN suppliers s ON s.id = pll.chosen_supplier_id
        LEFT JOIN currencies cur ON cur.id = pll.chosen_currency_id
        LEFT JOIN customer_quote_lines cql ON cql.parts_list_line_id = pll.id
        WHERE pl.customer_id = ?
          AND pls.name IN ('Quoted', 'Sent to Suppliers', 'New')
        ORDER BY pl.date_created DESC, pll.line_number ASC
        """,
        (customer_id,),
        fetch='all'
    ) or []

    # Build lookup by normalized part number
    # Key: base_part_number, Value: list of matching lines (most recent first)
    pn_lookup = {}
    for line in parts_list_lines:
        base_pn = _normalize_part_number(line['base_part_number'] or line['customer_part_number'])
        if base_pn:
            if base_pn not in pn_lookup:
                pn_lookup[base_pn] = []
            pn_lookup[base_pn].append(line)

    # Match each PO line
    for po_line in po_lines:
        po_base_pn = po_line.get('base_part_number') or _normalize_part_number(po_line.get('part_number'))
        po_qty = po_line.get('quantity') or 1
        po_price = po_line.get('unit_price')

        match_result = {
            'po_line': po_line,
            'match': None,
            'parts_list': None,
            'supplier_info': None,
            'discrepancies': [],
            'match_confidence': 'none'
        }

        # Look for matches
        candidates = pn_lookup.get(po_base_pn, [])

        if candidates:
            # Find best match - prefer exact quantity match, then closest quantity
            best_match = None
            best_score = -1

            for candidate in candidates:
                score = 0
                candidate_qty = candidate['chosen_qty'] or candidate['quantity'] or 1

                # Exact quantity match is best
                if candidate_qty == po_qty:
                    score += 100
                else:
                    # Partial score for close quantities
                    ratio = min(candidate_qty, po_qty) / max(candidate_qty, po_qty)
                    score += int(ratio * 50)

                # More recent parts lists get a bonus
                # (already sorted by date, so first match gets slight preference)
                if best_match is None:
                    score += 10

                if score > best_score:
                    best_score = score
                    best_match = candidate

            if best_match:
                # Use customer quote price/lead time if available, otherwise fall back to parts list line data
                quoted_price = None
                quoted_lead_days = None
                quote_status = best_match.get('quoted_status')

                # Prefer customer_quote_lines data (actual quoted price to customer)
                if best_match.get('quote_price_gbp'):
                    quoted_price = float(best_match['quote_price_gbp'])

                if best_match.get('quoted_lead_days'):
                    quoted_lead_days = best_match['quoted_lead_days']
                elif best_match.get('chosen_lead_days'):
                    quoted_lead_days = best_match['chosen_lead_days']

                match_result['match'] = {
                    'line_id': best_match['line_id'],
                    'parts_list_id': best_match['parts_list_id'],
                    'line_number': float(best_match['line_number']) if best_match['line_number'] else None,
                    'part_number': best_match['customer_part_number'],
                    'quantity': best_match['chosen_qty'] or best_match['quantity'],
                    'price': quoted_price,  # Customer quoted price from customer_quote_lines
                    'lead_days': quoted_lead_days,  # Lead days from customer_quote_lines
                    'quote_status': quote_status,
                    'is_no_bid': best_match.get('is_no_bid', False)
                }

                match_result['parts_list'] = {
                    'id': best_match['parts_list_id'],
                    'name': best_match['parts_list_name'],
                    'date': best_match['parts_list_date'].isoformat() if best_match['parts_list_date'] else None,
                    'status': best_match['status_name']
                }

                # Supplier info (cost side)
                if best_match['supplier_name']:
                    match_result['supplier_info'] = {
                        'name': best_match['supplier_name'],
                        'cost': float(best_match['chosen_cost']) if best_match['chosen_cost'] else None,
                        'cost_currency': best_match.get('cost_currency'),
                        'lead_days': best_match.get('chosen_lead_days')
                    }

                # Check for discrepancies
                matched_qty = best_match['chosen_qty'] or best_match['quantity'] or 1
                if po_qty != matched_qty:
                    diff = po_qty - matched_qty
                    if diff > 0:
                        match_result['discrepancies'].append(f"Qty: PO has {po_qty}, quoted {matched_qty} (+{diff})")
                    else:
                        match_result['discrepancies'].append(f"Qty: PO has {po_qty}, quoted {matched_qty} ({diff})")

                # Compare PO price to our quoted price (from customer_quote_lines)
                if po_price and quoted_price:
                    if abs(po_price - quoted_price) > 0.01:
                        diff = po_price - quoted_price
                        match_result['discrepancies'].append(
                            f"Price: PO has {po_price:.2f}, quoted {quoted_price:.2f} ({diff:+.2f})"
                        )

                # Set confidence level
                if not match_result['discrepancies']:
                    match_result['match_confidence'] = 'exact'
                else:
                    match_result['match_confidence'] = 'partial'

        results.append(match_result)

    return results


@parts_list_po_check_bp.route('/po-check')
@login_required
def po_check_page():
    """
    Main PO check page.

    Query params:
    - customer_id: Pre-select customer
    - contact_id: Look up customer via contact
    - email: Look up customer via contact email
    """
    preselected_customer = None

    # Try to get customer from various URL params
    customer_id = request.args.get('customer_id', type=int)
    contact_id = request.args.get('contact_id', type=int)
    email = request.args.get('email', '').strip()

    if customer_id:
        customer = db_execute(
            "SELECT id, name FROM customers WHERE id = ?",
            (customer_id,),
            fetch='one'
        )
        if customer:
            preselected_customer = {'id': customer['id'], 'name': customer['name']}

    elif contact_id:
        contact = db_execute(
            """
            SELECT c.id, c.name, c.customer_id, cust.name as customer_name
            FROM contacts c
            JOIN customers cust ON cust.id = c.customer_id
            WHERE c.id = ?
            """,
            (contact_id,),
            fetch='one'
        )
        if contact:
            preselected_customer = {'id': contact['customer_id'], 'name': contact['customer_name']}

    elif email:
        contact = db_execute(
            """
            SELECT c.customer_id, cust.name as customer_name
            FROM contacts c
            JOIN customers cust ON cust.id = c.customer_id
            WHERE LOWER(c.email) = LOWER(?)
            """,
            (email,),
            fetch='one'
        )
        if contact:
            preselected_customer = {'id': contact['customer_id'], 'name': contact['customer_name']}

    return render_template(
        'parts_list_po_check.html',
        preselected_customer=preselected_customer
    )


@parts_list_po_check_bp.route('/po-check/extract', methods=['POST'])
@login_required
def extract_po():
    """
    Extract line items and terms from uploaded PO PDF.
    """
    if 'file' not in request.files:
        return jsonify(success=False, message="No file uploaded"), 400

    file = request.files['file']
    if not file or not file.filename:
        return jsonify(success=False, message="No file selected"), 400

    if not file.filename.lower().endswith('.pdf'):
        return jsonify(success=False, message="File must be a PDF"), 400

    try:
        import pdfplumber

        text = ""
        with pdfplumber.open(file) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    text += page_text + "\n\n"

        if not text.strip():
            return jsonify(
                success=False,
                message="No text found in PDF (might be scanned/image-only)"
            ), 400

        # Extract PO data via AI
        po_data = extract_customer_po_data(text)

        if po_data.get('error'):
            return jsonify(
                success=False,
                message=po_data['error'],
                raw_text=text[:3000]
            ), 400

        return jsonify(
            success=True,
            po_data=po_data,
            raw_text=text[:5000] + ("..." if len(text) > 5000 else ""),
            message=f"Extracted {len(po_data.get('lines', []))} lines from PO"
        )

    except Exception as e:
        logger.exception("PO extraction failed")
        return jsonify(success=False, message=f"Failed to process PDF: {str(e)}"), 500


@parts_list_po_check_bp.route('/po-check/match', methods=['POST'])
@login_required
def match_po():
    """
    Match extracted PO lines to parts list lines for a given customer.
    """
    data = request.get_json(force=True)

    customer_id = data.get('customer_id')
    po_lines = data.get('lines', [])

    if not customer_id:
        return jsonify(success=False, message="Customer ID is required"), 400

    if not po_lines:
        return jsonify(success=False, message="No PO lines to match"), 400

    try:
        # Verify customer exists
        customer = db_execute(
            "SELECT id, name FROM customers WHERE id = ?",
            (customer_id,),
            fetch='one'
        )

        if not customer:
            return jsonify(success=False, message="Customer not found"), 404

        # Run matching algorithm
        matches = match_po_lines_to_parts_lists(customer_id, po_lines)

        # Collect summary stats
        matched_count = sum(1 for m in matches if m['match_confidence'] != 'none')
        exact_count = sum(1 for m in matches if m['match_confidence'] == 'exact')
        partial_count = sum(1 for m in matches if m['match_confidence'] == 'partial')
        unmatched_count = sum(1 for m in matches if m['match_confidence'] == 'none')

        # Get unique affected parts lists
        affected_lists = {}
        for m in matches:
            if m['parts_list']:
                pl_id = m['parts_list']['id']
                if pl_id not in affected_lists:
                    affected_lists[pl_id] = m['parts_list']

        return jsonify(
            success=True,
            customer={'id': customer['id'], 'name': customer['name']},
            matches=matches,
            summary={
                'total_lines': len(po_lines),
                'matched': matched_count,
                'exact_matches': exact_count,
                'partial_matches': partial_count,
                'unmatched': unmatched_count,
                'affected_parts_lists': list(affected_lists.values())
            }
        )

    except Exception as e:
        logger.exception("PO matching failed")
        return jsonify(success=False, message=str(e)), 500


@parts_list_po_check_bp.route('/po-check/confirm', methods=['POST'])
@login_required
def confirm_matches():
    """
    Mark selected parts lists as "Won" status.
    """
    data = request.get_json(force=True)

    parts_list_ids = data.get('parts_list_ids', [])

    if not parts_list_ids:
        return jsonify(success=False, message="No parts lists selected"), 400

    try:
        # Get the "Won" status ID
        won_status = db_execute(
            "SELECT id FROM parts_list_statuses WHERE name = 'Won'",
            fetch='one'
        )

        if not won_status:
            return jsonify(
                success=False,
                message="'Won' status not found. Please run the migration."
            ), 400

        won_status_id = won_status['id']

        # Update each parts list
        updated_count = 0
        updated_lists = []

        for pl_id in parts_list_ids:
            # Get current info
            pl = db_execute(
                """
                SELECT pl.id, pl.name, pls.name as current_status
                FROM parts_lists pl
                LEFT JOIN parts_list_statuses pls ON pls.id = pl.status_id
                WHERE pl.id = ?
                """,
                (pl_id,),
                fetch='one'
            )

            if pl:
                # Update status to Won
                db_execute(
                    """
                    UPDATE parts_lists
                    SET status_id = ?, date_modified = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (won_status_id, pl_id),
                    commit=True
                )
                updated_count += 1
                updated_lists.append({
                    'id': pl_id,
                    'name': pl['name'],
                    'previous_status': pl['current_status'],
                    'new_status': 'Won'
                })

        return jsonify(
            success=True,
            message=f"Updated {updated_count} parts list(s) to 'Won' status",
            updated_lists=updated_lists
        )

    except Exception as e:
        logger.exception("Failed to update parts list status")
        return jsonify(success=False, message=str(e)), 500


@parts_list_po_check_bp.route('/po-check/api/customers/search')
@login_required
def search_customers():
    """
    Search customers by name for the customer selector dropdown.
    Also searches by contact email.
    """
    query = request.args.get('q', '').strip()

    if not query or len(query) < 2:
        return jsonify([])

    try:
        # Search by customer name OR contact email
        customers = db_execute(
            """
            SELECT DISTINCT c.id, c.name
            FROM customers c
            LEFT JOIN contacts cont ON cont.customer_id = c.id
            WHERE LOWER(c.name) LIKE LOWER(?)
               OR LOWER(cont.email) LIKE LOWER(?)
            ORDER BY c.name
            LIMIT 20
            """,
            (f'%{query}%', f'%{query}%'),
            fetch='all'
        ) or []

        return jsonify([
            {'id': c['id'], 'name': c['name']}
            for c in customers
        ])

    except Exception as e:
        logger.exception("Customer search failed")
        return jsonify([])


@parts_list_po_check_bp.route('/po-check/from-email', methods=['POST'])
@login_required
def extract_po_from_email():
    """
    Extract PO from an email attachment (called from mailbox).
    Fetches the PDF from Graph API, extracts data, stores in session,
    and returns URL to redirect to.
    """
    try:
        data = request.get_json(force=True) or {}
        message_id = data.get('message_id')
        attachment_id = data.get('attachment_id')
        sender_email = data.get('sender_email', '').strip()

        if not message_id or not attachment_id:
            return jsonify(success=False, message="message_id and attachment_id are required"), 400

        # Import Graph API helpers from emails module
        from routes.emails import _get_graph_settings, _load_graph_cache, _build_msal_app, _save_graph_cache

        # Get the attachment content from Graph
        settings = _get_graph_settings(include_secret=True)
        cache = _load_graph_cache()
        app = _build_msal_app(settings, cache=cache)
        accounts = app.get_accounts()

        if not accounts:
            return jsonify(success=False, message="No Graph account connected"), 400

        token = app.acquire_token_silent(settings["scopes"], account=accounts[0])
        _save_graph_cache(cache)

        if not token or "access_token" not in token:
            return jsonify(success=False, message="Failed to refresh access token"), 400

        headers = {"Authorization": f"Bearer {token['access_token']}"}
        safe_message_id = quote(message_id, safe="")
        safe_attachment_id = quote(attachment_id, safe="")

        resp = requests.get(
            f"https://graph.microsoft.com/v1.0/me/messages/{safe_message_id}/attachments/{safe_attachment_id}",
            headers=headers,
            timeout=30,
        )

        if resp.status_code >= 400:
            return jsonify(success=False, message="Failed to fetch attachment from Graph"), 400

        try:
            attachment_data = resp.json()
        except ValueError:
            return jsonify(success=False, message="Invalid attachment response"), 400

        # Decode the base64 content
        content_bytes = attachment_data.get("contentBytes")
        if not content_bytes:
            return jsonify(success=False, message="No content in attachment"), 400

        pdf_bytes = base64.b64decode(content_bytes)

        # Extract text from PDF
        import pdfplumber
        import io

        text = ""
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    text += page_text + "\n\n"

        if not text.strip():
            return jsonify(success=False, message="No text found in PDF (might be scanned/image-only)"), 400

        # Extract PO data via AI
        po_data = extract_customer_po_data(text)

        if po_data.get('error'):
            return jsonify(success=False, message=po_data['error']), 400

        # Look up customer from sender email
        preselected_customer = None
        if sender_email:
            contact = db_execute(
                """
                SELECT c.customer_id, cust.name as customer_name
                FROM contacts c
                JOIN customers cust ON cust.id = c.customer_id
                WHERE LOWER(c.email) = LOWER(?)
                """,
                (sender_email,),
                fetch='one'
            )
            if contact:
                preselected_customer = {
                    'id': contact['customer_id'],
                    'name': contact['customer_name']
                }

        # Store extracted data in session for the PO check page to pick up
        session['po_check_preload'] = {
            'po_data': po_data,
            'raw_text': text[:5000] + ("..." if len(text) > 5000 else ""),
            'customer': preselected_customer,
            'attachment_name': attachment_data.get('name', 'attachment.pdf')
        }

        return jsonify(
            success=True,
            redirect_url=url_for('parts_list_po_check.po_check_page'),
            message=f"Extracted {len(po_data.get('lines', []))} lines from PO"
        )

    except Exception as e:
        logger.exception("Failed to extract PO from email attachment")
        return jsonify(success=False, message=str(e)), 500


@parts_list_po_check_bp.route('/po-check/preload-data')
@login_required
def get_preload_data():
    """
    Get pre-loaded PO data from session (used when coming from mailbox).
    """
    preload = session.pop('po_check_preload', None)
    if preload:
        return jsonify(success=True, **preload)
    return jsonify(success=False, message="No preloaded data")
