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

