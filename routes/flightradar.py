import os
import re
import json

from flask import Blueprint, current_app, jsonify, render_template, request
from flask_login import current_user, login_required

from integrations.flightradar_client import FlightradarClient, FlightradarConfig, FlightradarError
from db import execute as db_execute
from models import Permission, get_customer_by_id


flightradar_bp = Blueprint('flightradar', __name__, url_prefix='/flightradar')

_ICAO_LIST_RE = re.compile(r'^[A-Z0-9]{2,4}(,[A-Z0-9]{2,4}){0,14}$')
_BOUNDS_RE = re.compile(r'^-?\d{1,3}(?:\.\d{1,3})?,-?\d{1,3}(?:\.\d{1,3})?,-?\d{1,3}(?:\.\d{1,3})?,-?\d{1,3}(?:\.\d{1,3})?$')


def _build_client() -> FlightradarClient:
    api_key = (current_app.config.get('FLIGHTRADAR_API_KEY') or os.getenv('FLIGHTRADAR_API_KEY') or '').strip()
    api_base_url = (
        current_app.config.get('FLIGHTRADAR_API_BASE_URL')
        or os.getenv('FLIGHTRADAR_API_BASE_URL')
        or 'https://fr24api.flightradar24.com'
    ).strip()
    accept_version = (
        current_app.config.get('FLIGHTRADAR_ACCEPT_VERSION')
        or os.getenv('FLIGHTRADAR_ACCEPT_VERSION')
        or 'v1'
    ).strip()
    return FlightradarClient(
        FlightradarConfig(
            api_key=api_key,
            api_base_url=api_base_url,
            accept_version=accept_version,
        )
    )


def _normalize_icao_list(value: str) -> str:
    normalized = ','.join(
        part.strip().upper()
        for part in (value or '').split(',')
        if part.strip()
    )
    if normalized and not _ICAO_LIST_RE.match(normalized):
        raise ValueError('Use comma-separated ICAO operator codes, max 15 codes.')
    return normalized


def _normalize_bounds(value: str) -> str:
    normalized = (value or '').replace(' ', '').strip()
    if normalized and not _BOUNDS_RE.match(normalized):
        raise ValueError('Bounds must be north,south,west,east with up to 3 decimal places.')
    if not normalized:
        return ''

    north, south, west, east = [float(part) for part in normalized.split(',')]
    if not (-90 <= south <= north <= 90 and -180 <= west <= 180 and -180 <= east <= 180):
        raise ValueError('Bounds must be ordered north,south,west,east and use valid coordinates.')
    return normalized


def _normalize_limit(value: str) -> int:
    try:
        limit = int(value or 500)
    except (TypeError, ValueError):
        raise ValueError('Limit must be a number.')
    return max(1, min(limit, 30000))


def _normalize_match_mode(value: str) -> str:
    mode = (value or 'operating_as').strip().lower()
    if mode not in ('operating_as', 'painted_as', 'both'):
        raise ValueError('Match mode must be operating_as, painted_as, or both.')
    return mode


def _can_view_customer(customer_id: int) -> bool:
    customer = get_customer_by_id(customer_id)
    if not customer:
        return False
    if (
        current_user.is_administrator()
        or current_user.can(Permission.VIEW_CUSTOMERS)
        or current_user.can(Permission.EDIT_CUSTOMERS)
    ):
        return True
    try:
        user_salesperson_id = current_user.get_salesperson_id()
    except Exception:
        user_salesperson_id = None
    return bool(user_salesperson_id and customer.get('salesperson_id') == user_salesperson_id)


def _get_customer_flightradar_links(customer_id: int, *, active_only: bool = True):
    where_active = 'AND is_active = TRUE' if active_only else ''
    try:
        rows = db_execute(
            f"""
            SELECT id,
                   customer_id,
                   airline_icao,
                   airline_iata,
                   airline_name,
                   match_mode,
                   default_bounds,
                   is_active,
                   last_verified_at,
                   last_live_sync_at
            FROM customer_flightradar_links
            WHERE customer_id = ?
              {where_active}
            ORDER BY airline_name NULLS LAST, airline_icao, match_mode
            """,
            (customer_id,),
            fetch='all',
        ) or []
    except Exception as exc:
        current_app.logger.warning('Unable to load customer Flightradar links: %s', exc)
        return []
    return [dict(row) for row in rows]


