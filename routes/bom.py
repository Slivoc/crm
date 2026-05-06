from flask import Blueprint, render_template, jsonify, request, redirect, url_for, Response
from db import db_cursor, execute as db_execute
from models import create_base_part_number, get_global_alternatives
import logging
import pandas as pd
from werkzeug.utils import secure_filename
import os
import csv
import io
from datetime import datetime, timedelta
from flask_login import current_user


def _using_postgres() -> bool:
    return os.getenv('DATABASE_URL', '').startswith(('postgres://', 'postgresql://'))


def _prepare_query(query: str) -> str:
    return query.replace('?', '%s') if _using_postgres() else query


def _execute_with_cursor(cur, query, params=None):
    cur.execute(_prepare_query(query), params or [])
    return cur


def _with_returning_clause(query: str) -> str:
    trimmed = query.strip().rstrip(';')
    if not _using_postgres():
        return query
    return f"{trimmed} RETURNING id"


def _fetch_inserted_id(cur):
    if _using_postgres():
        row = cur.fetchone()
        if row:
            return row.get('id') if isinstance(row, dict) else row[0]
        return None
    return getattr(cur, 'lastrowid', None)


def _build_in_clause(values):
    if not values:
        return None, []
    return ', '.join(['?'] * len(values)), values


def _parse_positive_int(value, default=None):
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _parse_percentage(value, default=50):
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    if parsed < 0:
        return 0
    if parsed > 100:
        return 100
    return parsed


def _get_all_kit_boms():
    return db_execute('''
        SELECT id, name, description
        FROM bom_headers
        WHERE type = 'kit'
        ORDER BY name
    ''', fetch='all') or []


def _resolve_salesperson_id():
    salesperson_id = 1
    if current_user.is_authenticated:
        user_salesperson = db_execute(
            "SELECT legacy_salesperson_id FROM salesperson_user_link WHERE user_id = ?",
            (current_user.id,),
            fetch='one',
        )
        if user_salesperson and user_salesperson.get('legacy_salesperson_id'):
            salesperson_id = user_salesperson['legacy_salesperson_id']
    return salesperson_id


def _fetch_bom_header(bom_id):
    return db_execute('''
        SELECT bh.*,
               COALESCE(COUNT(DISTINCT bl.id), 0) as components_count,
               COALESCE(COUNT(DISTINCT cb.customer_id), 0) as customers_count
        FROM bom_headers bh
        LEFT JOIN bom_lines bl ON bh.id = bl.bom_header_id
        LEFT JOIN customer_boms cb ON bh.id = cb.bom_header_id
        WHERE bh.id = ?
        GROUP BY bh.id
    ''', (bom_id,), fetch='one')


def _fetch_bom_lines(bom_id):
    return db_execute('''
        SELECT
            bl.id,
            bl.base_part_number,
            COALESCE(bl.quantity, 0) AS quantity,
            COALESCE(bl.position, 0) AS position,
            COALESCE(bl.guide_price, 0) AS guide_price,
            COALESCE(NULLIF(TRIM(pn.part_number), ''), bl.base_part_number) AS part_number,
            bl.child_bom_header_id,
            child_bh.name AS child_bom_name
        FROM bom_lines bl
        LEFT JOIN part_numbers pn ON bl.base_part_number = pn.base_part_number
        LEFT JOIN bom_headers child_bh ON child_bh.id = bl.child_bom_header_id
        WHERE bl.bom_header_id = ?
        ORDER BY bl.position, bl.id
    ''', (bom_id,), fetch='all') or []


def _coerce_quantity(value):
    try:
        parsed = float(value or 0)
    except (TypeError, ValueError):
        return 0
    if parsed.is_integer():
        return int(parsed)
    return parsed


def _merge_flattened_parts(target, source):
    for base_part_number, source_item in source.items():
        target_item = target.setdefault(base_part_number, {
            'base_part_number': base_part_number,
            'part_number': source_item.get('part_number') or base_part_number,
            'total_quantity': 0,
            'guide_price': source_item.get('guide_price'),
            'sort_order': source_item.get('sort_order', 999999),
        })
        target_item['total_quantity'] += source_item.get('total_quantity') or 0
        if source_item.get('guide_price') is not None:
            current_guide = target_item.get('guide_price')
            if current_guide is None or source_item['guide_price'] > current_guide:
                target_item['guide_price'] = source_item['guide_price']
        target_item['sort_order'] = min(
            target_item.get('sort_order', 999999),
            source_item.get('sort_order', 999999),
        )
    return target


def _explode_bom_requirements(bom_id, multiplier=1, path=None):
    path = list(path or [])
    if bom_id in path:
        cycle = ' -> '.join(str(part) for part in path + [bom_id])
        raise ValueError(f"Cyclic child BOM relationship detected: {cycle}")

    flattened = {}
    for row in _fetch_bom_lines(bom_id):
        line = dict(row)
        line_qty = _coerce_quantity(line.get('quantity'))
        extended_qty = line_qty * multiplier
        child_bom_header_id = line.get('child_bom_header_id')

        if child_bom_header_id:
            child_flattened = _explode_bom_requirements(
                child_bom_header_id,
                multiplier=extended_qty,
                path=path + [bom_id],
            )
            _merge_flattened_parts(flattened, child_flattened)
            continue

        base_part_number = (line.get('base_part_number') or '').strip()
        if not base_part_number:
            continue

        item = flattened.setdefault(base_part_number, {
            'base_part_number': base_part_number,
            'part_number': line.get('part_number') or base_part_number,
            'total_quantity': 0,
            'guide_price': None,
            'sort_order': int(line.get('position') or 0),
        })
        item['total_quantity'] += extended_qty
        guide_price = line.get('guide_price')
        if guide_price not in (None, ''):
            guide_price = float(guide_price or 0)
            current_guide = item.get('guide_price')
            if current_guide is None or guide_price > current_guide:
                item['guide_price'] = guide_price

    return flattened


def _flattened_parts_to_rows(flattened_parts):
    rows = list(flattened_parts.values())
    rows.sort(key=lambda item: (item.get('sort_order', 999999), item.get('part_number') or item.get('base_part_number') or ''))
    for index, row in enumerate(rows, start=1):
        total_quantity = row.get('total_quantity') or 0
        guide_price = row.get('guide_price')
        row['item_number'] = index
        row['total_guide_value'] = (guide_price * total_quantity) if guide_price is not None else None
    return rows


def _get_direct_bom_rows_for_parts_list(bom_id, multiplier=1):
    flattened = {}
    for row in _fetch_bom_lines(bom_id):
        line = dict(row)
        if line.get('child_bom_header_id'):
            continue

        base_part_number = (line.get('base_part_number') or '').strip()
        if not base_part_number:
            continue

        line_qty = _coerce_quantity(line.get('quantity'))
        total_quantity = line_qty * multiplier
        item = flattened.setdefault(base_part_number, {
            'base_part_number': base_part_number,
            'part_number': line.get('part_number') or base_part_number,
            'total_quantity': 0,
            'guide_price': None,
            'sort_order': int(line.get('position') or 0),
        })
        item['total_quantity'] += total_quantity
        guide_price = line.get('guide_price')
        if guide_price not in (None, ''):
            guide_price = float(guide_price or 0)
            current_guide = item.get('guide_price')
            if current_guide is None or guide_price > current_guide:
                item['guide_price'] = guide_price

    return _flattened_parts_to_rows(flattened)


def _build_bom_matrix_data(bom_id):
    parent_lines = [dict(row) for row in _fetch_bom_lines(bom_id)]
    child_columns = []
    matrix_map = {}
    direct_flattened = {}

    for line in parent_lines:
        quantity_multiplier = _coerce_quantity(line.get('quantity'))
        child_bom_header_id = line.get('child_bom_header_id')
        if child_bom_header_id:
            existing_column = next((item for item in child_columns if item['bom_id'] == child_bom_header_id), None)
            if not existing_column:
                existing_column = {
                    'bom_id': child_bom_header_id,
                    'name': line.get('child_bom_name') or f'BOM {child_bom_header_id}',
                    'multiplier': 0,
                    'line_ids': [],
                }
                child_columns.append(existing_column)
            existing_column['multiplier'] += quantity_multiplier
            existing_column['line_ids'].append(line.get('id'))

            child_flattened = _explode_bom_requirements(child_bom_header_id, multiplier=quantity_multiplier, path=[bom_id])
            for base_part_number, child_item in child_flattened.items():
                row = matrix_map.setdefault(base_part_number, {
                    'base_part_number': base_part_number,
                    'part_number': child_item.get('part_number') or base_part_number,
                    'child_quantities': {},
                    'total_quantity': 0,
                    'guide_price': child_item.get('guide_price'),
                    'sort_order': child_item.get('sort_order', 999999),
                })
                qty = child_item.get('total_quantity') or 0
                row['child_quantities'][child_bom_header_id] = row['child_quantities'].get(child_bom_header_id, 0) + qty
                row['total_quantity'] += qty
                if child_item.get('guide_price') is not None:
                    current_guide = row.get('guide_price')
                    if current_guide is None or child_item['guide_price'] > current_guide:
                        row['guide_price'] = child_item['guide_price']
                row['sort_order'] = min(row.get('sort_order', 999999), child_item.get('sort_order', 999999))
            continue

        base_part_number = (line.get('base_part_number') or '').strip()
        if not base_part_number:
            continue
        item = direct_flattened.setdefault(base_part_number, {
            'base_part_number': base_part_number,
            'part_number': line.get('part_number') or base_part_number,
            'total_quantity': 0,
            'guide_price': None,
            'sort_order': int(line.get('position') or 0),
        })
        item['total_quantity'] += quantity_multiplier
        guide_price = line.get('guide_price')
        if guide_price not in (None, ''):
            guide_price = float(guide_price or 0)
            current_guide = item.get('guide_price')
            if current_guide is None or guide_price > current_guide:
                item['guide_price'] = guide_price

    child_columns.sort(key=lambda item: (item.get('name') or '', item.get('bom_id') or 0))
    matrix_rows = _flattened_parts_to_rows(matrix_map)
    for row in matrix_rows:
        row['child_quantities'] = {
            child['bom_id']: row['child_quantities'].get(child['bom_id'], 0)
            for child in child_columns
        }
    direct_rows = _flattened_parts_to_rows(direct_flattened)

    return {
        'child_columns': child_columns,
        'matrix_rows': matrix_rows,
        'direct_rows': direct_rows,
        'has_child_kits': bool(child_columns),
    }


