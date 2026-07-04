import os
import re
import json
import csv
import time
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from datetime import date, datetime, timedelta, timezone

from flask import Blueprint, current_app, jsonify, render_template, request
from flask_login import current_user, login_required

from integrations.flightradar_client import FlightradarClient, FlightradarConfig, FlightradarError
from db import execute as db_execute
from models import Permission, get_customer_by_id


flightradar_bp = Blueprint('flightradar', __name__, url_prefix='/flightradar')

_ICAO_LIST_RE = re.compile(r'^[A-Z0-9]{2,4}(,[A-Z0-9]{2,4}){0,14}$')
_BOUNDS_RE = re.compile(r'^-?\d{1,3}(?:\.\d{1,3})?,-?\d{1,3}(?:\.\d{1,3})?,-?\d{1,3}(?:\.\d{1,3})?,-?\d{1,3}(?:\.\d{1,3})?$')
_AIRLINES_DAT_PATH = Path(__file__).resolve().parent.parent / 'docs' / 'flightradar' / 'airlines.dat'


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
    return max(1, min(limit, 20000))


def _parse_iso_datetime(value: str):
    raw = (value or '').strip()
    if not raw:
        return None
    if raw.endswith('Z'):
        raw = raw[:-1] + '+00:00'
    parsed = datetime.fromisoformat(raw)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _format_fr24_datetime(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).strftime('%Y-%m-%dT%H:%M:%SZ')


def _flight_datetime_value(value: str):
    try:
        return _parse_iso_datetime(value)
    except (TypeError, ValueError):
        return None


def _flight_datetime_for_db(value: str):
    parsed = _flight_datetime_value(value)
    return parsed if parsed else None


def _safe_float(value):
    if value in (None, ''):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _flight_duration_seconds(flight: dict):
    direct_duration = _safe_float(flight.get('flight_time'))
    if direct_duration is not None and direct_duration >= 0:
        return direct_duration

    for start_key, end_key in (
        ('datetime_takeoff', 'datetime_landed'),
        ('first_seen', 'last_seen'),
    ):
        start = _flight_datetime_value(flight.get(start_key))
        end = _flight_datetime_value(flight.get(end_key))
        if start and end and end >= start:
            return (end - start).total_seconds()
    return None


def _flight_dedupe_key(flight: dict) -> str:
    fr24_id = str(flight.get('fr24_id') or '').strip()
    if fr24_id:
        return f"fr24:{fr24_id}"

    registration = str(flight.get('reg') or '').strip().upper()
    flight_number = str(flight.get('flight') or '').strip().upper()
    callsign = str(flight.get('callsign') or '').strip().upper()
    first_seen = str(flight.get('first_seen') or flight.get('datetime_takeoff') or '').strip()
    last_seen = str(flight.get('last_seen') or flight.get('datetime_landed') or '').strip()
    origin = str(flight.get('orig_iata') or flight.get('orig_icao') or '').strip().upper()
    destination = str(
        flight.get('dest_iata_actual')
        or flight.get('dest_iata')
        or flight.get('dest_icao_actual')
        or flight.get('dest_icao')
        or ''
    ).strip().upper()
    return '|'.join((registration, flight_number, callsign, first_seen, last_seen, origin, destination))


def _upsert_customer_flight(customer_id: int, link_id: int, flight: dict) -> bool:
    dedupe_key = _flight_dedupe_key(flight)
    if not dedupe_key or dedupe_key == '||||||':
        return False

    registration = str(flight.get('reg') or '').strip().upper() or None
    duration_seconds = _flight_duration_seconds(flight)
    estimated_hours = round(duration_seconds / 3600, 4) if duration_seconds is not None else None
    flight_ended = flight.get('flight_ended')
    datetime_landed = _flight_datetime_for_db(flight.get('datetime_landed'))
    cycle_count = 1 if flight_ended is True or datetime_landed else 0
    origin_iata = flight.get('orig_iata')
    origin_icao = flight.get('orig_icao')
    destination_iata = flight.get('dest_iata_actual') or flight.get('dest_iata')
    destination_icao = flight.get('dest_icao_actual') or flight.get('dest_icao')
    payload_json = json.dumps(flight, default=str)

    db_execute(
        """
        INSERT INTO customer_flightradar_flights (
            customer_id,
            link_id,
            flight_dedupe_key,
            fr24_id,
            registration,
            aircraft_type,
            flight,
            callsign,
            operating_as,
            painted_as,
            origin_iata,
            origin_icao,
            destination_iata,
            destination_icao,
            datetime_takeoff,
            datetime_landed,
            first_seen,
            last_seen,
            flight_time_seconds,
            estimated_flight_hours,
            cycle_count,
            flight_ended,
            actual_distance_km,
            circle_distance_km,
            raw_payload
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?::jsonb)
        ON CONFLICT (customer_id, flight_dedupe_key)
        DO UPDATE SET
            link_id = EXCLUDED.link_id,
            fr24_id = COALESCE(EXCLUDED.fr24_id, customer_flightradar_flights.fr24_id),
            registration = COALESCE(EXCLUDED.registration, customer_flightradar_flights.registration),
            aircraft_type = COALESCE(EXCLUDED.aircraft_type, customer_flightradar_flights.aircraft_type),
            flight = COALESCE(EXCLUDED.flight, customer_flightradar_flights.flight),
            callsign = COALESCE(EXCLUDED.callsign, customer_flightradar_flights.callsign),
            operating_as = COALESCE(EXCLUDED.operating_as, customer_flightradar_flights.operating_as),
            painted_as = COALESCE(EXCLUDED.painted_as, customer_flightradar_flights.painted_as),
            origin_iata = COALESCE(EXCLUDED.origin_iata, customer_flightradar_flights.origin_iata),
            origin_icao = COALESCE(EXCLUDED.origin_icao, customer_flightradar_flights.origin_icao),
            destination_iata = COALESCE(EXCLUDED.destination_iata, customer_flightradar_flights.destination_iata),
            destination_icao = COALESCE(EXCLUDED.destination_icao, customer_flightradar_flights.destination_icao),
            datetime_takeoff = COALESCE(EXCLUDED.datetime_takeoff, customer_flightradar_flights.datetime_takeoff),
            datetime_landed = COALESCE(EXCLUDED.datetime_landed, customer_flightradar_flights.datetime_landed),
            first_seen = COALESCE(EXCLUDED.first_seen, customer_flightradar_flights.first_seen),
            last_seen = COALESCE(EXCLUDED.last_seen, customer_flightradar_flights.last_seen),
            flight_time_seconds = COALESCE(EXCLUDED.flight_time_seconds, customer_flightradar_flights.flight_time_seconds),
            estimated_flight_hours = COALESCE(EXCLUDED.estimated_flight_hours, customer_flightradar_flights.estimated_flight_hours),
            cycle_count = GREATEST(customer_flightradar_flights.cycle_count, EXCLUDED.cycle_count),
            flight_ended = COALESCE(EXCLUDED.flight_ended, customer_flightradar_flights.flight_ended),
            actual_distance_km = COALESCE(EXCLUDED.actual_distance_km, customer_flightradar_flights.actual_distance_km),
            circle_distance_km = COALESCE(EXCLUDED.circle_distance_km, customer_flightradar_flights.circle_distance_km),
            raw_payload = EXCLUDED.raw_payload,
            updated_at = NOW()
        """,
        (
            customer_id,
            link_id,
            dedupe_key,
            flight.get('fr24_id'),
            registration,
            flight.get('type'),
            flight.get('flight'),
            flight.get('callsign'),
            flight.get('operating_as'),
            flight.get('painted_as'),
            origin_iata,
            origin_icao,
            destination_iata,
            destination_icao,
            _flight_datetime_for_db(flight.get('datetime_takeoff')),
            datetime_landed,
            _flight_datetime_for_db(flight.get('first_seen')),
            _flight_datetime_for_db(flight.get('last_seen')),
            duration_seconds,
            estimated_hours,
            cycle_count,
            flight_ended,
            _safe_float(flight.get('actual_distance')),
            _safe_float(flight.get('circle_distance')),
            payload_json,
        ),
        commit=True,
    )
    return True


def _normalize_summary_window(date_from: str, date_to: str):
    now = datetime.now(timezone.utc)
    end = _parse_iso_datetime(date_to) or now
    start = _parse_iso_datetime(date_from) or (end - timedelta(days=2))
    if end > now:
        end = now
    if start > end:
        raise ValueError('Start date must be before end date.')
    max_start = end - timedelta(days=2)
    if start < max_start:
        start = max_start
    return start, end


