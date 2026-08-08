from flask import Blueprint, render_template, jsonify, request, g, current_app, url_for, send_from_directory, flash, redirect
from flask_login import current_user, login_required
from werkzeug.utils import secure_filename
import sqlite3
import calendar
from datetime import datetime
from models import get_db, dict_from_row
import json
import os
import re
import uuid
from openai import OpenAI
import requests
from PIL import Image

dashboard_bp = Blueprint('dashboard', __name__)

TV_IMAGE_EXTENSIONS = {'jpg', 'jpeg', 'png', 'webp'}
TV_FACT_PROVIDER = 'perplexity_sonar_pro'
COMMONS_API_URL = 'https://commons.wikimedia.org/w/api.php'
TV_FACT_FALLBACK_IMAGE = 'img/aerospace-fact-fallback.svg'
IMAGE_DOWNLOAD_LIMIT = 12 * 1024 * 1024


def _monthly_target_pace(now=None):
    """Return the calendar-month percentage that has elapsed as of today."""
    now = now or datetime.now()
    days_in_month = calendar.monthrange(now.year, now.month)[1]
    return round(now.day / days_in_month * 100, 1)


def _use_mgc_company_name(value):
    """Correct legacy briefings that used the CRM product name as the company name."""
    return re.sub(r'\bSproutt\b', 'MGC', value or '', flags=re.IGNORECASE)


def _normalise_tv_briefing(value):
    """Clean common JSON/Markdown escaping artifacts from generated briefings."""
    value = _use_mgc_company_name(value)
    value = re.sub(r'\\r\\n|\\n|\\r', '\n', value)
    return re.sub(r'\\([\\`*_{}\[\]()#+.!|>~•-])', r'\1', value)


def _normalise_tv_briefing_fields(briefing):
    """Return the three TV sections, recovering JSON leaked into one field."""
    fields = ('summary', 'commercial_angle', 'suggested_action')
    values = {
        field: _normalise_tv_briefing(str(briefing.get(field) or '').strip())
        for field in fields
    }

    # Some model responses (and therefore cached rows) put the remainder of
    # the JSON object inside ``summary``. Split those labelled sections back
    # out instead of displaying JSON syntax on the TV.
    marker = re.compile(
        r'\s*,?\s*["\']?(commercial_angle|suggested_action)["\']?\s*:\s*["\']?',
        re.IGNORECASE,
    )
    parts = marker.split(values['summary'])
    if len(parts) > 1:
        values['summary'] = parts[0]
        for index in range(1, len(parts), 2):
            field = parts[index].lower()
            if index + 1 < len(parts) and not values[field]:
                values[field] = parts[index + 1]

    for field in fields:
        values[field] = values[field].strip().strip('{}').strip()
        values[field] = re.sub(r'["\']\s*,?\s*$', '', values[field]).strip()
        values[field] = re.sub(r'^["\']', '', values[field]).strip()
    return values


def _tv_employee(conn):
    row = conn.execute('''
        SELECT name, description, image_path
        FROM office_dashboard_employee
        WHERE id = 1
    ''').fetchone()
    return dict(row) if row else {'name': '', 'description': '', 'image_path': ''}


def _tv_facts(conn, status='approved'):
    rows = conn.execute('''
        SELECT id, topic, title, subtitle, facts, status, image_query,
               CASE WHEN image_file_path <> ''
                    THEN '/dashboard/tv/facts/image/' || id
                    ELSE image_url END AS image_url,
               image_credit, image_source, image_source_url, image_attribution,
               source_urls, approved_by, created_at, updated_at
        FROM office_dashboard_facts
        WHERE status = ?
        ORDER BY updated_at DESC, id DESC
    ''', (status,)).fetchall()
    facts = []
    for row in rows:
        item = dict(row)
        for field in ('facts', 'source_urls'):
            value = item.get(field)
            if isinstance(value, str):
                try:
                    value = json.loads(value)
                except json.JSONDecodeError:
                    value = []
            item[field] = value or []
        facts.append(item)
    return facts


def _previously_approved_tv_facts(conn):
    """Return slides removed from rotation after having been approved."""
    rows = conn.execute('''
        SELECT id, topic, title, subtitle, facts, status, image_query,
               CASE WHEN image_file_path <> ''
                    THEN '/dashboard/tv/facts/image/' || id
                    ELSE image_url END AS image_url,
               image_credit, image_source, image_source_url, image_attribution,
               source_urls, approved_by, created_at, updated_at
        FROM office_dashboard_facts
        WHERE status = 'archived' AND approved_by IS NOT NULL
        ORDER BY updated_at DESC, id DESC
    ''').fetchall()
    facts = []
    for row in rows:
        item = dict(row)
        for field in ('facts', 'source_urls'):
            value = item.get(field)
            if isinstance(value, str):
                try:
                    value = json.loads(value)
                except json.JSONDecodeError:
                    value = []
            item[field] = value or []
        facts.append(item)
    return facts


