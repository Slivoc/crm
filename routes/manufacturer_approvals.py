import logging
import os
import tempfile
import psycopg2
import requests
from bs4 import BeautifulSoup
from flask import Blueprint, current_app, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user
from werkzeug.utils import secure_filename

from db import db_cursor, execute as db_execute
from manufacturer_approval_importer import LIST_TYPES, process_workbooks


manufacturer_approvals_bp = Blueprint('manufacturer_approvals', __name__)

ALLOWED_EXTENSIONS = {'.csv', '.xlsx', '.xlsm'}
DEFAULT_LIST_TYPE = 'airbus_fixed_wing'
AIRBUS_AQPL_PAGE_URL = 'https://info.airbus.com/en/aqpl-airbus-qualified-parts-list'
AIRBUS_DOWNLOAD_HOST = 'mediaassets.airbus.com'


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


def _normalize_list_type(value):
    value = (value or DEFAULT_LIST_TYPE).strip()
    return value if value in LIST_TYPES else DEFAULT_LIST_TYPE


def _base_filters_from_request(args):
    return {
        'q': (args.get('q') or '').strip(),
        'import_id': _parse_optional_int(args.get('import_id')),
        'manufacturer_part_number': (args.get('manufacturer_part_number') or '').strip(),
        'airbus_material': (args.get('airbus_material') or '').strip(),
        'manufacturer_name': (args.get('manufacturer_name') or '').strip(),
        'cage_code': (args.get('cage_code') or '').strip(),
        'approval_status': (args.get('approval_status') or '').strip(),
        'data_type': (args.get('data_type') or '').strip(),
        'standard': (args.get('standard') or '').strip(),
        'p_status': (args.get('p_status') or '').strip(),
        'approval_list_type': _normalize_list_type(args.get('approval_list_type')),
    }


def _search_manufacturer_approvals_data(args):
    filters = _base_filters_from_request(args)

    page = args.get('page', 1, type=int)
    per_page = args.get('per_page', 50, type=int)
    per_page = max(1, min(per_page, 250))
    page = max(1, page)

    sort_by = (args.get('sort') or 'updated_at').strip()
    sort_dir = (args.get('direction') or 'desc').strip().lower()
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

    q = filters['q']
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
        where_clauses.append(f"({' OR '.join(search_clauses)})")
        params.extend([f'%{q}%'] * len(search_columns))

    if filters['approval_list_type']:
        where_clauses.append('approval_list_type = ?')
        params.append(filters['approval_list_type'])

    if filters['import_id'] is not None:
        where_clauses.append('import_id = ?')
        params.append(filters['import_id'])

    for field in (
        'manufacturer_part_number',
        'airbus_material',
        'manufacturer_name',
        'cage_code',
        'approval_status',
        'data_type',
        'standard',
        'p_status',
    ):
        value = filters[field]
        if value:
            where_clauses.append(_like_clause(field))
            params.append(f'%{value}%')

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
            approval_list_type,
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

    return {
        'filters': filters,
        'results': [dict(row) for row in rows],
        'page': page,
        'per_page': per_page,
        'total': total,
        'total_pages': (total + per_page - 1) // per_page if per_page else 0,
        'sort': sort_by,
        'direction': sort_dir,
    }


def _get_import_history(limit=25, approval_list_type=None):
    where_sql = ''
    params = []
    if approval_list_type:
        where_sql = 'WHERE i.approval_list_type = ?'
        params.append(approval_list_type)

    query = f'''
        SELECT
            i.id,
            i.source_file,
            i.source_files_json,
            i.source_file_count,
            i.imported_by,
            i.imported_at,
            i.row_count,
            i.approval_list_type,
            COALESCE(i.overwrite_existing, FALSE) AS overwrite_existing,
            COALESCE(a.approval_count, 0) AS approval_count
        FROM manufacturer_approval_imports i
        LEFT JOIN (
            SELECT import_id, COUNT(*) AS approval_count
            FROM manufacturer_approvals
            GROUP BY import_id
        ) a ON i.id = a.import_id
        {where_sql}
        ORDER BY i.imported_at DESC
        LIMIT ?
    '''
    rows = db_execute(query, params + [limit], fetch='all') or []
    return [dict(row) for row in rows]


