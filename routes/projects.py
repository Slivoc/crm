import os
import csv
import io
import json
from datetime import datetime
from flask import Blueprint, request, redirect, url_for, jsonify, current_app, render_template, send_from_directory, flash, session, Response
from werkzeug.utils import secure_filename
from db import db_cursor, execute as db_execute
from models import create_base_part_number, get_rfqs_for_project, insert_stage_update, get_stage_updates, insert_file_for_project_stage, insert_project, get_stage, get_stage_by_id, insert_project_stage, get_project_stages, generate_breadcrumbs, update_project, get_project_by_id, get_projects, insert_project_update, \
    get_project_updates, get_project_statuses, get_customers, get_salespeople, insert_file_for_project, link_file_to_project, get_files_for_project, get_file_by_id
from routes.auth import login_required, current_user
from backfill_project_parts_list_lines import run_backfill

projects_bp = Blueprint('projects', __name__)


@projects_bp.before_request
def require_login():
    if not current_user.is_authenticated:
        return redirect(url_for('auth.login', next=request.url))


def _using_postgres():
    return bool(os.getenv('DATABASE_URL', '').startswith(('postgres://', 'postgresql://')))


def _prepare_query(query):
    return query.replace('?', '%s') if _using_postgres() else query


def _manufacturer_name_normalized_sql(column_sql):
    if _using_postgres():
        cleaned_sql = f"REGEXP_REPLACE(LOWER(TRIM(COALESCE(NULLIF({column_sql}, ''), ''))), '[^a-z0-9 ]+', ' ', 'g')"
    else:
        cleaned_sql = f"LOWER(TRIM(COALESCE(NULLIF({column_sql}, ''), '')))"
        for old, new in (
            ('-', ' '),
            ('/', ' '),
            ('.', ' '),
            (',', ' '),
            ('(', ' '),
            (')', ' '),
            ('&', ' '),
            ("'", ' '),
        ):
            cleaned_sql = f"REPLACE({cleaned_sql}, '{old}', '{new}')"

    padded_sql = f"(' ' || {cleaned_sql} || ' ')"
    for stopword in (
        'ltd', 'limited', 'inc', 'llc', 'plc', 'corp', 'corporation',
        'co', 'company', 'gmbh', 'sa', 'bv', 'ag', 'srl', 'pte', 'group'
    ):
        padded_sql = f"REPLACE({padded_sql}, ' {stopword} ', ' ')"

    return f"REPLACE(TRIM({padded_sql}), ' ', '')"


def _execute_with_cursor(cur, query, params=None, fetch=None):
    cur.execute(_prepare_query(query), params or [])
    if fetch == 'one':
        return cur.fetchone()
    if fetch == 'all':
        return cur.fetchall()
    return cur


def _parse_usage_by_year(raw_value):
    if raw_value is None:
        return []
    if isinstance(raw_value, (list, tuple)):
        return list(raw_value)
    if isinstance(raw_value, (bytes, bytearray)):
        raw_value = raw_value.decode('utf-8', errors='ignore')
    if isinstance(raw_value, str):
        if not raw_value.strip():
            return []
        try:
            parsed = json.loads(raw_value)
        except json.JSONDecodeError:
            return []
        if isinstance(parsed, list):
            return parsed
        return []
    return []


def _coerce_usage_list(values):
    usage = []
    for value in values or []:
        if value is None or value == '':
            usage.append(None)
            continue
        try:
            number = float(value)
        except (TypeError, ValueError):
            usage.append(None)
            continue
        if number.is_integer():
            usage.append(int(number))
        else:
            usage.append(number)
    return usage


def _fetch_project_parts_list_rows(project_id):
    rows = db_execute(
        """
        WITH linked_project_lines AS (
            SELECT
                ppl.parts_list_id,
                ppl.parts_list_line_id,
                ROW_NUMBER() OVER (
                    PARTITION BY ppl.parts_list_line_id
                    ORDER BY COALESCE(ppl.date_modified, ppl.date_created) DESC, ppl.id DESC
                ) AS rn
            FROM project_parts_list_lines ppl
            WHERE ppl.project_id = ?
              AND ppl.parts_list_id IS NOT NULL
              AND ppl.parts_list_line_id IS NOT NULL
        ),
        linked_lines AS (
            SELECT
                pl.id AS parts_list_id,
                pl.name AS parts_list_name,
                pll.id AS parts_list_line_id,
                pll.line_number,
                pll.customer_part_number,
                pll.base_part_number,
                pll.description,
                pll.quantity AS requested_qty,
                pll.chosen_supplier_id,
                COALESCE(pll.parent_line_id, pll.id) AS quote_line_key
            FROM linked_project_lines lpl
            JOIN parts_lists pl ON pl.id = lpl.parts_list_id
            JOIN parts_list_lines pll ON pll.id = lpl.parts_list_line_id
            WHERE lpl.rn = 1
        ),
        latest_supplier_quotes AS (
            SELECT
                ranked.quote_line_key,
                ranked.supplier_quote_id,
                ranked.supplier_id,
                ranked.quote_reference,
                ranked.quote_date,
                ranked.currency_code,
                ranked.supplier_quote_line_id,
                ranked.quoted_part_number,
                ranked.manufacturer,
                ranked.quantity_quoted,
                ranked.unit_price,
                ranked.lead_time_days,
                ranked.condition_code,
                ranked.certifications,
                ranked.is_no_bid,
                ranked.line_notes
            FROM (
                SELECT
                    sql.parts_list_line_id AS quote_line_key,
                    sq.id AS supplier_quote_id,
                    sq.supplier_id,
                    sq.quote_reference,
                    sq.quote_date,
                    curr.currency_code,
                    sql.id AS supplier_quote_line_id,
                    sql.quoted_part_number,
                    sql.manufacturer,
                    sql.quantity_quoted,
                    sql.unit_price,
                    sql.lead_time_days,
                    sql.condition_code,
                    sql.certifications,
                    sql.is_no_bid,
                    sql.line_notes,
                    ROW_NUMBER() OVER (
                        PARTITION BY sql.parts_list_line_id
                        ORDER BY COALESCE(sql.date_modified, sql.date_created) DESC, sql.id DESC
                    ) AS rn
                FROM parts_list_supplier_quote_lines sql
                JOIN parts_list_supplier_quotes sq ON sq.id = sql.supplier_quote_id
                LEFT JOIN currencies curr ON curr.id = sq.currency_id
            ) ranked
            WHERE ranked.rn = 1
        ),
        latest_customer_quotes AS (
            SELECT
                ranked.parts_list_line_id,
                ranked.customer_quote_line_id,
                ranked.customer_quote_status,
                ranked.customer_quote_price_gbp,
                ranked.customer_quote_margin_percent,
                ranked.customer_quote_part_number,
                ranked.customer_quote_manufacturer,
                ranked.customer_quote_lead_days,
                ranked.customer_quote_condition,
                ranked.customer_quote_certs,
                ranked.customer_quote_no_bid,
                ranked.customer_quote_notes
            FROM (
                SELECT
                    cql.parts_list_line_id,
                    cql.id AS customer_quote_line_id,
                    cql.quoted_status AS customer_quote_status,
                    cql.quote_price_gbp AS customer_quote_price_gbp,
                    cql.margin_percent AS customer_quote_margin_percent,
                    cql.quoted_part_number AS customer_quote_part_number,
                    cql.manufacturer AS customer_quote_manufacturer,
                    cql.lead_days AS customer_quote_lead_days,
                    cql.standard_condition AS customer_quote_condition,
                    cql.standard_certs AS customer_quote_certs,
                    cql.is_no_bid AS customer_quote_no_bid,
                    cql.line_notes AS customer_quote_notes,
                    ROW_NUMBER() OVER (
                        PARTITION BY cql.parts_list_line_id
                        ORDER BY COALESCE(cql.date_modified, cql.date_created) DESC, cql.id DESC
                    ) AS rn
                FROM customer_quote_lines cql
            ) ranked
            WHERE ranked.rn = 1
        )
        SELECT
            ll.parts_list_id,
            ll.parts_list_name,
            ll.parts_list_line_id,
            ll.line_number,
            ll.customer_part_number,
            ll.base_part_number,
            ll.description,
            ll.requested_qty,
            lsq.supplier_quote_id,
            COALESCE(chosen_supplier.name, quote_supplier.name) AS supplier_name,
            lsq.quote_reference,
            lsq.quote_date,
            lsq.currency_code,
            lsq.supplier_quote_line_id,
            lsq.quoted_part_number,
            lsq.manufacturer,
            lsq.quantity_quoted,
            lsq.unit_price,
            lsq.lead_time_days,
            lsq.condition_code,
            lsq.certifications,
            lsq.is_no_bid,
            lsq.line_notes,
            lcq.customer_quote_line_id,
            lcq.customer_quote_status,
            lcq.customer_quote_price_gbp,
            lcq.customer_quote_margin_percent,
            lcq.customer_quote_part_number,
            lcq.customer_quote_manufacturer,
            lcq.customer_quote_lead_days,
            lcq.customer_quote_condition,
            lcq.customer_quote_certs,
            lcq.customer_quote_no_bid,
            lcq.customer_quote_notes
        FROM linked_lines ll
        LEFT JOIN latest_supplier_quotes lsq ON lsq.quote_line_key = ll.quote_line_key
        LEFT JOIN latest_customer_quotes lcq ON lcq.parts_list_line_id = ll.parts_list_line_id
        LEFT JOIN suppliers chosen_supplier ON chosen_supplier.id = ll.chosen_supplier_id
        LEFT JOIN suppliers quote_supplier ON quote_supplier.id = lsq.supplier_id
        ORDER BY ll.parts_list_id, ll.line_number, ll.parts_list_line_id
        """,
        (project_id,),
        fetch='all',
    ) or []
    return [dict(row) for row in rows]