def _tv_payload(conn):
    """Build the read-only snapshot used by the office TV presentation."""
    now = datetime.now()
    month_key = now.strftime('%Y-%m')
    summary = conn.execute('''
        SELECT
            COALESCE(SUM(total_value), 0) AS actual,
            COUNT(*) AS order_count
        FROM sales_orders
        WHERE date_entered >= date_trunc('month', CURRENT_DATE)
          AND date_entered < date_trunc('month', CURRENT_DATE) + INTERVAL '1 month'
    ''').fetchone()
    target = conn.execute('''
        SELECT COALESCE(SUM(goal_amount), 0) AS amount
        FROM salesperson_monthly_goals
        WHERE target_month = ?
    ''', (month_key,)).fetchone()
    biggest_orders = conn.execute('''
        SELECT so.sales_order_ref, so.total_value, so.date_entered, c.name AS customer_name,
               c.logo_url, s.name AS salesperson_name
        FROM sales_orders so
        JOIN customers c ON c.id = so.customer_id
        LEFT JOIN salespeople s ON s.id = c.salesperson_id
        WHERE so.date_entered >= date_trunc('month', CURRENT_DATE)
          AND so.date_entered < date_trunc('month', CURRENT_DATE) + INTERVAL '1 month'
        ORDER BY so.total_value DESC NULLS LAST
        LIMIT 5
    ''').fetchall()
    biggest_non_dave_orders = conn.execute('''
        SELECT so.sales_order_ref, so.total_value, so.date_entered, c.name AS customer_name,
               c.logo_url, s.name AS salesperson_name
        FROM sales_orders so
        JOIN customers c ON c.id = so.customer_id
        LEFT JOIN salespeople s ON s.id = c.salesperson_id
        WHERE so.date_entered >= date_trunc('month', CURRENT_DATE)
          AND so.date_entered < date_trunc('month', CURRENT_DATE) + INTERVAL '1 month'
          AND so.salesperson_id <> 3
        ORDER BY so.total_value DESC NULLS LAST
        LIMIT 3
    ''').fetchall()
    highest_spending_customers = conn.execute('''
        SELECT c.name, COALESCE(SUM(so.total_value), 0) AS month_value,
               COUNT(*) AS order_count,
               STRING_AGG(DISTINCT s.name, ', ' ORDER BY s.name) AS salesperson_names
        FROM sales_orders so
        JOIN customers c ON c.id = so.customer_id
        LEFT JOIN salespeople s ON s.id = c.salesperson_id
        WHERE so.date_entered >= date_trunc('month', CURRENT_DATE)
          AND so.date_entered < date_trunc('month', CURRENT_DATE) + INTERVAL '1 month'
        GROUP BY c.id, c.name
        ORDER BY SUM(so.total_value) DESC NULLS LAST, c.name
        LIMIT 3
    ''').fetchall()
    new_customers = conn.execute('''
        WITH first_orders AS (
            SELECT customer_id, MIN(date_entered) AS first_order_date
            FROM sales_orders
            GROUP BY customer_id
        )
        SELECT c.name, fo.first_order_date, COALESCE(SUM(so.total_value), 0) AS month_value,
               STRING_AGG(DISTINCT s.name, ', ' ORDER BY s.name) AS salesperson_names
        FROM first_orders fo
        JOIN customers c ON c.id = fo.customer_id
        JOIN sales_orders so ON so.customer_id = fo.customer_id
        LEFT JOIN salespeople s ON s.id = c.salesperson_id
        WHERE fo.first_order_date >= date_trunc('month', CURRENT_DATE)
          AND fo.first_order_date < date_trunc('month', CURRENT_DATE) + INTERVAL '1 month'
          AND so.date_entered >= date_trunc('month', CURRENT_DATE)
          AND so.date_entered < date_trunc('month', CURRENT_DATE) + INTERVAL '1 month'
        GROUP BY c.id, c.name, fo.first_order_date
        ORDER BY fo.first_order_date DESC, c.name
        LIMIT 5
    ''').fetchall()
    customer_focus = conn.execute('''
        WITH eligible_customers AS (
            SELECT c.id, c.name, c.logo_url, c.country,
                   s.name AS salesperson_name,
                   cs.status AS customer_status,
                   COALESCE(SUM(so.total_value), 0) AS spend_90d,
                   COUNT(*) AS order_count_90d,
                   MAX(so.date_entered) AS last_order_date,
                   (ARRAY_AGG(so.sales_order_ref ORDER BY so.date_entered DESC, so.id DESC))[1]
                       AS last_order_ref
            FROM sales_orders so
            JOIN customers c ON c.id = so.customer_id
            LEFT JOIN salespeople s ON s.id = c.salesperson_id
            LEFT JOIN customer_status cs ON cs.id = c.status_id
            LEFT JOIN sales_statuses ss ON ss.id = so.sales_status_id
            WHERE so.date_entered >= CURRENT_DATE - INTERVAL '90 days'
              AND COALESCE(so.total_value, 0) > 0
              AND COALESCE(ss.status_name, '') <> 'Cancelled'
            GROUP BY c.id, c.name, c.logo_url, c.country, s.name, cs.status
            HAVING SUM(so.total_value) > 0
            ORDER BY MAX(so.date_entered) DESC, SUM(so.total_value) DESC
            LIMIT 100
        )
        SELECT eligible.*,
               COALESCE(parts.most_ordered_parts, '[]'::jsonb) AS most_ordered_parts
        FROM eligible_customers eligible
        LEFT JOIN LATERAL (
            SELECT JSONB_AGG(
                JSONB_BUILD_OBJECT(
                    'base_part_number', ranked.base_part_number,
                    'order_count', ranked.order_count,
                    'total_quantity', ranked.total_quantity
                ) ORDER BY ranked.order_count DESC, ranked.total_quantity DESC
            ) AS most_ordered_parts
            FROM (
                SELECT COALESCE(NULLIF(sol.base_part_number, ''), 'Unknown part') AS base_part_number,
                       COUNT(DISTINCT so.id) AS order_count,
                       COALESCE(SUM(sol.quantity), 0) AS total_quantity,
                       COALESCE(SUM(
                           (sol.price * sol.quantity)
                           / COALESCE(NULLIF(cur.exchange_rate_to_base, 0), 1)
                       ), 0) AS sales_value_gbp
                FROM sales_order_lines sol
                JOIN sales_orders so ON so.id = sol.sales_order_id
                LEFT JOIN currencies cur ON cur.id = so.currency_id
                WHERE so.customer_id = eligible.id
                   OR so.customer_id IN (
                       SELECT ca.associated_customer_id
                       FROM customer_associations ca
                       WHERE ca.main_customer_id = eligible.id
                   )
                GROUP BY COALESCE(NULLIF(sol.base_part_number, ''), 'Unknown part')
                ORDER BY order_count DESC, total_quantity DESC, sales_value_gbp DESC
                LIMIT 5
            ) ranked
        ) parts ON TRUE
        ORDER BY eligible.last_order_date DESC, eligible.spend_90d DESC
    ''').fetchall()
    news = conn.execute('''
        WITH ranked_customer_articles AS (
            SELECT acm.article_id, acm.customer_id,
                   ROW_NUMBER() OVER (
                       PARTITION BY acm.customer_id
                       ORDER BY COALESCE(
                                    (SELECT ner.editorial_score FROM news_editorial_reviews ner WHERE ner.article_id = na.id),
                                    acm.relevance_score
                                ) DESC,
                                na.published_at DESC,
                                na.id DESC
                   ) AS customer_article_rank
            FROM article_customer_mentions acm
            JOIN news_articles na ON na.id = acm.article_id
            WHERE na.duplicate_of_article_id IS NULL
              AND na.published_at >= CURRENT_TIMESTAMP - INTERVAL '45 days'
              AND COALESCE((SELECT ner.tv_recommended FROM news_editorial_reviews ner WHERE ner.article_id = na.id), TRUE)
        ), selected_articles AS (
            SELECT DISTINCT article_id
            FROM ranked_customer_articles
            WHERE customer_article_rank <= 10
        )
        SELECT na.id, na.title, na.url, na.source_name, na.summary_raw, na.body_excerpt,
               na.published_at,
               (na.published_at::date = CURRENT_DATE) AS is_today,
               MAX(acm.relevance_score) AS relevance_score,
               (SELECT ner.editorial_score FROM news_editorial_reviews ner WHERE ner.article_id = na.id) AS editorial_score,
               STRING_AGG(DISTINCT c.name, ', ' ORDER BY c.name) AS customer_names,
               JSONB_AGG(
                   DISTINCT JSONB_BUILD_OBJECT('id', c.id, 'name', c.name)
               ) AS customers
        FROM selected_articles selected
        JOIN news_articles na ON na.id = selected.article_id
        JOIN article_customer_mentions acm ON acm.article_id = na.id
        JOIN customers c ON c.id = acm.customer_id
        GROUP BY na.id
        ORDER BY COALESCE(na.published_at, na.fetched_at) DESC,
                 MAX(acm.relevance_score) DESC
    ''').fetchall()

    portal_searches = conn.execute('''
        SELECT psh.id, psh.search_type, psh.parts_count, psh.date_searched,
               c.name AS customer_name,
               NULLIF(TRIM(CONCAT_WS(' ', pu.first_name, pu.last_name)), '') AS user_name
        FROM portal_search_history psh
        JOIN portal_users pu ON pu.id = psh.portal_user_id
        JOIN customers c ON c.id = psh.customer_id
        ORDER BY psh.date_searched DESC
        LIMIT 8
    ''').fetchall()
    portal_quote_requests = conn.execute('''
        SELECT pqr.id, pqr.reference_number, pqr.status, pqr.date_submitted,
               c.name AS customer_name, COUNT(pqrl.id) AS line_count
        FROM portal_quote_requests pqr
        JOIN customers c ON c.id = pqr.customer_id
        LEFT JOIN portal_quote_request_lines pqrl ON pqrl.portal_quote_request_id = pqr.id
        GROUP BY pqr.id, c.name
        ORDER BY pqr.date_submitted DESC
        LIMIT 8
    ''').fetchall()

    actual = float(summary['actual'] or 0)
    target_amount = float(target['amount'] or 0)
    pace_percentage = _monthly_target_pace(now)
    employee = _tv_employee(conn)
    if employee.get('image_path'):
        employee['image_url'] = url_for('dashboard.tv_employee_image', filename=os.path.basename(employee['image_path']))
    else:
        employee['image_url'] = ''
    news_items = [dict(row) for row in news]
    for article in news_items:
        article['customers'] = [
            {
                **customer,
                'url': url_for('customers.get_customer_details', customer_id=customer['id']),
            }
            for customer in (article.get('customers') or [])
        ]

    customer_focus_items = []
    for row in customer_focus:
        item = dict(row)
        parts = item.get('most_ordered_parts') or []
        if isinstance(parts, str):
            try:
                parts = json.loads(parts)
            except json.JSONDecodeError:
                parts = []
        item['most_ordered_parts'] = parts
        customer_focus_items.append(item)

    return {
        'month_label': now.strftime('%B %Y'),
        'updated_at': now.isoformat(timespec='seconds'),
        'sales': {
            'actual': actual,
            'target': target_amount,
            'remaining': max(target_amount - actual, 0),
            'percentage': round((actual / target_amount * 100), 1) if target_amount else 0,
            'pace_percentage': pace_percentage,
            'pace_amount': round(target_amount * pace_percentage / 100, 2),
            'order_count': int(summary['order_count'] or 0),
        },
        'biggest_orders': [dict(row) for row in biggest_orders],
        'biggest_non_dave_orders': [dict(row) for row in biggest_non_dave_orders],
        'highest_spending_customers': [dict(row) for row in highest_spending_customers],
        'new_customers': [dict(row) for row in new_customers],
        'customer_focus': customer_focus_items,
        'news': news_items,
        'portal_activity': {
            'searches': [dict(row) for row in portal_searches],
            'quote_requests': [dict(row) for row in portal_quote_requests],
        },
        'employee': employee,
        'aerospace_facts': _tv_facts(conn),
    }


