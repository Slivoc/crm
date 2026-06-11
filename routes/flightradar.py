import os

from flask import Blueprint, current_app, jsonify, render_template
from flask_login import login_required

from integrations.flightradar_client import FlightradarClient, FlightradarConfig, FlightradarError


flightradar_bp = Blueprint('flightradar', __name__, url_prefix='/flightradar')


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