def _get_list_summaries():
    query = '''
        WITH latest_imports AS (
            SELECT DISTINCT ON (approval_list_type)
                approval_list_type,
                id,
                imported_at,
                imported_by,
                row_count,
                source_file,
                source_file_count
            FROM manufacturer_approval_imports
            ORDER BY approval_list_type, imported_at DESC, id DESC
        ),
        counts AS (
            SELECT approval_list_type, COUNT(*) AS active_rows
            FROM manufacturer_approvals
            GROUP BY approval_list_type
        )
        SELECT
            COALESCE(lt.approval_list_type, c.approval_list_type) AS approval_list_type,
            COALESCE(c.active_rows, 0) AS active_rows,
            lt.id AS latest_import_id,
            lt.imported_at AS latest_imported_at,
            lt.imported_by AS latest_imported_by,
            lt.row_count AS latest_row_count,
            lt.source_file,
            lt.source_file_count
        FROM latest_imports lt
        FULL OUTER JOIN counts c ON c.approval_list_type = lt.approval_list_type
    '''
    rows = db_execute(query, fetch='all') or []
    summary_map = {key: {'key': key, 'label': label, 'active_rows': 0} for key, label in LIST_TYPES.items()}
    for row in rows:
        row_dict = dict(row)
        key = row_dict.get('approval_list_type')
        if not key:
            continue
        summary_map.setdefault(key, {'key': key, 'label': LIST_TYPES.get(key, key), 'active_rows': 0})
        summary_map[key].update(row_dict)
        summary_map[key]['label'] = LIST_TYPES.get(key, key)
    return [summary_map[key] for key in LIST_TYPES]


def _serialize_non_empty_filters(filters, **extra):
    params = {}
    for key, value in {**filters, **extra}.items():
        if value in (None, ''):
            continue
        params[key] = value
    return params


def _save_uploaded_files(files):
    temp_paths = []
    for storage in files:
        filename = secure_filename(storage.filename or '')
        suffix = os.path.splitext(filename)[1].lower()
        if not filename:
            raise ValueError('One of the uploaded files is missing a filename.')
        if suffix not in ALLOWED_EXTENSIONS:
            raise ValueError(f'{filename} is not a supported source file. Upload .csv, .xlsx, or .xlsm files only.')

        with tempfile.NamedTemporaryFile(prefix='manufacturer-approval-', suffix=suffix, delete=False) as tmp:
            storage.save(tmp.name)
            temp_paths.append(tmp.name)
    return temp_paths


def _extract_airbus_fixed_wing_downloads(html):
    soup = BeautifulSoup(html, 'html.parser')
    downloads = []
    seen = set()

    for link in soup.find_all('a', href=True):
        href = (link.get('href') or '').strip()
        if not href or AIRBUS_DOWNLOAD_HOST not in href.lower():
            continue
        if not href.lower().endswith('.csv'):
            continue
        if href in seen:
            continue
        seen.add(href)
        downloads.append({
            'url': href,
            'label': link.get_text(' ', strip=True) or os.path.basename(href),
        })

    if len(downloads) != 2:
        raise RuntimeError(
            f'Expected exactly 2 Airbus fixed-wing CSV downloads on {AIRBUS_AQPL_PAGE_URL}, found {len(downloads)}.'
        )

    return downloads


def _download_remote_file(url, *, prefix):
    response = requests.get(url, stream=True, timeout=(20, 300))
    response.raise_for_status()

    filename = secure_filename(os.path.basename(url.split('?', 1)[0]) or f'{prefix}.csv')
    if not filename.lower().endswith('.csv'):
        filename = f'{filename}.csv'

    with tempfile.NamedTemporaryFile(
        prefix=f'{prefix}-{os.path.splitext(filename)[0]}-',
        suffix='.csv',
        delete=False,
    ) as tmp:
        for chunk in response.iter_content(chunk_size=1024 * 1024):
            if chunk:
                tmp.write(chunk)
        return tmp.name