@dashboard_bp.route('/tv')
@login_required
def tv_dashboard():
    conn = get_db()
    try:
        payload = _tv_payload(conn)
    finally:
        conn.close()
    return render_template(
        'dashboard_tv.html',
        initial_data=payload,
    )


@dashboard_bp.route('/tv/control')
@login_required
def tv_control():
    """Show administrators exactly what is configured and queued for the office TV."""
    if not current_user.is_administrator():
        flash('Administrator access is required to manage the office TV.', 'error')
        return redirect(url_for('dashboard.tv_dashboard'))

    conn = get_db()
    try:
        payload = _tv_payload(conn)
        draft_facts = _tv_facts(conn, 'draft')
        previously_approved_facts = _previously_approved_tv_facts(conn)
        cached_briefings = conn.execute('''
            SELECT article_id, created_at
            FROM news_ai_summaries
            WHERE customer_id IS NULL
              AND model_provider = 'dashboard_perplexity_compact'
        ''').fetchall()
        briefing_dates = {
            row['article_id']: row['created_at']
            for row in cached_briefings
        }
        for article in payload['news']:
            article['briefing_created_at'] = briefing_dates.get(article['id'])
    finally:
        conn.close()

    return render_template(
        'dashboard_tv_control.html',
        employee=payload['employee'],
        news=payload['news'],
        updated_at=payload['updated_at'],
        approved_facts=payload['aerospace_facts'],
        draft_facts=draft_facts,
        previously_approved_facts=previously_approved_facts,
    )


@dashboard_bp.route('/tv/facts/control')
@login_required
def tv_fact_control():
    """Manage AI-researched fact slides separately from the main TV controls."""
    if not current_user.is_administrator():
        flash('Administrator access is required to manage TV fact slides.', 'error')
        return redirect(url_for('dashboard.tv_dashboard'))

    conn = get_db()
    try:
        approved_facts = _tv_facts(conn, 'approved')
        draft_facts = _tv_facts(conn, 'draft')
        previously_approved_facts = _previously_approved_tv_facts(conn)
    finally:
        conn.close()
    return render_template(
        'dashboard_tv_facts_control.html',
        approved_facts=approved_facts,
        draft_facts=draft_facts,
        previously_approved_facts=previously_approved_facts,
    )


@dashboard_bp.route('/tv/data')
@login_required
def tv_dashboard_data():
    conn = get_db()
    try:
        response = jsonify(_tv_payload(conn))
        response.headers['Cache-Control'] = 'no-store, max-age=0'
        return response
    finally:
        conn.close()