def _normalize_match_mode(value: str) -> str:
    mode = (value or 'operating_as').strip().lower()
    if mode not in ('operating_as', 'painted_as', 'both'):
        raise ValueError('Match mode must be operating_as, painted_as, or both.')
    return mode


@lru_cache(maxsize=1)
def _load_airline_dat_rows():
    if not _AIRLINES_DAT_PATH.exists():
        return []

    rows = []
    with _AIRLINES_DAT_PATH.open('r', encoding='utf-8', newline='') as handle:
        reader = csv.reader(handle)
        for raw in reader:
            if len(raw) < 8:
                continue
            airline_id, name, alias, iata, icao, callsign, country, active = raw[:8]
            def clean(value):
                value = (value or '').strip()
                return '' if value == r'\N' else value

            icao = clean(icao).upper()
            if not icao:
                continue

            row = {
                'id': clean(airline_id),
                'name': clean(name),
                'alias': clean(alias),
                'iata': clean(iata).upper(),
                'icao': icao,
                'callsign': clean(callsign),
                'country': clean(country),
                'active': clean(active).upper() == 'Y',
            }
            row['search_text'] = ' '.join(
                str(row.get(key) or '').lower()
                for key in ('name', 'alias', 'iata', 'icao', 'callsign', 'country')
            )
            rows.append(row)
    return rows


def search_local_airline_operators(query: str, *, limit: int = 25):
    terms = [term.lower() for term in re.split(r'\s+', (query or '').strip()) if term.strip()]
    if not terms:
        return []

    def term_matches(row, term):
        if len(term) <= 3:
            if term in (row['icao'].lower(), row['iata'].lower()):
                return True
            words = re.findall(r'[a-z0-9]+', row['search_text'])
            return any(word.startswith(term) for word in words)
        return term in row['search_text']

    matches = []
    for row in _load_airline_dat_rows():
        if all(term_matches(row, term) for term in terms):
            score = 0
            q = ' '.join(terms)
            if row['icao'].lower() == q:
                score += 100
            if row['iata'].lower() == q:
                score += 80
            if row['name'].lower().startswith(q):
                score += 50
            if row['active']:
                score += 10
            matches.append((score, row))

    matches.sort(key=lambda item: (-item[0], item[1]['name'], item[1]['icao']))
    return [
        {key: value for key, value in row.items() if key != 'search_text'}
        for _, row in matches[:limit]
    ]


def get_local_airline_operator(icao: str):
    normalized = (icao or '').strip().upper()
    if not normalized:
        return None
    for row in _load_airline_dat_rows():
        if row['icao'] == normalized:
            return {key: value for key, value in row.items() if key != 'search_text'}
    return None


def _flightradar_error_response(exc: FlightradarError):
    hints = {
        'invalid_api_key': 'Check the Flightradar24 API key in Settings.',
        'subscription_or_credit_required': 'This Flightradar24 endpoint may not be included in the current plan, or the account may be out of credits.',
        'endpoint_forbidden': 'The current Flightradar24 plan may not allow this endpoint.',
        'rate_limited': 'Flightradar24 rate-limited the request. Try again later.',
        'not_found': 'Flightradar24 did not find a matching record.',
    }
    status = exc.status_code if exc.status_code in (400, 401, 402, 403, 404, 429) else 502
    return jsonify({
        'ok': False,
        'error': str(exc),
        'reason': exc.reason,
        'hint': hints.get(exc.reason, 'Flightradar24 returned an error.'),
    }), status


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


def _customer_access_sql(customer_alias='c'):
    if (
        current_user.is_administrator()
        or current_user.can(Permission.VIEW_CUSTOMERS)
        or current_user.can(Permission.EDIT_CUSTOMERS)
    ):
        return '', []
    try:
        user_salesperson_id = current_user.get_salesperson_id()
    except Exception:
        user_salesperson_id = None
    if not user_salesperson_id:
        return 'AND 1 = 0', []
    return f'AND {customer_alias}.salesperson_id = ?', [user_salesperson_id]


def _json_value(value):
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, date) and not isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _row_to_json(row):
    return {key: _json_value(value) for key, value in dict(row).items()}


def record_flightradar_sync_run(result: dict, *, source: str, sync_type: str = 'activity', started_at=None, error_message: str = None):
    result = result or {}
    completed_at = datetime.now(timezone.utc)
    if started_at is None:
        started_at = completed_at
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=timezone.utc)
    duration_seconds = max(0.0, (completed_at - started_at).total_seconds())
    errors = result.get('errors') or []
    payload_json = json.dumps(result, default=str)
    db_execute(
        """
        INSERT INTO flightradar_sync_runs (
            sync_type,
            source,
            mode,
            ok,
            started_at,
            completed_at,
            duration_seconds,
            customer_id,
            lookback_hours,
            chunk_hours,
            max_requests,
            request_count,
            link_count,
            processed_link_count,
            flight_count,
            logged_flight_count,
            refreshed_aircraft_count,
            error_count,
            stopped_reason,
            error_message,
            result_payload
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?::jsonb)
        """,
        (
            sync_type,
            source,
            result.get('mode'),
            bool(result.get('ok')),
            started_at,
            completed_at,
            duration_seconds,
            result.get('customer_id'),
            result.get('lookback_hours'),
            result.get('chunk_hours'),
            result.get('max_requests'),
            int(result.get('request_count') or 0),
            int(result.get('link_count') or 0),
            int(result.get('processed_link_count') or 0),
            int(result.get('flight_count') or 0),
            int(result.get('logged_flight_count') or 0),
            int(result.get('refreshed_aircraft_count') or 0),
            len(errors),
            result.get('stopped_reason'),
            (error_message or result.get('error') or '')[:1000] or None,
            payload_json,
        ),
        commit=True,
    )


def _aircraft_model_expr(table_alias='f'):
    return (
        f"COALESCE(NULLIF({table_alias}.raw_payload->>'model', ''), "
        f"NULLIF({table_alias}.raw_payload->>'aircraft_model', ''), "
        f"NULLIF({table_alias}.raw_payload->>'aircraft', ''), "
        f"{table_alias}.aircraft_type)"
    )


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


def _get_active_flightradar_links(customer_id=None):
    customer_filter = ''
    params = []
    if customer_id:
        customer_filter = 'AND l.customer_id = ?'
        params.append(customer_id)

    rows = db_execute(
        f"""
        SELECT id,
               customer_id,
               customer_name,
               airline_icao,
               airline_iata,
               airline_name,
               match_mode,
               default_bounds,
               is_active,
               last_live_sync_at,
               last_activity_sync_at,
               activity_sync_cursor_at,
               last_activity_sync_window_from,
               last_activity_sync_window_to
        FROM (
            SELECT l.id,
                   l.customer_id,
                   c.name AS customer_name,
                   l.airline_icao,
                   l.airline_iata,
                   l.airline_name,
                   l.match_mode,
                   l.default_bounds,
                   l.is_active,
                   l.last_live_sync_at,
                   l.last_activity_sync_at,
                   l.activity_sync_cursor_at,
                   l.last_activity_sync_window_from,
                   l.last_activity_sync_window_to
            FROM customer_flightradar_links l
            JOIN customers c ON c.id = l.customer_id
            WHERE l.is_active = TRUE
              {customer_filter}
        ) active_links
        ORDER BY customer_name, airline_name NULLS LAST, airline_icao, match_mode
        """,
        tuple(params),
        fetch='all',
    ) or []
    return [dict(row) for row in rows]


def _mark_flightradar_link_activity_sync(link_id: int, *, error: str = None):
    db_execute(
        """
        UPDATE customer_flightradar_links
        SET last_activity_sync_at = NOW(),
            last_activity_sync_error = ?,
            updated_at = NOW()
        WHERE id = ?
        """,
        (error, link_id),
        commit=True,
    )


def _mark_flightradar_link_activity_window(
    link_id: int,
    *,
    window_from: datetime = None,
    window_to: datetime = None,
    next_cursor: datetime = None,
    error: str = None,
):
    db_execute(
        """
        UPDATE customer_flightradar_links
        SET last_activity_sync_at = NOW(),
            last_activity_sync_error = ?,
            last_activity_sync_window_from = ?,
            last_activity_sync_window_to = ?,
            activity_sync_cursor_at = ?,
            updated_at = NOW()
        WHERE id = ?
        """,
        (error, window_from, window_to, next_cursor, link_id),
        commit=True,
    )


def _mark_flightradar_link_live_sync(link_id: int):
    db_execute(
        """
        UPDATE customer_flightradar_links
        SET last_live_sync_at = NOW(),
            updated_at = NOW()
        WHERE id = ?
        """,
        (link_id,),
        commit=True,
    )


