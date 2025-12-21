from flask import Blueprint, render_template, request, jsonify, current_app
from models import get_contact_by_id
from routes.emails import get_all_templates, get_template_by_id, get_email_signature_by_id
from db import execute as db_execute
import json

# Create a new Blueprint for bulk email functionality
bulk_emails_bp = Blueprint('bulk_emails', __name__)


@bulk_emails_bp.route('/')
def bulk_email_sender():
    """
    Render the bulk email sender page
    """
    # Get all email templates for the dropdown
    templates = get_all_templates()

    # Get all customers for the filter dropdown
    customers = db_execute(
        'SELECT id, name FROM customers ORDER BY name',
        fetch='all'
    ) or []
    customers = [dict(customer) for customer in customers]

    return render_template(
        'emails/bulk_sender.html',
        templates=templates,
        customers=customers
    )


@bulk_emails_bp.route('/api/contacts/search', methods=['GET'])
def search_contacts():
    """API endpoint to search contacts with filters"""
    search_term = request.args.get('term', '').strip()
    customer_id = request.args.get('customer_id', None)

    query = """
        SELECT c.id, c.name, c.email, c.job_title, cu.name as customer_name, c.customer_id 
        FROM contacts c
        LEFT JOIN customers cu ON c.customer_id = cu.id
        WHERE 1=1
    """
    params = []

    if search_term:
        query += " AND (c.name LIKE ? OR c.email LIKE ?)"
        params.extend([f'%{search_term}%', f'%{search_term}%'])

    if customer_id and customer_id != 'all':
        query += " AND c.customer_id = ?"
        params.append(customer_id)

    query += " ORDER BY c.name LIMIT 100"

    contacts = db_execute(query, params, fetch='all') or []
    contacts = [dict(contact) for contact in contacts]

    return jsonify(contacts)


@bulk_emails_bp.route('/api/send', methods=['POST'])
def bulk_send_email():
    """API endpoint to send emails to multiple contacts"""
    from routes.api import send_email, send_custom_email
    import flask

    data = request.json
    contact_ids = data.get('contact_ids', [])
    template_id = data.get('template_id')
    is_custom = data.get('is_custom', False)
    custom_subject = data.get('custom_subject', '')
    custom_body = data.get('custom_body', '')

    if not contact_ids:
        return jsonify({'success': False, 'error': 'No contacts selected'})

    if is_custom and (not custom_subject or not custom_body):
        return jsonify({'success': False, 'error': 'Subject and body are required for custom emails'})

    if not is_custom and not template_id:
        return jsonify({'success': False, 'error': 'Template is required for template emails'})

    # Process each contact
    results = []
    for contact_id in contact_ids:
        try:
            contact = get_contact_by_id(contact_id)
            if not contact:
                results.append({
                    'contact_id': contact_id,
                    'success': False,
                    'message': 'Contact not found'
                })
                continue

            customer_id = contact.get('customer_id')

            # Prepare request data for existing email API
            email_data = {
                'contact_id': contact_id,
                'customer_id': customer_id
            }

            if is_custom:
                # Use custom email with a new request context
                with current_app.test_request_context(
                        '/api/send-custom-email',
                        method='POST',
                        data=json.dumps({
                            'subject': custom_subject,
                            'body': custom_body,
                            'contact_id': contact_id,
                            'customer_id': customer_id
                        }),
                        content_type='application/json'
                ) as ctx:
                    # Call send_custom_email in the new request context
                    response = send_custom_email()

                    # The response is a Flask Response object, so we need to convert it to json
                    if isinstance(response, flask.Response):
                        response_data = json.loads(response.get_data(as_text=True))
                    else:
                        # Just in case it's already a dict
                        response_data = response
            else:
                # For template emails, create a new request context
                with current_app.test_request_context(
                        '/api/send-email',
                        method='POST',
                        data=json.dumps({
                            'template_id': template_id,
                            'contact_id': contact_id,
                            'customer_id': customer_id
                        }),
                        content_type='application/json'
                ) as ctx:
                    # Call send_email in the new request context
                    response = send_email()

                    # The response is a Flask Response object, so we need to convert it to json
                    if isinstance(response, flask.Response):
                        response_data = json.loads(response.get_data(as_text=True))
                    else:
                        # Just in case it's already a dict
                        response_data = response

            if response_data.get('success'):
                results.append({
                    'contact_id': contact_id,
                    'contact_name': contact.get('name', ''),
                    'success': True,
                    'message': f"Email sent to {contact.get('email', '')}"
                })
            else:
                results.append({
                    'contact_id': contact_id,
                    'contact_name': contact.get('name', ''),
                    'success': False,
                    'message': response_data.get('error', 'Unknown error')
                })

        except Exception as e:
            import traceback
            error_details = traceback.format_exc()
            print(f"Error sending email to contact #{contact_id}: {str(e)}")
            print(error_details)
            results.append({
                'contact_id': contact_id,
                'success': False,
                'message': f"Error: {str(e)}"
            })

    # Count successes and failures
    success_count = sum(1 for result in results if result.get('success'))
    failure_count = len(results) - success_count

    return jsonify({
        'success': True,
        'message': f"Sent {success_count} emails, {failure_count} failed",
        'results': results
    })