def _perplexity_api_key(conn):
    key = os.getenv('PERPLEXITY_API_KEY') or current_app.config.get('PERPLEXITY_API_KEY')
    if key:
        return key
    row = conn.execute(
        "SELECT value FROM app_settings WHERE key IN (?, ?) ORDER BY CASE key WHEN ? THEN 0 ELSE 1 END LIMIT 1",
        ('PERPLEXITY_API_KEY', 'perplexity_api_key', 'PERPLEXITY_API_KEY'),
    ).fetchone()
    return row['value'] if row and row['value'] else None


def _require_tv_admin():
    return current_user.is_authenticated and current_user.is_administrator()


def _clean_json_response(value):
    value = (value or '').strip()
    if value.startswith('```'):
        value = value.split('\n', 1)[1].rsplit('```', 1)[0]
    return json.loads(value)


def _perplexity_json(api_key, prompt):
    completion = OpenAI(api_key=api_key, base_url='https://api.perplexity.ai').chat.completions.create(
        model='sonar-pro',
        messages=[
            {'role': 'system', 'content': 'You are a meticulous aerospace researcher. Return only valid JSON.'},
            {'role': 'user', 'content': prompt},
        ],
    )
    return _clean_json_response(completion.choices[0].message.content)


def _normalise_customer_focus_insight(value):
    """Validate and constrain Perplexity content before it reaches the TV."""
    value = value if isinstance(value, dict) else {}
    description = str(value.get('description') or '').strip()[:700]
    similar_companies = []
    for company in value.get('similar_companies') or []:
        if isinstance(company, str):
            name, reason = company, ''
        elif isinstance(company, dict):
            name = company.get('name')
            reason = company.get('reason')
        else:
            continue
        name = str(name or '').strip()[:160]
        reason = str(reason or '').strip()[:240]
        if name:
            similar_companies.append({'name': name, 'reason': reason})
        if len(similar_companies) == 4:
            break
    source_urls = [
        str(url)[:1000] for url in (value.get('source_urls') or [])
        if str(url).startswith('https://')
    ][:4]
    return {
        'description': description,
        'similar_companies': similar_companies,
        'source_urls': source_urls,
    }


@dashboard_bp.route('/tv/customers/<int:customer_id>/focus')
@login_required
def tv_customer_focus_insight(customer_id):
    """Return cached Perplexity context for a recently spending customer."""
    conn = get_db()
    try:
        customer = conn.execute('''
            SELECT c.id, c.name, c.country, c.website,
                   SUM(so.total_value) AS spend_90d,
                   COUNT(*) AS order_count_90d
            FROM customers c
            JOIN sales_orders so ON so.customer_id = c.id
            LEFT JOIN sales_statuses ss ON ss.id = so.sales_status_id
            WHERE c.id = ?
              AND so.date_entered >= CURRENT_DATE - INTERVAL '90 days'
              AND COALESCE(so.total_value, 0) > 0
              AND COALESCE(ss.status_name, '') <> 'Cancelled'
            GROUP BY c.id, c.name, c.country, c.website
            HAVING SUM(so.total_value) > 0
        ''', (customer_id,)).fetchone()
        if not customer:
            return jsonify({'error': 'Customer is not eligible for an In Focus slide'}), 404

        cached = conn.execute('''
            SELECT description, similar_companies, source_urls
            FROM office_dashboard_customer_focus_summaries
            WHERE customer_id = ?
              AND updated_at >= CURRENT_TIMESTAMP - INTERVAL '30 days'
        ''', (customer_id,)).fetchone()
        if cached:
            return jsonify({'insight': _normalise_customer_focus_insight(dict(cached)), 'cached': True})

        api_key = _perplexity_api_key(conn)
        if not api_key:
            return jsonify({'error': 'Perplexity API key is not configured'}), 503
        insight = _normalise_customer_focus_insight(_perplexity_json(api_key, f'''Research this current MGC customer for a concise office-TV spotlight.

Customer: {customer['name']}
Country: {customer['country'] or 'Unknown'}
Website: {customer['website'] or 'Unknown'}
Recent relationship: {customer['order_count_90d']} orders in the last 90 days.

MGC supplies aerospace hardware and aircraft parts, particularly to helicopter operators, MROs, aviation suppliers and related organisations.

Return JSON exactly as:
{{"description":"Two concise factual sentences, 35-55 words total, describing what the customer does and why it is relevant to MGC","similar_companies":[{{"name":"company name","reason":"maximum 18 words explaining the operational or commercial similarity"}}],"source_urls":["https://authoritative-source"]}}

Provide 3-4 real similar organisations that MGC could reasonably research as prospects. Base similarity on sector, fleet, operations, maintenance role or purchasing needs. Do not claim they are unserved prospects or existing MGC customers. Prefer current company and authoritative sources. Do not use markdown or invent details.'''))
        if not insight['description']:
            raise ValueError('Perplexity returned no customer description')
        conn.execute('''
            INSERT INTO office_dashboard_customer_focus_summaries
                (customer_id, description, similar_companies, source_urls, model_provider)
            VALUES (?, ?, ?::jsonb, ?::jsonb, 'perplexity_sonar_pro')
            ON CONFLICT (customer_id) DO UPDATE SET
                description = EXCLUDED.description,
                similar_companies = EXCLUDED.similar_companies,
                source_urls = EXCLUDED.source_urls,
                model_provider = EXCLUDED.model_provider,
                updated_at = CURRENT_TIMESTAMP
        ''', (customer_id, insight['description'], json.dumps(insight['similar_companies']),
              json.dumps(insight['source_urls'])))
        conn.commit()
        return jsonify({'insight': insight, 'cached': False})
    except Exception:
        conn.rollback()
        current_app.logger.exception('Unable to prepare customer focus insight for customer %s', customer_id)
        return jsonify({'error': 'Customer research is temporarily unavailable'}), 502
    finally:
        conn.close()


def _plain_metadata(value):
    """Turn Commons HTML metadata into searchable, display-safe text."""
    return re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', str(value or ''))).strip()


def _commons_image_candidates(image_query):
    response = requests.get(COMMONS_API_URL, params={
        'action': 'query', 'generator': 'search', 'gsrsearch': image_query,
        'gsrnamespace': 6, 'gsrlimit': 12, 'prop': 'imageinfo',
        'iiprop': 'url|size|mime|extmetadata', 'iiurlwidth': 1920,
        'format': 'json', 'formatversion': 2,
    }, headers={'User-Agent': 'SprouttCRM/1.0 (office TV image retrieval)'}, timeout=12)
    response.raise_for_status()
    return (response.json().get('query') or {}).get('pages') or []


