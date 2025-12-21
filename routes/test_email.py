from flask import Blueprint, render_template, request
import os
import email
from email import policy
from email.parser import BytesParser
from bs4 import BeautifulSoup

test_email_bp = Blueprint('test_email', __name__)

UPLOAD_FOLDER = 'uploads'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

@test_email_bp.route('/upload_email', methods=['GET', 'POST'])
def upload_email():
    if request.method == 'POST':
        file = request.files['email_file']
        if file and file.filename.endswith('.eml'):
            file_path = os.path.join(UPLOAD_FOLDER, file.filename)
            file.save(file_path)

            # Parse the email file
            with open(file_path, 'rb') as f:
                msg = BytesParser(policy=policy.default).parse(f)

            # Extract the HTML part of the email
            html_content = None
            if msg.is_multipart():
                for part in msg.iter_parts():
                    if part.get_content_type() == 'text/html':
                        html_content = part.get_payload(decode=True).decode(part.get_content_charset())
                        break
            else:
                if msg.get_content_type() == 'text/html':
                    html_content = msg.get_payload(decode=True).decode(msg.get_content_charset())

            if not html_content:
                return "No HTML content found in the email.", 400

            # Clean and prettify the HTML content using BeautifulSoup
            soup = BeautifulSoup(html_content, 'html.parser')
            pretty_html_content = soup.prettify()

            return render_template('display_email.html', email_content=pretty_html_content)

    return render_template('upload_email.html')
