import os
import json
import logging
import re
from html import escape
from datetime import datetime, timedelta, date
from decimal import Decimal
from math import ceil
from flask import Blueprint, render_template, request, jsonify, session
from flask_login import current_user, login_required
from db import db_cursor, execute as db_execute
from models import convert_currency

purchase_suggestions_bp = Blueprint('purchase_suggestions', __name__, url_prefix='/purchase-suggestions')

PURCHASE_REPORT_CONFIG_KEY = 'purchase_suggestions_email_config'
SPROUTT_ADMIN_EMAIL = 'admin@sproutt.io'
SPROUTT_ADMIN_NAME = 'Sproutt Admin'
PURCHASE_REPORT_DEFAULT_CONFIG = {
    'enabled': False,
    'recipients': '',
    'frequency_days': 1,
    'quote_period_days': 30,
    'sales_period_days': 90,
    'quote_min_occurrences': 2,
    'quote_max_sales_ratio_percent': 25,
    'frequent_min_orders': 3,
    'max_rows': 50,
    'include_unordered_quotes': True,
    'include_frequent_sales': True,
    'include_stock_not_won': True,
    'only_out_of_stock': True,
    'frequent_only_out_of_stock': True,
    'last_sent_at': None,
}


def _load_email_report_config():
    row = db_execute(
        'SELECT value FROM app_settings WHERE key = ?',
        (PURCHASE_REPORT_CONFIG_KEY,),
        fetch='one',
    )
    config = dict(PURCHASE_REPORT_DEFAULT_CONFIG)
    raw_value = row.get('value') if row else None
    if raw_value:
        try:
            saved = json.loads(raw_value)
            if isinstance(saved, dict):
                config.update(saved)
        except (TypeError, ValueError):
            logging.warning('Invalid purchase suggestions email config JSON in app_settings')

    config['frequency_days'] = max(1, min(int(config.get('frequency_days') or 1), 30))
    config['quote_period_days'] = max(1, min(int(config.get('quote_period_days') or 30), 365))
    config['sales_period_days'] = max(1, min(int(config.get('sales_period_days') or 90), 730))
    config['quote_min_occurrences'] = max(1, min(int(config.get('quote_min_occurrences') or 2), 100))
    config['quote_max_sales_ratio_percent'] = max(0, min(float(config.get('quote_max_sales_ratio_percent') or 0), 100))
    config['frequent_min_orders'] = max(1, min(int(config.get('frequent_min_orders') or 3), 100))
    config['max_rows'] = max(1, min(int(config.get('max_rows') or 50), 500))
    config['enabled'] = bool(config.get('enabled'))
    config['include_unordered_quotes'] = bool(config.get('include_unordered_quotes'))
    config['include_frequent_sales'] = bool(config.get('include_frequent_sales'))
    config['include_stock_not_won'] = bool(config.get('include_stock_not_won', True))
    config['only_out_of_stock'] = bool(config.get('only_out_of_stock', True))
    config['frequent_only_out_of_stock'] = bool(config.get('frequent_only_out_of_stock', config.get('only_out_of_stock', True)))
    config['recipients'] = str(config.get('recipients') or '').strip()
    return config


def _save_email_report_config(config):
    stored = dict(PURCHASE_REPORT_DEFAULT_CONFIG)
    stored.update(config or {})
    db_execute(
        """
        INSERT INTO app_settings (key, value)
        VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = EXCLUDED.value
        """,
        (PURCHASE_REPORT_CONFIG_KEY, json.dumps(stored, default=str)),
        commit=True,
    )
    return _load_email_report_config()


def _apply_email_report_payload(config, payload):
    payload = payload or {}
    config.update({
        'enabled': bool(payload.get('enabled')),
        'recipients': str(payload.get('recipients') or '').strip(),
        'frequency_days': max(1, min(int(payload.get('frequency_days') or 1), 30)),
        'quote_period_days': max(1, min(int(payload.get('quote_period_days') or 30), 365)),
        'sales_period_days': max(1, min(int(payload.get('sales_period_days') or 90), 730)),
        'quote_min_occurrences': max(1, min(int(payload.get('quote_min_occurrences') or 2), 100)),
        'quote_max_sales_ratio_percent': max(0, min(float(payload.get('quote_max_sales_ratio_percent') or 0), 100)),
        'frequent_min_orders': max(1, min(int(payload.get('frequent_min_orders') or 3), 100)),
        'max_rows': max(1, min(int(payload.get('max_rows') or 50), 500)),
        'include_unordered_quotes': bool(payload.get('include_unordered_quotes', True)),
        'include_stock_not_won': bool(payload.get('include_stock_not_won', True)),
        'include_frequent_sales': bool(payload.get('include_frequent_sales', True)),
        'only_out_of_stock': bool(payload.get('only_out_of_stock', True)),
        'frequent_only_out_of_stock': bool(payload.get('frequent_only_out_of_stock', True)),
    })
    return config


def _split_email_recipients(value):
    recipients = []
    for chunk in str(value or '').replace(';', ',').split(','):
        email = chunk.strip()
        if email and '@' in email:
            recipients.append(email)
    return recipients


def _current_user_id():
    try:
        if current_user and getattr(current_user, 'is_authenticated', False):
            return getattr(current_user, 'id', None)
    except RuntimeError:
        return None
    return None


def _current_user_display_name():
    try:
        if current_user and getattr(current_user, 'is_authenticated', False):
            username = getattr(current_user, 'username', '') or ''
            return username.replace('_', ' ').strip().title() or f'User {getattr(current_user, "id", "")}'
    except RuntimeError:
        return 'System'
    return 'System'


def _report_public_base_url():
    configured = (os.getenv('PURCHASE_REPORT_BASE_URL') or '').strip().rstrip('/')
    if configured:
        return configured
    try:
        if request:
            return request.host_url.rstrip('/')
    except RuntimeError:
        pass
    return 'https://mgc.sproutt.io'


def _report_review_url(run_id):
    return f"{_report_public_base_url()}/purchase-suggestions/reports/{run_id}"


def _json_default(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return str(value)


def _normalize_part_key(value):
    return re.sub(r'[^A-Z0-9]', '', str(value or '').upper())


REPORT_SECTION_KEYS = (
    ('quoted_not_won', 'unordered_quotes'),
    ('stock_not_won', 'stock_not_won'),
    ('frequent_sales', 'frequent_sales'),
)


REPORT_SECTION_LABELS = {
    'quoted_not_won': 'Repeatedly quoted parts not yet ordered',
    'stock_not_won': 'Quoted from stock but not won',
    'frequent_sales': 'Frequent sales order parts',
}


def _parse_datetime(value):
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace('Z', '+00:00')).replace(tzinfo=None)
    except ValueError:
        return None


def _date_cutoff(days):
    return datetime.utcnow().date() - timedelta(days=max(1, int(days or 1)))


def _normalized_base_sql(column_sql):
    if _using_postgres():
        return f"regexp_replace(UPPER(COALESCE({column_sql}, '')), '[^A-Z0-9]', '', 'g')"
    expr = f"UPPER(COALESCE({column_sql}, ''))"
    for char in ("-", " ", "/", "_", "."):
        expr = f"REPLACE({expr}, '{char}', '')"
    return expr


def _format_source_label(source_type):
    source_type = (source_type or '').strip().lower()
    labels = {
        'quote': 'Supplier offer',
        'stock': 'Stock',
        'manual': 'Manual cost',
        'manual_cost': 'Manual cost',
        'customer_quote': 'Customer quote',
    }
    return labels.get(source_type, source_type.replace('_', ' ').title() if source_type else 'Unknown')


def _money(value):
    numeric = _safe_float(value)
    return f'£{numeric:,.2f}' if numeric is not None else '-'


def _pct(value):
    numeric = _safe_float(value)
    return f'{numeric:.1f}%' if numeric is not None else '-'


def _source_detail(row):
    source_label = _format_source_label(row.get('chosen_source_type'))
    if (row.get('chosen_source_type') or '').lower() == 'quote':
        supplier = row.get('source_supplier_name') or row.get('chosen_supplier_name') or 'Supplier'
        ref = row.get('source_quote_reference') or row.get('chosen_source_reference') or ''
        quoted_date = _stringify_date(row.get('source_quote_date'))
        bits = [supplier]
        if ref:
            bits.append(f'ref {ref}')
        if quoted_date:
            bits.append(quoted_date)
        return f"{source_label}: " + ' · '.join(bits)
    if (row.get('chosen_source_type') or '').lower() == 'stock':
        return 'Stock allocation'
    return source_label


def _normalize_report_row(row):
    item = dict(row)
    for key in (
        'quoted_on', 'date_created', 'source_quote_date', 'last_sale_date',
        'first_sale_date', 'latest_quote_date', 'first_quoted_on',
        'latest_quoted_on'
    ):
        if key in item:
            item[key] = _stringify_date(item.get(key))
    for key in (
        'quantity', 'chosen_qty', 'base_cost_gbp', 'delivery_per_unit',
        'delivery_per_line', 'margin_percent', 'quote_price_gbp', 'chosen_cost',
        'source_unit_price', 'ordered_qty_after_quote', 'sales_order_count',
        'total_sales_qty', 'avg_sale_price', 'latest_margin_percent',
        'latest_base_cost_gbp', 'stock_quantity', 'latest_quote_price_gbp',
        'customer_count', 'quote_line_count', 'total_quoted_qty',
        'quote_occurrence_count', 'sales_order_count_in_period',
        'sales_order_occurrence_count_in_period', 'sales_ratio'
    ):
        if key in item:
            item[key] = _coerce_numeric(item.get(key))
    item['source_label'] = _format_source_label(item.get('chosen_source_type'))
    item['source_detail'] = _source_detail(item)
    return item

def _load_unordered_customer_quote_report(
    cursor,
    period_days=30,
    sales_period_days=90,
    max_rows=50,
    min_occurrences=2,
    max_sales_ratio_percent=25,
    only_out_of_stock=True,
    stock_only=False,
):
    cutoff = _date_cutoff(period_days)
    sales_cutoff = _date_cutoff(sales_period_days)
    min_occurrences = max(1, int(min_occurrences or 1))
    max_sales_ratio = max(0.0, min(float(max_sales_ratio_percent or 0), 100.0)) / 100.0
    pll_base_key = _normalized_base_sql('pll.base_part_number')
    stock_base_key = _normalized_base_sql('base_part_number')
    sales_base_key = _normalized_base_sql('sol.base_part_number')
    rows = _execute_with_cursor(
        cursor,
        f"""
        WITH eligible_quote_lines AS (
            SELECT
                cql.id AS quote_line_id,
                pll.id AS parts_list_line_id,
                pl.id AS parts_list_id,
                pl.name AS parts_list_name,
                c.id AS customer_id,
                c.name AS customer_name,
                {pll_base_key} AS base_part_number,
                pll.base_part_number AS raw_base_part_number,
                COALESCE(NULLIF(cql.quoted_part_number, ''), NULLIF(cql.display_part_number, ''), pll.customer_part_number, pll.base_part_number) AS part_number,
                pn.system_part_number,
                cql.manufacturer,
                COALESCE(pll.chosen_qty, pll.quantity) AS quantity,
                COALESCE(cql.quoted_on, cql.date_created) AS quoted_date,
                cql.quoted_on,
                cql.date_created,
                cql.base_cost_gbp,
                cql.delivery_per_unit,
                cql.delivery_per_line,
                cql.margin_percent,
                cql.quote_price_gbp,
                cql.lead_days,
                pll.chosen_source_type,
                pll.chosen_source_reference,
                pll.chosen_cost,
                s.name AS chosen_supplier_name,
                psq.quote_reference AS source_quote_reference,
                psq.quote_date AS source_quote_date,
                sqs.name AS source_supplier_name,
                psql.unit_price AS source_unit_price,
                curr.currency_code AS source_currency_code
            FROM customer_quote_lines cql
            JOIN parts_list_lines pll ON pll.id = cql.parts_list_line_id
            JOIN parts_lists pl ON pl.id = pll.parts_list_id
            LEFT JOIN customers c ON c.id = pl.customer_id
            LEFT JOIN part_numbers pn ON pn.base_part_number = pll.base_part_number
            LEFT JOIN suppliers s ON s.id = pll.chosen_supplier_id
            LEFT JOIN parts_list_supplier_quote_lines psql
                ON pll.chosen_source_type = 'quote'
               AND CAST(psql.id AS TEXT) = pll.chosen_source_reference
            LEFT JOIN parts_list_supplier_quotes psq ON psq.id = psql.supplier_quote_id
            LEFT JOIN suppliers sqs ON sqs.id = psq.supplier_id
            LEFT JOIN currencies curr ON curr.id = psq.currency_id
            WHERE COALESCE(cql.is_no_bid::int, 0) = 0
              AND COALESCE(cql.quoted_status, '') = 'quoted'
              AND cql.quote_price_gbp IS NOT NULL
              AND cql.quote_price_gbp > 0
              AND {pll_base_key} <> ''
              AND COALESCE(cql.quoted_on, cql.date_created) >= ?
              AND (? = 0 OR LOWER(COALESCE(pll.chosen_source_type, '')) = 'stock')
        ), part_stats AS (
            SELECT
                base_part_number,
                COUNT(*) AS quote_line_count,
                COUNT(DISTINCT parts_list_id) AS quote_occurrence_count,
                COUNT(DISTINCT customer_id) AS customer_count,
                SUM(COALESCE(quantity, 0)) AS total_quoted_qty,
                MIN(quoted_date) AS first_quoted_on,
                MAX(quoted_date) AS latest_quoted_on
            FROM eligible_quote_lines
            GROUP BY base_part_number
            HAVING COUNT(DISTINCT parts_list_id) >= ?
        ), latest_line AS (
            SELECT
                eql.*,
                ROW_NUMBER() OVER (
                    PARTITION BY eql.base_part_number
                    ORDER BY eql.quoted_date DESC, eql.quote_line_id DESC
                ) AS rn
            FROM eligible_quote_lines eql
        ), stock AS (
            SELECT {stock_base_key} AS base_part_number, SUM(available_quantity) AS stock_quantity
            FROM stock_movements
            WHERE movement_type = 'IN'
              AND available_quantity > 0
              AND {stock_base_key} <> ''
            GROUP BY {stock_base_key}
        ), recent_sales AS (
            SELECT
                {sales_base_key} AS base_part_number,
                COUNT(*) AS sales_order_count_in_period,
                COUNT(DISTINCT so.id) AS sales_order_occurrence_count_in_period
            FROM sales_order_lines sol
            JOIN sales_orders so ON so.id = sol.sales_order_id
            WHERE {sales_base_key} <> ''
              AND so.date_entered >= ?
            GROUP BY {sales_base_key}
        )
        SELECT
            ll.quote_line_id,
            ll.parts_list_line_id,
            ll.parts_list_id,
            ll.parts_list_name,
            ll.customer_id,
            ll.customer_name,
            part_stats.base_part_number,
            ll.part_number,
            ll.system_part_number,
            ll.manufacturer,
            ll.quantity,
            part_stats.total_quoted_qty,
            part_stats.quote_line_count,
            part_stats.quote_occurrence_count,
            part_stats.customer_count,
            part_stats.first_quoted_on,
            part_stats.latest_quoted_on,
            COALESCE(stock.stock_quantity, 0) AS stock_quantity,
            COALESCE(recent_sales.sales_order_count_in_period, 0) AS sales_order_count_in_period,
            COALESCE(recent_sales.sales_order_occurrence_count_in_period, 0) AS sales_order_occurrence_count_in_period,
            CASE
                WHEN part_stats.quote_occurrence_count > 0
                THEN COALESCE(recent_sales.sales_order_occurrence_count_in_period, 0) * 1.0 / part_stats.quote_occurrence_count
                ELSE 0
            END AS sales_ratio,
            ll.quoted_on,
            ll.date_created,
            ll.base_cost_gbp,
            ll.delivery_per_unit,
            ll.delivery_per_line,
            ll.margin_percent,
            ll.quote_price_gbp,
            ll.lead_days,
            ll.chosen_source_type,
            ll.chosen_source_reference,
            ll.chosen_cost,
            ll.chosen_supplier_name,
            ll.source_quote_reference,
            ll.source_quote_date,
            ll.source_supplier_name,
            ll.source_unit_price,
            ll.source_currency_code,
            0 AS ordered_qty_after_quote,
            NULL AS last_order_date
        FROM part_stats
        JOIN latest_line ll ON ll.base_part_number = part_stats.base_part_number AND ll.rn = 1
        LEFT JOIN stock ON stock.base_part_number = part_stats.base_part_number
        LEFT JOIN recent_sales ON recent_sales.base_part_number = part_stats.base_part_number
        WHERE (? = 0 OR COALESCE(stock.stock_quantity, 0) <= 0)
          AND NOT EXISTS (
              SELECT 1
              FROM purchase_report_ignored_parts prip
              WHERE prip.base_part_number = part_stats.base_part_number
          )
          AND (
              CASE
                  WHEN part_stats.quote_occurrence_count > 0
                  THEN COALESCE(recent_sales.sales_order_occurrence_count_in_period, 0) * 1.0 / part_stats.quote_occurrence_count
                  ELSE 0
              END
          ) <= ?
        ORDER BY part_stats.customer_count DESC, part_stats.quote_occurrence_count DESC, part_stats.quote_line_count DESC, part_stats.latest_quoted_on DESC, ll.quote_price_gbp DESC
        LIMIT ?
        """,
        (
            cutoff,
            1 if stock_only else 0,
            min_occurrences,
            sales_cutoff,
            1 if only_out_of_stock else 0,
            max_sales_ratio,
            int(max_rows),
        ),
        fetch='all',
    ) or []
    return [_normalize_report_row(row) for row in rows]