def _commons_image_by_page_id(page_id):
    """Resolve a selection server-side rather than trusting a browser-supplied URL."""
    response = requests.get(COMMONS_API_URL, params={
        'action': 'query', 'pageids': page_id, 'prop': 'imageinfo',
        'iiprop': 'url|size|mime|extmetadata', 'iiurlwidth': 1920,
        'format': 'json', 'formatversion': 2,
    }, headers={'User-Agent': 'SprouttCRM/1.0 (office TV image retrieval)'}, timeout=12)
    response.raise_for_status()
    pages = (response.json().get('query') or {}).get('pages') or []
    return pages[0] if pages else None


def _commons_image_metadata(candidate):
    info = (candidate.get('imageinfo') or [{}])[0]
    metadata = info.get('extmetadata') or {}
    author = _plain_metadata((metadata.get('Artist') or {}).get('value'))[:300]
    licence = _plain_metadata((metadata.get('LicenseShortName') or {}).get('value'))[:120]
    attribution = _plain_metadata((metadata.get('Attribution') or {}).get('value'))[:500]
    if not attribution:
        attribution = ', '.join(value for value in (author, licence, 'Wikimedia Commons') if value)
    return info, {
        'image_source': 'Wikimedia Commons',
        'image_source_url': info.get('descriptionurl') or info.get('url') or '',
        'image_author': author,
        'image_license': licence,
        'image_attribution': attribution,
    }


def _commons_match(candidate, image_query, specific_subject=False):
    info = (candidate.get('imageinfo') or [{}])[0]
    metadata = info.get('extmetadata') or {}
    searchable = ' '.join([
        candidate.get('title', ''),
        _plain_metadata((metadata.get('ImageDescription') or {}).get('value')),
        _plain_metadata((metadata.get('Categories') or {}).get('value')),
    ]).lower()
    words = [word for word in re.findall(r'[a-z0-9]+', image_query.lower()) if len(word) > 2]
    matched = sum(word in searchable for word in words)
    confidence = 'HIGH' if words and matched >= max(2, len(words) - 1) else 'MEDIUM'
    if specific_subject and confidence != 'HIGH':
        return None
    width, height = int(info.get('width') or 0), int(info.get('height') or 0)
    mime = str(info.get('mime') or '')
    if width < 1200 or height < 600 or width <= height or mime not in {'image/jpeg', 'image/png', 'image/webp'}:
        return None
    return {'info': info, 'metadata': metadata, 'confidence': confidence, 'score': matched + width / 10000}


def _specific_image_subject(topic, image_query):
    """Be conservative when a query appears to name a model, standard, or component."""
    text = f'{topic} {image_query}'
    return bool(re.search(r'\b[A-Z]{1,5}[- ]?\d{2,4}[A-Z0-9-]*\b', text) or
                re.search(r'\b(?:Airbus|Boeing|Bell|Leonardo|Sikorsky|Robinson|AW\d|H\d{3})\b', text, re.I))


def _download_fact_image(url, fact_id, mime):
    response = requests.get(url, stream=True, timeout=20,
                            headers={'User-Agent': 'SprouttCRM/1.0 (office TV image cache)'})
    response.raise_for_status()
    content = response.content
    if len(content) > IMAGE_DOWNLOAD_LIMIT:
        raise ValueError('Image exceeds download limit')
    extension = {'image/jpeg': 'jpg', 'image/png': 'png', 'image/webp': 'webp'}.get(mime)
    if not extension:
        raise ValueError('Unsupported image format')
    folder = os.path.join(current_app.config['UPLOAD_FOLDER'], 'office-dashboard', 'facts')
    os.makedirs(folder, exist_ok=True)
    filename = f'fact-{fact_id}-{uuid.uuid4().hex}.{extension}'
    path = os.path.join(folder, filename)
    with open(path, 'wb') as image_file:
        image_file.write(content)
    with Image.open(path) as image:
        image.verify()
    return f'office-dashboard/facts/{filename}'


def _generic_stock_image(image_query, fact_id):
    """Try configured stock-photo APIs for generic subjects, in stated priority order."""
    pexels_key = os.getenv('PEXELS_API_KEY') or current_app.config.get('PEXELS_API_KEY')
    if pexels_key:
        response = requests.get('https://api.pexels.com/v1/search',
                                params={'query': image_query, 'orientation': 'landscape', 'per_page': 10},
                                headers={'Authorization': pexels_key}, timeout=12)
        response.raise_for_status()
        for photo in response.json().get('photos') or []:
            if int(photo.get('width') or 0) >= 1200 and int(photo.get('width') or 0) > int(photo.get('height') or 0):
                path = _download_fact_image((photo.get('src') or {}).get('large2x'), fact_id, 'image/jpeg')
                author = str(photo.get('photographer') or '')[:300]
                return {'image_file_path': path, 'image_source': 'Pexels',
                        'image_source_url': photo.get('url', ''), 'image_author': author,
                        'image_license': 'Pexels licence',
                        'image_attribution': f'{author} / Pexels' if author else 'Pexels'}
    pixabay_key = os.getenv('PIXABAY_API_KEY') or current_app.config.get('PIXABAY_API_KEY')
    if pixabay_key:
        response = requests.get('https://pixabay.com/api/', params={
            'key': pixabay_key, 'q': image_query, 'image_type': 'photo',
            'orientation': 'horizontal', 'safesearch': 'true', 'per_page': 10,
        }, timeout=12)
        response.raise_for_status()
        for photo in response.json().get('hits') or []:
            if int(photo.get('imageWidth') or 0) >= 1200:
                path = _download_fact_image(photo.get('largeImageURL'), fact_id, 'image/jpeg')
                author = str(photo.get('user') or '')[:300]
                return {'image_file_path': path, 'image_source': 'Pixabay',
                        'image_source_url': photo.get('pageURL', ''), 'image_author': author,
                        'image_license': 'Pixabay content licence',
                        'image_attribution': f'{author} / Pixabay' if author else 'Pixabay'}
    return None


