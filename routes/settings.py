# routes/settings.py

from flask import Blueprint, request, render_template, redirect, url_for, flash
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

    return render_template('settings.html',
                           exchange_rates=exchange_rates,
                           conversion_mode=conversion_mode,
                           base_currency=base_currency)