def _fetch_airbus_fixed_wing_temp_files():
    response = requests.get(AIRBUS_AQPL_PAGE_URL, timeout=(20, 60))
    response.raise_for_status()

    download_specs = _extract_airbus_fixed_wing_downloads(response.text)
    temp_paths = []
    for index, spec in enumerate(download_specs, start=1):
        temp_paths.append(_download_remote_file(spec['url'], prefix=f'airbus-fixed-wing-{index}'))
    return temp_paths


def _reassign_import_list_type(import_id, target_list_type):
    with db_cursor(commit=True) as cur:
        cur.execute(
            '''
            SELECT id, approval_list_type
            FROM manufacturer_approval_imports
            WHERE id = ?
            ''',
            (import_id,),
        )
        import_row = cur.fetchone()
        if not import_row:
            raise ValueError('Import not found.')

        current_list_type = import_row['approval_list_type']
        if current_list_type == target_list_type:
            return {'updated_rows': 0, 'deleted_conflicts': 0, 'previous_list_type': current_list_type}

        cur.execute(
            '''
            DELETE FROM manufacturer_approvals target
            USING manufacturer_approvals source
            WHERE source.import_id = ?
              AND source.id <> target.id
              AND source.approval_list_type = ?
              AND target.approval_list_type = ?
              AND COALESCE(source.airbus_material, '') = COALESCE(target.airbus_material, '')
              AND COALESCE(source.manufacturer_part_number, '') = COALESCE(target.manufacturer_part_number, '')
              AND source.manufacturer_name = target.manufacturer_name
              AND COALESCE(source.cage_code, '') = COALESCE(target.cage_code, '')
              AND COALESCE(source.location, '') = COALESCE(target.location, '')
            ''',
            (import_id, current_list_type, target_list_type),
        )
        deleted_conflicts = cur.rowcount or 0

        cur.execute(
            '''
            UPDATE manufacturer_approvals
            SET approval_list_type = ?,
                updated_at = NOW()
            WHERE import_id = ?
            ''',
            (target_list_type, import_id),
        )
        updated_rows = cur.rowcount or 0

        cur.execute(
            '''
            UPDATE manufacturer_approval_imports
            SET approval_list_type = ?
            WHERE id = ?
            ''',
            (target_list_type, import_id),
        )

    return {
        'updated_rows': updated_rows,
        'deleted_conflicts': deleted_conflicts,
        'previous_list_type': current_list_type,
    }


@manufacturer_approvals_bp.route('/manufacturer-approvals', methods=['GET'])
def manufacturer_approvals_dashboard():
    try:
        search_data = _search_manufacturer_approvals_data(request.args)
        filters = search_data['filters']
        page_params = _serialize_non_empty_filters(
            filters,
            per_page=search_data['per_page'],
            sort=search_data['sort'],
            direction=search_data['direction'],
        )
        imports_filter = request.args.get('imports_list_type') or filters['approval_list_type']
        prev_page_url = (
            url_for(
                'manufacturer_approvals.manufacturer_approvals_dashboard',
                **(page_params | {'page': search_data['page'] - 1}),
            )
            if search_data['page'] > 1
            else '#'
        )
        next_page_url = (
            url_for(
                'manufacturer_approvals.manufacturer_approvals_dashboard',
                **(page_params | {'page': search_data['page'] + 1}),
            )
            if search_data['page'] < search_data['total_pages']
            else '#'
        )

        return render_template(
            'manufacturer_approvals.html',
            list_types=LIST_TYPES,
            summaries=_get_list_summaries(),
            search=search_data,
            page_params=page_params,
            prev_page_url=prev_page_url,
            next_page_url=next_page_url,
            imports=_get_import_history(limit=20, approval_list_type=_normalize_list_type(imports_filter) if imports_filter else None),
            allowed_extensions=sorted(ALLOWED_EXTENSIONS),
        )
    except Exception as exc:
        logging.exception('Error rendering manufacturer approvals dashboard')
        flash(f'Unable to load manufacturer approvals: {exc}', 'danger')
        return render_template(
            'manufacturer_approvals.html',
            list_types=LIST_TYPES,
            summaries=[],
            search={'filters': _base_filters_from_request(request.args), 'results': [], 'page': 1, 'per_page': 50, 'total': 0, 'total_pages': 0, 'sort': 'updated_at', 'direction': 'desc'},
            page_params={},
            prev_page_url='#',
            next_page_url='#',
            imports=[],
            allowed_extensions=sorted(ALLOWED_EXTENSIONS),
        )