def _retrieve_fact_image(image_query, topic, fact_id):
    """Select and locally cache a defensibly matched Commons image."""
    specific = _specific_image_subject(topic, image_query)
    matches = []
    for candidate in _commons_image_candidates(image_query):
        match = _commons_match(candidate, image_query, specific)
        if match:
            matches.append((match, candidate))
    if not matches:
        stock_image = None if specific else _generic_stock_image(image_query, fact_id)
        if stock_image:
            return stock_image
        return {'image_file_path': TV_FACT_FALLBACK_IMAGE, 'image_source': 'local',
                'image_attribution': 'MGC Aerospace educational slide fallback'}
    match, candidate = max(matches, key=lambda item: item[0]['score'])
    info, metadata = match['info'], match['metadata']
    path = _download_fact_image(info['url'], fact_id, info.get('mime'))
    author = _plain_metadata((metadata.get('Artist') or {}).get('value'))[:300]
    licence = _plain_metadata((metadata.get('LicenseShortName') or {}).get('value'))[:120]
    attribution = _plain_metadata((metadata.get('Attribution') or {}).get('value'))[:500]
    if not attribution:
        attribution = ', '.join(value for value in (author, licence, 'Wikimedia Commons') if value)
    return {'image_file_path': path, 'image_source': 'Wikimedia Commons',
            'image_source_url': info.get('descriptionurl') or info.get('url'),
            'image_author': author, 'image_license': licence,
            'image_attribution': attribution, 'image_confidence': match['confidence']}


@dashboard_bp.route('/tv/facts/suggestions', methods=['POST'])
@login_required
def suggest_tv_fact_topics():
    if not _require_tv_admin():
        return jsonify({'error': 'Administrator access required'}), 403
    conn = get_db()
    try:
        key = _perplexity_api_key(conn)
        if not key:
            return jsonify({'error': 'Perplexity API key is not configured'}), 503
        existing = conn.execute('SELECT topic FROM office_dashboard_facts ORDER BY created_at DESC LIMIT 30').fetchall()
        topics = _perplexity_json(key, f'''Suggest 8 varied topics for short educational office-TV slides for an aerospace hardware distributor serving helicopter operators, MROs and aviation customers.

The purpose is to make staff gradually more knowledgeable about the aircraft, customers, missions, components, engineering and commercial realities behind the parts we sell.

Choose a genuinely varied mix. Do NOT default mainly to random aircraft types.

Across each batch, aim to cover several of these categories:

- Aircraft and helicopter types relevant to commercial, HEMS, SAR, offshore and military aviation
- Aerospace hardware: rivets, blind rivets, solid rivets, lockbolts, screws, nuts, washers, inserts and other fasteners
- Engineering concepts: grip length, countersinking, fatigue, vibration, load paths, galvanic corrosion, material selection
- Materials and finishes: aluminium, titanium, stainless steel, cadmium plating, passivation, anodising, corrosion protection
- Maintenance and MRO: inspections, component replacement, repair practices, aircraft downtime
- Quality and traceability: EASA Form 1, FAA 8130-3, certificates of conformity, batch traceability, approved parts
- Aviation terminology: AOG, MRO, operator, OEM, rotable, consumable, shipset, line maintenance, base maintenance
- Missions: HEMS, SAR, offshore transport, firefighting, police aviation, military support
- Customer/industry knowledge: helicopter operators, maintenance organisations, aviation hubs and why particular regions use certain aircraft
- Supply-chain/commercial concepts: MOQ, lead time, obsolescence, spot buying, strategic pricing, why apparently simple parts can become scarce
- Manufacturing: thread rolling, riveting, machining, heat treatment, plating and aerospace fastener production
- Engineering curiosities and 'small part, big consequence' examples
- Occasional wider aerospace subjects such as spaceflight, historic engineering milestones or unusual aircraft technology

Prefer topics which answer questions such as:
'What is a blind rivet?'
'Why are aerospace fasteners traceable?'
'Why does corrosion matter so much offshore?'
'What does AOG actually mean?'
'Why can a £5 fastener ground a £10m aircraft?'
'How can a helicopter glide after engine failure?'
'Why do some helicopters have skids and others wheels?'

Aircraft-specific topics should normally have a reason they are interesting to our business, customers or missions rather than simply being a random aircraft.

Avoid these recent topics: {', '.join(row['topic'] for row in existing) or 'none'}.

Make topics specific enough that another AI can later research and write a factual slide about them.

Return JSON exactly as:
{{"topics":[{{"topic":"specific subject","teaser":"one intriguing sentence, maximum 18 words","image_query":"concise literal image search query"}}]}}

Do not include category names, explanations or any text outside the JSON.''')
        suggestions = topics.get('topics', []) if isinstance(topics, dict) else []
        return jsonify({'topics': suggestions[:8]})
    except Exception:
        current_app.logger.exception('Unable to suggest TV aerospace topics')
        return jsonify({'error': 'Topic suggestions are temporarily unavailable'}), 502
    finally:
        conn.close()


@dashboard_bp.route('/tv/facts/generate', methods=['POST'])
@login_required
def generate_tv_fact():
    if not _require_tv_admin():
        return jsonify({'error': 'Administrator access required'}), 403
    request_data = request.get_json(silent=True) or {}
    topic = str(request_data.get('topic') or '').strip()
    image_query = str(request_data.get('image_query') or '').strip()[:180]
    if not topic or len(topic) > 180:
        return jsonify({'error': 'Choose a topic of no more than 180 characters'}), 400
    conn = get_db()
    try:
        key = _perplexity_api_key(conn)
        if not key:
            return jsonify({'error': 'Perplexity API key is not configured'}), 503
        slide = _perplexity_json(key, f'''Research this aerospace topic and create a factual TV slide: {topic}
Return JSON exactly with: "title" (max 8 words), "subtitle" (max 16 words), "facts" (array of exactly 4 concise facts, each max 20 words), "image_query" (a concise literal search phrase for a relevant photograph), and "source_urls" (2-4 authoritative HTTPS research URLs).
Prefer manufacturer, museum, government and reputable reference sources. Every claim must be supported. Do not use markdown or invent specifications.''')
        facts = slide.get('facts') if isinstance(slide, dict) else None
        sources = slide.get('source_urls') if isinstance(slide, dict) else None
        if not isinstance(facts, list) or len(facts) != 4 or not isinstance(sources, list):
            raise ValueError('Invalid fact slide response')
        image_query = image_query or str(slide.get('image_query') or '').strip()[:180] or topic
        row = conn.execute('''
            INSERT INTO office_dashboard_facts
                (topic, title, subtitle, facts, image_query, source_urls, model_provider, created_by)
            VALUES (?, ?, ?, ?::jsonb, ?, ?::jsonb, ?, ?)
            RETURNING id
        ''', (topic, str(slide.get('title') or topic)[:180], str(slide.get('subtitle') or '')[:240],
              json.dumps([str(fact)[:240] for fact in facts]), image_query, json.dumps([str(url) for url in sources[:4]]),
              TV_FACT_PROVIDER, current_user.id)).fetchone()
        try:
            image = _retrieve_fact_image(image_query, topic, row['id'])
        except Exception:
            current_app.logger.exception('Unable to retrieve TV fact image for %s', topic)
            image = {'image_file_path': TV_FACT_FALLBACK_IMAGE, 'image_source': 'local',
                     'image_attribution': 'MGC Aerospace educational slide fallback'}
        conn.execute('''UPDATE office_dashboard_facts SET
            image_file_path = ?, image_source = ?, image_source_url = ?, image_author = ?,
            image_license = ?, image_attribution = ?, image_credit = ?, image_retrieved_at = CURRENT_TIMESTAMP
            WHERE id = ?''', (image.get('image_file_path', ''), image.get('image_source', ''),
            image.get('image_source_url', ''), image.get('image_author', ''), image.get('image_license', ''),
            image.get('image_attribution', ''), image.get('image_attribution', '')[:300], row['id']))
        conn.commit()
        return jsonify({'success': True, 'id': row['id']})
    except Exception:
        conn.rollback()
        current_app.logger.exception('Unable to generate TV aerospace fact for %s', topic)
        return jsonify({'error': 'The slide could not be generated'}), 502
    finally:
        conn.close()