def _qpl_supplier_mapping_table_exists():
    row = db_execute(
        """
        SELECT 1
        FROM information_schema.tables
        WHERE table_schema = current_schema()
          AND table_name = ?
        LIMIT 1
        """,
        ('qpl_manufacturer_supplier_mappings',),
        fetch='one',
    )
    return row is not None


def _fetch_project_qpl_mapped_rows(project_id):
    if not _qpl_supplier_mapping_table_exists():
        return []

    manufacturer_name_normalized_sql = _manufacturer_name_normalized_sql('ma.manufacturer_name')

    rows = db_execute(
        f"""
        WITH project_lines AS (
            SELECT
                pl.id AS parts_list_id,
                pl.name AS parts_list_name,
                pll.id AS parts_list_line_id,
                pll.line_number,
                pll.customer_part_number,
                pll.base_part_number,
                pll.description,
                pll.quantity AS requested_qty,
                UPPER(TRIM(pll.base_part_number)) AS normalized_base_part_number
            FROM parts_lists pl
            JOIN parts_list_lines pll ON pll.parts_list_id = pl.id
            WHERE pl.project_id = ?
              AND TRIM(COALESCE(pll.base_part_number, '')) <> ''
        ),
        qpl_mapped AS (
            SELECT DISTINCT
                UPPER(TRIM(COALESCE(NULLIF(ma.airbus_material_base, ''), NULLIF(ma.manufacturer_part_number_base, '')))) AS normalized_base_part_number,
                TRIM(ma.manufacturer_name) AS qpl_manufacturer_name,
                map.supplier_id,
                s.name AS mapped_supplier_name,
                s.contact_name AS mapped_supplier_contact_name,
                s.contact_email AS mapped_supplier_contact_email
            FROM manufacturer_approvals ma
            JOIN qpl_manufacturer_supplier_mappings map
                ON map.manufacturer_name_normalized = {manufacturer_name_normalized_sql}
            LEFT JOIN suppliers s ON s.id = map.supplier_id
            WHERE TRIM(COALESCE(ma.manufacturer_name, '')) <> ''
              AND TRIM(COALESCE(NULLIF(ma.airbus_material_base, ''), NULLIF(ma.manufacturer_part_number_base, ''))) <> ''
        )
        SELECT
            pl.parts_list_id,
            pl.parts_list_name,
            pl.parts_list_line_id,
            pl.line_number,
            pl.customer_part_number,
            pl.base_part_number,
            pl.description,
            pl.requested_qty,
            qm.qpl_manufacturer_name,
            qm.supplier_id AS mapped_supplier_id,
            qm.mapped_supplier_name,
            qm.mapped_supplier_contact_name,
            qm.mapped_supplier_contact_email
        FROM project_lines pl
        JOIN qpl_mapped qm ON qm.normalized_base_part_number = pl.normalized_base_part_number
        ORDER BY pl.parts_list_id, pl.line_number, pl.parts_list_line_id, qm.qpl_manufacturer_name
        """,
        (project_id,),
        fetch='all',
    ) or []
    return [dict(row) for row in rows]


def _fetch_project_parts_list_overview(project_id):
    rows = db_execute(
        """
        SELECT
            ppl.id AS project_line_id,
            ppl.line_number,
            ppl.customer_part_number,
            ppl.description,
            ppl.category,
            ppl.comment,
            ppl.line_type,
            ppl.total_quantity,
            ppl.usage_by_year,
            ppl.status,
            ppl.parts_list_id,
            ppl.parts_list_line_id,
            CASE
                WHEN ppl.parts_list_line_id IS NOT NULL
                     AND EXISTS (
                        SELECT 1
                        FROM customer_quote_lines cql
                        WHERE cql.parts_list_line_id = ppl.parts_list_line_id
                          AND cql.quoted_status = 'quoted'
                     )
                THEN 1 ELSE 0
            END AS is_quoted,
            CASE
                WHEN ppl.parts_list_line_id IS NOT NULL
                     AND EXISTS (
                        SELECT 1
                        FROM parts_list_lines pll_cost
                        WHERE pll_cost.id = ppl.parts_list_line_id
                          AND pll_cost.chosen_cost IS NOT NULL
                     )
                THEN 1 ELSE 0
            END AS is_costed,
            pl.name AS parts_list_name
        FROM project_parts_list_lines ppl
        LEFT JOIN parts_lists pl ON pl.id = ppl.parts_list_id
        WHERE ppl.project_id = ?
        ORDER BY ppl.line_number, ppl.id
        """,
        (project_id,),
        fetch='all',
    ) or []
    formatted = []
    for row in rows:
        data = dict(row)
        usage = _parse_usage_by_year(data.get('usage_by_year'))
        data['usage_by_year'] = usage
        data['usage_total'] = sum(value for value in usage if isinstance(value, (int, float)))
        # Default status for rows without the column yet
        if not data.get('status'):
            data['status'] = 'linked' if data.get('parts_list_id') else 'pending'
        data['is_quoted'] = bool(data.get('is_quoted'))
        data['is_costed'] = bool(data.get('is_costed'))
        formatted.append(data)
    return formatted


def _fetch_project_parts_list_status_overview(project_id):
    rows = db_execute(
        """
        SELECT
            ppl.id AS project_line_id,
            ppl.line_number,
            ppl.customer_part_number,
            ppl.status,
            ppl.parts_list_id,
            pl.name AS parts_list_name,
            CASE
                WHEN ppl.parts_list_line_id IS NOT NULL
                     AND EXISTS (
                        SELECT 1
                        FROM customer_quote_lines cql
                        WHERE cql.parts_list_line_id = ppl.parts_list_line_id
                          AND cql.quoted_status = 'quoted'
                     )
                THEN 1 ELSE 0
            END AS is_quoted,
            CASE
                WHEN ppl.parts_list_line_id IS NOT NULL
                     AND EXISTS (
                        SELECT 1
                        FROM parts_list_lines pll_cost
                        WHERE pll_cost.id = ppl.parts_list_line_id
                          AND pll_cost.chosen_cost IS NOT NULL
                     )
                THEN 1 ELSE 0
            END AS is_costed
        FROM project_parts_list_lines ppl
        LEFT JOIN parts_lists pl ON pl.id = ppl.parts_list_id
        WHERE ppl.project_id = ?
        ORDER BY ppl.line_number, ppl.id
        """,
        (project_id,),
        fetch='all',
    ) or []
    formatted = []
    for row in rows:
        data = dict(row)
        if not data.get('status'):
            data['status'] = 'linked' if data.get('parts_list_id') else 'pending'
        data['is_quoted'] = bool(data.get('is_quoted'))
        data['is_costed'] = bool(data.get('is_costed'))
        formatted.append(data)
    return formatted


def _fetch_project_parts_list_max_usage_years(project_id):
    rows = db_execute(
        """
        SELECT usage_by_year
        FROM project_parts_list_lines
        WHERE project_id = ?
        """,
        (project_id,),
        fetch='all',
    ) or []
    max_years = 1
    for row in rows:
        usage = _parse_usage_by_year(dict(row).get('usage_by_year'))
        max_years = max(max_years, len(usage))
    return max_years or 1


def _fetch_project_parts_lists_summary(project_id, limit=None):
    sql = """
        SELECT
            pl.id,
            pl.name,
            pl.notes,
            pl.date_created,
            pl.date_modified,
            pl.status_id,
            pls.name AS status_name,
            (SELECT COUNT(*)
             FROM parts_list_lines pll
             WHERE pll.parts_list_id = pl.id) AS line_count,
            (SELECT COUNT(*)
             FROM parts_list_lines pll
             WHERE pll.parts_list_id = pl.id
               AND pll.chosen_cost IS NOT NULL) AS costed_line_count,
            (SELECT COUNT(DISTINCT pll.id)
             FROM parts_list_lines pll
             LEFT JOIN customer_quote_lines cql ON cql.parts_list_line_id = pll.id
             WHERE pll.parts_list_id = pl.id
               AND cql.quoted_status = 'quoted') AS quoted_line_count
        FROM parts_lists pl
        LEFT JOIN parts_list_statuses pls ON pls.id = pl.status_id
        WHERE pl.project_id = ?
        ORDER BY pl.date_modified DESC, pl.date_created DESC
    """
    params = [project_id]
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)
    rows = db_execute(sql, params, fetch='all') or []
    return [dict(row) for row in rows]