def _get_linked_parts_lists_for_bom(bom_id):
    return db_execute('''
        SELECT
            pl.id,
            pl.name,
            pl.date_modified,
            c.name AS customer_name,
            s.name AS status_name,
            (SELECT COUNT(*) FROM parts_list_lines pll WHERE pll.parts_list_id = pl.id) AS line_count,
            COALESCE((
                SELECT SUM(COALESCE(cql.quote_price_gbp, 0) * COALESCE(pll.quantity, 0))
                FROM parts_list_lines pll
                LEFT JOIN customer_quote_lines cql ON cql.id = (
                    SELECT cql_latest.id
                    FROM customer_quote_lines cql_latest
                    WHERE cql_latest.parts_list_line_id = pll.id
                    ORDER BY cql_latest.date_modified DESC NULLS LAST, cql_latest.id DESC
                    LIMIT 1
                )
                WHERE pll.parts_list_id = pl.id
            ), 0) AS quoted_value_gbp
        FROM parts_lists pl
        LEFT JOIN customers c ON c.id = pl.customer_id
        LEFT JOIN parts_list_statuses s ON s.id = pl.status_id
        WHERE pl.bom_header_id = ?
        ORDER BY pl.date_modified DESC, pl.id DESC
    ''', (bom_id,), fetch='all') or []


def _get_parts_list_progress(parts_list_id):
    rows = db_execute('''
        SELECT
            pll.id,
            pll.base_part_number,
            COALESCE(NULLIF(TRIM(pll.customer_part_number), ''), pll.base_part_number) AS customer_part_number,
            COALESCE(pll.chosen_qty, pll.quantity, 0) AS effective_quantity,
            cql.display_part_number,
            cql.quoted_part_number,
            cql.base_cost_gbp,
            cql.quote_price_gbp,
            cql.target_price_gbp,
            cql.quoted_status
        FROM parts_list_lines pll
        LEFT JOIN customer_quote_lines cql ON cql.id = (
            SELECT cql_latest.id
            FROM customer_quote_lines cql_latest
            WHERE cql_latest.parts_list_line_id = pll.id
            ORDER BY cql_latest.date_modified DESC NULLS LAST, cql_latest.id DESC
            LIMIT 1
        )
        WHERE pll.parts_list_id = ?
        ORDER BY pll.line_number ASC, pll.id ASC
    ''', (parts_list_id,), fetch='all') or []

    progress_by_base = {}
    summary = {
        'total_lines': 0,
        'costed_lines': 0,
        'quoted_lines': 0,
        'target_lines': 0,
        'total_base_cost_gbp': 0.0,
        'total_quoted_value_gbp': 0.0,
        'total_target_value_gbp': 0.0,
    }

    for row in rows:
        line = dict(row)
        base_part_number = line.get('base_part_number')
        if not base_part_number:
            continue
        quantity = _coerce_quantity(line.get('effective_quantity'))
        base_cost_gbp = float(line['base_cost_gbp']) if line.get('base_cost_gbp') is not None else None
        quote_price_gbp = float(line['quote_price_gbp']) if line.get('quote_price_gbp') is not None else None
        target_price_gbp = float(line['target_price_gbp']) if line.get('target_price_gbp') is not None else None
        quoted_status = line.get('quoted_status') or 'created'

        summary['total_lines'] += 1
        if base_cost_gbp is not None:
            summary['costed_lines'] += 1
            summary['total_base_cost_gbp'] += base_cost_gbp * quantity
        if quote_price_gbp is not None:
            summary['total_quoted_value_gbp'] += quote_price_gbp * quantity
        if target_price_gbp is not None:
            summary['target_lines'] += 1
            summary['total_target_value_gbp'] += target_price_gbp * quantity
        if quoted_status == 'quoted':
            summary['quoted_lines'] += 1

        progress_by_base[base_part_number] = {
            'base_part_number': base_part_number,
            'customer_part_number': line.get('customer_part_number') or base_part_number,
            'display_part_number': line.get('display_part_number'),
            'quoted_part_number': line.get('quoted_part_number'),
            'effective_part_number': (
                line.get('quoted_part_number')
                or line.get('display_part_number')
                or line.get('customer_part_number')
                or base_part_number
            ),
            'effective_quantity': quantity,
            'base_cost_gbp': base_cost_gbp,
            'chosen_unit_gbp': base_cost_gbp,
            'quote_price_gbp': quote_price_gbp,
            'target_price_gbp': target_price_gbp,
            'quoted_status': quoted_status,
            'chosen_total_gbp': (base_cost_gbp * quantity) if base_cost_gbp is not None else None,
            'quoted_total_gbp': (quote_price_gbp * quantity) if quote_price_gbp is not None else None,
            'target_total_gbp': (target_price_gbp * quantity) if target_price_gbp is not None else None,
        }

    return summary, progress_by_base


def _get_bom_line_linked_parts_list_details(bom_id, base_part_number):
    linked_lists = _get_linked_parts_lists_for_bom(bom_id)
    if not linked_lists:
        return []

    details = []
    for linked in linked_lists:
        line = db_execute('''
            SELECT
                pll.id AS parts_list_line_id,
                pll.parts_list_id,
                pll.line_number,
                COALESCE(NULLIF(TRIM(pll.customer_part_number), ''), pll.base_part_number) AS customer_part_number,
                COALESCE(pll.chosen_qty, pll.quantity, 0) AS effective_quantity,
                pll.chosen_cost,
                pll.chosen_lead_days,
                pll.chosen_source_type,
                s.name AS chosen_supplier_name,
                c.currency_code AS chosen_currency_code,
                cql.display_part_number,
                cql.quoted_part_number,
                cql.quote_price_gbp,
                cql.target_price_gbp,
                cql.base_cost_gbp,
                cql.quoted_status
            FROM parts_list_lines pll
            LEFT JOIN suppliers s ON s.id = pll.chosen_supplier_id
            LEFT JOIN currencies c ON c.id = pll.chosen_currency_id
            LEFT JOIN customer_quote_lines cql ON cql.id = (
                SELECT cql_latest.id
                FROM customer_quote_lines cql_latest
                WHERE cql_latest.parts_list_line_id = pll.id
                ORDER BY cql_latest.date_modified DESC NULLS LAST, cql_latest.id DESC
                LIMIT 1
            )
            WHERE pll.parts_list_id = ?
              AND pll.base_part_number = ?
            ORDER BY pll.line_number ASC, pll.id ASC
            LIMIT 1
        ''', (linked['id'], base_part_number), fetch='one')
        if not line:
            continue
        line_data = dict(line)
        line_data['parts_list_id'] = linked['id']
        line_data['parts_list_name'] = linked.get('name')
        line_data['customer_name'] = linked.get('customer_name')
        line_data['status_name'] = linked.get('status_name')
        details.append(line_data)

    return details


def _build_child_kit_panel_details(parent_bom_id, child_bom_header_id, line_multiplier=1):
    child_bom = _fetch_bom_header(child_bom_header_id)
    child_rows = _flattened_parts_to_rows(_explode_bom_requirements(child_bom_header_id, multiplier=line_multiplier))
    linked_parts_lists = [dict(row) for row in _get_linked_parts_lists_for_bom(parent_bom_id)]
    active_parts_list = linked_parts_lists[0] if linked_parts_lists else None
    progress_summary = None
    progress_by_base = {}
    if active_parts_list:
        progress_summary, progress_by_base = _get_parts_list_progress(active_parts_list['id'])

    chosen_total_gbp = 0.0
    chosen_total_present = False
    for row in child_rows:
        progress = progress_by_base.get(row['base_part_number'])
        row['pricing_progress'] = progress
        if progress and progress.get('chosen_total_gbp') is not None:
            chosen_total_present = True
            chosen_total_gbp += progress['chosen_total_gbp']

    return {
        'child_bom': dict(child_bom) if child_bom else {'id': child_bom_header_id},
        'rows': child_rows,
        'active_parts_list': active_parts_list,
        'summary': {
            'row_count': len(child_rows),
            'total_quantity': sum(row.get('total_quantity') or 0 for row in child_rows),
            'chosen_total_gbp': chosen_total_gbp if chosen_total_present else None,
        }
    }


def _calculate_bom_row_chosen_value(line, progress_by_base):
    child_bom_header_id = line.get('child_bom_header_id')
    if child_bom_header_id:
        exploded_rows = _flattened_parts_to_rows(
            _explode_bom_requirements(
                int(child_bom_header_id),
                multiplier=_coerce_quantity(line.get('quantity')),
            )
        )
        total = 0.0
        has_value = False
        for exploded in exploded_rows:
            progress = progress_by_base.get(exploded['base_part_number'])
            chosen_unit = (progress or {}).get('chosen_unit_gbp')
            if chosen_unit is None:
                continue
            total += float(chosen_unit) * float(exploded.get('total_quantity') or 0)
            has_value = True
        return total if has_value else None

    base_part_number = create_base_part_number(line.get('base_part_number') or '') if line.get('base_part_number') else ''
    progress = progress_by_base.get(base_part_number)
    chosen_unit = (progress or {}).get('chosen_unit_gbp')
    if chosen_unit is None:
        return None
    return float(chosen_unit) * float(_coerce_quantity(line.get('quantity')))