@dashboard_bp.route('/tv/facts/images/search')
@login_required
def search_tv_fact_images():
    """Return reviewable, landscape Wikimedia Commons choices for a fact slide."""
    if not _require_tv_admin():
        return jsonify({'error': 'Administrator access required'}), 403
    query = str(request.args.get('q') or '').strip()[:180]
    if not query:
        return jsonify({'error': 'Enter an image search'}), 400
    try:
        results = []
        for candidate in _commons_image_candidates(query):
            match = _commons_match(candidate, query, False)
            if not match:
                continue
            info, image = _commons_image_metadata(candidate)
            results.append({
                'page_id': candidate.get('pageid'),
                'title': re.sub(r'^File:', '', candidate.get('title') or '', flags=re.I),
                'preview_url': info.get('thumburl') or info.get('url'),
                'width': info.get('width'),
                'height': info.get('height'),
                'credit': image['image_attribution'],
                'source_url': image['image_source_url'],
            })
        return jsonify({'images': results})
    except Exception:
        current_app.logger.exception('Unable to search Commons for TV fact image %s', query)
        return jsonify({'error': 'Image search is temporarily unavailable'}), 502


@dashboard_bp.route('/tv/facts/<int:fact_id>/image', methods=['POST'])
@login_required
def select_tv_fact_image(fact_id):
    """Cache an administrator-selected Commons image for preview and TV display."""
    if not _require_tv_admin():
        return jsonify({'error': 'Administrator access required'}), 403
    request_data = request.get_json(silent=True) or {}
    try:
        page_id = int(request_data.get('page_id'))
    except (TypeError, ValueError):
        return jsonify({'error': 'Choose a valid image'}), 400

    conn = get_db()
    try:
        fact = conn.execute('SELECT id FROM office_dashboard_facts WHERE id = ?', (fact_id,)).fetchone()
        if not fact:
            return jsonify({'error': 'Fact slide not found'}), 404
        candidate = _commons_image_by_page_id(page_id)
        if not candidate:
            return jsonify({'error': 'The selected image is no longer available'}), 404
        info, image = _commons_image_metadata(candidate)
        if not _commons_match(candidate, '', False):
            return jsonify({'error': 'Choose a landscape JPG, PNG or WebP image at least 1200 pixels wide'}), 400
        image['image_file_path'] = _download_fact_image(
            info.get('thumburl') or info.get('url'), fact_id, info.get('mime'))
        conn.execute('''
            UPDATE office_dashboard_facts SET
                image_url = '', image_file_path = ?, image_source = ?, image_source_url = ?,
                image_author = ?, image_license = ?, image_attribution = ?, image_credit = ?,
                image_retrieved_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        ''', (image['image_file_path'], image['image_source'], image['image_source_url'],
              image['image_author'], image['image_license'], image['image_attribution'],
              image['image_attribution'][:300], fact_id))
        conn.commit()
        return jsonify({
            'success': True,
            'image_url': url_for('dashboard.tv_fact_image', fact_id=fact_id),
            'image_credit': image['image_attribution'],
        })
    except Exception:
        conn.rollback()
        current_app.logger.exception('Unable to select TV fact image for slide %s', fact_id)
        return jsonify({'error': 'The selected image could not be saved'}), 502
    finally:
        conn.close()


@dashboard_bp.route('/tv/facts/image/<int:fact_id>')
@login_required
def tv_fact_image(fact_id):
    conn = get_db()
    try:
        row = conn.execute('SELECT image_file_path FROM office_dashboard_facts WHERE id = ?', (fact_id,)).fetchone()
    finally:
        conn.close()
    path = row['image_file_path'] if row else ''
    if path == TV_FACT_FALLBACK_IMAGE:
        return send_from_directory(current_app.static_folder, path)
    if not path.startswith('office-dashboard/facts/'):
        return send_from_directory(current_app.static_folder, TV_FACT_FALLBACK_IMAGE)
    return send_from_directory(current_app.config['UPLOAD_FOLDER'], path)


@dashboard_bp.route('/tv/facts/<int:fact_id>/status', methods=['POST'])
@login_required
def update_tv_fact_status(fact_id):
    if not _require_tv_admin():
        return jsonify({'error': 'Administrator access required'}), 403
    status = request.form.get('status', '')
    if status not in {'approved', 'archived'}:
        return jsonify({'error': 'Invalid slide status'}), 400
    conn = get_db()
    try:
        conn.execute('''
            UPDATE office_dashboard_facts
            SET status = ?, approved_by = CASE WHEN ? = 'approved' THEN ? ELSE approved_by END,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        ''', (status, status, current_user.id, fact_id))
        conn.commit()
        flash('Aerospace slide approved for TV.' if status == 'approved' else 'Aerospace slide removed.', 'success')
    finally:
        conn.close()
    destination = 'dashboard.tv_fact_control' if request.form.get('next') == 'facts' else 'dashboard.tv_control'
    return redirect(url_for(destination))


