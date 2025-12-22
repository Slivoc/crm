from dotenv import load_dotenv
# Load environment variables FIRST before any other imports
load_dotenv()

from flask import Flask, redirect, request, url_for, send_from_directory, g, jsonify, render_template, current_app, session
from flask_login import LoginManager, current_user
import imaplib
import os
import time
from routes.rfqs import rfqs_bp, get_rfq_lines
from routes.customers import customers_bp
from routes.suppliers import suppliers_bp
from routes.test_email import test_email_bp
from routes.salespeople import salespeople_bp, collect_customer_news
from models import get_salespeople, insert_update, get_updates_by_customer_id, insert_rfq_from_macro, get_all_tags, get_project_by_id, Permission
from routes.emails import get_company_name_by_email
from routes.parts import parts_bp
from routes.manufacturers import manufacturers_bp
from routes.offers import offers_bp
from routes.settings import settings_bp
from routes.files import files_bp
from models import get_rfq_line_currency, verify_rfq_and_lines, get_rfq_by_id, get_contacts, User
from db import execute as db_execute
from routes.currencies import currencies_bp
import logging
from routes.sales_orders import sales_orders_bp
from routes.purchase_orders import purchase_orders_bp
from routes.emails import emails_bp
from routes.projects import projects_bp
from routes.upload import upload_bp
from routes.excess import excess_bp  # Import the excess stock list routes
from routes.handson import handson_bp
from routes.api import api_bp
from routes.dynamic_table import dynamic_table_bp
from routes.dashboard import dashboard_bp
from routes.imports import imports_bp
from routes.templates import templates_bp
from routes.hubspot_integration import hubspot_bp
from routes.auth import auth_bp
from routes.admin import admin_bp
from routes.bom import bom_bp
from routes.price_lists import price_lists_bp
from routes.invoices import invoices_bp
from routes.stock_movements import stock_movements_bp
from routes.tax_rates import tax_rates_bp
from routes.expediting import expediting_bp
from routes.finance import finance_bp
import email
from email.header import decode_header  # For handling encoded email headers
from email.message import EmailMessage  # For creating/manipulating email messages
from email.parser import BytesParser  # For parsing raw email bytes
from flask_apscheduler import APScheduler
from routes.nexar import nexar_bp
from routes.salesperson_metrics import salesperson_metrics_bp
from routes.bulk_emails import bulk_emails_bp
from routes.email_signatures import signatures_bp
from markdown import markdown
from routes.geo_deepdive import geo_deepdive_bp
from routes.sales_suggestions import sales_suggestions_bp
from routes.purchase_suggestions import purchase_suggestions_bp
from flask_session import Session
from routes.vqs import vqs_bp
from routes.parts_list import parts_list_bp
from routes.cqs import cqs_bp
from routes.so_import import so_import_bp
from routes.ils import ils_bp
from routes.customer_quoting import customer_quoting_bp
from routes.marketplace import marketplace_bp
from routes.portal_api import portal_api_bp
from routes.portal_admin import portal_admin_bp
from routes.parts_list_ai import parts_list_ai_bp

scheduler = APScheduler()

logging.basicConfig(level=logging.INFO)
_SALESPEOPLE_CACHE = {'value': None, 'ts': 0.0}
_SALESPEOPLE_CACHE_TTL_S = 60.0

# Initialize the Flask app
app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'secret_key')
app.config['OPENAI_API_KEY'] = os.getenv('OPENAI_API_KEY')
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['API_KEY'] = os.getenv('API_KEY')
app.config['APOLLO_API_KEY'] = os.getenv('APOLLO_API_KEY')
app.config['HUBSPOT_API_KEY'] = os.getenv('HUBSPOT_API_KEY')
app.config['APOLLO_BASE_URL'] = 'https://api.apollo.io/v1'  # It's good to keep the base URL in config too
app.config['EXCHANGE_RATE_API_KEY'] = 'dca912446be60b1b0aa83a4f'  # Get this from exchangerate-api.com or similar service
app.config['SESSION_TYPE'] = 'filesystem'  # Store sessions in files
app.config['SESSION_FILE_DIR'] = './flask_session'  # Session folder
app.secret_key = 'your-secret-key-here'
Session(app)

logging.info(f"Loaded API_KEY: {app.config['API_KEY']}")

