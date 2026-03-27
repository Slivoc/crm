# routes/settings.py

from flask import Blueprint, request, render_template, redirect, url_for, flash, current_app
from models import (
    update_currency_rate,
    get_conversion_mode,
    set_conversion_mode,
    get_base_currency,
    set_base_currency
)
from db import CURRENCY_RATE_COLUMN, db_cursor, execute as db_execute
import logging
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


settings_bp = Blueprint('settings', __name__)

API_SETTING_FIELDS = [
    {'name': 'openai_api_key', 'storage_key': 'OPENAI_API_KEY', 'label': 'OpenAI API Key'},
    {'name': 'perplexity_api_key', 'storage_key': 'PERPLEXITY_API_KEY', 'label': 'Perplexity API Key'},
    {'name': 'apollo_api_key', 'storage_key': 'APOLLO_API_KEY', 'label': 'Apollo API Key'},
    {'name': 'hubspot_api_key', 'storage_key': 'HUBSPOT_API_KEY', 'label': 'HubSpot API Key'},
    {'name': 'exchange_rate_api_key', 'storage_key': 'EXCHANGE_RATE_API_KEY', 'label': 'Exchange Rate API Key'},
    {'name': 'tickets_hub_api_key', 'storage_key': 'TICKETS_HUB_API_KEY', 'label': 'Tickets Hub API Key'},
    {'name': 'internal_api_key', 'storage_key': 'API_KEY', 'label': 'Internal API Key'},
]


def _get_app_setting_value(key, default=''):
    row = db_execute('SELECT value FROM app_settings WHERE key = ?', (key,), fetch='one')
    if not row:
        return default
    return row.get('value', default) if isinstance(row, dict) else row[0]


def _set_app_setting_value(key, value):
    with db_cursor(commit=True) as cur:
        existing = _execute_with_cursor(cur, 'SELECT 1 FROM app_settings WHERE key = ?', (key,)).fetchone()
        if existing:
            _execute_with_cursor(cur, 'UPDATE app_settings SET value = ? WHERE key = ?', (value, key))
        else:
            _execute_with_cursor(cur, 'INSERT INTO app_settings (key, value) VALUES (?, ?)', (key, value))


def _apply_api_key_to_runtime_config(storage_key, value):
    current_app.config[storage_key] = value or ''
    if storage_key in ('OPENAI_API_KEY', 'PERPLEXITY_API_KEY'):
        os.environ[storage_key] = value or ''


def convert_rates_to_new_base(old_base, new_base):
    """
    Convert all exchange rates from old base currency to new base currency

    For example, if switching from EUR to GBP:
    - Old: 1 EUR = 1.27 USD (stored as 1.27)
    - If 1 EUR = 0.85 GBP
    - New: 1 GBP = (1.27 / 0.85) USD = 1.494 USD

    Args:
        old_base: Old base currency code (e.g., 'EUR')
        new_base: New base currency code (e.g., 'GBP')
    """
    if old_base == new_base:
        return  # No conversion needed

    rate_col = CURRENCY_RATE_COLUMN  # 'exchange_rate_to_eur' (SQLite) or 'exchange_rate_to_base' (Postgres)
    currencies = db_execute('SELECT * FROM currencies', fetch='all') or []
    currencies = [dict(row) for row in currencies]

    new_base_rate = next(
        (c.get(rate_col) for c in currencies if c.get('currency_code') == new_base),
        None
    )

    if new_base_rate is None or new_base_rate == 0:
        logging.error(f"Cannot convert to {new_base}: rate not found or zero")
        return

    logging.info(f"Converting rates from {old_base} to {new_base} (conversion factor: {new_base_rate})")

    with db_cursor(commit=True) as cur:
        for currency in currencies:
            old_rate = currency.get(rate_col)
            if old_rate is None:
                continue

            if currency.get('currency_code') == new_base:
                new_rate = 1.0
            else:
                new_rate = old_rate / new_base_rate

            _execute_with_cursor(
                cur,
                f'UPDATE currencies SET {rate_col} = ? WHERE currency_code = ?',
                (new_rate, currency.get('currency_code'))
            )

            logging.debug(f"Converted {currency.get('currency_code')}: {old_rate:.6f} -> {new_rate:.6f}")

    logging.info(f"Successfully converted all rates from {old_base} to {new_base}")


@settings_bp.route('/settings', methods=['GET', 'POST'])
def settings():
    logging.debug("Accessing settings page")

    if request.method == 'POST':
        logging.debug("Processing POST request")

        old_base_currency = get_base_currency()
        conversion_mode = request.form.get('conversion_mode', 'manual')
        logging.debug(f"Conversion mode: {conversion_mode}")
        set_conversion_mode(conversion_mode)

        new_base_currency = request.form.get('base_currency')
        logging.debug(f"Base currency: old={old_base_currency}, new={new_base_currency}")

        if new_base_currency and new_base_currency != old_base_currency:
            convert_rates_to_new_base(old_base_currency, new_base_currency)
            set_base_currency(new_base_currency)
            flash(f'Base currency changed to {new_base_currency}. All rates have been converted.', 'success')
            logging.info(f"Base currency changed from {old_base_currency} to {new_base_currency}")

        if conversion_mode == 'manual':
            currencies = db_execute('SELECT currency_code FROM currencies', fetch='all') or []
            for currency in currencies:
                code = currency['currency_code']
                rate = request.form.get(f'exchange_rate_{code}', type=float)
                logging.debug(f"Currency: {code}, Rate: {rate}")
                if rate is not None:
                    update_currency_rate(code, rate)

        for field in API_SETTING_FIELDS:
            raw_value = (request.form.get(field['name']) or '').strip()
            if not raw_value:
                continue
            _set_app_setting_value(field['storage_key'], raw_value)
            _apply_api_key_to_runtime_config(field['storage_key'], raw_value)

        flash('Settings updated successfully!', 'success')
        return redirect(url_for('settings.settings'))

    conversion_mode = get_conversion_mode()
    base_currency = get_base_currency()
    rate_col = CURRENCY_RATE_COLUMN
    exchange_rates = db_execute('SELECT * FROM currencies ORDER BY id', fetch='all') or []
    exchange_rates = [dict(row) for row in exchange_rates]
    # Backward/forward compatibility for templates/JS expecting the legacy key.
    for row in exchange_rates:
        rate = row.get(rate_col)
        if rate is None:
            rate = row.get('exchange_rate_to_base', row.get('exchange_rate_to_eur'))
        row['exchange_rate_to_base'] = rate
        row['exchange_rate_to_eur'] = rate

    logging.debug(f"Exchange Rates: {exchange_rates}")
    logging.debug(f"Conversion Mode: {conversion_mode}")
    logging.debug(f"Base Currency: {base_currency}")

    api_key_status = {}
    for field in API_SETTING_FIELDS:
        value = _get_app_setting_value(field['storage_key'], '')
        api_key_status[field['storage_key']] = bool(value)

    return render_template('settings.html',
                           exchange_rates=exchange_rates,
                           conversion_mode=conversion_mode,
                           base_currency=base_currency,
                           api_setting_fields=API_SETTING_FIELDS,
                           api_key_status=api_key_status)
