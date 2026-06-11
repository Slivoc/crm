import os
import re

from flask import Blueprint, current_app, jsonify, render_template, request
from flask_login import login_required

from integrations.flightradar_client import FlightradarClient, FlightradarConfig, FlightradarError


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