def _load_stock_sourced_unwon_quote_report(cursor, period_days=30, max_rows=50):
    cutoff = _date_cutoff(period_days)
    pll_base_key = _normalized_base_sql('pll.base_part_number')
    stock_base_key = _normalized_base_sql('base_part_number')
    sale_base_key = _normalized_base_sql('sol_ord.base_part_number')
    rows = _execute_with_cursor(
        cursor,
        f"""
        WITH stock AS (
            SELECT {stock_base_key} AS base_part_number, SUM(available_quantity) AS stock_quantity
            FROM stock_movements
            WHERE movement_type = 'IN'
              AND available_quantity > 0
              AND {stock_base_key} <> ''
            GROUP BY {stock_base_key}
        )
        SELECT
            cql.id AS quote_line_id,
            pll.id AS parts_list_line_id,
            pl.id AS parts_list_id,
            pl.name AS parts_list_name,
            c.id AS customer_id,
            c.name AS customer_name,
            {pll_base_key} AS base_part_number,
            pll.base_part_number AS raw_base_part_number,
            COALESCE(NULLIF(cql.quoted_part_number, ''), NULLIF(cql.display_part_number, ''), pll.customer_part_number, pll.base_part_number) AS part_number,
            pn.system_part_number,
            cql.manufacturer,
            COALESCE(pll.chosen_qty, pll.quantity) AS quantity,
            COALESCE(pll.chosen_qty, pll.quantity) AS total_quoted_qty,
            1 AS quote_line_count,
            1 AS quote_occurrence_count,
            1 AS customer_count,
            COALESCE(cql.quoted_on, cql.date_created) AS latest_quoted_on,
            cql.quoted_on,
            cql.date_created,
            cql.base_cost_gbp,
            cql.delivery_per_unit,
            cql.delivery_per_line,
            cql.margin_percent,
            cql.quote_price_gbp,
            cql.lead_days,
            pll.chosen_source_type,
            pll.chosen_source_reference,
            pll.chosen_cost,
            'Stock' AS chosen_supplier_name,
            NULL AS source_quote_reference,
            NULL AS source_quote_date,
            NULL AS source_supplier_name,
            NULL AS source_unit_price,
            NULL AS source_currency_code,
            COALESCE(stock.stock_quantity, 0) AS stock_quantity,
            0 AS sales_order_count_in_period,
            0 AS sales_order_occurrence_count_in_period,
            NULL AS sales_ratio,
            0 AS ordered_qty_after_quote,
            NULL AS last_order_date
        FROM customer_quote_lines cql
        JOIN parts_list_lines pll ON pll.id = cql.parts_list_line_id
        JOIN parts_lists pl ON pl.id = pll.parts_list_id
        LEFT JOIN customers c ON c.id = pl.customer_id
        LEFT JOIN part_numbers pn ON pn.base_part_number = pll.base_part_number
        LEFT JOIN stock ON stock.base_part_number = {pll_base_key}
        WHERE COALESCE(cql.is_no_bid::int, 0) = 0
          AND COALESCE(cql.quoted_status, '') = 'quoted'
          AND cql.quote_price_gbp IS NOT NULL
          AND cql.quote_price_gbp > 0
          AND LOWER(COALESCE(pll.chosen_source_type, '')) = 'stock'
          AND {pll_base_key} <> ''
          AND COALESCE(cql.quoted_on, cql.date_created) >= ?
          AND NOT EXISTS (
              SELECT 1
              FROM sales_order_lines sol_ord
              JOIN sales_orders so_ord ON so_ord.id = sol_ord.sales_order_id
              WHERE {sale_base_key} = {pll_base_key}
                AND (pl.customer_id IS NULL OR so_ord.customer_id = pl.customer_id)
                AND so_ord.date_entered >= COALESCE(cql.quoted_on, cql.date_created)
          )
          AND NOT EXISTS (
              SELECT 1
              FROM purchase_report_ignored_parts prip
              WHERE prip.base_part_number = {pll_base_key}
          )
        ORDER BY {pll_base_key} ASC, COALESCE(cql.quoted_on, cql.date_created) DESC, cql.quote_price_gbp DESC
        LIMIT ?
        """,
        (cutoff, int(max_rows)),
        fetch='all',
    ) or []
    return [_normalize_report_row(row) for row in rows]


def _load_frequent_sales_source_cost_report(cursor, period_days=90, min_orders=3, max_rows=50, only_out_of_stock=True):
    cutoff = _date_cutoff(period_days)
    sales_base_key = _normalized_base_sql('sol.base_part_number')
    quote_base_key = _normalized_base_sql('pll.base_part_number')
    stock_base_key = _normalized_base_sql('base_part_number')
    rows = _execute_with_cursor(
        cursor,
        f"""
        WITH sales AS (
            SELECT
                {sales_base_key} AS base_part_number,
                COUNT(*) AS sales_order_count,
                SUM(COALESCE(sol.quantity, 0)) AS total_sales_qty,
                AVG(CASE WHEN sol.price > 0 THEN sol.price END) AS avg_sale_price,
                MIN(so.date_entered) AS first_sale_date,
                MAX(so.date_entered) AS last_sale_date,
                COUNT(DISTINCT so.customer_id) AS customer_count
            FROM sales_order_lines sol
            JOIN sales_orders so ON so.id = sol.sales_order_id
            WHERE {sales_base_key} <> ''
              AND so.date_entered >= ?
            GROUP BY {sales_base_key}
            HAVING COUNT(*) >= ?
        ), latest_quote AS (
            SELECT
                {quote_base_key} AS base_part_number,
                cql.id AS quote_line_id,
                pl.id AS parts_list_id,
                pl.name AS parts_list_name,
                c.name AS customer_name,
                cql.base_cost_gbp,
                cql.margin_percent,
                cql.quote_price_gbp,
                COALESCE(cql.quoted_on, cql.date_created) AS latest_quote_date,
                pll.chosen_source_type,
                pll.chosen_source_reference,
                pll.chosen_cost,
                s.name AS chosen_supplier_name,
                psq.quote_reference AS source_quote_reference,
                psq.quote_date AS source_quote_date,
                sqs.name AS source_supplier_name,
                psql.unit_price AS source_unit_price,
                curr.currency_code AS source_currency_code,
                ROW_NUMBER() OVER (
                    PARTITION BY {quote_base_key}
                    ORDER BY COALESCE(cql.quoted_on, cql.date_created) DESC, cql.id DESC
                ) AS rn
            FROM customer_quote_lines cql
            JOIN parts_list_lines pll ON pll.id = cql.parts_list_line_id
            JOIN parts_lists pl ON pl.id = pll.parts_list_id
            LEFT JOIN customers c ON c.id = pl.customer_id
            LEFT JOIN suppliers s ON s.id = pll.chosen_supplier_id
            LEFT JOIN parts_list_supplier_quote_lines psql
                ON pll.chosen_source_type = 'quote'
               AND CAST(psql.id AS TEXT) = pll.chosen_source_reference
            LEFT JOIN parts_list_supplier_quotes psq ON psq.id = psql.supplier_quote_id
            LEFT JOIN suppliers sqs ON sqs.id = psq.supplier_id
            LEFT JOIN currencies curr ON curr.id = psq.currency_id
            WHERE COALESCE(cql.is_no_bid::int, 0) = 0
              AND cql.quote_price_gbp IS NOT NULL
              AND cql.quote_price_gbp > 0
        ), stock AS (
            SELECT {stock_base_key} AS base_part_number, SUM(available_quantity) AS stock_quantity
            FROM stock_movements
            WHERE movement_type = 'IN'
              AND available_quantity > 0
              AND {stock_base_key} <> ''
            GROUP BY {stock_base_key}
        )
        SELECT
            sales.base_part_number,
            COALESCE(pn.part_number, sales.base_part_number) AS part_number,
            pn.system_part_number,
            sales.sales_order_count,
            sales.total_sales_qty,
            sales.avg_sale_price,
            sales.first_sale_date,
            sales.last_sale_date,
            sales.customer_count,
            COALESCE(stock.stock_quantity, 0) AS stock_quantity,
            latest_quote.quote_line_id,
            latest_quote.parts_list_id,
            latest_quote.parts_list_name,
            latest_quote.customer_name,
            latest_quote.base_cost_gbp AS latest_base_cost_gbp,
            latest_quote.margin_percent AS latest_margin_percent,
            latest_quote.quote_price_gbp AS latest_quote_price_gbp,
            latest_quote.latest_quote_date,
            latest_quote.chosen_source_type,
            latest_quote.chosen_source_reference,
            latest_quote.chosen_cost,
            latest_quote.chosen_supplier_name,
            latest_quote.source_quote_reference,
            latest_quote.source_quote_date,
            latest_quote.source_supplier_name,
            latest_quote.source_unit_price,
            latest_quote.source_currency_code
        FROM sales
        LEFT JOIN latest_quote ON latest_quote.base_part_number = sales.base_part_number AND latest_quote.rn = 1
        LEFT JOIN stock ON stock.base_part_number = sales.base_part_number
        LEFT JOIN part_numbers pn ON pn.base_part_number = sales.base_part_number
        WHERE (? = 0 OR COALESCE(stock.stock_quantity, 0) <= 0)
          AND NOT EXISTS (
              SELECT 1
              FROM purchase_report_ignored_parts prip
              WHERE prip.base_part_number = sales.base_part_number
          )
        ORDER BY sales.sales_order_count DESC, sales.total_sales_qty DESC, sales.last_sale_date DESC
        LIMIT ?
        """,
        (cutoff, int(min_orders), 1 if only_out_of_stock else 0, int(max_rows)),
        fetch='all',
    ) or []
    items = [_normalize_report_row(row) for row in rows]
    for item in items:
        item['recommendation'] = 'Review for stock holding' if (_safe_float(item.get('stock_quantity')) or 0) <= 0 else 'Review reorder point'
    return items


def _load_email_reports(config):
    with db_cursor() as cursor:
        unordered_quotes = _load_unordered_customer_quote_report(
            cursor,
            period_days=config.get('quote_period_days', 30),
            sales_period_days=config.get('sales_period_days', 90),
            max_rows=config.get('max_rows', 50),
            min_occurrences=config.get('quote_min_occurrences', 2),
            max_sales_ratio_percent=config.get('quote_max_sales_ratio_percent', 25),
            only_out_of_stock=config.get('only_out_of_stock', True),
        ) if config.get('include_unordered_quotes') else []
        stock_not_won = _load_stock_sourced_unwon_quote_report(
            cursor,
            period_days=config.get('quote_period_days', 30),
            max_rows=config.get('max_rows', 50),
        ) if config.get('include_stock_not_won') else []
        frequent_sales = _load_frequent_sales_source_cost_report(
            cursor,
            period_days=config.get('sales_period_days', 90),
            min_orders=config.get('frequent_min_orders', 3),
            max_rows=config.get('max_rows', 50),
            only_out_of_stock=config.get('frequent_only_out_of_stock', True),
        ) if config.get('include_frequent_sales') else []
    return {
        'generated_at': datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S'),
        'unordered_quotes': unordered_quotes,
        'stock_not_won': stock_not_won,
        'frequent_sales': frequent_sales,
        'summary': {
            'unordered_quote_count': len(unordered_quotes),
            'stock_not_won_count': len(stock_not_won),
            'frequent_sales_count': len(frequent_sales),
        },
        'config': config,
    }