EMAIL_HOST = os.getenv('EMAIL_HOST')
EMAIL_PORT = int(os.getenv('EMAIL_PORT', 993))  # Default to 993 for IMAP SSL if not set
EMAIL_USER = os.getenv('EMAIL_USER')
EMAIL_PASSWORD = os.getenv('EMAIL_PASSWORD')

def is_mobile():
    """Detect if the request is from a mobile device"""
    user_agent = request.headers.get('User-Agent', '').lower()
    mobile_keywords = ['mobile', 'android', 'iphone', 'ipad', 'windows phone', 'blackberry']
    return any(keyword in user_agent for keyword in mobile_keywords)

def _is_static_request():
    path = request.path or ''
    return request.endpoint == 'static' or path.startswith('/static/') or path == '/favicon.ico'

def _get_salespeople_cached():
    now = time.monotonic()
    cached = _SALESPEOPLE_CACHE
    if cached['value'] is None or (now - cached['ts']) > _SALESPEOPLE_CACHE_TTL_S:
        cached['value'] = get_salespeople()
        cached['ts'] = now
    return cached['value']

@app.context_processor
def inject_device_info():
    return {'is_mobile': is_mobile()}

@app.context_processor
def inject_base_template():
    base = 'base_mobile.html' if is_mobile() else 'base.html'
    return {'base_template': base}

# Register a filter for converting newlines to <br> tags
def nl2br(value):
    return value.replace('\n', '<br>')

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'auth.login'

@login_manager.user_loader
def load_user(user_id):
    if _is_static_request():
        return None
    return User.get(int(user_id))  # Using the User.get method we defined in models.py

app.jinja_env.filters['nl2br'] = nl2br

# Register Blueprints
app.register_blueprint(rfqs_bp, url_prefix='/rfqs')
app.register_blueprint(customers_bp, url_prefix='/customers')
app.register_blueprint(suppliers_bp, url_prefix='/suppliers')
app.register_blueprint(test_email_bp, url_prefix='/test')
app.register_blueprint(salespeople_bp, url_prefix='/salespeople')
app.register_blueprint(parts_bp, url_prefix='/')
app.register_blueprint(manufacturers_bp, url_prefix='/manufacturers')
app.register_blueprint(offers_bp, url_prefix='/offers')
app.register_blueprint(settings_bp)
app.register_blueprint(files_bp, url_prefix='/files')
app.register_blueprint(currencies_bp)
app.register_blueprint(sales_orders_bp, url_prefix='/sales_orders')
app.register_blueprint(purchase_orders_bp, url_prefix='/purchase_orders')
app.register_blueprint(emails_bp)
app.register_blueprint(projects_bp, url_prefix='/projects')
app.register_blueprint(upload_bp, url_prefix='/upload')
app.register_blueprint(excess_bp, url_prefix='/excess')  # Set up the route prefix for excess lists
app.register_blueprint(handson_bp, url_prefix='/handson')
app.register_blueprint(api_bp, url_prefix='/api')
app.register_blueprint(dynamic_table_bp, url_prefix='/dynamic')
app.register_blueprint(dashboard_bp, url_prefix='/dashboard')
app.register_blueprint(imports_bp, url_prefix='/imports')
app.register_blueprint(templates_bp, url_prefix='/templates')
app.register_blueprint(hubspot_bp, url_prefix='/hubspot')
app.register_blueprint(auth_bp, url_prefix='/auth')
app.register_blueprint(admin_bp)
app.register_blueprint(bom_bp, url_prefix='/bom')
app.register_blueprint(invoices_bp, url_prefix='/invoices')
app.register_blueprint(price_lists_bp, url_prefix='/price_lists')
app.register_blueprint(stock_movements_bp, url_prefix='/stock')
app.register_blueprint(tax_rates_bp, url_prefix='/tax_rates')
app.register_blueprint(expediting_bp, url_prefix='/expediting')
app.register_blueprint(finance_bp, url_prefix='/finance')
app.register_blueprint(nexar_bp, url_prefix='/nexar')
app.register_blueprint(salesperson_metrics_bp, url_prefix='/metrics')
app.register_blueprint(bulk_emails_bp, url_prefix='/bulk_emails')
app.register_blueprint(signatures_bp, url_prefix='/signatures')
app.register_blueprint(geo_deepdive_bp)
app.register_blueprint(sales_suggestions_bp, url_prefix='/sales-suggestions')
app.register_blueprint(purchase_suggestions_bp, url_prefix='/purchase-suggestions')
app.register_blueprint(vqs_bp, url_prefix='/vqs')
app.register_blueprint(cqs_bp, url_prefix='/cqs')
app.register_blueprint(parts_list_bp, url_prefix='/parts_list')
app.register_blueprint(so_import_bp, url_prefix='/so-import')
app.register_blueprint(ils_bp, url_prefix='/ils')
app.register_blueprint(customer_quoting_bp, url_prefix='/customer-quoting')
app.register_blueprint(marketplace_bp, url_prefix='/marketplace')
app.register_blueprint(parts_list_ai_bp, url_prefix='/parts-list-ai')
app.secret_key = 'your-secret-key-here'  # Required for sessions
app.register_blueprint(portal_api_bp)
app.register_blueprint(portal_admin_bp)

