from flask import Blueprint, render_template, jsonify, request, g, current_app
import sqlite3
from datetime import datetime
from models import get_db, dict_from_row
import json

dashboard_bp = Blueprint('dashboard', __name__)


def close_db(e=None):
    db = g.pop('db', None)
    if db is not None:
        db.close()


@dashboard_bp.route('/')
def view_dashboard():
    """Render the dashboard page"""
    conn = get_db()

    # International sales using sales_orders.total_value, current year only
    sales_data = conn.execute('''
        SELECT 
            SUM(CASE WHEN c.country != 'IT' THEN so.total_value ELSE 0 END) as international_sales,
            SUM(so.total_value) as total_sales
        FROM sales_orders so
        JOIN customers c ON so.customer_id = c.id
        WHERE strftime('%Y', so.date_entered) = strftime('%Y', 'now')
    ''').fetchone()

    # Monthly comparison using total_value
    monthly_comparison = conn.execute('''
        WITH current_month AS (
            SELECT SUM(total_value) as sales
            FROM sales_orders
            WHERE strftime('%Y-%m', date_entered) = strftime('%Y-%m', 'now')
        ),
        last_year_month AS (
            SELECT SUM(total_value) as sales
            FROM sales_orders
            WHERE strftime('%Y-%m', date_entered) = strftime('%Y-%m', 'now', '-1 year')
        )
        SELECT 
            current_month.sales as current_sales,
            last_year_month.sales as previous_sales,
            CASE 
                WHEN last_year_month.sales > 0 
                THEN ((current_month.sales - last_year_month.sales) / last_year_month.sales * 100)
                ELSE 0 
            END as growth_percentage
        FROM current_month, last_year_month
    ''').fetchone()

    # Yearly comparison using total_value
    yearly_comparison = conn.execute('''
        WITH current_year AS (
            SELECT SUM(total_value) as sales
            FROM sales_orders
            WHERE strftime('%Y', date_entered) = strftime('%Y', 'now')
        ),
        last_year AS (
            SELECT SUM(total_value) as sales
            FROM sales_orders
            WHERE strftime('%Y', date_entered) = strftime('%Y', 'now', '-1 year')
        )
        SELECT 
            current_year.sales as current_sales,
            last_year.sales as previous_sales,
            CASE 
                WHEN last_year.sales > 0 
                THEN ((current_year.sales - last_year.sales) / last_year.sales * 100)
                ELSE 0 
            END as growth_percentage
        FROM current_year, last_year
    ''').fetchone()

    # New customers this year - both count and sales value
    new_customers_data = conn.execute('''
        WITH first_orders AS (
            SELECT 
                customer_id,
                MIN(date_entered) as first_order_date
            FROM sales_orders 
            GROUP BY customer_id
        )
        SELECT 
            COUNT(DISTINCT fo.customer_id) as new_customer_count,
            SUM(so.total_value) as new_customer_sales
        FROM first_orders fo
        JOIN sales_orders so ON fo.customer_id = so.customer_id
        WHERE strftime('%Y', fo.first_order_date) = strftime('%Y', 'now')
        AND strftime('%Y', so.date_entered) = strftime('%Y', 'now')
    ''').fetchone()

    # Calculate percentages
    total_sales = sales_data['total_sales'] or 0
    international_sales = sales_data['international_sales'] or 0
    international_sales_pct = (international_sales / total_sales * 100) if total_sales > 0 else 0

    # Get saved queries and panels as before
    saved_queries = conn.execute('SELECT id, query_name FROM saved_queries').fetchall()
    panels = conn.execute('SELECT * FROM dashboard_panels ORDER BY panel_order').fetchall()

    new_customer_count = new_customers_data['new_customer_count'] if new_customers_data else 0
    new_customer_sales = new_customers_data['new_customer_sales'] if new_customers_data else 0


    conn.close()

    return render_template(
        'dashboard.html',
        saved_queries=[dict_from_row(query) for query in saved_queries],
        panels=[dict_from_row(panel) for panel in panels],
        breadcrumbs=[('Home', '/'), ('Dashboard', '/dashboard')],
        # Metrics
        international_sales=international_sales,
        total_sales=total_sales,
        international_sales_pct=international_sales_pct,
        # Monthly comparison
        monthly_sales=monthly_comparison['current_sales'] or 0,
        monthly_sales_prev=monthly_comparison['previous_sales'] or 0,
        monthly_growth=monthly_comparison['growth_percentage'] or 0,
        # Yearly comparison
        yearly_sales=yearly_comparison['current_sales'] or 0,
        yearly_sales_prev=yearly_comparison['previous_sales'] or 0,
        yearly_growth=yearly_comparison['growth_percentage'] or 0,
        new_customer_count=new_customer_count,
        new_customer_sales=new_customer_sales if new_customer_sales is not None else 0
    )