def _create_purchase_report_run(report, config, recipients):
    row = db_execute(
        """
        INSERT INTO purchase_report_runs (
            generated_by_user_id, config_json, recipients,
            unordered_quote_count, stock_not_won_count, frequent_sales_count
        )
        VALUES (?, ?::jsonb, ?, ?, ?, ?)
        RETURNING id
        """,
        (
            _current_user_id(),
            json.dumps(config or {}, default=_json_default),
            ', '.join(recipients or []),
            len(report.get('unordered_quotes', [])),
            len(report.get('stock_not_won', [])),
            len(report.get('frequent_sales', [])),
        ),
        fetch='one',
        commit=True,
    )
    run_id = row.get('id') if row else None
    if not run_id:
        raise RuntimeError('Unable to create purchase report run')

    with db_cursor(commit=True) as cursor:
        for section, report_key in REPORT_SECTION_KEYS:
            for index, item in enumerate(report.get(report_key, []) or []):
                base_part_number = item.get('base_part_number') or item.get('part_number')
                if not base_part_number:
                    continue
                _execute_with_cursor(
                    cursor,
                    """
                    INSERT INTO purchase_report_run_items (
                        run_id, report_section, base_part_number,
                        display_part_number, item_order, item_json
                    )
                    VALUES (?, ?, ?, ?, ?, ?::jsonb)
                    """,
                    (
                        run_id,
                        section,
                        str(base_part_number),
                        item.get('part_number') or item.get('base_part_number'),
                        index,
                        json.dumps(item, default=_json_default),
                    ),
                )
    return run_id


def _load_report_comments(section_parts):
    if not section_parts:
        return {}

    clauses = []
    params = []
    for section, base_part_number in sorted(section_parts):
        clauses.append('(report_section = ? AND base_part_number = ?)')
        params.extend([section, base_part_number])

    rows = db_execute(
        f"""
        SELECT id, report_section, base_part_number, user_id, user_name, comment,
               created_at, updated_at
        FROM purchase_report_comments
        WHERE {' OR '.join(clauses)}
        ORDER BY user_name, updated_at DESC
        """,
        tuple(params),
        fetch='all',
    ) or []

    comments = {}
    for row in rows:
        item = dict(row)
        item['created_at'] = _stringify_date(item.get('created_at'))
        item['updated_at'] = _stringify_date(item.get('updated_at'))
        comments.setdefault((item['report_section'], item['base_part_number']), []).append(item)
    return comments


def _attach_report_comments(report):
    section_parts = set()
    for section, report_key in REPORT_SECTION_KEYS:
        for item in report.get(report_key, []) or []:
            base_part_number = item.get('base_part_number') or item.get('part_number')
            if base_part_number:
                item['report_section'] = section
                section_parts.add((section, str(base_part_number)))

    comments = _load_report_comments(section_parts)
    for section, report_key in REPORT_SECTION_KEYS:
        for item in report.get(report_key, []) or []:
            base_part_number = str(item.get('base_part_number') or item.get('part_number') or '')
            item['comments'] = comments.get((section, base_part_number), [])
    return report


def _load_ignored_purchase_report_parts(base_part_numbers):
    keys = sorted({_normalize_part_key(part) for part in base_part_numbers if _normalize_part_key(part)})
    if not keys:
        return {}

    placeholders = ', '.join(['?'] * len(keys))
    rows = db_execute(
        f"""
        SELECT id, base_part_number, display_part_number, ignored_by_user_id,
               ignored_by_user_name, reason, created_at, updated_at
        FROM purchase_report_ignored_parts
        WHERE base_part_number IN ({placeholders})
        """,
        tuple(keys),
        fetch='all',
    ) or []

    ignored = {}
    for row in rows:
        item = dict(row)
        item['created_at'] = _stringify_date(item.get('created_at'))
        item['updated_at'] = _stringify_date(item.get('updated_at'))
        ignored[item['base_part_number']] = item
    return ignored


def _attach_ignored_purchase_report_parts(report):
    base_part_numbers = []
    for _, report_key in REPORT_SECTION_KEYS:
        for item in report.get(report_key, []) or []:
            base_part_numbers.append(item.get('base_part_number') or item.get('part_number'))

    ignored = _load_ignored_purchase_report_parts(base_part_numbers)
    for _, report_key in REPORT_SECTION_KEYS:
        for item in report.get(report_key, []) or []:
            key = _normalize_part_key(item.get('base_part_number') or item.get('part_number'))
            item['is_ignored'] = key in ignored
            item['ignored_part'] = ignored.get(key)
    return report


def _load_purchase_report_run(run_id):
    run = db_execute(
        """
        SELECT prr.*, u.username AS generated_by_username
        FROM purchase_report_runs prr
        LEFT JOIN users u ON u.id = prr.generated_by_user_id
        WHERE prr.id = ?
        """,
        (run_id,),
        fetch='one',
    )
    if not run:
        return None

    rows = db_execute(
        """
        SELECT report_section, base_part_number, display_part_number, item_order, item_json
        FROM purchase_report_run_items
        WHERE run_id = ?
        ORDER BY report_section, item_order
        """,
        (run_id,),
        fetch='all',
    ) or []

    report = {
        'generated_at': _stringify_date(run.get('generated_at')),
        'unordered_quotes': [],
        'stock_not_won': [],
        'frequent_sales': [],
        'summary': {
            'unordered_quote_count': run.get('unordered_quote_count') or 0,
            'stock_not_won_count': run.get('stock_not_won_count') or 0,
            'frequent_sales_count': run.get('frequent_sales_count') or 0,
        },
        'config': run.get('config_json') or {},
        'run': dict(run),
    }

    for row in rows:
        raw_item = row.get('item_json') or {}
        if isinstance(raw_item, str):
            try:
                item = json.loads(raw_item)
            except ValueError:
                item = {}
        else:
            item = dict(raw_item)
        item['report_section'] = row.get('report_section')
        item['base_part_number'] = item.get('base_part_number') or row.get('base_part_number')
        item['part_number'] = item.get('part_number') or row.get('display_part_number')
        for section, report_key in REPORT_SECTION_KEYS:
            if row.get('report_section') == section:
                report[report_key].append(item)
                break

    _attach_report_comments(report)
    return _attach_ignored_purchase_report_parts(report)


def _save_ignored_purchase_report_parts(parts, reason=''):
    user_id = _current_user_id()
    if not user_id:
        raise RuntimeError('Login required')

    user_name = _current_user_display_name()
    cleaned_reason = str(reason or '').strip()
    saved = []
    seen = set()

    with db_cursor(commit=True) as cursor:
        for part in parts or []:
            display_part_number = str(part.get('display_part_number') or part.get('part_number') or part.get('base_part_number') or '').strip()
            base_part_number = _normalize_part_key(part.get('base_part_number') or display_part_number)
            if not base_part_number or base_part_number in seen:
                continue
            seen.add(base_part_number)

            _execute_with_cursor(
                cursor,
                """
                INSERT INTO purchase_report_ignored_parts (
                    base_part_number, display_part_number, ignored_by_user_id,
                    ignored_by_user_name, reason, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, NOW(), NOW())
                ON CONFLICT(base_part_number)
                DO UPDATE SET
                    display_part_number = COALESCE(NULLIF(EXCLUDED.display_part_number, ''), purchase_report_ignored_parts.display_part_number),
                    ignored_by_user_id = EXCLUDED.ignored_by_user_id,
                    ignored_by_user_name = EXCLUDED.ignored_by_user_name,
                    reason = EXCLUDED.reason,
                    updated_at = NOW()
                RETURNING id, base_part_number, display_part_number, ignored_by_user_id,
                          ignored_by_user_name, reason, created_at, updated_at
                """,
                (
                    base_part_number,
                    display_part_number or base_part_number,
                    user_id,
                    user_name,
                    cleaned_reason,
                ),
            )
            row = cursor.fetchone()
            item = dict(row or {})
            item['created_at'] = _stringify_date(item.get('created_at'))
            item['updated_at'] = _stringify_date(item.get('updated_at'))
            saved.append(item)

    return saved


def _save_purchase_report_comment(section, base_part_number, comment):
    user_id = _current_user_id()
    if not user_id:
        raise RuntimeError('Login required')
    user_name = _current_user_display_name()
    cleaned = str(comment or '').strip()

    if not cleaned:
        db_execute(
            """
            DELETE FROM purchase_report_comments
            WHERE report_section = ? AND base_part_number = ? AND user_id = ?
            """,
            (section, base_part_number, user_id),
            commit=True,
        )
        return {'deleted': True}

    row = db_execute(
        """
        INSERT INTO purchase_report_comments (
            report_section, base_part_number, user_id, user_name, comment, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, NOW(), NOW())
        ON CONFLICT(report_section, base_part_number, user_id)
        DO UPDATE SET
            user_name = EXCLUDED.user_name,
            comment = EXCLUDED.comment,
            updated_at = NOW()
        RETURNING id, report_section, base_part_number, user_id, user_name, comment, created_at, updated_at
        """,
        (section, base_part_number, user_id, user_name, cleaned),
        fetch='one',
        commit=True,
    )
    item = dict(row or {})
    item['created_at'] = _stringify_date(item.get('created_at'))
    item['updated_at'] = _stringify_date(item.get('updated_at'))
    return item


def _comment_columns(items):
    columns = []
    seen = set()
    for item in items:
        for comment in item.get('comments') or []:
            name = comment.get('user_name') or 'User'
            if name not in seen:
                seen.add(name)
                columns.append(name)
    return columns


def _comment_cell(item, user_name):
    for comment in item.get('comments') or []:
        if (comment.get('user_name') or 'User') == user_name:
            bits = [comment.get('comment') or '']
            if comment.get('updated_at'):
                bits.append(comment.get('updated_at'))
            return ' - '.join([bit for bit in bits if bit])
    return ''


def _compact_number(value, digits=0):
    numeric = _safe_float(value)
    if numeric is None:
        return '-'
    if abs(numeric - round(numeric)) < 0.000001:
        return f'{int(round(numeric)):,}'
    return f'{numeric:,.{digits}f}'.rstrip('0').rstrip('.')


def _review_source_label(item):
    source_type = (item.get('chosen_source_type') or '').strip().lower()
    if source_type == 'stock':
        return 'Stock'
    supplier = item.get('source_supplier_name') or item.get('chosen_supplier_name')
    if supplier:
        return supplier
    return item.get('source_label') or _format_source_label(source_type)


def _decorate_review_item(item):
    item['display_part'] = item.get('part_number') or item.get('base_part_number') or '-'
    item['display_qty'] = _compact_number(item.get('total_quoted_qty') or item.get('total_sales_qty'), 2)
    item['display_stock'] = _compact_number(item.get('stock_quantity'), 2)
    item['display_latest_cost'] = _money(item.get('base_cost_gbp') or item.get('latest_base_cost_gbp'))
    item['display_latest_price'] = _money(item.get('quote_price_gbp'))
    item['display_avg_sell'] = _money(item.get('avg_sale_price'))
    item['display_margin'] = _pct(item.get('margin_percent') or item.get('latest_margin_percent'))
    item['display_source'] = _review_source_label(item)
    return item


def _prepare_report_review_sections(report):
    sections = []
    current_user_id = _current_user_id()
    for section, report_key in REPORT_SECTION_KEYS:
        rows = report.get(report_key, []) or []
        comment_columns = _comment_columns(rows)
        for item in rows:
            _decorate_review_item(item)
            item['current_user_comment'] = ''
            for comment in item.get('comments') or []:
                if comment.get('user_id') == current_user_id:
                    item['current_user_comment'] = comment.get('comment') or ''
                    break
        sections.append({
            'section': section,
            'report_key': report_key,
            'title': REPORT_SECTION_LABELS.get(section, section),
            'rows': rows,
            'comment_columns': comment_columns,
        })
    return sections


