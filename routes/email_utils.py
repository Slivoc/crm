import extract_msg
from email import policy
from email.parser import BytesParser
import chardet

def parse_email(file_path):
    try:
        if file_path.lower().endswith('.msg'):
            # Handle .msg files using extract_msg
            msg = extract_msg.Message(file_path)
            msg_body = msg.body
            if msg_body:
                return clean_html(msg_body) if '<html' in msg_body.lower() else format_plain_text(msg_body)
            else:
                return None

        # For .eml files
        with open(file_path, 'rb') as f:
            msg = BytesParser(policy=policy.default).parse(f)

        html_content = None
        plain_content = None

        for part in msg.walk():
            if part.get_content_type() == 'text/html':
                html_content = decode_content(part)
            elif part.get_content_type() == 'text/plain':
                plain_content = decode_content(part)

        if html_content:
            return clean_html(html_content)
        elif plain_content:
            return format_plain_text(plain_content)
        else:
            return None
    except Exception as e:
        print(f"Error parsing email: {e}")
        return None

def decode_content(part):
    content = part.get_payload(decode=True)
    charset = part.get_content_charset()
    if charset is None:
        detected = chardet.detect(content)
        charset = detected['encoding']
    return content.decode(charset, errors='ignore') if charset else content.decode('utf-8', errors='ignore')

def clean_html(html_content):
    # Implement your HTML sanitization logic, e.g., using BeautifulSoup or Bleach
    pass

def format_plain_text(plain_content):
    # Implement plain text formatting logic
    pass
