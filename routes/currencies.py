from flask import Blueprint, jsonify, render_template
from db import CURRENCY_RATE_COLUMN, execute as db_execute
from models import get_conversion_mode, fetch_live_exchange_rates, format_timestamp
import logging
from models import get_currencies

currencies_bp = Blueprint('currencies', __name__)


@currencies_bp.route('/api/currencies', methods=['GET'])
def api_currencies():
    rate_col = CURRENCY_RATE_COLUMN  # 'exchange_rate_to_eur' (SQLite) or 'exchange_rate_to_base' (Postgres)
    conversion_mode = get_conversion_mode()

    if conversion_mode == 'live':
        # Try to fetch live rates
        live_rates = fetch_live_exchange_rates()

        if live_rates:
            # Get symbols from the database and UPDATE the rates
            db_currencies = {row['currency_code']: row for row in db_execute('SELECT * FROM currencies', fetch='all')}

            # UPDATE: Save live rates to database
            currencies = []
            for code, rate in live_rates.items():
                # Only include currencies that are in our database
                if code in db_currencies:
                    # Update the database with the live rate
                    db_execute(
                        f'UPDATE currencies SET {rate_col} = ? WHERE currency_code = ?',
                        (rate, code),
                        commit=True
                    )

                    currencies.append({
                        'id': db_currencies[code]['id'],
                        'currency_code': code,
                        # Return both keys for backward/forward compatibility with UI/code.
                        'exchange_rate_to_base': rate,
                        'exchange_rate_to_eur': rate,
                        'symbol': db_currencies[code]['symbol'],
                        'is_live': True,
                        'last_updated': format_timestamp()  # Add timestamp for UI display
                    })

            logging.info(f"Updated {len(currencies)} currencies with live rates")

            # Sort by ID to maintain consistent order
            currencies.sort(key=lambda x: x['id'])
            return jsonify(currencies)
        else:
            # Fall back to manual rates if live rates couldn't be fetched
            logging.warning("Falling back to manual rates due to API failure")

    # Use manual rates from database
    currencies = db_execute('SELECT * FROM currencies', fetch='all')

    # Convert DB rows to dictionaries
    currencies_list = []
    for currency in currencies:
        rate = currency.get(rate_col)
        if rate is None:
            # Fallback for mixed/legacy schemas
            rate = currency.get('exchange_rate_to_base', currency.get('exchange_rate_to_eur'))
        currencies_list.append({
            'id': currency['id'],
            'currency_code': currency['currency_code'],
            'exchange_rate_to_base': rate,
            'exchange_rate_to_eur': rate,
            'symbol': currency['symbol'],
            'is_live': False
        })

    return jsonify(currencies_list)


@currencies_bp.route('/currencies', methods=['GET'])
def currencies_page():
    """
    Display the currencies page with live/manual indicator
    """
    return render_template('currencies.html')