def bit_and(value, other):
    return value & other
# Add this filter registration (after creating your Flask app)
@app.template_filter('markdown')
def markdown_filter(text):
    """Convert markdown text to HTML"""
    if not text:
        return ""
    return markdown(text, extensions=[
        'nl2br',           # Convert line breaks to <br> (you already have this)
        'tables',          # Support for markdown tables
        'fenced_code',     # Support for ```code blocks```
        'toc'              # Table of contents support
    ])

# Register the filter with your app
app.jinja_env.filters['bit_and'] = bit_and

def list_routes():
    import urllib
    output = []
    for rule in app.url_map.iter_rules():
        methods = ','.join(rule.methods)
        line = urllib.parse.unquote(f"{rule.endpoint}: {rule} [{methods}]")
        output.append(line)

    # Print all routes
    for line in sorted(output):
        print(line)

# Load salespeople before each request
@app.before_request
def before_request():
    if _is_static_request():
        return None
    # Load salespeople
    g.salespeople = _get_salespeople_cached()

    # Add the current user's salesperson_id to g if they're logged in
    if current_user.is_authenticated and hasattr(current_user, 'get_salesperson_id'):
        g.current_salesperson_id = current_user.get_salesperson_id()
    else:
        g.current_salesperson_id = session.get('selected_salesperson_id')

@app.context_processor
def inject_auth_status():
    return {
        'current_user': current_user,
        'selected_salesperson_id': session.get('selected_salesperson_id')
    }
# Inject salespeople globally
@app.context_processor
def inject_salespeople():
    return dict(salespeople=g.salespeople)

# Define routes
@app.route('/')
def index():
    # Determine which base template to use
    base_template = 'base_mobile.html' if is_mobile() else 'base.html'

    if current_user.is_authenticated:
        salesperson_id = current_user.get_salesperson_id()
        if not salesperson_id:
            salesperson_id = session.get('selected_salesperson_id')

        if salesperson_id:
            return redirect(url_for('salespeople.activity', salesperson_id=salesperson_id))
        return redirect(url_for('salespeople.dashboard'))

    return redirect(url_for('auth.login'))

@app.route('/uploads/<filename>')
def uploaded_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

@app.route('/<path:path>', methods=['GET', 'POST'])
def catch_all(path):
    print(f"Caught request to: {path}")
    return jsonify({"error": "Route not found"}), 404