def _build_purchase_reports_email(report, report_url=None):
    _attach_report_comments(report)
    config = report['config']
    generated_at = escape(report.get('generated_at') or '')
    quote_stock_filter_label = 'Out-of-stock only' if config.get('only_out_of_stock', True) else 'All stock statuses'
    sales_stock_filter_label = 'Out-of-stock only' if config.get('frequent_only_out_of_stock', True) else 'All stock statuses'
    quoted_sales_filter_label = f"Sales ratio at or below {config.get('quote_max_sales_ratio_percent')}%"
    frequent_sales_heading = (
        'Frequently ordered parts not in stock'
        if config.get('frequent_only_out_of_stock', True)
        else 'Frequent sales order parts'
    )
    quote_comment_columns = _comment_columns(report.get('unordered_quotes', []))
    stock_comment_columns = _comment_columns(report.get('stock_not_won', []))
    sales_comment_columns = _comment_columns(report.get('frequent_sales', []))
    quote_comment_headers = ''.join(
        f'<th style="border:1px solid #ddd;padding:6px;">{escape(name)}</th>'
        for name in quote_comment_columns
    )
    sales_comment_headers = ''.join(
        f'<th style="border:1px solid #ddd;padding:6px;">{escape(name)}</th>'
        for name in sales_comment_columns
    )
    stock_comment_headers = ''.join(
        f'<th style="border:1px solid #ddd;padding:6px;">{escape(name)}</th>'
        for name in stock_comment_columns
    )

    def table_cell(value):
        return f'<td style="border:1px solid #ddd;padding:6px;vertical-align:top;">{escape(str(value if value is not None else "-"))}</td>'

    quote_rows_html = []
    quote_rows_text = []
    for item in report.get('unordered_quotes', []):
        quote_rows_html.append(
            '<tr>'
            + table_cell(item.get('part_number') or item.get('base_part_number'))
            + table_cell(item.get('quote_line_count'))
            + table_cell(item.get('quote_occurrence_count'))
            + table_cell(item.get('customer_count'))
            + table_cell(item.get('total_quoted_qty'))
            + table_cell(_pct((_safe_float(item.get('sales_ratio')) or 0) * 100))
            + table_cell(item.get('stock_quantity'))
            + table_cell(item.get('latest_quoted_on') or item.get('quoted_on') or item.get('date_created'))
            + table_cell(_money(item.get('base_cost_gbp')))
            + table_cell(_money(item.get('quote_price_gbp')))
            + table_cell(_pct(item.get('margin_percent')))
            + table_cell(item.get('source_detail'))
            + table_cell(item.get('customer_name'))
            + table_cell(item.get('parts_list_name'))
            + ''.join(table_cell(_comment_cell(item, name)) for name in quote_comment_columns)
            + '</tr>'
        )
        quote_rows_text.append(
            f"- {item.get('part_number') or item.get('base_part_number')} | "
            f"{item.get('quote_occurrence_count') or 0} quote occurrences / {item.get('customer_count') or 0} customers | "
            f"sales ratio {_pct((_safe_float(item.get('sales_ratio')) or 0) * 100)} | "
            f"total qty {item.get('total_quoted_qty') or '-'} | stock {item.get('stock_quantity') or 0} | "
            f"latest {item.get('latest_quoted_on') or item.get('quoted_on') or item.get('date_created') or '-'} | "
            f"latest cost {_money(item.get('base_cost_gbp'))} -> quoted price {_money(item.get('quote_price_gbp'))} | "
            f"margin {_pct(item.get('margin_percent'))} | {item.get('source_detail') or '-'}"
        )

    stock_rows_html = []
    stock_rows_text = []
    for item in report.get('stock_not_won', []):
        stock_rows_html.append(
            '<tr>'
            + table_cell(item.get('part_number') or item.get('base_part_number'))
            + table_cell(item.get('total_quoted_qty'))
            + table_cell(item.get('stock_quantity'))
            + table_cell(item.get('latest_quoted_on') or item.get('quoted_on') or item.get('date_created'))
            + table_cell(_money(item.get('base_cost_gbp')))
            + table_cell(_money(item.get('quote_price_gbp')))
            + table_cell(_pct(item.get('margin_percent')))
            + table_cell(item.get('customer_name'))
            + table_cell(item.get('parts_list_name'))
            + ''.join(table_cell(_comment_cell(item, name)) for name in stock_comment_columns)
            + '</tr>'
        )
        stock_rows_text.append(
            f"- {item.get('part_number') or item.get('base_part_number')} | "
            f"total qty {item.get('total_quoted_qty') or '-'} | stock {item.get('stock_quantity') or 0} | "
            f"latest {item.get('latest_quoted_on') or item.get('quoted_on') or item.get('date_created') or '-'} | "
            f"quoted price {_money(item.get('quote_price_gbp'))} | margin {_pct(item.get('margin_percent'))}"
        )

    sales_rows_html = []
    sales_rows_text = []
    for item in report.get('frequent_sales', []):
        sales_rows_html.append(
            '<tr>'
            + table_cell(item.get('part_number') or item.get('base_part_number'))
            + table_cell(item.get('sales_order_count'))
            + table_cell(item.get('total_sales_qty'))
            + table_cell(item.get('customer_count'))
            + table_cell(item.get('last_sale_date'))
            + table_cell(_money(item.get('avg_sale_price')))
            + table_cell(item.get('stock_quantity'))
            + table_cell(_money(item.get('latest_base_cost_gbp')))
            + table_cell(_pct(item.get('latest_margin_percent')))
            + table_cell(item.get('source_detail'))
            + table_cell(item.get('recommendation'))
            + ''.join(table_cell(_comment_cell(item, name)) for name in sales_comment_columns)
            + '</tr>'
        )
        sales_rows_text.append(
            f"- {item.get('part_number') or item.get('base_part_number')} | {item.get('sales_order_count')} orders / "
            f"{item.get('total_sales_qty')} units | stock {item.get('stock_quantity')} | "
            f"avg sell {_money(item.get('avg_sale_price'))} | latest cost {_money(item.get('latest_base_cost_gbp'))} | "
            f"margin {_pct(item.get('latest_margin_percent'))} | {item.get('source_detail') or '-'}"
        )

    quote_colspan = 14 + len(quote_comment_columns)
    stock_colspan = 9 + len(stock_comment_columns)
    sales_colspan = 11 + len(sales_comment_columns)
    quote_table = ''.join(quote_rows_html) or f'<tr><td colspan="{quote_colspan}" style="padding:8px;color:#666;">No repeatedly quoted, not ordered parts found.</td></tr>'
    stock_table = ''.join(stock_rows_html) or f'<tr><td colspan="{stock_colspan}" style="padding:8px;color:#666;">No stock-sourced quote losses found.</td></tr>'
    sales_table = ''.join(sales_rows_html) or f'<tr><td colspan="{sales_colspan}" style="padding:8px;color:#666;">No frequent sales order candidates found.</td></tr>'
    review_link_html = ''
    review_link_text = ''
    if report_url:
        safe_url = escape(report_url)
        review_link_html = f'<p><a href="{safe_url}" style="color:#0d6efd;">Open report to add comments or resend with comments</a></p>'
        review_link_text = f"\nReview/add comments: {report_url}\n"

    html_body = f"""
    <html><body style="font-family:Arial,sans-serif;color:#333;line-height:1.4;">
        <h2>Purchase Suggestions Report</h2>
        <p><strong>Generated:</strong> {generated_at} UTC</p>
        {review_link_html}
        <p>
            Quoted-not-ordered period: last {escape(str(config.get('quote_period_days')))} days.<br>
            Grouped quoted-not-won minimum: {escape(str(config.get('quote_min_occurrences')))} distinct quote/list occurrences.<br>
            Grouped quoted-not-won sales ratio ceiling: {escape(str(config.get('quote_max_sales_ratio_percent')))}%.<br>
            Stock-sourced losses: individual stock-sourced quote lines with no matching customer sales order after the quote date.<br>
            Frequent sales period: last {escape(str(config.get('sales_period_days')))} days, minimum {escape(str(config.get('frequent_min_orders')))} order lines.<br>
            Quote-loss stock filter: {escape(quote_stock_filter_label)}.<br>
            Frequent-sales stock filter: {escape(sales_stock_filter_label)}.<br>
            Quoted/not-won sales filter: {escape(quoted_sales_filter_label)}.
        </p>
        <h3>Repeatedly quoted parts not yet ordered ({len(report.get('unordered_quotes', []))})</h3>
        <table style="border-collapse:collapse;width:100%;font-size:13px;">
            <thead><tr style="background:#f3f4f6;">
                <th style="border:1px solid #ddd;padding:6px;">Part</th>
                <th style="border:1px solid #ddd;padding:6px;">Quote lines</th>
                <th style="border:1px solid #ddd;padding:6px;">Occurrences</th>
                <th style="border:1px solid #ddd;padding:6px;">Customers</th>
                <th style="border:1px solid #ddd;padding:6px;">Total qty</th>
                <th style="border:1px solid #ddd;padding:6px;">Sales ratio</th>
                <th style="border:1px solid #ddd;padding:6px;">Stock</th>
                <th style="border:1px solid #ddd;padding:6px;">Latest quote</th>
                <th style="border:1px solid #ddd;padding:6px;">Latest cost</th>
                <th style="border:1px solid #ddd;padding:6px;">Latest quoted price</th>
                <th style="border:1px solid #ddd;padding:6px;">Margin</th>
                <th style="border:1px solid #ddd;padding:6px;">Cost source</th>
                <th style="border:1px solid #ddd;padding:6px;">Latest customer</th>
                <th style="border:1px solid #ddd;padding:6px;">Latest quote list</th>
                {quote_comment_headers}
            </tr></thead><tbody>{quote_table}</tbody>
        </table>
        <h3 style="margin-top:24px;">Quoted from stock but not won ({len(report.get('stock_not_won', []))})</h3>
        <table style="border-collapse:collapse;width:100%;font-size:13px;">
            <thead><tr style="background:#f3f4f6;">
                <th style="border:1px solid #ddd;padding:6px;">Part</th>
                <th style="border:1px solid #ddd;padding:6px;">Qty</th>
                <th style="border:1px solid #ddd;padding:6px;">Stock</th>
                <th style="border:1px solid #ddd;padding:6px;">Quote date</th>
                <th style="border:1px solid #ddd;padding:6px;">Latest cost</th>
                <th style="border:1px solid #ddd;padding:6px;">Quoted price</th>
                <th style="border:1px solid #ddd;padding:6px;">Margin</th>
                <th style="border:1px solid #ddd;padding:6px;">Customer</th>
                <th style="border:1px solid #ddd;padding:6px;">Quote list</th>
                {stock_comment_headers}
            </tr></thead><tbody>{stock_table}</tbody>
        </table>
        <h3 style="margin-top:24px;">{escape(frequent_sales_heading)} ({len(report.get('frequent_sales', []))})</h3>
        <table style="border-collapse:collapse;width:100%;font-size:13px;">
            <thead><tr style="background:#f3f4f6;">
                <th style="border:1px solid #ddd;padding:6px;">Part</th>
                <th style="border:1px solid #ddd;padding:6px;">Orders</th>
                <th style="border:1px solid #ddd;padding:6px;">Qty sold</th>
                <th style="border:1px solid #ddd;padding:6px;">Customers</th>
                <th style="border:1px solid #ddd;padding:6px;">Last sale</th>
                <th style="border:1px solid #ddd;padding:6px;">Avg sell</th>
                <th style="border:1px solid #ddd;padding:6px;">Stock</th>
                <th style="border:1px solid #ddd;padding:6px;">Latest quote cost</th>
                <th style="border:1px solid #ddd;padding:6px;">Margin</th>
                <th style="border:1px solid #ddd;padding:6px;">Cost source</th>
                <th style="border:1px solid #ddd;padding:6px;">Action</th>
                {sales_comment_headers}
            </tr></thead><tbody>{sales_table}</tbody>
        </table>
    </body></html>
    """

    text_body = (
        'Purchase Suggestions Report\n\n'
        f"Generated: {report.get('generated_at')} UTC\n"
        + review_link_text
        + f"Quote-loss stock filter: {quote_stock_filter_label}\n"
        f"Frequent-sales stock filter: {sales_stock_filter_label}\n"
        f"Grouped quoted/not-won sales filter: {quoted_sales_filter_label}\n"
        f"Repeatedly quoted, not ordered parts: {len(report.get('unordered_quotes', []))}\n"
        f"Quoted from stock but not won: {len(report.get('stock_not_won', []))}\n"
        f"Frequent sales candidates: {len(report.get('frequent_sales', []))}\n\n"
        'Repeatedly quoted parts not yet ordered:\n'
        + ('\n'.join(quote_rows_text) if quote_rows_text else '- None')
        + '\n\nQuoted from stock but not won:\n'
        + ('\n'.join(stock_rows_text) if stock_rows_text else '- None')
        + f'\n\n{frequent_sales_heading}:\n'
        + ('\n'.join(sales_rows_text) if sales_rows_text else '- None')
    )

    total = len(report.get('unordered_quotes', [])) + len(report.get('stock_not_won', [])) + len(report.get('frequent_sales', []))
    subject = f'Purchase suggestions report ({total} candidates)'
    return subject, html_body, text_body


def _send_purchase_report_email(
    config,
    recipients,
    update_last_sent=True,
    subject_prefix='',
    report=None,
    run_id=None,
):
    recipients = _split_email_recipients(','.join(recipients) if isinstance(recipients, list) else recipients)
    if not recipients:
        return {'success': False, 'sent': False, 'error': 'No recipients configured'}

    report = report or _load_email_reports(config)
    if run_id is None:
        run_id = _create_purchase_report_run(report, config, recipients)
    report_url = _report_review_url(run_id)
    subject, html_body, text_body = _build_purchase_reports_email(report, report_url=report_url)
    subject = f'{subject_prefix}{subject}' if subject_prefix else subject

    from routes.portal_admin import send_email
    failures = []
    for recipient in recipients:
        if not send_email(
            recipient,
            subject,
            html_body,
            text_body,
            from_email=SPROUTT_ADMIN_EMAIL,
            from_name=SPROUTT_ADMIN_NAME,
        ):
            failures.append(recipient)

    if failures:
        return {'success': False, 'sent': False, 'error': f"Failed to send to: {', '.join(failures)}"}

    if update_last_sent:
        config['last_sent_at'] = datetime.utcnow().isoformat(timespec='seconds')
        _save_email_report_config(config)

    return {
        'success': True,
        'sent': True,
        'recipients': recipients,
        'from_email': SPROUTT_ADMIN_EMAIL,
        'summary': report.get('summary'),
        'report_run_id': run_id,
        'report_url': report_url,
    }


def send_due_purchase_suggestion_reports(force=False):
    config = _load_email_report_config()
    recipients = _split_email_recipients(config.get('recipients'))
    if not force:
        if not config.get('enabled') or not recipients:
            return {'success': True, 'sent': False, 'reason': 'disabled_or_no_recipients'}
        last_sent = _parse_datetime(config.get('last_sent_at'))
        if last_sent and datetime.utcnow() < last_sent + timedelta(days=config.get('frequency_days', 1)):
            return {'success': True, 'sent': False, 'reason': 'not_due'}

    return _send_purchase_report_email(config, recipients, update_last_sent=True)


def _using_postgres():
    return bool(os.getenv('DATABASE_URL', '').startswith(('postgres://', 'postgresql://')))


def _prepare_query(query):
    return query.replace('?', '%s') if _using_postgres() else query


def _stringify_date(value):
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, (datetime, date)):
        return value.strftime('%Y-%m-%d')
    return str(value)


def _coerce_numeric(value):
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return value
    try:
        return float(value)
    except (TypeError, ValueError):
        return value


def _execute_with_cursor(cur, query, params=None, fetch=None):
    cur.execute(_prepare_query(query), params or [])
    if fetch == 'one':
        return cur.fetchone()
    if fetch == 'all':
        return cur.fetchall()
    return cur


def convert_vq_price_to_gbp(price, currency_code):
    """
    Convert VQ price to GBP for comparison with sales prices

    Args:
        price: The price to convert
        currency_code: The currency code (e.g., 'USD', 'EUR', 'GBP')

    Returns:
        float: Price converted to GBP, or original price if conversion fails
    """
    if not price or not currency_code:
        return price

    # If already in GBP, return as-is
    if currency_code == 'GBP':
        return price

    try:
        decimal_price = price if isinstance(price, Decimal) else Decimal(str(price))
        converted = convert_currency(decimal_price, currency_code, 'GBP')
        return float(converted) if converted is not None else converted
    except Exception as e:
        print(f"Warning: Could not convert {price} {currency_code} to GBP: {e}")
        return price  # Return original price if conversion fails


def _safe_float(value):
    try:
        if value is None or value == '':
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _get_recent_date_filter(column_name, days):
    if _using_postgres():
        return f"{column_name} >= CURRENT_DATE - INTERVAL '{int(days)} days'"
    return f"{column_name} >= date('now', '-{int(days)} days')"


def _parse_iso_date(value):
    if not value:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return datetime.strptime(str(value)[:10], '%Y-%m-%d').date()
    except ValueError:
        return None


