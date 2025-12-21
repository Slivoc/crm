# In your Flask application, create a new blueprint for API routes
from collections import defaultdict
from flask import Blueprint, request, jsonify, current_app, url_for
import base64
import os
import re
import requests
import extract_msg
from datetime import datetime
from db import db_cursor, execute as db_execute
from models import delete_customer_tag, get_contacts_by_customer, insert_customer_tag, filter_tags_by_search, get_all_tags, update_customer_apollo_id, get_tags_by_customer_id, get_email_logs, get_customer_tags, get_customer_apollo_id, get_excess_stock_list_by_id, get_supplier_by_email, save_email_log, get_email_signature_by_id, get_template_by_id, get_contact_by_id, get_customer_by_id
from routes.emails import clean_message_id, allowed_file, build_email_from_template, send_email_from_template
import mimetypes
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.image import MIMEImage
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

        # Add email signature
        email_signature = get_email_signature_by_id(1)
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
                'body': body_html
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
        # Email configuration
        email_host = 'smtps.aruba.it'
        email_port = 465
        email_user = os.getenv('EMAIL_USER')
        email_password = os.getenv('EMAIL_PASSWORD')
        imap_host = 'imaps.aruba.it'

        if not all([email_user, email_password]):
            error_msg = 'Email configuration is incomplete'
            return jsonify({'success': False, 'error': error_msg})

        data = request.json
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

        # Generate a unique Message-ID
        # Generate a unique Message-ID
        message_id_raw = f"{uuid.uuid4()}@recitalia.it"

        # Create the email message
        msg = MIMEMultipart('related')
        msg['From'] = f"Tom Palmer <{email_user}>"
        msg['To'] = contact_email
        msg['Subject'] = subject
        msg['Date'] = datetime.now().strftime('%a, %d %b %Y %H:%M:%S +0000')
        msg['Message-ID'] = f"<{message_id_raw}>"

        # Add BCC
        bcc_email = '145554557@bcc.eu1.hubspot.com'  # Replace with your email address for testing
        msg['Bcc'] = bcc_email
        print(f"BCC added: {msg['Bcc']}")

        msg_alternative = MIMEMultipart('alternative')
        msg.attach(msg_alternative)

        # Fetch and attach the email signature
        email_signature = get_email_signature_by_id(1)
        if email_signature:
            signature_html = email_signature['signature_html']
            body += signature_html

        # Attach plain text and HTML versions
        text_part = MIMEText(template['body'].strip(), 'plain')
        html_part = MIMEText(body.strip(), 'html')
        msg_alternative.attach(text_part)
        msg_alternative.attach(html_part)

        # Handle images
        uploads_dir = os.path.join(current_app.root_path, 'uploads')

        # Attach logo image
        logo_path = os.path.join(uploads_dir, 'blimage001.jpg')
        if os.path.exists(logo_path):
            with open(logo_path, 'rb') as img:
                img_data = img.read()
                image = MIMEImage(img_data)
                image.add_header('Content-ID', '<image001>')
                image.add_header('Content-Disposition', 'inline')
                msg.attach(image)
        else:
            print(f"Warning: Logo image not found at {logo_path}")

        # Attach LinkedIn icon
        linkedin_path = os.path.join(uploads_dir, 'linkedin_icon.png')
        if os.path.exists(linkedin_path):
            with open(linkedin_path, 'rb') as img:
                img_data = img.read()
                image = MIMEImage(img_data)
                image.add_header('Content-ID', '<linkedin_icon>')
                image.add_header('Content-Disposition', 'inline')
                msg.attach(image)
        else:
            print(f"Warning: LinkedIn icon not found at {linkedin_path}")

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
            with smtplib.SMTP_SSL(email_host, email_port) as server:
                server.login(email_user, email_password)

                # Print the raw email headers and body for debugging
                print("Generated Email Content:")
                print(msg.as_string())

                # Send the email
                server.send_message(msg)

                # Save to Sent folder via IMAP
                try:
                    print(f"Saving email to Sent folder for request {request_id}")
                    import imaplib
                    import time
                    with imaplib.IMAP4_SSL(imap_host) as imap:
                        imap.login(email_user, email_password)

                        # Select the Sent folder
                        sent_folder = 'INBOX.Sent'
                        imap.select(sent_folder)

                        # Convert the email message to string format
                        email_str = msg.as_string().encode('utf-8')

                        # Add the email to Sent folder
                        imap.append(sent_folder, '\\Seen', imaplib.Time2Internaldate(time.time()), email_str)

                    print(f"Successfully saved email to Sent folder for request {request_id}")

                    # Add this new block for email logging with message_id instead of uid
                    # Add this new block for email logging with message_id instead of uid
                    try:
                        print(f"Logging email to database for request {request_id}")

                        # Extract the Message-ID from the headers for database storage
                        message_id = msg['Message-ID']
                        # Remove angle brackets if present
                        if message_id.startswith('<') and message_id.endswith('>'):
                            message_id = message_id[1:-1]

                        print(f"Logging email with Message-ID: {message_id}")

                        email_data = {
                            'message_id': clean_message_id(msg['Message-ID']),  # Use the extracted Message-ID
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
                                    email_user,
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

                except Exception as imap_error:
                    print(f"Warning: Failed to save to Sent folder for request {request_id}: {str(imap_error)}")

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
            error_msg = f'SMTP Error: {str(e)}'
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
        per_page = 10
        offset = (page - 1) * per_page

        contacts = get_contacts_by_customer(customer_id, limit=per_page, offset=offset)

        return jsonify({
            'success': True,
            'data': {
                'items': [{
                    'id': contact['id'],
                    'name': contact['name'],
                    'email': contact['email'],
                    'job_title': contact['job_title']
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
        customer = get_customer_by_id(customer_id)
        print(f"Customer data: {customer}")

        if not customer:
            print("Customer not found")
            return jsonify({'error': 'Customer not found'}), 404

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
                    'apollo_id': apollo_id if apollo_id else None
                },
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
        # Email configuration
        email_host = 'smtps.aruba.it'
        email_port = 465
        email_user = os.getenv('EMAIL_USER')
        email_password = os.getenv('EMAIL_PASSWORD')
        imap_host = 'imaps.aruba.it'

        if not all([email_user, email_password]):
            error_msg = 'Email configuration is incomplete'
            return jsonify({'success': False, 'error': error_msg})

        data = request.json
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
        email_signature = get_email_signature_by_id(1)
        if email_signature:
            signature_html = email_signature.get('signature_html', '')
            body += signature_html

        # Generate a unique Message-ID
        message_id_raw = f"{uuid.uuid4()}@recitalia.it"

        # Create the email message
        msg = MIMEMultipart('related')
        msg['From'] = f"Tom Palmer <{email_user}>"
        msg['To'] = contact_email
        msg['Subject'] = subject
        msg['Date'] = datetime.now().strftime('%a, %d %b %Y %H:%M:%S +0000')
        msg['Message-ID'] = f"<{message_id_raw}>"

        # Add BCC
        bcc_email = '145554557@bcc.eu1.hubspot.com'  # Replace with your email address for testing
        msg['Bcc'] = bcc_email
        print(f"BCC added: {msg['Bcc']}")

        msg_alternative = MIMEMultipart('alternative')
        msg.attach(msg_alternative)

        # Create plain text version by stripping HTML tags
        plain_text = re.sub('<.*?>', '', body)
        plain_text = plain_text.replace('&nbsp;', ' ')
        plain_text = plain_text.replace('&amp;', '&')
        plain_text = plain_text.replace('&lt;', '<')
        plain_text = plain_text.replace('&gt;', '>')

        # Attach plain text and HTML versions
        text_part = MIMEText(plain_text.strip(), 'plain')
        html_part = MIMEText(body.strip(), 'html')
        msg_alternative.attach(text_part)
        msg_alternative.attach(html_part)

        # Handle images
        uploads_dir = os.path.join(current_app.root_path, 'uploads')

        # Attach logo image
        logo_path = os.path.join(uploads_dir, 'blimage001.jpg')
        if os.path.exists(logo_path):
            with open(logo_path, 'rb') as img:
                img_data = img.read()
                image = MIMEImage(img_data)
                image.add_header('Content-ID', '<image001>')
                image.add_header('Content-Disposition', 'inline')
                msg.attach(image)
        else:
            print(f"Warning: Logo image not found at {logo_path}")

        # Attach LinkedIn icon
        linkedin_path = os.path.join(uploads_dir, 'linkedin_icon.png')
        if os.path.exists(linkedin_path):
            with open(linkedin_path, 'rb') as img:
                img_data = img.read()
                image = MIMEImage(img_data)
                image.add_header('Content-ID', '<linkedin_icon>')
                image.add_header('Content-Disposition', 'inline')
                msg.attach(image)
        else:
            print(f"Warning: LinkedIn icon not found at {linkedin_path}")

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
            with smtplib.SMTP_SSL(email_host, email_port) as server:
                server.login(email_user, email_password)

                # Print the raw email headers and body for debugging
                print("Generated Email Content:")
                print(msg.as_string())

                # Send the email
                server.send_message(msg)

                # Save to Sent folder via IMAP
                try:
                    print(f"Saving email to Sent folder for request {request_id}")
                    import imaplib
                    import time
                    with imaplib.IMAP4_SSL(imap_host) as imap:
                        imap.login(email_user, email_password)

                        # Select the Sent folder
                        sent_folder = 'INBOX.Sent'
                        imap.select(sent_folder)

                        # Convert the email message to string format
                        email_str = msg.as_string().encode('utf-8')

                        # Add the email to Sent folder
                        imap.append(sent_folder, '\\Seen', imaplib.Time2Internaldate(time.time()), email_str)

                    print(f"Successfully saved email to Sent folder for request {request_id}")

                    # Add email logging with message_id
                    try:
                        print(f"Logging email to database for request {request_id}")

                        # Clean message_id (remove angle brackets if present)
                        message_id = msg['Message-ID']
                        if message_id.startswith('<') and message_id.endswith('>'):
                            message_id = message_id[1:-1]

                        print(f"Logging email with Message-ID: {message_id}")

                        email_data = {
                            'message_id': message_id,
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
                                    email_user,
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

                except Exception as imap_error:
                    print(f"Warning: Failed to save to Sent folder for request {request_id}: {str(imap_error)}")

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
            error_msg = f'SMTP Error: {str(e)}'
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
