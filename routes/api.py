# In your Flask application, create a new blueprint for API routes
from collections import defaultdict
from flask import Blueprint, request, jsonify, current_app, url_for, session
import base64
import os
import requests
import extract_msg
from datetime import datetime
from db import db_cursor, execute as db_execute
from models import Permission, delete_customer_tag, get_contacts_by_customer, get_customer_statuses, insert_customer_tag, filter_tags_by_search, get_all_tags, update_customer_apollo_id, get_tags_by_customer_id, get_email_logs, get_customer_tags, get_customer_apollo_id, get_excess_stock_list_by_id, get_supplier_by_email, save_email_log, get_email_signature_by_id, get_template_by_id, get_contact_by_id, get_customer_by_id, get_call_list_contact_ids
from routes.emails import (
    allowed_file,
    build_email_from_template,
    send_email_from_template,
    send_graph_email,
    build_graph_inline_attachments,
)
from flask_login import current_user
from routes.email_signatures import get_user_default_signature
import mimetypes
import uuid
from hubspot_helpers import get_or_create_hubspot_contact, get_or_create_hubspot_company, log_email_to_hubspot

api_bp = Blueprint('api', __name__)

def _using_postgres():
    return bool(os.getenv('DATABASE_URL', '').startswith(('postgres://', 'postgresql://')))


def _prepare_query(query):
    return query.replace('?', '%s') if _using_postgres() else query


def _execute_with_cursor(cur, query, params=None):
    cur.execute(_prepare_query(query), params or [])
    return cur


def _get_inserted_id(row, cur):
    if row is None:
        return getattr(cur, 'lastrowid', None)
    if isinstance(row, dict):
        return row.get('id')
    try:
        return row[0]
    except Exception:
        return getattr(cur, 'lastrowid', None)


def _get_default_signature(user_id=None):
    signature = None
    if user_id:
        signature = get_user_default_signature(user_id)
    if not signature and current_user and getattr(current_user, "is_authenticated", False):
        signature = get_user_default_signature(current_user.id)
    if signature:
        return signature
    return get_email_signature_by_id(1)


def _log_email_communication(contact, customer, customer_id, subject):
    """Insert an email communication row for contact timeline/metrics."""
    if not contact:
        return

    resolved_customer_id = customer_id or contact.get('customer_id')
    salesperson_id = None
    if customer:
        salesperson_id = customer.get('salesperson_id')
    if not salesperson_id and current_user and getattr(current_user, "is_authenticated", False):
        salesperson_id = current_user.id

    notes = f"Email sent: {subject}" if subject else "Email sent"
    db_execute(
        '''
        INSERT INTO contact_communications
            (date, contact_id, customer_id, salesperson_id, communication_type, notes)
        VALUES (CURRENT_TIMESTAMP, ?, ?, ?, ?, ?)
        ''',
        (contact.get('id'), resolved_customer_id, salesperson_id, 'email', notes),
        commit=True
    )

from functools import wraps