def _calculate_bom_row_pricing(line, progress_by_base):
    child_bom_header_id = line.get('child_bom_header_id')
    line_quantity = float(_coerce_quantity(line.get('quantity')) or 0)
    if child_bom_header_id:
        exploded_rows = _flattened_parts_to_rows(
            _explode_bom_requirements(
                int(child_bom_header_id),
                multiplier=_coerce_quantity(line.get('quantity')),
            )
        )
        chosen_total = 0.0
        quoted_total = 0.0
        has_chosen = False
        has_quoted = False
        for exploded in exploded_rows:
            progress = progress_by_base.get(exploded['base_part_number'])
            exploded_qty = float(exploded.get('total_quantity') or 0)
            if progress and progress.get('chosen_unit_gbp') is not None:
                chosen_total += float(progress['chosen_unit_gbp']) * exploded_qty
                has_chosen = True
            if progress and progress.get('quote_price_gbp') is not None:
                quoted_total += float(progress['quote_price_gbp']) * exploded_qty
                has_quoted = True

        chosen_total = chosen_total if has_chosen else None
        quoted_total = quoted_total if has_quoted else None
        return {
            'effective_part_number': line.get('child_bom_name') or f"Child Kit {child_bom_header_id}",
            'chosen_unit_gbp': (chosen_total / line_quantity) if (chosen_total is not None and line_quantity) else None,
            'chosen_total_gbp': chosen_total,
            'quote_unit_gbp': (quoted_total / line_quantity) if (quoted_total is not None and line_quantity) else None,
            'quote_total_gbp': quoted_total,
        }

    base_part_number = create_base_part_number(line.get('base_part_number') or '') if line.get('base_part_number') else ''
    progress = progress_by_base.get(base_part_number) or {}
    chosen_unit = progress.get('chosen_unit_gbp')
    quote_unit = progress.get('quote_price_gbp')
    return {
        'effective_part_number': progress.get('effective_part_number') or line.get('part_number') or base_part_number,
        'chosen_unit_gbp': chosen_unit,
        'chosen_total_gbp': (float(chosen_unit) * line_quantity) if chosen_unit is not None else None,
        'quote_unit_gbp': quote_unit,
        'quote_total_gbp': (float(quote_unit) * line_quantity) if quote_unit is not None else None,
    }


def _get_supplier_quote_offers_for_base_part(base_part_number, list_ids=None, limit=15):
    where_clauses = ['pll.base_part_number = ?']
    params = [base_part_number]

    if list_ids:
        in_clause, list_params = _build_in_clause(list_ids)
        where_clauses.append(f'pll.parts_list_id IN ({in_clause})')
        params.extend(list_params)

    where_sql = ' AND '.join(where_clauses)
    return db_execute(f'''
        SELECT
            sql.id AS quote_line_id,
            sql.quoted_part_number,
            sql.manufacturer,
            sql.quantity_quoted,
            sql.qty_available,
            sql.unit_price,
            sql.lead_time_days,
            sql.condition_code,
            sql.certifications,
            sql.is_no_bid,
            sq.id AS quote_id,
            sq.quote_reference,
            sq.quote_date,
            s.name AS supplier_name,
            c.currency_code,
            pl.id AS parts_list_id,
            pl.name AS parts_list_name
        FROM parts_list_supplier_quote_lines sql
        JOIN parts_list_supplier_quotes sq ON sq.id = sql.supplier_quote_id
        JOIN suppliers s ON s.id = sq.supplier_id
        LEFT JOIN currencies c ON c.id = sq.currency_id
        JOIN parts_list_lines pll ON pll.id = sql.parts_list_line_id
        JOIN parts_lists pl ON pl.id = pll.parts_list_id
        WHERE {where_sql}
        ORDER BY
            sql.is_no_bid ASC,
            CASE WHEN sql.unit_price IS NULL THEN 1 ELSE 0 END,
            sql.unit_price ASC,
            COALESCE(sq.quote_date, sql.date_modified, sql.date_created) DESC
        LIMIT ?
    ''', params + [limit], fetch='all') or []


def _get_recent_offer_cutoff(recent_offer_days):
    if not recent_offer_days:
        return None
    return (datetime.utcnow() - timedelta(days=recent_offer_days)).strftime('%Y-%m-%d %H:%M:%S')


def _get_global_alt_comment_map(base_part_numbers, recent_offer_days=None):
    cleaned = [str(value).strip() for value in (base_part_numbers or []) if str(value).strip()]
    if not cleaned:
        return {}

    placeholders = ', '.join(['?'] * len(cleaned))
    recent_offer_cutoff = _get_recent_offer_cutoff(recent_offer_days or 30)

    rows = db_execute(f'''
        WITH stock_totals AS (
            SELECT
                sm.base_part_number,
                SUM(sm.available_quantity) AS amount_in_stock
            FROM stock_movements sm
            WHERE sm.movement_type = 'IN'
              AND sm.available_quantity > 0
            GROUP BY sm.base_part_number
        ),
        recent_offer_parts AS (
            SELECT
                pll.base_part_number,
                COUNT(*) AS recent_offer_count
            FROM parts_list_supplier_quote_lines sql
            JOIN parts_list_supplier_quotes sq ON sq.id = sql.supplier_quote_id
            JOIN parts_list_lines pll ON pll.id = sql.parts_list_line_id
            WHERE COALESCE(sq.quote_date, sq.date_created) >= ?
            GROUP BY pll.base_part_number
        )
        SELECT DISTINCT
            source.base_part_number AS source_base_part_number,
            alt.base_part_number AS alt_base_part_number,
            COALESCE(NULLIF(TRIM(pn_alt.part_number), ''), alt.base_part_number) AS alt_part_number,
            COALESCE(st.amount_in_stock, 0) AS amount_in_stock,
            COALESCE(rop.recent_offer_count, 0) AS recent_offer_count
        FROM part_alt_group_members source
        JOIN part_alt_group_members alt
          ON alt.group_id = source.group_id
         AND alt.base_part_number <> source.base_part_number
        LEFT JOIN part_numbers pn_alt ON pn_alt.base_part_number = alt.base_part_number
        LEFT JOIN stock_totals st ON st.base_part_number = alt.base_part_number
        LEFT JOIN recent_offer_parts rop ON rop.base_part_number = alt.base_part_number
        WHERE source.base_part_number IN ({placeholders})
          AND (
              COALESCE(st.amount_in_stock, 0) > 0
              OR COALESCE(rop.recent_offer_count, 0) > 0
          )
        ORDER BY
            source.base_part_number,
            COALESCE(st.amount_in_stock, 0) DESC,
            COALESCE(rop.recent_offer_count, 0) DESC,
            COALESCE(NULLIF(TRIM(pn_alt.part_number), ''), alt.base_part_number)
    ''', [recent_offer_cutoff] + cleaned, fetch='all') or []

    comment_map = {}
    for row in rows:
        row_data = dict(row)
        source_base = row_data.get('source_base_part_number')
        if not source_base:
            continue

        metrics = []
        if row_data.get('amount_in_stock'):
            metrics.append(f"{row_data['amount_in_stock']} in stock")
        if row_data.get('recent_offer_count'):
            metrics.append(f"{row_data['recent_offer_count']} supplier offer{'s' if row_data['recent_offer_count'] != 1 else ''}")
        if not metrics:
            continue

        detail = f"alt: {row_data.get('alt_part_number')}: {', '.join(metrics)}"
        comment_map.setdefault(source_base, []).append(detail)

    return {
        source_base: '; '.join(details)
        for source_base, details in comment_map.items()
    }


def _get_bom_stock_report_data(selected_bom_ids, include_recent_offers=False, recent_offer_days=None):
    if not selected_bom_ids:
        return [], []

    in_clause, params = _build_in_clause(selected_bom_ids)
    if not in_clause:
        return [], []

    selected_boms = db_execute(f'''
        SELECT id, name
        FROM bom_headers
        WHERE id IN ({in_clause})
        ORDER BY name
    ''', params, fetch='all') or []

    if not selected_boms:
        return [], []

    selected_bom_ids = [int(row['id']) for row in selected_boms]
    in_clause, params = _build_in_clause(selected_bom_ids)

    recent_offer_clause = ''
    query_params = list(params)
    if include_recent_offers and recent_offer_days:
        recent_offer_cutoff = _get_recent_offer_cutoff(recent_offer_days)
        recent_offer_clause = '''
            , recent_offer_parts AS (
                SELECT
                    pll.base_part_number,
                    COUNT(*) AS recent_offer_count,
                    MAX(COALESCE(sq.quote_date, sq.date_created)) AS latest_offer_date
                FROM parts_list_supplier_quote_lines sql
                JOIN parts_list_supplier_quotes sq ON sq.id = sql.supplier_quote_id
                JOIN parts_list_lines pll ON pll.id = sql.parts_list_line_id
                WHERE COALESCE(sq.quote_date, sq.date_created) >= ?
                GROUP BY pll.base_part_number
            )
        '''
        query_params.append(recent_offer_cutoff)

    rows = db_execute(f'''
        WITH selected_parts AS (
            SELECT DISTINCT bl.base_part_number
            FROM bom_lines bl
            WHERE bl.bom_header_id IN ({in_clause})
        ),
        stock_totals AS (
            SELECT
                sm.base_part_number,
                SUM(sm.available_quantity) AS amount_in_stock
            FROM stock_movements sm
            WHERE sm.movement_type = 'IN'
              AND sm.available_quantity > 0
            GROUP BY sm.base_part_number
        )
        {recent_offer_clause}
        SELECT
            sp.base_part_number,
            COALESCE(MAX(pn.part_number), sp.base_part_number) AS part_number,
            COALESCE(st.amount_in_stock, 0) AS amount_in_stock
            {", COALESCE(rop.recent_offer_count, 0) AS recent_offer_count, rop.latest_offer_date" if recent_offer_clause else ""}
        FROM selected_parts sp
        LEFT JOIN stock_totals st ON st.base_part_number = sp.base_part_number
        LEFT JOIN part_numbers pn ON pn.base_part_number = sp.base_part_number
        {"LEFT JOIN recent_offer_parts rop ON rop.base_part_number = sp.base_part_number" if recent_offer_clause else ""}
        WHERE COALESCE(st.amount_in_stock, 0) > 0
            {"OR COALESCE(rop.recent_offer_count, 0) > 0" if recent_offer_clause else ""}
        GROUP BY
            sp.base_part_number,
            st.amount_in_stock
            {", rop.recent_offer_count, rop.latest_offer_date" if recent_offer_clause else ""}
        ORDER BY COALESCE(MAX(pn.part_number), sp.base_part_number)
    ''', query_params, fetch='all') or []

    memberships = db_execute(f'''
        SELECT DISTINCT
            bl.base_part_number,
            bl.bom_header_id
        FROM bom_lines bl
        WHERE bl.bom_header_id IN ({in_clause})
    ''', params, fetch='all') or []

    membership_map = {}
    for membership in memberships:
        base_part_number = membership['base_part_number']
        bom_id = int(membership['bom_header_id'])
        membership_map.setdefault(base_part_number, set()).add(bom_id)

    matrix_rows = []
    for row in rows:
        row_data = dict(row)
        base_part_number = row['base_part_number']
        bom_flags = {
            bom['id']: ('X' if bom['id'] in membership_map.get(base_part_number, set()) else '')
            for bom in selected_boms
        }
        matrix_rows.append({
            'part_number': row_data.get('part_number') or base_part_number,
            'amount_in_stock': row_data.get('amount_in_stock') or 0,
            'recent_offer_count': row_data.get('recent_offer_count', 0),
            'latest_offer_date': row_data.get('latest_offer_date'),
            'bom_flags': bom_flags
        })

    return selected_boms, matrix_rows