def _flight_seen_time_sql():
    return 'COALESCE(first_seen, datetime_takeoff, created_at)'


def _refresh_aircraft_utilization(customer_id: int, registration: str):
    normalized_registration = str(registration or '').strip().upper()
    if not normalized_registration:
        return False

    route_rows = db_execute(
        f"""
        SELECT CONCAT(COALESCE(origin_iata, origin_icao, 'Unknown'), ' -> ', COALESCE(destination_iata, destination_icao, 'Unknown')) AS route,
               COUNT(*) AS count
        FROM customer_flightradar_flights
        WHERE customer_id = ?
          AND registration = ?
        GROUP BY route
        ORDER BY count DESC, route
        LIMIT 10
        """,
        (customer_id, normalized_registration),
        fetch='all',
    ) or []
    top_routes_json = json.dumps([_row_to_json(row) for row in route_rows], default=str)

    db_execute(
        f"""
        INSERT INTO customer_flightradar_aircraft_utilization (
            customer_id,
            registration,
            aircraft_type,
            first_seen_at,
            latest_seen_at,
            total_flight_count,
            total_flight_hours,
            total_cycles,
            flight_count_7d,
            flight_hours_7d,
            cycles_7d,
            flight_count_30d,
            flight_hours_30d,
            cycles_30d,
            flight_count_90d,
            flight_hours_90d,
            cycles_90d,
            avg_daily_hours_30d,
            avg_daily_cycles_30d,
            top_routes
        )
        SELECT customer_id,
               registration,
               (ARRAY_AGG(aircraft_type ORDER BY {_flight_seen_time_sql()} DESC)
                   FILTER (WHERE aircraft_type IS NOT NULL AND aircraft_type <> ''))[1] AS aircraft_type,
               MIN({_flight_seen_time_sql()}) AS first_seen_at,
               MAX(COALESCE(last_seen, datetime_landed, first_seen, datetime_takeoff, created_at)) AS latest_seen_at,
               COUNT(*) AS total_flight_count,
               COALESCE(SUM(estimated_flight_hours), 0) AS total_flight_hours,
               COALESCE(SUM(cycle_count), 0) AS total_cycles,
               COUNT(*) FILTER (WHERE {_flight_seen_time_sql()} >= NOW() - INTERVAL '7 days') AS flight_count_7d,
               COALESCE(SUM(estimated_flight_hours) FILTER (WHERE {_flight_seen_time_sql()} >= NOW() - INTERVAL '7 days'), 0) AS flight_hours_7d,
               COALESCE(SUM(cycle_count) FILTER (WHERE {_flight_seen_time_sql()} >= NOW() - INTERVAL '7 days'), 0) AS cycles_7d,
               COUNT(*) FILTER (WHERE {_flight_seen_time_sql()} >= NOW() - INTERVAL '30 days') AS flight_count_30d,
               COALESCE(SUM(estimated_flight_hours) FILTER (WHERE {_flight_seen_time_sql()} >= NOW() - INTERVAL '30 days'), 0) AS flight_hours_30d,
               COALESCE(SUM(cycle_count) FILTER (WHERE {_flight_seen_time_sql()} >= NOW() - INTERVAL '30 days'), 0) AS cycles_30d,
               COUNT(*) FILTER (WHERE {_flight_seen_time_sql()} >= NOW() - INTERVAL '90 days') AS flight_count_90d,
               COALESCE(SUM(estimated_flight_hours) FILTER (WHERE {_flight_seen_time_sql()} >= NOW() - INTERVAL '90 days'), 0) AS flight_hours_90d,
               COALESCE(SUM(cycle_count) FILTER (WHERE {_flight_seen_time_sql()} >= NOW() - INTERVAL '90 days'), 0) AS cycles_90d,
               ROUND((COALESCE(SUM(estimated_flight_hours) FILTER (WHERE {_flight_seen_time_sql()} >= NOW() - INTERVAL '30 days'), 0) / 30.0)::numeric, 4) AS avg_daily_hours_30d,
               ROUND((COALESCE(SUM(cycle_count) FILTER (WHERE {_flight_seen_time_sql()} >= NOW() - INTERVAL '30 days'), 0) / 30.0)::numeric, 4) AS avg_daily_cycles_30d,
               ?::jsonb AS top_routes
        FROM customer_flightradar_flights
        WHERE customer_id = ?
          AND registration = ?
        GROUP BY customer_id, registration
        ON CONFLICT (customer_id, registration)
        DO UPDATE SET
            aircraft_type = COALESCE(EXCLUDED.aircraft_type, customer_flightradar_aircraft_utilization.aircraft_type),
            first_seen_at = EXCLUDED.first_seen_at,
            latest_seen_at = EXCLUDED.latest_seen_at,
            total_flight_count = EXCLUDED.total_flight_count,
            total_flight_hours = EXCLUDED.total_flight_hours,
            total_cycles = EXCLUDED.total_cycles,
            flight_count_7d = EXCLUDED.flight_count_7d,
            flight_hours_7d = EXCLUDED.flight_hours_7d,
            cycles_7d = EXCLUDED.cycles_7d,
            flight_count_30d = EXCLUDED.flight_count_30d,
            flight_hours_30d = EXCLUDED.flight_hours_30d,
            cycles_30d = EXCLUDED.cycles_30d,
            flight_count_90d = EXCLUDED.flight_count_90d,
            flight_hours_90d = EXCLUDED.flight_hours_90d,
            cycles_90d = EXCLUDED.cycles_90d,
            avg_daily_hours_30d = EXCLUDED.avg_daily_hours_30d,
            avg_daily_cycles_30d = EXCLUDED.avg_daily_cycles_30d,
            top_routes = EXCLUDED.top_routes,
            updated_at = NOW()
        """,
        (top_routes_json, customer_id, normalized_registration),
        commit=True,
    )
    return True


def refresh_flightradar_utilization(customer_ids=None, registrations=None):
    where_clauses = ["registration IS NOT NULL", "registration <> ''"]
    params = []

    if customer_ids:
        where_clauses.append("customer_id = ANY(?)")
        params.append(list(customer_ids))
    if registrations:
        where_clauses.append("registration = ANY(?)")
        params.append([str(reg).strip().upper() for reg in registrations if str(reg or '').strip()])

    rows = db_execute(
        f"""
        SELECT DISTINCT customer_id, registration
        FROM customer_flightradar_flights
        WHERE {' AND '.join(where_clauses)}
        """,
        tuple(params),
        fetch='all',
    ) or []

    refreshed = 0
    for row in rows:
        if _refresh_aircraft_utilization(row['customer_id'], row['registration']):
            refreshed += 1
    return refreshed


def _sync_flightradar_link_window(
    *,
    client,
    link,
    start: datetime,
    end: datetime,
    limit: int,
    seen_keys,
    affected_tails,
):
    mode = _normalize_match_mode(link.get('match_mode'))
    icao = link.get('airline_icao')
    payload = client.get_flight_summary_full(
        flight_datetime_from=_format_fr24_datetime(start),
        flight_datetime_to=_format_fr24_datetime(end),
        operating_as=icao if mode in ('operating_as', 'both') else None,
        painted_as=icao if mode in ('painted_as', 'both') else None,
        limit=limit,
        sort='desc',
    )
    link_flights = payload.get('data') if isinstance(payload, dict) else []
    returned_times = []
    logged = 0
    unique = 0

    for flight in link_flights or []:
        returned_time = (
            _flight_datetime_value(flight.get('first_seen'))
            or _flight_datetime_value(flight.get('datetime_takeoff'))
            or _flight_datetime_value(flight.get('last_seen'))
            or _flight_datetime_value(flight.get('datetime_landed'))
        )
        if returned_time:
            returned_times.append(returned_time)

        dedupe_key = (link.get('customer_id'), _flight_dedupe_key(flight))
        if dedupe_key in seen_keys:
            continue
        seen_keys.add(dedupe_key)
        unique += 1
        flight['_customer_flightradar_link_id'] = link.get('id')
        flight['_customer_flightradar_match_mode'] = mode
        if _upsert_customer_flight(link.get('customer_id'), link.get('id'), flight):
            logged += 1
            registration = str(flight.get('reg') or '').strip().upper()
            if registration:
                affected_tails.add((link.get('customer_id'), registration))

    return {
        'mode': mode,
        'returned_flight_count': len(link_flights or []),
        'unique_flight_count': unique,
        'logged_flight_count': logged,
        'first_returned_at': min(returned_times).isoformat() if returned_times else None,
        'last_returned_at': max(returned_times).isoformat() if returned_times else None,
    }