@manufacturer_approvals_bp.route('/manufacturer-approvals/import', methods=['POST'])
def import_manufacturer_approvals():
    files = [f for f in request.files.getlist('files') if f and (f.filename or '').strip()]
    approval_list_type = _normalize_list_type(request.form.get('approval_list_type'))
    overwrite_existing = request.form.get('overwrite_existing', '1') == '1'
    batch_size = request.form.get('batch_size', 5000, type=int)
    batch_size = max(250, min(batch_size, 10000))

    if not files:
        flash('Upload at least one Airbus source file to import approvals.', 'warning')
        return redirect(url_for('manufacturer_approvals.manufacturer_approvals_dashboard', approval_list_type=approval_list_type))

    temp_paths = []
    try:
        temp_paths = _save_uploaded_files(files)
        database_url = os.getenv('DATABASE_URL')
        if not database_url:
            raise RuntimeError('DATABASE_URL is required to import manufacturer approvals.')

        imported_by = None
        if getattr(current_user, 'is_authenticated', False):
            imported_by = getattr(current_user, 'username', None) or getattr(current_user, 'email', None)
        imported_by = imported_by or os.getenv('USER') or 'web'

        connection = psycopg2.connect(database_url)
        try:
            stats = process_workbooks(
                connection,
                workbook_paths=temp_paths,
                approval_list_type=approval_list_type,
                batch_size=batch_size,
                overwrite_existing=overwrite_existing,
                imported_by=imported_by,
            )
        finally:
            connection.close()

        flash(
            (
                f"Imported {stats['rows_written']:,} Airbus approvals into {LIST_TYPES[approval_list_type]} "
                f"from {stats['files_processed']} source file(s). "
                f"Skipped {stats['rows_skipped']:,} invalid row(s)"
                + (f" and removed {stats['deleted_previous_rows']:,} previous row(s)." if overwrite_existing else '.')
            ),
            'success',
        )
    except Exception as exc:
        logging.exception('Error importing manufacturer approvals')
        flash(f'Import failed: {exc}', 'danger')
    finally:
        for temp_path in temp_paths:
            try:
                os.remove(temp_path)
            except OSError:
                current_app.logger.warning('Could not remove temp file %s', temp_path)

    return redirect(url_for('manufacturer_approvals.manufacturer_approvals_dashboard', approval_list_type=approval_list_type))


@manufacturer_approvals_bp.route('/manufacturer-approvals/import-airbus-fixed-wing', methods=['POST'])
def import_airbus_fixed_wing_from_airbus():
    temp_paths = []
    approval_list_type = 'airbus_fixed_wing'
    batch_size = request.form.get('batch_size', 5000, type=int)
    batch_size = max(250, min(batch_size, 10000))

    try:
        temp_paths = _fetch_airbus_fixed_wing_temp_files()
        database_url = os.getenv('DATABASE_URL')
        if not database_url:
            raise RuntimeError('DATABASE_URL is required to import manufacturer approvals.')

        imported_by = None
        if getattr(current_user, 'is_authenticated', False):
            imported_by = getattr(current_user, 'username', None) or getattr(current_user, 'email', None)
        imported_by = imported_by or os.getenv('USER') or 'web'

        connection = psycopg2.connect(database_url)
        try:
            stats = process_workbooks(
                connection,
                workbook_paths=temp_paths,
                approval_list_type=approval_list_type,
                batch_size=batch_size,
                overwrite_existing=True,
                imported_by=imported_by,
            )
        finally:
            connection.close()

        flash(
            (
                f"Fetched and imported {stats['rows_written']:,} Airbus approvals into {LIST_TYPES[approval_list_type]} "
                f"from {stats['files_processed']} Airbus CSV file(s). "
                f"Skipped {stats['rows_skipped']:,} invalid row(s) and removed {stats['deleted_previous_rows']:,} previous row(s)."
            ),
            'success',
        )
    except Exception as exc:
        logging.exception('Error importing Airbus fixed wing approvals from Airbus')
        flash(f'Airbus fetch/import failed: {exc}', 'danger')
    finally:
        for temp_path in temp_paths:
            try:
                os.remove(temp_path)
            except OSError:
                current_app.logger.warning('Could not remove temp file %s', temp_path)

    return redirect(url_for('manufacturer_approvals.manufacturer_approvals_dashboard', approval_list_type=approval_list_type))