def _load_speculative_buy_report(cursor, lookback_months=2):
    recent_sales_filter = _get_recent_date_filter('so.date_entered', 365)
    lookback_months = max(1, min(int(lookback_months or 2), 24))
    lookback_cutoff = datetime.utcnow().date() - timedelta(days=lookback_months * 30)

    quote_rows = _execute_with_cursor(
        cursor,
        '''
        SELECT
            psql.id AS quote_line_id,
            psq.id AS supplier_quote_id,
            pll.base_part_number,
            COALESCE(pn.part_number, pll.base_part_number) AS part_number,
            pn.system_part_number,
            psql.quoted_part_number,
            psql.manufacturer,
            psql.quantity_quoted,
            psql.unit_price,
            psql.lead_time_days,
            psql.condition_code,
            psq.quote_reference,
            psq.quote_date,
            psq.parts_list_id,
            pl.name AS parts_list_name,
            s.id AS supplier_id,
            s.name AS supplier_name,
            c.name AS customer_name,
            curr.currency_code,
            curr.symbol AS currency_symbol
        FROM parts_list_supplier_quote_lines psql
        JOIN parts_list_supplier_quotes psq ON psq.id = psql.supplier_quote_id
        JOIN parts_list_lines pll ON pll.id = psql.parts_list_line_id
        LEFT JOIN part_numbers pn ON pn.base_part_number = pll.base_part_number
        LEFT JOIN parts_lists pl ON pl.id = psq.parts_list_id
        LEFT JOIN customers c ON c.id = pl.customer_id
        LEFT JOIN suppliers s ON s.id = psq.supplier_id
        LEFT JOIN currencies curr ON curr.id = psq.currency_id
        WHERE COALESCE(psql.is_no_bid, FALSE) = FALSE
          AND psql.unit_price IS NOT NULL
          AND psql.unit_price > 0
          AND pll.base_part_number IS NOT NULL
          AND TRIM(pll.base_part_number) <> ''
        ORDER BY psq.quote_date DESC, psql.id DESC
        ''',
        fetch='all'
    ) or []

    sales_rows = _execute_with_cursor(
        cursor,
        f'''
        SELECT
            sol.base_part_number,
            COUNT(*) AS sales_order_count,
            COALESCE(SUM(sol.quantity), 0) AS total_sales_qty,
            MAX(so.date_entered) AS last_sale_date,
            AVG(CASE WHEN sol.price > 0 THEN sol.price END) AS avg_sale_price,
            MAX(CASE WHEN sol.price > 0 THEN sol.price END) AS max_sale_price,
            SUM(CASE WHEN {recent_sales_filter} THEN COALESCE(sol.quantity, 0) ELSE 0 END) AS recent_sales_qty,
            SUM(CASE WHEN {recent_sales_filter} THEN 1 ELSE 0 END) AS recent_sales_orders
        FROM sales_order_lines sol
        JOIN sales_orders so ON so.id = sol.sales_order_id
        WHERE sol.base_part_number IS NOT NULL
          AND TRIM(sol.base_part_number) <> ''
        GROUP BY sol.base_part_number
        ''',
        fetch='all'
    ) or []

    customer_quote_rows = _execute_with_cursor(
        cursor,
        '''
        SELECT
            pll.base_part_number,
            COUNT(*) AS customer_quote_count,
            AVG(cql.quote_price_gbp) AS avg_customer_quote_price,
            MAX(cql.quote_price_gbp) AS max_customer_quote_price,
            MAX(cql.date_created) AS last_customer_quote_date
        FROM customer_quote_lines cql
        JOIN parts_list_lines pll ON pll.id = cql.parts_list_line_id
        WHERE cql.quote_price_gbp IS NOT NULL
          AND cql.quote_price_gbp > 0
          AND COALESCE(cql.is_no_bid, 0) = 0
          AND pll.base_part_number IS NOT NULL
          AND TRIM(pll.base_part_number) <> ''
        GROUP BY pll.base_part_number
        ''',
        fetch='all'
    ) or []

    stock_rows = _execute_with_cursor(
        cursor,
        '''
        SELECT
            base_part_number,
            COALESCE(SUM(available_quantity), 0) AS stock_quantity
        FROM stock_movements
        WHERE movement_type = 'IN'
          AND available_quantity > 0
          AND base_part_number IS NOT NULL
          AND TRIM(base_part_number) <> ''
        GROUP BY base_part_number
        ''',
        fetch='all'
    ) or []

    quotes_by_part = {}
    normalized_quotes = []

    for row in quote_rows:
        quote = dict(row)
        raw_price = _safe_float(quote.get('unit_price'))
        currency_code = quote.get('currency_code') or 'GBP'
        price_gbp = convert_vq_price_to_gbp(raw_price, currency_code) if raw_price is not None else None
        quote['unit_price'] = raw_price
        quote['unit_price_gbp'] = _safe_float(price_gbp)
        quote['quote_date'] = _stringify_date(quote.get('quote_date'))
        normalized_quotes.append(quote)
        quotes_by_part.setdefault(quote['base_part_number'], []).append(quote)

    sales_map = {}
    for row in sales_rows:
        sales_map[row['base_part_number']] = {
            'sales_order_count': int(row['sales_order_count'] or 0),
            'total_sales_qty': _safe_float(row['total_sales_qty']) or 0.0,
            'last_sale_date': _stringify_date(row.get('last_sale_date')),
            'avg_sale_price': _safe_float(row.get('avg_sale_price')),
            'max_sale_price': _safe_float(row.get('max_sale_price')),
            'recent_sales_qty': _safe_float(row.get('recent_sales_qty')) or 0.0,
            'recent_sales_orders': int(row['recent_sales_orders'] or 0),
        }

    customer_quote_map = {}
    for row in customer_quote_rows:
        customer_quote_map[row['base_part_number']] = {
            'customer_quote_count': int(row['customer_quote_count'] or 0),
            'avg_customer_quote_price': _safe_float(row.get('avg_customer_quote_price')),
            'max_customer_quote_price': _safe_float(row.get('max_customer_quote_price')),
            'last_customer_quote_date': _stringify_date(row.get('last_customer_quote_date')),
        }

    stock_map = {
        row['base_part_number']: _safe_float(row.get('stock_quantity')) or 0.0
        for row in stock_rows
    }

    best_opportunity_by_part = {}
    reason_counts = {
        'sales_orders': 0,
        'customer_quotes': 0,
    }

    for quote in normalized_quotes:
        current_price = quote.get('unit_price_gbp')
        if current_price is None or current_price <= 0:
            continue
        quote_date = _parse_iso_date(quote.get('quote_date'))
        if quote_date is None or quote_date < lookback_cutoff:
            continue

        base_part_number = quote['base_part_number']
        part_quotes = quotes_by_part.get(base_part_number, [])
        other_prices = [
            q['unit_price_gbp']
            for q in part_quotes
            if q['quote_line_id'] != quote['quote_line_id'] and q.get('unit_price_gbp')
        ]

        purchase_quote_count = len(other_prices)
        avg_purchase_price = sum(other_prices) / purchase_quote_count if purchase_quote_count else None
        best_purchase_price = min(other_prices) if other_prices else None
        sales_stats = sales_map.get(base_part_number, {})
        customer_quote_stats = customer_quote_map.get(base_part_number, {})

        reasons = []
        opportunity_score = 0.0

        avg_sale_price = sales_stats.get('avg_sale_price')
        sales_order_count = sales_stats.get('sales_order_count', 0)
        if avg_sale_price and sales_order_count >= 3:
            discount_to_sale_pct = ((avg_sale_price - current_price) / avg_sale_price) * 100
            if discount_to_sale_pct >= 35:
                reasons.append({
                    'source': 'sales_orders',
                    'label': 'Sales orders',
                    'detail': (
                        f"{discount_to_sale_pct:.1f}% under average sell price: "
                        f"GBP {current_price:.2f} buy vs GBP {avg_sale_price:.2f} average "
                        f"across {sales_order_count} sales order lines"
                    ),
                })
                opportunity_score += discount_to_sale_pct * 1.1

        avg_customer_quote_price = customer_quote_stats.get('avg_customer_quote_price')
        customer_quote_count = customer_quote_stats.get('customer_quote_count', 0)
        if avg_customer_quote_price and customer_quote_count >= 3:
            discount_to_customer_quote_pct = (
                (avg_customer_quote_price - current_price) / avg_customer_quote_price
            ) * 100
            if discount_to_customer_quote_pct >= 35:
                reasons.append({
                    'source': 'customer_quotes',
                    'label': 'Customer quotes',
                    'detail': (
                        f"{discount_to_customer_quote_pct:.1f}% under quoted sell price: "
                        f"GBP {current_price:.2f} buy vs GBP {avg_customer_quote_price:.2f} "
                        f"average across {customer_quote_count} customer quotes"
                    ),
                })
                opportunity_score += discount_to_customer_quote_pct

        if not reasons:
            continue

        recent_sales_qty = sales_stats.get('recent_sales_qty', 0.0)
        recent_sales_orders = sales_stats.get('recent_sales_orders', 0)
        stock_quantity = stock_map.get(base_part_number, 0.0)

        demand_score = min(25.0, (recent_sales_qty * 2.0) + (recent_sales_orders * 1.5) + (customer_quote_count * 0.5))
        stock_penalty = min(15.0, stock_quantity * 0.5)
        opportunity_score += demand_score
        opportunity_score -= stock_penalty

        candidate = {
            'quote_line_id': quote['quote_line_id'],
            'supplier_quote_id': quote['supplier_quote_id'],
            'parts_list_id': quote.get('parts_list_id'),
            'parts_list_name': quote.get('parts_list_name'),
            'customer_name': quote.get('customer_name'),
            'base_part_number': base_part_number,
            'part_number': quote.get('part_number') or base_part_number,
            'system_part_number': quote.get('system_part_number'),
            'quoted_part_number': quote.get('quoted_part_number'),
            'manufacturer': quote.get('manufacturer'),
            'supplier_id': quote.get('supplier_id'),
            'supplier_name': quote.get('supplier_name'),
            'quote_reference': quote.get('quote_reference'),
            'quote_date': quote.get('quote_date'),
            'quantity_quoted': quote.get('quantity_quoted'),
            'lead_time_days': quote.get('lead_time_days'),
            'condition_code': quote.get('condition_code'),
            'currency_code': quote.get('currency_code') or 'GBP',
            'currency_symbol': quote.get('currency_symbol') or '£',
            'unit_price_original': current_price if (quote.get('currency_code') or 'GBP') == 'GBP' else quote.get('unit_price'),
            'unit_price_gbp': current_price,
            'stock_quantity': stock_quantity,
            'sales_order_count': sales_order_count,
            'total_sales_qty': sales_stats.get('total_sales_qty', 0.0),
            'recent_sales_qty': recent_sales_qty,
            'recent_sales_orders': recent_sales_orders,
            'last_sale_date': sales_stats.get('last_sale_date'),
            'avg_sale_price': avg_sale_price,
            'max_sale_price': sales_stats.get('max_sale_price'),
            'customer_quote_count': customer_quote_count,
            'avg_customer_quote_price': avg_customer_quote_price,
            'max_customer_quote_price': customer_quote_stats.get('max_customer_quote_price'),
            'other_purchase_quote_count': purchase_quote_count,
            'avg_purchase_price': avg_purchase_price,
            'best_purchase_price': best_purchase_price,
            'reason_count': len(reasons),
            'reasons': reasons,
            'opportunity_score': round(max(opportunity_score, 0), 1),
        }

        existing = best_opportunity_by_part.get(base_part_number)
        if existing is None:
            best_opportunity_by_part[base_part_number] = candidate
            continue

        candidate_quote_date = _parse_iso_date(candidate.get('quote_date'))
        existing_quote_date = _parse_iso_date(existing.get('quote_date'))
        candidate_key = (
            candidate.get('opportunity_score', 0),
            candidate.get('reason_count', 0),
            candidate.get('recent_sales_qty', 0),
            candidate_quote_date or date.min,
        )
        existing_key = (
            existing.get('opportunity_score', 0),
            existing.get('reason_count', 0),
            existing.get('recent_sales_qty', 0),
            existing_quote_date or date.min,
        )
        if candidate_key > existing_key:
            best_opportunity_by_part[base_part_number] = candidate

    opportunities = list(best_opportunity_by_part.values())

    for opportunity in opportunities:
        for reason in opportunity['reasons']:
            reason_counts[reason['source']] += 1

    opportunities.sort(
        key=lambda item: (
            item.get('opportunity_score', 0),
            item.get('recent_sales_qty', 0),
            item.get('reason_count', 0),
            item.get('quote_date') or '',
        ),
        reverse=True,
    )

    return {
        'generated_at': datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S'),
        'opportunities': opportunities,
        'summary': {
            'total_candidates': len(opportunities),
            'distinct_parts': len({item['base_part_number'] for item in opportunities}),
            'sales_order_matches': reason_counts['sales_orders'],
            'customer_quote_matches': reason_counts['customer_quotes'],
        },
        'thresholds': {
            'lookback_months': lookback_months,
            'lookback_cutoff': lookback_cutoff.strftime('%Y-%m-%d'),
            'sell_discount_pct': 35,
            'minimum_sales_or_customer_quotes': 3,
        }
    }


@purchase_suggestions_bp.route('/upload-stock', methods=['POST'])
def upload_stock():
    """Store uploaded stock data temporarily in session"""
    try:
        print("DEBUG: upload_stock route called")
        data = request.get_json()
        print(f"DEBUG: Received data keys: {data.keys() if data else 'None'}")

        stock_data = data.get('stock_data', [])
        mapping = data.get('mapping', {})

        print(f"DEBUG: Stock data rows: {len(stock_data)}")
        print(f"DEBUG: Mapping: {mapping}")

        # Process and store the mapped stock data
        processed_stock = {}

        for i, row in enumerate(stock_data):
            # Get the part number based on mapping
            part_col = mapping.get('part_number')
            qty_col = mapping.get('quantity')
            price_col = mapping.get('unit_price')  # Optional unit price column

            if part_col is not None and qty_col is not None:
                try:
                    part_number = row[int(part_col)]
                    quantity = row[int(qty_col)]

                    # Get unit price if provided (optional)
                    unit_price = None
                    if price_col is not None:
                        try:
                            unit_price = float(row[int(price_col)])
                        except (ValueError, TypeError, IndexError):
                            unit_price = None

                    if part_number and quantity:
                        # Store with part number as key
                        part_key = str(part_number).strip()
                        qty_value = float(quantity)

                        if part_key in processed_stock:
                            # If part already exists, sum quantities and average prices
                            existing_qty = processed_stock[part_key]['quantity']
                            existing_price = processed_stock[part_key].get('unit_price')

                            new_qty = existing_qty + qty_value

                            # Calculate weighted average price if both have prices
                            # Guard against division by zero
                            if new_qty > 0 and existing_price is not None and unit_price is not None:
                                new_price = ((existing_price * existing_qty) + (unit_price * qty_value)) / new_qty
                            elif unit_price is not None:
                                new_price = unit_price
                            else:
                                new_price = existing_price

                            processed_stock[part_key] = {
                                'quantity': new_qty,
                                'unit_price': new_price
                            }
                        else:
                            processed_stock[part_key] = {
                                'quantity': qty_value,
                                'unit_price': unit_price
                            }
                except (ValueError, TypeError, IndexError) as e:
                    if i < 5:  # Only print first 5 errors
                        print(f"DEBUG: Error processing row {i}: {e}")
                    continue

        print(f"DEBUG: Processed {len(processed_stock)} parts")

        # Store in session
        session['uploaded_stock'] = processed_stock
        session.modified = True

        print(f"DEBUG: Session updated with {len(session['uploaded_stock'])} parts")

        for part in parts:
            part['last_sale_date'] = _stringify_date(part.get('last_sale_date'))
        return jsonify({
            'success': True,
            'parts_loaded': len(processed_stock)
        })

    except Exception as e:
        print(f"ERROR in upload_stock: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({
            'success': False,
            'error': str(e)
        }), 400


