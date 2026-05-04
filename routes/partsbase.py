import io
import os
from flask import Blueprint, current_app, flash, render_template, request, send_file, url_for

from integrations.partsbase_client import PartsBaseClient, PartsBaseConfig, PartsBaseError

partsbase_bp = Blueprint('partsbase', __name__, url_prefix='/partsbase')


def _find_request_id(payload):
    if isinstance(payload, dict):
        for key in ('requestId', 'requestID', 'RequestId', 'RequestID', 'id', 'Id', 'ID', 'guid', 'Guid'):
            value = payload.get(key)
            if value:
                return str(value)
        for value in payload.values():
            nested = _find_request_id(value)
            if nested:
                return nested
    if isinstance(payload, list):
        for item in payload:
            nested = _find_request_id(item)
            if nested:
                return nested
    return ''


def _partsbase_error_hint(message: str) -> str:
    msg = (message or '').lower()
    if "schema file wasn't found" in msg:
        return (
            "Upload format issue: this endpoint expects a ZIP with a manifest file "
            "(typically `manifest.xml`) plus a data file."
        )
    if "data file wasn't found" in msg:
        return (
            "Manifest issue: the manifest file is present but it does not point to "
            "a valid data file in the same ZIP."
        )
    if "there must be only 2 files in archive" in msg:
        return "ZIP shape issue: include exactly 2 files (manifest + data)."
    if "please attach zip archive containing two files" in msg:
        return "Upload transport issue: send multipart form-data with one ZIP file field."
    if '"code": 14' in msg or 'schema or data file has invalid data' in msg:
        return (
            "PartsBase accepted the ZIP but rejected row-level content during processing. "
            "Use the request ID and ask PartsBase Support for the processing error log."
        )
    return ''


def _coerce_marketplace_quantity(value) -> int:
    try:
        quantity = int(float(value))
    except (TypeError, ValueError):
        quantity = 0
    if quantity > 999999:
        return 999999
    return quantity if quantity > 0 else -1


def _coerce_marketplace_price(value):
    try:
        price = round(float(value), 2)
    except (TypeError, ValueError):
        return ''
    return price if price >= 0 else ''


def _load_marketplace_parts(reference_lines):
    from routes.marketplace import _MASTER_LIST_TEST_REFERENCE_MAP, _fetch_marketplace_parts_by_references, get_parts_for_export

    references = [line.strip() for line in reference_lines if line and line.strip()]
    if references:
        return references, _fetch_marketplace_parts_by_references(references)

    with current_app.test_request_context(
        '/marketplace/get-parts-for-export',
        method='POST',
        json={
            'stock_filter': 'stock_only',
            'category_filter': 'all',
            'pricing_only': True,
            'max_results': 10,
            'source_mode': 'filters',
        },
    ):
        response = get_parts_for_export()
    response_obj = response[0] if isinstance(response, tuple) else response
    payload = response_obj.get_json() if response_obj is not None else {}
    parts = payload.get('parts') if isinstance(payload, dict) else []
    matched_rows = [
        {
            'requested_reference': str(part.get('part_number') or part.get('base_part_number') or '').strip(),
            'crm_part': {
                'base_part_number': part.get('base_part_number'),
                'part_number': part.get('part_number') or part.get('base_part_number'),
                'manufacturer': part.get('manufacturer'),
                'mkp_description': part.get('mkp_description'),
                'mkp_name': part.get('mkp_name'),
                'estimated_price_gbp': part.get('estimated_price'),
                'stock_qty': part.get('stock_qty'),
                'price_source': part.get('price_source'),
            },
        }
        for part in (parts or [])
        if str(part.get('part_number') or part.get('base_part_number') or '').strip()
    ]
    references = [row['requested_reference'] for row in matched_rows]
    if not references:
        references = list(_MASTER_LIST_TEST_REFERENCE_MAP.keys())
        return references, _fetch_marketplace_parts_by_references(references)
    return references, matched_rows