def _get_bom_commonality_report_data(selected_bom_ids, recent_offer_days=None, coverage_threshold_pct=50):
    if not selected_bom_ids:
        return [], []

    in_clause, params = _build_in_clause(selected_bom_ids)
    if not in_clause:
        return [], []

    selected_boms = db_execute(f'''
        SELECT id, name
        FROM bom_headers
        WHERE id IN ({in_clause})
        ORDER BY name
    ''', params, fetch='all') or []

    if not selected_boms:
        return [], []

    selected_bom_ids = [int(row['id']) for row in selected_boms]
    in_clause, params = _build_in_clause(selected_bom_ids)
    recent_offer_cutoff = _get_recent_offer_cutoff(recent_offer_days)
    query_params = list(params)
    if recent_offer_cutoff:
        query_params.append(recent_offer_cutoff)

    rows = db_execute(f'''
        WITH selected_lines AS (
            SELECT DISTINCT
                bl.bom_header_id,
                bl.base_part_number
            FROM bom_lines bl
            WHERE bl.bom_header_id IN ({in_clause})
        ),
        common_parts AS (
            SELECT
                sl.base_part_number,
                COUNT(*) AS bom_count
            FROM selected_lines sl
            GROUP BY sl.base_part_number
        ),
        stock_totals AS (
            SELECT
                sm.base_part_number,
                SUM(sm.available_quantity) AS amount_in_stock
            FROM stock_movements sm
            WHERE sm.movement_type = 'IN'
              AND sm.available_quantity > 0
            GROUP BY sm.base_part_number
        ),
        recent_offer_parts AS (
            SELECT
                pll.base_part_number,
                COUNT(*) AS recent_offer_count,
                MAX(COALESCE(sq.quote_date, sq.date_created)) AS latest_offer_date
            FROM parts_list_supplier_quote_lines sql
            JOIN parts_list_supplier_quotes sq ON sq.id = sql.supplier_quote_id
            JOIN parts_list_lines pll ON pll.id = sql.parts_list_line_id
            WHERE COALESCE(sq.quote_date, sq.date_created) >= ?
            GROUP BY pll.base_part_number
        )
        SELECT
            cp.base_part_number,
            COALESCE(MAX(pn.part_number), cp.base_part_number) AS part_number,
            cp.bom_count,
            COALESCE(st.amount_in_stock, 0) AS amount_in_stock,
            COALESCE(rop.recent_offer_count, 0) AS recent_offer_count,
            rop.latest_offer_date
        FROM common_parts cp
        LEFT JOIN stock_totals st ON st.base_part_number = cp.base_part_number
        LEFT JOIN recent_offer_parts rop ON rop.base_part_number = cp.base_part_number
        LEFT JOIN part_numbers pn ON pn.base_part_number = cp.base_part_number
        GROUP BY
            cp.base_part_number,
            cp.bom_count,
            st.amount_in_stock,
            rop.recent_offer_count,
            rop.latest_offer_date
        ORDER BY
            cp.bom_count DESC,
            COALESCE(st.amount_in_stock, 0) DESC,
            COALESCE(MAX(pn.part_number), cp.base_part_number)
    ''', query_params, fetch='all') or []

    memberships = db_execute(f'''
        SELECT DISTINCT
            bl.base_part_number,
            bl.bom_header_id
        FROM bom_lines bl
        WHERE bl.bom_header_id IN ({in_clause})
    ''', params, fetch='all') or []

    membership_map = {}
    for membership in memberships:
        base_part_number = membership['base_part_number']
        bom_id = int(membership['bom_header_id'])
        membership_map.setdefault(base_part_number, set()).add(bom_id)

    total_selected_boms = len(selected_boms)
    minimum_bom_count = 0
    if total_selected_boms:
        minimum_bom_count = max(1, int((coverage_threshold_pct / 100) * total_selected_boms + 0.999999))
    alt_comment_map = _get_global_alt_comment_map(
        [dict(row).get('base_part_number') for row in rows],
        recent_offer_days=recent_offer_days,
    )
    report_rows = []
    for row in rows:
        row_data = dict(row)
        base_part_number = row_data['base_part_number']
        bom_count = row_data.get('bom_count') or 0
        if bom_count < minimum_bom_count:
            continue
        bom_ids = membership_map.get(base_part_number, set())
        bom_flags = {
            bom['id']: ('X' if bom['id'] in bom_ids else '')
            for bom in selected_boms
        }
        report_rows.append({
            'part_number': row_data.get('part_number') or base_part_number,
            'amount_in_stock': row_data.get('amount_in_stock') or 0,
            'recent_offer_count': row_data.get('recent_offer_count', 0),
            'latest_offer_date': row_data.get('latest_offer_date'),
            'bom_count': bom_count,
            'bom_coverage_pct': (bom_count / total_selected_boms * 100) if total_selected_boms else 0,
            'comments': alt_comment_map.get(base_part_number, ''),
            'bom_flags': bom_flags,
        })

    return selected_boms, report_rows

def _load_bom_dataframe(file, filename):
    if filename.endswith('.csv'):
        return pd.read_csv(file)
    if filename.endswith(('.xlsx', '.xls')):
        return pd.read_excel(file)
    raise ValueError("Unsupported file format. Use CSV or Excel")


