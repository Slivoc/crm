import os
import extract_msg
from flask import Blueprint, request, render_template, flash, redirect, url_for
from striprtf.striprtf import rtf_to_text
import re
from werkzeug.utils import secure_filename

from db import db_cursor, execute as db_execute
from flask import current_app
import datetime


# Initialize Blueprint
upload_bp = Blueprint('upload_bp', __name__)

# Set your upload folder
UPLOAD_FOLDER = 'uploads/'  # Ensure this folder exists


def _using_postgres():
    """Detect whether DATABASE_URL indicates a Postgres connection."""
    return bool(os.getenv('DATABASE_URL'))


def _prepare_query(query):
    """Translate SQLite '?' placeholders to Postgres '%s' when needed."""
    return query.replace('?', '%s') if _using_postgres() else query


def _execute_with_cursor(cur, query, params=None):
    """Run a query on the provided cursor with placeholder translation."""
    cur.execute(_prepare_query(query), params or [])
    return cur


def parse_email(filepath):
    """
    Function to parse the email file and return the email content.
    """
    try:
        # Open and parse the .msg file
        msg = extract_msg.Message(filepath)

        # Extract relevant information
        subject = msg.subject
        body = msg.body
        html_body = msg.htmlBody if msg.htmlBody else None

        # If HTML body exists, process it
        if html_body:
            if isinstance(html_body, bytes):
                html_body = html_body.decode('utf-8', errors='ignore')

            # Remove all <img> tags using regex
            html_body = re.sub(r'<img[^>]*>', '', html_body)
            body = html_body  # Set HTML body as the main content

        # Check if the body is in RTF format and convert it to plain text
        elif body.strip().startswith('{\\rtf'):
            body = rtf_to_text(body)

        # Ensure body is properly decoded if it is bytes
        elif isinstance(body, bytes):
            body = body.decode('utf-8', errors='ignore')

        # Replace newlines
        body = body.replace('\r\n', '\n').replace('\r', '\n')

        return body  # Return the processed email body
    except Exception as e:
        return f"Failed to parse email: {str(e)}"

@upload_bp.route('/upload_email/<int:rfq_id>', methods=['POST'])
def upload_email(rfq_id):
    """
    Handles the uploading of an email file, saves it, and associates it with the given RFQ.
    """
    if 'file' not in request.files:
        flash('No file part')
        return redirect(request.url)

    file = request.files['file']

    if file.filename == '':
        flash('No selected file')
        return redirect(request.url)

    if file:
        try:
            # Save the file to the specified upload folder
            filename = secure_filename(file.filename)
            file_path = os.path.join(current_app.config['UPLOAD_FOLDER'], filename)
            file.save(file_path)

            # Parse the email content
            email_content = parse_email(file_path)

            # Insert into the files table and associate to RFQ inside a transaction
            with db_cursor(commit=True) as cur:
                insert_file_sql = 'INSERT INTO files (filename, filepath, upload_date) VALUES (?, ?, ?)'
                if _using_postgres():
                    _execute_with_cursor(cur, insert_file_sql + ' RETURNING id', (filename, file_path, datetime.now()))
                    row = cur.fetchone()
                    file_id = row['id'] if isinstance(row, dict) else row[0]
                else:
                    _execute_with_cursor(cur, insert_file_sql, (filename, file_path, datetime.now()))
                    file_id = getattr(cur, 'lastrowid', None)

                _execute_with_cursor(
                    cur,
                    'INSERT INTO rfq_files (rfq_id, file_id) VALUES (?, ?)',
                    (rfq_id, file_id),
                )

                if email_content:
                    _execute_with_cursor(
                        cur,
                        'UPDATE rfqs SET email = ? WHERE id = ?',
                        (email_content, rfq_id),
                    )

            flash('Email uploaded and processed successfully.')
            return redirect(url_for('rfqs.edit_rfq', rfq_id=rfq_id))
        except Exception as e:
            flash(f"An error occurred: {str(e)}")
            return redirect(request.url)

    return redirect(url_for('rfqs.edit_rfq', rfq_id=rfq_id))