def _get_customer_flightradar_aircraft(customer_id: int, *, limit: int = 25):
    try:
        rows = db_execute(
            """
            SELECT id,
                   registration,
                   hex,
                   aircraft_type,
                   first_seen_at,
                   last_seen_at,
                   last_flight,
                   last_callsign,
                   last_origin,
                   last_destination,
                   last_alt,
                   last_gspeed,
                   observed_count
            FROM customer_flightradar_aircraft
            WHERE customer_id = ?
            ORDER BY last_seen_at DESC
            LIMIT ?
            """,
            (customer_id, limit),
            fetch='all',
        ) or []
    except Exception as exc:
        current_app.logger.warning('Unable to load customer Flightradar aircraft: %s', exc)
        return []
    return [dict(row) for row in rows]


def _upsert_customer_aircraft(customer_id: int, link_id: int, flight: dict) -> bool:
    registration = str(flight.get('reg') or '').strip().upper()
    if not registration:
        return False

    origin = flight.get('orig_iata') or flight.get('orig_icao')
    destination = flight.get('dest_iata') or flight.get('dest_icao')
    payload_json = json.dumps(flight, default=str)
    db_execute(
        """
        INSERT INTO customer_flightradar_aircraft (
            customer_id,
            link_id,
            registration,
            hex,
            aircraft_type,
            last_fr24_id,
            last_flight,
            last_callsign,
            last_origin,
            last_destination,
            last_lat,
            last_lon,
            last_alt,
            last_gspeed,
            last_payload
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?::jsonb)
        ON CONFLICT (customer_id, registration)
        DO UPDATE SET
            link_id = EXCLUDED.link_id,
            hex = COALESCE(EXCLUDED.hex, customer_flightradar_aircraft.hex),
            aircraft_type = COALESCE(EXCLUDED.aircraft_type, customer_flightradar_aircraft.aircraft_type),
            last_seen_at = NOW(),
            last_fr24_id = EXCLUDED.last_fr24_id,
            last_flight = EXCLUDED.last_flight,
            last_callsign = EXCLUDED.last_callsign,
            last_origin = EXCLUDED.last_origin,
            last_destination = EXCLUDED.last_destination,
            last_lat = EXCLUDED.last_lat,
            last_lon = EXCLUDED.last_lon,
            last_alt = EXCLUDED.last_alt,
            last_gspeed = EXCLUDED.last_gspeed,
            observed_count = customer_flightradar_aircraft.observed_count + 1,
            last_payload = EXCLUDED.last_payload,
            updated_at = NOW()
        """,
        (
            customer_id,
            link_id,
            registration,
            flight.get('hex'),
            flight.get('type'),
            flight.get('fr24_id'),
            flight.get('flight'),
            flight.get('callsign'),
            origin,
            destination,
            flight.get('lat'),
            flight.get('lon'),
            flight.get('alt'),
            flight.get('gspeed'),
            payload_json,
        ),
        commit=True,
    )
    return True


def lookup_airline_by_icao(icao: str) -> dict:
    normalized = _normalize_icao_list(icao)
    if not normalized or ',' in normalized:
        raise ValueError('Provide one airline/operator ICAO code.')
    return _build_client().get_airline_light(normalized)


@flightradar_bp.route('/')
@login_required
def flightradar_home():
    api_key = (current_app.config.get('FLIGHTRADAR_API_KEY') or os.getenv('FLIGHTRADAR_API_KEY') or '').strip()
    return render_template(
        'flightradar/index.html',
        has_api_key=bool(api_key),
        api_base_url=current_app.config.get('FLIGHTRADAR_API_BASE_URL')
        or os.getenv('FLIGHTRADAR_API_BASE_URL')
        or 'https://fr24api.flightradar24.com',
        accept_version=current_app.config.get('FLIGHTRADAR_ACCEPT_VERSION')
        or os.getenv('FLIGHTRADAR_ACCEPT_VERSION')
        or 'v1',
        default_bounds='72.0,25.0,-25.0,45.0',
    )


@flightradar_bp.route('/api/auth-test', methods=['POST'])
@login_required
def auth_test():
    try:
        usage = _build_client().get_usage(period='24h')
        return jsonify({'ok': True, 'usage': usage})
    except FlightradarError as exc:
        return jsonify({'ok': False, 'error': str(exc)}), 502
    except Exception as exc:
        current_app.logger.exception('Unexpected Flightradar auth test error')
        return jsonify({'ok': False, 'error': f'Unexpected Flightradar error: {exc}'}), 500