@manufacturer_approvals_bp.route('/manufacturer-approvals/imports/<int:import_id>/list-type', methods=['POST'])
def update_manufacturer_approval_import_list_type(import_id):
    target_list_type = _normalize_list_type(request.form.get('approval_list_type'))

    try:
        result = _reassign_import_list_type(import_id, target_list_type)
        if result['updated_rows'] == 0 and result['previous_list_type'] == target_list_type:
            flash(f'Import #{import_id} is already assigned to {LIST_TYPES[target_list_type]}.', 'info')
        else:
            message = (
                f"Import #{import_id} moved from {LIST_TYPES[result['previous_list_type']]} "
                f"to {LIST_TYPES[target_list_type]}. Updated {result['updated_rows']:,} row(s)."
            )
            if result['deleted_conflicts']:
                message += f" Removed {result['deleted_conflicts']:,} duplicate row(s) already present in the target list."
            flash(message, 'success')
    except Exception as exc:
        logging.exception('Error reassigning manufacturer approval import %s', import_id)
        flash(f'Unable to reassign import #{import_id}: {exc}', 'danger')

    return redirect(
        url_for(
            'manufacturer_approvals.manufacturer_approvals_dashboard',
            approval_list_type=target_list_type,
            imports_list_type=target_list_type,
        )
    )


@manufacturer_approvals_bp.route('/manufacturer-approvals/search', methods=['GET'])
def search_manufacturer_approvals():
    """Search manufacturer approvals with optional filters and pagination."""
    try:
        data = _search_manufacturer_approvals_data(request.args)
        return jsonify({'success': True, **data})
    except Exception as exc:
        logging.error('Error searching manufacturer approvals: %s', exc)
        return jsonify({'success': False, 'error': str(exc)}), 500


@manufacturer_approvals_bp.route('/manufacturer-approvals/imports', methods=['GET'])
def list_manufacturer_approval_imports():
    """List manufacturer approval import history."""
    try:
        limit = request.args.get('limit', 100, type=int)
        limit = max(1, min(limit, 500))
        approval_list_type = request.args.get('approval_list_type')
        approval_list_type = _normalize_list_type(approval_list_type) if approval_list_type else None
        return jsonify({'success': True, 'imports': _get_import_history(limit=limit, approval_list_type=approval_list_type)})
    except Exception as exc:
        logging.error('Error listing manufacturer approval imports: %s', exc)
        return jsonify({'success': False, 'error': str(exc)}), 500


@manufacturer_approvals_bp.route('/manufacturer-approvals/imports/<int:import_id>', methods=['GET'])
def get_manufacturer_approval_import(import_id):
    """Fetch a single import row with summary stats."""
    try:
        import_row = db_execute(
            '''
            SELECT
                id,
                source_file,
                source_files_json,
                source_file_count,
                imported_by,
                imported_at,
                row_count,
                approval_list_type,
                overwrite_existing
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
        logging.error('Error fetching manufacturer approval import %s: %s', import_id, exc)
        return jsonify({'success': False, 'error': str(exc)}), 500
