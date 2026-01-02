import os
import logging
from flask import Blueprint, request, jsonify

from db import execute as db_execute


manufacturer_approvals_bp = Blueprint('manufacturer_approvals', __name__)


def _using_postgres():
    return bool(os.getenv('DATABASE_URL', '').startswith(('postgres://', 'postgresql://')))


def _like_clause(column):
    if _using_postgres():
        return f"{column} ILIKE ?"
    return f"LOWER({column}) LIKE LOWER(?)"


def _parse_optional_int(value):
    if value in (None, ''):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _extract_scalar(row, default=0):
    if not row:
        return default
    if isinstance(row, dict):
        return next(iter(row.values()), default)
    return row[0] if row else default


@manufacturer_approvals_bp.route('/manufacturer-approvals/search', methods=['GET'])
def search_manufacturer_approvals():
    """Search manufacturer approvals with optional filters and pagination."""
    try:
        q = (request.args.get('q') or '').strip()
        import_id = _parse_optional_int(request.args.get('import_id'))
        manufacturer_part_number = (request.args.get('manufacturer_part_number') or '').strip()
        airbus_material = (request.args.get('airbus_material') or '').strip()
        manufacturer_name = (request.args.get('manufacturer_name') or '').strip()
        cage_code = (request.args.get('cage_code') or '').strip()
        approval_status = (request.args.get('approval_status') or '').strip()
        data_type = (request.args.get('data_type') or '').strip()
        standard = (request.args.get('standard') or '').strip()
        p_status = (request.args.get('p_status') or '').strip()

        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 50, type=int)
        per_page = max(1, min(per_page, 250))
        page = max(1, page)

        sort_by = (request.args.get('sort') or 'updated_at').strip()
        sort_dir = (request.args.get('direction') or 'desc').strip().lower()
        allowed_sort = {
            'updated_at',
            'created_at',
            'manufacturer_name',
            'manufacturer_part_number',
            'airbus_material',
            'approval_status',
            'p_status',
            'status_change_date',
        }
        if sort_by not in allowed_sort:
            sort_by = 'updated_at'
        if sort_dir not in ('asc', 'desc'):
            sort_dir = 'desc'

        where_clauses = []
        params = []

        if q:
            search_columns = [
                'manufacturer_name',
                'manufacturer_part_number',
                'airbus_material',
                'airbus_material_text',
                'cage_code',
                'manufacturer_code',
                'standard',
            ]
            search_clauses = [_like_clause(col) for col in search_columns]
            where_clauses.append(f"({ ' OR '.join(search_clauses) })")
            params.extend([f'%{q}%'] * len(search_columns))

        if import_id is not None:
            where_clauses.append('import_id = ?')
            params.append(import_id)

        if manufacturer_part_number:
            where_clauses.append(_like_clause('manufacturer_part_number'))
            params.append(f'%{manufacturer_part_number}%')

        if airbus_material:
            where_clauses.append(_like_clause('airbus_material'))
            params.append(f'%{airbus_material}%')

        if manufacturer_name:
            where_clauses.append(_like_clause('manufacturer_name'))
            params.append(f'%{manufacturer_name}%')

        if cage_code:
            where_clauses.append(_like_clause('cage_code'))
            params.append(f'%{cage_code}%')

        if approval_status:
            where_clauses.append(_like_clause('approval_status'))
            params.append(f'%{approval_status}%')

        if data_type:
            where_clauses.append(_like_clause('data_type'))
            params.append(f'%{data_type}%')

        if standard:
            where_clauses.append(_like_clause('standard'))
            params.append(f'%{standard}%')

        if p_status:
            where_clauses.append(_like_clause('p_status'))
            params.append(f'%{p_status}%')

        where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ''
        offset = (page - 1) * per_page

        count_query = f'''
            SELECT COUNT(*) AS count
            FROM manufacturer_approvals
            {where_sql}
        '''
        total_row = db_execute(count_query, params, fetch='one')
        total = _extract_scalar(total_row, default=0) or 0

        data_query = f'''
            SELECT
                id,
                import_id,
                manufacturer_code,
                manufacturer_name,
                location,
                country,
                cage_code,
                approval_status,
                data_type,
                standard,
                airbus_material,
                airbus_material_text,
                interchangeability_flag,
                manufacturer_part_number,
                usage_restriction,
                p_status,
                p_status_text,
                status_change_date,
                qir_count,
                created_at,
                updated_at
            FROM manufacturer_approvals
            {where_sql}
            ORDER BY {sort_by} {sort_dir}
            LIMIT ? OFFSET ?
        '''
        rows = db_execute(data_query, params + [per_page, offset], fetch='all') or []

        return jsonify({
            'success': True,
            'results': [dict(row) for row in rows],
            'page': page,
            'per_page': per_page,
            'total': total,
            'total_pages': (total + per_page - 1) // per_page if per_page else 0,
        })

    except Exception as exc:
        logging.error(f'Error searching manufacturer approvals: {exc}')
        return jsonify({'success': False, 'error': str(exc)}), 500


@manufacturer_approvals_bp.route('/manufacturer-approvals/imports', methods=['GET'])
def list_manufacturer_approval_imports():
    """List manufacturer approval import history."""
    try:
        limit = request.args.get('limit', 100, type=int)
        limit = max(1, min(limit, 500))

        rows = db_execute('''
            SELECT
                i.id,
                i.source_file,
                i.imported_by,
                i.imported_at,
                i.row_count,
                COALESCE(a.approval_count, 0) AS approval_count
            FROM manufacturer_approval_imports i
            LEFT JOIN (
                SELECT import_id, COUNT(*) AS approval_count
                FROM manufacturer_approvals
                GROUP BY import_id
            ) a ON i.id = a.import_id
            ORDER BY i.imported_at DESC
            LIMIT ?
        ''', (limit,), fetch='all') or []

        return jsonify({
            'success': True,
            'imports': [dict(row) for row in rows],
        })

    except Exception as exc:
        logging.error(f'Error listing manufacturer approval imports: {exc}')
        return jsonify({'success': False, 'error': str(exc)}), 500


@manufacturer_approvals_bp.route('/manufacturer-approvals/imports/<int:import_id>', methods=['GET'])
def get_manufacturer_approval_import(import_id):
    """Fetch a single import row with summary stats."""
    try:
        import_row = db_execute(
            '''
            SELECT id, source_file, imported_by, imported_at, row_count
            FROM manufacturer_approval_imports
            WHERE id = ?
            ''',
            (import_id,),
            fetch='one',
        )

        if not import_row:
            return jsonify({'success': False, 'error': 'Import not found'}), 404

        status_rows = db_execute(
            '''
            SELECT approval_status, COUNT(*) AS count
            FROM manufacturer_approvals
            WHERE import_id = ?
            GROUP BY approval_status
            ORDER BY count DESC
            ''',
            (import_id,),
            fetch='all',
        ) or []

        total_row = db_execute(
            'SELECT COUNT(*) AS count FROM manufacturer_approvals WHERE import_id = ?',
            (import_id,),
            fetch='one',
        )

        return jsonify({
            'success': True,
            'import': dict(import_row),
            'total_records': _extract_scalar(total_row, default=0) or 0,
            'status_breakdown': [dict(row) for row in status_rows],
        })

    except Exception as exc:
        logging.error(f'Error fetching manufacturer approval import {import_id}: {exc}')
        return jsonify({'success': False, 'error': str(exc)}), 500