@purchase_suggestions_bp.route('/clear-stock', methods=['POST'])
def clear_stock():
    """Clear uploaded stock data from session"""
    if 'uploaded_stock' in session:
        del session['uploaded_stock']
        session.modified = True
    return jsonify({'success': True})


@purchase_suggestions_bp.route('/debug/part')
def debug_part():
    """Debug endpoint to check if a specific part exists in uploaded stock"""
    part_to_check = request.args.get('part', '')

    if not part_to_check:
        return jsonify({'success': False, 'message': 'Please provide a part number via ?part=XXX'}), 400

    uploaded_stock = session.get('uploaded_stock', {})

    if not uploaded_stock:
        return jsonify({'success': False, 'message': 'No stock data uploaded'}), 400

    # Try various formats
    results = {
        'searched_for': part_to_check,
        'total_parts_in_stock': len(uploaded_stock),
        'exact_match': part_to_check in uploaded_stock,
        'exact_match_data': uploaded_stock.get(part_to_check, 'N/A'),
        'stripped_match': part_to_check.strip() in uploaded_stock,
        'stripped_match_data': uploaded_stock.get(part_to_check.strip(), 'N/A'),
        'similar_keys': []
    }

    # Find similar keys
    search_lower = part_to_check.lower().strip()
    for key in uploaded_stock.keys():
        if search_lower in key.lower() or key.lower() in search_lower:
            results['similar_keys'].append({
                'key': key,
                'data': uploaded_stock[key]
            })

    return jsonify({'success': True, 'debug_info': results})


@purchase_suggestions_bp.route('/', methods=['GET'])
def purchase_suggestions():
    """Main page for purchase suggestions - shows parts being sold with low stock based on usage"""
    try:
        view_by = request.args.get('view_by', 'part')
        search_query = request.args.get('search', '')
        page = request.args.get('page', 1, type=int)
        per_page = 50

        # Get sorting parameters
        sort_column = request.args.get('sort', 'purchase_priority_score')
        sort_direction = request.args.get('dir', 'desc')

        # Validate sort_direction to prevent SQL injection
        if sort_direction not in ['asc', 'desc']:
            sort_direction = 'desc'

        # Usage-based low stock parameters (configurable)
        TIME_PERIOD_DAYS = 365  # Rolling period for sales data (e.g., last year)
        BUFFER_MONTHS = 2  # Months of buffer stock to trigger "low stock"
        MIN_SALES_FOR_THRESHOLD = 1  # Minimum units sold in period to apply dynamic threshold (otherwise fallback to 1)

        data = []

        with db_cursor() as cursor:
            if view_by == 'part':
                data = _load_part_view(cursor, sort_column, sort_direction, TIME_PERIOD_DAYS, BUFFER_MONTHS,
                                       MIN_SALES_FOR_THRESHOLD)

            elif view_by == 'customer':
                # TODO: Implement customer view with same stock approach
                pass

            elif view_by == 'bom':
                # TODO: Implement BOM view with same stock approach
                pass

        # Apply search filter if provided
        if search_query:
            search_lower = search_query.lower()
            if view_by == 'part':
                data = [p for p in data if
                        search_lower in str(p.get('part_number', '')).lower() or
                        search_lower in str(p.get('system_part_number', '')).lower() or
                        search_lower in str(p.get('base_part_number', '')).lower()]
            elif view_by == 'customer':
                data = [c for c in data if search_lower in str(c.get('customer_name', '')).lower()]
            elif view_by == 'bom':
                data = [b for b in data if
                        search_lower in str(b.get('bom_name', '')).lower() or
                        search_lower in str(b.get('description', '')).lower()]

        # Pagination
        total_items = len(data)
        total_pages = ceil(total_items / per_page)
        start_idx = (page - 1) * per_page
        end_idx = start_idx + per_page
        paginated_data = data[start_idx:end_idx]

        return render_template('purchase_stock_suggestions.html',
                               data=paginated_data,
                               total_parts=total_items,
                               page=page,
                               total_pages=total_pages,
                               per_page=per_page,
                               search_query=search_query,
                               view_by=view_by,
                               sort_column=sort_column,
                               sort_direction=sort_direction)

    except Exception as e:
        print(f"Error in purchase_suggestions: {str(e)}")
        import traceback
        traceback.print_exc()
        return render_template('purchase_stock_suggestions.html',
                               data=[],
                               total_parts=0,
                               page=1,
                               total_pages=0,
                               per_page=per_page,
                               search_query='',
                               view_by='part',
                               sort_column='purchase_priority_score',
                               sort_direction='desc',
                               error=str(e))


@purchase_suggestions_bp.route('/api/email-report-config', methods=['GET'])
def get_email_report_config():
    """Return the saved nightly purchase report email configuration."""
    try:
        return jsonify({'success': True, 'config': _load_email_report_config()})
    except Exception as e:
        logging.exception('Error loading purchase report email config: %s', e)
        return jsonify({'success': False, 'error': str(e)}), 500


@purchase_suggestions_bp.route('/api/email-report-config', methods=['POST'])
def save_email_report_config():
    """Save the nightly purchase report email configuration."""
    try:
        payload = request.get_json(silent=True) or {}
        config = _load_email_report_config()
        _apply_email_report_payload(config, payload)
        saved = _save_email_report_config(config)
        return jsonify({'success': True, 'config': saved})
    except Exception as e:
        logging.exception('Error saving purchase report email config: %s', e)
        return jsonify({'success': False, 'error': str(e)}), 500


@purchase_suggestions_bp.route('/api/email-report-preview', methods=['POST'])
def preview_email_report():
    """Run both report datasets for the current/supplied configuration without sending email."""
    try:
        payload = request.get_json(silent=True) or {}
        config = _load_email_report_config()
        config.update({key: payload[key] for key in payload if key in PURCHASE_REPORT_DEFAULT_CONFIG})
        config['quote_period_days'] = max(1, min(int(config.get('quote_period_days') or 30), 365))
        config['sales_period_days'] = max(1, min(int(config.get('sales_period_days') or 90), 730))
        config['quote_min_occurrences'] = max(1, min(int(config.get('quote_min_occurrences') or 2), 100))
        config['quote_max_sales_ratio_percent'] = max(0, min(float(config.get('quote_max_sales_ratio_percent') or 0), 100))
        config['frequent_min_orders'] = max(1, min(int(config.get('frequent_min_orders') or 3), 100))
        config['max_rows'] = max(1, min(int(config.get('max_rows') or 50), 500))
        report = _load_email_reports(config)
        return jsonify({'success': True, **report})
    except Exception as e:
        logging.exception('Error previewing purchase report email: %s', e)
        return jsonify({'success': False, 'error': str(e)}), 500


@purchase_suggestions_bp.route('/api/send-email-report', methods=['POST'])
def send_email_report_now():
    """Send the configured purchase reports immediately."""
    try:
        payload = request.get_json(silent=True) or {}
        if payload:
            config = _load_email_report_config()
            _apply_email_report_payload(config, payload)
            _save_email_report_config(config)
        result = send_due_purchase_suggestion_reports(force=True)
        status = 200 if result.get('success') else 400
        return jsonify(result), status
    except Exception as e:
        logging.exception('Error sending purchase report email: %s', e)
        return jsonify({'success': False, 'error': str(e)}), 500


@purchase_suggestions_bp.route('/api/send-test-email-report', methods=['POST'])
def send_test_email_report():
    """Send the current report to a one-off test recipient without changing the schedule."""
    try:
        payload = request.get_json(silent=True) or {}
        test_recipient = str(payload.get('test_recipient') or '').strip()
        if not test_recipient:
            return jsonify({'success': False, 'error': 'Test recipient is required'}), 400

        config = _load_email_report_config()
        _apply_email_report_payload(config, payload)
        result = _send_purchase_report_email(
            config,
            [test_recipient],
            update_last_sent=False,
            subject_prefix='[Test] ',
        )
        status = 200 if result.get('success') else 400
        return jsonify(result), status
    except Exception as e:
        logging.exception('Error sending test purchase report email: %s', e)
        return jsonify({'success': False, 'error': str(e)}), 500


@purchase_suggestions_bp.route('/reports/<int:run_id>', methods=['GET'])
@login_required
def view_purchase_report_run(run_id):
    report = _load_purchase_report_run(run_id)
    if not report:
        return render_template(
            'purchase_report_review.html',
            report=None,
            run_id=run_id,
            error='Purchase report run not found.',
        ), 404

    return render_template(
        'purchase_report_review.html',
        report=report,
        run_id=run_id,
        review_sections=_prepare_report_review_sections(report),
        section_labels=REPORT_SECTION_LABELS,
        current_user_id=_current_user_id(),
        current_user_name=_current_user_display_name(),
    )


@purchase_suggestions_bp.route('/api/reports/<int:run_id>/comments', methods=['POST'])
@login_required
def save_purchase_report_comment(run_id):
    try:
        payload = request.get_json(silent=True) or {}
        section = str(payload.get('section') or '').strip()
        base_part_number = str(payload.get('base_part_number') or '').strip()
        comment = payload.get('comment') or ''
        if section not in {section_name for section_name, _ in REPORT_SECTION_KEYS}:
            return jsonify({'success': False, 'error': 'Invalid report section'}), 400
        if not base_part_number:
            return jsonify({'success': False, 'error': 'Part number is required'}), 400

        item = db_execute(
            """
            SELECT id
            FROM purchase_report_run_items
            WHERE run_id = ? AND report_section = ? AND base_part_number = ?
            """,
            (run_id, section, base_part_number),
            fetch='one',
        )
        if not item:
            return jsonify({'success': False, 'error': 'Part is not in this report snapshot'}), 404

        saved = _save_purchase_report_comment(section, base_part_number, comment)
        return jsonify({'success': True, 'comment': saved})
    except Exception as e:
        logging.exception('Error saving purchase report comment: %s', e)
        return jsonify({'success': False, 'error': str(e)}), 500


@purchase_suggestions_bp.route('/api/reports/<int:run_id>/ignored-parts', methods=['POST'])
@login_required
def ignore_purchase_report_parts(run_id):
    try:
        payload = request.get_json(silent=True) or {}
        requested_parts = payload.get('parts') or []
        reason = payload.get('reason') or ''
        if not isinstance(requested_parts, list) or not requested_parts:
            return jsonify({'success': False, 'error': 'Select at least one part to ignore'}), 400

        rows = db_execute(
            """
            SELECT report_section, base_part_number, display_part_number
            FROM purchase_report_run_items
            WHERE run_id = ?
            """,
            (run_id,),
            fetch='all',
        ) or []
        if not rows:
            return jsonify({'success': False, 'error': 'Purchase report run not found'}), 404

        snapshot_parts = {}
        for row in rows:
            key = (row.get('report_section'), _normalize_part_key(row.get('base_part_number')))
            snapshot_parts[key] = row

        parts_to_ignore = []
        for part in requested_parts:
            if not isinstance(part, dict):
                continue
            section = str(part.get('section') or '').strip()
            base_part_number = _normalize_part_key(part.get('base_part_number'))
            row = snapshot_parts.get((section, base_part_number))
            if not row:
                return jsonify({'success': False, 'error': 'One or more selected parts are not in this report snapshot'}), 400
            parts_to_ignore.append({
                'base_part_number': row.get('base_part_number'),
                'display_part_number': row.get('display_part_number') or part.get('display_part_number'),
            })

        saved = _save_ignored_purchase_report_parts(parts_to_ignore, reason=reason)
        return jsonify({'success': True, 'ignored_parts': saved, 'ignored_count': len(saved)})
    except Exception as e:
        logging.exception('Error ignoring purchase report parts: %s', e)
        return jsonify({'success': False, 'error': str(e)}), 500


@purchase_suggestions_bp.route('/api/reports/<int:run_id>/resend', methods=['POST'])
@login_required
def resend_purchase_report_run(run_id):
    try:
        report = _load_purchase_report_run(run_id)
        if not report:
            return jsonify({'success': False, 'error': 'Purchase report run not found'}), 404

        payload = request.get_json(silent=True) or {}
        run = report.get('run') or {}
        configured_recipients = payload.get('recipients') or run.get('recipients') or _load_email_report_config().get('recipients')
        result = _send_purchase_report_email(
            report.get('config') or _load_email_report_config(),
            configured_recipients,
            update_last_sent=False,
            subject_prefix='[Updated] ',
            report=report,
            run_id=run_id,
        )
        status = 200 if result.get('success') else 400
        return jsonify(result), status
    except Exception as e:
        logging.exception('Error resending purchase report run: %s', e)
        return jsonify({'success': False, 'error': str(e)}), 500


@purchase_suggestions_bp.route('/api/speculative-buy-report', methods=['POST'])
def speculative_buy_report():
    """Manual report for supplier quotes that look unusually attractive for stock buys."""
    try:
        payload = request.get_json(silent=True) or {}
        lookback_months = payload.get('lookback_months', 2)
        with db_cursor() as cursor:
            report = _load_speculative_buy_report(cursor, lookback_months=lookback_months)
        return jsonify({
            'success': True,
            **report,
        })
    except Exception as e:
        print(f"Error running speculative buy report: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({
            'success': False,
            'error': str(e),
        }), 500


