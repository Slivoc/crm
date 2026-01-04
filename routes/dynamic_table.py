import openai
from flask import Blueprint, request, jsonify, render_template, current_app, flash
import os
import re

from openai import OpenAI

from db import db_cursor, execute as db_execute


# Initialize OpenAI API key
openai.api_key = os.getenv('OPENAI_API_KEY')

# Create a new blueprint for dynamic queries
dynamic_table_bp = Blueprint('dynamic_table_bp', __name__)

# NOTE: This module has been prepared for the dual-mode DB layer.
# Use db_execute/db_cursor so queries run on SQLite by default and on Postgres
# when DATABASE_URL is set.


def _using_postgres() -> bool:
    return bool(os.getenv('DATABASE_URL'))


def _prepare_query(query: str) -> str:
    """Translate SQLite '?' placeholders to psycopg2 '%s' placeholders when needed."""
    if _using_postgres():
        return query.replace('?', '%s')
    return query


def _execute_with_cursor(cur, query: str, params=None):
    if params is None:
        cur.execute(_prepare_query(query))
    else:
        cur.execute(_prepare_query(query), params)
    return cur
def generate_sql_query(user_query):
    # Database structure with full schema
    db_structure = """
    Table: customers (id, name, primary_contact_id, payment_terms, incoterms, salesperson_id, status_id, currency_id, system_code, estimated_revenue, country, logo_url)
    Table: contacts (id, customer_id, name, email)
    Table: customer_status (id, status)
    Table: cqs (id, cq_number, customer_id, status, entry_date, due_date, currency_id, sales_person, created_at, updated_at)
    Table: cq_lines (id, cq_id, base_part_number, part_number, description, condition_code, quantity_requested, quantity_quoted, quantity_allocated, unit_of_measure, unit_cost, unit_price, total_price, lead_days, sales_person, is_no_quote, line_number, serial_number)
    Table: vqs (id, vq_number, supplier_id, status, entry_date, expiration_date, currency_id, created_at)
    Table: vq_lines (id, vq_id, base_part_number, part_number, pn_quoted, description, condition_code, quantity_quoted, quantity_requested, unit_of_measure, lead_days, vendor_price, item_total, line_number, quoted_date)
    Table: part_numbers (base_part_number, part_number, system_part_number, stock, datecode, target_price, SPQ, packaging, rohs, category_id)
    Table: manufacturers (id, name)
    Table: part_manufacturers (base_part_number, manufacturer_id)
    Table: suppliers (id, name, contact_name, contact_email, contact_phone, buffer, currency)
    Table: offer_lines (id, offer_id, base_part_number, manufacturer_id, quantity, price, lead_time)
    Table: industry_tags (id, tag)
    Table: customer_industry_tags (customer_id, tag_id)
    Table: salespeople (id, name)
    Table: files (id, filename, filepath, upload_date)
    Table: offers (id, supplier_id, valid_to, supplier_reference, file_id, price, lead_time, currency_id)
    Table: offer_files (offer_id, file_id)
    Table: statuses (id, status)
    Table: customer_part_numbers (id, base_part_number, customer_part_number, customer_id)
    Table: currencies (id, currency_code, exchange_rate_to_eur, symbol)
    Table: sales_orders (id, sales_order_ref, customer_id, customer_po_ref, salesperson_id, contact_name, date_entered, incoterms, payment_terms, sales_status_id, currency_id, shipping_address_id, invoicing_address_id, updated_at, total_value)
    Table: sales_order_lines (id, sales_order_id, line_number, base_cost, price, quantity, delivery_date, requested_date, promise_date, ship_date, sales_status_id, note, rfq_line_id, updated_at, base_part_number)
    Table: customer_addresses (id, customer_id, address, city, postal_code, country, is_default_shipping, is_default_invoicing)
    Table: purchase_orders (id, purchase_order_ref, supplier_id, date_issued, incoterms, payment_terms, purchase_status_id, currency_id, delivery_address_id, billing_address_id, created_at, updated_at, total_value)
    Table: purchase_order_lines (id, purchase_order_id, line_number, base_part_number, quantity, price, ship_date, promised_date, status_id, sales_order_line_id, received_quantity, created_at, updated_at)
    Table: acknowledgments (id, sales_order_id, acknowledgment_pdf, created_at)
    Table: projects (id, customer_id, salesperson_id, status_id, name)
    Table: project_stages (id, project_id, name, description, parent_stage_id, stage_order, status_id, date_created, due_date, recurrence_id)
    Table: project_stage_salespeople (stage_id, salesperson_id)
    Table: recurrence_types (id, name, interval)
    Table: excess_stock_lists (id, email, customer_id, supplier_id, entered_date, status, upload_date)
    Table: excess_stock_files (excess_stock_list_id, file_id)
    Table: excess_stock_lines (id, excess_stock_list_id, base_part_number, quantity, date_code, manufacturer)
    Table: part_categories (category_id, category_name, description, created_at)
    Table: stock_movements (movement_id, base_part_number, movement_type, quantity, datecode, cost_per_unit, movement_date reference, notes, available_quantity, parent_movement_id)
    Table: parts_lists (id, name, customer_id, salesperson_id, status_id, notes, date_created, date_modified, contact_id)
    Table: parts_list_lines (id, parts_list_id, line_number, customer_part_number, base_part_number, quantity, parent_line_id, line_type, chosen_supplier_id, chosen_cost, chosen_price, chosen_currency_id, chosen_lead_days, customer_notes, internal_notes, date_created, date_modified, chosen_qty)
    Table: parts_list_statuses (id, name, display_order)
    Table: parts_list_supplier_quotes (id, parts_list_id, supplier_id, quote_reference, quote_date, currency_id, notes, date_created, date_modified, created_by_user_id)
    Table: parts_list_supplier_quote_lines (id, supplier_quote_id, parts_list_line_id, quoted_part_number, manufacturer, quantity_quoted, qty_available, purchase_increment, moq, unit_price, lead_time_days, condition_code, certifications, is_no_bid, line_notes, date_created, date_modified)
    Table: parts_list_line_suppliers (id, parts_list_line_id, supplier_id, supplier_name, cost, currency_id, lead_days, source_type, source_reference, condition_code, notes, is_preferred)
    Table: parts_list_line_supplier_emails (id, parts_list_line_id, supplier_id, date_sent, sent_by_user_id, email_subject, email_body, recipient_email, recipient_name, notes)
    Table: parts_list_line_suggested_suppliers (id, parts_list_line_id, supplier_id, source_type, date_added)
    Table: parts_list_no_response_dismissals (id, email_id, dismissed_at)
    Table: customer_quote_lines (id, parts_list_line_id, display_part_number, quoted_part_number, manufacturer, base_cost_gbp, margin_percent, quote_price_gbp, is_no_bid, line_notes, date_created, date_modified, delivery_per_unit, delivery_per_line, quoted_status, lead_days, standard_condition, standard_certs)
    Important Notes:
    - The base_part_number field is used across multiple tables as a reference
    - Columns like system_part_number in part_numbers are distinct fields
    """

    # Enhanced prompt with explicit instructions about comparisons
    prompt = f"""
    Given this database schema:
    {db_structure}

    Task: Generate a PostgreSQL query for this request: "{user_query}"

    Important rules:
    1. Never compare a column to itself (e.g., avoid 'WHERE column = column')
    2. Use proper column names exactly as they appear in the schema
    3. Do NOT add quotes around column names in comparisons
    4. Only use quotes for actual string literals
    5. Join tables when needed to access data across tables
    6. Ensure column references are from the correct tables

    Bad examples:
    - "SELECT * FROM part_numbers WHERE base_part_number = part_number"  # Self-reference
    - "SELECT * FROM part_numbers WHERE base_part_number = 'system_part_number'"  # Wrong: treats column as string
    - "SELECT * FROM part_numbers WHERE base_part_number = system_code"  # Wrong: system_code is from customers table

    Good examples:
    - "SELECT * FROM part_numbers WHERE base_part_number = system_part_number"  # Correct column comparison
    - "SELECT * FROM part_numbers WHERE base_part_number = 'ABC123'"  # Actual string literal
    - "SELECT p.*, c.system_code FROM part_numbers p JOIN customers c ON ..."  # Correct cross-table reference

    Important rules:
    1. Never compare a column to itself (e.g., avoid 'WHERE column = column')
    2. Treat each column as distinct, even if names are similar
    3. Use clear aliases when joining tables
    4. Do NOT add quotes around column names in comparisons
    5. Only use quotes for actual string literals
    6. Return only the SQL query without any formatting or comments

    Bad examples:
    - "SELECT * FROM part_numbers WHERE base_part_number = part_number"  # Self-reference
    - "SELECT * FROM part_numbers WHERE base_part_number = 'system_part_number'"  # Wrong: treats column as string

    Good examples:
    - "SELECT * FROM part_numbers WHERE base_part_number = system_part_number"  # Correct column comparison
    - "SELECT * FROM part_numbers WHERE base_part_number = 'ABC123'"  # Actual string literal
    """

    try:
        client = OpenAI()  # This will use OPENAI_API_KEY environment variable

        response = client.chat.completions.create(
            model="gpt-4o",  # Make sure to use the correct model identifier
            messages=[
                {
                    "role": "system",
                    "content": "You are a SQL expert that generates precise queries while avoiding self-referential comparisons."
                },
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            max_tokens=2000,
            temperature=0.1  # Lower temperature for more consistent output
        )

        sql_query = response.choices[0].message.content.strip()
        return clean_sql_query(sql_query)

    except Exception as e:
        current_app.logger.error(f"Error generating SQL query: {str(e)}")
        return ""

def execute_query(sql_query):
    """Execute a generated SELECT query via the shared DB helpers.

    Important: dynamic SQL is inherently dangerous. We rely on validate_sql_query
    to enforce SELECT-only patterns and block obvious injection patterns.
    
    Returns (columns, rows) where both may be empty lists if no data found.
    This is NOT an error condition - zero rows is a valid result.
    """
    with db_cursor() as cur:
        try:
            _execute_with_cursor(cur, sql_query)

            rows = cur.fetchall()  # list[dict] on Postgres RealDictCursor, sqlite3.Row on SQLite
            current_app.logger.debug(
                "Dynamic query cursor description: %s",
                getattr(cur, "description", None)
            )
            if rows:
                current_app.logger.debug(
                    "Dynamic query first row type: %s, value: %s",
                    type(rows[0]),
                    rows[0]
                )

            # Fetch dynamically generated column names with fallbacks.
            # IMPORTANT: Get columns from cursor description even if no rows returned
            columns = []
            
            # First try to get columns from cursor description (works even with empty results)
            if cur.description:
                for desc in cur.description:
                    if isinstance(desc, (list, tuple)) and desc:
                        columns.append(desc[0])
                    elif hasattr(desc, 'name'):
                        columns.append(desc.name)
            
            # Fallback: if we have rows but no columns yet, extract from first row
            if not columns and rows and hasattr(rows[0], 'keys'):
                columns = list(rows[0].keys())

            # Normalize rows to list[dict]
            if not rows:
                # Empty result is valid - return columns (if found) with empty rows
                current_app.logger.info(
                    "Query returned 0 rows. Columns found: %s", columns
                )
                return columns, []

            first = rows[0]
            if hasattr(first, 'keys'):
                rows_as_dicts = [dict(r) for r in rows]
            else:
                # Fallback for tuple rows
                rows_as_dicts = [dict(zip(columns, r)) for r in rows]

            current_app.logger.info(
                "Query returned %d rows with columns: %s", len(rows_as_dicts), columns
            )
            return columns, rows_as_dicts

        except Exception as e:
            current_app.logger.exception(
                "Error executing dynamic query: %s for query: %s",
                str(e),
                sql_query
            )
            raise


def clean_sql_query(sql_query):
    # Remove any markdown code block syntax
    sql_query = re.sub(r"```.*?```|```sql|```", "", sql_query).strip()

    # Flag potentially problematic patterns
    def check_self_references(query):
        # Look for patterns where a column is compared to itself
        patterns = [
            r'(\w+)\s*=\s*\1\b',  # column = column
            r'(\w+)\s+AS\s+\1\b',  # column AS column
            r'(\w+)\s*<=?\s*\1\b',  # column <= column
            r'(\w+)\s*>=?\s*\1\b',  # column >= column
            r'(\w+)\s*<>\s*\1\b',  # column <> column
            r'(\w+)\s*!=\s*\1\b'  # column != column
        ]

        for pattern in patterns:
            matches = re.finditer(pattern, query, re.IGNORECASE)
            for match in matches:
                raise ValueError(f"Invalid self-referential comparison detected: {match.group(0)}")

        return query

    # Clean up whitespace and check for problems
    sql_query = re.sub(r'\s+', ' ', sql_query).strip()
    return check_self_references(sql_query)


def validate_sql_query(sql_query):
    """Validate the SQL query for safety and correctness."""
    if not sql_query:
        raise ValueError("Empty query generated")

    required_keywords = ["SELECT", "FROM"]
    if not all(keyword in sql_query.upper() for keyword in required_keywords):
        raise ValueError("Missing required SQL keywords")

    # Check for self-referential comparisons
    matches = re.finditer(r'(\w+)\s*=\s*\1\b', sql_query)
    for match in matches:
        raise ValueError(f"Self-referential comparison detected: {match.group(0)}")

    # Check for quoted column names
    matches = re.finditer(r"'(\w+)'\s*(?:=|<|>|<=|>=|<>|!=|\sLIKE\s)", sql_query)
    for match in matches:
        quoted_value = match.group(1)
        if quoted_value in ['system_part_number', 'base_part_number', 'part_number', 'system_code']:
            raise ValueError(f"Possible quoted column name detected: '{quoted_value}'")

    # Define dangerous patterns with proper escaping
    dangerous_patterns = [
        r';\s*DROP\s+TABLE',
        r';\s*DELETE\s+FROM',
        r';\s*UPDATE\s+.*?\s*SET',
        r'--\s*$',
        r'/\*.*?\*/'
    ]

    # Check for SQL injection patterns
    for pattern in dangerous_patterns:
        if re.search(pattern, sql_query, re.IGNORECASE):
            raise ValueError("Potentially unsafe SQL pattern detected")

    return sql_query


@dynamic_table_bp.route('/dynamic_query', methods=['GET', 'POST'])
def dynamic_query():
    # Initialize variables for the current flow
    columns = []
    rows = []
    chart_labels = []
    chart_data = []
    chart_type = 'bar'
    stage = 'query'
    sql_query = request.form.get('sql_query', '')
    user_query = request.form.get('query', '').strip()  # User-entered query
    breadcrumbs = [('Home', '/'), ('Dynamic Query', '/dynamic_query')]

    def get_template_vars():
        return {
            'stage': stage,
            'columns': columns,
            'rows': rows,
            'chart_labels': chart_labels,
            'chart_data': chart_data,
            'chart_type': chart_type,
            'sql_query': sql_query,
            'breadcrumbs': breadcrumbs
        }

    if request.method == 'POST':
        # Check if this is an AJAX request
        is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'

        try:
            # Handle save_chart action specifically (when saving a chart)
            if 'save_chart' in request.form:
                return save_chart_data()  # This always returns a JSON response

            # Step 1: Generate the SQL query dynamically (if we have a user query)
            if user_query:
                sql_query = generate_sql_query(user_query)
                print("Generated SQL Query (Raw):", sql_query)

                # Step 2: Clean and validate the SQL query
                sql_query = clean_sql_query(sql_query)
                print("Cleaned SQL Query:", sql_query)

                sql_query = validate_sql_query(sql_query)
                print("Validated SQL Query:", sql_query)

                # Step 3: Execute the query and fetch results
                columns, rows = execute_query(sql_query)
                current_app.logger.info(f"Fetched Columns: {columns}")
                current_app.logger.info(f"Number of Rows Fetched: {len(rows)}")

                # Handle AJAX requests for chart updates or data fetching
                if is_ajax:
                    return jsonify({
                        'success': True,
                        'columns': columns,
                        'rows': rows,
                        'sql_query': sql_query
                    })

                # Update the stage for chart customization
                stage = 'customize'

            # Return appropriate response based on request type
            if is_ajax:
                # If it's an AJAX request that didn't match any of the above conditions
                return jsonify({
                    'success': False,
                    'error': 'No action performed'
                })
            else:
                # Regular request gets HTML
                return render_template('dynamic_table.html', **get_template_vars())

        except ValueError as e:
            current_app.logger.error(f"Validation Error: {str(e)}")
            if is_ajax:
                return jsonify({'success': False, 'error': str(e)})
            else:
                flash(f"Error: {str(e)}", 'danger')
                return render_template('dynamic_table.html', **get_template_vars())

        except Exception as e:
            error_msg = f"Database error: {str(e)}"
            current_app.logger.error(error_msg)
            if is_ajax:
                return jsonify({'success': False, 'error': error_msg})
            else:
                flash(error_msg, 'danger')
                return render_template('dynamic_table.html', **get_template_vars())

    # GET request - initial page render
    return render_template('dynamic_table.html', **get_template_vars())

@dynamic_table_bp.route('/debug', methods=['POST'])
def debug_route():
    print("Headers:", dict(request.headers))
    print("Form data:", dict(request.form))
    return jsonify({
        'success': True,
        'message': 'Debug information printed to console'
    })


@dynamic_table_bp.route('/dashboard', methods=['GET'])
def dashboard():
    # Get saved queries
    saved_queries = db_execute(
        "SELECT id, query_name, query, chart_type, date_saved FROM saved_queries ORDER BY date_saved DESC",
        fetch='all'
    ) or []

    # Get dashboard panels if the table exists
    try:
        panels = db_execute(
            "SELECT id, panel_title, query_id, display_type, panel_order FROM dashboard_panels ORDER BY panel_order",
            fetch='all'
        ) or []
    except Exception:
        # Table doesn't exist yet (or other DB error); keep UI usable.
        panels = []

    # Create basic context with what we know
    context = {
        'saved_queries': [dict(r) for r in saved_queries],
        'panels': [dict(r) for r in panels],
    }

    # Get list of variables used in the template
    template_path = os.path.join(current_app.template_folder, 'dashboard.html')
    if os.path.exists(template_path):
        try:
            with open(template_path, 'r') as f:
                template_content = f.read()
                # Look for {{ variable }} patterns
                potential_vars = re.findall(r'{{\s*([a-zA-Z0-9_]+)', template_content)

                # Add default values for any variables found
                for var_name in potential_vars:
                    if var_name not in context and not var_name.startswith('_'):
                        context[var_name] = 0
        except Exception as e:
            current_app.logger.error(f"Error parsing template: {str(e)}")

    return render_template('dashboard.html', **context)

def save_chart_data():
    """Handle saving chart data to the database with improved logging for debugging."""
    try:
        # Dump all form data for debugging
        current_app.logger.info("Form data received:")
        for key, value in request.form.items():
            current_app.logger.info(f"  {key}: {value}")

        chart_name = request.form.get('query_name', 'Untitled Chart')  # Use query_name instead of chart_name

        # Try alternative field names in case the form uses a different name
        if chart_name == 'Untitled Chart':
            possible_names = ['title', 'name', 'chart_title', 'queryName']
            for field_name in possible_names:
                if field_name in request.form and request.form[field_name].strip():
                    chart_name = request.form[field_name]
                    current_app.logger.info(f"Found title in alternative field: {field_name}")
                    break

        chart_type = request.form.get('chart_type', 'bar')
        sql_query = request.form.get('sql_query', '')

        # Get the label and value columns as specified in your schema
        label_column_1 = request.form.get('label_column_1', '')
        label_column_2 = request.form.get('label_column_2', '')
        value_column_1 = request.form.get('value_column_1', '')
        value_column_2 = request.form.get('value_column_2', '')

        # Debug logging
        current_app.logger.info(f"Saving chart with name: '{chart_name}'")
        current_app.logger.info(f"SQL Query: {sql_query}")
        current_app.logger.info(f"Chart Type: {chart_type}")
        current_app.logger.info(f"Label Columns: {label_column_1}, {label_column_2}")
        current_app.logger.info(f"Value Columns: {value_column_1}, {value_column_2}")

        insert_sql = """
            INSERT INTO saved_queries
            (query_name, query, chart_type, label_column_1, label_column_2,
             value_column_1, value_column_2)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """

        # Run insert inside a transaction; use RETURNING id on Postgres.
        with db_cursor(commit=True) as cur:
            if _using_postgres():
                insert_sql_pg = insert_sql.strip() + " RETURNING id"
                _execute_with_cursor(
                    cur,
                    insert_sql_pg,
                    (chart_name, sql_query, chart_type, label_column_1, label_column_2, value_column_1, value_column_2)
                )
                row = cur.fetchone()
                query_id = row['id'] if isinstance(row, dict) else row[0]
            else:
                _execute_with_cursor(
                    cur,
                    insert_sql,
                    (chart_name, sql_query, chart_type, label_column_1, label_column_2, value_column_1, value_column_2)
                )
                query_id = getattr(cur, 'lastrowid', None)

        return jsonify({
            'success': True,
            'message': f"Chart '{chart_name}' saved successfully",
            'query_id': query_id,
            'chart_name': chart_name,
        })

    except Exception as e:
        current_app.logger.error(f"Error saving chart: {str(e)}")
        import traceback
        current_app.logger.error(traceback.format_exc())

        return jsonify({
            'success': False,
            'error': f"Error saving chart: {str(e)}"
        })