@dashboard_bp.route('/tv/news/<int:article_id>/extended')
@login_required
def tv_extended_news(article_id):
    """Return a cached, commercially focused Perplexity briefing for a TV story."""
    conn = get_db()
    try:
        article = conn.execute('''
            SELECT na.id, na.title, na.url, na.source_name, na.summary_raw, na.body_excerpt,
                   STRING_AGG(DISTINCT c.name, ', ' ORDER BY c.name) AS customer_names
            FROM news_articles na
            LEFT JOIN article_customer_mentions acm ON acm.article_id = na.id
            LEFT JOIN customers c ON c.id = acm.customer_id
            WHERE na.id = ? AND na.duplicate_of_article_id IS NULL
            GROUP BY na.id
        ''', (article_id,)).fetchone()
        if not article:
            return jsonify({'error': 'News story not found'}), 404

        cached = conn.execute('''
            SELECT summary, commercial_angle, suggested_action
            FROM news_ai_summaries
            WHERE article_id = ? AND customer_id IS NULL AND model_provider = 'dashboard_perplexity_compact'
        ''', (article_id,)).fetchone()
        if cached:
            cached_briefing = _normalise_tv_briefing_fields(cached)
            return jsonify({'story': {**dict(article), **cached_briefing}})

        api_key = _perplexity_api_key(conn)
        if not api_key:
            return jsonify({'error': 'Perplexity API key is not configured'}), 503
        prompt = f'''Research and expand this aviation news story for MGC, a helicopter parts supplier. Sproutt is the name of MGC's CRM software, not the company.
Title: {article['title']}
Source URL: {article['url']}
Known customers mentioned: {article['customer_names'] or 'None'}
Existing excerpt: {article['summary_raw'] or article['body_excerpt'] or 'None'}

Return ONLY valid JSON with these string fields:
"summary": exactly 3 short bullet lines beginning with •, maximum 12 words per bullet,
"commercial_angle": exactly 2 short bullet lines beginning with •, maximum 12 words per bullet, explaining relevance to MGC and its helicopter-parts customers,
"suggested_action": exactly 2 concrete bullet lines beginning with •, maximum 10 words per bullet.
Do not use paragraphs, preambles, headings, citations, or repeated facts. Refer to the company only as MGC, never Sproutt. Use current web research, do not invent facts, and make every line quickly readable on a TV.'''
        completion = OpenAI(api_key=api_key, base_url='https://api.perplexity.ai').chat.completions.create(
            model='sonar-pro',
            messages=[{'role': 'system', 'content': 'You are a precise commercial aviation intelligence analyst.'},
                      {'role': 'user', 'content': prompt}],
        )
        raw = completion.choices[0].message.content.strip()
        if raw.startswith('```'):
            raw = raw.split('\n', 1)[1].rsplit('```', 1)[0]
        briefing = json.loads(raw)
        normalised = _normalise_tv_briefing_fields(briefing)
        values = tuple(normalised[field] for field in ('summary', 'commercial_angle', 'suggested_action'))
        conn.execute('''
            INSERT INTO news_ai_summaries
                (article_id, customer_id, model_provider, summary, commercial_angle, suggested_action)
            VALUES (?, NULL, 'dashboard_perplexity_compact', ?, ?, ?)
            ON CONFLICT (article_id, customer_id, model_provider) DO UPDATE SET
                summary = EXCLUDED.summary,
                commercial_angle = EXCLUDED.commercial_angle,
                suggested_action = EXCLUDED.suggested_action,
                created_at = CURRENT_TIMESTAMP
        ''', (article_id, *values))
        conn.commit()
        return jsonify({'story': {**dict(article), 'summary': values[0], 'commercial_angle': values[1], 'suggested_action': values[2]}})
    except (json.JSONDecodeError, IndexError, KeyError, TypeError):
        conn.rollback()
        current_app.logger.exception('Perplexity returned an invalid TV news briefing for article %s', article_id)
        return jsonify({'error': 'The extended briefing could not be prepared'}), 502
    except Exception:
        conn.rollback()
        current_app.logger.exception('Unable to build TV news briefing for article %s', article_id)
        return jsonify({'error': 'The extended briefing is temporarily unavailable'}), 502
    finally:
        conn.close()


@dashboard_bp.route('/tv/employee', methods=['POST'])
@login_required
def update_tv_employee():
    if not current_user.is_administrator():
        return jsonify({'error': 'Administrator access required'}), 403

    name = request.form.get('name', '').strip()
    description = request.form.get('description', '').strip()
    if not name or len(name) > 120 or len(description) > 600:
        return jsonify({'error': 'Enter a name (maximum 120 characters) and a description of up to 600 characters.'}), 400

    image = request.files.get('image')
    image_path = None
    if image and image.filename:
        extension = secure_filename(image.filename).rsplit('.', 1)[-1].lower()
        if extension not in TV_IMAGE_EXTENSIONS:
            return jsonify({'error': 'Please upload a JPG, PNG, or WebP image.'}), 400
        folder = os.path.join(current_app.config['UPLOAD_FOLDER'], 'office-dashboard')
        os.makedirs(folder, exist_ok=True)
        filename = f"employee-{uuid.uuid4().hex}.{extension}"
        image.save(os.path.join(folder, filename))
        image_path = f'office-dashboard/{filename}'

    conn = get_db()
    try:
        existing = conn.execute('SELECT image_path FROM office_dashboard_employee WHERE id = 1').fetchone()
        if image_path is None:
            image_path = existing['image_path'] if existing else ''
        conn.execute('''
            INSERT INTO office_dashboard_employee (id, name, description, image_path, updated_by, updated_at)
            VALUES (1, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT (id) DO UPDATE SET
                name = EXCLUDED.name,
                description = EXCLUDED.description,
                image_path = EXCLUDED.image_path,
                updated_by = EXCLUDED.updated_by,
                updated_at = CURRENT_TIMESTAMP
        ''', (name, description, image_path, current_user.id))
        conn.commit()
        employee = _tv_employee(conn)
        employee['image_url'] = url_for('dashboard.tv_employee_image', filename=os.path.basename(image_path)) if image_path else ''
        if request.form.get('next') == 'tv_control':
            flash('Employee of the month updated.', 'success')
            return redirect(url_for('dashboard.tv_control'))
        return jsonify({'success': True, 'employee': employee})
    except Exception:
        conn.rollback()
        current_app.logger.exception('Unable to update office dashboard employee')
        return jsonify({'error': 'Unable to save employee of the month.'}), 500
    finally:
        conn.close()


@dashboard_bp.route('/tv/employee-image/<path:filename>')
@login_required
def tv_employee_image(filename):
    return send_from_directory(
        os.path.join(current_app.config['UPLOAD_FOLDER'], 'office-dashboard'),
        filename,
    )


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