def sync_flightradar_activity_window(*, window_hours: int = 48, limit: int = 500, customer_id=None, chunk_hours=None):
    end = datetime.now(timezone.utc)
    start = end - timedelta(hours=max(1, min(int(window_hours or 48), 336)))
    limit = max(1, min(int(limit or 500), 20000))
    if chunk_hours is None:
        chunk_hours = int(os.getenv(
            'FLIGHTRADAR_ACTIVITY_SYNC_CHUNK_HOURS',
            '6' if customer_id else str(max(1, min(int(window_hours or 48), 336)))
        ))
    chunk_hours = max(1, min(int(chunk_hours or 24), 336))
    request_delay_seconds = max(0.0, float(os.getenv('FLIGHTRADAR_ACTIVITY_SYNC_DELAY_SECONDS', '0.25') or 0))
    client = _build_client()
    links = _get_active_flightradar_links(customer_id=customer_id)
    seen_keys = set()
    affected_tails = set()
    result = {
        'ok': True,
        'window': {
            'from': _format_fr24_datetime(start),
            'to': _format_fr24_datetime(end),
            'chunk_hours': chunk_hours,
        },
        'link_count': len(links),
        'flight_count': 0,
        'logged_flight_count': 0,
        'refreshed_aircraft_count': 0,
        'links': [],
        'errors': [],
        'stopped_reason': None,
    }

    for link in links:
        if result['stopped_reason']:
            break

        link_id = link.get('id')
        link_result = {
            'link_id': link_id,
            'customer_id': link.get('customer_id'),
            'customer_name': link.get('customer_name'),
            'airline_icao': link.get('airline_icao'),
            'airline_name': link.get('airline_name'),
            'match_mode': link.get('match_mode'),
            'returned_flight_count': 0,
            'logged_flight_count': 0,
            'request_count': 0,
            'first_returned_at': None,
            'last_returned_at': None,
            'error': None,
        }
        try:
            mode = _normalize_match_mode(link.get('match_mode'))
            icao = link.get('airline_icao')
            link_result['match_mode'] = mode
            returned_times = []
            link_logged = 0

            chunk_end = end
            while chunk_end > start:
                chunk_start = max(start, chunk_end - timedelta(hours=chunk_hours))
                payload = client.get_flight_summary_full(
                    flight_datetime_from=_format_fr24_datetime(chunk_start),
                    flight_datetime_to=_format_fr24_datetime(chunk_end),
                    operating_as=icao if mode in ('operating_as', 'both') else None,
                    painted_as=icao if mode in ('painted_as', 'both') else None,
                    limit=limit,
                    sort='desc',
                )
                link_result['request_count'] += 1
                link_flights = payload.get('data') if isinstance(payload, dict) else []
                link_result['returned_flight_count'] += len(link_flights or [])

                for flight in link_flights or []:
                    returned_time = (
                        _flight_datetime_value(flight.get('first_seen'))
                        or _flight_datetime_value(flight.get('datetime_takeoff'))
                        or _flight_datetime_value(flight.get('last_seen'))
                        or _flight_datetime_value(flight.get('datetime_landed'))
                    )
                    if returned_time:
                        returned_times.append(returned_time)
                    dedupe_key = (link.get('customer_id'), _flight_dedupe_key(flight))
                    if dedupe_key in seen_keys:
                        continue
                    seen_keys.add(dedupe_key)
                    result['flight_count'] += 1
                    flight['_customer_flightradar_link_id'] = link_id
                    flight['_customer_flightradar_match_mode'] = mode
                    if _upsert_customer_flight(link.get('customer_id'), link_id, flight):
                        result['logged_flight_count'] += 1
                        link_logged += 1
                        registration = str(flight.get('reg') or '').strip().upper()
                        if registration:
                            affected_tails.add((link.get('customer_id'), registration))

                chunk_end = chunk_start
                if request_delay_seconds and chunk_end > start:
                    time.sleep(request_delay_seconds)

            link_result['logged_flight_count'] = link_logged
            if returned_times:
                link_result['first_returned_at'] = min(returned_times).isoformat()
                link_result['last_returned_at'] = max(returned_times).isoformat()
            _mark_flightradar_link_activity_sync(link_id)
            current_app.logger.info(
                "Flightradar activity sync link_id=%s customer_id=%s flights=%s logged=%s",
                link_id,
                link.get('customer_id'),
                link_result['returned_flight_count'],
                link_logged,
            )
        except FlightradarError as exc:
            error = str(exc)
            result['errors'].append({
                'link_id': link_id,
                'customer_id': link.get('customer_id'),
                'reason': exc.reason,
                'error': error,
            })
            link_result['error'] = error
            _mark_flightradar_link_activity_sync(link_id, error=error[:1000])
            current_app.logger.warning(
                "Flightradar activity sync API error link_id=%s customer_id=%s reason=%s error=%s",
                link_id,
                link.get('customer_id'),
                exc.reason,
                exc,
            )
            if exc.reason == 'rate_limited':
                result['stopped_reason'] = 'rate_limited'
        except Exception as exc:
            error = str(exc)
            result['errors'].append({
                'link_id': link_id,
                'customer_id': link.get('customer_id'),
                'reason': 'unexpected_error',
                'error': error,
            })
            link_result['error'] = error
            _mark_flightradar_link_activity_sync(link_id, error=error[:1000])
            current_app.logger.exception(
                "Unexpected Flightradar activity sync error link_id=%s customer_id=%s",
                link_id,
                link.get('customer_id'),
            )
        finally:
            result['links'].append(link_result)
            if request_delay_seconds and not result['stopped_reason']:
                time.sleep(request_delay_seconds)

    for customer_id, registration in affected_tails:
        if _refresh_aircraft_utilization(customer_id, registration):
            result['refreshed_aircraft_count'] += 1

    result['ok'] = len(result['errors']) == 0
    return result


