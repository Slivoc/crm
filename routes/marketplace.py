"""
Airbus Marketplace routes
"""

from flask import Blueprint, jsonify, request, send_file, render_template
import calendar
import csv
from datetime import datetime, date
import io
import logging
import os
import time
import json
import re

from airbus_marketplace_helper import (
    suggest_marketplace_category,
    suggest_categories_batch,
    get_available_categories
)
from airbus_marketplace_export import export_parts_to_airbus_marketplace_csv
from models import get_db
from routes.portal_api import _analyze_quote_internal, get_portal_setting
from integrations.mirakl.client import MiraklClient, MiraklError
from integrations.mirakl.services.offers import build_offers_csv
from openpyxl import load_workbook
from openai import OpenAI

logger = logging.getLogger(__name__)

marketplace_bp = Blueprint('marketplace', __name__)

PERPLEXITY_API_KEY = os.getenv('PERPLEXITY_API_KEY') or "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"


def _months_ago(reference: date, months: int) -> date:
    if months <= 0:
        return reference

    year = reference.year
    month = reference.month - months
    while month <= 0:
        month += 12
        year -= 1

    day = min(reference.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def _get_mirakl_config():
    base_url = os.getenv('MIRAKL_BASE_URL') or get_portal_setting('mirakl_base_url')
    api_key = os.getenv('MIRAKL_API_KEY') or get_portal_setting('mirakl_api_key')
    shop_id = os.getenv('MIRAKL_SHOP_ID') or get_portal_setting('mirakl_shop_id')
    return base_url, api_key, shop_id


def _get_mirakl_client():
    base_url, api_key, shop_id = _get_mirakl_config()
    if not base_url or not api_key:
        return None, "MIRAKL_BASE_URL and MIRAKL_API_KEY must be configured."
    return MiraklClient(base_url, api_key, shop_id=shop_id), None


def _mask_api_key(value):
    if not value:
        return ''
    text = str(value)
    if len(text) <= 8:
        return '*' * len(text)
    return f"{text[:4]}...{text[-4:]}"


def _upsert_portal_setting(cursor, key, value):
    cursor.execute(
        "UPDATE portal_settings SET setting_value = ?, date_modified = CURRENT_TIMESTAMP WHERE setting_key = ?",
        (value, key),
    )
    if cursor.rowcount == 0:
        cursor.execute(
            "INSERT INTO portal_settings (setting_key, setting_value, date_modified) VALUES (?, ?, CURRENT_TIMESTAMP)",
            (key, value),
        )


def _get_marketplace_customer_id():
    value = get_portal_setting('marketplace_customer_id')
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _get_perplexity_client():
    if not PERPLEXITY_API_KEY:
        return None
    return OpenAI(api_key=PERPLEXITY_API_KEY, base_url="https://api.perplexity.ai")


def _extract_json_object(raw_content):
    start = raw_content.find("{")
    end = raw_content.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    return raw_content[start:end + 1]


def _coerce_int(value, *, default=0):
    if value is None:
        return default
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(round(value))
    text = str(value).strip()
    if not text:
        return default
    try:
        return int(round(float(text.replace(',', '.'))))
    except ValueError:
        return default


def _coerce_price(value):
    if value is None:
        return ''
    if isinstance(value, bool):
        return ''
    if isinstance(value, (int, float)):
        return round(float(value), 2)
    text = str(value).strip()
    if not text:
        return ''
    try:
        return round(float(text.replace(',', '.')), 2)
    except ValueError:
        return ''


def _dedupe_csv_headers(csv_bytes):
    text = csv_bytes.decode('utf-8-sig', errors='replace')
    if not text:
        return csv_bytes

    newline_index = text.find('\n')
    if newline_index == -1:
        header_line = text
        remainder = ''
        line_ending = '\n'
    else:
        header_line = text[:newline_index].rstrip('\r')
        remainder = text[newline_index + 1:]
        line_ending = '\n'

    if '\t' in header_line:
        delimiter = '\t'
    elif ',' in header_line:
        delimiter = ','
    elif ';' in header_line:
        delimiter = ';'
    else:
        return csv_bytes

    header = next(csv.reader([header_line], delimiter=delimiter))
    seen = {}
    updated_header = []
    changed = False
    for name in header:
        normalized = " ".join(str(name).split())
        count = seen.get(normalized, 0) + 1
        seen[normalized] = count
        if count > 1:
            updated_header.append(f"{normalized} {count}")
            changed = True
        else:
            updated_header.append(normalized)

    if not changed:
        return csv_bytes

    updated_line = delimiter.join(updated_header)
    rebuilt = f"{updated_line}{line_ending}{remainder}"
    return rebuilt.encode('utf-8')


def _normalize_import_header(header):
    return re.sub(r'[^a-z0-9]+', '', str(header or '').strip().lower())


def _parse_import_bool(value):
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if not text:
        return None
    if text in ('1', 'true', 'yes', 'y', 'on'):
        return True
    if text in ('0', 'false', 'no', 'n', 'off'):
        return False
    return None


def _decode_csv_bytes(csv_bytes):
    try:
        return csv_bytes.decode('utf-8-sig')
    except UnicodeDecodeError:
        return csv_bytes.decode('latin-1', errors='replace')


def _detect_csv_delimiter(csv_text):
    first_line = csv_text.splitlines()[0] if csv_text else ''
    if '\t' in first_line:
        return '\t'
    if ';' in first_line and first_line.count(';') > first_line.count(','):
        return ';'
    return ','


def _read_import_rows(uploaded_file, filename):
    extension = os.path.splitext(filename.lower())[1]

    if extension == '.xlsx':
        workbook = load_workbook(io.BytesIO(uploaded_file.read()), read_only=True, data_only=True)
        worksheet = workbook.active
        row_iter = worksheet.iter_rows(values_only=True)
        headers = next(row_iter, None)
        if not headers:
            return []

        output_rows = []
        for row_number, values in enumerate(row_iter, start=2):
            row_data = {}
            for idx, raw_header in enumerate(headers):
                header = str(raw_header or '').strip()
                if not header:
                    continue
                row_data[header] = values[idx] if idx < len(values) else None
            output_rows.append((row_number, row_data))
        return output_rows

    csv_bytes = uploaded_file.read()
    if not csv_bytes:
        return []
    csv_text = _decode_csv_bytes(csv_bytes)
    delimiter = _detect_csv_delimiter(csv_text)
    reader = csv.DictReader(io.StringIO(csv_text), delimiter=delimiter)
    output_rows = []
    for row_number, row in enumerate(reader, start=2):
        output_rows.append((row_number, row))
    return output_rows


def _build_marketplace_updates_from_row(row_data, valid_categories):
    # Airbus export column -> internal field mapping.
    column_map = {
        'code': 'part_number',
        'sku': 'part_number',
        'productid': 'part_number',
        'mkpcategory': 'mkp_category',
        'descriptionen': 'mkp_description',
        'name': 'mkp_name',
        'productsummaryen': 'mkp_product_summary',
        'productpresentationen': 'mkp_product_presentation',
        'productunit': 'mkp_product_unit',
        'packagecontent': 'mkp_package_content',
        'packagecontentunit': 'mkp_package_content_unit',
        'thirdlevel': 'mkp_third_level',
        'dangerous': 'mkp_dangerous',
        'eccn': 'mkp_eccn',
        'serialized': 'mkp_serialized',
        'logcard': 'mkp_log_card',
        'easaf1': 'mkp_easaf1',
    }

    extracted = {}
    for raw_header, raw_value in (row_data or {}).items():
        normalized = _normalize_import_header(raw_header)
        mapped_field = column_map.get(normalized)
        if not mapped_field:
            continue
        # Prefer first identifier field encountered.
        if mapped_field in extracted and mapped_field == 'part_number':
            continue
        extracted[mapped_field] = raw_value

    part_ref = str(extracted.get('part_number') or '').strip()
    if not part_ref:
        return None, None, "Missing part identifier (expected one of: code, sku, product-id)."

    updates = {}
    text_fields = (
        'mkp_description',
        'mkp_name',
        'mkp_product_summary',
        'mkp_product_presentation',
        'mkp_product_unit',
        'mkp_package_content_unit',
        'mkp_third_level',
        'mkp_eccn',
    )
    bool_fields = ('mkp_dangerous', 'mkp_serialized', 'mkp_log_card', 'mkp_easaf1')

    category_value = extracted.get('mkp_category')
    if category_value is not None:
        category_text = str(category_value).strip()
        if category_text:
            if category_text not in valid_categories:
                return part_ref, None, f"Invalid category: {category_text}"
            updates['mkp_category'] = category_text

    for field in text_fields:
        value = extracted.get(field)
        if value is None:
            continue
        value_text = str(value).strip()
        if value_text:
            updates[field] = value_text

    package_content_value = extracted.get('mkp_package_content')
    if package_content_value is not None:
        package_content_text = str(package_content_value).strip()
        if package_content_text:
            parsed = _coerce_int(package_content_text, default=None)
            if parsed is None:
                return part_ref, None, f"Invalid packageContent value: {package_content_text}"
            updates['mkp_package_content'] = parsed

    for field in bool_fields:
        parsed_bool = _parse_import_bool(extracted.get(field))
        if parsed_bool is not None:
            updates[field] = 1 if parsed_bool else 0

    if not updates:
        return part_ref, None, "No importable marketplace detail fields found in row."

    return part_ref, updates, None


def _normalize_prefixes(prefixes):
    cleaned = []
    for prefix in prefixes or []:
        value = (prefix or "").strip()
        if value:
            cleaned.append(value)
    return list(dict.fromkeys(cleaned))


def _guess_prefixes_from_part_number(part_number):
    if not part_number:
        return []
    token = re.split(r"[-/\\s]+", part_number.strip())[0]
    if token and token != part_number:
        return [token]
    return []


def _build_prefix_conditions(prefixes):
    conditions = []
    params = []
    for prefix in prefixes:
        like_value = f"{prefix}%"
        conditions.append("(pn.part_number LIKE ? OR pn.base_part_number LIKE ?)")
        params.extend([like_value, like_value])
    return conditions, params


def _get_portal_estimates(parts, customer_id):
    if not parts:
        return {}

    estimate_response = _analyze_quote_internal(customer_id, parts)
    estimate_data = (
        estimate_response[0].get_json()
        if isinstance(estimate_response, tuple)
        else estimate_response.get_json()
    )
    if not estimate_data or not estimate_data.get('success'):
        logger.info(
            "Marketplace export estimates unavailable: customer_id=%s success=%s",
            customer_id,
            estimate_data.get('success') if estimate_data else None,
        )
        return {}

    results = estimate_data.get('results', [])
    return {
        (item.get('base_part_number') or item.get('part_number')): item
        for item in results
        if item.get('base_part_number') or item.get('part_number')
    }


@marketplace_bp.route('/import-details-file', methods=['POST'])
def import_marketplace_details_file():
    """
    Import Airbus marketplace CSV/XLSX and update non-commercial marketplace fields.
    Price/quantity/stock values are intentionally ignored.
    """
    db = None
    try:
        file = request.files.get('file')
        if not file or not file.filename:
            return jsonify({'success': False, 'error': 'CSV/XLSX file is required'}), 400

        filename = file.filename
        extension = os.path.splitext(filename.lower())[1]
        if extension not in ('.csv', '.xlsx'):
            return jsonify({'success': False, 'error': 'Only .csv and .xlsx files are supported'}), 400

        rows = _read_import_rows(file, filename)
        if not rows:
            return jsonify({'success': False, 'error': 'Uploaded file is empty'}), 400

        valid_categories = set(get_available_categories())
        db = get_db()
        cursor = db.cursor()

        processed = 0
        updated = 0
        changed = 0
        skipped_no_match = 0
        skipped_no_updates = 0
        row_errors = []

        for row_number, row_data in rows:
            processed += 1
            part_ref, field_updates, row_error = _build_marketplace_updates_from_row(row_data, valid_categories)
            if row_error:
                skipped_no_updates += 1
                if len(row_errors) < 20:
                    row_errors.append(f"Row {row_number}: {row_error}")
                continue

            cursor.execute(
                "SELECT 1 FROM part_numbers WHERE base_part_number = ? OR part_number = ? LIMIT 1",
                (part_ref, part_ref),
            )
            part_exists = cursor.fetchone() is not None
            if not part_exists:
                skipped_no_match += 1
                if len(row_errors) < 20:
                    row_errors.append(f"Row {row_number}: Part not found for '{part_ref}'.")
                continue

            updated += 1
            set_clause = ", ".join(f"{field} = ?" for field in field_updates.keys())
            params = list(field_updates.values()) + [part_ref, part_ref]
            cursor.execute(
                f"UPDATE part_numbers SET {set_clause} WHERE base_part_number = ? OR part_number = ?",
                params
            )
            if cursor.rowcount > 0:
                changed += cursor.rowcount

        db.commit()

        return jsonify({
            'success': True,
            'message': 'Marketplace detail import complete.',
            'summary': {
                'processed_rows': processed,
                'updated_rows': updated,
                'changed_rows': changed,
                'skipped_no_match': skipped_no_match,
                'skipped_no_updates': skipped_no_updates,
            },
            'errors': row_errors,
        }), 200
    except Exception as e:
        logger.exception("Error importing marketplace details file")
        if db:
            db.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500


@marketplace_bp.route('/export-page', methods=['GET'])
def export_page():
    """Render the marketplace export page"""
    from flask import render_template
    mirakl_base_url = get_portal_setting('mirakl_base_url')
    mirakl_shop_id = get_portal_setting('mirakl_shop_id')
    mirakl_api_key = get_portal_setting('mirakl_api_key')
    return render_template(
        'marketplace_export.html',
        mirakl_base_url=mirakl_base_url,
        mirakl_shop_id=mirakl_shop_id,
        mirakl_api_key_set=bool(mirakl_api_key),
    )


@marketplace_bp.route('/mirakl', methods=['GET'])
def mirakl_connection_page():
    """Render the Mirakl connection page."""
    portal_base_url = get_portal_setting('mirakl_base_url')
    portal_shop_id = get_portal_setting('mirakl_shop_id')
    portal_api_key = get_portal_setting('mirakl_api_key')
    env_base_url = os.getenv('MIRAKL_BASE_URL')
    env_shop_id = os.getenv('MIRAKL_SHOP_ID')
    env_api_key = os.getenv('MIRAKL_API_KEY')
    display_base_url = portal_base_url or env_base_url
    display_shop_id = portal_shop_id or env_shop_id
    return render_template(
        'marketplace_mirakl.html',
        mirakl_base_url=display_base_url,
        mirakl_shop_id=display_shop_id,
        mirakl_api_key_set=bool(portal_api_key or env_api_key),
        mirakl_env_configured=bool(env_base_url or env_shop_id or env_api_key),
    )


@marketplace_bp.route('/categories', methods=['GET'])
def get_marketplace_categories():
    """Get all available Airbus Marketplace categories"""
    try:
        categories = get_available_categories()
        return jsonify({'categories': categories}), 200
    except Exception as e:
        logger.exception("Error getting marketplace categories")
        return jsonify({'error': str(e)}), 500


@marketplace_bp.route('/categorization-tool', methods=['GET'])
def categorization_tool_page():
    """Render the marketplace categorization tool page"""
    return render_template('marketplace_category_tool.html')


@marketplace_bp.route('/categorization-tool/suggest', methods=['POST'])
def categorization_tool_suggest():
    data = request.get_json() or {}
    part_number = (data.get('part_number') or '').strip()
    description = (data.get('description') or '').strip()
    use_perplexity = bool(data.get('use_perplexity', True))

    if not part_number:
        return jsonify({'success': False, 'error': 'part_number is required'}), 400

    categories = get_available_categories()
    category_list = "\n".join([f"- {cat}" for cat in categories])

    if use_perplexity:
        client = _get_perplexity_client()
        if not client:
            return jsonify({'success': False, 'error': 'Perplexity API key not configured'}), 400

        system_message = f"""You are an aviation hardware analyst. Return ONLY valid JSON.

Choose one category from this exact list:
{category_list}

Rules:
1. Output ONLY JSON, no markdown
2. If unsure, use "Marketplace Categories/Hardware and Electrical/Miscellaneous"
3. Reasoning must be max 12 words, no quotes
4. Suggest 0-3 prefixes to apply for mass updates, or an empty list

Output format:
{{
  "category": "Marketplace Categories/Hardware and Electrical/CategoryName",
  "confidence": "high|medium|low",
  "reasoning": "short reason",
  "prefixes": ["EXAMPLE", "SERIES"]
}}"""

        user_message = f"""Part number: {part_number}
Description: {description or "None"}

Suggest a category and any useful prefixes or series identifiers."""

        try:
            response = client.chat.completions.create(
                model="sonar-pro",
                messages=[
                    {"role": "system", "content": system_message},
                    {"role": "user", "content": user_message},
                ],
                temperature=0.2,
                max_tokens=500,
            )
            raw_content = response.choices[0].message.content.strip()
            try:
                result = json.loads(raw_content)
            except json.JSONDecodeError:
                extracted = _extract_json_object(raw_content)
                result = json.loads(extracted) if extracted else {}
        except Exception as exc:
            logger.exception("Perplexity suggestion failed")
            return jsonify({'success': False, 'error': str(exc)}), 500
    else:
        result = suggest_marketplace_category(part_number, description, '')

    category = result.get('category') if isinstance(result, dict) else None
    confidence = result.get('confidence', 'low') if isinstance(result, dict) else 'low'
    reasoning = result.get('reasoning', '') if isinstance(result, dict) else ''
    prefixes = _normalize_prefixes(result.get('prefixes') if isinstance(result, dict) else [])

    if category not in categories:
        category = "Marketplace Categories/Hardware and Electrical/Miscellaneous"
        confidence = "low"
        if not reasoning:
            reasoning = "invalid_category"

    if not prefixes and not use_perplexity:
        prefixes = _guess_prefixes_from_part_number(part_number)

    return jsonify({
        'success': True,
        'category': category,
        'confidence': confidence,
        'reasoning': reasoning,
        'prefixes': prefixes,
    }), 200


@marketplace_bp.route('/categorization-tool/uncategorized', methods=['GET'])
def categorization_tool_uncategorized():
    limit = request.args.get('limit', 50)
    try:
        limit = max(1, min(int(limit), 200))
    except (TypeError, ValueError):
        limit = 50

    db = get_db()
    cursor = db.cursor()
    cursor.execute(
        """
        SELECT base_part_number, part_number
        FROM part_numbers
        WHERE mkp_category IS NULL OR mkp_category = ''
        ORDER BY part_number
        LIMIT ?
        """,
        (limit,)
    )
    rows = cursor.fetchall()
    parts = [
        {
            'base_part_number': row['base_part_number'],
            'part_number': row['part_number'] or row['base_part_number'],
        }
        for row in rows
    ]
    return jsonify({'success': True, 'parts': parts}), 200


@marketplace_bp.route('/categorization-tool/uncategorized-search', methods=['GET'])
def categorization_tool_uncategorized_search():
    query = (request.args.get('q') or '').strip().lower()
    limit = request.args.get('limit', 200)
    try:
        limit = max(1, min(int(limit), 200))
    except (TypeError, ValueError):
        limit = 200

    if not query:
        return jsonify({'success': True, 'parts': []}), 200

    like_value = f"%{query}%"
    db = get_db()
    cursor = db.cursor()
    cursor.execute(
        """
        SELECT base_part_number, part_number
        FROM part_numbers
        WHERE (mkp_category IS NULL OR mkp_category = '')
          AND (LOWER(part_number) LIKE ? OR LOWER(base_part_number) LIKE ?)
        ORDER BY part_number
        LIMIT ?
        """,
        (like_value, like_value, limit)
    )
    rows = cursor.fetchall()
    parts = [
        {
            'base_part_number': row['base_part_number'],
            'part_number': row['part_number'] or row['base_part_number'],
        }
        for row in rows
    ]
    return jsonify({'success': True, 'parts': parts}), 200


@marketplace_bp.route('/categorization-tool/prefix-preview', methods=['POST'])
def categorization_tool_prefix_preview():
    data = request.get_json() or {}
    prefixes = _normalize_prefixes(data.get('prefixes', []))
    only_uncategorized = bool(data.get('only_uncategorized', True))

    if not prefixes:
        return jsonify({'success': True, 'total': 0, 'sample': []}), 200

    conditions, params = _build_prefix_conditions(prefixes)
    where_clause = " OR ".join(conditions)
    filters = [f"({where_clause})"]
    if only_uncategorized:
        filters.append("(pn.mkp_category IS NULL OR pn.mkp_category = '')")

    where_sql = " AND ".join(filters)

    db = get_db()
    cursor = db.cursor()

    count_query = f"SELECT COUNT(*) AS total FROM part_numbers pn WHERE {where_sql}"
    cursor.execute(count_query, params)
    total_row = cursor.fetchone()
    total = total_row['total'] if total_row else 0

    sample_query = f"""
        SELECT pn.base_part_number, pn.part_number, pn.mkp_category
        FROM part_numbers pn
        WHERE {where_sql}
        ORDER BY pn.part_number
        LIMIT 20
    """
    cursor.execute(sample_query, params)
    rows = cursor.fetchall()
    sample = [
        {
            'base_part_number': row['base_part_number'],
            'part_number': row['part_number'],
            'mkp_category': row['mkp_category'],
        }
        for row in rows
    ]

    return jsonify({'success': True, 'total': total, 'sample': sample}), 200


@marketplace_bp.route('/categorization-tool/apply-prefix', methods=['POST'])
def categorization_tool_apply_prefix():
    data = request.get_json() or {}
    prefixes = _normalize_prefixes(data.get('prefixes', []))
    category = (data.get('category') or '').strip()
    only_uncategorized = bool(data.get('only_uncategorized', True))

    if not prefixes:
        return jsonify({'success': False, 'error': 'prefixes are required'}), 400
    if not category:
        return jsonify({'success': False, 'error': 'category is required'}), 400

    valid_categories = get_available_categories()
    if category not in valid_categories:
        return jsonify({'success': False, 'error': 'Invalid category'}), 400

    conditions, params = _build_prefix_conditions(prefixes)
    where_clause = " OR ".join(conditions)
    filters = [f"({where_clause})"]
    if only_uncategorized:
        filters.append("(mkp_category IS NULL OR mkp_category = '')")
    where_sql = " AND ".join(filters)

    db = get_db()
    cursor = db.cursor()
    update_query = f"UPDATE part_numbers pn SET mkp_category = ? WHERE {where_sql}"
    cursor.execute(update_query, [category] + params)
    db.commit()

    return jsonify({'success': True, 'updated_count': cursor.rowcount}), 200


@marketplace_bp.route('/categorization-tool/apply-list', methods=['POST'])
def categorization_tool_apply_list():
    data = request.get_json() or {}
    base_part_numbers = data.get('base_part_numbers') or []
    category = (data.get('category') or '').strip()

    if not base_part_numbers:
        return jsonify({'success': False, 'error': 'base_part_numbers are required'}), 400
    if not category:
        return jsonify({'success': False, 'error': 'category is required'}), 400

    valid_categories = get_available_categories()
    if category not in valid_categories:
        return jsonify({'success': False, 'error': 'Invalid category'}), 400

    placeholders = ','.join('?' * len(base_part_numbers))
    query = f"UPDATE part_numbers SET mkp_category = ? WHERE base_part_number IN ({placeholders})"

    db = get_db()
    cursor = db.cursor()
    cursor.execute(query, [category] + base_part_numbers)
    db.commit()

    return jsonify({'success': True, 'updated_count': cursor.rowcount}), 200


@marketplace_bp.route('/suggest-category', methods=['POST'])
def suggest_category():
    """
    Suggest category for a single part

    POST body:
    {
        "part_number": "ABC123",
        "description": "Optional description",
        "additional_info": "Optional additional context"
    }
    """
    try:
        data = request.get_json()
        part_number = data.get('part_number')
        description = data.get('description', '')
        additional_info = data.get('additional_info', '')

        if not part_number:
            return jsonify({'error': 'part_number is required'}), 400

        suggestion = suggest_marketplace_category(
            part_number,
            description,
            additional_info
        )

        if not suggestion:
            return jsonify({'error': 'Unable to suggest category'}), 500

        return jsonify(suggestion), 200

    except Exception as e:
        logger.exception("Error suggesting category")
        return jsonify({'error': str(e)}), 500


@marketplace_bp.route('/suggest-categories-batch', methods=['POST'])
def suggest_categories_batch_route():
    """
    Suggest categories for multiple parts

    POST body:
    {
        "parts": [
            {"part_number": "ABC123", "description": "..."},
            {"part_number": "XYZ789", "description": "..."}
        ]
    }
    """
    try:
        data = request.get_json()
        parts = data.get('parts', [])

        if not parts:
            return jsonify({'error': 'parts array is required'}), 400

        results = suggest_categories_batch(parts)

        return jsonify({'results': results}), 200

    except Exception as e:
        logger.exception("Error suggesting categories in batch")
        return jsonify({'error': str(e)}), 500


@marketplace_bp.route('/update-category/<base_part_number>', methods=['POST'])
def update_part_category(base_part_number):
    """
    Update marketplace category for a part

    POST body:
    {
        "mkp_category": "Marketplace Categories/Hardware and Electrical/Bolts"
    }
    """
    try:
        data = request.get_json()
        mkp_category = data.get('mkp_category')

        if not mkp_category:
            return jsonify({'error': 'mkp_category is required'}), 400

        # Validate category is in allowed list
        valid_categories = get_available_categories()
        if mkp_category not in valid_categories:
            return jsonify({'error': 'Invalid category'}), 400

        # Update part in database
        db = get_db()
        cursor = db.cursor()
        cursor.execute(
            "UPDATE part_numbers SET mkp_category = ? WHERE base_part_number = ?",
            (mkp_category, base_part_number)
        )
        db.commit()

        if cursor.rowcount == 0:
            return jsonify({'error': 'Part not found'}), 404

        return jsonify({'success': True, 'message': 'Category updated'}), 200

    except Exception as e:
        logger.exception("Error updating part category")
        if db:
            db.rollback()
        return jsonify({'error': str(e)}), 500


@marketplace_bp.route('/update-marketplace-fields/<base_part_number>', methods=['POST'])
def update_marketplace_fields(base_part_number):
    """
    Update all marketplace fields for a part

    POST body:
    {
        "mkp_category": "Marketplace Categories/Hardware and Electrical/Bolts",
        "mkp_description": "Custom description",
        "mkp_name": "Custom name",
        "mkp_product_summary": "Product summary text",
        "mkp_product_presentation": "Product presentation text",
        "mkp_product_unit": "EA",
        "mkp_package_content": 1,
        "mkp_package_content_unit": "EA",
        "mkp_third_level": "EA",
        "mkp_dangerous": false,
        "mkp_eccn": "",
        "mkp_serialized": false,
        "mkp_log_card": false,
        "mkp_easaf1": false
    }
    """
    db = None
    try:
        data = request.get_json() or {}

        # Validate category if provided
        mkp_category = data.get('mkp_category')
        if mkp_category:
            valid_categories = get_available_categories()
            if mkp_category not in valid_categories:
                return jsonify({'error': 'Invalid category'}), 400

        # Build update query dynamically based on provided fields
        allowed_fields = [
            'mkp_category', 'mkp_description', 'mkp_name', 'mkp_product_summary',
            'mkp_product_presentation', 'mkp_product_unit', 'mkp_package_content',
            'mkp_package_content_unit', 'mkp_third_level', 'mkp_dangerous',
            'mkp_eccn', 'mkp_serialized', 'mkp_log_card', 'mkp_easaf1'
        ]

        updates = []
        params = []
        for field in allowed_fields:
            if field in data:
                updates.append(f"{field} = ?")
                value = data[field]
                # Convert booleans properly for SQLite
                if field in ('mkp_dangerous', 'mkp_serialized', 'mkp_log_card', 'mkp_easaf1'):
                    value = 1 if value else 0
                params.append(value)

        if not updates:
            return jsonify({'error': 'No fields to update'}), 400

        params.append(base_part_number)
        query = f"UPDATE part_numbers SET {', '.join(updates)} WHERE base_part_number = ?"

        db = get_db()
        cursor = db.cursor()
        cursor.execute(query, params)
        db.commit()

        if cursor.rowcount == 0:
            return jsonify({'error': 'Part not found'}), 404

        return jsonify({'success': True, 'message': 'Marketplace fields updated'}), 200

    except Exception as e:
        logger.exception("Error updating marketplace fields")
        if db:
            db.rollback()
        return jsonify({'error': str(e)}), 500


@marketplace_bp.route('/get-parts-for-export', methods=['POST'])
def get_parts_for_export():
    """
    Get filtered parts with portal pricing data for export preview

    POST body:
    {
        "stock_filter": "all|stock_only|no_stock",
        "category_filter": "all|categorized_only|missing_only",
        "pricing_only": true/false,
        "part_number_search": optional part number search
    }
    """
    try:
        data = request.get_json() or {}

        stock_filter = data.get('stock_filter', 'all')
        category_filter = data.get('category_filter', 'all')
        pricing_only = data.get('pricing_only', False)
        part_number_search = data.get('part_number_search', '').strip()
        max_results = _coerce_int(data.get('max_results'), default=0)
        if max_results < 0:
            max_results = 0

        logger.info(
            "Marketplace export parts request: stock_filter=%s category_filter=%s "
            "pricing_only=%s part_number_search=%s max_results=%s",
            stock_filter,
            category_filter,
            pricing_only,
            part_number_search or "<none>",
            max_results or "<none>",
        )

        customer_id = _get_marketplace_customer_id()

        db = get_db()
        cursor = db.cursor()

        query = """
            SELECT
                pn.base_part_number,
                pn.part_number,
                pn.mkp_category,
                pn.mkp_description,
                pn.mkp_name,
                pn.mkp_product_summary,
                pn.mkp_product_presentation,
                pn.mkp_product_unit,
                pn.mkp_package_content,
                pn.mkp_package_content_unit,
                pn.mkp_third_level,
                pn.mkp_dangerous,
                pn.mkp_eccn,
                pn.mkp_serialized,
                pn.mkp_log_card,
                pn.mkp_easaf1
            FROM part_numbers pn
            WHERE 1=1
        """

        params = []

        # Apply category filter
        if category_filter == 'categorized_only':
            query += " AND pn.mkp_category IS NOT NULL AND pn.mkp_category != ''"
        elif category_filter == 'missing_only':
            query += " AND (pn.mkp_category IS NULL OR pn.mkp_category = '')"

        # Apply pricing filter (mirror portal recency rules to avoid loading all parts)
        if pricing_only:
            so_months = int(get_portal_setting('sales_order_recency_months', 6))
            vq_months = int(get_portal_setting('vq_recency_months', 12))
            po_months = int(get_portal_setting('po_recency_months', 12))
            cq_months = int(get_portal_setting('cq_recency_months', 6))
            min_stock = int(get_portal_setting('min_stock_threshold', 1))
            show_estimates = bool(int(get_portal_setting('show_estimated_prices', 1)))

            today = datetime.utcnow().date()
            so_cutoff = _months_ago(today, so_months)
            vq_cutoff = _months_ago(today, vq_months)
            po_cutoff = _months_ago(today, po_months)
            cq_cutoff = _months_ago(today, cq_months)

            pricing_filters = [
                """
                EXISTS (
                    SELECT 1
                    FROM stock_movements sm
                    WHERE sm.base_part_number = pn.base_part_number
                      AND sm.movement_type = 'IN'
                      AND sm.available_quantity >= ?
                )
                """,
                """
                EXISTS (
                    SELECT 1
                    FROM cq_lines cl
                    JOIN cqs c ON cl.cq_id = c.id
                    WHERE cl.base_part_number = pn.base_part_number
                      AND c.entry_date >= ?
                      AND cl.unit_price > 0
                      AND cl.is_no_quote = FALSE
                )
                """,
                """
                EXISTS (
                    SELECT 1
                    FROM sales_order_lines sol
                    JOIN sales_orders so ON sol.sales_order_id = so.id
                    WHERE sol.base_part_number = pn.base_part_number
                      AND so.date_entered >= ?
                      AND sol.price > 0
                )
                """,
                """
                EXISTS (
                    SELECT 1
                    FROM customer_quote_lines cql
                    JOIN parts_list_lines pll ON pll.id = cql.parts_list_line_id
                    WHERE pll.base_part_number = pn.base_part_number
                      AND cql.quoted_status = 'quoted'
                      AND cql.quote_price_gbp > 0
                      AND COALESCE(cql.is_no_bid, 0) = 0
                      AND cql.date_created >= ?
                )
                """,
                """
                EXISTS (
                    SELECT 1
                    FROM parts_list_supplier_quote_lines sql
                    JOIN parts_list_supplier_quotes sq ON sq.id = sql.supplier_quote_id
                    JOIN parts_list_lines pll ON pll.id = sql.parts_list_line_id
                    WHERE pll.base_part_number = pn.base_part_number
                      AND sql.unit_price > 0
                      AND (sql.is_no_bid = FALSE OR sql.is_no_bid IS NULL)
                      AND COALESCE(sq.quote_date, sq.date_created) >= ?
                )
                """,
            ]

            params.extend([
                min_stock,
                cq_cutoff,
                so_cutoff,
                cq_cutoff,
                vq_cutoff,
            ])

            if customer_id:
                pricing_filters.append(
                    """
                    EXISTS (
                        SELECT 1
                        FROM portal_customer_pricing pcp
                        WHERE pcp.customer_id = ?
                          AND pcp.base_part_number = pn.base_part_number
                          AND pcp.is_active = TRUE
                          AND (pcp.valid_from IS NULL OR pcp.valid_from <= ?)
                          AND (pcp.valid_until IS NULL OR pcp.valid_until >= ?)
                    )
                    """
                )
                params.extend([customer_id, today, today])

            if show_estimates:
                pricing_filters.extend([
                    """
                    EXISTS (
                        SELECT 1
                        FROM vq_lines vl
                        JOIN vqs v ON vl.vq_id = v.id
                        WHERE vl.base_part_number = pn.base_part_number
                          AND v.entry_date >= ?
                          AND vl.vendor_price > 0
                    )
                    """,
                    """
                    EXISTS (
                        SELECT 1
                        FROM purchase_order_lines pol
                        JOIN purchase_orders po ON pol.purchase_order_id = po.id
                        WHERE pol.base_part_number = pn.base_part_number
                          AND po.date_issued >= ?
                          AND pol.price > 0
                    )
                    """,
                ])
                params.extend([vq_cutoff, po_cutoff])

            query += " AND (" + " OR ".join(pricing_filters) + ")"

        # Apply part number search
        if part_number_search:
            query += " AND (pn.part_number LIKE ? OR pn.base_part_number LIKE ?)"
            params.append(f"%{part_number_search}%")
            params.append(f"%{part_number_search}%")

        query += " ORDER BY pn.part_number"
        if max_results:
            query += " LIMIT ?"
            params.append(max_results)

        cursor.execute(query, params)
        rows = cursor.fetchall()
        logger.info("Marketplace export query returned %s parts", len(rows))

        parts_payload = [
            {'part_number': row['part_number'] or row['base_part_number'], 'quantity': 1}
            for row in rows
        ]
        estimates_start = time.monotonic()
        estimates = _get_portal_estimates(parts_payload, customer_id)
        logger.info(
            "Marketplace export estimates: requested=%s returned=%s took=%.2fs",
            len(parts_payload),
            len(estimates),
            time.monotonic() - estimates_start,
        )

        parts = []
        filtered_stock = 0
        filtered_no_stock = 0
        filtered_pricing = 0
        for row in rows:
            base_part_number = row['base_part_number']
            estimate = estimates.get(base_part_number) or estimates.get(row['part_number'])
            estimated_price = estimate.get('estimated_price') if estimate else None
            in_stock = bool(estimate.get('in_stock')) if estimate else False
            stock_qty = estimate.get('stock_quantity') if estimate else None

            if stock_filter == 'stock_only' and not in_stock:
                filtered_stock += 1
                continue
            if stock_filter == 'no_stock' and in_stock:
                filtered_no_stock += 1
                continue
            if pricing_only and estimated_price is None:
                filtered_pricing += 1
                continue

            parts.append({
                'base_part_number': base_part_number,
                'part_number': row['part_number'],
                'mkp_category': row['mkp_category'],
                'mkp_description': row['mkp_description'],
                'mkp_name': row['mkp_name'],
                'mkp_product_summary': row['mkp_product_summary'],
                'mkp_product_presentation': row['mkp_product_presentation'],
                'mkp_product_unit': row['mkp_product_unit'],
                'mkp_package_content': row['mkp_package_content'],
                'mkp_package_content_unit': row['mkp_package_content_unit'],
                'mkp_third_level': row['mkp_third_level'],
                'mkp_dangerous': row['mkp_dangerous'],
                'mkp_eccn': row['mkp_eccn'],
                'mkp_serialized': row['mkp_serialized'],
                'mkp_log_card': row['mkp_log_card'],
                'mkp_easaf1': row['mkp_easaf1'],
                'description': '',
                'manufacturer': '',
                'estimated_price': estimated_price,
                'price_source': estimate.get('price_source') if estimate else None,
                'estimated_lead_days': estimate.get('estimated_lead_days') if estimate else None,
                'currency': estimate.get('currency') if estimate else None,
                'in_stock': in_stock,
                'stock_qty': stock_qty if stock_qty is not None else 0,
            })

        logger.info(
            "Marketplace export parts response: total=%s returned=%s "
            "filtered_stock=%s filtered_no_stock=%s filtered_pricing=%s",
            len(rows),
            len(parts),
            filtered_stock,
            filtered_no_stock,
            filtered_pricing,
        )
        return jsonify({'success': True, 'parts': parts}), 200

    except Exception as e:
        logger.exception("Error getting parts for export")
        return jsonify({'success': False, 'error': str(e)}), 500

@marketplace_bp.route('/export', methods=['POST'])
def export_to_marketplace():
    """
    Export selected parts to Airbus Marketplace format with pricing

    POST body (as form data from JS):
    {
        "export_data": {
            "base_part_numbers": ["ABC", "XYZ"],
            "default_quantity": 1
        }
    }
    """
    try:
        # Get export_data from form (sent by JS)
        import json
        export_data_str = request.form.get('export_data')
        if not export_data_str:
            return jsonify({'error': 'No export data provided'}), 400

        export_data = json.loads(export_data_str)
        base_part_numbers = export_data.get('base_part_numbers', [])
        default_quantity = int(export_data.get('default_quantity') or 1)

        if not base_part_numbers:
            return jsonify({'error': 'No parts selected for export'}), 400

        db = get_db()
        cursor = db.cursor()

        placeholders = ','.join('?' * len(base_part_numbers))
        query = f"""
            SELECT
                pn.base_part_number,
                pn.part_number,
                pn.mkp_category,
                pn.mkp_description,
                pn.mkp_name,
                pn.mkp_product_summary,
                pn.mkp_product_presentation,
                pn.mkp_product_unit,
                pn.mkp_package_content,
                pn.mkp_package_content_unit,
                pn.mkp_third_level,
                pn.mkp_dangerous,
                pn.mkp_eccn,
                pn.mkp_serialized,
                pn.mkp_log_card,
                pn.mkp_easaf1
            FROM part_numbers pn
            WHERE pn.base_part_number IN ({placeholders})
        """

        cursor.execute(query, base_part_numbers)
        rows = cursor.fetchall()

        if not rows:
            return jsonify({'error': 'No parts found to export'}), 404

        customer_id = _get_marketplace_customer_id()
        parts_payload = [
            {'part_number': row['part_number'] or row['base_part_number'], 'quantity': 1}
            for row in rows
        ]
        estimates = _get_portal_estimates(parts_payload, customer_id)

        default_lead_days = _coerce_int(get_portal_setting('default_lead_time_days', 7), default=7)

        # Calculate prices based on portal estimates
        parts_data = []
        for row in rows:
            estimate = estimates.get(row['base_part_number']) or estimates.get(row['part_number'])
            price = estimate.get('estimated_price') if estimate else None
            lead_time = estimate.get('estimated_lead_days') if estimate else None
            in_stock = bool(estimate.get('in_stock')) if estimate else False
            stock_qty = estimate.get('stock_quantity') if estimate else None

            quantity = default_quantity
            stock_qty_value = _coerce_int(stock_qty, default=0)
            if in_stock and stock_qty_value:
                quantity = stock_qty_value

            if lead_time is None:
                lead_time = 0 if in_stock else default_lead_days
            lead_time_value = _coerce_int(lead_time, default=default_lead_days)

            parts_data.append({
                'base_part_number': row['base_part_number'],
                'part_number': row['part_number'],
                'mkp_category': row['mkp_category'],
                'mkp_description': row['mkp_description'],
                'mkp_name': row['mkp_name'],
                'mkp_product_summary': row['mkp_product_summary'],
                'mkp_product_presentation': row['mkp_product_presentation'],
                'mkp_product_unit': row['mkp_product_unit'],
                'mkp_package_content': row['mkp_package_content'],
                'mkp_package_content_unit': row['mkp_package_content_unit'],
                'mkp_third_level': row['mkp_third_level'],
                'mkp_dangerous': row['mkp_dangerous'],
                'mkp_eccn': row['mkp_eccn'],
                'mkp_serialized': row['mkp_serialized'],
                'mkp_log_card': row['mkp_log_card'],
                'mkp_easaf1': row['mkp_easaf1'],
                'description': '',
                'manufacturer': '',
                'quantity': quantity,
                'price': _coerce_price(price),
                'condition': 'New',
                'lead_time_days': lead_time_value,
            })

        # Generate CSV file
        csv_file = export_parts_to_airbus_marketplace_csv(parts_data)

        # Generate filename with timestamp
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f"AH_Marketplace_Upload_{timestamp}.csv"

        return send_file(
            csv_file,
            mimetype='text/csv',
            as_attachment=True,
            download_name=filename
        )

    except Exception as e:
        logger.exception("Error exporting to marketplace")
        return jsonify({'error': str(e)}), 500


@marketplace_bp.route('/auto-categorize', methods=['POST'])
def auto_categorize_parts():
    """
    Auto-categorize parts in batches with progress tracking

    POST body:
    {
        "overwrite": false,
        "base_part_numbers": [],
        "batch_size": 10,  // Process this many at a time
        "offset": 0  // Start from this position
    }
    """
    try:
        data = request.get_json() or {}
        overwrite = data.get('overwrite', False)
        base_part_numbers = data.get('base_part_numbers', [])
        batch_size = data.get('batch_size', 10)
        offset = data.get('offset', 0)

        db = get_db()
        cursor = db.cursor()

        # Build query
        if base_part_numbers:
            placeholders = ','.join('?' * len(base_part_numbers))
            query = f"""
                SELECT base_part_number, part_number
                FROM part_numbers 
                WHERE base_part_number IN ({placeholders})
            """
            params = base_part_numbers
        else:
            query = """
                SELECT base_part_number, part_number
                FROM part_numbers
            """
            if not overwrite:
                query += " WHERE mkp_category IS NULL OR mkp_category = ''"
            params = []

        query += f" LIMIT {batch_size} OFFSET {offset}"

        cursor.execute(query, params)
        rows = cursor.fetchall()

        if not rows:
            return jsonify({
                'success': True,
                'message': 'All parts processed',
                'updated_count': 0,
                'total_in_batch': 0,
                'has_more': False
            }), 200

        # Process this batch
        parts_list = [
            {
                'part_number': row['part_number'] or row['base_part_number'],
                'description': '',
                'additional_info': ''
            }
            for row in rows
        ]

        suggestions = suggest_categories_batch(parts_list)

        # Update database
        updated_count = 0
        batch_details = []
        for row, suggestion in zip(rows, suggestions):
            base_part_number = row['base_part_number']
            suggested_category = suggestion.get('suggested_category')
            was_updated = False

            if suggested_category:
                cursor.execute(
                    "UPDATE part_numbers SET mkp_category = ? WHERE base_part_number = ?",
                    (suggested_category, base_part_number)
                )
                updated_count += 1
                was_updated = True

            batch_details.append({
                'base_part_number': base_part_number,
                'part_number': row['part_number'],
                'suggested_category': suggested_category,
                'confidence': suggestion.get('confidence'),
                'reasoning': suggestion.get('reasoning'),
                'updated': was_updated
            })

        db.commit()

        # Check if there are more parts to process
        cursor.execute(query.replace(f"LIMIT {batch_size} OFFSET {offset}", f"LIMIT 1 OFFSET {offset + batch_size}"), params)
        has_more = cursor.fetchone() is not None

        return jsonify({
            'success': True,
            'message': f'Processed batch of {len(rows)} parts',
            'updated_count': updated_count,
            'total_in_batch': len(rows),
            'has_more': has_more,
            'next_offset': offset + batch_size,
            'batch_details': batch_details
        }), 200

    except Exception as e:
        logger.exception("Error auto-categorizing parts")
        if db:
            db.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500


@marketplace_bp.route('/mirakl/health', methods=['GET'])
def mirakl_health():
    client, error = _get_mirakl_client()
    env_base_url = os.getenv('MIRAKL_BASE_URL')
    env_shop_id = os.getenv('MIRAKL_SHOP_ID')
    env_api_key = os.getenv('MIRAKL_API_KEY')
    portal_base_url = get_portal_setting('mirakl_base_url')
    portal_shop_id = get_portal_setting('mirakl_shop_id')
    portal_api_key = get_portal_setting('mirakl_api_key')
    base_url = env_base_url or portal_base_url
    shop_id = env_shop_id or portal_shop_id
    api_key = env_api_key or portal_api_key
    diagnostics = {
        'base_url': base_url,
        'shop_id': shop_id,
        'api_key_masked': _mask_api_key(api_key),
        'api_key_length': len(api_key) if api_key else 0,
        'sources': {
            'base_url': 'env' if env_base_url else ('portal' if portal_base_url else None),
            'shop_id': 'env' if env_shop_id else ('portal' if portal_shop_id else None),
            'api_key': 'env' if env_api_key else ('portal' if portal_api_key else None),
        },
        'headers': ['Authorization'] + (['X-Mirakl-Shop-Id'] if shop_id else []),
    }
    if error:
        return jsonify({'success': False, 'error': error, 'diagnostics': diagnostics}), 400

    try:
        data = client.get_account()
        return jsonify({'success': True, 'account': data, 'diagnostics': diagnostics}), 200
    except MiraklError as exc:
        logger.exception("Mirakl health check failed")
        return jsonify({'success': False, 'error': str(exc), 'diagnostics': diagnostics}), 502


@marketplace_bp.route('/mirakl/offers/import', methods=['POST'])
def mirakl_import_offers():
    client, error = _get_mirakl_client()
    if error:
        return jsonify({'success': False, 'error': error}), 400

    data = request.get_json() or {}
    offers = data.get('offers') or []
    import_mode = data.get('import_mode', 'NORMAL')

    if not offers:
        return jsonify({'success': False, 'error': 'offers array is required'}), 400

    try:
        csv_bytes = build_offers_csv(offers)
    except ValueError as exc:
        return jsonify({'success': False, 'error': str(exc)}), 400

    try:
        result = client.import_offers(csv_bytes, import_mode=import_mode)
        logger.info("Mirakl offer import result: %s", result)
        return jsonify({'success': True, 'result': result}), 200
    except MiraklError as exc:
        logger.exception("Mirakl offer import failed")
        return jsonify({'success': False, 'error': str(exc)}), 502


@marketplace_bp.route('/mirakl/products/import', methods=['POST'])
def mirakl_import_products():
    """
    Import products using the full Airbus template format (products + offers combined).
    This is the correct endpoint for the Airbus marketplace.
    """
    client, error = _get_mirakl_client()
    if error:
        return jsonify({'success': False, 'error': error}), 400

    data = request.get_json() or {}
    parts = data.get('parts') or []
    import_mode = data.get('import_mode', 'NORMAL')

    if not parts:
        return jsonify({'success': False, 'error': 'parts array is required'}), 400

    try:
        # Use the Airbus marketplace export function to build the full template CSV
        csv_file = export_parts_to_airbus_marketplace_csv(parts)
        csv_bytes = csv_file.read()
        logger.info("Built Airbus template CSV with %d parts (%d bytes)", len(parts), len(csv_bytes))
    except Exception as exc:
        logger.exception("Failed to build Airbus template CSV")
        return jsonify({'success': False, 'error': str(exc)}), 400

    try:
        result = client.import_products(csv_bytes, import_mode=import_mode)
        logger.info("Mirakl product import result: %s", result)
        return jsonify({'success': True, 'result': result}), 200
    except MiraklError as exc:
        logger.exception("Mirakl product import failed")
        return jsonify({'success': False, 'error': str(exc)}), 502


@marketplace_bp.route('/mirakl/offers/import-file', methods=['POST'])
def mirakl_import_offers_file():
    client, error = _get_mirakl_client()
    if error:
        return jsonify({'success': False, 'error': error}), 400

    file = request.files.get('file')
    import_mode = (request.form.get('import_mode') or 'NORMAL').strip() or 'NORMAL'

    if not file or not file.filename:
        return jsonify({'success': False, 'error': 'CSV file is required'}), 400

    if not file.filename.lower().endswith('.csv'):
        return jsonify({'success': False, 'error': 'Only .csv files are supported'}), 400

    csv_bytes = file.read()
    if not csv_bytes:
        return jsonify({'success': False, 'error': 'CSV file is empty'}), 400
    csv_bytes = _dedupe_csv_headers(csv_bytes)

    try:
        result = client.import_offers(csv_bytes, import_mode=import_mode)
        logger.info("Mirakl offer import (file) result: %s", result)
        return jsonify({'success': True, 'result': result}), 200
    except MiraklError as exc:
        logger.exception("Mirakl offer import (file) failed")
        return jsonify({'success': False, 'error': str(exc)}), 502


@marketplace_bp.route('/mirakl/products/import-file', methods=['POST'])
def mirakl_import_products_file():
    client, error = _get_mirakl_client()
    if error:
        return jsonify({'success': False, 'error': error}), 400

    file = request.files.get('file')
    import_mode = (request.form.get('import_mode') or 'NORMAL').strip() or 'NORMAL'

    if not file or not file.filename:
        return jsonify({'success': False, 'error': 'CSV file is required'}), 400

    if not file.filename.lower().endswith('.csv'):
        return jsonify({'success': False, 'error': 'Only .csv files are supported'}), 400

    csv_bytes = file.read()
    if not csv_bytes:
        return jsonify({'success': False, 'error': 'CSV file is empty'}), 400

    try:
        result = client.import_products(csv_bytes, import_mode=import_mode)
        logger.info("Mirakl product import result: %s", result)
        return jsonify({'success': True, 'result': result}), 200
    except MiraklError as exc:
        logger.exception("Mirakl product import (file) failed")
        return jsonify({'success': False, 'error': str(exc)}), 502


@marketplace_bp.route('/mirakl/offers/imports/<import_id>', methods=['GET'])
def mirakl_import_status(import_id):
    client, error = _get_mirakl_client()
    if error:
        return jsonify({'success': False, 'error': error}), 400

    try:
        result = client.get_offers_import(import_id)
        return jsonify({'success': True, 'result': result}), 200
    except MiraklError as exc:
        logger.exception("Mirakl import status fetch failed")
        return jsonify({'success': False, 'error': str(exc)}), 502


@marketplace_bp.route('/mirakl/offers/imports/<import_id>/errors', methods=['GET'])
def mirakl_import_errors(import_id):
    client, error = _get_mirakl_client()
    if error:
        return jsonify({'success': False, 'error': error}), 400

    try:
        content, content_type = client.get_offers_import_errors(import_id)
        return content, 200, {'Content-Type': content_type}
    except MiraklError as exc:
        logger.exception("Mirakl import error report fetch failed")
        return jsonify({'success': False, 'error': str(exc)}), 502


@marketplace_bp.route('/mirakl/settings', methods=['POST'])
def mirakl_update_settings():
    data = request.get_json() or {}
    base_url = (data.get('mirakl_base_url') or '').strip()
    shop_id = (data.get('mirakl_shop_id') or '').strip()
    api_key = (data.get('mirakl_api_key') or '').strip()

    if not base_url:
        return jsonify({'success': False, 'error': 'Mirakl base URL is required.'}), 400

    db = get_db()
    cursor = None
    try:
        cursor = db.cursor()
        _upsert_portal_setting(cursor, 'mirakl_base_url', base_url)
        _upsert_portal_setting(cursor, 'mirakl_shop_id', shop_id)
        if api_key:
            _upsert_portal_setting(cursor, 'mirakl_api_key', api_key)
        db.commit()
    except Exception as exc:
        logger.exception("Failed to update Mirakl settings")
        db.rollback()
        return jsonify({'success': False, 'error': str(exc)}), 500
    finally:
        if cursor:
            cursor.close()

    return jsonify({'success': True}), 200