def _load_part_view(cursor, sort_column, sort_direction, time_period_days, buffer_months, min_sales_threshold):
    if _using_postgres():
        recent_sales_filter = f"AND so.date_entered >= CURRENT_DATE - INTERVAL '{time_period_days} days'"
    else:
        recent_sales_filter = f"AND so.date_entered >= date('now', '-{time_period_days} days')"

    if _using_postgres():
        customer_names_expr = "STRING_AGG(DISTINCT c.name, ', ')"
        bom_names_expr = "STRING_AGG(DISTINCT bh.name, ', ')"
    else:
        customer_names_expr = "GROUP_CONCAT(DISTINCT c.name)"
        bom_names_expr = "GROUP_CONCAT(DISTINCT bh.name)"

    query = f'''
        SELECT 
            pn.base_part_number,
            pn.part_number,
            pn.system_part_number,
            COUNT(DISTINCT so.id) as order_count,
            COUNT(DISTINCT so.customer_id) as customer_count,
            MAX(so.date_entered) as last_sale_date,
            SUM(sol.quantity) as total_quantity_sold,
            AVG(sol.price) as avg_sale_price,
            MIN(sol.price) as min_sale_price,
            MAX(sol.price) as max_sale_price,
            {customer_names_expr} as customer_names,
            {bom_names_expr} as bom_names
        FROM part_numbers pn
        LEFT JOIN sales_order_lines sol ON pn.base_part_number = sol.base_part_number
        LEFT JOIN sales_orders so ON sol.sales_order_id = so.id
        LEFT JOIN customers c ON so.customer_id = c.id
        LEFT JOIN bom_lines bl ON pn.base_part_number = bl.base_part_number
        LEFT JOIN bom_headers bh ON bl.bom_header_id = bh.id
        WHERE sol.id IS NOT NULL {recent_sales_filter}
        GROUP BY pn.base_part_number
    '''

    base_parts = [dict(row) for row in (_execute_with_cursor(cursor, query, fetch='all') or [])]
    for part in base_parts:
        part['last_sale_date'] = _stringify_date(part.get('last_sale_date'))
    months_in_period = time_period_days / 30.4375
    parts = []

    for part in base_parts:
        base_part_number = part['base_part_number']

        stock_data = _execute_with_cursor(
            cursor,
            '''
            SELECT SUM(available_quantity) as total_stock
            FROM stock_movements
            WHERE base_part_number = ?
              AND movement_type = 'IN'
              AND available_quantity > 0
            ''',
            (base_part_number,),
            fetch='one'
        )

        stock_qty = float(stock_data['total_stock']) if (stock_data and stock_data['total_stock']) else 0.0
        part['stock_quantity'] = stock_qty

        qty_sold = float(part.get('total_quantity_sold') or 0)
        if qty_sold >= min_sales_threshold:
            avg_monthly_sales = qty_sold / months_in_period
            dynamic_threshold = avg_monthly_sales * buffer_months
            part['avg_monthly_sales'] = round(avg_monthly_sales, 2)
            part['suggested_reorder_point'] = round(dynamic_threshold, 2)
            threshold = dynamic_threshold
        else:
            threshold = 1
            part['avg_monthly_sales'] = 0
            part['suggested_reorder_point'] = 1

        if stock_qty < threshold:
            part['low_stock_threshold'] = threshold

            vqs = get_multiple_vqs(cursor, part['base_part_number'], limit=3)
            part['vq_available'] = vqs[0] if vqs else None
            part['all_vqs'] = vqs

            recency_factor = 1.0
            if part.get('last_sale_date'):
                try:
                    last_sale_value = part['last_sale_date']
                    if isinstance(last_sale_value, datetime):
                        last_sale = last_sale_value
                    elif isinstance(last_sale_value, date):
                        last_sale = datetime.combine(last_sale_value, datetime.min.time())
                    else:
                        last_sale = datetime.strptime(str(last_sale_value), '%Y-%m-%d')
                    days_since = (datetime.now() - last_sale).days
                    recency_factor = max(0, 1 - (days_since / time_period_days))
                except ValueError:
                    pass

            avg_price = float(part.get('avg_sale_price') or 0)
            customer_count = float(part.get('customer_count') or 0)
            order_count = float(part.get('order_count') or 0)

            economic_demand = (qty_sold * avg_price * 0.2) / 1000
            customer_breadth = (customer_count * 10 * 0.4)
            order_freq = (order_count * 5 * 0.4)

            pps_raw = (economic_demand + customer_breadth + order_freq) / time_period_days * recency_factor
            part['purchase_priority_score'] = min(50, pps_raw * 50)

            parts.append(part)

    sort_key_map = {
        'part_number': lambda x: (x.get('part_number') or '').lower(),
        'system_part_number': lambda x: (x.get('system_part_number') or '').lower(),
        'order_count': lambda x: x.get('order_count') or 0,
        'customer_count': lambda x: x.get('customer_count') or 0,
        'total_quantity_sold': lambda x: x.get('total_quantity_sold') or 0,
        'avg_sale_price': lambda x: x.get('avg_sale_price') or 0,
        'last_sale_date': lambda x: x.get('last_sale_date') or '',
        'bom_names': lambda x: (x.get('bom_names') or '').lower(),
        'vq_available': lambda x: (
            0 if not x.get('vq_available') else
            x.get('vq_available', {}).get('vendor_price_gbp', 0)
        ),
        'purchase_priority_score': lambda x: x.get('purchase_priority_score', 0),
        'stock_quantity': lambda x: x.get('stock_quantity', 0),
        'avg_monthly_sales': lambda x: x.get('avg_monthly_sales', 0),
        'suggested_reorder_point': lambda x: x.get('suggested_reorder_point', 0)
    }

    if sort_column in sort_key_map:
        parts.sort(
            key=sort_key_map[sort_column],
            reverse=(sort_direction == 'desc')
        )
    else:
        parts.sort(
            key=lambda x: x.get('purchase_priority_score', 0),
            reverse=True
        )

    return parts


def get_vq_availability(cursor, base_part_number):
    """Get the best VQ availability for a part with currency conversion to GBP and currency symbol"""
    vq = _execute_with_cursor(
        cursor,
        '''
        SELECT 
            vl.*,
            v.vq_number,
            v.status,
            v.entry_date,
            v.expiration_date,
            s.name as supplier_name,
            c.currency_code,
            c.symbol as currency_symbol,
            vl.foreign_currency,
            vl.quoted_date
        FROM vq_lines vl
        JOIN vqs v ON vl.vq_id = v.id
        LEFT JOIN suppliers s ON v.supplier_id = s.id
        LEFT JOIN currencies c ON v.currency_id = c.id
        WHERE vl.base_part_number = ?
        AND (v.expiration_date IS NULL OR v.expiration_date >= date('now'))
        AND v.status != 'Cancelled'
        ORDER BY vl.vendor_price ASC
        LIMIT 1
        ''',
        (base_part_number,),
        fetch='one',
    )

    if not vq:
        return None

    vq_dict = dict(vq)
    vq_dict['entry_date'] = _stringify_date(vq_dict.get('entry_date'))
    vq_dict['expiration_date'] = _stringify_date(vq_dict.get('expiration_date'))

    # Convert vendor price to GBP for comparison with sales prices (which are in GBP)
    original_price = vq_dict.get('vendor_price')
    currency_code = vq_dict.get('currency_code', 'GBP')
    currency_symbol = vq_dict.get('currency_symbol', '£')

    if original_price and currency_code:
        vq_dict['vendor_price_gbp'] = convert_vq_price_to_gbp(original_price, currency_code)
        vq_dict['vendor_price_original'] = original_price
        vq_dict['vendor_price_currency'] = currency_code
        vq_dict['currency_symbol'] = currency_symbol
    else:
        vq_dict['vendor_price_gbp'] = original_price
        vq_dict['vendor_price_original'] = original_price
        vq_dict['vendor_price_currency'] = 'GBP'
        vq_dict['currency_symbol'] = '£'

    return vq_dict


