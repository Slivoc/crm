from flask import Blueprint, send_file, abort, render_template, jsonify, request
from db import execute as db_execute, db_cursor
from models import get_file_by_id
import extract_msg
from markupsafe import Markup
from striprtf.striprtf import rtf_to_text

files_bp = Blueprint('files', __name__)


@files_bp.route('/<int:file_id>')
def serve_file(file_id):
    file_data = db_execute('SELECT filepath FROM files WHERE id = ?', (file_id,), fetch='one')

    if not file_data:
        abort(404, description="File record not found")

    try:
        return send_file(file_data['filepath'], as_attachment=True)
    except FileNotFoundError:
        abort(404, description="File not found on server")

@files_bp.route('/email/<int:file_id>', methods=['GET'])
def view_email_file(file_id):
    email_file = get_file_by_id(file_id)  # Your existing function
    if not email_file:
        abort(404)  # Return a 404 if the file doesn't exist

    try:
        # Parse the .msg file using extract_msg
        msg = extract_msg.Message(email_file['filepath'])

        # If HTML body exists, use it; otherwise, use the plain text or RTF body
        if msg.htmlBody:
            email_content = msg.htmlBody  # Display HTML body if available
        else:
            # Print the raw content of msg.body before any processing
            print(f"Raw msg.body content: {msg.body}")

            # Get the email body (it could be RTF)
            email_content = msg.body

            # Ensure the content is decoded from bytes if necessary
            if isinstance(email_content, bytes):
                email_content = email_content.decode('utf-8', errors='ignore')

            # Check if the content is in RTF format and print out for verification
            if email_content.strip().startswith('{\\rtf'):
                print("RTF content detected. Converting...")
                email_content = rtf_to_text(email_content)  # Convert RTF to plain text
                print(f"Converted RTF content: {email_content}")
            else:
                print("RTF content not detected. Proceeding with plain text.")

            # Replace newline characters with <br> for HTML rendering
            email_content = email_content.replace('\r\n', '\n').replace('\n', '<br>')

    except Exception as e:
        # If something goes wrong, show an error message
        email_content = f"Failed to parse email content: {str(e)}"

    # Render the email content in the template
    return render_template('view_email.html', email_content=email_content)

@files_bp.route('/edit_file_description/<int:file_id>', methods=['POST'])
def edit_file_description(file_id):
    data = request.json
    new_description = data.get('description')
    if not new_description:
        return jsonify({'success': False, 'error': 'Description is required.'}), 400

    try:
        with db_cursor(commit=True) as cur:
            _ = cur.execute('UPDATE files SET description = ? WHERE id = ?', (new_description, file_id))
        return jsonify({'success': True})
    except Exception as e:
        print(f"Error updating file description: {e}")
        return jsonify({'success': False, 'error': 'Could not update file description.'}), 500