def sync_flightradar_activity_incremental(
    *,
    lookback_hours: int = 336,
    chunk_hours: int = 6,
    max_requests: int = 20,
    limit: int = 20000,
    customer_id=None,
):
    end = datetime.now(timezone.utc)
    lookback_hours = max(1, min(int(lookback_hours or 336), 336))
    chunk_hours = max(1, min(int(chunk_hours or 6), lookback_hours))
    max_requests = max(1, min(int(max_requests or 20), 500))
    limit = max(1, min(int(limit or 20000), 20000))
    floor_start = end - timedelta(hours=lookback_hours)
    request_delay_seconds = max(0.0, float(os.getenv('FLIGHTRADAR_ACTIVITY_SYNC_DELAY_SECONDS', '0.25') or 0))
    client = _build_client()
    links = _get_active_flightradar_links(customer_id=customer_id)
    seen_keys = set()
    affected_tails = set()
    result = {
        'ok': True,
        'mode': 'incremental',
        'customer_id': customer_id,
        'lookback_hours': lookback_hours,
        'chunk_hours': chunk_hours,
        'max_requests': max_requests,
        'request_count': 0,
        'link_count': len(links),
        'processed_link_count': 0,
        'flight_count': 0,
        'logged_flight_count': 0,
        'refreshed_aircraft_count': 0,
        'links': [],
        'errors': [],
        'stopped_reason': None,
        'more_available': False,
    }

    def cursor_for_link(link):
        cursor = link.get('activity_sync_cursor_at')
        if isinstance(cursor, str):
            cursor = _flight_datetime_value(cursor)
        last_window_from = link.get('last_activity_sync_window_from')
        if isinstance(last_window_from, str):
            last_window_from = _flight_datetime_value(last_window_from)
        if last_window_from:
            if last_window_from.tzinfo is None:
                last_window_from = last_window_from.replace(tzinfo=timezone.utc)
            else:
                last_window_from = last_window_from.astimezone(timezone.utc)
        if last_window_from and last_window_from <= floor_start:
            return end
        if not cursor or cursor <= floor_start or cursor > end:
            return end
        if cursor.tzinfo is None:
            return cursor.replace(tzinfo=timezone.utc)
        return cursor.astimezone(timezone.utc)

    def sync_sort_time(link):
        value = link.get('last_activity_sync_at')
        if isinstance(value, str):
            value = _flight_datetime_value(value)
        if not value:
            return datetime.min.replace(tzinfo=timezone.utc)
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    ordered_links = sorted(
        links,
        key=lambda link: (
            link.get('last_activity_sync_at') is not None,
            sync_sort_time(link),
            link.get('customer_name') or '',
            link.get('airline_icao') or '',
            link.get('id') or 0,
        ),
    )
    completed_link_ids = set()
    touched_link_ids = set()
    link_results = {}

    def link_result_for(link):
        link_id = link.get('id')
        if link_id not in link_results:
            link_results[link_id] = {
                'link_id': link_id,
                'customer_id': link.get('customer_id'),
                'customer_name': link.get('customer_name'),
                'airline_icao': link.get('airline_icao'),
                'airline_name': link.get('airline_name'),
                'match_mode': link.get('match_mode'),
                'request_count': 0,
                'returned_flight_count': 0,
                'logged_flight_count': 0,
                'window_from': None,
                'window_to': None,
                'next_cursor': None,
                'first_returned_at': None,
                'last_returned_at': None,
                'error': None,
            }
        return link_results[link_id]

    def merge_returned_range(link_result, sync_result):
        first_returned = sync_result.get('first_returned_at')
        last_returned = sync_result.get('last_returned_at')
        if first_returned and (
            not link_result.get('first_returned_at')
            or first_returned < link_result['first_returned_at']
        ):
            link_result['first_returned_at'] = first_returned
        if last_returned and (
            not link_result.get('last_returned_at')
            or last_returned > link_result['last_returned_at']
        ):
            link_result['last_returned_at'] = last_returned

    while result['request_count'] < max_requests and not result['stopped_reason']:
        made_request = False
        for link in ordered_links:
            if result['request_count'] >= max_requests or result['stopped_reason']:
                break

            link_id = link.get('id')
            if link_id in completed_link_ids:
                continue

            chunk_end = cursor_for_link(link)
            chunk_start = max(floor_start, chunk_end - timedelta(hours=chunk_hours))
            next_cursor = end if chunk_start <= floor_start else chunk_start
            link_result = link_result_for(link)
            link_result['window_from'] = chunk_start.isoformat()
            link_result['window_to'] = chunk_end.isoformat() if not link_result.get('window_to') else link_result['window_to']
            link_result['next_cursor'] = next_cursor.isoformat()

            try:
                sync_result = _sync_flightradar_link_window(
                    client=client,
                    link=link,
                    start=chunk_start,
                    end=chunk_end,
                    limit=limit,
                    seen_keys=seen_keys,
                    affected_tails=affected_tails,
                )
                made_request = True
                touched_link_ids.add(link_id)
                result['request_count'] += 1
                result['processed_link_count'] = len(touched_link_ids)
                result['flight_count'] += sync_result['unique_flight_count']
                result['logged_flight_count'] += sync_result['logged_flight_count']
                link_result['match_mode'] = sync_result.get('mode') or link_result['match_mode']
                link_result['request_count'] += 1
                link_result['returned_flight_count'] += sync_result['returned_flight_count']
                link_result['logged_flight_count'] += sync_result['logged_flight_count']
                merge_returned_range(link_result, sync_result)
                _mark_flightradar_link_activity_window(
                    link_id,
                    window_from=chunk_start,
                    window_to=chunk_end,
                    next_cursor=next_cursor,
                )
                link['activity_sync_cursor_at'] = next_cursor
                link['last_activity_sync_window_from'] = chunk_start
                link['last_activity_sync_window_to'] = chunk_end
                if next_cursor == end:
                    completed_link_ids.add(link_id)
            except FlightradarError as exc:
                made_request = True
                touched_link_ids.add(link_id)
                result['request_count'] += 1
                result['processed_link_count'] = len(touched_link_ids)
                error = str(exc)
                link_result['request_count'] += 1
                link_result['error'] = error
                result['errors'].append({
                    'link_id': link_id,
                    'customer_id': link.get('customer_id'),
                    'reason': exc.reason,
                    'error': error,
                })
                _mark_flightradar_link_activity_window(
                    link_id,
                    window_from=chunk_start,
                    window_to=chunk_end,
                    next_cursor=chunk_end,
                    error=error[:1000],
                )
                if exc.reason == 'rate_limited':
                    result['stopped_reason'] = 'rate_limited'
                else:
                    completed_link_ids.add(link_id)
            except Exception as exc:
                made_request = True
                touched_link_ids.add(link_id)
                result['request_count'] += 1
                result['processed_link_count'] = len(touched_link_ids)
                error = str(exc)
                link_result['request_count'] += 1
                link_result['error'] = error
                result['errors'].append({
                    'link_id': link_id,
                    'customer_id': link.get('customer_id'),
                    'reason': 'unexpected_error',
                    'error': error,
                })
                _mark_flightradar_link_activity_window(
                    link_id,
                    window_from=chunk_start,
                    window_to=chunk_end,
                    next_cursor=chunk_end,
                    error=error[:1000],
                )
                current_app.logger.exception(
                    "Unexpected incremental Flightradar sync error link_id=%s customer_id=%s",
                    link_id,
                    link.get('customer_id'),
                )
                completed_link_ids.add(link_id)
            finally:
                if request_delay_seconds and result['request_count'] < max_requests and not result['stopped_reason']:
                    time.sleep(request_delay_seconds)

        if not made_request:
            break

    result['links'] = list(link_results.values())
    result['more_available'] = len(completed_link_ids) < len(ordered_links)

    if (
        not result['stopped_reason']
        and result['request_count'] >= max_requests
        and result['more_available']
    ):
        result['stopped_reason'] = 'request_budget_exhausted'

    for customer_id, registration in affected_tails:
        if _refresh_aircraft_utilization(customer_id, registration):
            result['refreshed_aircraft_count'] += 1

    result['ok'] = len(result['errors']) == 0
    return result


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
                   last_lat,
                   last_lon,
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


def sync_flightradar_live_incremental(*, max_customers: int = 1, limit: int = 500, customer_id=None):
    max_customers = max(1, min(int(max_customers or 1), 25))
    limit = max(1, min(int(limit or 500), 20000))
    links = _get_active_flightradar_links(customer_id=customer_id)
    client = _build_client()
    result = {
        'ok': True,
        'mode': 'live_incremental',
        'customer_count': 0,
        'link_count': len(links),
        'processed_link_count': 0,
        'flight_count': 0,
        'stored_tail_count': 0,
        'customers': [],
        'links': [],
        'errors': [],
        'stopped_reason': None,
    }

    def live_sort_time(link):
        value = link.get('last_live_sync_at')
        if isinstance(value, str):
            value = _flight_datetime_value(value)
        if not value:
            return datetime.min.replace(tzinfo=timezone.utc)
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    customer_groups = {}
    for link in links:
        customer_groups.setdefault(link.get('customer_id'), []).append(link)

    ordered_customer_ids = sorted(
        customer_groups.keys(),
        key=lambda cid: (
            min(live_sort_time(link) for link in customer_groups[cid]),
            customer_groups[cid][0].get('customer_name') or '',
            cid or 0,
        ),
    )

    for current_customer_id in ordered_customer_ids[:max_customers]:
        if result['stopped_reason']:
            break

        customer_links = sorted(
            customer_groups[current_customer_id],
            key=lambda link: (
                live_sort_time(link),
                link.get('airline_name') or '',
                link.get('airline_icao') or '',
                link.get('id') or 0,
            ),
        )
        customer_name = customer_links[0].get('customer_name') if customer_links else current_customer_id
        customer_result = {
            'customer_id': current_customer_id,
            'customer_name': customer_name,
            'link_count': len(customer_links),
            'flight_count': 0,
            'stored_tail_count': 0,
            'errors': [],
        }
        seen_keys = set()

        for link in customer_links:
            link_id = link.get('id')
            mode = _normalize_match_mode(link.get('match_mode'))
            icao = link.get('airline_icao')
            link_result = {
                'link_id': link_id,
                'customer_id': current_customer_id,
                'customer_name': customer_name,
                'airline_icao': icao,
                'airline_name': link.get('airline_name'),
                'match_mode': mode,
                'returned_flight_count': 0,
                'stored_tail_count': 0,
                'error': None,
            }
            try:
                payload = client.get_live_positions_full(
                    operating_as=icao if mode in ('operating_as', 'both') else None,
                    painted_as=icao if mode in ('painted_as', 'both') else None,
                    bounds=link.get('default_bounds') or '',
                    limit=limit,
                )
                link_flights = payload.get('data') if isinstance(payload, dict) else []
                link_result['returned_flight_count'] = len(link_flights or [])
                result['processed_link_count'] += 1

                for flight in link_flights or []:
                    dedupe_key = flight.get('fr24_id') or flight.get('reg') or (
                        flight.get('callsign'),
                        flight.get('lat'),
                        flight.get('lon'),
                    )
                    if dedupe_key in seen_keys:
                        continue
                    seen_keys.add(dedupe_key)
                    result['flight_count'] += 1
                    customer_result['flight_count'] += 1
                    flight['_customer_flightradar_link_id'] = link_id
                    flight['_customer_flightradar_match_mode'] = mode
                    if _upsert_customer_aircraft(current_customer_id, link_id, flight):
                        result['stored_tail_count'] += 1
                        customer_result['stored_tail_count'] += 1
                        link_result['stored_tail_count'] += 1

                _mark_flightradar_link_live_sync(link_id)
            except FlightradarError as exc:
                error = str(exc)
                link_result['error'] = error
                result['errors'].append({
                    'link_id': link_id,
                    'customer_id': current_customer_id,
                    'reason': exc.reason,
                    'error': error,
                })
                customer_result['errors'].append(error)
                if exc.reason == 'rate_limited':
                    result['stopped_reason'] = 'rate_limited'
            except Exception as exc:
                error = str(exc)
                link_result['error'] = error
                result['errors'].append({
                    'link_id': link_id,
                    'customer_id': current_customer_id,
                    'reason': 'unexpected_error',
                    'error': error,
                })
                customer_result['errors'].append(error)
                current_app.logger.exception(
                    "Unexpected Flightradar live sync error link_id=%s customer_id=%s",
                    link_id,
                    current_customer_id,
                )
            finally:
                result['links'].append(link_result)

            if result['stopped_reason']:
                break

        result['customer_count'] += 1
        result['customers'].append(customer_result)

    result['ok'] = len(result['errors']) == 0
    return result