@flightradar_bp.route('/api/airline-lookup', methods=['POST'])
@login_required
def airline_lookup():
    try:
        payload = request.get_json(silent=True) or {}
        airline = lookup_airline_by_icao(payload.get('icao', ''))
        return jsonify({'ok': True, 'airline': airline})
    except ValueError as exc:
        return jsonify({'ok': False, 'error': str(exc)}), 400
    except FlightradarError as exc:
        return jsonify({'ok': False, 'error': str(exc)}), 502
    except Exception as exc:
        current_app.logger.exception('Unexpected Flightradar airline lookup error')
        return jsonify({'ok': False, 'error': f'Unexpected Flightradar error: {exc}'}), 500


@flightradar_bp.route('/api/live-positions', methods=['GET'])
@login_required
def live_positions():
    try:
        operating_as = _normalize_icao_list(request.args.get('operating_as', ''))
        painted_as = _normalize_icao_list(request.args.get('painted_as', ''))
        bounds = _normalize_bounds(request.args.get('bounds', ''))
        limit = _normalize_limit(request.args.get('limit', '500'))

        if not any((operating_as, painted_as, bounds)):
            return jsonify({
                'ok': False,
                'error': 'Provide at least one filter: operating_as, painted_as, or bounds.',
            }), 400

        payload = _build_client().get_live_positions_full(
            operating_as=operating_as,
            painted_as=painted_as,
            bounds=bounds,
            limit=limit,
        )
        flights = payload.get('data') if isinstance(payload, dict) else []
        return jsonify({
            'ok': True,
            'count': len(flights or []),
            'flights': flights or [],
            'filters': {
                'operating_as': operating_as,
                'painted_as': painted_as,
                'bounds': bounds,
                'limit': limit,
            },
        })
    except ValueError as exc:
        return jsonify({'ok': False, 'error': str(exc)}), 400
    except FlightradarError as exc:
        return jsonify({'ok': False, 'error': str(exc)}), 502
    except Exception as exc:
        current_app.logger.exception('Unexpected Flightradar live positions error')
        return jsonify({'ok': False, 'error': f'Unexpected Flightradar error: {exc}'}), 500


@flightradar_bp.route('/api/customers/<int:customer_id>/live-active-flights', methods=['POST'])
@login_required
def customer_live_active_flights(customer_id):
    if not _can_view_customer(customer_id):
        return jsonify({'ok': False, 'error': 'Customer not found or access denied.'}), 404

    try:
        request_payload = request.get_json(silent=True) or {}
        limit = _normalize_limit(str(request_payload.get('limit') or '500'))
        override_bounds = _normalize_bounds(str(request_payload.get('bounds') or ''))
        links = _get_customer_flightradar_links(customer_id)
        if not links:
            return jsonify({
                'ok': True,
                'count': 0,
                'stored_tail_count': 0,
                'links': [],
                'flights': [],
                'aircraft': _get_customer_flightradar_aircraft(customer_id),
            })

        client = _build_client()
        flights = []
        seen_keys = set()
        stored_tail_count = 0

        for link in links:
            mode = _normalize_match_mode(link.get('match_mode'))
            icao = link.get('airline_icao')
            bounds = override_bounds or link.get('default_bounds') or ''
            payload = client.get_live_positions_full(
                operating_as=icao if mode in ('operating_as', 'both') else None,
                painted_as=icao if mode in ('painted_as', 'both') else None,
                bounds=bounds,
                limit=limit,
            )
            link_flights = payload.get('data') if isinstance(payload, dict) else []

            for flight in link_flights or []:
                flight['_customer_flightradar_link_id'] = link.get('id')
                flight['_customer_flightradar_match_mode'] = mode
                dedupe_key = flight.get('fr24_id') or flight.get('reg') or (
                    flight.get('callsign'),
                    flight.get('lat'),
                    flight.get('lon'),
                )
                if dedupe_key in seen_keys:
                    continue
                seen_keys.add(dedupe_key)
                flights.append(flight)
                if _upsert_customer_aircraft(customer_id, link.get('id'), flight):
                    stored_tail_count += 1

            db_execute(
                """
                UPDATE customer_flightradar_links
                SET last_live_sync_at = NOW(),
                    updated_at = NOW()
                WHERE id = ?
                """,
                (link.get('id'),),
                commit=True,
            )

        return jsonify({
            'ok': True,
            'count': len(flights),
            'stored_tail_count': stored_tail_count,
            'links': links,
            'flights': flights,
            'aircraft': _get_customer_flightradar_aircraft(customer_id),
        })
    except ValueError as exc:
        return jsonify({'ok': False, 'error': str(exc)}), 400
    except FlightradarError as exc:
        return jsonify({'ok': False, 'error': str(exc)}), 502
    except Exception as exc:
        current_app.logger.exception('Unexpected customer Flightradar live flight error')
        return jsonify({'ok': False, 'error': f'Unexpected Flightradar error: {exc}'}), 500