@dashboard_bp.route('/panels', methods=['GET'])
def get_panels():
    """Get all dashboard panels"""
    conn = get_db()
    panels = conn.execute('SELECT * FROM dashboard_panels ORDER BY panel_order').fetchall()
    conn.close()
    return jsonify([dict_from_row(panel) for panel in panels])


@dashboard_bp.route('/panels/<int:panel_id>', methods=['GET'])
def get_panel(panel_id):
    """Get panel configuration"""
    db = get_db()

    panel = db.execute('''
        SELECT id, user_id, query_id, display_type, panel_title, panel_order,
               column_mappings, formatting_rules, header_styles, summary_calculation,
               panel_height, panel_width, background_color, text_color, column_styles
        FROM dashboard_panels
        WHERE id = ?
    ''', (panel_id,)).fetchone()

    if not panel:
        return jsonify({'error': 'Panel not found'}), 404

    return jsonify(dict(panel))

@dashboard_bp.route('/panels', methods=['POST'])
def create_panel():
    """Create a new dashboard panel"""
    data = request.json
    db = get_db()

    # Validate required fields
    required_fields = ['query_id', 'display_type', 'panel_order']
    if not all(field in data for field in required_fields):
        return jsonify({'error': 'Missing required fields'}), 400

    try:
        cursor = db.execute(
            '''
            INSERT INTO dashboard_panels 
            (user_id, query_id, display_type, panel_title, panel_order, date_added)
            VALUES (?, ?, ?, ?, ?, ?)
            ''',
            (
                1,  # Default user_id since we don't have login
                data['query_id'],
                data['display_type'],
                data.get('panel_title', 'New Panel'),
                data['panel_order'],
                datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            )
        )
        db.commit()

        return jsonify({
            'success': True,
            'panel_id': cursor.lastrowid
        })

    except Exception as e:
        db.rollback()
        current_app.logger.error(f"Error creating panel: {str(e)}")
        return jsonify({'error': 'Failed to create panel'}), 500


@dashboard_bp.route('/panels/<int:panel_id>', methods=['PUT'])
def update_panel(panel_id):
    """Update panel configuration"""
    data = request.json
    db = get_db()

    try:
        # Build update query dynamically based on provided fields
        update_fields = []
        params = []

        # Basic fields
        if 'query_id' in data:
            update_fields.append('query_id = ?')
            params.append(data['query_id'])
        if 'display_type' in data:
            update_fields.append('display_type = ?')
            params.append(data['display_type'])
        if 'panel_title' in data:
            update_fields.append('panel_title = ?')
            params.append(data['panel_title'])
        if 'panel_order' in data:
            update_fields.append('panel_order = ?')
            params.append(data['panel_order'])

        # Formatting fields
        if 'panel_height' in data:
            update_fields.append('panel_height = ?')
            params.append(data['panel_height'])
        if 'panel_width' in data:
            update_fields.append('panel_width = ?')
            params.append(data['panel_width'])
        if 'background_color' in data:
            update_fields.append('background_color = ?')
            params.append(data['background_color'])
        if 'text_color' in data:
            update_fields.append('text_color = ?')
            params.append(data['text_color'])
        if 'column_mappings' in data:
            update_fields.append('column_mappings = ?')
            params.append(data['column_mappings'])
        if 'formatting_rules' in data:
            update_fields.append('formatting_rules = ?')
            params.append(data['formatting_rules'])
        if 'header_styles' in data:
            update_fields.append('header_styles = ?')
            params.append(data['header_styles'])
        if 'summary_calculation' in data:
            update_fields.append('summary_calculation = ?')
            params.append(data['summary_calculation'])
        # Add column_styles handling
        if 'column_styles' in data:
            update_fields.append('column_styles = ?')
            params.append(data['column_styles'])

        if not update_fields:
            return jsonify({'error': 'No fields to update'}), 400

        params.append(panel_id)  # for WHERE clause

        query = f'''
            UPDATE dashboard_panels 
            SET {', '.join(update_fields)}
            WHERE id = ?
        '''

        db.execute(query, params)
        db.commit()

        return jsonify({'success': True})

    except Exception as e:
        db.rollback()
        current_app.logger.error(f"Error updating panel: {str(e)}")
        return jsonify({'error': f'Failed to update panel: {str(e)}'}), 500

@dashboard_bp.route('/panels/<int:panel_id>', methods=['DELETE'])
def delete_panel(panel_id):
    """Delete a dashboard panel"""
    db = get_db()

    try:
        db.execute('DELETE FROM dashboard_panels WHERE id = ?', (panel_id,))
        db.commit()
        return jsonify({'success': True})

    except Exception as e:
        db.rollback()
        current_app.logger.error(f"Error deleting panel: {str(e)}")
        return jsonify({'error': 'Failed to delete panel'}), 500