def _summarize_flights(flights):
    tails = set()
    routes = {}
    types = {}
    airports = {}
    flight_hours = 0.0
    completed_cycles = 0

    for flight in flights:
        reg = flight.get('reg')
        if reg:
            tails.add(str(reg).upper())

        aircraft_type = flight.get('type')
        if aircraft_type:
            types[aircraft_type] = types.get(aircraft_type, 0) + 1

        origin = flight.get('orig_iata') or flight.get('orig_icao')
        destination = (
            flight.get('dest_iata_actual')
            or flight.get('dest_iata')
            or flight.get('dest_icao_actual')
            or flight.get('dest_icao')
        )
        if origin:
            airports[origin] = airports.get(origin, 0) + 1
        if destination:
            airports[destination] = airports.get(destination, 0) + 1
        if origin or destination:
            route = f"{origin or '?'} -> {destination or '?'}"
            routes[route] = routes.get(route, 0) + 1

        duration_seconds = _flight_duration_seconds(flight)
        if duration_seconds is not None:
            flight_hours += duration_seconds / 3600
        if flight.get('flight_ended') is True or flight.get('datetime_landed'):
            completed_cycles += 1

    def top_items(mapping, limit=10):
        return [
            {'name': key, 'count': count}
            for key, count in sorted(mapping.items(), key=lambda item: (-item[1], item[0]))[:limit]
        ]

    return {
        'flight_count': len(flights),
        'unique_tail_count': len(tails),
        'unique_tails': sorted(tails),
        'estimated_flight_hours': round(flight_hours, 2),
        'completed_cycle_count': completed_cycles,
        'top_routes': top_items(routes),
        'top_aircraft_types': top_items(types),
        'top_airports': top_items(airports),
    }


def lookup_airline_by_icao(icao: str) -> dict:
    normalized = _normalize_icao_list(icao)
    if not normalized or ',' in normalized:
        raise ValueError('Provide one airline/operator ICAO code.')
    return _build_client().get_airline_light(normalized)


def lookup_airline_by_icao_with_local_fallback(icao: str) -> dict:
    normalized = _normalize_icao_list(icao)
    if not normalized or ',' in normalized:
        raise ValueError('Provide one airline/operator ICAO code.')
    local = get_local_airline_operator(normalized)
    try:
        airline = _build_client().get_airline_light(normalized)
        airline['source'] = 'flightradar'
        return airline
    except FlightradarError as exc:
        if local:
            result = {
                'name': local.get('name'),
                'iata': local.get('iata'),
                'icao': local.get('icao'),
                'callsign': local.get('callsign'),
                'country': local.get('country'),
                'source': 'airlines.dat',
            }
            if exc.reason in ('invalid_api_key', 'subscription_or_credit_required', 'endpoint_forbidden'):
                result['validation_warning'] = str(exc)
            return result
        raise


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


@flightradar_bp.route('/aircraft')
@login_required
def aircraft_analytics():
    access_sql, access_params = _customer_access_sql('c')
    model_expr = _aircraft_model_expr('f')
    try:
        customers = db_execute(
            f"""
            SELECT DISTINCT c.id, c.name
            FROM customer_flightradar_flights f
            JOIN customers c ON c.id = f.customer_id
            WHERE 1 = 1
              {access_sql}
            ORDER BY c.name
            """,
            tuple(access_params),
            fetch='all',
        ) or []
        aircraft_types = db_execute(
            f"""
            SELECT DISTINCT f.aircraft_type
            FROM customer_flightradar_flights f
            JOIN customers c ON c.id = f.customer_id
            WHERE f.aircraft_type IS NOT NULL
              AND f.aircraft_type <> ''
              {access_sql}
            ORDER BY f.aircraft_type
            """,
            tuple(access_params),
            fetch='all',
        ) or []
        aircraft_models = db_execute(
            f"""
            SELECT DISTINCT {model_expr} AS aircraft_model
            FROM customer_flightradar_flights f
            JOIN customers c ON c.id = f.customer_id
            WHERE {model_expr} IS NOT NULL
              AND {model_expr} <> ''
              {access_sql}
            ORDER BY aircraft_model
            LIMIT 250
            """,
            tuple(access_params),
            fetch='all',
        ) or []
    except Exception as exc:
        current_app.logger.warning('Unable to load aircraft analytics filters: %s', exc)
        customers = []
        aircraft_types = []
        aircraft_models = []

    return render_template(
        'flightradar/aircraft.html',
        customers=[_row_to_json(row) for row in customers],
        aircraft_types=[row['aircraft_type'] for row in aircraft_types],
        aircraft_models=[row['aircraft_model'] for row in aircraft_models],
    )


def _aircraft_filter_sql(args):
    access_sql, access_params = _customer_access_sql('c')
    clauses = [access_sql] if access_sql else []
    params = list(access_params)
    model_expr = _aircraft_model_expr('f')

    customer_id = (args.get('customer_id') or '').strip()
    if customer_id:
        clauses.append('AND f.customer_id = ?')
        params.append(int(customer_id))

    registration = (args.get('registration') or '').strip().upper()
    if registration:
        clauses.append('AND UPPER(f.registration) = ?')
        params.append(registration)

    aircraft_type = (args.get('aircraft_type') or '').strip()
    if aircraft_type:
        clauses.append('AND f.aircraft_type = ?')
        params.append(aircraft_type)

    aircraft_model = (args.get('aircraft_model') or '').strip()
    if aircraft_model:
        clauses.append(f'AND {model_expr} = ?')
        params.append(aircraft_model)

    date_from = (args.get('date_from') or '').strip()
    if date_from:
        clauses.append("AND COALESCE(f.first_seen, f.datetime_takeoff, f.created_at) >= ?::date")
        params.append(date_from)

    date_to = (args.get('date_to') or '').strip()
    if date_to:
        clauses.append("AND COALESCE(f.first_seen, f.datetime_takeoff, f.created_at) < (?::date + INTERVAL '1 day')")
        params.append(date_to)

    return '\n'.join(clauses), params


def _aircraft_group_expr(group_by):
    model_expr = _aircraft_model_expr('f')
    if group_by == 'customer':
        return "COALESCE(c.name, 'Unknown customer')"
    if group_by == 'aircraft_type':
        return "COALESCE(NULLIF(f.aircraft_type, ''), 'Unknown type')"
    if group_by == 'aircraft_model':
        return f"COALESCE(NULLIF({model_expr}, ''), 'Unknown model')"
    if group_by == 'aircraft':
        return "COALESCE(NULLIF(f.registration, ''), 'Unknown aircraft')"
    return "'Traffic'"


def _aircraft_breakdown_sql(group_by):
    model_expr = _aircraft_model_expr('f')
    if group_by == 'customer':
        return "f.customer_id::text", "COALESCE(c.name, 'Unknown customer')"
    if group_by == 'aircraft_type':
        return "COALESCE(NULLIF(f.aircraft_type, ''), 'Unknown type')", "COALESCE(NULLIF(f.aircraft_type, ''), 'Unknown type')"
    if group_by == 'aircraft_model':
        return f"COALESCE(NULLIF({model_expr}, ''), 'Unknown model')", f"COALESCE(NULLIF({model_expr}, ''), 'Unknown model')"
    return "COALESCE(NULLIF(f.registration, ''), 'Unknown aircraft')", "COALESCE(NULLIF(f.registration, ''), 'Unknown aircraft')"


