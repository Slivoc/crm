import io
import os
from flask import Blueprint, flash, render_template, request, send_file, url_for

from integrations.partsbase_client import PartsBaseClient, PartsBaseConfig, PartsBaseError

partsbase_bp = Blueprint('partsbase', __name__, url_prefix='/partsbase')


def _build_client() -> PartsBaseClient:
    config = PartsBaseConfig(
        auth_url=os.getenv('PARTSBASE_AUTH_URL', 'https://auth.partsbase.com/connect/token'),
        api_base_url=os.getenv('PARTSBASE_API_BASE_URL', 'https://apiservices.partsbase.com'),
        client_id=os.getenv('PARTSBASE_CLIENT_ID', 'MGCAAPI'),
        client_secret=os.getenv('PARTSBASE_CLIENT_SECRET', ''),
        username=os.getenv('PARTSBASE_USERNAME', ''),
        password=os.getenv('PARTSBASE_PASSWORD', ''),
        scope=os.getenv('PARTSBASE_SCOPE', 'api openid'),
        grant_type=os.getenv('PARTSBASE_GRANT_TYPE', 'password'),
        timeout=int(os.getenv('PARTSBASE_TIMEOUT_SECONDS', '60')),
    )

    missing = []
    if not config.client_secret:
        missing.append('PARTSBASE_CLIENT_SECRET')
    if not config.username:
        missing.append('PARTSBASE_USERNAME')
    if not config.password:
        missing.append('PARTSBASE_PASSWORD')

    if missing:
        raise PartsBaseError(f"Missing PartsBase environment variables: {', '.join(missing)}")

    return PartsBaseClient(config)


@partsbase_bp.route('/', methods=['GET', 'POST'])
def partsbase_home():
    breadcrumbs = [
        ('Home', url_for('index')),
        ('PartsBase Test Console', None),
    ]

    context = {
        'breadcrumbs': breadcrumbs,
        'submitted_parts': '',
        'last_submit_response': None,
        'status_request_id': '',
        'status_response': None,
        'result_request_id': '',
        'show_download_hint': False,
        'token_test_response': None,
    }

    if request.method != 'POST':
        return render_template('partsbase/index.html', **context)

    action = request.form.get('action', '').strip()

    try:
        client = _build_client()

        if action == 'token_test':
            token = client.get_access_token()
            context['token_test_response'] = {
                'success': True,
                'token_length': len(token),
                'token_preview': f"{token[:12]}..." if len(token) > 12 else token,
            }
            flash(f'✅ Token request succeeded. Token length: {len(token)}', 'success')

        elif action == 'batch_search':
            submitted_parts = request.form.get('parts', '').strip()
            context['submitted_parts'] = submitted_parts
            parts = [line.strip() for line in submitted_parts.splitlines()]
            zip_payload = PartsBaseClient.create_test_search_zip(parts)
            response = client.submit_inventory_availability_zip(zip_payload)
            context['last_submit_response'] = response
            flash('✅ Batch search file uploaded to PartsBase.', 'success')

        elif action == 'status_check':
            request_id = request.form.get('status_request_id', '').strip()
            context['status_request_id'] = request_id
            if not request_id:
                raise PartsBaseError('Request ID is required for status checks.')
            response = client.get_inventory_availability_status(request_id)
            context['status_response'] = response
            flash('✅ Status request completed.', 'success')

        elif action == 'download_result':
            request_id = request.form.get('result_request_id', '').strip()
            context['result_request_id'] = request_id
            if not request_id:
                raise PartsBaseError('Request ID is required to download results.')

            content, content_type = client.get_inventory_availability_result(request_id)
            extension = 'zip' if 'zip' in (content_type or '').lower() else 'bin'
            filename = f'partsbase-result-{request_id}.{extension}'
            return send_file(
                io.BytesIO(content),
                mimetype=content_type,
                as_attachment=True,
                download_name=filename,
            )

        else:
            flash('Choose an action to run.', 'warning')

    except PartsBaseError as exc:
        flash(f'PartsBase error: {exc}', 'danger')
    except ValueError as exc:
        flash(str(exc), 'danger')
    except Exception as exc:
        flash(f'Unexpected error: {exc}', 'danger')

    return render_template('partsbase/index.html', **context)