# Decorator for API Key validation
# At the top of your api.py, modify the decorator
def require_api_key(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        api_key = request.headers.get('x-api-key')
        print(f"Received API key: {api_key}")  # Debug print
        if not api_key or api_key != current_app.config.get('API_KEY'):
            print("API key validation failed")  # Debug print
            return jsonify({'error': 'Unauthorized'}), 401
        return f(*args, **kwargs)
    return decorated


@api_bp.route('/upload_email/excess_list/<int:entity_id>', methods=['POST'])
@require_api_key
def api_upload_email(entity_id):
    print(f"Received API request for entity_id: {entity_id}")
    print(f"Files in request: {request.files.keys()}")

    # Check if an email file is present in the request
    if 'email_file' not in request.files:
        return jsonify({'error': 'No email file part'}), 400

    file = request.files['email_file']
    if file.filename == '':
        return jsonify({'error': 'No selected file'}), 400

    try:
        filename = secure_filename(file.filename)
        file_path = os.path.join(current_app.config['UPLOAD_FOLDER'], filename)
        print(f"\nProcessing email file: {filename}")

        # Read and decode base64 content
        file_content = file.read()
        decoded_content = base64.b64decode(file_content)
        with open(file_path, 'wb') as f:
            f.write(decoded_content)
        print(f"Successfully saved email file to: {file_path}")

        # Process the .msg file
        msg = extract_msg.Message(file_path)
        # Use HTML body if available, otherwise fallback to plain text
        email_content = msg.htmlBody if msg.htmlBody else msg.body

        # Save email + attachments inside a single transaction
        with db_cursor(commit=True) as cur:
            _execute_with_cursor(cur, 'UPDATE excess_stock_lists SET email = ? WHERE id = ?', (email_content, entity_id))

            for attachment_name, attachment_file in request.files.items():
                if not attachment_name.startswith('attachment'):
                    continue

                attachment_filename = secure_filename(attachment_file.filename)
                print(f"\nProcessing attachment: {attachment_filename}")

                if not attachment_filename or attachment_filename.lower().endswith('.png'):
                    print(f"Skipping attachment: {attachment_filename}")
                    continue

                attachment_path = os.path.join(current_app.config['UPLOAD_FOLDER'], attachment_filename)
                print(f"Saving to: {attachment_path}")

                try:
                    attachment_content = attachment_file.read()
                    decoded_attachment = base64.b64decode(attachment_content)
                    print(f"Successfully decoded attachment. Size: {len(decoded_attachment)} bytes")

                    with open(attachment_path, 'wb') as f:
                        f.write(decoded_attachment)
                    print(f"Successfully wrote file to disk")

                    _execute_with_cursor(
                        cur,
                        '''
                        INSERT INTO files (filename, filepath, upload_date)
                        VALUES (?, ?, ?)
                        RETURNING id
                        ''',
                        (attachment_filename, attachment_path, datetime.now())
                    )
                    file_row = cur.fetchone()
                    file_id = _get_inserted_id(file_row, cur)
                    print(f"Inserted with file_id: {file_id}")

                    if file_id:
                        _execute_with_cursor(
                            cur,
                            '''
                            INSERT INTO excess_stock_files (excess_stock_list_id, file_id)
                            VALUES (?, ?)
                            ''',
                            (entity_id, file_id)
                        )

                    print(f"Successfully processed attachment: {attachment_filename}")
                except Exception as e:
                    print(f"Error processing attachment {attachment_filename}: {str(e)}")
                    continue

        return jsonify({'message': 'Email and attachments uploaded successfully!'}), 200

    except Exception as e:
        print(f"Fatal error in upload process: {str(e)}")
        return jsonify({'error': f'Error processing upload: {str(e)}'}), 500

@api_bp.route('/supplier/lookup', methods=['GET'])
@require_api_key
def lookup_supplier():
    email = request.args.get('email')
    if not email:
        return '', 404

    supplier = get_supplier_by_email(email)
    if supplier:
        return str(supplier[0])  # Assuming the ID is the first column
    return '', 404


@api_bp.route('/email-templates')
def get_email_templates():
    """Return all available email templates"""
    print("Email templates endpoint called")  # Basic logging
    try:
        templates = get_all_templates()
        print(f"Retrieved {len(templates)} templates")  # Log number of templates

        # Transform templates and add logging
        formatted_templates = []
        for template in templates:
            try:
                formatted_template = {
                    'id': template['id'],
                    'name': template['name'],
                    'description': template['description']
                }
                formatted_templates.append(formatted_template)
            except KeyError as ke:
                print(f"Error processing template: missing key {ke}")
                print(f"Template data: {template}")
                continue

        print(f"Returning {len(formatted_templates)} formatted templates")
        return jsonify(formatted_templates)

    except Exception as e:
        print(f"Error in get_email_templates: {str(e)}")
        return jsonify({'error': str(e)}), 500


def get_all_templates():
    templates = db_execute(
        '''
        SELECT *
        FROM email_templates
        ORDER BY updated_at DESC
        ''',
        fetch='all'
    ) or []

    tags_rows = db_execute(
        '''
        SELECT tit.template_id, it.tag
        FROM template_industry_tags tit
        JOIN industry_tags it ON tit.industry_tag_id = it.id
        ''',
        fetch='all'
    ) or []

    tag_map = defaultdict(list)
    for row in tags_rows:
        row_dict = dict(row)
        tag_map[row_dict['template_id']].append(row_dict['tag'])

    template_list = []
    for template in templates:
        template_dict = dict(template)
        tags = tag_map.get(template_dict['id'], [])
        template_dict['industry_tags'] = ', '.join(tags)
        template_dict['tag_count'] = len(tags)
        template_list.append(template_dict)

    return template_list


@api_bp.route('/preview-email', methods=['POST'])
def preview_email():
    """Preview an email template or custom email with contact/customer data"""
    try:
        data = request.json
        template_id = data.get('template_id')
        contact_id = data.get('contact_id')
        customer_id = data.get('customer_id')

        # Check if this is a custom email request
        is_custom = data.get('is_custom', False)
        custom_subject = data.get('custom_subject', '')
        custom_body = data.get('custom_body', '')

        contact = get_contact_by_id(contact_id)
        customer = get_customer_by_id(customer_id) if customer_id else None

        if not contact:
            return jsonify({'success': False, 'error': 'Contact not found'})

        # Process either template or custom email
        if is_custom:
            # For custom email, use provided subject and body
            subject = custom_subject
            body = custom_body

            if not subject or not body:
                return jsonify({'success': False, 'error': 'Subject and body are required for custom emails'})
        else:
            # For template email, fetch and use template
            template = get_template_by_id(template_id)
            if not template:
                return jsonify({'success': False, 'error': 'Template not found'})

            subject = template.get('subject', '')
            body = template.get('body', '')

        # Replace placeholders (same for both template and custom email)
        if customer:
            customer_name = customer.get('name', '')
            subject = subject.replace('{{company_name}}', str(customer_name))
            body = body.replace('{{company_name}}', str(customer_name))
        else:
            subject = subject.replace('{{company_name}}', '')
            body = body.replace('{{company_name}}', '')

        if contact:
            contact_name = contact.get('name', '')
            contact_first_name = contact_name.split()[0] if contact_name else ''
            contact_title = contact.get('job_title', '')

            replacements = {
                '{{contact_name}}': str(contact_name),
                '{{contact_first_name}}': str(contact_first_name),
                '{{contact_title}}': str(contact_title),
                '{{sender_name}}': "Tom Palmer",
                '{{sender_title}}': "Sales Manager",
                '{{today_date}}': datetime.now().strftime('%Y-%m-%d')
            }

            for placeholder, value in replacements.items():
                subject = subject.replace(placeholder, value)
                body = body.replace(placeholder, value)

        # Convert line breaks to HTML for display
        body_html = body.replace('\n', '<br>')
        body_without_signature = body_html

        graph_user_id = data.get('graph_user_id') or getattr(current_user, "id", None)

        # Add email signature
        email_signature = _get_default_signature(graph_user_id)
        if email_signature:
            signature_html = email_signature.get('signature_html', '')
            # Convert CID references to actual image URLs for preview
            signature_html = signature_html.replace('cid:image001', url_for('emails.uploaded_file', filename='blimage001.jpg'))
            signature_html = signature_html.replace('cid:linkedin_icon', url_for('emails.uploaded_file', filename='linkedin_icon.png'))
            body_html += f"<br><br>{signature_html}"

        return jsonify({
            'success': True,
            'data': {
                'subject': subject,
                'body': body_html,
                'body_without_signature': body_without_signature
            }
        })

    except Exception as e:
        print(f"Error in preview_email: {str(e)}")  # For debugging
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@api_bp.route('/send-email', methods=['POST'])
def send_email():
    request_id = uuid.uuid4()
    print(f"Starting email request {request_id}")
    """Send an email using the template and log to HubSpot"""
    try:
        data = request.json
        graph_user_id = data.get('graph_user_id') or getattr(current_user, "id", None)
        print("Received data:", data)

        template_id = data.get('template_id')
        contact_id = data.get('contact_id')
        customer_id = data.get('customer_id')

        print(f"Template ID: {template_id}")
        print(f"Contact ID: {contact_id}")
        print(f"Customer ID: {customer_id}")

        # Validate required data
        if not contact_id:
            return jsonify({'success': False, 'error': 'Contact is required'})

        if not template_id:
            return jsonify({'success': False, 'error': 'Template is required'})

        # Get related objects
        template = get_template_by_id(template_id)
        contact = get_contact_by_id(contact_id)
        customer = get_customer_by_id(customer_id) if customer_id else None

        print(f"Template: {template}")
        print(f"Contact: {contact}")
        print(f"Customer: {customer}")

        if not template:
            return jsonify({'success': False, 'error': 'Template not found'})

        if not contact:
            return jsonify({'success': False, 'error': 'Contact not found'})

        # Process template
        subject = template['subject']
        body = template['body'].replace('\n', '<br>')
        body = f"""
        <html>
            <head>
                <style>
                    p {{ margin: 0 0 1em 0; }}
                    br {{ margin-bottom: 0.5em; }}
                </style>
            </head>
            <body>
                <div style="font-family: Arial, sans-serif; font-size: 14px; line-height: 1.6;">
                    {body}
                </div>
            </body>
        </html>
        """

        # Handle template replacements
        if customer:
            customer_name = customer.get('name', '')
            subject = subject.replace('{{company_name}}', str(customer_name))
            body = body.replace('{{company_name}}', str(customer_name))
        else:
            subject = subject.replace('{{company_name}}', '')
            body = body.replace('{{company_name}}', '')

        contact_name = contact.get('name', '')
        contact_first_name = contact_name.split()[0] if contact_name else ''
        contact_title = contact.get('job_title', '')
        contact_email = contact.get('email', '')

        replacements = {
            '{{contact_name}}': str(contact_name),
            '{{contact_first_name}}': str(contact_first_name),
            '{{contact_title}}': str(contact_title),
            '{{sender_name}}': "Tom Palmer",
            '{{sender_title}}': "Sales Manager",
            '{{today_date}}': datetime.now().strftime('%Y-%m-%d')
        }

        for placeholder, value in replacements.items():
            body = body.replace(placeholder, value)

        # Fetch and attach the email signature
        email_signature = _get_default_signature(graph_user_id)
        if email_signature:
            signature_html = email_signature['signature_html']
            body += signature_html

        attachments = build_graph_inline_attachments()

        print(f"Preparing to send email for request {request_id}")

        # Try HubSpot operations first
        hubspot_company_id = None
        hubspot_contact_id = None
        try:
            if customer:
                print(f"Creating/fetching HubSpot company for request {request_id}")
                hubspot_company_id = get_or_create_hubspot_company(customer)

            print(f"Creating/fetching HubSpot contact for request {request_id}")
            hubspot_contact_id = get_or_create_hubspot_contact(contact, customer)
        except Exception as e:
            print(f"Warning: HubSpot contact/company creation failed for request {request_id}: {str(e)}")
            # Continue with email sending even if HubSpot fails

        try:
            # Send the email
            print(f"Sending email for request {request_id}")
            result = send_graph_email(
                subject=subject,
                html_body=body.strip(),
                to_emails=[contact_email],
                attachments=attachments,
                user_id=graph_user_id,
            )
            if not result.get("success"):
                raise RuntimeError(result.get("error", "Graph send failed"))

            try:
                print(f"Logging email to database for request {request_id}")
                sent_folder = 'Sent Items'
                sender_email = (session.get('graph_last_user') or '').strip() or None
                email_data = {
                    'message_id': None,
                    'folder': sent_folder,
                    'recipient_email': contact_email,
                    'subject': subject,
                    'sent_date': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                    'direction': 'sent',
                    'sync_status': 'synced',
                    'customer_id': customer_id if customer else None,
                    'contact_id': contact_id
                }

                with db_cursor(commit=True) as cur:
                    _execute_with_cursor(
                        cur,
                        '''
                        INSERT INTO emails (
                            customer_id,
                            contact_id,
                            sender_email,
                            recipient_email,
                            subject,
                            sent_date,
                            direction,
                            sync_status,
                            message_id,
                            folder
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ''',
                        (
                            email_data.get('customer_id'),
                            email_data.get('contact_id'),
                            sender_email,
                            email_data.get('recipient_email'),
                            email_data.get('subject'),
                            email_data.get('sent_date'),
                            email_data.get('direction'),
                            email_data.get('sync_status'),
                            email_data.get('message_id'),
                            email_data.get('folder')
                        )
                    )
                print(f"Successfully logged email to database for request {request_id}")
            except Exception as db_error:
                print(f"Warning: Failed to log email to database for request {request_id}: {str(db_error)}")
                # Continue execution even if database logging fails

            try:
                _log_email_communication(contact, customer, customer_id, subject)
            except Exception as comm_error:
                print(f"Warning: Failed to log contact communication for request {request_id}: {str(comm_error)}")

            # Try to log to HubSpot if we have IDs
            if hubspot_contact_id:
                try:
                    print(f"Logging email to HubSpot for request {request_id}")
                    hubspot_activity_id = log_email_to_hubspot(
                        hubspot_contact_id,
                        hubspot_company_id,
                        subject,
                        body,
                        contact['email']
                    )
                except Exception as e:
                    print(f"Warning: Failed to log email to HubSpot for request {request_id}: {str(e)}")

            # Log successful send to database
            log_data = {
                'template_id': template_id,
                'contact_id': contact_id,
                'customer_id': customer_id if customer else None,
                'subject': subject,
                'recipient_email': contact_email,
                'status': 'sent',
                'hubspot_contact_id': hubspot_contact_id,
                'hubspot_company_id': hubspot_company_id
            }
            save_email_log(log_data)

            print(f"Completed email request {request_id} successfully")
            return jsonify({
                'success': True,
                'message': f'Email sent successfully to {contact_email}'
            })

        except Exception as e:
            error_msg = f'Graph Error: {str(e)}'
            log_data = {
                'template_id': template_id,
                'contact_id': contact_id,
                'customer_id': customer_id if customer else None,
                'subject': subject,
                'recipient_email': contact_email,
                'status': 'error',
                'error_message': error_msg,
                'hubspot_contact_id': hubspot_contact_id,
                'hubspot_company_id': hubspot_company_id
            }
            save_email_log(log_data)
            print(f"Failed to send email for request {request_id}: {error_msg}")
            return jsonify({'success': False, 'error': error_msg})

    except Exception as e:
        error_msg = f'Unexpected error: {str(e)}'
        try:
            log_data = {
                'template_id': template_id if 'template_id' in locals() else None,
                'contact_id': contact_id if 'contact_id' in locals() else None,
                'customer_id': customer_id if 'customer_id' in locals() else None,
                'subject': subject if 'subject' in locals() else 'Error occurred before subject creation',
                'recipient_email': contact_email if 'contact_email' in locals() else 'Unknown',
                'status': 'error',
                'error_message': error_msg
            }
            save_email_log(log_data)
        except:
            print(f"Critical error - couldn't log error for request {request_id}: {error_msg}")

        print(f"Unexpected error in request {request_id}: {error_msg}")
        return jsonify({'success': False, 'error': error_msg})


@api_bp.route('/add-email', methods=['POST'])
def add_email():
    """Add a single email entry to the SQLite database"""
    try:
        data = request.json
        required_fields = ['uid', 'folder']
        if not all(field in data for field in required_fields):
            return jsonify({'success': False, 'error': 'Missing required fields'})

        with db_cursor(commit=True) as cur:
            _execute_with_cursor(cur, 'SELECT uid FROM emails WHERE uid = ?', (data['uid'],))
            if cur.fetchone():
                return jsonify({'success': False, 'error': 'Email with this UID already exists'})

            insert_values = (
                data.get('customer_id'),
                data.get('contact_id'),
                data.get('sender_email'),
                data.get('recipient_email'),
                data.get('subject'),
                data.get('sent_date', datetime.now().strftime('%Y-%m-%d %H:%M:%S')),
                data.get('direction', 'sent'),
                data.get('sync_status', 'pending'),
                data['uid'],
                data['folder']
            )

            _execute_with_cursor(
                cur,
                '''
                INSERT INTO emails (
                    customer_id,
                    contact_id,
                    sender_email,
                    recipient_email,
                    subject,
                    sent_date,
                    direction,
                    sync_status,
                    uid,
                    folder
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                RETURNING id
                ''',
                insert_values
            )
            row = cur.fetchone()
            new_id = _get_inserted_id(row, cur)

        return jsonify({
            'success': True,
            'message': 'Email added successfully',
            'id': new_id
        })
    except Exception as e:
        return jsonify({'success': False, 'error': f'Unexpected error: {str(e)}'})

@api_bp.route('/customer-preview/<int:customer_id>/contacts', methods=['GET'])
@require_api_key
def get_customer_contacts(customer_id: int):
    """Get paginated customer contacts"""
    try:
        page = request.args.get('page', 1, type=int)
        salesperson_id = request.args.get('salesperson_id', type=int)
        per_page = 10
        offset = (page - 1) * per_page

        contacts = get_contacts_by_customer(customer_id, limit=per_page, offset=offset)
        call_list_contact_ids = set()
        if salesperson_id:
            call_list_contact_ids = get_call_list_contact_ids(salesperson_id)

        return jsonify({
            'success': True,
            'data': {
                'items': [{
                    'id': contact['id'],
                    'name': contact['name'],
                    'email': contact['email'],
                    'job_title': contact['job_title'],
                    'is_on_call_list': contact['id'] in call_list_contact_ids
                } for contact in contacts],
                'page': page,
                'per_page': per_page
            }
        })

    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@api_bp.route('/customer-preview/<int:customer_id>/emails', methods=['GET'])
@require_api_key
def get_customer_emails(customer_id: int):
    """Get paginated customer email history"""
    try:
        page = request.args.get('page', 1, type=int)
        per_page = 10
        offset = (page - 1) * per_page

        email_logs = get_email_logs(limit=per_page, offset=offset)
        customer_emails = [log for log in email_logs if log['customer_id'] == customer_id]

        return jsonify({
            'success': True,
            'data': {
                'items': [{
                    'id': email['id'],
                    'template_name': email['template_name'],
                    'contact_name': email['contact_name'],
                    'subject': email['subject'],
                    'sent_at': email['sent_at'],
                    'status': email['status']
                } for email in customer_emails],
                'page': page,
                'per_page': per_page
            }
        })

    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@api_bp.route('/customer-preview/<int:customer_id>/apollo-search', methods=['POST'])
@require_api_key
def search_apollo_for_customer(customer_id: int):
    """Search Apollo for potential company matches"""
    try:
        customer = get_customer_by_id(customer_id)
        if not customer:
            return jsonify({'error': 'Customer not found'}), 404

        # Get search term from request or use customer name
        data = request.get_json()
        search_term = data.get('q_organization_name', customer['name'])

        response = requests.post(
            "https://api.apollo.io/v1/organizations/search",
            headers={
                'X-API-KEY': current_app.config['APOLLO_API_KEY'],
                'Cache-Control': 'no-cache',
                'Content-Type': 'application/json'
            },
            json={
                'api_key': current_app.config['APOLLO_API_KEY'],
                'q_organization_name': search_term,
                'page': 1,
                'per_page': 10
            }
        )

        if response.status_code == 200:
            data = response.json()
            organizations = [{
                'id': org.get('id'),
                'name': org.get('name'),
                'website': org.get('website_url'),
                'linkedin_url': org.get('linkedin_url'),
                'domain': org.get('primary_domain'),
                'description': org.get('description'),
                'country': org.get('country'),
                'logo_url': org.get('logo_url'),
                'employee_count': org.get('estimated_num_employees')
            } for org in data.get('organizations', [])]

            return jsonify({
                'success': True,
                'data': {
                    'organizations': organizations,
                    'total_results': data.get('pagination', {}).get('total_entries', 0)
                }
            })

        return jsonify({
            'success': False,
            'error': f'Apollo API error: {response.status_code}'
        }), response.status_code

    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@api_bp.route('/customer-preview/<int:customer_id>', methods=['GET'])
@require_api_key
def get_customer_preview(customer_id: int):
    print(f"Loading preview for customer {customer_id}")

    try:
        salesperson_id = request.args.get('salesperson_id', type=int)
        customer = get_customer_by_id(customer_id)
        print(f"Customer data: {customer}")

        if not customer:
            print("Customer not found")
            return jsonify({'error': 'Customer not found'}), 404

        customer_salesperson_id = customer['salesperson_id'] if 'salesperson_id' in customer.keys() else None
        user_salesperson_id = current_user.get_salesperson_id() if getattr(current_user, 'is_authenticated', False) else None
        can_edit_status = bool(
            getattr(current_user, 'is_authenticated', False) and (
                current_user.is_administrator() or
                current_user.can(Permission.EDIT_CUSTOMERS) or
                (user_salesperson_id and user_salesperson_id == customer_salesperson_id)
            )
        )

        # Initialize variables with defaults
        contacts = []
        tags = []
        apollo_data = None
        recent_emails = []

        # Get contacts
        page = request.args.get('contacts_page', 1, type=int)
        per_page = 10
        offset = (page - 1) * per_page
        contacts = get_contacts_by_customer(customer_id, limit=per_page, offset=offset) or []
        call_list_contact_ids = set()
        if salesperson_id:
            call_list_contact_ids = get_call_list_contact_ids(salesperson_id)
            for contact in contacts:
                contact['is_on_call_list'] = contact.get('id') in call_list_contact_ids
        print("Contacts:", contacts)

        # Fetch tags using the new function
        tags = [{'name': tag} for tag in get_tags_by_customer_id(customer_id) or []]
        print("Tags:", tags)

        # Get Apollo match
        apollo_id = get_customer_apollo_id(customer_id)
        if apollo_id:
            apollo_data = {'id': apollo_id}  # Just pass the ID without API call

        # Get email history
        email_logs = get_email_logs(limit=5, offset=0) or []
        recent_emails = [log for log in email_logs if log['customer_id'] == customer_id]
        print("Emails:", recent_emails)

        response_data = {
            'success': True,
            'data': {
                'customer': {
                    'id': customer['id'],
                    'name': customer['name'],
                    'country': customer['country'] if 'country' in customer.keys() else '',
                    'apollo_id': apollo_id if apollo_id else None,
                    'status_id': customer['status_id'] if 'status_id' in customer.keys() else None,
                    'status_name': customer['customer_status'] if 'customer_status' in customer.keys() else ''
                },
                'status_options': get_customer_statuses(),
                'can_edit_status': can_edit_status,
                'contacts': {
                    'items': contacts,
                    'page': page,
                    'per_page': per_page
                },
                'tags': tags,  # Now uses get_tags_by_customer_id
                'apollo_match': apollo_data,
                'recent_emails': recent_emails
            }
        }

        print(f"Sending response: {response_data}")
        return jsonify(response_data)

    except Exception as e:
        print(f"ERROR in customer preview: {str(e)}")
        import traceback
        print(traceback.format_exc())
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@api_bp.route('/customer-preview/<int:customer_id>/apollo-search', methods=['POST'])
@require_api_key
def search_apollo(customer_id: int):
    try:
        data = request.get_json()
        search_term = data.get('q_organization_name')

        if not search_term:
            return jsonify({'error': 'Search term required'}), 400

        response = requests.post(
            "https://api.apollo.io/v1/organizations/search",
            headers={
                'X-API-KEY': current_app.config['APOLLO_API_KEY'],
                'Cache-Control': 'no-cache',
                'Content-Type': 'application/json'
            },
            json={
                'api_key': current_app.config['APOLLO_API_KEY'],
                'q_organization_name': search_term,
                'page': 1,
                'per_page': 5
            }
        )

        if response.status_code == 200:
            data = response.json()
            return jsonify({
                'success': True,
                'data': {
                    'organizations': data.get('organizations', [])
                }
            })
        else:
            return jsonify({'error': 'Apollo API error'}), 500

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@api_bp.route('/customer-preview/<int:customer_id>/apollo_match', methods=['POST'])
@require_api_key
def set_apollo_match(customer_id: int):
    try:
        data = request.get_json()
        apollo_id = data.get('apollo_id')

        if not apollo_id:
            return jsonify({'error': 'Apollo ID required'}), 400

        # Fetch organization details from Apollo
        response = requests.get(
            f"https://api.apollo.io/v1/organizations/{apollo_id}",
            headers={
                'X-API-KEY': current_app.config['APOLLO_API_KEY'],
                'Cache-Control': 'no-cache'
            },
            params={
                'api_key': current_app.config['APOLLO_API_KEY']
            }
        )

        if response.status_code == 200:
            org_data = response.json().get('organization', {})
            logo_url = org_data.get('logo_url')
            website = org_data.get('website_url')

            try:
                db_execute(
                    'UPDATE customers SET apollo_id = ?, logo_url = ?, website = ? WHERE id = ?',
                    (apollo_id, logo_url, website, customer_id),
                    commit=True
                )
                return jsonify({
                    'success': True,
                    'data': {
                        'apollo_id': apollo_id,
                        'logo_url': logo_url,
                        'website': website
                    }
                })
            except Exception as exc:
                print(f"Database error: {exc}")
                return jsonify({'error': 'Failed to update customer'}), 500
        else:
            return jsonify({'error': f'Apollo API error: {response.status_code}'}), response.status_code

    except Exception as e:
        return jsonify({'error': str(e)}), 500

@api_bp.route('/customer-preview/<int:customer_id>/tags', methods=['GET'])
@require_api_key
def get_customer_tags_api(customer_id):
    search = request.args.get('search', '').lower()
    try:
        tags = [{'name': tag, 'customer_count': 1} for tag in get_tags_by_customer_id(customer_id)]
        if search:
            tags = [tag for tag in tags if search in tag['name'].lower()]
        return jsonify({'success': True, 'data': tags})
    except Exception as e:
        print(f"Error fetching tags for customer {customer_id}: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

@api_bp.route('/tags', methods=['GET'])
@require_api_key
def get_all_tags_api():
    search = request.args.get('search', '').lower()
    print(f"Search term received: {search}")
    try:
        # Fetch all tags
        all_tags = get_all_tags()
        print(f"Fetched all tags: {all_tags}")

        # Flatten the hierarchy
        def flatten_tags(tags):
            flat = []
            for tag in tags:
                flat.append({
                    'id': tag['id'],
                    'name': tag['name'],
                    'level': tag['level'],
                    'customer_count': tag.get('customer_count', 0)
                })
                if tag['children']:
                    flat.extend(flatten_tags(tag['children']))
            return flat

        flat_tags = flatten_tags(all_tags)

        # Filter tags by search term
        filtered_tags = [
            tag for tag in flat_tags
            if search in tag['name'].lower()
        ]
        print(f"Filtered tags: {filtered_tags}")

        return jsonify({'success': True, 'data': filtered_tags})
    except Exception as e:
        print(f"Error in /tags route: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

@api_bp.route('/customer/<int:customer_id>/add-tag', methods=['POST'])
@require_api_key
def add_customer_tag(customer_id):
    try:
        data = request.json
        tag_id = data.get('tag_id')

        if not tag_id:
            return jsonify({'success': False, 'error': 'Tag ID is required'}), 400

        insert_customer_tag(customer_id, tag_id)
        return jsonify({'success': True, 'message': 'Tag added successfully'})
    except Exception as e:
        print(f"Error adding tag for customer {customer_id}: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

@api_bp.route('/customer/<int:customer_id>/remove-tag/<int:tag_id>', methods=['DELETE'])
def remove_customer_tag(customer_id, tag_id):
    try:
        delete_customer_tag(customer_id, tag_id)
        return jsonify({'success': True, 'message': 'Tag removed successfully'})
    except Exception as e:
        print(f"Error removing tag {tag_id} for customer {customer_id}: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

@api_bp.route('/customer-preview/temp/apollo-search', methods=['POST'])
@require_api_key
def search_apollo_for_new_customer():
    """Search Apollo for potential company matches for new customer"""
    try:
        data = request.get_json()
        search_term = data.get('q_organization_name')

        if not search_term:
            return jsonify({'error': 'Search term required'}), 400

        response = requests.post(
            "https://api.apollo.io/v1/organizations/search",
            headers={
                'X-API-KEY': current_app.config['APOLLO_API_KEY'],
                'Cache-Control': 'no-cache',
                'Content-Type': 'application/json'
            },
            json={
                'api_key': current_app.config['APOLLO_API_KEY'],
                'q_organization_name': search_term,
                'page': 1,
                'per_page': 10
            }
        )

        if response.status_code == 200:
            data = response.json()
            organizations = [{
                'id': org.get('id'),
                'name': org.get('name'),
                'website': org.get('website_url'),
                'linkedin_url': org.get('linkedin_url'),
                'domain': org.get('primary_domain'),
                'description': org.get('description'),
                'country': org.get('country'),
                'logo_url': org.get('logo_url'),
                'employee_count': org.get('estimated_num_employees')
            } for org in data.get('organizations', [])]

            return jsonify({
                'success': True,
                'data': {
                    'organizations': organizations,
                    'total_results': data.get('pagination', {}).get('total_entries', 0)
                }
            })

        return jsonify({
            'success': False,
            'error': f'Apollo API error: {response.status_code}'
        }), response.status_code

    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


# Get all watched industry tags for a user
@api_bp.route('/watched-industry-tags/<int:user_id>', methods=['GET'])
def get_watched_industry_tags(user_id):
    tags = db_execute(
        """
        SELECT it.id, it.tag, it.description
        FROM industry_tags it
        JOIN watched_industry_tags wit ON it.id = wit.tag_id
        WHERE wit.user_id = ?
        """,
        (user_id,),
        fetch='all'
    ) or []
    return jsonify([dict(row) for row in tags])


# Get watched tags for the current user (defaulting to user_id=1 for now)
@api_bp.route('/watched-industry-tags/current', methods=['GET'])
def get_current_watched_tags():
    # For demo purposes; replace with your auth/session mechanism.
    user_id = 1
    tags = db_execute(
        """
        SELECT it.id, it.tag, it.description
        FROM industry_tags it
        JOIN watched_industry_tags wit ON it.id = wit.tag_id
        WHERE wit.user_id = ?
        """,
        (user_id,),
        fetch='all'
    ) or []
    return jsonify([dict(row) for row in tags])


# Add a watched industry tag (POST)
@api_bp.route('/watched-industry-tags', methods=['POST'])
def add_watched_industry_tag():
    data = request.json
    # Use provided user_id if available, otherwise default to 1.
    user_id = data.get('user_id', 1)
    tag_id = data.get('tag_id')

    if not tag_id:
        return jsonify({"error": "tag_id is required"}), 400

    try:
        db_execute(
            """
            INSERT INTO watched_industry_tags (user_id, tag_id) 
            VALUES (?, ?)
            ON CONFLICT(user_id, tag_id) DO NOTHING
            """,
            (user_id, tag_id),
            commit=True
        )
        return jsonify({"message": "Industry tag added to watch list"}), 201
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# Remove a watched industry tag (DELETE)
@api_bp.route('/watched-industry-tags', methods=['DELETE'])
def remove_watched_industry_tag():
    data = request.json
    # Use provided user_id if available, otherwise default to 1.
    user_id = data.get('user_id', 1)
    tag_id = data.get('tag_id')

    if not tag_id:
        return jsonify({"error": "tag_id is required"}), 400

    db_execute(
        """
        DELETE FROM watched_industry_tags 
        WHERE user_id = ? AND tag_id = ?
        """,
        (user_id, tag_id),
        commit=True
    )

    return jsonify({"message": "Industry tag removed from watch list"}), 200


@api_bp.route('/send-custom-email', methods=['POST'])
def send_custom_email():
    """Send a custom email and log to HubSpot"""
    request_id = uuid.uuid4()
    print(f"Starting custom email request {request_id}")

    try:
        data = request.json
        graph_user_id = data.get('graph_user_id') or getattr(current_user, "id", None)
        print("Received data:", data)

        subject = data.get('subject')
        body = data.get('body')
        contact_id = data.get('contact_id')
        customer_id = data.get('customer_id')

        print(f"Subject: {subject}")
        print(f"Contact ID: {contact_id}")
        print(f"Customer ID: {customer_id}")

        # Validate required data
        if not contact_id:
            return jsonify({'success': False, 'error': 'Contact is required'})

        if not subject or not body:
            return jsonify({'success': False, 'error': 'Subject and body are required'})

        # Get related objects
        contact = get_contact_by_id(contact_id)
        customer = get_customer_by_id(customer_id) if customer_id else None

        print(f"Contact: {contact}")
        print(f"Customer: {customer}")

        if not contact:
            return jsonify({'success': False, 'error': 'Contact not found'})

        # Process HTML body
        # If the body doesn't look like HTML, wrap it in simple HTML
        if not body.strip().startswith('<'):
            body = body.replace('\n', '<br>')
            body = f"""
            <html>
                <head>
                    <style>
                        p {{ margin: 0 0 1em 0; }}
                        br {{ margin-bottom: 0.5em; }}
                    </style>
                </head>
                <body>
                    <div style="font-family: Arial, sans-serif; font-size: 14px; line-height: 1.6;">
                        {body}
                    </div>
                </body>
            </html>
            """

        # Handle template replacements
        if customer:
            customer_name = customer.get('name', '')
            subject = subject.replace('{{company_name}}', str(customer_name))
            body = body.replace('{{company_name}}', str(customer_name))
        else:
            subject = subject.replace('{{company_name}}', '')
            body = body.replace('{{company_name}}', '')

        contact_name = contact.get('name', '')
        contact_first_name = contact_name.split()[0] if contact_name else ''
        contact_title = contact.get('job_title', '')
        contact_email = contact.get('email', '')

        replacements = {
            '{{contact_name}}': str(contact_name),
            '{{contact_first_name}}': str(contact_first_name),
            '{{contact_title}}': str(contact_title),
            '{{sender_name}}': "Tom Palmer",
            '{{sender_title}}': "Sales Manager",
            '{{today_date}}': datetime.now().strftime('%Y-%m-%d')
        }

        for placeholder, value in replacements.items():
            subject = subject.replace(placeholder, value)
            body = body.replace(placeholder, value)

        # Add signature if needed
        email_signature = _get_default_signature(graph_user_id)
        if email_signature:
            signature_html = email_signature.get('signature_html', '')
            body += signature_html

        attachments = build_graph_inline_attachments()

        print(f"Preparing to send email for request {request_id}")

        # Try HubSpot operations first
        hubspot_company_id = None
        hubspot_contact_id = None
        try:
            if customer:
                print(f"Creating/fetching HubSpot company for request {request_id}")
                hubspot_company_id = get_or_create_hubspot_company(customer)

            print(f"Creating/fetching HubSpot contact for request {request_id}")
            hubspot_contact_id = get_or_create_hubspot_contact(contact, customer)
        except Exception as e:
            print(f"Warning: HubSpot contact/company creation failed for request {request_id}: {str(e)}")
            # Continue with email sending even if HubSpot fails

        try:
            # Send the email
            print(f"Sending email for request {request_id}")
            result = send_graph_email(
                subject=subject,
                html_body=body.strip(),
                to_emails=[contact_email],
                attachments=attachments,
                user_id=graph_user_id,
            )
            if not result.get("success"):
                raise RuntimeError(result.get("error", "Graph send failed"))

            try:
                print(f"Logging email to database for request {request_id}")
                sent_folder = 'Sent Items'
                sender_email = (session.get('graph_last_user') or '').strip() or None
                email_data = {
                    'message_id': None,
                    'folder': sent_folder,
                    'recipient_email': contact_email,
                    'subject': subject,
                    'sent_date': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                    'direction': 'sent',
                    'sync_status': 'synced',
                    'customer_id': customer_id if customer else None,
                    'contact_id': contact_id
                }

                with db_cursor(commit=True) as cur:
                    _execute_with_cursor(
                        cur,
                        '''
                        INSERT INTO emails (
                            customer_id,
                            contact_id,
                            sender_email,
                            recipient_email,
                            subject,
                            sent_date,
                            direction,
                            sync_status,
                            message_id,
                            folder
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ''',
                        (
                            email_data.get('customer_id'),
                            email_data.get('contact_id'),
                            sender_email,
                            email_data.get('recipient_email'),
                            email_data.get('subject'),
                            email_data.get('sent_date'),
                            email_data.get('direction'),
                            email_data.get('sync_status'),
                            email_data.get('message_id'),
                            email_data.get('folder')
                        )
                    )
                print(f"Successfully logged email to database for request {request_id}")

            except Exception as db_error:
                print(f"Warning: Failed to log email to database for request {request_id}: {str(db_error)}")
                # Continue execution even if database logging fails

            try:
                _log_email_communication(contact, customer, customer_id, subject)
            except Exception as comm_error:
                print(f"Warning: Failed to log contact communication for request {request_id}: {str(comm_error)}")

            # Try to log to HubSpot if we have IDs
            if hubspot_contact_id:
                try:
                    print(f"Logging email to HubSpot for request {request_id}")
                    hubspot_activity_id = log_email_to_hubspot(
                        hubspot_contact_id,
                        hubspot_company_id,
                        subject,
                        body,
                        contact['email']
                    )
                except Exception as e:
                    print(f"Warning: Failed to log email to HubSpot for request {request_id}: {str(e)}")

            # Log successful send to database
            log_data = {
                'template_id': None,  # Custom email has no template
                'contact_id': contact_id,
                'customer_id': customer_id if customer else None,
                'subject': subject,
                'recipient_email': contact_email,
                'status': 'sent',
                'hubspot_contact_id': hubspot_contact_id,
                'hubspot_company_id': hubspot_company_id,
                'custom_email': True  # Mark as custom email
            }
            save_email_log(log_data)

            print(f"Completed email request {request_id} successfully")
            return jsonify({
                'success': True,
                'message': f'Email sent successfully to {contact_email}'
            })

        except Exception as e:
            error_msg = f'Graph Error: {str(e)}'
            log_data = {
                'template_id': None,
                'contact_id': contact_id,
                'customer_id': customer_id if customer else None,
                'subject': subject,
                'recipient_email': contact_email,
                'status': 'error',
                'error_message': error_msg,
                'hubspot_contact_id': hubspot_contact_id,
                'hubspot_company_id': hubspot_company_id,
                'custom_email': True
            }
            save_email_log(log_data)
            print(f"Failed to send email for request {request_id}: {error_msg}")
            return jsonify({'success': False, 'error': error_msg})

    except Exception as e:
        error_msg = f'Unexpected error: {str(e)}'
        try:
            log_data = {
                'template_id': None,
                'contact_id': contact_id if 'contact_id' in locals() else None,
                'customer_id': customer_id if 'customer_id' in locals() else None,
                'subject': subject if 'subject' in locals() else 'Error occurred before subject creation',
                'recipient_email': contact_email if 'contact_email' in locals() else 'Unknown',
                'status': 'error',
                'error_message': error_msg,
                'custom_email': True
            }
            save_email_log(log_data)
        except:
            print(f"Critical error - couldn't log error for request {request_id}: {error_msg}")

        print(f"Unexpected error in request {request_id}: {error_msg}")
        return jsonify({'success': False, 'error': error_msg})


@api_bp.route('/apollo-search-general', methods=['POST'])
@require_api_key
def search_apollo_general():
    """General Apollo search for companies (for new customer creation)"""
    try:
        # Get search term from request
        data = request.get_json()
        search_term = data.get('q_organization_name', '')

        if not search_term:
            return jsonify({'error': 'Search term is required'}), 400

        response = requests.post(
            "https://api.apollo.io/v1/organizations/search",
            headers={
                'X-API-KEY': current_app.config['APOLLO_API_KEY'],
                'Cache-Control': 'no-cache',
                'Content-Type': 'application/json'
            },
            json={
                'api_key': current_app.config['APOLLO_API_KEY'],
                'q_organization_name': search_term,
                'page': 1,
                'per_page': 10
            }
        )

        if response.status_code == 200:
            data = response.json()
            organizations = [{
                'id': org.get('id'),
                'name': org.get('name'),
                'website': org.get('website_url'),
                'linkedin_url': org.get('linkedin_url'),
                'domain': org.get('primary_domain'),
                'description': org.get('description'),
                'country': org.get('country'),
                'logo_url': org.get('logo_url'),
                'employee_count': org.get('estimated_num_employees')
            } for org in data.get('organizations', [])]

            return jsonify({
                'success': True,
                'data': {
                    'organizations': organizations,
                    'total_results': data.get('pagination', {}).get('total_entries', 0)
                }
            })

        return jsonify({
            'success': False,
            'error': f'Apollo API error: {response.status_code}'
        }), response.status_code

    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500