def _load_aircraft_breakdown(group_by, where_sql, params, *, limit=25):
    group_id_expr, group_name_expr = _aircraft_breakdown_sql(group_by)
    rows = db_execute(
        f"""
        SELECT {group_id_expr} AS group_id,
               {group_name_expr} AS group_name,
               COUNT(*) AS flight_count,
               COUNT(DISTINCT f.registration) FILTER (WHERE f.registration IS NOT NULL AND f.registration <> '') AS aircraft_count,
               COUNT(DISTINCT f.customer_id) AS customer_count,
               COALESCE(SUM(f.estimated_flight_hours), 0) AS estimated_flight_hours,
               COALESCE(SUM(f.cycle_count), 0) AS cycle_count,
               MAX(COALESCE(f.last_seen, f.datetime_landed, f.first_seen, f.datetime_takeoff, f.created_at)) AS latest_seen_at
        FROM customer_flightradar_flights f
        JOIN customers c ON c.id = f.customer_id
        WHERE 1 = 1
          {where_sql}
        GROUP BY group_id, group_name
        ORDER BY flight_count DESC, estimated_flight_hours DESC, group_name
        LIMIT ?
        """,
        tuple(params + [limit]),
        fetch='all',
    ) or []
    return [_row_to_json(row) for row in rows]


def _chart_bucket_expr(granularity):
    if granularity == 'week':
        return "DATE_TRUNC('week', COALESCE(f.first_seen, f.datetime_takeoff, f.created_at))::date"
    if granularity == 'month':
        return "DATE_TRUNC('month', COALESCE(f.first_seen, f.datetime_takeoff, f.created_at))::date"
    return "DATE_TRUNC('day', COALESCE(f.first_seen, f.datetime_takeoff, f.created_at))::date"


@flightradar_bp.route('/api/aircraft-analytics', methods=['GET'])
@login_required
def aircraft_analytics_data():
    try:
        where_sql, params = _aircraft_filter_sql(request.args)
        group_by = (request.args.get('group_by') or 'aircraft').strip()
        if group_by not in ('overall', 'aircraft', 'customer', 'aircraft_type', 'aircraft_model'):
            group_by = 'aircraft'
        granularity = (request.args.get('granularity') or 'day').strip()
        if granularity not in ('day', 'week', 'month'):
            granularity = 'day'
        group_expr = _aircraft_group_expr(group_by)
        bucket_expr = _chart_bucket_expr(granularity)
        model_expr = _aircraft_model_expr('f')

        summary = db_execute(
            f"""
            SELECT COUNT(*) AS flight_count,
                   COUNT(DISTINCT f.registration) FILTER (WHERE f.registration IS NOT NULL AND f.registration <> '') AS aircraft_count,
                   COUNT(DISTINCT f.customer_id) AS customer_count,
                   COALESCE(SUM(f.estimated_flight_hours), 0) AS estimated_flight_hours,
                   COALESCE(SUM(f.cycle_count), 0) AS cycle_count,
                   COALESCE(SUM(f.actual_distance_km), 0) AS actual_distance_km,
                   MAX(COALESCE(f.last_seen, f.datetime_landed, f.first_seen, f.datetime_takeoff, f.created_at)) AS latest_logged_at
            FROM customer_flightradar_flights f
            JOIN customers c ON c.id = f.customer_id
            WHERE 1 = 1
              {where_sql}
            """,
            tuple(params),
            fetch='one',
        ) or {}

        top_groups = db_execute(
            f"""
            SELECT {group_expr} AS group_name,
                   COUNT(*) AS flight_count
            FROM customer_flightradar_flights f
            JOIN customers c ON c.id = f.customer_id
            WHERE 1 = 1
              {where_sql}
            GROUP BY group_name
            ORDER BY flight_count DESC, group_name
            LIMIT 8
            """,
            tuple(params),
            fetch='all',
        ) or []
        selected_groups = [row['group_name'] for row in top_groups]

        history_rows = []
        if selected_groups:
            history_rows = db_execute(
                f"""
                SELECT {bucket_expr} AS bucket,
                       {group_expr} AS group_name,
                       COUNT(*) AS flight_count,
                       COALESCE(SUM(f.estimated_flight_hours), 0) AS estimated_flight_hours,
                       COALESCE(SUM(f.cycle_count), 0) AS cycle_count
                FROM customer_flightradar_flights f
                JOIN customers c ON c.id = f.customer_id
                WHERE 1 = 1
                  {where_sql}
                  AND {group_expr} = ANY(?)
                GROUP BY bucket, group_name
                ORDER BY bucket, group_name
                """,
                tuple(params + [selected_groups]),
                fetch='all',
            ) or []

        aircraft_rows = db_execute(
            f"""
            SELECT f.registration,
                   MAX(c.name) AS customer_name,
                   MAX(f.aircraft_type) AS aircraft_type,
                   MAX({model_expr}) AS aircraft_model,
                   COUNT(*) AS flight_count,
                   COALESCE(SUM(f.estimated_flight_hours), 0) AS estimated_flight_hours,
                   COALESCE(SUM(f.cycle_count), 0) AS cycle_count,
                   MIN(COALESCE(f.first_seen, f.datetime_takeoff, f.created_at)) AS first_seen,
                   MAX(COALESCE(f.last_seen, f.datetime_landed, f.first_seen, f.datetime_takeoff, f.created_at)) AS last_seen
            FROM customer_flightradar_flights f
            JOIN customers c ON c.id = f.customer_id
            WHERE f.registration IS NOT NULL
              AND f.registration <> ''
              {where_sql}
            GROUP BY f.registration
            ORDER BY flight_count DESC, last_seen DESC
            LIMIT 100
            """,
            tuple(params),
            fetch='all',
        ) or []

        route_rows = db_execute(
            f"""
            SELECT CONCAT(COALESCE(f.origin_iata, f.origin_icao, 'Unknown'), ' -> ', COALESCE(f.destination_iata, f.destination_icao, 'Unknown')) AS route,
                   COUNT(*) AS flight_count,
                   COUNT(DISTINCT f.registration) FILTER (WHERE f.registration IS NOT NULL AND f.registration <> '') AS aircraft_count,
                   COUNT(DISTINCT f.customer_id) AS customer_count,
                   COALESCE(SUM(f.estimated_flight_hours), 0) AS estimated_flight_hours,
                   COALESCE(SUM(f.cycle_count), 0) AS cycle_count
            FROM customer_flightradar_flights f
            JOIN customers c ON c.id = f.customer_id
            WHERE 1 = 1
              {where_sql}
            GROUP BY route
            ORDER BY flight_count DESC, estimated_flight_hours DESC, route
            LIMIT 25
            """,
            tuple(params),
            fetch='all',
        ) or []

        flight_rows = db_execute(
            f"""
            SELECT f.id,
                   f.customer_id,
                   c.name AS customer_name,
                   f.registration,
                   f.aircraft_type,
                   {model_expr} AS aircraft_model,
                   f.flight,
                   f.callsign,
                   f.origin_iata,
                   f.origin_icao,
                   f.destination_iata,
                   f.destination_icao,
                   f.first_seen,
                   f.datetime_takeoff,
                   f.last_seen,
                   f.datetime_landed,
                   f.estimated_flight_hours,
                   f.cycle_count,
                   f.actual_distance_km
            FROM customer_flightradar_flights f
            JOIN customers c ON c.id = f.customer_id
            WHERE 1 = 1
              {where_sql}
            ORDER BY COALESCE(f.first_seen, f.datetime_takeoff, f.created_at) DESC
            LIMIT 250
            """,
            tuple(params),
            fetch='all',
        ) or []

        return jsonify({
            'ok': True,
            'summary': _row_to_json(summary),
            'groups': [_row_to_json(row) for row in top_groups],
            'history': [_row_to_json(row) for row in history_rows],
            'breakdowns': {
                'customers': _load_aircraft_breakdown('customer', where_sql, params),
                'aircraft_types': _load_aircraft_breakdown('aircraft_type', where_sql, params),
                'aircraft_models': _load_aircraft_breakdown('aircraft_model', where_sql, params),
                'aircraft': _load_aircraft_breakdown('aircraft', where_sql, params),
                'routes': [_row_to_json(row) for row in route_rows],
            },
            'aircraft': [_row_to_json(row) for row in aircraft_rows],
            'flights': [_row_to_json(row) for row in flight_rows],
        })
    except (TypeError, ValueError):
        return jsonify({'ok': False, 'error': 'Invalid aircraft analytics filter.'}), 400
    except Exception as exc:
        current_app.logger.exception('Unable to load aircraft analytics')
        return jsonify({'ok': False, 'error': f'Unable to load aircraft analytics: {exc}'}), 500