@projects_bp.route('/<int:project_id>/parts-lists/<int:list_id>/comments', methods=['POST'])
def project_parts_list_update_comments(project_id, list_id):
    payload = request.get_json(force=True) or {}
    notes = payload.get('notes')

    if notes is not None and not isinstance(notes, str):
        return jsonify(success=False, message='Comments must be a string'), 400

    exists = db_execute(
        """
        SELECT 1
        FROM parts_lists
        WHERE id = ? AND project_id = ?
        """,
        (list_id, project_id),
        fetch='one',
    )
    if not exists:
        return jsonify(success=False, message='Parts list not found for project'), 404

    db_execute(
        """
        UPDATE parts_lists
        SET notes = ?, date_modified = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (notes, list_id),
        commit=True,
    )
    return jsonify(success=True)

@projects_bp.route('/new', methods=['POST'])
def create_project():
    try:
        customer_id = request.form['customer_id']
        salesperson_id = request.form['salesperson_id']
        name = request.form['name']
        description = request.form.get('description', '')  # Default to empty string if not provided
        status_id = request.form.get('status_id', 1)

        project_id = insert_project(customer_id, salesperson_id, name, description, status_id)
        return jsonify({'success': True, 'project_id': project_id})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@projects_bp.route('/<int:project_id>', methods=['GET'], endpoint='view_project')
def view_project(project_id):
    return redirect(url_for('projects.edit_project', project_id=project_id))


@projects_bp.route('/<int:project_id>/edit', methods=['GET', 'POST'])
def edit_project(project_id):
    project = get_project_by_id(project_id)  # Fetch project, including description
    statuses = get_project_statuses()
    updates = get_project_updates(project_id)
    salespeople = get_salespeople()
    customers = get_customers()

    if request.method == 'POST':
        try:
            name = request.form['name']
            description = request.form.get('description', '')
            customer_id = request.form['customer_id']
            salesperson_id = request.form.get('salesperson_id')
            status_id = request.form['status_id']

            # Update to match parameter order in models.py
            update_project(project_id, customer_id, salesperson_id, name, description, status_id)
            return redirect(url_for('projects.edit_project', project_id=project_id))
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)})

    stages = get_project_stages(project_id)
    recurrence_types = db_execute('SELECT * FROM recurrence_types', fetch='all') or []
    rendered_stages = ""
    for stage in stages:
        rendered_stages += render_stage(stage, recurrence_types, updates)

    breadcrumbs = generate_breadcrumbs(
        ('Edit Project #{}'.format(project_id), url_for('projects.edit_project', project_id=project_id))
    )

    project_rfqs = get_rfqs_for_project(project_id)
    project_parts_lists = _fetch_project_parts_lists_summary(project_id, limit=5)
    project_parts_lists_total = db_execute(
        "SELECT COUNT(*) AS list_count FROM parts_lists WHERE project_id = ?",
        (project_id,),
        fetch='one',
    ) or {}

    return render_template(
        'project_edit.html',
        project=project,
        statuses=statuses,
        updates=updates,
        files=get_files_for_project(project_id),
        breadcrumbs=breadcrumbs,
        stages=stages,
        salespeople=salespeople,
        customers=customers,
        recurrence_types=recurrence_types,
        rendered_stages=rendered_stages,
        get_project_stages=get_project_stages,
        project_rfqs=project_rfqs,  # Add this line to pass RFQs to template
        project_parts_lists=project_parts_lists,
        project_parts_lists_total=project_parts_lists_total.get('list_count', 0),
    )


@projects_bp.route('/<int:project_id>/parts-lists/report', methods=['GET'])
def project_parts_list_report(project_id):
    project = get_project_by_id(project_id)
    if not project:
        flash('Project not found', 'error')
        return redirect(url_for('projects.list_projects'))

    summary = db_execute(
        """
        SELECT
            COUNT(DISTINCT pl.id) AS list_count,
            COUNT(DISTINCT pll.id) AS line_count,
            COUNT(sql.id) AS quote_line_count
        FROM parts_lists pl
        JOIN parts_list_lines pll ON pll.parts_list_id = pl.id
        LEFT JOIN parts_list_supplier_quote_lines sql ON sql.parts_list_line_id = pll.id
        WHERE pl.project_id = ?
        """,
        (project_id,),
        fetch='one',
    ) or {}

    rows = _fetch_project_parts_list_rows(project_id)

    return render_template(
        'project_parts_list_report.html',
        project=project,
        summary=summary,
        rows=rows,
    )


@projects_bp.route('/<int:project_id>/parts-lists/report/qpl-mapped', methods=['GET'])
def project_parts_list_qpl_mapped_report(project_id):
    project = get_project_by_id(project_id)
    if not project:
        flash('Project not found', 'error')
        return redirect(url_for('projects.list_projects'))

    has_mapping_table = _qpl_supplier_mapping_table_exists()
    rows = _fetch_project_qpl_mapped_rows(project_id) if has_mapping_table else []

    summary = {
        'line_count': len({row.get('parts_list_line_id') for row in rows if row.get('parts_list_line_id') is not None}),
        'mapping_count': len(rows),
        'supplier_count': len({
            row.get('mapped_supplier_id')
            for row in rows
            if row.get('mapped_supplier_id') is not None
        }),
    }

    return render_template(
        'project_parts_list_qpl_report.html',
        project=project,
        has_mapping_table=has_mapping_table,
        summary=summary,
        rows=rows,
    )


@projects_bp.route('/<int:project_id>/parts-lists/overview', methods=['GET'])
def project_parts_list_overview(project_id):
    project = get_project_by_id(project_id)
    if not project:
        flash('Project not found', 'error')
        return redirect(url_for('projects.list_projects'))

    rows = _fetch_project_parts_list_status_overview(project_id)
    max_usage_years = _fetch_project_parts_list_max_usage_years(project_id)

    return render_template(
        'project_parts_list.html',
        project=project,
        rows=rows,
        max_usage_years=max_usage_years,
    )


@projects_bp.route('/<int:project_id>/project-parts-list/lines/overview-data', methods=['GET'])
def project_parts_list_overview_data(project_id):
    project = get_project_by_id(project_id)
    if not project:
        return jsonify(success=False, message='Project not found'), 404

    rows = _fetch_project_parts_list_overview(project_id)
    return jsonify(success=True, rows=rows)


@projects_bp.route('/<int:project_id>/parts-lists/all', methods=['GET'])
def project_parts_lists_all(project_id):
    project = get_project_by_id(project_id)
    if not project:
        flash('Project not found', 'error')
        return redirect(url_for('projects.list_projects'))

    parts_lists = _fetch_project_parts_lists_summary(project_id)

    return render_template(
        'project_parts_lists.html',
        project=project,
        parts_lists=parts_lists,
    )


@projects_bp.route('/<int:project_id>/parts-lists/supplier-quote-counts', methods=['GET'])
def project_parts_lists_supplier_quote_counts(project_id):
    """Get supplier quote line counts and requested line counts for all parts lists in a project (for lazy loading)."""
    try:
        # Count of supplier quote lines per parts list
        offer_counts = db_execute(
            """
            SELECT
                sq.parts_list_id,
                COUNT(sql.id) AS quote_line_count
            FROM parts_list_supplier_quotes sq
            JOIN parts_list_supplier_quote_lines sql ON sql.supplier_quote_id = sq.id
            JOIN parts_lists pl ON pl.id = sq.parts_list_id
            WHERE pl.project_id = ?
            GROUP BY sq.parts_list_id
            """,
            (project_id,),
            fetch='all'
        ) or []

        # Count of lines that have had emails sent (requested)
        requested_counts = db_execute(
            """
            SELECT
                pll.parts_list_id,
                COUNT(DISTINCT pll.id) AS requested_line_count
            FROM parts_list_lines pll
            JOIN parts_list_line_supplier_emails se ON se.parts_list_line_id = pll.id
            JOIN parts_lists pl ON pl.id = pll.parts_list_id
            WHERE pl.project_id = ?
            GROUP BY pll.parts_list_id
            """,
            (project_id,),
            fetch='all'
        ) or []

        offers = {row['parts_list_id']: row['quote_line_count'] for row in offer_counts}
        requested = {row['parts_list_id']: row['requested_line_count'] for row in requested_counts}
        return jsonify(success=True, counts=offers, requested=requested)
    except Exception as e:
        return jsonify(success=False, message=str(e)), 500


@projects_bp.route('/<int:project_id>/project-parts-list/backfill', methods=['POST'])
def project_parts_list_backfill(project_id):
    project = get_project_by_id(project_id)
    if not project:
        return jsonify(success=False, message='Project not found'), 404

    os.makedirs('logs', exist_ok=True)
    ambiguous_csv = os.path.join(
        'logs',
        f'backfill_project_parts_list_lines_project_{project_id}.csv'
    )

    try:
        result = run_backfill(
            project_id=project_id,
            dry_run=False,
            import_lines=True,
            ambiguous_csv=ambiguous_csv,
            verbose=False,
        )
    except Exception as exc:
        current_app.logger.exception("Backfill failed for project %s", project_id)
        return jsonify(success=False, message=str(exc)), 500

    return jsonify(
        success=True,
        inserted=result.get('inserted', 0),
        linked=result.get('linked', 0),
        ambiguous=result.get('ambiguous', 0),
        unmatched=result.get('unmatched', 0),
        ambiguous_csv=result.get('ambiguous_csv', ''),
    )


@projects_bp.route('/<int:project_id>/parts-lists/import', methods=['GET'])
def project_parts_list_import(project_id):
    project = get_project_by_id(project_id)
    if not project:
        flash('Project not found', 'error')
        return redirect(url_for('projects.list_projects'))

    max_usage_years = _fetch_project_parts_list_max_usage_years(project_id)
    if max_usage_years < 5:
        max_usage_years = 5

    return render_template(
        'project_parts_list_import.html',
        project=project,
        max_usage_years=max_usage_years,
        edit_mode=bool(request.args.get('edit')),
    )


@projects_bp.route('/<int:project_id>/project-parts-list/lines', methods=['POST'])
def project_parts_list_add_lines(project_id):
    data = request.get_json(force=True) or {}
    raw_lines = data.get('lines') or []
    if isinstance(raw_lines, dict):
        raw_lines = [raw_lines]

    project = get_project_by_id(project_id)
    if not project:
        return jsonify(success=False, message='Project not found'), 404

    cleaned_lines = []
    for entry in raw_lines:
        if not isinstance(entry, dict):
            continue
        customer_part_number = (entry.get('customer_part_number') or '').strip()
        if not customer_part_number:
            continue
        cleaned_lines.append({
            'customer_part_number': customer_part_number,
            'description': (entry.get('description') or '').strip() or None,
            'category': (entry.get('category') or '').strip() or None,
            'comment': (entry.get('comment') or '').strip() or None,
            'line_type': (entry.get('line_type') or 'normal').strip() or 'normal',
            'total_quantity': entry.get('total_quantity'),
            'usage_by_year': _coerce_usage_list(entry.get('usage_by_year') or []),
        })

    if not cleaned_lines:
        return jsonify(success=False, message='No valid lines provided'), 400

    with db_cursor(commit=True) as cur:
        max_row = _execute_with_cursor(
            cur,
            """
            SELECT COALESCE(MAX(line_number), 0) AS max_line
            FROM project_parts_list_lines
            WHERE project_id = ?
            """,
            (project_id,),
            fetch='one',
        ) or {}
        next_line = max_row.get('max_line') or 0

        for line in cleaned_lines:
            total_quantity = line['total_quantity']
            if total_quantity in ('', None):
                total_quantity = None
            else:
                try:
                    total_quantity = int(float(total_quantity))
                except (TypeError, ValueError):
                    total_quantity = None

            next_line += 1
            _execute_with_cursor(
                cur,
                """
                INSERT INTO project_parts_list_lines
                    (project_id, line_number, customer_part_number, description, category, comment,
                     line_type, total_quantity, usage_by_year, date_created, date_modified)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """,
                (
                    project_id,
                    next_line,
                    line['customer_part_number'],
                    line['description'],
                    line['category'],
                    line['comment'],
                    line['line_type'],
                    total_quantity,
                    json.dumps(line['usage_by_year']) if line['usage_by_year'] else None,
                ),
            )

    return jsonify(success=True)


@projects_bp.route('/<int:project_id>/project-parts-list/lines/bulk-update', methods=['POST'])
def project_parts_list_bulk_update(project_id):
    data = request.get_json(force=True) or {}
    raw_lines = data.get('lines') or []
    if isinstance(raw_lines, dict):
        raw_lines = [raw_lines]

    project = get_project_by_id(project_id)
    if not project:
        return jsonify(success=False, message='Project not found'), 404

    updates = []
    inserts = []
    for entry in raw_lines:
        if not isinstance(entry, dict):
            continue
        customer_part_number = (entry.get('customer_part_number') or '').strip()
        line_id = entry.get('line_id')
        usage_by_year = entry.get('usage_by_year')
        if usage_by_year is None:
            usage_by_year = entry.get('usage_by_years')
        usage_by_year = _coerce_usage_list(usage_by_year or [])
        payload = {
            'customer_part_number': customer_part_number,
            'description': (entry.get('description') or '').strip() or None,
            'category': (entry.get('category') or '').strip() or None,
            'comment': (entry.get('comment') or '').strip() or None,
            'line_type': (entry.get('line_type') or 'normal').strip() or 'normal',
            'total_quantity': entry.get('total_quantity'),
            'usage_by_year': usage_by_year,
        }

        if line_id:
            try:
                line_id = int(line_id)
            except (TypeError, ValueError):
                continue
            if not customer_part_number:
                continue
            payload['line_id'] = line_id
            updates.append(payload)
        else:
            if not customer_part_number:
                continue
            inserts.append(payload)

    if not updates and not inserts:
        return jsonify(success=False, message='No valid lines provided'), 400

    updated_count = 0
    inserted_count = 0

    with db_cursor(commit=True) as cur:
        if inserts:
            max_row = _execute_with_cursor(
                cur,
                """
                SELECT COALESCE(MAX(line_number), 0) AS max_line
                FROM project_parts_list_lines
                WHERE project_id = ?
                """,
                (project_id,),
                fetch='one',
            ) or {}
            next_line = max_row.get('max_line') or 0

            for line in inserts:
                total_quantity = line['total_quantity']
                if total_quantity in ('', None):
                    total_quantity = None
                else:
                    try:
                        total_quantity = int(float(total_quantity))
                    except (TypeError, ValueError):
                        total_quantity = None

                next_line += 1
                _execute_with_cursor(
                    cur,
                    """
                    INSERT INTO project_parts_list_lines
                        (project_id, line_number, customer_part_number, description, category, comment,
                         line_type, total_quantity, usage_by_year, date_created, date_modified)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                    """,
                    (
                        project_id,
                        next_line,
                        line['customer_part_number'],
                        line['description'],
                        line['category'],
                        line['comment'],
                        line['line_type'],
                        total_quantity,
                        json.dumps(line['usage_by_year']) if line['usage_by_year'] else None,
                    ),
                )
                inserted_count += 1

        for line in updates:
            total_quantity = line['total_quantity']
            if total_quantity in ('', None):
                total_quantity = None
            else:
                try:
                    total_quantity = int(float(total_quantity))
                except (TypeError, ValueError):
                    total_quantity = None

            _execute_with_cursor(
                cur,
                """
                UPDATE project_parts_list_lines
                SET
                    customer_part_number = ?,
                    description = ?,
                    category = ?,
                    comment = ?,
                    line_type = ?,
                    total_quantity = ?,
                    usage_by_year = ?,
                    date_modified = CURRENT_TIMESTAMP
                WHERE id = ?
                  AND project_id = ?
                """,
                (
                    line['customer_part_number'],
                    line['description'],
                    line['category'],
                    line['comment'],
                    line['line_type'],
                    total_quantity,
                    json.dumps(line['usage_by_year']) if line['usage_by_year'] else None,
                    line['line_id'],
                    project_id,
                ),
            )
            updated_count += 1

    return jsonify(success=True, updated=updated_count, inserted=inserted_count)


@projects_bp.route('/<int:project_id>/parts-lists/create-from-lines', methods=['POST'])
def project_parts_list_create_from_lines(project_id):
    data = request.get_json(force=True) or {}
    list_name = (data.get('name') or '').strip()
    raw_line_ids = data.get('line_ids') or []
    quantity_source = data.get('quantity_source', 'total')  # 'total', 'sum', or 'year_N'

    if not list_name:
        return jsonify(success=False, message='List name is required'), 400

    line_ids = []
    for line_id in raw_line_ids:
        try:
            line_ids.append(int(line_id))
        except (TypeError, ValueError):
            continue

    if not line_ids:
        return jsonify(success=False, message='Select at least one line'), 400

    project = get_project_by_id(project_id)
    if not project:
        return jsonify(success=False, message='Project not found'), 404

    placeholders = ','.join(['?'] * len(line_ids))

    with db_cursor(commit=True) as cur:
        lines = _execute_with_cursor(
            cur,
            f"""
            SELECT
                ppl.id,
                ppl.customer_part_number,
                ppl.description,
                ppl.category,
                ppl.comment,
                ppl.line_type,
                ppl.total_quantity,
                ppl.usage_by_year
            FROM project_parts_list_lines ppl
            WHERE ppl.project_id = ?
              AND ppl.id IN ({placeholders})
            ORDER BY ppl.line_number, ppl.id
            """,
            [project_id, *line_ids],
            fetch='all',
        ) or []

        if not lines:
            return jsonify(success=False, message='No matching lines found'), 400

        header_row = _execute_with_cursor(
            cur,
            """
            INSERT INTO parts_lists
                (name, customer_id, salesperson_id, status_id, notes, project_id, date_created, date_modified)
            VALUES (?, ?, ?, 1, '', ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            RETURNING id
            """,
            (list_name, project.get('customer_id'), project.get('salesperson_id'), project_id),
            fetch='one',
        )
        parts_list_id = header_row['id'] if header_row else getattr(cur, 'lastrowid', None)

        for index, line in enumerate(lines, start=1):
            usage_values = _parse_usage_by_year(line.get('usage_by_year'))
            usage_total = sum(value for value in usage_values if isinstance(value, (int, float)))
            base_part_number = create_base_part_number(line['customer_part_number'])

            # Determine quantity based on source selection
            chosen_quantity = None
            if quantity_source == 'total':
                chosen_quantity = line.get('total_quantity')
                if chosen_quantity in ('', None):
                    chosen_quantity = None
                else:
                    try:
                        chosen_quantity = int(float(chosen_quantity))
                    except (TypeError, ValueError):
                        chosen_quantity = None
                # Fallback to sum if total is empty
                if chosen_quantity is None and usage_values:
                    chosen_quantity = int(usage_total)
            elif quantity_source == 'sum':
                chosen_quantity = int(usage_total) if usage_values else None
            elif quantity_source.startswith('year_'):
                try:
                    year_index = int(quantity_source.replace('year_', '')) - 1
                    if usage_values and 0 <= year_index < len(usage_values):
                        val = usage_values[year_index]
                        if val is not None:
                            chosen_quantity = int(float(val))
                except (ValueError, TypeError):
                    pass

            if chosen_quantity is None:
                chosen_quantity = 1

            parts_list_line_id = None
            if _using_postgres():
                insert_row = _execute_with_cursor(
                    cur,
                    """
                    INSERT INTO parts_list_lines
                        (parts_list_id, line_number, customer_part_number, base_part_number, description, category,
                         quantity, customer_notes, internal_notes, line_type)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    RETURNING id
                    """,
                    (
                        parts_list_id,
                        index,
                        line['customer_part_number'],
                        base_part_number,
                        line.get('description'),
                        line.get('category'),
                        chosen_quantity,
                        line.get('comment'),
                        None,
                        line['line_type'] or 'normal',
                    ),
                    fetch='one',
                )
                parts_list_line_id = insert_row['id'] if insert_row else None
            else:
                _execute_with_cursor(
                    cur,
                    """
                    INSERT INTO parts_list_lines
                        (parts_list_id, line_number, customer_part_number, base_part_number, description, category,
                         quantity, customer_notes, internal_notes, line_type)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        parts_list_id,
                        index,
                        line['customer_part_number'],
                        base_part_number,
                        line.get('description'),
                        line.get('category'),
                        chosen_quantity,
                        line.get('comment'),
                        None,
                        line['line_type'] or 'normal',
                    ),
                )
                parts_list_line_id = getattr(cur, 'lastrowid', None)

            _execute_with_cursor(
                cur,
                """
                UPDATE project_parts_list_lines
                SET parts_list_id = ?, parts_list_line_id = ?, status = 'linked', date_modified = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (parts_list_id, parts_list_line_id, line['id']),
            )

    return jsonify(
        success=True,
        parts_list_id=parts_list_id,
        redirect=url_for('parts_list.view_parts_list', list_id=parts_list_id),
    )


@projects_bp.route('/<int:project_id>/project-parts-list/lines/<int:line_id>', methods=['PATCH'])
def project_parts_list_update_line(project_id, line_id):
    data = request.get_json(force=True) or {}

    project = get_project_by_id(project_id)
    if not project:
        return jsonify(success=False, message='Project not found'), 404

    allowed_fields = {'comment', 'status', 'category'}
    updates = {}
    for field in allowed_fields:
        if field in data:
            value = data[field]
            if field == 'status' and value not in ('pending', 'linked', 'no_bid', 'ignore'):
                return jsonify(success=False, message=f'Invalid status: {value}'), 400
            updates[field] = value.strip() if isinstance(value, str) else value

    if not updates:
        return jsonify(success=False, message='No valid fields to update'), 400

    set_clauses = ', '.join([f'{field} = ?' for field in updates.keys()])
    values = list(updates.values())

    with db_cursor(commit=True) as cur:
        _execute_with_cursor(
            cur,
            f"""
            UPDATE project_parts_list_lines
            SET {set_clauses}, date_modified = CURRENT_TIMESTAMP
            WHERE id = ? AND project_id = ?
            """,
            [*values, line_id, project_id],
        )

    return jsonify(success=True)


@projects_bp.route('/<int:project_id>/project-parts-list/lines/bulk-status', methods=['POST'])
def project_parts_list_bulk_status(project_id):
    data = request.get_json(force=True) or {}
    raw_line_ids = data.get('line_ids') or []
    new_status = data.get('status', '')

    if new_status not in ('pending', 'no_bid', 'ignore'):
        return jsonify(success=False, message='Invalid status'), 400

    line_ids = []
    for line_id in raw_line_ids:
        try:
            line_ids.append(int(line_id))
        except (TypeError, ValueError):
            continue

    if not line_ids:
        return jsonify(success=False, message='No lines selected'), 400

    project = get_project_by_id(project_id)
    if not project:
        return jsonify(success=False, message='Project not found'), 404

    placeholders = ','.join(['?'] * len(line_ids))

    with db_cursor(commit=True) as cur:
        _execute_with_cursor(
            cur,
            f"""
            UPDATE project_parts_list_lines
            SET status = ?, date_modified = CURRENT_TIMESTAMP
            WHERE project_id = ? AND id IN ({placeholders}) AND status != 'linked'
            """,
            [new_status, project_id, *line_ids],
        )

    return jsonify(success=True)


@projects_bp.route('/<int:project_id>/project-parts-list/lines/bulk-category', methods=['POST'])
def project_parts_list_bulk_category(project_id):
    data = request.get_json(force=True) or {}
    raw_line_ids = data.get('line_ids') or []
    new_category = (data.get('category') or '').strip()

    line_ids = []
    for line_id in raw_line_ids:
        try:
            line_ids.append(int(line_id))
        except (TypeError, ValueError):
            continue

    if not line_ids:
        return jsonify(success=False, message='No lines selected'), 400

    project = get_project_by_id(project_id)
    if not project:
        return jsonify(success=False, message='Project not found'), 404

    placeholders = ','.join(['?'] * len(line_ids))

    with db_cursor(commit=True) as cur:
        _execute_with_cursor(
            cur,
            f"""
            UPDATE project_parts_list_lines
            SET category = ?, date_modified = CURRENT_TIMESTAMP
            WHERE project_id = ? AND id IN ({placeholders})
            """,
            [new_category or None, project_id, *line_ids],
        )

    return jsonify(success=True)


@projects_bp.route('/<int:project_id>/parts-lists/report.csv', methods=['GET'])
def project_parts_list_report_csv(project_id):
    project = get_project_by_id(project_id)
    if not project:
        return jsonify({'success': False, 'error': 'Project not found'}), 404

    rows = _fetch_project_parts_list_rows(project_id)

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        'parts_list_id',
        'parts_list_name',
        'parts_list_line_id',
        'line_number',
        'customer_part_number',
        'base_part_number',
        'requested_qty',
        'supplier_quote_id',
        'supplier_name',
        'quote_reference',
        'quote_date',
        'currency_code',
        'supplier_quote_line_id',
        'quoted_part_number',
        'manufacturer',
        'quantity_quoted',
        'unit_price',
        'lead_time_days',
        'condition_code',
        'certifications',
        'is_no_bid',
        'line_notes',
        'customer_quote_line_id',
        'customer_quote_status',
        'customer_quote_price_gbp',
        'customer_quote_margin_percent',
        'customer_quote_part_number',
        'customer_quote_manufacturer',
        'customer_quote_lead_days',
        'customer_quote_condition',
        'customer_quote_certs',
        'customer_quote_no_bid',
        'customer_quote_notes',
    ])

    for row in rows:
        writer.writerow([
            row.get('parts_list_id'),
            row.get('parts_list_name'),
            row.get('parts_list_line_id'),
            row.get('line_number'),
            row.get('customer_part_number'),
            row.get('base_part_number'),
            row.get('requested_qty'),
            row.get('supplier_quote_id'),
            row.get('supplier_name'),
            row.get('quote_reference'),
            row.get('quote_date'),
            row.get('currency_code'),
            row.get('supplier_quote_line_id'),
            row.get('quoted_part_number'),
            row.get('manufacturer'),
            row.get('quantity_quoted'),
            row.get('unit_price'),
            row.get('lead_time_days'),
            row.get('condition_code'),
            row.get('certifications'),
            row.get('is_no_bid'),
            row.get('line_notes'),
            row.get('customer_quote_line_id'),
            row.get('customer_quote_status'),
            row.get('customer_quote_price_gbp'),
            row.get('customer_quote_margin_percent'),
            row.get('customer_quote_part_number'),
            row.get('customer_quote_manufacturer'),
            row.get('customer_quote_lead_days'),
            row.get('customer_quote_condition'),
            row.get('customer_quote_certs'),
            row.get('customer_quote_no_bid'),
            row.get('customer_quote_notes'),
        ])

    filename = f"project_{project_id}_parts_list_report.csv"
    return Response(
        output.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename={filename}'},
    )


@projects_bp.route('/<int:project_id>/update', methods=['POST'])
def update_project_route(project_id):
    print("Received form data:", dict(request.form))
    try:
        # Get form data
        name = request.form.get('name')
        description = request.form.get('description', '').strip()
        customer_id = request.form.get('customer_id')
        salesperson_id = request.form.get('salesperson_id') or None
        status_id = request.form.get('status_id')

        # Validate required fields
        if not all([name, customer_id, status_id]):
            flash('Missing required fields', 'error')
            return redirect(url_for('projects.edit_project', project_id=project_id))

        # Validate IDs are integers
        try:
            customer_id = int(customer_id)
            status_id = int(status_id)
            if salesperson_id:
                salesperson_id = int(salesperson_id)
        except ValueError:
            flash('Invalid ID format', 'error')
            return redirect(url_for('projects.edit_project', project_id=project_id))

        # Update project with parameters in correct order
        update_project(project_id, customer_id, salesperson_id, name, description, status_id)
        flash('Project updated successfully', 'success')
        return redirect(url_for('projects.edit_project', project_id=project_id))

    except Exception as e:
        flash(f'Error updating project: {str(e)}', 'error')
        return redirect(url_for('projects.edit_project', project_id=project_id))


@projects_bp.route('/<int:project_id>/add_update', methods=['POST'])
@login_required
def add_project_update(project_id):
    comment = request.form.get('comment', '').strip()
    if not comment:
        return jsonify({"success": False, "error": "Comment is required."}), 400

    # Use the method to get the salesperson ID
    salesperson_id = current_user.get_salesperson_id()
    if not salesperson_id:
        return jsonify({"success": False, "error": "No salesperson associated with this user."}), 400

    # Insert the update into the database
    insert_project_update(project_id, salesperson_id, comment)

    return jsonify({"success": True})



@projects_bp.route('/', methods=['GET', 'POST'])
def list_projects():
    if request.method == 'POST':
        customer_id = request.form['customer_id']
        salesperson_id = request.form['salesperson_id']
        name = request.form['name']
        description = request.form.get('description')
        status_id = request.form.get('status_id', 1)

        project_id = insert_project(customer_id, salesperson_id, name, description, status_id)
        return redirect(url_for('projects.list_projects'))

    show_all = request.args.get('show_all', '0') == '1'

    # Filter projects based on show_all parameter
    if show_all:
        projects = get_projects()
    else:
        projects = get_projects(salesperson_id=current_user.get_salesperson_id())

    active_project = None
    if 'active_project_id' in session:
        active_project = get_project_by_id(session['active_project_id'])

    for project in projects:
        if project['next_stage_deadline']:
            try:
                from datetime import datetime
                deadline = datetime.strptime(project['next_stage_deadline'], '%Y-%m-%d')
                project['next_stage_deadline_formatted'] = deadline.strftime('%Y-%m-%d')
            except (ValueError, TypeError):
                project['next_stage_deadline_formatted'] = project['next_stage_deadline']
        else:
            project['next_stage_deadline_formatted'] = None

        project['estimated_value_formatted'] = f"${float(project['estimated_value']):,.2f}" if project[
            'estimated_value'] else None

    customers = get_customers()
    salespeople = get_salespeople()
    statuses = get_project_statuses()

    return render_template('projects.html',
                           projects=projects,
                           customers=customers,
                           salespeople=salespeople,
                           statuses=statuses,
                           project=active_project,
                           show_all=show_all,
                           get_project_stages=get_project_stages)


ALLOWED_EXTENSIONS = {'pdf', 'doc', 'docx', 'jpg', 'png', 'xlsx'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@projects_bp.route('/<int:project_id>/upload', methods=['POST'])
def upload_file(project_id):
    if 'file' not in request.files:
        return jsonify({'success': False, 'error': 'No file part in request'}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({'success': False, 'error': 'No selected file'}), 400

    if file and allowed_file(file.filename):
        try:
            filename = secure_filename(file.filename)
            upload_folder = os.path.join(current_app.config['UPLOAD_FOLDER'], 'projects')
            if not os.path.exists(upload_folder):
                os.makedirs(upload_folder)

            filepath = os.path.join(upload_folder, filename)
            file.save(filepath)

            file_id = insert_file_for_project(filename, filepath, datetime.now().date())
            link_file_to_project(project_id, file_id)

            # Files are already dictionaries from dict_from_row
            files = get_files_for_project(project_id)
            return jsonify({
                'success': True,
                'files': files
            })

        except Exception as e:
            return jsonify({'success': False, 'error': str(e)}), 500
    else:
        return jsonify({'success': False, 'error': 'File type not allowed'}), 400

@projects_bp.route('/download/<int:file_id>', methods=['GET'])
def download_file(file_id):
    # Get the file from the database using the file_id
    file = get_file_by_id(file_id)
    if file:
        return send_from_directory(directory=os.path.dirname(file['filepath']),
                                   filename=os.path.basename(file['filepath']), as_attachment=True)
    else:
        return jsonify({'error': 'File not found'}), 404


@projects_bp.route('/<int:project_id>/add_stage', methods=['POST'])
def add_stage(project_id):
    data = request.get_json()  # Parse JSON data for AJAX request

    # Add debugging
    print(f"Received data: {data}")

    name = data.get('name', 'New Stage')
    status_id = data.get('status_id', 1)
    parent_stage_id = data.get('parent_stage_id')

    # More debugging
    print(f"name: {name}")
    print(f"status_id: {status_id}")
    print(f"parent_stage_id: {parent_stage_id} (type: {type(parent_stage_id)})")

    due_date = None

    new_stage_id = insert_project_stage(project_id, name, None, parent_stage_id, status_id, due_date)
    print(f"Created stage with ID: {new_stage_id}")

    return jsonify({'success': True, 'new_stage_id': new_stage_id})

@projects_bp.route('/<int:project_id>/edit_stage/<int:stage_id>', methods=['GET', 'POST'])
def edit_stage(stage_id, project_id):
    if request.method == 'GET':
        stage = get_stage(stage_id)
        return jsonify({
            'name': stage['name'],
            'description': stage['description'],
            'files': stage['files'],
            'updates': stage['updates']
        })

    # Handle POST
    data = request.json

    # Get current stage data
    current_stage = get_stage(stage_id)

    # Update only the fields that were sent
    name = data.get('name', current_stage['name'])
    description = data.get('description', current_stage['description'])
    status_id = data.get('status_id')

    update_project_stage(stage_id, name, description, status_id=status_id)
    return jsonify({'success': True})


def update_project_stage(stage_id, name, description, status_id=None):
    try:
        update_fields = []
        params = []

        if name is not None:
            update_fields.append("name = ?")
            params.append(name)
        if description is not None:
            update_fields.append("description = ?")
            params.append(description)
        if status_id is not None:
            update_fields.append("status_id = ?")
            params.append(status_id)

        if not update_fields:
            return True

        params.append(stage_id)

        query = f"""
            UPDATE project_stages 
            SET {', '.join(update_fields)}
            WHERE id = ?
        """

        db_execute(query, params, commit=True)
        return True
    except Exception as e:
        print(f"Error updating stage: {e}")
        return False

@projects_bp.route('/update_stage_recurrence/<int:stage_id>', methods=['POST'])
def update_stage_recurrence(stage_id):
    data = request.get_json()
    recurrence_id = data.get('recurrence_id', None)

    # Update the recurrence_id in the database
    db_execute('UPDATE project_stages SET recurrence_id = ? WHERE id = ?', (recurrence_id, stage_id), commit=True)

    return jsonify({"success": True})

@projects_bp.route('/<int:stage_id>/update_stage_description', methods=['POST'])
def update_stage_description(stage_id):
    data = request.get_json()
    description = data.get('description')

    # Update the stage description in the project_stages table
    db_execute('UPDATE project_stages SET description = ? WHERE id = ?', (description, stage_id), commit=True)

    return jsonify({"success": True})




def generate_stage_breadcrumbs(stage, project_id):
    breadcrumbs = []
    current_stage = stage

    # Traverse up through parent stages to build the breadcrumb trail
    while current_stage:
        # Insert the current stage's name and URL at the beginning of the breadcrumbs list
        breadcrumbs.insert(0, (
        current_stage['name'], url_for('projects.edit_stage', project_id=project_id, stage_id=current_stage['id'])))

        # Fetch the parent stage using parent_stage_id
        parent_stage_id = current_stage.get('parent_stage_id')
        if parent_stage_id:
            current_stage = get_stage_by_id(parent_stage_id)  # Fetch the parent stage by its ID
        else:
            current_stage = None  # No parent stage

    print(f"Final breadcrumbs: {breadcrumbs}")  # Debug final breadcrumbs
    return breadcrumbs

@projects_bp.route('/<int:stage_id>/update_stage_name', methods=['POST'])
def update_stage_name(stage_id):
    data = request.get_json()
    new_name = data.get('name')

    # Ensure the new name is valid
    if not new_name or new_name.strip() == "":
        return jsonify({"success": False, "error": "Stage name cannot be empty."}), 400

    # Update the stage name in the project_stages table
    db_execute('UPDATE project_stages SET name = ? WHERE id = ?', (new_name, stage_id), commit=True)

    return jsonify({"success": True})

@projects_bp.route('/<int:substage_id>/update_substage_name', methods=['POST'])
def update_substage_name(substage_id):
    data = request.get_json()
    new_name = data.get('name')

    # Ensure the new name is valid
    if not new_name or new_name.strip() == "":
        return jsonify({"success": False, "error": "Substage name cannot be empty."}), 400

    # Update the substage name in the project_stages table (assuming substages are also stored in the same table)
    db_execute('UPDATE project_stages SET name = ? WHERE id = ?', (new_name, substage_id), commit=True)

    return jsonify({"success": True})


@projects_bp.route('/<int:parent_stage_id>/add_substage', methods=['POST'])
def add_substage(parent_stage_id):
    try:
        # Parse the JSON data from the request
        data = request.get_json()
        name = data.get('name')
        status_id = data.get('status_id', 1)  # Default to 1 (incomplete) if not provided
        project_id = data.get('project_id')  # Expect the project_id in the request

        if not name or not project_id:
            return jsonify({"success": False, "error": "Name and project_id are required"}), 400

        # Insert the new sub-stage into the database
        row = db_execute(
            '''
            INSERT INTO project_stages (name, parent_stage_id, status_id, project_id)
            VALUES (?, ?, ?, ?)
            RETURNING id
            ''',
            (name, parent_stage_id, status_id, project_id),
            fetch='one',
            commit=True,
        )

        new_substage_id = row.get('id', list(row.values())[0]) if row else None
        if new_substage_id is None:
            raise RuntimeError("Failed to insert sub-stage")

        return jsonify({"success": True, "new_substage_id": new_substage_id})

    except Exception as e:
        print(f"Error while adding sub-stage: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


def render_stage(stage, recurrence_types, updates):
    # Determine if the checkbox should be checked based on the status
    checked = 'checked' if stage['status_id'] == 2 else ''

    # Handle recurrence dropdown options
    if not recurrence_types:
        recurrence_options = '<option value="" disabled>No recurrence types available</option>'
    else:
        recurrence_id = stage.get('recurrence_id', None)
        recurrence_options = ''.join([
            f'<option value="{recurrence["id"]}" {"selected" if recurrence["id"] == recurrence_id else ""}>{recurrence["name"]}</option>'
            for recurrence in recurrence_types
        ])

    # Create the updates section
    update_items = ""
    for update in updates:
        if update['stage_id'] == stage['id']:
            update_items += f"""
            <li class="list-group-item">
                {update['comment']} 
                <br> <small class="text-muted">Posted on: {update['date_created']}</small>
            </li>
            """

    if not update_items:
        update_items = '<li class="list-group-item text-muted">No updates available.</li>'

    # Render the stage accordion item using Bootstrap grid
    rendered = f"""
    <div class="accordion-item" id="stage-{stage['id']}">
        <h2 class="accordion-header" id="heading-stage-{stage['id']}">
            <button class="accordion-button collapsed" type="button" data-bs-toggle="collapse" data-bs-target="#collapse-stage-{stage['id']}" aria-expanded="false" aria-controls="collapse-stage-{stage['id']}">
                <input type="checkbox" class="form-check-input me-2" id="stageCheck{stage['id']}" {checked} onchange="toggleStageStatus('{stage['id']}')">
                <span class="editable-text" contenteditable="true" id="stageName{stage['id']}" oninput="updateStageName('{stage['id']}')">
                    {stage['name']}
                </span>
                <i class="bi bi-trash ms-2" style="cursor: pointer;" onclick="deleteStage('{stage['id']}')"></i>
            </button>
        </h2>
        <div id="collapse-stage-{stage['id']}" class="accordion-collapse collapse" aria-labelledby="heading-stage-{stage['id']}">
            <div class="accordion-body">
                <div class="row">
                    <!-- Left column: Stage details -->
                    <div class="col-md-6">
                        <div class="mb-3">
                            <label for="description-{stage['id']}" class="form-label">Description:</label>
                            <textarea class="form-control" id="description-{stage['id']}" rows="3" oninput="updateStageDescription('{stage['id']}')">{stage['description']}</textarea>
                        </div>
                        <div class="mb-3">
                            <label for="recurrence-{stage['id']}" class="form-label">Recurrence:</label>
                            <select class="form-select" id="recurrence-{stage['id']}" onchange="updateStageRecurrence('{stage['id']}')">
                                {recurrence_options}
                            </select>
                        </div>
                    </div>

                    <!-- Right column: Updates section -->
                    <div class="col-md-6">
                        <h6>Updates</h6>
                        <ul class="list-group">
                            {update_items}
                        </ul>
                        <form action="/projects/{stage['id']}/add_update" method="post" class="mt-3">
                            <textarea class="form-control mb-2" name="comment" placeholder="Add new update" rows="2" required></textarea>
                            <button type="submit" class="btn btn-primary btn-sm">Add Update</button>
                        </form>
                    </div>
                </div>
                <div class="accordion mt-3" id="substageAccordion-{stage['id']}">
    """

    # Recursively render substages, if any
    for substage in stage.get('substages', []):
        rendered += render_stage(substage, recurrence_types, updates)

    # Close the accordion body and add the "Add Sub-Stage" button
    rendered += f"""
                </div>
                <button class="btn btn-sm btn-primary mt-3" onclick="showNewSubStageForm('{stage['id']}')">+</button>
            </div>
        </div>
    </div>
    """

    return rendered


@projects_bp.route('/<int:stage_id>/update_stage_status', methods=['POST'])
def update_stage_status(stage_id):
    data = request.get_json()
    new_status = data.get('status')

    # Update the validation to accept 1 (incomplete), 2 (complete), and 3 (deleted)
    if new_status not in [1, 2, 3]:
        return jsonify({"success": False, "error": "Invalid status value."}), 400

    # Update the stage status in the project_stages table
    db_execute('UPDATE project_stages SET status_id = ? WHERE id = ?', (new_status, stage_id), commit=True)

    return jsonify({"success": True})

@projects_bp.route('/<int:project_id>/add_update', methods=['POST'])
def add_update(project_id):
    try:
        comment = request.form['comment']
        salesperson_id = request.form['salesperson_id']  # Replace with logged-in user ID
        insert_project_update(project_id, salesperson_id, comment)

        # Fetch updated list of updates
        updates = get_project_updates(project_id)
        return jsonify({'success': True, 'updates': updates})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@projects_bp.route('/<int:project_id>/quick_update', methods=['POST'])
def quick_update_project(project_id):
    try:
        next_stage_name = request.form.get('next_stage_name')
        next_stage_deadline = request.form.get('next_stage_deadline')
        estimated_value = request.form.get('estimated_value')
        estimated_value_input = estimated_value

        with db_cursor(commit=True) as cursor:
            current_values = _execute_with_cursor(
                cursor,
                'SELECT next_stage_id, next_stage_deadline, estimated_value FROM projects WHERE id = ?',
                (project_id,),
                fetch='one'
            )
            if not current_values:
                raise RuntimeError(f"Project not found: {project_id}")

            next_stage_id = current_values['next_stage_id']

            if next_stage_name:
                stage_row = _execute_with_cursor(
                    cursor,
                    'SELECT id FROM project_stages WHERE project_id = ? AND name = ?',
                    (project_id, next_stage_name),
                    fetch='one'
                )
                if stage_row:
                    next_stage_id = stage_row['id']
                else:
                    insert_row = _execute_with_cursor(
                        cursor,
                        'INSERT INTO project_stages (project_id, name, status_id) VALUES (?, ?, ?) RETURNING id',
                        (project_id, next_stage_name, 1),
                        fetch='one'
                    )
                    if insert_row:
                        next_stage_id = insert_row.get('id', list(insert_row.values())[0])

            next_stage_deadline = next_stage_deadline or current_values['next_stage_deadline']
            estimated_value = float(estimated_value_input) if estimated_value_input else current_values['estimated_value']

            _execute_with_cursor(
                cursor,
                '''
                UPDATE projects 
                SET next_stage_id = ?, 
                    next_stage_deadline = ?, 
                    estimated_value = ?
                WHERE id = ?
                ''',
                (next_stage_id, next_stage_deadline, estimated_value, project_id)
            )

            stage_name = None
            if next_stage_id:
                stage_row = _execute_with_cursor(
                    cursor,
                    'SELECT name FROM project_stages WHERE id = ?',
                    (next_stage_id,),
                    fetch='one'
                )
                stage_name = stage_row['name'] if stage_row else None

        return jsonify({
            'success': True,
            'next_stage_id': next_stage_id,
            'next_stage_name': stage_name,
            'next_stage_deadline': next_stage_deadline,
            'estimated_value': estimated_value
        })

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@projects_bp.route('/<int:project_id>/upload_stage_file', methods=['POST'])
def upload_stage_file(project_id):
    stage_id = request.form.get('stage_id')
    file = request.files.get('file')

    if not stage_id or not file:
        return jsonify({'success': False, 'error': 'Missing stage_id or file'})

    # Save the file to the server
    upload_dir = 'uploads/'
    if not os.path.exists(upload_dir):
        os.makedirs(upload_dir)

    filename = file.filename
    filepath = os.path.join(upload_dir, filename)
    file.save(filepath)

    # Insert file and link it to the stage
    upload_date = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    file_id = insert_file_for_project_stage(stage_id, filename, filepath, upload_date)

    return jsonify({'success': True, 'file_id': file_id})

@projects_bp.route('/<int:project_id>/files', methods=['GET'])
def get_project_files_route(project_id):
    try:
        files = get_files_for_project(project_id)
        return jsonify({'success': True, 'files': files})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# Route for adding stage updates
@projects_bp.route('/<int:project_id>/stages/<int:stage_id>/add_update', methods=['POST'])
def add_stage_update(project_id, stage_id):
    salesperson_id = request.form.get('salesperson_id')
    comment = request.form.get('comment', '').strip()

    if not salesperson_id or not comment:
        return jsonify({"success": False, "error": "Salesperson and comment are required."}), 400

    try:
        # Insert the update
        insert_stage_update(stage_id, salesperson_id, comment)

        # Get updated list of stage updates
        updates = get_stage_updates(stage_id)

        return jsonify({
            "success": True,
            "updates": updates
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@projects_bp.route('/sidebar-stages/<int:project_id>')
def get_sidebar_stages(project_id):
    project = get_project_by_id(project_id)

    # Debug: Check raw database data BEFORE processing
    raw_stages = db_execute(
        '''
        SELECT id, name, description, status_id
        FROM project_stages
        WHERE project_id = ? AND status_id != 3
        ORDER BY id
        ''',
        (project_id,),
        fetch='all'
    ) or []

    print(f"\n=== RAW DATABASE DATA for project {project_id} ===")
    for stage in raw_stages:
        stage_dict = dict(stage)
        desc = stage_dict['description']
        print(f"Stage {stage_dict['id']}:")
        print(f"  - description value: {repr(desc)}")
        print(f"  - description type: {type(desc)}")
        print(f"  - is None?: {desc is None}")
        print(f"  - equals 'None'?: {desc == 'None'}")
        print(f"  - length: {len(desc) if desc else 'N/A'}")

    # Now get processed stages
    stages = get_project_stages(project_id)

    print(f"\n=== PROCESSED DATA ===")
    for stage in stages:
        desc = stage.get('description')
        print(f"Stage {stage.get('id')}:")
        print(f"  - description value: {repr(desc)}")
        print(f"  - description type: {type(desc)}")
        print(f"  - is None?: {desc is None}")
        print(f"  - equals 'None'?: {desc == 'None'}")
        print(f"  - truthiness: {bool(desc)}")
        print(f"  - length check would pass?: {bool(desc and len(str(desc)) > 0)}")

    # Make sure get_project_stages is available in template context
    return render_template('components/project_stages_list.html',
                           project=project,
                           stages=stages,
                           get_project_stages=get_project_stages)  # Add this line!

@projects_bp.route('/set-active/<int:project_id>')
def set_active_project(project_id):
    session['active_project_id'] = project_id
    project = get_project_by_id(project_id)
    return jsonify(
        success=True,
        projectName=project['name'] if project else "Selected Project"
    )

@projects_bp.route('/<int:project_id>/stages', methods=['GET'])
def get_project_stages_api(project_id):
    try:
        stages = get_project_stages(project_id)  # Fetch stages from the database
        return jsonify({'success': True, 'stages': stages})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500




@projects_bp.route('/kanban', methods=['GET'])
def kanban_projects():
    """
    Display the projects in a Kanban board view.
    """
    show_all = request.args.get('show_all', '0') == '1'

    # Get the projects data (using existing method)
    if show_all:
        projects = get_projects()
    else:
        projects = get_projects(salesperson_id=current_user.get_salesperson_id())

    # Format dates and values for display
    for project in projects:
        if project['next_stage_deadline']:
            try:
                from datetime import datetime
                deadline = datetime.strptime(project['next_stage_deadline'], '%Y-%m-%d')
                project['next_stage_deadline_formatted'] = deadline.strftime('%Y-%m-%d')
            except (ValueError, TypeError):
                project['next_stage_deadline_formatted'] = project['next_stage_deadline']
        else:
            project['next_stage_deadline_formatted'] = None

        project['estimated_value_formatted'] = f"${float(project['estimated_value']):,.2f}" if project[
            'estimated_value'] else None

    customers = get_customers()
    salespeople = get_salespeople()
    statuses = get_project_statuses()

    return render_template('kanban.html',
                           projects=projects,
                           customers=customers,
                           salespeople=salespeople,
                           statuses=statuses,
                           show_all=show_all)


@projects_bp.route('/api/projects', methods=['GET'])
def api_list_projects():
    """
    API endpoint to get projects as JSON, with optional filtering.
    """
    try:
        # Get filter parameters
        customer_id = request.args.get('customer_id', '')
        salesperson_id = request.args.get('salesperson_id', '')
        status_id = request.args.get('status_id', '')

        # Apply filters only if they are provided
        projects = get_projects(
            customer_id=customer_id if customer_id else None,
            salesperson_id=salesperson_id if salesperson_id else None,
            status_id=status_id if status_id else None
        )

        # Format dates and values
        for project in projects:
            if project['next_stage_deadline']:
                try:
                    from datetime import datetime
                    deadline = datetime.strptime(project['next_stage_deadline'], '%Y-%m-%d')
                    project['next_stage_deadline_formatted'] = deadline.strftime('%Y-%m-%d')
                except (ValueError, TypeError):
                    project['next_stage_deadline_formatted'] = project['next_stage_deadline']
            else:
                project['next_stage_deadline_formatted'] = None

            project['estimated_value_formatted'] = f"${float(project['estimated_value']):,.2f}" if project[
                'estimated_value'] else None

        return jsonify({'success': True, 'projects': projects})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@projects_bp.route('/<int:project_id>/update_status', methods=['POST'])
def update_project_status(project_id):
    """
    Update a project's status (for drag and drop functionality).
    """
    try:
        status_id = request.form.get('status_id')
        if not status_id:
            return jsonify({'success': False, 'error': 'Status ID is required'})

        # Get current project data
        project = get_project_by_id(project_id)

        # Update just the status
        update_project(
            project_id=project_id,
            customer_id=project['customer_id'],
            salesperson_id=project['salesperson_id'],
            name=project['name'],
            description=project['description'],
            status_id=status_id
        )

        # Add an update comment about the status change
        statuses = get_project_statuses()
        status_name = next((s['status'] for s in statuses if s['id'] == int(status_id)), 'Unknown')

        insert_project_update(
            project_id=project_id,
            salesperson_id=current_user.get_salesperson_id(),
            comment=f"Status changed to: {status_name}"
        )

        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@projects_bp.route('/<int:project_id>/rfqs', methods=['GET'])
def get_project_rfqs(project_id):
    """
    Get all RFQs associated with a project as JSON.
    """
    try:
        # Import the function we defined for getting RFQs by project
        from models import get_rfqs_for_project

        rfqs = get_rfqs_for_project(project_id)
        # Ensure all objects are serializable
        for rfq in rfqs:
            for key, value in rfq.items():
                # Convert non-serializable objects to strings
                if not isinstance(value, (str, int, float, bool, type(None), list, dict)):
                    rfq[key] = str(value)

        return jsonify({'success': True, 'rfqs': rfqs})
    except Exception as e:
        print(f"Error in get_project_rfqs: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500