@dashboard_bp.route('/panel-data/<int:query_id>')
def get_panel_data(query_id):
    print(f"Fetching data for query ID: {query_id}")
    conn = get_db()
    try:
        # Get the full query configuration
        saved_query = conn.execute('''
            SELECT query, chart_type, 
                   label_column_1, label_column_2,
                   value_column_1, value_column_2 
            FROM saved_queries 
            WHERE id = ?
        ''', (query_id,)).fetchone()

        if not saved_query:
            conn.close()
            return jsonify({'error': 'Query not found'}), 404

        # Execute the saved query
        result = conn.execute(saved_query['query'])
        columns = [description[0] for description in result.description]
        rows = result.fetchall()

        # Convert to list of dictionaries
        data = [dict(zip(columns, row)) for row in rows]

        # Return complete configuration
        response_data = {
            'rows': data,
            'chartType': saved_query['chart_type'],
            'columns': columns,
            'config': {
                'labelColumn1': saved_query['label_column_1'],
                'labelColumn2': saved_query['label_column_2'],
                'valueColumn1': saved_query['value_column_1'],
                'valueColumn2': saved_query['value_column_2']
            }
        }

        conn.close()
        return jsonify(response_data)

    except Exception as e:
        conn.close()
        current_app.logger.error(f"Error executing query: {str(e)}")
        return jsonify({'error': f'Failed to execute query: {str(e)}'}), 500

@dashboard_bp.route('/panel-data/<int:query_id>/<int:panel_id>')
def get_panel_data_with_formatting(query_id, panel_id):
    """Get panel data with formatting applied"""
    print(f"Fetching data for query ID: {query_id} with panel ID: {panel_id}")
    conn = get_db()
    try:
        # Get panel configuration
        panel = conn.execute('''
            SELECT * FROM dashboard_panels WHERE id = ?
        ''', (panel_id,)).fetchone()

        if not panel:
            conn.close()
            return jsonify({'error': 'Panel not found'}), 404

        # Get the full query configuration
        saved_query = conn.execute('''
            SELECT query, chart_type, 
                   label_column_1, label_column_2,
                   value_column_1, value_column_2 
            FROM saved_queries 
            WHERE id = ?
        ''', (query_id,)).fetchone()

        if not saved_query:
            conn.close()
            return jsonify({'error': 'Query not found'}), 404

        # Execute the saved query
        result = conn.execute(saved_query['query'])
        columns = [description[0] for description in result.description]
        rows = result.fetchall()

        # Convert to list of dictionaries
        data = [dict(zip(columns, row)) for row in rows]

        # Parse formatting configuration
        column_mappings = {}
        formatting_rules = {}
        header_styles = {}
        summary_calculation = {}

        if panel['column_mappings']:
            try:
                column_mappings = json.loads(panel['column_mappings'])
            except json.JSONDecodeError as e:
                current_app.logger.error(f"Error parsing column_mappings: {str(e)}")

        if panel['formatting_rules']:
            try:
                formatting_rules = json.loads(panel['formatting_rules'])
            except json.JSONDecodeError as e:
                current_app.logger.error(f"Error parsing formatting_rules: {str(e)}")

        if panel['header_styles']:
            try:
                header_styles = json.loads(panel['header_styles'])
            except json.JSONDecodeError as e:
                current_app.logger.error(f"Error parsing header_styles: {str(e)}")

        if panel['summary_calculation']:
            try:
                summary_calculation = json.loads(panel['summary_calculation'])
            except json.JSONDecodeError as e:
                current_app.logger.error(f"Error parsing summary_calculation: {str(e)}")

        # Calculate summaries if specified
        summary_data = {}
        if summary_calculation:
            for col, calc_type in summary_calculation.items():
                if col in columns:
                    # Filter out non-numeric values
                    values = []
                    for row in data:
                        try:
                            if row[col] is not None:
                                values.append(float(row[col]))
                        except (ValueError, TypeError):
                            # Skip non-numeric values
                            pass

                    if values:
                        if calc_type == 'sum':
                            summary_data[col] = sum(values)
                        elif calc_type == 'avg':
                            summary_data[col] = sum(values) / len(values)
                        elif calc_type == 'min':
                            summary_data[col] = min(values)
                        elif calc_type == 'max':
                            summary_data[col] = max(values)
                        elif calc_type == 'count':
                            summary_data[col] = len(values)
                    else:
                        summary_data[col] = 0

        # Return complete configuration with formatting
        response_data = {
            'rows': data,
            'columns': columns,
            'chartType': saved_query['chart_type'],
            'config': {
                'labelColumn1': saved_query['label_column_1'],
                'labelColumn2': saved_query['label_column_2'],
                'valueColumn1': saved_query['value_column_1'],
                'valueColumn2': saved_query['value_column_2']
            },
            'formatting': {
                'columnMappings': column_mappings,
                'formattingRules': formatting_rules,
                'headerStyles': header_styles
            },
            'summary': summary_data
        }

        conn.close()
        return jsonify(response_data)

    except Exception as e:
        conn.close()
        current_app.logger.error(f"Error executing query: {str(e)}")
        return jsonify({'error': f'Failed to execute query: {str(e)}'}), 500