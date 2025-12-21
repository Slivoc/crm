from flask import Blueprint, jsonify, request, render_template
from db import db_cursor, execute as db_execute
import os


def _using_postgres():
    return bool(os.getenv('DATABASE_URL', '').startswith(('postgres://', 'postgresql://')))


def _prepare_query(query):
    return query.replace('?', '%s') if _using_postgres() else query


def _execute_with_cursor(cur, query, params=None):
    cur.execute(_prepare_query(query), params or [])
    return cur


def _with_returning_clause(query):
    if not _using_postgres():
        return query
    trimmed = query.strip().rstrip(';')
    return f"{trimmed} RETURNING id"


def _last_inserted_id(cur):
    if _using_postgres():
        row = cur.fetchone()
        if row:
            return row.get('id') if isinstance(row, dict) else row[0]
        return None
    return getattr(cur, 'lastrowid', None)


def _row_to_dict(row):
    return dict(row) if row else None

tax_rates_bp = Blueprint('tax_rates', __name__)


@tax_rates_bp.route('/', methods=['GET'])
def tax_rates_page():
    """Render the tax rates management page."""
    return render_template('tax_rates.html')


@tax_rates_bp.route('/api', methods=['GET'])
def get_tax_rates():
    """Get all tax rates."""
    try:
        tax_rates = db_execute(
            "SELECT id, tax_name, tax_percentage, country, created_at FROM tax_rates ORDER BY tax_name",
            fetch='all'
        ) or []
        tax_rates = [dict(row) for row in tax_rates]

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


@tax_rates_bp.route('/api', methods=['POST'])
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

    # Validate tax percentage
    try:
        tax_percentage = float(data['tax_percentage'])
        if tax_percentage < 0 or tax_percentage > 100:
            return jsonify({
                'success': False,
                'error': 'Tax percentage must be between 0 and 100'
            }), 400
    except ValueError:
        return jsonify({
            'success': False,
            'error': 'Invalid tax percentage'
        }), 400

    try:
        existing_tax = db_execute(
            "SELECT id FROM tax_rates WHERE tax_name = ? AND country = ?",
            (data['tax_name'], data['country']),
            fetch='one'
        )
        if existing_tax:
            return jsonify({
                'success': False,
                'error': f"A tax rate with name '{data['tax_name']}' already exists for {data['country']}"
            }), 409

        insert_query = _with_returning_clause("""
            INSERT INTO tax_rates (tax_name, tax_percentage, country, created_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
        """)

        with db_cursor(commit=True) as cur:
            _execute_with_cursor(cur, insert_query, (
                data['tax_name'],
                data['tax_percentage'],
                data['country']
            ))
            tax_rate_id = _last_inserted_id(cur)

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


@tax_rates_bp.route('/api/<int:tax_id>', methods=['PUT'])
def update_tax_rate(tax_id):
    """Update an existing tax rate."""
    data = request.json

    # Validate required fields
    required_fields = ['tax_name', 'tax_percentage', 'country']
    if not all(field in data for field in required_fields):
        return jsonify({
            'success': False,
            'error': 'Missing required fields'
        }), 400

    # Validate tax percentage
    try:
        tax_percentage = float(data['tax_percentage'])
        if tax_percentage < 0 or tax_percentage > 100:
            return jsonify({
                'success': False,
                'error': 'Tax percentage must be between 0 and 100'
            }), 400
    except ValueError:
        return jsonify({
            'success': False,
            'error': 'Invalid tax percentage'
        }), 400

    try:
        tax_rate = db_execute(
            "SELECT id FROM tax_rates WHERE id = ?",
            (tax_id,),
            fetch='one'
        )

        if not tax_rate:
            return jsonify({
                'success': False,
                'error': 'Tax rate not found'
            }), 404

        existing_tax = db_execute(
            "SELECT id FROM tax_rates WHERE tax_name = ? AND country = ? AND id != ?",
            (data['tax_name'], data['country'], tax_id),
            fetch='one'
        )

        if existing_tax:
            return jsonify({
                'success': False,
                'error': f"Another tax rate with name '{data['tax_name']}' already exists for {data['country']}"
            }), 409

        with db_cursor(commit=True) as cur:
            _execute_with_cursor(cur, """
                UPDATE tax_rates 
                SET tax_name = ?, tax_percentage = ?, country = ?
                WHERE id = ?
            """, (
                data['tax_name'],
                data['tax_percentage'],
                data['country'],
                tax_id
            ))

        return jsonify({
            'success': True,
            'message': 'Tax rate updated successfully'
        })
    except Exception as e:
        print("Error updating tax rate:", str(e))
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@tax_rates_bp.route('/api/<int:tax_id>', methods=['DELETE'])
def delete_tax_rate(tax_id):
    """Delete a tax rate."""
    print(f"Received DELETE request for tax rate ID: {tax_id}")
    try:
        tax_rate = db_execute(
            "SELECT id FROM tax_rates WHERE id = ?",
            (tax_id,),
            fetch='one'
        )

        if not tax_rate:
            return jsonify({
                'success': False,
                'error': 'Tax rate not found'
            }), 404

        usage = db_execute(
            "SELECT COUNT(*) as count FROM invoice_taxes WHERE tax_rate_id = ?",
            (tax_id,),
            fetch='one'
        )
        if usage and usage.get('count', 0) > 0:
            return jsonify({
                'success': False,
                'error': 'Cannot delete tax rate that is in use by invoices'
            }), 409

        with db_cursor(commit=True) as cur:
            _execute_with_cursor(cur, "DELETE FROM tax_rates WHERE id = ?", (tax_id,))

        return jsonify({
            'success': True,
            'message': 'Tax rate deleted successfully'
        })
    except Exception as e:
        print("Error deleting tax rate:", str(e))
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500