def _import_bom_dataframe(cur, bom_id, df, start_position=0):
    imported_count = 0
    skipped_count = 0

    for idx, row in df.iterrows():
        try:
            raw_part_number = str(row.get('part_number', '')).strip()
            if not raw_part_number:
                skipped_count += 1
                continue

            base_part_number = create_base_part_number(raw_part_number)
            quantity = int(row.get('quantity', 1))
            position = start_position + ((idx + 1) * 10)
            raw_guide_price = row.get('guide_price')
            guide_price = float(raw_guide_price) if pd.notna(raw_guide_price) else None

            part = _execute_with_cursor(cur,
                'SELECT base_part_number FROM part_numbers WHERE base_part_number = ?',
                (base_part_number,)
            ).fetchone()

            if not part:
                _execute_with_cursor(cur,
                    'INSERT INTO part_numbers (part_number, base_part_number) VALUES (?, ?)',
                    (raw_part_number, base_part_number)
                )

            _execute_with_cursor(cur, '''
                INSERT INTO bom_lines (
                    bom_header_id, base_part_number, quantity,
                    reference_designator, notes, position, guide_price
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (
                bom_id,
                base_part_number,
                quantity,
                row.get('reference_designator'),
                row.get('notes'),
                position,
                guide_price
            ))

            imported_count += 1
        except Exception as exc:
            logging.error(f"Failed to import row {idx}: {exc}", exc_info=True)
            skipped_count += 1

    return imported_count, skipped_count

bom_bp = Blueprint('bom', __name__, url_prefix='/bom')


@bom_bp.route('/')
def boms():
    # Get all BOMs with their details
    customer_names_expr = (
        "STRING_AGG(DISTINCT c.name, ', ')" if _using_postgres() else "GROUP_CONCAT(DISTINCT c.name)"
    )
    boms = db_execute(f'''
        SELECT bh.*,
               COUNT(DISTINCT bl.id) as components_count,
               COUNT(DISTINCT cb.customer_id) as customers_count,
               {customer_names_expr} as customer_names
        FROM bom_headers bh
        LEFT JOIN bom_lines bl ON bh.id = bl.bom_header_id
        LEFT JOIN customer_boms cb ON bh.id = cb.bom_header_id
        LEFT JOIN customers c ON cb.customer_id = c.id
        WHERE bh.type = 'kit'
        GROUP BY bh.id
        ORDER BY bh.created_at DESC
    ''', fetch='all')

    return render_template('bom/boms.html', boms=boms)


@bom_bp.route('/common-parts-report')
def common_parts_report():
    selected_bom_ids = request.args.getlist('bom_ids', type=int)
    recent_offer_days = _parse_positive_int(request.args.get('recent_offer_days'), default=30)
    coverage_threshold_pct = _parse_percentage(request.args.get('coverage_threshold_pct'), default=50)
    all_boms = _get_all_kit_boms()
    selected_boms, report_rows = _get_bom_commonality_report_data(
        selected_bom_ids,
        recent_offer_days=recent_offer_days,
        coverage_threshold_pct=coverage_threshold_pct,
    )

    return render_template(
        'bom/common_parts_report.html',
        boms=all_boms,
        selected_bom_ids=selected_bom_ids,
        selected_boms=selected_boms,
        report_rows=report_rows,
        recent_offer_days=recent_offer_days,
        coverage_threshold_pct=coverage_threshold_pct,
    )


@bom_bp.route('/common-parts-report.csv')
def common_parts_report_csv():
    selected_bom_ids = request.args.getlist('bom_ids', type=int)
    recent_offer_days = _parse_positive_int(request.args.get('recent_offer_days'), default=30)
    coverage_threshold_pct = _parse_percentage(request.args.get('coverage_threshold_pct'), default=50)
    selected_boms, report_rows = _get_bom_commonality_report_data(
        selected_bom_ids,
        recent_offer_days=recent_offer_days,
        coverage_threshold_pct=coverage_threshold_pct,
    )

    output = io.StringIO()
    writer = csv.writer(output)
    header = [
        'Part Number',
        'BOM Count',
        'BOM Coverage %',
        'Coverage Threshold %',
        'Amount In Stock',
        f'Recent Supplier Offers ({recent_offer_days} days)',
        'Latest Supplier Offer Date',
        'Comments',
    ] + [bom['name'] for bom in selected_boms]
    writer.writerow(header)

    for row in report_rows:
        csv_row = [
            row['part_number'],
            row['bom_count'],
            round(float(row['bom_coverage_pct']), 2),
            coverage_threshold_pct,
            row['amount_in_stock'],
            row.get('recent_offer_count', 0),
            row.get('latest_offer_date') or '',
            row.get('comments') or '',
        ]
        for bom in selected_boms:
            csv_row.append(row['bom_flags'].get(bom['id'], ''))
        writer.writerow(csv_row)

    csv_data = output.getvalue()
    output.close()

    return Response(
        csv_data,
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename=bom_common_parts_report_{recent_offer_days}d.csv'}
    )


@bom_bp.route('/stock-report')
def stock_report():
    selected_bom_ids = request.args.getlist('bom_ids', type=int)
    include_recent_offers = str(request.args.get('include_recent_offers', '')).lower() in ('1', 'true', 'yes', 'on')
    recent_offer_days = _parse_positive_int(request.args.get('recent_offer_days'), default=30)

    all_boms = _get_all_kit_boms()

    selected_boms, matrix_rows = _get_bom_stock_report_data(
        selected_bom_ids,
        include_recent_offers=include_recent_offers,
        recent_offer_days=recent_offer_days,
    )

    return render_template(
        'bom/stock_report.html',
        boms=all_boms,
        selected_bom_ids=selected_bom_ids,
        selected_boms=selected_boms,
        matrix_rows=matrix_rows,
        include_recent_offers=include_recent_offers,
        recent_offer_days=recent_offer_days,
    )


@bom_bp.route('/stock-report.csv')
def stock_report_csv():
    selected_bom_ids = request.args.getlist('bom_ids', type=int)
    include_recent_offers = str(request.args.get('include_recent_offers', '')).lower() in ('1', 'true', 'yes', 'on')
    recent_offer_days = _parse_positive_int(request.args.get('recent_offer_days'), default=30)
    selected_boms, matrix_rows = _get_bom_stock_report_data(
        selected_bom_ids,
        include_recent_offers=include_recent_offers,
        recent_offer_days=recent_offer_days,
    )

    output = io.StringIO()
    writer = csv.writer(output)

    header = ['Part Number', 'Amount In Stock', 'Recent Supplier Offers', 'Latest Supplier Offer Date'] + [bom['name'] for bom in selected_boms]
    writer.writerow(header)

    for row in matrix_rows:
        csv_row = [
            row['part_number'],
            row['amount_in_stock'],
            row.get('recent_offer_count', 0),
            row.get('latest_offer_date') or '',
        ]
        for bom in selected_boms:
            csv_row.append(row['bom_flags'].get(bom['id'], ''))
        writer.writerow(csv_row)

    csv_data = output.getvalue()
    output.close()

    return Response(
        csv_data,
        mimetype='text/csv',
        headers={'Content-Disposition': 'attachment; filename=bom_stock_report.csv'}
    )


@bom_bp.route('/create', methods=['POST'])
def create_bom():
    try:
        with db_cursor(commit=True) as cur:
            insert_query = _with_returning_clause('''
                INSERT INTO bom_headers (name, description, type)
                VALUES (?, ?, 'kit')
            ''')
            _execute_with_cursor(cur, insert_query, [
                request.form['name'],
                request.form.get('description')
            ])
            bom_id = _fetch_inserted_id(cur)

            if not bom_id:
                raise RuntimeError("Failed to create BOM header")

            if 'file' in request.files:
                file = request.files['file']
                if file.filename:
                    filename = secure_filename(file.filename)
                    df = _load_bom_dataframe(file, filename)
                    _import_bom_dataframe(cur, bom_id, df)

        return redirect(url_for('bom.view_bom', bom_id=bom_id))

    except Exception as e:
        logging.error(f"Error creating BOM: {str(e)}", exc_info=True)
        return str(e), 400


@bom_bp.route('/view/<int:bom_id>')
def view_bom(bom_id):
    logging.debug(f"Viewing BOM {bom_id}")
    try:
        bom_row = db_execute('''
            SELECT bh.*,
                   COALESCE(COUNT(DISTINCT bl.id), 0) as components_count,
                   COALESCE(COUNT(DISTINCT cb.customer_id), 0) as customers_count
            FROM bom_headers bh
            LEFT JOIN bom_lines bl ON bh.id = bl.bom_header_id
            LEFT JOIN customer_boms cb ON bh.id = cb.bom_header_id
            WHERE bh.id = ?
            GROUP BY bh.id
        ''', (bom_id,), fetch='one')

        if not bom_row:
            logging.warning(f"BOM {bom_id} not found")
            return "BOM not found", 404

        bom = {k: (v if v is not None else '') for k, v in dict(bom_row).items()}
        logging.debug(f"BOM details: {bom}")

        lines_rows = db_execute('''
            SELECT 
                bl.id,
                bl.base_part_number,
                COALESCE(bl.quantity, 0) as quantity,
                COALESCE(bl.position, 0) as position,
                COALESCE(bl.guide_price, 0) as guide_price,
                pn.part_number,
                bp.offer_line_id,
                COALESCE(ol.price, 0) as current_price,
                ol.lead_time as current_lead_time,
                s.name as supplier_name,
                c.currency_code,
                bl.child_bom_header_id,
                child_bh.name AS child_bom_name
            FROM bom_lines bl
            LEFT JOIN part_numbers pn ON bl.base_part_number = pn.base_part_number
            LEFT JOIN bom_pricing bp ON bl.id = bp.bom_line_id
            LEFT JOIN offer_lines ol ON bp.offer_line_id = ol.id
            LEFT JOIN offers o ON ol.offer_id = o.id
            LEFT JOIN suppliers s ON o.supplier_id = s.id
            LEFT JOIN currencies c ON o.currency_id = c.id
            LEFT JOIN bom_headers child_bh ON child_bh.id = bl.child_bom_header_id
            WHERE bl.bom_header_id = ?
            ORDER BY bl.position, bl.id
        ''', (bom_id,), fetch='all')

        components = []
        component_lines = []
        for row in lines_rows:
            row_dict = dict(row)
            component_lines.append(row_dict)
            components.append({
                'line_id': row_dict.get('id'),
                'base_part_number': row_dict.get('part_number') or row_dict.get('base_part_number') or '',
                'raw_base_part_number': row_dict.get('base_part_number') or '',
                'quantity': int(row_dict.get('quantity', 0) or 0),
                'position': int(row_dict.get('position', 0) or 0),
                'guide_price': float(row_dict.get('guide_price', 0) or 0),
                'current_price': float(row_dict.get('current_price', 0) or 0),
                'supplier_name': row_dict.get('supplier_name', ''),
                'currency_code': row_dict.get('currency_code', ''),
                'lead_time': row_dict.get('current_lead_time', ''),
                'child_bom_header_id': row_dict.get('child_bom_header_id'),
                'child_bom_name': row_dict.get('child_bom_name')
            })

        line_ids = [component['line_id'] for component in components if component.get('line_id') is not None]
        alternates_by_line_id = {}
        if line_ids:
            in_clause, params = _build_in_clause(line_ids)
            alt_rows = db_execute(f'''
                SELECT
                    bla.bom_line_id,
                    bla.preference_rank,
                    COALESCE(pn.part_number, bla.alt_base_part_number) AS alt_part_number
                FROM bom_line_accepted_alternates bla
                LEFT JOIN part_numbers pn ON pn.base_part_number = bla.alt_base_part_number
                WHERE bla.bom_line_id IN ({in_clause})
                ORDER BY bla.bom_line_id, bla.preference_rank, bla.id
            ''', params, fetch='all') or []

            for alt_row in alt_rows:
                alt_data = dict(alt_row)
                line_id = alt_data.get('bom_line_id')
                alt_part_number = (alt_data.get('alt_part_number') or '').strip()
                if not line_id or not alt_part_number:
                    continue
                alternates_by_line_id.setdefault(line_id, []).append(alt_part_number)

        for component in components:
            accepted_alternates = alternates_by_line_id.get(component.get('line_id'), [])
            component['accepted_alternates'] = accepted_alternates
            component['accepted_alternates_display'] = ', '.join(accepted_alternates)

        logging.debug(f"Processed {len(components)} components")

        customer_rows = db_execute('''
            SELECT c.*, cb.reference
            FROM customers c
            JOIN customer_boms cb ON c.id = cb.customer_id
            WHERE cb.bom_header_id = ?
        ''', (bom_id,), fetch='all')

        customers = [{k: (v if v is not None else '') for k, v in dict(row).items()}
                     for row in customer_rows]

        logging.debug(f"Found {len(customers)} customers")

        kit_bom_rows = db_execute('''
            SELECT id, name
            FROM bom_headers
            WHERE type = 'kit'
              AND id <> ?
            ORDER BY name
        ''', (bom_id,), fetch='all') or []
        kit_boms = [
            {
                'id': int(row['id']),
                'name': row['name'] or '',
            }
            for row in kit_bom_rows
        ]

        linked_parts_lists = [dict(row) for row in _get_linked_parts_lists_for_bom(bom_id)]
        active_parts_list = linked_parts_lists[0] if linked_parts_lists else None
        progress_summary = None
        progress_by_base = {}
        if active_parts_list:
            progress_summary, progress_by_base = _get_parts_list_progress(active_parts_list['id'])

        bom_total_value_gbp = 0.0
        bom_total_has_value = False
        bom_total_quoted_gbp = 0.0
        bom_total_has_quoted = False
        for component, component_line in zip(components, component_lines):
            pricing = _calculate_bom_row_pricing(component_line, progress_by_base) if progress_by_base else {}
            component['effective_part_number'] = pricing.get('effective_part_number')
            component['chosen_unit_gbp'] = pricing.get('chosen_unit_gbp')
            component['line_value_gbp'] = pricing.get('chosen_total_gbp')
            component['quote_unit_gbp'] = pricing.get('quote_unit_gbp')
            component['quoted_line_value_gbp'] = pricing.get('quote_total_gbp')
            if component['line_value_gbp'] is not None:
                bom_total_value_gbp += component['line_value_gbp']
                bom_total_has_value = True
            if component['quoted_line_value_gbp'] is not None:
                bom_total_quoted_gbp += component['quoted_line_value_gbp']
                bom_total_has_quoted = True

        return render_template('bom/view_bom.html',
                               bom=bom,
                               components=components,
                               customers=customers,
                               kit_boms=kit_boms,
                               active_parts_list=active_parts_list,
                               bom_total_value_gbp=(bom_total_value_gbp if bom_total_has_value else None),
                               bom_total_quoted_gbp=(bom_total_quoted_gbp if bom_total_has_quoted else None),
                               progress_summary=progress_summary)

    except Exception as e:
        logging.error(f"Error viewing BOM {bom_id}: {str(e)}", exc_info=True)
        return f"Error loading BOM: {str(e)}", 500


@bom_bp.route('/view/<int:bom_id>/matrix')
def view_bom_matrix(bom_id):
    try:
        bom_row = _fetch_bom_header(bom_id)
        if not bom_row:
            return "BOM not found", 404

        bom = {k: (v if v is not None else '') for k, v in dict(bom_row).items()}
        matrix_data = _build_bom_matrix_data(bom_id)
        linked_parts_lists = [dict(row) for row in _get_linked_parts_lists_for_bom(bom_id)]
        active_parts_list = linked_parts_lists[0] if linked_parts_lists else None
        progress_summary = None
        progress_by_base = {}
        if active_parts_list:
            progress_summary, progress_by_base = _get_parts_list_progress(active_parts_list['id'])

        for row in matrix_data['matrix_rows']:
            row['pricing_progress'] = progress_by_base.get(row['base_part_number'])
        for row in matrix_data['direct_rows']:
            row['pricing_progress'] = progress_by_base.get(row['base_part_number'])

        matrix_summary = {
            'row_count': len(matrix_data['matrix_rows']),
            'direct_row_count': len(matrix_data['direct_rows']),
            'child_kit_count': len(matrix_data['child_columns']),
            'total_quantity': sum(row.get('total_quantity') or 0 for row in matrix_data['matrix_rows']),
            'total_chosen_value_gbp': (progress_summary or {}).get('total_base_cost_gbp', 0.0) if active_parts_list else 0.0,
        }

        return render_template(
            'bom/matrix_bom.html',
            bom=bom,
            matrix_data=matrix_data,
            matrix_summary=matrix_summary,
            linked_parts_lists=linked_parts_lists,
            active_parts_list=active_parts_list,
            progress_summary=progress_summary,
        )
    except Exception as exc:
        logging.error(f"Error rendering BOM matrix for {bom_id}: {exc}", exc_info=True)
        return f"Error loading BOM matrix: {exc}", 500


@bom_bp.route('/<int:bom_id>/create-parts-list', methods=['POST'])
def create_parts_list_from_bom(bom_id):
    try:
        bom_row = _fetch_bom_header(bom_id)
        if not bom_row:
            return "BOM not found", 404

        build_quantity = _coerce_quantity(request.form.get('build_quantity') or 1)
        if not build_quantity:
            build_quantity = 1
        include_child_kit_parts = str(request.form.get('include_child_kit_parts') or '1').lower() in ('1', 'true', 'yes', 'on')

        if include_child_kit_parts:
            flattened_rows = _flattened_parts_to_rows(_explode_bom_requirements(bom_id, multiplier=build_quantity))
        else:
            flattened_rows = _get_direct_bom_rows_for_parts_list(bom_id, multiplier=build_quantity)

        if not flattened_rows:
            return "This BOM does not contain any explodable part lines", 400

        assigned_customers = db_execute('''
            SELECT customer_id
            FROM customer_boms
            WHERE bom_header_id = ?
            ORDER BY customer_id
        ''', (bom_id,), fetch='all') or []
        customer_ids = [row['customer_id'] for row in assigned_customers if row.get('customer_id') is not None]
        customer_id = customer_ids[0] if len(customer_ids) == 1 else None
        salesperson_id = _resolve_salesperson_id()
        timestamp_suffix = datetime.utcnow().strftime('%Y-%m-%d %H:%M')
        list_name = f"{bom_row['name']} x{build_quantity} Pricing {timestamp_suffix}"
        list_notes = (
            f"Created from BOM {bom_row['name']} (ID {bom_id}) at build quantity {build_quantity}. "
            f"{'Includes' if include_child_kit_parts else 'Excludes'} child kit component explosion."
        )

        with db_cursor(commit=True) as cur:
            header_row = _execute_with_cursor(cur, '''
                INSERT INTO parts_lists
                    (name, customer_id, contact_id, salesperson_id, status_id, notes, bom_header_id,
                     date_created, date_modified)
                VALUES (?, ?, NULL, ?, 1, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                RETURNING id
            ''', (list_name, customer_id, salesperson_id, list_notes, bom_id)).fetchone()

            parts_list_id = header_row['id'] if header_row else None
            if not parts_list_id:
                raise RuntimeError("Failed to create parts list header")

            for line_number, row in enumerate(flattened_rows, start=1):
                quantity = _coerce_quantity(row.get('total_quantity'))
                inserted_line = _execute_with_cursor(cur, '''
                    INSERT INTO parts_list_lines (
                        parts_list_id, line_number, customer_part_number, base_part_number,
                        revision, description, quantity, chosen_supplier_id, chosen_cost,
                        chosen_price, chosen_currency_id, chosen_lead_days, chosen_qty,
                        customer_notes, internal_notes, date_created, date_modified
                    )
                    VALUES (?, ?, ?, ?, NULL, NULL, ?, NULL, NULL, NULL, NULL, NULL, ?, NULL, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                    RETURNING id
                ''', (
                    parts_list_id,
                    line_number,
                    row.get('part_number') or row.get('base_part_number'),
                    row.get('base_part_number'),
                    quantity,
                    quantity,
                    f"Generated from BOM {bom_row['name']} (ID {bom_id})."
                )).fetchone()

                parts_list_line_id = inserted_line['id'] if inserted_line else None
                guide_price = row.get('guide_price')
                if parts_list_line_id and guide_price is not None:
                    _execute_with_cursor(cur, '''
                        INSERT INTO customer_quote_lines (
                            parts_list_line_id,
                            target_price_gbp,
                            quoted_status,
                            display_part_number,
                            quoted_part_number
                        )
                        VALUES (?, ?, 'created', ?, ?)
                    ''', (
                        parts_list_line_id,
                        float(guide_price),
                        row.get('part_number') or row.get('base_part_number'),
                        row.get('part_number') or row.get('base_part_number'),
                    ))

        return redirect(url_for('customer_quoting.customer_quote_simple', list_id=parts_list_id))
    except Exception as exc:
        logging.error(f"Error creating parts list from BOM {bom_id}: {exc}", exc_info=True)
        return f"Error creating parts list: {exc}", 500


@bom_bp.route('/line-alternative-suggestions', methods=['GET'])
def line_alternative_suggestions():
    raw_part_number = (request.args.get('base_part_number') or '').strip()
    if not raw_part_number:
        return jsonify({'suggestions': []})

    base_part_number = create_base_part_number(raw_part_number)
    alt_bases = get_global_alternatives(base_part_number)
    if not alt_bases:
        return jsonify({'suggestions': []})

    in_clause, params = _build_in_clause(alt_bases)
    if not in_clause:
        return jsonify({'suggestions': []})

    alt_rows = db_execute(f'''
        SELECT
            pn.base_part_number,
            COALESCE(NULLIF(TRIM(pn.part_number), ''), pn.base_part_number) AS part_number
        FROM part_numbers pn
        WHERE pn.base_part_number IN ({in_clause})
        ORDER BY COALESCE(NULLIF(TRIM(pn.part_number), ''), pn.base_part_number)
    ''', params, fetch='all') or []

    return jsonify({
        'suggestions': [
            {
                'base_part_number': row['base_part_number'],
                'part_number': row['part_number'],
            }
            for row in alt_rows
        ]
    })


@bom_bp.route('/<int:bom_id>/lines/<int:line_id>/details', methods=['GET'])
def bom_line_details(bom_id, line_id):
    try:
        line = db_execute('''
            SELECT
                bl.id,
                bl.bom_header_id,
                bl.base_part_number,
                COALESCE(NULLIF(TRIM(pn.part_number), ''), bl.base_part_number) AS part_number,
                bl.quantity,
                bl.position,
                bl.guide_price,
                bl.child_bom_header_id,
                child_bh.name AS child_bom_name
            FROM bom_lines bl
            LEFT JOIN part_numbers pn ON pn.base_part_number = bl.base_part_number
            LEFT JOIN bom_headers child_bh ON child_bh.id = bl.child_bom_header_id
            WHERE bl.id = ?
              AND bl.bom_header_id = ?
        ''', (line_id, bom_id), fetch='one')

        if not line:
            return jsonify(success=False, message='BOM line not found'), 404

        line_data = dict(line)
        base_part_number = line_data.get('base_part_number')
        linked_parts_list_lines = _get_bom_line_linked_parts_list_details(bom_id, base_part_number) if base_part_number else []
        linked_list_ids = [item['parts_list_id'] for item in linked_parts_list_lines if item.get('parts_list_id') is not None]
        supplier_quotes = _get_supplier_quote_offers_for_base_part(base_part_number, list_ids=linked_list_ids or None, limit=12) if base_part_number else []
        child_kit_details = None
        if line_data.get('child_bom_header_id'):
            child_kit_details = _build_child_kit_panel_details(
                bom_id,
                int(line_data['child_bom_header_id']),
                line_multiplier=_coerce_quantity(line_data.get('quantity')),
            )

        return jsonify({
            'success': True,
            'line': line_data,
            'linked_parts_list_lines': [dict(item) for item in linked_parts_list_lines],
            'supplier_quotes': [dict(item) for item in supplier_quotes],
            'child_kit_details': child_kit_details,
        })
    except Exception as exc:
        logging.error(f"Error loading BOM line details {line_id} for BOM {bom_id}: {exc}", exc_info=True)
        return jsonify(success=False, message=str(exc)), 500


@bom_bp.route('/import_components/<int:bom_id>', methods=['POST'])
def import_components(bom_id):
    """Handle file upload for importing components into existing BOM"""
    try:
        if request.is_json:
            rows = (request.json or {}).get('rows') or []
            if not rows:
                return jsonify({'error': 'No pasted rows provided'}), 400
            df = pd.DataFrame(rows)
            filename = 'pasted-grid'
            logging.info(f"Starting grid import for BOM {bom_id} with {len(df)} rows")
        else:
            if 'file' not in request.files:
                return jsonify({'error': 'No file provided'}), 400

            file = request.files['file']
            if not file.filename:
                return jsonify({'error': 'No file selected'}), 400

            filename = secure_filename(file.filename)
            logging.info(f"Starting import for BOM {bom_id} from file: {filename}")
            df = _load_bom_dataframe(file, filename)
    except Exception as exc:
        logging.error(f"Failed to read file for BOM {bom_id}: {exc}")
        return jsonify({'error': f"Failed to read file: {exc}"}), 400

    logging.info(f"Loaded dataframe with {len(df)} rows")
    logging.info(f"Columns in file: {list(df.columns)}")

    with db_cursor(commit=True) as cur:
        max_position_row = _execute_with_cursor(cur, '''
            SELECT COALESCE(MAX(position), 0) as max_pos
            FROM bom_lines
            WHERE bom_header_id = ?
        ''', (bom_id,)).fetchone()
        max_position = max_position_row['max_pos'] if max_position_row else 0
        logging.info(f"Current max position: {max_position}")
        imported_count, skipped_count = _import_bom_dataframe(cur, bom_id, df, start_position=max_position)

    logging.info(f"Successfully imported: {imported_count} components")
    logging.info(f"Skipped (empty part_number): {skipped_count} rows")

    return jsonify({
        'status': 'success',
        'message': f"Successfully imported {imported_count} components (skipped {skipped_count})"
    }), 200


@bom_bp.route('/create-child-kit', methods=['POST'])
def create_child_kit():
    data = request.json or {}
    name = (data.get('name') or '').strip()
    description = (data.get('description') or '').strip() or None
    rows = data.get('rows') or []

    if not name:
        return jsonify({'error': 'Kit name is required'}), 400

    try:
        with db_cursor(commit=True) as cur:
            insert_query = _with_returning_clause('''
                INSERT INTO bom_headers (name, description, type)
                VALUES (?, ?, 'kit')
            ''')
            _execute_with_cursor(cur, insert_query, [name, description])
            bom_id = _fetch_inserted_id(cur)

            if not bom_id:
                raise RuntimeError("Failed to create child kit")

            if rows:
                df = pd.DataFrame(rows)
                _import_bom_dataframe(cur, bom_id, df)

        return jsonify({
            'status': 'success',
            'bom_id': bom_id,
            'name': name,
            'message': f'Created child kit {name}'
        }), 200
    except Exception as exc:
        logging.error(f"Error creating child kit: {exc}", exc_info=True)
        return jsonify({'error': str(exc)}), 400


@bom_bp.route('/api/customers/search')
def search_customers():
    search = request.args.get('q', '')
    customers = db_execute('''
        SELECT id, name 
        FROM customers 
        WHERE name LIKE ? 
        ORDER BY name 
        LIMIT 10
    ''', ('%' + search + '%',), fetch='all')

    return jsonify({
        'results': [{'id': c['id'], 'text': c['name']} for c in customers]
    })


@bom_bp.route('/update/<int:bom_id>', methods=['POST'])
def update_bom(bom_id):
    data = request.json or {}

    try:
        with db_cursor(commit=True) as cur:
            existing_lines = _execute_with_cursor(cur, '''
                SELECT id
                FROM bom_lines
                WHERE bom_header_id = ?
            ''', (bom_id,)).fetchall()
            existing_line_ids = {int(line['id']) for line in existing_lines if line.get('id') is not None}
            submitted_line_ids = set()

            for component in data.get('components') or []:
                line_id = component.get('line_id')
                raw_part_number = (component.get('base_part_number') or '').strip()
                base_part_number = create_base_part_number(raw_part_number) if raw_part_number else ''
                position = component.get('position', 0)
                quantity = component.get('quantity', 0)
                guide_price = component.get('guide_price')
                child_bom_header_id = component.get('child_bom_header_id')
                accepted_alternates = component.get('accepted_alternates') or []

                if base_part_number:
                    part = _execute_with_cursor(cur, '''
                        SELECT base_part_number 
                        FROM part_numbers 
                        WHERE base_part_number = ?
                    ''', (base_part_number,)).fetchone()
                    if not part:
                        _execute_with_cursor(cur, '''
                            INSERT INTO part_numbers (part_number, base_part_number) 
                            VALUES (?, ?)
                        ''', (raw_part_number, base_part_number))

                if line_id:
                    line_id = int(line_id)
                    submitted_line_ids.add(line_id)
                    _execute_with_cursor(cur, '''
                        UPDATE bom_lines 
                        SET quantity = ?,
                            position = ?,
                            base_part_number = ?,
                            guide_price = ?,
                            child_bom_header_id = ?
                        WHERE id = ?
                    ''', (
                        quantity,
                        position,
                        base_part_number,
                        guide_price,
                        child_bom_header_id,
                        line_id
                    ))
                else:
                    inserted_line = _execute_with_cursor(cur, '''
                        INSERT INTO bom_lines (
                            bom_header_id, 
                            base_part_number, 
                            quantity, 
                            position,
                            guide_price,
                            child_bom_header_id
                        ) VALUES (?, ?, ?, ?, ?, ?)
                        RETURNING id
                    ''', (
                        bom_id,
                        base_part_number,
                        quantity,
                        position,
                        guide_price,
                        child_bom_header_id
                    )).fetchone()
                    line_id = inserted_line['id'] if inserted_line else None
                    if line_id is not None:
                        submitted_line_ids.add(int(line_id))

                if line_id is not None:
                    _execute_with_cursor(cur, 'DELETE FROM bom_line_accepted_alternates WHERE bom_line_id = ?', (line_id,))
                    for rank, alt in enumerate(accepted_alternates, start=1):
                        raw_alt = (str(alt).strip() if alt is not None else '')
                        if not raw_alt:
                            continue
                        alt_base = create_base_part_number(raw_alt)
                        alt_part = _execute_with_cursor(cur,
                            'SELECT base_part_number FROM part_numbers WHERE base_part_number = ?',
                            (alt_base,)
                        ).fetchone()
                        if not alt_part:
                            _execute_with_cursor(cur,
                                'INSERT INTO part_numbers (part_number, base_part_number) VALUES (?, ?)',
                                (raw_alt, alt_base)
                            )
                        _execute_with_cursor(cur, '''
                            INSERT INTO bom_line_accepted_alternates (bom_line_id, alt_base_part_number, preference_rank)
                            VALUES (?, ?, ?)
                        ''', (line_id, alt_base, rank))

            line_ids_to_delete = sorted(existing_line_ids - submitted_line_ids)
            if line_ids_to_delete:
                in_clause, params = _build_in_clause(line_ids_to_delete)
                _execute_with_cursor(cur, f'''
                    DELETE FROM bom_line_accepted_alternates
                    WHERE bom_line_id IN ({in_clause})
                ''', params)
                _execute_with_cursor(cur, f'''
                    DELETE FROM bom_lines
                    WHERE id IN ({in_clause})
                ''', params)

        return jsonify({
            'status': 'success',
            'message': 'BOM updated successfully'
        })

    except Exception as exc:
        logging.error(f"Error updating BOM {bom_id}: {exc}", exc_info=True)
        return jsonify({
            'status': 'error',
            'message': str(exc)
        }), 400


@bom_bp.route('/customers/remove/<int:bom_id>', methods=['POST'])
def remove_customer(bom_id):
    data = request.json or {}

    try:
        with db_cursor(commit=True) as cur:
            _execute_with_cursor(cur, '''
                DELETE FROM customer_boms 
                WHERE bom_header_id = ? AND customer_id = ?
            ''', (bom_id, data.get('customer_id')))

        return jsonify({
            'status': 'success',
            'message': 'Customer removed from BOM'
        })

    except Exception as exc:
        logging.error(f"Error removing customer from BOM {bom_id}: {exc}", exc_info=True)
        return jsonify({
            'status': 'error',
            'message': str(exc)
        }), 400


@bom_bp.route('/rename/<int:bom_id>', methods=['POST'])
def rename_bom(bom_id):
    data = request.json or {}
    name = (data.get('name') or '').strip()
    description = (data.get('description') or '').strip() or None

    if not name:
        return jsonify({
            'status': 'error',
            'message': 'BOM name is required'
        }), 400

    try:
        with db_cursor(commit=True) as cur:
            existing = _execute_with_cursor(cur, 'SELECT id FROM bom_headers WHERE id = ?', (bom_id,)).fetchone()
            if not existing:
                return jsonify({
                    'status': 'error',
                    'message': 'BOM not found'
                }), 404

            _execute_with_cursor(cur, '''
                UPDATE bom_headers
                SET name = ?, description = ?
                WHERE id = ?
            ''', (name, description, bom_id))

        return jsonify({
            'status': 'success',
            'message': 'BOM updated successfully'
        })
    except Exception as exc:
        logging.error(f"Error renaming BOM {bom_id}: {exc}", exc_info=True)
        return jsonify({
            'status': 'error',
            'message': str(exc)
        }), 400


@bom_bp.route('/customers/update_ref/<int:bom_id>', methods=['POST'])
def update_customer_ref(bom_id):
    data = request.json or {}

    try:
        with db_cursor(commit=True) as cur:
            _execute_with_cursor(cur, '''
                UPDATE customer_boms 
                SET reference = ?
                WHERE bom_header_id = ? AND customer_id = ?
            ''', (
                data.get('reference', ''),
                bom_id,
                data.get('customer_id')
            ))

        return jsonify({
            'status': 'success',
            'message': 'Customer reference updated'
        })

    except Exception as exc:
        logging.error(f"Error updating customer reference for BOM {bom_id}: {exc}", exc_info=True)
        return jsonify({
            'status': 'error',
            'message': str(exc)
        }), 400


@bom_bp.route('/components/add/<int:bom_id>', methods=['POST'])
def add_component(bom_id):
    data = request.json or {}

    try:
        with db_cursor(commit=True) as cur:
            bom = _execute_with_cursor(cur, 'SELECT id FROM bom_headers WHERE id = ?', (bom_id,)).fetchone()
            if not bom:
                return jsonify({
                    'status': 'error',
                    'message': 'BOM not found'
                }), 404

            raw_part_number = (data.get('base_part_number') or '').strip()
            logging.info(f"Raw part number before base conversion: '{raw_part_number}'")

            base_part_number = create_base_part_number(raw_part_number) if raw_part_number else ''
            logging.info(f"Base part number after conversion: '{base_part_number}'")

            if base_part_number:
                part = _execute_with_cursor(cur,
                    'SELECT base_part_number FROM part_numbers WHERE base_part_number = ?',
                    (base_part_number,)
                ).fetchone()
                if not part:
                    _execute_with_cursor(cur,
                        'INSERT INTO part_numbers (part_number, base_part_number) VALUES (?, ?)',
                        (raw_part_number, base_part_number)
                    )

            max_position_row = _execute_with_cursor(cur, '''
                SELECT COALESCE(MAX(position), 0) as max_pos
                FROM bom_lines
                WHERE bom_header_id = ?
            ''', (bom_id,)).fetchone()
            max_position = max_position_row['max_pos'] if max_position_row else 0

            guide_price_value = data.get('guide_price')
            child_bom_header_id = data.get('child_bom_header_id')
            accepted_alternates = data.get('accepted_alternates') or []
            logging.info(f"guide_price from add_component: {guide_price_value} (type: {type(guide_price_value)})")

            insert_line = _with_returning_clause('''
                INSERT INTO bom_lines (
                    bom_header_id,
                    base_part_number,
                    quantity,
                    position,
                    guide_price,
                    child_bom_header_id
                ) VALUES (?, ?, ?, ?, ?, ?)
            ''')
            _execute_with_cursor(cur, insert_line, (
                bom_id,
                base_part_number,
                data.get('quantity', 1),
                max_position + 10,
                guide_price_value,
                child_bom_header_id
            ))
            new_line_id = _fetch_inserted_id(cur)
            if not new_line_id:
                raise RuntimeError("Failed to insert BOM line")

            for rank, alt in enumerate(accepted_alternates, start=1):
                raw_alt = (str(alt).strip() if alt is not None else '')
                if not raw_alt:
                    continue
                alt_base = create_base_part_number(raw_alt)
                alt_part = _execute_with_cursor(
                    cur,
                    'SELECT base_part_number FROM part_numbers WHERE base_part_number = ?',
                    (alt_base,)
                ).fetchone()
                if not alt_part:
                    _execute_with_cursor(
                        cur,
                        'INSERT INTO part_numbers (part_number, base_part_number) VALUES (?, ?)',
                        (raw_alt, alt_base)
                    )
                _execute_with_cursor(cur, '''
                    INSERT INTO bom_line_accepted_alternates (bom_line_id, alt_base_part_number, preference_rank)
                    VALUES (?, ?, ?)
                ''', (new_line_id, alt_base, rank))

            new_component = _execute_with_cursor(cur, '''
                SELECT bl.*,
                       pn.part_number,
                       bp.offer_line_id,
                       ol.price as current_price,
                       ol.lead_time as current_lead_time,
                       s.name as supplier_name,
                       c.currency_code,
                       bl.guide_price, bl.child_bom_header_id, child_bh.name AS child_bom_name
                FROM bom_lines bl
                LEFT JOIN part_numbers pn ON bl.base_part_number = pn.base_part_number
                LEFT JOIN bom_pricing bp ON bl.id = bp.bom_line_id
                LEFT JOIN offer_lines ol ON bp.offer_line_id = ol.id
                LEFT JOIN offers o ON ol.offer_id = o.id
                LEFT JOIN suppliers s ON o.supplier_id = s.id
                LEFT JOIN currencies c ON o.currency_id = c.id
                LEFT JOIN bom_headers child_bh ON child_bh.id = bl.child_bom_header_id
                WHERE bl.id = ?
            ''', (new_line_id,)).fetchone()

            if new_component is None:
                raise RuntimeError("Failed to retrieve newly created component")

        component_data = {
            'base_part_number': new_component['part_number'] or new_component['base_part_number'] or '',
            'quantity': new_component['quantity'] or 0,
            'position': new_component['position'] or 0,
            'guide_price': new_component['guide_price'] or 0.0,
            'current_price': new_component.get('current_price') or 0.0,
            'supplier_name': new_component.get('supplier_name') or '',
            'currency_code': new_component.get('currency_code') or '',
            'lead_time': new_component.get('current_lead_time') or '',
            'child_bom_header_id': new_component.get('child_bom_header_id'),
            'child_bom_name': new_component.get('child_bom_name'),
            'accepted_alternates': [str(a).strip() for a in accepted_alternates if str(a).strip()]
        }

        return jsonify({
            'status': 'success',
            'component': component_data
        })

    except Exception as exc:
        logging.error(f"Error adding component to BOM {bom_id}: {exc}", exc_info=True)
        return jsonify({
            'status': 'error',
            'message': str(exc)
        }), 400


@bom_bp.route('/customers/add/<int:bom_id>', methods=['POST'])
def add_customer(bom_id):
    data = request.json or {}

    try:
        with db_cursor(commit=True) as cur:
            _execute_with_cursor(cur, '''
                INSERT INTO customer_boms (bom_header_id, customer_id, reference)
                VALUES (?, ?, ?)
            ''', (
                bom_id,
                data['customer_id'],
                data.get('reference', '')
            ))

        return jsonify({
            'status': 'success',
            'message': 'Customer added to BOM'
        })

    except Exception as exc:
        logging.error(f"Error adding customer to BOM {bom_id}: {exc}", exc_info=True)
        return jsonify({
            'status': 'error',
            'message': str(exc)
        }), 400


@bom_bp.route('/delete/<int:bom_id>', methods=['POST'])
def delete_bom(bom_id):
    try:
        with db_cursor(commit=True) as cur:
            _execute_with_cursor(cur, '''
                DELETE FROM bom_pricing
                WHERE bom_line_id IN (
                    SELECT id FROM bom_lines WHERE bom_header_id = ?
                )
            ''', (bom_id,))
            _execute_with_cursor(cur, 'DELETE FROM bom_lines WHERE bom_header_id = ?', (bom_id,))
            _execute_with_cursor(cur, 'DELETE FROM bom_files WHERE bom_header_id = ?', (bom_id,))
            _execute_with_cursor(cur, 'DELETE FROM customer_boms WHERE bom_header_id = ?', (bom_id,))
            _execute_with_cursor(cur, 'DELETE FROM bom_revisions WHERE bom_header_id = ?', (bom_id,))
            _execute_with_cursor(cur, 'DELETE FROM bom_headers WHERE id = ?', (bom_id,))

        return jsonify({
            'status': 'success',
            'message': 'BOM deleted successfully'
        })
    except Exception as exc:
        logging.error(f"Error deleting BOM {bom_id}: {exc}", exc_info=True)
        return jsonify({
            'status': 'error',
            'message': str(exc)
        }), 400