def _build_marketplace_upload_rows(matched_rows, *, condition_code, uom, traceability, trace_to):
    upload_rows = []
    preview_rows = []
    skipped = []

    for item in matched_rows:
        requested_reference = str(item.get('requested_reference') or '').strip()
        crm_part = item.get('crm_part') or {}
        if not crm_part:
            skipped.append({'reference': requested_reference, 'reason': 'CRM part not found in marketplace source'})
            continue

        part_number = str(crm_part.get('part_number') or crm_part.get('base_part_number') or '').strip()
        if not part_number:
            skipped.append({'reference': requested_reference, 'reason': 'CRM part is missing a part number'})
            continue

        price_value = crm_part.get('estimated_price_gbp')
        quantity_value = _coerce_marketplace_quantity(crm_part.get('stock_qty'))
        upload_row = {
            'action_code': 'A',
            'part_number': part_number,
            'description': str(
                crm_part.get('mkp_description')
                or crm_part.get('mkp_name')
                or crm_part.get('part_number')
                or crm_part.get('base_part_number')
                or requested_reference
            ).strip(),
            'alternate_part_number': str(crm_part.get('base_part_number') or '').strip()
            if str(crm_part.get('base_part_number') or '').strip() != part_number
            else '',
            'condition_code': condition_code,
            'quantity': quantity_value,
            'uom': uom,
            'manufacturer': str(crm_part.get('manufacturer') or '').strip(),
            'unit_price': _coerce_marketplace_price(price_value),
            'aircraft_type': '',
            'engine_type': '',
            'serial_number': '',
            'traceability': traceability,
            'trace_to': trace_to,
            'image_url': '',
            'documentation_url': '',
            'documentation_caption': '',
        }
        upload_rows.append(upload_row)
        preview_rows.append({
            'requested_reference': requested_reference,
            'part_number': upload_row['part_number'],
            'alternate_part_number': upload_row['alternate_part_number'],
            'description': upload_row['description'],
            'manufacturer': upload_row['manufacturer'],
            'quantity': upload_row['quantity'],
            'unit_price': upload_row['unit_price'],
            'condition_code': upload_row['condition_code'],
            'uom': upload_row['uom'],
            'price_source': crm_part.get('price_source'),
            'stock_qty': crm_part.get('stock_qty'),
            'estimated_price_gbp': crm_part.get('estimated_price_gbp'),
        })

    return upload_rows, preview_rows, skipped


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
        'last_submit_request_id': '',
        'status_request_id': '',
        'status_response': None,
        'result_request_id': '',
        'show_download_hint': False,
        'token_test_response': None,
        'partsbase_config_preview': {
            'auth_url': os.getenv('PARTSBASE_AUTH_URL', 'https://auth.partsbase.com/connect/token'),
            'api_base_url': os.getenv('PARTSBASE_API_BASE_URL', 'https://apiservices.partsbase.com'),
            'client_id': os.getenv('PARTSBASE_CLIENT_ID', 'MGCAAPI'),
            'scope': os.getenv('PARTSBASE_SCOPE', 'api openid'),
            'grant_type': os.getenv('PARTSBASE_GRANT_TYPE', 'password'),
            'username': os.getenv('PARTSBASE_USERNAME', ''),
            'has_client_secret': bool(os.getenv('PARTSBASE_CLIENT_SECRET', '')),
            'has_password': bool(os.getenv('PARTSBASE_PASSWORD', '')),
        },
        'marketplace_reference_input': '',
        'marketplace_defaults': {
            'condition_code': 'AR',
            'uom': 'EA',
            'traceability': 'C of C',
            'trace_to': '',
        },
        'marketplace_preview_rows': [],
        'marketplace_preview_skipped': [],
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
            flash(f'Token request succeeded. Token length: {len(token)}', 'success')

        elif action == 'batch_search':
            submitted_parts = request.form.get('parts', '').strip()
            context['submitted_parts'] = submitted_parts
            parts = [line.strip() for line in submitted_parts.splitlines()]
            zip_payload = PartsBaseClient.create_test_search_zip(parts)
            response = client.submit_inventory_availability_zip(zip_payload)
            context['last_submit_response'] = response
            request_id = _find_request_id(response)
            if request_id:
                context['last_submit_request_id'] = request_id
                context['status_request_id'] = request_id
                context['result_request_id'] = request_id
                flash(f'Batch search file uploaded to PartsBase. Request ID: {request_id}', 'success')
            else:
                flash('Batch search file uploaded to PartsBase, but no request ID was detected in the response.', 'warning')

        elif action == 'status_check':
            request_id = request.form.get('status_request_id', '').strip()
            context['status_request_id'] = request_id
            context['result_request_id'] = request_id
            if not request_id:
                raise PartsBaseError('Request ID is required for status checks.')
            response = client.get_inventory_availability_status(request_id)
            context['status_response'] = response
            flash(f'Status request completed for {request_id}.', 'success')

        elif action in ('marketplace_preview', 'marketplace_submit'):
            submitted_refs = request.form.get('marketplace_references', '').strip()
            condition_code = (request.form.get('marketplace_condition_code', '') or 'AR').strip() or 'AR'
            uom = (request.form.get('marketplace_uom', '') or 'EA').strip() or 'EA'
            traceability = request.form.get('marketplace_traceability', '').strip()
            trace_to = request.form.get('marketplace_trace_to', '').strip()
            context['marketplace_reference_input'] = submitted_refs
            context['marketplace_defaults'] = {
                'condition_code': condition_code,
                'uom': uom,
                'traceability': traceability,
                'trace_to': trace_to,
            }

            references, matched_rows = _load_marketplace_parts(submitted_refs.splitlines())
            upload_rows, preview_rows, skipped_rows = _build_marketplace_upload_rows(
                matched_rows,
                condition_code=condition_code,
                uom=uom,
                traceability=traceability,
                trace_to=trace_to,
            )
            context['marketplace_reference_input'] = '\n'.join(references)
            context['marketplace_preview_rows'] = preview_rows
            context['marketplace_preview_skipped'] = skipped_rows

            if action == 'marketplace_preview':
                if preview_rows:
                    flash(f'Loaded {len(preview_rows)} marketplace-derived row(s) for PartsBase preview.', 'success')
                else:
                    flash('Marketplace source did not produce any usable PartsBase rows.', 'warning')
                if skipped_rows:
                    flash(f'Skipped {len(skipped_rows)} reference(s) that could not be mapped cleanly.', 'warning')
            else:
                zip_payload = PartsBaseClient.create_inventory_upload_zip(upload_rows)
                response = client.submit_inventory_availability_zip(zip_payload, filename='partsbase-marketplace-test.zip')
                context['last_submit_response'] = response
                request_id = _find_request_id(response)
                if request_id:
                    context['last_submit_request_id'] = request_id
                    context['status_request_id'] = request_id
                    context['result_request_id'] = request_id
                    flash(
                        f'Marketplace-derived inventory upload submitted to PartsBase. Request ID: {request_id}',
                        'success',
                    )
                else:
                    flash(
                        'Marketplace-derived inventory upload was submitted, but no request ID was detected in the response.',
                        'warning',
                    )
                if skipped_rows:
                    flash(f'Skipped {len(skipped_rows)} reference(s) that could not be mapped cleanly.', 'warning')

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
        message = str(exc)
        hint = _partsbase_error_hint(message)
        flash(f'PartsBase error: {message}', 'danger')
        if hint:
            flash(hint, 'warning')
    except ValueError as exc:
        flash(str(exc), 'danger')
    except Exception as exc:
        flash(f'Unexpected error: {exc}', 'danger')

    return render_template('partsbase/index.html', **context)