@flightradar_bp.route('/api/auth-test', methods=['POST'])
@login_required
def auth_test():
    try:
        usage = _build_client().get_usage(period='24h')
        return jsonify({'ok': True, 'usage': usage})
    except FlightradarError as exc:
        return _flightradar_error_response(exc)
    except Exception as exc:
        current_app.logger.exception('Unexpected Flightradar auth test error')
        return jsonify({'ok': False, 'error': f'Unexpected Flightradar error: {exc}'}), 500


@flightradar_bp.route('/api/activity-sync/history', methods=['GET'])
@login_required
def activity_sync_history():
    if not (
        current_user.is_administrator()
        or current_user.can(Permission.EDIT_CUSTOMERS)
    ):
        return jsonify({'ok': False, 'error': 'Administrator or customer edit permission required.'}), 403

    try:
        limit = max(1, min(int(request.args.get('limit') or 20), 100))
        customer_id = (request.args.get('customer_id') or '').strip()
        clauses = ["r.sync_type = 'activity'"]
        params = []
        if customer_id:
            clauses.append('r.customer_id = ?')
            params.append(int(customer_id))

        rows = db_execute(
            f"""
            SELECT r.id,
                   r.sync_type,
                   r.source,
                   r.mode,
                   r.ok,
                   r.started_at,
                   r.completed_at,
                   r.duration_seconds,
                   r.customer_id,
                   c.name AS customer_name,
                   r.lookback_hours,
                   r.chunk_hours,
                   r.max_requests,
                   r.request_count,
                   r.link_count,
                   r.processed_link_count,
                   r.flight_count,
                   r.logged_flight_count,
                   r.refreshed_aircraft_count,
                   r.error_count,
                   r.stopped_reason,
                   r.error_message,
                   COALESCE(r.result_payload->'errors', '[]'::jsonb) AS errors
            FROM flightradar_sync_runs r
            LEFT JOIN customers c ON c.id = r.customer_id
            WHERE {' AND '.join(clauses)}
            ORDER BY r.completed_at DESC
            LIMIT ?
            """,
            tuple(params + [limit]),
            fetch='all',
        ) or []
        return jsonify({'ok': True, 'runs': [_row_to_json(row) for row in rows]})
    except (TypeError, ValueError):
        return jsonify({'ok': False, 'error': 'Invalid sync history filter.'}), 400
    except Exception as exc:
        current_app.logger.exception('Unable to load Flightradar sync history')
        return jsonify({'ok': False, 'error': f'Unable to load Flightradar sync history: {exc}'}), 500


@flightradar_bp.route('/api/activity-sync', methods=['POST'])
@login_required
def activity_sync():
    if not (
        current_user.is_administrator()
        or current_user.can(Permission.EDIT_CUSTOMERS)
    ):
        return jsonify({'ok': False, 'error': 'Administrator or customer edit permission required.'}), 403

    try:
        payload = request.get_json(silent=True) or {}
        customer_id = payload.get('customer_id') or None
        use_batches = payload.get('batch') is not False
        started_at = datetime.now(timezone.utc)
        if use_batches:
            result = sync_flightradar_activity_incremental(
                lookback_hours=int(payload.get('window_hours') or 48),
                limit=int(payload.get('limit') or os.getenv('FLIGHTRADAR_ACTIVITY_SYNC_LIMIT', '20000')),
                customer_id=int(customer_id) if customer_id else None,
                chunk_hours=int(payload.get('chunk_hours') or os.getenv('FLIGHTRADAR_ACTIVITY_SYNC_CHUNK_HOURS', '6')),
                max_requests=int(payload.get('max_requests') or os.getenv('FLIGHTRADAR_ACTIVITY_SYNC_MAX_REQUESTS', '20')),
            )
        else:
            result = sync_flightradar_activity_window(
                window_hours=int(payload.get('window_hours') or 48),
                limit=int(payload.get('limit') or 500),
                customer_id=int(customer_id) if customer_id else None,
                chunk_hours=int(payload.get('chunk_hours')) if payload.get('chunk_hours') else None,
            )
        try:
            record_flightradar_sync_run(result, source='manual', started_at=started_at)
        except Exception as record_exc:
            current_app.logger.warning('Unable to record manual Flightradar sync run: %s', record_exc)
        status = 200 if result.get('ok') else 207
        return jsonify(result), status
    except (TypeError, ValueError):
        return jsonify({'ok': False, 'error': 'Invalid sync parameters.'}), 400
    except Exception as exc:
        current_app.logger.exception('Unexpected Flightradar activity sync error')
        return jsonify({'ok': False, 'error': f'Unexpected Flightradar sync error: {exc}'}), 500


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
        return _flightradar_error_response(exc)
    except Exception as exc:
        current_app.logger.exception('Unexpected Flightradar airline lookup error')
        return jsonify({'ok': False, 'error': f'Unexpected Flightradar error: {exc}'}), 500


@flightradar_bp.route('/api/operator-search', methods=['GET'])
@login_required
def operator_search():
    query = request.args.get('q', '')
    limit = _normalize_limit(request.args.get('limit', '25'))
    return jsonify({
        'ok': True,
        'operators': search_local_airline_operators(query, limit=min(limit, 100)),
    })


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
        return _flightradar_error_response(exc)
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
        return _flightradar_error_response(exc)
    except Exception as exc:
        current_app.logger.exception('Unexpected customer Flightradar live flight error')
        return jsonify({'ok': False, 'error': f'Unexpected Flightradar error: {exc}'}), 500


@flightradar_bp.route('/api/customers/<int:customer_id>/activity-summary', methods=['POST'])
@login_required
def customer_activity_summary(customer_id):
    if not _can_view_customer(customer_id):
        return jsonify({'ok': False, 'error': 'Customer not found or access denied.'}), 404

    try:
        request_payload = request.get_json(silent=True) or {}
        start, end = _normalize_summary_window(
            request_payload.get('flight_datetime_from') or '',
            request_payload.get('flight_datetime_to') or '',
        )
        limit = _normalize_limit(str(request_payload.get('limit') or '500'))
        links = _get_customer_flightradar_links(customer_id)
        if not links:
            return jsonify({
                'ok': True,
                'window': {
                    'from': _format_fr24_datetime(start),
                    'to': _format_fr24_datetime(end),
                    'max_days': 2,
                },
                'links': [],
                'flights': [],
                'summary': _summarize_flights([]),
            })

        client = _build_client()
        flights = []
        seen_keys = set()
        logged_flight_count = 0
        affected_tails = set()
        for link in links:
            mode = _normalize_match_mode(link.get('match_mode'))
            icao = link.get('airline_icao')
            payload = client.get_flight_summary_full(
                flight_datetime_from=_format_fr24_datetime(start),
                flight_datetime_to=_format_fr24_datetime(end),
                operating_as=icao if mode in ('operating_as', 'both') else None,
                painted_as=icao if mode in ('painted_as', 'both') else None,
                limit=limit,
            )
            link_flights = payload.get('data') if isinstance(payload, dict) else []
            for flight in link_flights or []:
                dedupe_key = flight.get('fr24_id') or (
                    flight.get('reg'),
                    flight.get('flight'),
                    flight.get('first_seen'),
                    flight.get('last_seen'),
                )
                if dedupe_key in seen_keys:
                    continue
                seen_keys.add(dedupe_key)
                flight['_customer_flightradar_link_id'] = link.get('id')
                flight['_customer_flightradar_match_mode'] = mode
                flights.append(flight)
                if _upsert_customer_flight(customer_id, link.get('id'), flight):
                    logged_flight_count += 1
                    registration = str(flight.get('reg') or '').strip().upper()
                    if registration:
                        affected_tails.add(registration)

        refreshed_aircraft_count = 0
        for registration in affected_tails:
            if _refresh_aircraft_utilization(customer_id, registration):
                refreshed_aircraft_count += 1

        return jsonify({
            'ok': True,
            'window': {
                'from': _format_fr24_datetime(start),
                'to': _format_fr24_datetime(end),
                'max_days': 2,
            },
            'links': links,
            'logged_flight_count': logged_flight_count,
            'refreshed_aircraft_count': refreshed_aircraft_count,
            'flights': flights,
            'summary': _summarize_flights(flights),
        })
    except ValueError as exc:
        return jsonify({'ok': False, 'error': str(exc)}), 400
    except FlightradarError as exc:
        return _flightradar_error_response(exc)
    except Exception as exc:
        current_app.logger.exception('Unexpected customer Flightradar activity summary error')
        return jsonify({'ok': False, 'error': f'Unexpected Flightradar error: {exc}'}), 500