@app.route('/get_rfq_line_currency/<int:line_id>')
def rfq_line_currency(line_id):
    current_app.logger.debug(f"Received request for RFQ line currency: line_id={line_id}")
    try:
        result = get_rfq_line_currency(line_id)
        current_app.logger.debug(f"Result from get_rfq_line_currency: {result}")
        return jsonify(result)
    except Exception as e:
        current_app.logger.error(f"Error in rfq_line_currency route: {str(e)}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/verify-rfq/<int:rfq_id>')
def debug_verify_rfq(rfq_id):
    lines = verify_rfq_and_lines(rfq_id)
    if lines:
        return jsonify([dict(row) for row in lines])
    else:
        return jsonify({"error": "RFQ or RFQ lines not found"}), 404

@app.route('/view_rfq/<int:rfq_id>')
def view_rfq(rfq_id):
    rfq = get_rfq_by_id(rfq_id)
    if not rfq:
        return "RFQ not found", 404

    rfq_lines = get_rfq_lines(rfq_id)

    # Add debug logging
    for line in rfq_lines:
        logging.info(
            f"RFQ Line in route: id={line.get('id')}, base_part_number={line.get('base_part_number')}, has_price_list_item={line.get('has_price_list_item')}")

    return render_template('rfq_lines.html', rfq=rfq, rfq_lines=rfq_lines)

@app.route('/customers/<int:customer_id>/add_update', methods=['POST'])
def add_update(customer_id):
    update_text = request.form['update_text']
    salesperson_id = request.form.get('salesperson_id', None)

    # Insert the new update
    insert_update(customer_id, salesperson_id, update_text)

    # Redirect back to the customer edit page
    return redirect(url_for('customers.edit_customer', customer_id=customer_id))


@app.route('/add_rfq', methods=['POST'])
def add_rfq_from_outlook():
    data = request.get_json()

    # Extract necessary fields
    customer_ref = data.get('subject', 'email macro test')
    sender_email = data.get('sender_email')
    email_content = data.get('email_content')  # Add this

    # Fix: Get the result as a single object, then extract what you need
    result = get_company_name_by_email(sender_email)
    customer_contact = result['customer_contact']

    if not customer_contact:
        return jsonify(
            {"status": "error", "message": "No matching contact or customer found for this email address"}), 404

    try:
        customer = db_execute(
            'SELECT * FROM customers WHERE id = ?',
            (customer_contact['customer_id'],),
            fetch='one',
        )

        if not customer:
            return jsonify(
                {"status": "error", "message": "Customer not found"}), 404

        # Insert the new RFQ
        new_rfq_id = insert_rfq_from_macro(
            customer['id'],
            customer_contact['id'],
            customer_ref,
            customer['currency_id'],
            "new",
            email_content  # Pass email content directly
        )

        # Return the RFQ ID in the response
        return str(new_rfq_id), 201

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/test_get_all_tags', methods=['GET'])
def test_get_all_tags():
    try:
        tags = get_all_tags()
        print(f"Test tags fetched: {tags}")
        return jsonify(tags)
    except Exception as e:
        print(f"Test route error: {e}")
        return str(e), 500

@scheduler.task('cron', id='news_scan', day_of_week='mon', hour=1, minute=0)
def scheduled_news_scan():
    with app.app_context():
        salespeople = get_salespeople() or []
        for salesperson in salespeople:
            salesperson_id = salesperson.get('id')
            if not salesperson_id:
                continue
            try:
                result = collect_customer_news(salesperson_id)
                current_app.logger.info(
                    "Scheduled News Scan: salesperson_id=%s total=%s",
                    salesperson_id,
                    result.get('total_news_items', 0)
                )
            except Exception as exc:
                current_app.logger.exception(
                    "Scheduled News Scan failed: salesperson_id=%s error=%s",
                    salesperson_id,
                    exc
                )

scheduler.init_app(app)
scheduler.start()

@app.context_processor
def inject_active_project():
    # Get the active project ID from the session
    project_id = session.get('active_project_id')
    # Fetch the project details from the database
    project = get_project_by_id(project_id) if project_id else None
    # Return a dictionary with 'project' to inject into the templates
    return {'project': project}


@app.context_processor
def inject_projects():
    def get_all_projects():
        # Filter by current salesperson if authenticated
        if current_user.is_authenticated:
            salesperson_id = current_user.get_salesperson_id()
            if salesperson_id:
                query = "SELECT id, name FROM projects WHERE salesperson_id = ? ORDER BY name"
                result = db_execute(query, (salesperson_id,), fetch='all')
            else:
                # If no salesperson_id, return empty list
                result = []
        else:
            # If not authenticated, return empty list
            result = []

        return [{"id": row["id"], "name": row["name"]} for row in result]

    return {'projects': get_all_projects()}

@app.context_processor
def inject_functions():
    def get_project_stages(project_id):
        # Your database query logic
        query = """
            SELECT id, name, status_id
            FROM project_stages
            WHERE project_id = ?
        """
        result = db_execute(query, (project_id,), fetch='all')

        return [
            {"id": row["id"], "name": row["name"], "status_id": row["status_id"]}
            for row in result
        ]
    return dict(get_project_stages=get_project_stages)



@app.context_processor
def inject_permissions():
    return dict(Permission=Permission)


@app.context_processor
def finance_utility_processor():
    def format_currency(amount, currency_symbol='€'):
        """Format currency with symbol."""
        if amount is None:
            return f"{currency_symbol}0.00"
        return f"{currency_symbol}{amount:,.2f}"

    return dict(format_currency=format_currency)

app = app

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', use_reloader=False, threaded=True)