@purchase_suggestions_bp.route('/api/part-details/<base_part_number>')
def get_part_details(base_part_number):
    """Get detailed information about a specific part for the modal"""
    try:
        with db_cursor() as cursor:
            part_info = _execute_with_cursor(
                cursor,
                '''
                SELECT 
                    pn.part_number,
                    pn.system_part_number,
                    pn.base_part_number,
                    pc.category_name
                FROM part_numbers pn
                LEFT JOIN part_categories pc ON pn.category_id = pc.category_id
                WHERE pn.base_part_number = ?
                LIMIT 1
                ''',
                (base_part_number,),
                fetch='one'
            )

            if not part_info:
                return jsonify({'error': 'Part not found'}), 404

            part_dict = dict(part_info)

            sales_history_rows = _execute_with_cursor(
                cursor,
                '''
                SELECT 
                    so.id as order_id,
                    so.date_entered,
                    c.name as customer_name,
                    sol.quantity,
                    sol.price,
                    (sol.quantity * sol.price) as line_total
                FROM sales_order_lines sol
                INNER JOIN sales_orders so ON sol.sales_order_id = so.id
                LEFT JOIN customers c ON so.customer_id = c.id
                WHERE sol.base_part_number = ?
                ORDER BY so.date_entered DESC
                LIMIT 20
                ''',
                (base_part_number,),
                fetch='all'
            ) or []
            sales_history = []
            for row in sales_history_rows:
                sale = dict(row)
                sale['price'] = _coerce_numeric(sale.get('price'))
                sale['quantity'] = _coerce_numeric(sale.get('quantity'))
                sale['line_total'] = _coerce_numeric(sale.get('line_total'))
                sale['date_entered'] = _stringify_date(sale.get('date_entered'))
                sales_history.append(sale)

            boms = _execute_with_cursor(
                cursor,
                '''
                SELECT 
                    bh.id,
                    bh.name,
                    bh.description,
                    bl.quantity as bom_quantity,
                    bl.guide_price
                FROM bom_lines bl
                INNER JOIN bom_headers bh ON bl.bom_header_id = bh.id
                WHERE bl.base_part_number = ?
                ORDER BY bh.name
                ''',
                (base_part_number,),
                fetch='all'
            ) or []

            sales_summary_raw = _execute_with_cursor(
                cursor,
                '''
                SELECT 
                    COUNT(DISTINCT so.id) as total_orders,
                    COUNT(DISTINCT so.customer_id) as total_customers,
                    SUM(sol.quantity) as total_quantity_sold,
                    AVG(sol.price) as avg_price,
                    MIN(sol.price) as min_price,
                    MAX(sol.price) as max_price,
                    MAX(so.date_entered) as last_sale_date
                FROM sales_order_lines sol
                INNER JOIN sales_orders so ON sol.sales_order_id = so.id
                WHERE sol.base_part_number = ?
                ''',
                (base_part_number,),
                fetch='one'
            )

            sales_summary = dict(sales_summary_raw) if sales_summary_raw else {}
            numeric_summary_keys = [
                'total_orders',
                'total_customers',
                'total_quantity_sold',
                'avg_price',
                'min_price',
                'max_price'
            ]
            for key in numeric_summary_keys:
                if key in sales_summary:
                    sales_summary[key] = _coerce_numeric(sales_summary[key])

            vqs = []
            vqs_raw = _execute_with_cursor(
                cursor,
                '''
                SELECT 
                    vl.id as vq_line_id,
                    v.id as vq_id,
                    v.vq_number,
                    s.name as supplier_name,
                    vl.quantity_quoted,
                    vl.vendor_price,
                    vl.lead_days,
                    v.entry_date,
                    v.expiration_date,
                    v.status,
                    c.currency_code,
                    c.symbol as currency_symbol,
                    vl.quoted_date
                FROM vq_lines vl
                INNER JOIN vqs v ON vl.vq_id = v.id
                LEFT JOIN suppliers s ON v.supplier_id = s.id
                LEFT JOIN currencies c ON v.currency_id = c.id
                WHERE vl.base_part_number = ?
                ORDER BY v.entry_date DESC
                LIMIT 10
                ''',
                (base_part_number,),
                fetch='all'
            ) or []

            for vq_raw in vqs_raw:
                vq_dict = dict(vq_raw)
                original_price = _coerce_numeric(vq_dict.get('vendor_price'))
                currency_code = vq_dict.get('currency_code', 'GBP')
                currency_symbol = vq_dict.get('currency_symbol', '£')

                vq_dict['entry_date'] = _stringify_date(vq_dict.get('entry_date'))
                vq_dict['expiration_date'] = _stringify_date(vq_dict.get('expiration_date'))

                if original_price and currency_code:
                    vq_dict['vendor_price_gbp'] = convert_vq_price_to_gbp(original_price, currency_code)
                    vq_dict['vendor_price_original'] = original_price
                    vq_dict['vendor_price_currency'] = currency_code
                    vq_dict['currency_symbol'] = currency_symbol
                else:
                    fallback_price = _coerce_numeric(vq_dict.get('vendor_price'))
                    vq_dict['vendor_price_gbp'] = fallback_price
                    vq_dict['vendor_price_original'] = fallback_price
                    vq_dict['vendor_price_currency'] = 'GBP'
                    vq_dict['currency_symbol'] = '£'

                vqs.append(vq_dict)

        return jsonify({
            'success': True,
            'part': part_dict,
            'sales_history': sales_history,
            'boms': [dict(row) for row in boms],
            'sales_summary': dict(sales_summary) if sales_summary else {},
            'vqs': vqs
        })

    except Exception as e:
        print(f"Error getting part details: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@purchase_suggestions_bp.route('/api/out-of-stock')
def get_out_of_stock_parts():
    """API endpoint to get parts being sold but not in stock"""
    try:
        with db_cursor() as cursor:
            query = '''
                SELECT 
                    pn.base_part_number,
                    pn.part_number,
                    pn.system_part_number,
                    COUNT(DISTINCT so.id) as order_count,
                    COUNT(DISTINCT so.customer_id) as customer_count,
                    SUM(sol.quantity) as total_quantity_sold
                FROM part_numbers pn
                LEFT JOIN sales_order_lines sol ON pn.base_part_number = sol.base_part_number
                LEFT JOIN sales_orders so ON sol.sales_order_id = so.id
                WHERE sol.id IS NOT NULL
                GROUP BY pn.base_part_number
            '''

        all_sold_parts = [dict(row) for row in (_execute_with_cursor(cursor, query, fetch='all') or [])]
        for part in all_sold_parts:
            part['last_sale_date'] = _stringify_date(part.get('last_sale_date'))

        # Filter to only parts NOT in stock using same method as parts list
        out_of_stock = []
        for part in all_sold_parts:
            base_part_number = part['base_part_number']

            # Check stock using same query as parts list analyzer
            stock_data = _execute_with_cursor(
                cursor,
                '''
                SELECT SUM(available_quantity) as total_stock
                FROM stock_movements
                WHERE base_part_number = ?
                  AND movement_type = 'IN'
                  AND available_quantity > 0
                ''',
                (base_part_number,),
                fetch='one'
            )

            stock_qty = stock_data['total_stock'] if (stock_data and stock_data['total_stock']) else 0

            if stock_qty == 0:
                part['stock_quantity'] = stock_qty
                out_of_stock.append(part)

        # Sort by order count desc
        out_of_stock.sort(key=lambda x: x.get('order_count', 0), reverse=True)

        return jsonify({
            'success': True,
            'parts': out_of_stock[:100]  # Limit to 100
        })

    except Exception as e:
        print(f"Error getting out of stock parts: {str(e)}")
        return jsonify({'error': str(e)}), 500


@purchase_suggestions_bp.route('/api/customer-parts/<int:customer_id>')
def get_customer_parts(customer_id):
    """Get parts not in stock for a specific customer"""
    try:
        with db_cursor() as cursor:
            use_uploaded_stock = 'uploaded_stock' in session
            uploaded_stock = session.get('uploaded_stock', {})

            # Get customer info
            customer = _execute_with_cursor(
                cursor,
                'SELECT id, name FROM customers WHERE id = ?',
                (customer_id,),
                fetch='one'
            )
            if not customer:
                return jsonify({'error': 'Customer not found'}), 404

            parts = []
            if use_uploaded_stock:
                # Get all parts sold to this customer
                query = '''
                    SELECT 
                        pn.base_part_number,
                        pn.part_number,
                        pn.system_part_number,
                        COUNT(DISTINCT so.id) as order_count,
                        SUM(sol.quantity) as total_quantity,
                        AVG(sol.price) as avg_price,
                        MAX(so.date_entered) as last_sale_date
                    FROM sales_order_lines sol
                    INNER JOIN sales_orders so ON sol.sales_order_id = so.id
                    INNER JOIN part_numbers pn ON sol.base_part_number = pn.base_part_number
                    WHERE so.customer_id = ?
                    GROUP BY pn.base_part_number
                '''
                all_parts = [dict(row) for row in (_execute_with_cursor(cursor, query, (customer_id,), fetch='all') or [])]

                # Filter to only parts NOT in stock
                parts = []
                for part in all_parts:
                    part_identifiers = [part.get('part_number'), part.get('system_part_number'),
                                        part.get('base_part_number')]
                    in_stock = False
                    for identifier in part_identifiers:
                        if identifier and str(identifier).strip() in uploaded_stock:
                            stock_info = uploaded_stock[str(identifier).strip()]
                            if isinstance(stock_info, dict):
                                stock_qty = stock_info.get('quantity', 0)
                            else:
                                stock_qty = stock_info
                            if stock_qty > 0:
                                in_stock = True
                                break
                    if not in_stock:
                        # Add VQ info
                        vqs = get_multiple_vqs(cursor, part['base_part_number'], limit=3)
                        part['vq_available'] = vqs[0] if vqs else None
                        part['all_vqs'] = vqs
                        parts.append(part)
            else:
                query = '''
                    SELECT 
                        pn.base_part_number,
                        pn.part_number,
                        pn.system_part_number,
                        COUNT(DISTINCT so.id) as order_count,
                        SUM(sol.quantity) as total_quantity,
                        AVG(sol.price) as avg_price,
                        MAX(so.date_entered) as last_sale_date
                    FROM sales_order_lines sol
                    INNER JOIN sales_orders so ON sol.sales_order_id = so.id
                    INNER JOIN part_numbers pn ON sol.base_part_number = pn.base_part_number
                    LEFT JOIN stock_movements sm ON pn.base_part_number = sm.base_part_number
                        AND sm.movement_type = 'IN'
                        AND sm.available_quantity > 0
                    WHERE so.customer_id = ?
                    AND sm.id IS NULL
                    GROUP BY pn.base_part_number
                    ORDER BY order_count DESC
                '''
                parts = [dict(row) for row in (_execute_with_cursor(cursor, query, (customer_id,), fetch='all') or [])]

                # Add VQ info
                for part in parts:
                    vqs = get_multiple_vqs(cursor, part['base_part_number'], limit=3)
                    part['vq_available'] = vqs[0] if vqs else None
                    part['all_vqs'] = vqs
                    part['last_sale_date'] = _stringify_date(part.get('last_sale_date'))

            for part in parts:
                part['last_sale_date'] = _stringify_date(part.get('last_sale_date'))

            return jsonify({
                'success': True,
                'customer': {'id': customer['id'], 'name': customer['name']},
                'parts': parts,
                'total_parts': len(parts)
            })

    except Exception as e:
        print(f"Error getting customer parts: {str(e)}")
        return jsonify({'error': str(e)}), 500


@purchase_suggestions_bp.route('/api/bom-parts/<int:bom_id>')
def get_bom_parts(bom_id):
    """Get parts not in stock for a specific BOM with price difference calculations"""
    try:
        with db_cursor() as cursor:
            use_uploaded_stock = 'uploaded_stock' in session
            uploaded_stock = session.get('uploaded_stock', {})

            bom = _execute_with_cursor(
                cursor,
                'SELECT id, name, description FROM bom_headers WHERE id = ?',
                (bom_id,),
                fetch='one'
            )
            if not bom:
                return jsonify({'error': 'BOM not found'}), 404

            parts = []
            if use_uploaded_stock:
                query = '''
                    SELECT 
                        pn.base_part_number,
                        pn.part_number,
                        pn.system_part_number,
                        bl.quantity as bom_quantity,
                        bl.guide_price,
                        COUNT(DISTINCT so.id) as order_count,
                        SUM(sol.quantity) as total_sold,
                        MAX(so.date_entered) as last_sale_date
                    FROM bom_lines bl
                    INNER JOIN part_numbers pn ON bl.base_part_number = pn.base_part_number
                    LEFT JOIN sales_order_lines sol ON pn.base_part_number = sol.base_part_number
                    LEFT JOIN sales_orders so ON sol.sales_order_id = so.id
                    WHERE bl.bom_header_id = ?
                    GROUP BY pn.base_part_number
                '''
                all_parts = [dict(row) for row in (_execute_with_cursor(cursor, query, (bom_id,), fetch='all') or [])]

                for part in all_parts:
                    if part.get('order_count', 0) > 0:
                        part_identifiers = [part.get('part_number'), part.get('system_part_number'),
                                            part.get('base_part_number')]
                        in_stock = False
                        for identifier in part_identifiers:
                            if identifier and str(identifier).strip() in uploaded_stock:
                                stock_info = uploaded_stock[str(identifier).strip()]
                                stock_qty = stock_info.get('quantity', 0) if isinstance(stock_info, dict) else stock_info
                                if stock_qty > 0:
                                    in_stock = True
                                    break
                        if not in_stock:
                            vq_info = get_vq_availability(cursor, part['base_part_number'])
                            part['vq_available'] = vq_info
                            part['all_vqs'] = get_multiple_vqs(cursor, part['base_part_number'], limit=3)

                            if vq_info and part.get('guide_price'):
                                guide_price = part['guide_price']
                                vq_price_gbp = vq_info.get('vendor_price_gbp', 0)
                                if guide_price > 0 and vq_price_gbp > 0:
                                    part['price_difference_pct'] = ((guide_price - vq_price_gbp) / guide_price) * 100
                                else:
                                    part['price_difference_pct'] = None
                            else:
                                part['price_difference_pct'] = None

                            parts.append(part)
            else:
                query = '''
                    SELECT 
                        pn.base_part_number,
                        pn.part_number,
                        pn.system_part_number,
                        bl.quantity as bom_quantity,
                        bl.guide_price,
                        COUNT(DISTINCT so.id) as order_count,
                        SUM(sol.quantity) as total_sold,
                        MAX(so.date_entered) as last_sale_date
                    FROM bom_lines bl
                    INNER JOIN part_numbers pn ON bl.base_part_number = pn.base_part_number
                    LEFT JOIN sales_order_lines sol ON pn.base_part_number = sol.base_part_number
                    LEFT JOIN sales_orders so ON sol.sales_order_id = so.id
                    LEFT JOIN stock_movements sm ON pn.base_part_number = sm.base_part_number
                        AND sm.movement_type = 'IN'
                        AND sm.available_quantity > 0
                    WHERE bl.bom_header_id = ?
                    AND sol.id IS NOT NULL
                    AND sm.id IS NULL
                    GROUP BY pn.base_part_number
                    ORDER BY order_count DESC
                '''
                parts = [dict(row) for row in (_execute_with_cursor(cursor, query, (bom_id,), fetch='all') or [])]

                for part in parts:
                    vq_info = get_vq_availability(cursor, part['base_part_number'])
                    part['vq_available'] = vq_info
                    part['all_vqs'] = get_multiple_vqs(cursor, part['base_part_number'], limit=3)

                    if vq_info and part.get('guide_price'):
                        guide_price = part['guide_price']
                        vq_price_gbp = vq_info.get('vendor_price_gbp', 0)
                        if guide_price > 0 and vq_price_gbp > 0:
                            part['price_difference_pct'] = ((guide_price - vq_price_gbp) / guide_price) * 100
                        else:
                            part['price_difference_pct'] = None
                    else:
                        part['price_difference_pct'] = None

        return jsonify({
            'success': True,
            'bom': {'id': bom['id'], 'name': bom['name'], 'description': bom['description']},
            'parts': parts,
            'total_parts': len(parts)
        })

    except Exception as e:
        print(f"Error getting BOM parts: {str(e)}")
        return jsonify({'error': str(e)}), 500


@purchase_suggestions_bp.route('/api/debug-bom/<int:bom_id>')
def debug_bom(bom_id):
    """Debug route to verify BOM filtering logic"""
    use_uploaded_stock = 'uploaded_stock' in session
    uploaded_stock = session.get('uploaded_stock', {})

    with db_cursor() as cursor:
        total_row = _execute_with_cursor(
            cursor,
            'SELECT COUNT(*) as count FROM bom_lines WHERE bom_header_id = ?',
            (bom_id,),
            fetch='one'
        )
        total = total_row['count'] if total_row else 0

        if use_uploaded_stock:
            with_sales_query = '''
                SELECT COUNT(DISTINCT pn.base_part_number) as count
                FROM bom_lines bl
                INNER JOIN part_numbers pn ON bl.base_part_number = pn.base_part_number
                LEFT JOIN sales_order_lines sol ON pn.base_part_number = sol.base_part_number
                LEFT JOIN sales_orders so ON sol.sales_order_id = so.id
                WHERE bl.bom_header_id = ?
                AND sol.id IS NOT NULL
            '''
            with_sales_row = _execute_with_cursor(cursor, with_sales_query, (bom_id,), fetch='one')
            with_sales = with_sales_row['count'] if with_sales_row else 0

            parts_query = '''
                SELECT DISTINCT 
                    pn.base_part_number,
                    pn.part_number,
                    pn.system_part_number
                FROM bom_lines bl
                INNER JOIN part_numbers pn ON bl.base_part_number = pn.base_part_number
                LEFT JOIN sales_order_lines sol ON pn.base_part_number = sol.base_part_number
                WHERE bl.bom_header_id = ?
                AND sol.id IS NOT NULL
            '''
            parts_with_sales = _execute_with_cursor(cursor, parts_query, (bom_id,), fetch='all') or []

            in_stock_count = 0
            for part in parts_with_sales:
                part_identifiers = [part[1], part[2], part[0]]
                for identifier in part_identifiers:
                    if identifier and str(identifier).strip() in uploaded_stock:
                        stock_info = uploaded_stock[str(identifier).strip()]
                        stock_qty = stock_info.get('quantity', 0) if isinstance(stock_info, dict) else stock_info
                        if stock_qty > 0:
                            in_stock_count += 1
                            break
        else:
            with_sales_row = _execute_with_cursor(
                cursor,
                '''
                SELECT COUNT(DISTINCT pn.base_part_number) as count
                FROM bom_lines bl
                INNER JOIN part_numbers pn ON bl.base_part_number = pn.base_part_number
                INNER JOIN sales_order_lines sol ON pn.base_part_number = sol.base_part_number
                WHERE bl.bom_header_id = ?
                ''',
                (bom_id,),
                fetch='one'
            )
            with_sales = with_sales_row['count'] if with_sales_row else 0

            in_stock_row = _execute_with_cursor(
                cursor,
                '''
                SELECT COUNT(DISTINCT bl.base_part_number) as count
                FROM bom_lines bl
                INNER JOIN part_numbers pn ON bl.base_part_number = pn.base_part_number
                INNER JOIN sales_order_lines sol ON pn.base_part_number = sol.base_part_number
                INNER JOIN stock_movements sm ON pn.base_part_number = sm.base_part_number
                    AND sm.movement_type = 'IN'
                    AND sm.available_quantity > 0
                WHERE bl.bom_header_id = ?
                ''',
                (bom_id,),
                fetch='one'
            )
            in_stock_count = in_stock_row['count'] if in_stock_row else 0

        not_in_stock = with_sales - in_stock_count

    return jsonify({
        'total_parts_in_bom': total,
        'parts_with_sales_history': with_sales,
        'parts_with_sales_and_in_stock': in_stock_count,
        'parts_with_sales_not_in_stock': not_in_stock,
        'using_uploaded_stock': use_uploaded_stock
    })

def get_multiple_vqs(cursor, base_part_number, limit=3):
    """Get multiple VQs for a part, ordered by price"""
    vqs_raw = _execute_with_cursor(
        cursor,
        '''
        SELECT 
            vl.*,
            v.vq_number,
            v.status,
            v.entry_date,
            v.expiration_date,
            s.name as supplier_name,
            c.currency_code,
            c.symbol as currency_symbol,
            vl.foreign_currency,
            vl.quoted_date
        FROM vq_lines vl
        JOIN vqs v ON vl.vq_id = v.id
        LEFT JOIN suppliers s ON v.supplier_id = s.id
        LEFT JOIN currencies c ON v.currency_id = c.id
        WHERE vl.base_part_number = ?
        AND (v.expiration_date IS NULL OR v.expiration_date >= date('now'))
        AND v.status != 'Cancelled'
        ORDER BY vl.vendor_price ASC
        LIMIT ?
        ''',
        (base_part_number, limit),
        fetch='all',
    )

    vqs = []
    for vq_raw in vqs_raw:
        vq_dict = dict(vq_raw)
        vq_dict['entry_date'] = _stringify_date(vq_dict.get('entry_date'))
        vq_dict['expiration_date'] = _stringify_date(vq_dict.get('expiration_date'))
        original_price = vq_dict.get('vendor_price')
        currency_code = vq_dict.get('currency_code', 'GBP')
        currency_symbol = vq_dict.get('currency_symbol', '£')

        if original_price and currency_code:
            vq_dict['vendor_price_gbp'] = convert_vq_price_to_gbp(original_price, currency_code)
            vq_dict['vendor_price_original'] = original_price
            vq_dict['vendor_price_currency'] = currency_code
            vq_dict['currency_symbol'] = currency_symbol
        else:
            vq_dict['vendor_price_gbp'] = original_price
            vq_dict['vendor_price_original'] = original_price
            vq_dict['vendor_price_currency'] = 'GBP'
            vq_dict['currency_symbol'] = '£'

        vqs.append(vq_dict)

    return vqs
