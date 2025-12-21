from flask import Blueprint, jsonify, request, render_template, flash, redirect, url_for
from db import db_cursor
from datetime import date, datetime, timedelta
import os
import json

finance_bp = Blueprint('finance', __name__)


def _using_postgres() -> bool:
    """Return True when DATABASE_URL points at Postgres."""
    return os.getenv('DATABASE_URL', '').startswith(('postgres://', 'postgresql://'))


def _execute_with_cursor(cur, query, params=None):
    """Execute a query with placeholder translation for Postgres."""
    prepared = query.replace('?', '%s') if _using_postgres() else query
    cur.execute(prepared, params or [])
    return cur


def _get_inserted_id(row, cursor=None):
    """Pull an inserted row id from a fetched row or cursor fallback."""
    if row is None:
        return getattr(cursor, 'lastrowid', None) if cursor is not None else None
    if isinstance(row, dict):
        return row.get('id')
    try:
        return row['id']
    except Exception:
        pass
    try:
        return row[0]
    except Exception:
        pass
    return getattr(cursor, 'lastrowid', None) if cursor is not None else None


# ---------------------- Chart of Accounts Routes ----------------------

@finance_bp.route('/', methods=['GET'])
def dashboard():
    """Finance Module Dashboard - Main landing page."""
    today = date.today()
    current_month_start = date(today.year, today.month, 1).isoformat()
    current_month_end = date(today.year, today.month + 1, 1).isoformat() if today.month < 12 else date(today.year + 1, 1, 1).isoformat()

    prev_month = today.month - 1 if today.month > 1 else 12
    prev_month_year = today.year if today.month > 1 else today.year - 1
    prev_month_start = date(prev_month_year, prev_month, 1).isoformat()
    prev_month_end = date(today.year, today.month, 1).isoformat()

    months = []
    chart_revenue = []
    chart_expenses = []

    revenue_this_month = 0
    revenue_last_month = 0
    outstanding_invoices = 0
    open_invoice_count = 0
    cash_position = 0
    bank_account_count = 0
    unposted_journal_count = 0
    recent_activities = []
    account_count = 0
    journal_entry_count = 0
    top_accounts = []
    current_period = None
    open_invoices = []

    with db_cursor() as cursor:
        revenue_this_month_row = _execute_with_cursor(cursor, """
            SELECT COALESCE(SUM(
                CASE WHEN at.normal_balance = 'credit' 
                    THEN jel.credit_amount - jel.debit_amount
                    ELSE jel.debit_amount - jel.credit_amount
                END
            ), 0) as revenue
            FROM journal_entry_lines jel
            JOIN chart_of_accounts coa ON jel.account_id = coa.id
            JOIN account_types at ON coa.account_type_id = at.id
            JOIN journal_entries je ON jel.journal_entry_id = je.id
            WHERE at.name = 'Revenue' 
              AND je.is_posted = TRUE
              AND je.entry_date BETWEEN ? AND ?
        """, (current_month_start, current_month_end)).fetchone()
        revenue_this_month = revenue_this_month_row['revenue'] if revenue_this_month_row else 0

        revenue_last_month_row = _execute_with_cursor(cursor, """
            SELECT COALESCE(SUM(
                CASE WHEN at.normal_balance = 'credit' 
                    THEN jel.credit_amount - jel.debit_amount
                    ELSE jel.debit_amount - jel.credit_amount
                END
            ), 0) as revenue
            FROM journal_entry_lines jel
            JOIN chart_of_accounts coa ON jel.account_id = coa.id
            JOIN account_types at ON coa.account_type_id = at.id
            JOIN journal_entries je ON jel.journal_entry_id = je.id
            WHERE at.name = 'Revenue' 
              AND je.is_posted = TRUE
              AND je.entry_date BETWEEN ? AND ?
        """, (prev_month_start, prev_month_end)).fetchone()
        revenue_last_month = revenue_last_month_row['revenue'] if revenue_last_month_row else 0

        invoice_info = _execute_with_cursor(cursor, """
            SELECT COUNT(*) as count, COALESCE(SUM(total_amount), 0) as total
            FROM invoices
            WHERE status != 'Paid'
        """).fetchone() or {'count': 0, 'total': 0}
        outstanding_invoices = invoice_info['total']
        open_invoice_count = invoice_info['count']

        open_invoices = _execute_with_cursor(cursor, """
            SELECT i.id, i.invoice_number, i.due_date, i.total_amount as balance_due,
                   i.status, c.name as customer_name, cur.symbol as currency_symbol
            FROM invoices i
            JOIN customers c ON i.customer_id = c.id
            JOIN currencies cur ON i.currency_id = cur.id
            WHERE i.status != 'Paid'
            ORDER BY i.due_date ASC
            LIMIT 5
        """).fetchall()

        bank_info = _execute_with_cursor(cursor, """
            SELECT COUNT(*) as count, COALESCE(SUM(balance), 0) as total
            FROM chart_of_accounts
            WHERE account_type_id = (SELECT id FROM account_types WHERE name = 'Asset')
              AND account_name LIKE '%bank%'
        """).fetchone() or {'count': 0, 'total': 0}
        cash_position = bank_info['total']
        bank_account_count = bank_info['count']

        unposted_row = _execute_with_cursor(cursor, """
            SELECT COUNT(*) as count
            FROM journal_entries
            WHERE is_posted = FALSE
        """).fetchone()
        unposted_journal_count = unposted_row['count'] if unposted_row else 0

        recent_activities = _execute_with_cursor(cursor, """
            SELECT je.id, je.entry_date, je.reference_number, jet.type_name, 
                   je.description, cur.symbol as currency_symbol,
                   (SELECT SUM(debit_amount) FROM journal_entry_lines WHERE journal_entry_id = je.id) as amount
            FROM journal_entries je
            JOIN journal_entry_types jet ON je.journal_entry_type_id = jet.id
            JOIN currencies cur ON je.currency_id = cur.id
            WHERE je.is_posted = TRUE
            ORDER BY je.entry_date DESC
            LIMIT 5
        """).fetchall()

        account_count_row = _execute_with_cursor(cursor, "SELECT COUNT(*) as count FROM chart_of_accounts WHERE is_active = TRUE").fetchone()
        account_count = account_count_row['count'] if account_count_row else 0

        journal_entry_count_row = _execute_with_cursor(cursor, "SELECT COUNT(*) as count FROM journal_entries").fetchone()
        journal_entry_count = journal_entry_count_row['count'] if journal_entry_count_row else 0

        top_accounts = _execute_with_cursor(cursor, """
            SELECT coa.id, coa.account_number, coa.account_name, coa.balance,
                   at.name as account_type, cur.symbol as currency_symbol
            FROM chart_of_accounts coa
            JOIN account_types at ON coa.account_type_id = at.id
            JOIN currencies cur ON coa.currency_id = cur.id
            WHERE coa.is_active = TRUE
            ORDER BY ABS(coa.balance) DESC
            LIMIT 5
        """).fetchall()

        current_period = _execute_with_cursor(cursor, """
            SELECT fp.id, fp.period_name, fp.start_date, fp.end_date, fy.year_name
            FROM fiscal_periods fp
            JOIN fiscal_years fy ON fp.fiscal_year_id = fy.id
            WHERE ? BETWEEN fp.start_date AND fp.end_date
            LIMIT 1
        """, (today.isoformat(),)).fetchone()

        for i in range(11, -1, -1):
            month_date = today.replace(day=1) - timedelta(days=i * 30)
            month_name = month_date.strftime('%b %Y')
            month_start = date(month_date.year, month_date.month, 1).isoformat()
            month_end = date(month_date.year, month_date.month + 1, 1).isoformat() if month_date.month < 12 else date(month_date.year + 1, 1, 1).isoformat()

            month_rev_row = _execute_with_cursor(cursor, """
                SELECT COALESCE(SUM(
                    CASE WHEN at.normal_balance = 'credit' 
                        THEN jel.credit_amount - jel.debit_amount
                        ELSE jel.debit_amount - jel.credit_amount
                    END
                ), 0) as revenue
                FROM journal_entry_lines jel
                JOIN chart_of_accounts coa ON jel.account_id = coa.id
                JOIN account_types at ON coa.account_type_id = at.id
                JOIN journal_entries je ON jel.journal_entry_id = je.id
                WHERE at.name = 'Revenue' 
                  AND je.is_posted = TRUE
                  AND je.entry_date BETWEEN ? AND ?
            """, (month_start, month_end)).fetchone()
            month_revenue = month_rev_row['revenue'] if month_rev_row else 0

            month_exp_row = _execute_with_cursor(cursor, """
                SELECT COALESCE(SUM(
                    CASE WHEN at.normal_balance = 'debit' 
                        THEN jel.debit_amount - jel.credit_amount
                        ELSE jel.credit_amount - jel.debit_amount
                    END
                ), 0) as expenses
                FROM journal_entry_lines jel
                JOIN chart_of_accounts coa ON jel.account_id = coa.id
                JOIN account_types at ON coa.account_type_id = at.id
                JOIN journal_entries je ON jel.journal_entry_id = je.id
                WHERE at.name = 'Expense' 
                  AND je.is_posted = TRUE
                  AND je.entry_date BETWEEN ? AND ?
            """, (month_start, month_end)).fetchone()
            month_expenses = month_exp_row['expenses'] if month_exp_row else 0

            months.append(month_name)
            chart_revenue.append(float(month_revenue))
            chart_expenses.append(float(month_expenses))

    revenue_change = round(((revenue_this_month - revenue_last_month) / revenue_last_month) * 100, 2) if revenue_last_month > 0 else (100 if revenue_this_month > 0 else 0)

    period_progress = 0
    period_days_left = 0
    if current_period:
        start_date = datetime.strptime(current_period['start_date'], '%Y-%m-%d').date()
        end_date = datetime.strptime(current_period['end_date'], '%Y-%m-%d').date()
        total_days = (end_date - start_date).days
        days_passed = (today - start_date).days
        if total_days > 0:
            period_progress = round((days_passed / total_days) * 100)
        period_days_left = (end_date - today).days
    else:
        current_period = {
            'period_name': 'Unknown',
            'year_name': '',
            'start_date': '',
            'end_date': ''
        }

    open_invoices_list = []
    for invoice in open_invoices:
        invoice_dict = dict(invoice)
        due_date_val = invoice_dict.get('due_date')
        if isinstance(due_date_val, str):
            due_date_obj = datetime.strptime(due_date_val, '%Y-%m-%d').date()
        else:
            due_date_obj = due_date_val
        invoice_dict['overdue_days'] = (today - due_date_obj).days if due_date_obj and due_date_obj < today else 0
        open_invoices_list.append(invoice_dict)

    return render_template('finance/finance_dashboard.html',
                           revenue_this_month=revenue_this_month,
                           revenue_change=revenue_change,
                           outstanding_invoices=outstanding_invoices,
                           open_invoice_count=open_invoice_count,
                           cash_position=cash_position,
                           bank_account_count=bank_account_count,
                           unposted_journal_count=unposted_journal_count,
                           recent_activities=recent_activities,
                           account_count=account_count,
                           journal_entry_count=journal_entry_count,
                           open_invoices=open_invoices_list,
                           top_accounts=top_accounts,
                           current_period=current_period,
                           period_progress=period_progress,
                           period_days_left=period_days_left,
                           chart_months=json.dumps(months),
                           chart_revenue=json.dumps(chart_revenue),
                           chart_expenses=json.dumps(chart_expenses))

@finance_bp.route('/chart-of-accounts', methods=['GET'])
def list_accounts():
    """List all accounts in the chart of accounts."""
    accounts = []
    with db_cursor() as cursor:
        accounts = [dict(row) for row in _execute_with_cursor(cursor, """
            SELECT coa.id, coa.account_number, coa.account_name, coa.description, 
                   coa.is_active, coa.balance, coa.parent_account_id,
                   at.name as account_type, at.normal_balance,
                   cur.currency_code, cur.symbol as currency_symbol
            FROM chart_of_accounts coa
            JOIN account_types at ON coa.account_type_id = at.id
            JOIN currencies cur ON coa.currency_id = cur.id
            ORDER BY coa.account_number
        """).fetchall()]

        for account in accounts:
            if account['parent_account_id']:
                parent = _execute_with_cursor(cursor, """
                    SELECT account_name FROM chart_of_accounts 
                    WHERE id = ?
                """, (account['parent_account_id'],)).fetchone()
                account['parent_account_name'] = parent['account_name'] if parent else "Unknown"
            else:
                account['parent_account_name'] = "None"

        account_types = _execute_with_cursor(cursor, "SELECT id, name FROM account_types ORDER BY name").fetchall()
        currencies = _execute_with_cursor(cursor, "SELECT id, currency_code, symbol FROM currencies ORDER BY currency_code").fetchall()

    return render_template('finance/chart_of_accounts.html',
                           accounts=accounts,
                           account_types=account_types,
                           currencies=currencies)


@finance_bp.route('/chart-of-accounts', methods=['POST'])
def add_account():
    """Add a new account to the chart of accounts."""
    account_number = request.form.get('account_number')
    account_name = request.form.get('account_name')
    account_type_id = request.form.get('account_type_id')
    parent_account_id = request.form.get('parent_account_id') or None
    description = request.form.get('description')
    currency_id = request.form.get('currency_id')

    if not all([account_number, account_name, account_type_id, currency_id]):
        flash('Missing required fields', 'error')
        return redirect(url_for('finance.list_accounts'))

    with db_cursor() as cursor:
        existing = _execute_with_cursor(cursor, "SELECT id FROM chart_of_accounts WHERE account_number = ?", (account_number,)).fetchone()
        if existing:
            flash('Account number already exists', 'error')
            return redirect(url_for('finance.list_accounts'))

    try:
        with db_cursor(commit=True) as cursor:
            _execute_with_cursor(cursor, """
                INSERT INTO chart_of_accounts 
                (account_number, account_name, account_type_id, parent_account_id, 
                 description, currency_id)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (account_number, account_name, account_type_id, parent_account_id,
                  description, currency_id))
        flash('Account created successfully', 'success')
    except Exception as e:
        flash(f'Error creating account: {str(e)}', 'error')

    return redirect(url_for('finance.list_accounts'))


@finance_bp.route('/chart-of-accounts/<int:account_id>', methods=['GET'])
def view_account(account_id):
    """View detailed information for a specific account."""
    with db_cursor() as cursor:
        account = _execute_with_cursor(cursor, """
            SELECT coa.id, coa.account_number, coa.account_name, coa.description, 
                   coa.is_active, coa.balance, coa.parent_account_id,
                   at.id as account_type_id, at.name as account_type, at.normal_balance,
                   cur.id as currency_id, cur.currency_code, cur.symbol as currency_symbol,
                   coa.created_at, coa.updated_at
            FROM chart_of_accounts coa
            JOIN account_types at ON coa.account_type_id = at.id
            JOIN currencies cur ON coa.currency_id = cur.id
            WHERE coa.id = ?
        """, (account_id,)).fetchone()

        if not account:
            flash('Account not found', 'error')
            return redirect(url_for('finance.list_accounts'))

        account_activity = _execute_with_cursor(cursor, """
            SELECT aal.id, aal.transaction_date, aal.debit_amount, aal.credit_amount, 
                   aal.balance_after, je.reference_number, je.description
            FROM account_activity_log aal
            JOIN journal_entries je ON aal.journal_entry_id = je.id
            WHERE aal.account_id = ?
            ORDER BY aal.transaction_date DESC, aal.id DESC
            LIMIT 50
        """, (account_id,)).fetchall()

        child_accounts = _execute_with_cursor(cursor, """
            SELECT id, account_number, account_name, balance
            FROM chart_of_accounts
            WHERE parent_account_id = ?
        """, (account_id,)).fetchall()

        account_types = _execute_with_cursor(cursor, "SELECT id, name FROM account_types ORDER BY name").fetchall()
        currencies = _execute_with_cursor(cursor, "SELECT id, currency_code, symbol FROM currencies ORDER BY currency_code").fetchall()

    return render_template('finance/account_detail.html',
                           account=account,
                           account_activity=account_activity,
                           child_accounts=child_accounts,
                           account_types=account_types,
                           currencies=currencies)


@finance_bp.route('/chart-of-accounts/<int:account_id>', methods=['POST'])
def update_account(account_id):
    """Update an existing account."""
    account_name = request.form.get('account_name')
    description = request.form.get('description')
    is_active = 'is_active' in request.form

    try:
        with db_cursor(commit=True) as cursor:
            _execute_with_cursor(cursor, """
                UPDATE chart_of_accounts
                SET account_name = ?, description = ?, is_active = ?
                WHERE id = ?
            """, (account_name, description, is_active, account_id))
        flash('Account updated successfully', 'success')
    except Exception as e:
        flash(f'Error updating account: {str(e)}', 'error')

    return redirect(url_for('finance.view_account', account_id=account_id))


# ---------------------- Journal Entry Routes ----------------------

@finance_bp.route('/journal-entries', methods=['GET'])
def list_journal_entries():
    """List all journal entries."""
    fiscal_periods = []
    journal_entry_types = []
    currencies = []
    journal_entries = []

    with db_cursor() as cursor:
        fiscal_periods = _execute_with_cursor(cursor, """
            SELECT fp.id, fp.period_name, fy.year_name
            FROM fiscal_periods fp
            JOIN fiscal_years fy ON fp.fiscal_year_id = fy.id
            ORDER BY fp.start_date DESC
        """).fetchall()

        journal_entry_types = _execute_with_cursor(cursor, "SELECT id, type_name FROM journal_entry_types ORDER BY type_name").fetchall()

    # Get filters from request
    fiscal_period_id = request.args.get('fiscal_period_id')
    journal_entry_type_id = request.args.get('journal_entry_type_id')
    search_query = request.args.get('search', '')

    # Build query with optional filters
    query = """
        SELECT je.id, je.entry_date, je.reference_number, je.description,
               jet.type_name, je.is_posted,
               fp.period_name, fy.year_name,
               u.username as created_by,
               (SELECT SUM(debit_amount) FROM journal_entry_lines WHERE journal_entry_id = je.id) as total_debit,
               c.currency_code
        FROM journal_entries je
        JOIN journal_entry_types jet ON je.journal_entry_type_id = jet.id
        JOIN fiscal_periods fp ON je.fiscal_period_id = fp.id
        JOIN fiscal_years fy ON fp.fiscal_year_id = fy.id
        JOIN users u ON je.created_by = u.id
        JOIN currencies c ON je.currency_id = c.id
        WHERE 1=1
    """

    params = []

    if fiscal_period_id:
        query += " AND je.fiscal_period_id = ?"
        params.append(fiscal_period_id)

    if journal_entry_type_id:
        query += " AND je.journal_entry_type_id = ?"
        params.append(journal_entry_type_id)

    if search_query:
        query += " AND (je.reference_number LIKE ? OR je.description LIKE ?)"
        params.extend([f'%{search_query}%', f'%{search_query}%'])

    query += " ORDER BY je.entry_date DESC, je.id DESC LIMIT 100"

    with db_cursor() as cursor:
        journal_entries = _execute_with_cursor(cursor, query, params).fetchall()
        currencies = _execute_with_cursor(cursor, "SELECT id, currency_code, symbol FROM currencies ORDER BY currency_code").fetchall()

    return render_template('finance/journal_entries.html',
                           journal_entries=journal_entries,
                           fiscal_periods=fiscal_periods,
                           journal_entry_types=journal_entry_types,
                           currencies=currencies,
                           selected_fiscal_period=fiscal_period_id,
                           selected_journal_entry_type=journal_entry_type_id,
                           search_query=search_query)


@finance_bp.route('/journal-entries', methods=['POST'])
def add_journal_entry():
    """Create a new journal entry."""
    if request.is_json:
        data = request.get_json()
    else:
        data = request.form

    entry_date = data.get('entry_date')
    fiscal_period_id = data.get('fiscal_period_id')
    journal_entry_type_id = data.get('journal_entry_type_id')
    reference_number = data.get('reference_number')
    description = data.get('description')
    currency_id = data.get('currency_id')
    lines = json.loads(data.get('lines', '[]')) if not request.is_json else data.get('lines', [])

    # Basic validation
    if not all([entry_date, fiscal_period_id, journal_entry_type_id, currency_id]) or not lines:
        return jsonify(success=False, error="Missing required fields"), 400

    # Validate balanced debits and credits
    total_debit = sum(float(line.get('debit_amount', 0)) for line in lines)
    total_credit = sum(float(line.get('credit_amount', 0)) for line in lines)

    if round(total_debit, 2) != round(total_credit, 2):
        return jsonify(success=False, error="Journal entry is not balanced. Debits must equal credits."), 400

    try:
        is_posted_raw = data.get('is_posted', False)
        is_posted = bool(is_posted_raw) if isinstance(is_posted_raw, bool) else str(is_posted_raw).lower() in ('true', '1', 'on', 'yes')

        with db_cursor(commit=True) as cursor:
            header_row = _execute_with_cursor(cursor, """
                INSERT INTO journal_entries
                (entry_date, fiscal_period_id, journal_entry_type_id, reference_number, 
                 description, currency_id, is_posted, created_by)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                RETURNING id
            """, (entry_date, fiscal_period_id, journal_entry_type_id, reference_number,
                  description, currency_id, False, 1)).fetchone()  # TODO: Replace 1 with actual user ID

            journal_entry_id = _get_inserted_id(header_row, cursor)

            for line in lines:
                account_id = line.get('account_id')
                line_description = line.get('description', '')
                debit_amount = float(line.get('debit_amount', 0))
                credit_amount = float(line.get('credit_amount', 0))

                _execute_with_cursor(cursor, """
                    INSERT INTO journal_entry_lines
                    (journal_entry_id, account_id, description, debit_amount, credit_amount)
                    VALUES (?, ?, ?, ?, ?)
                """, (journal_entry_id, account_id, line_description, debit_amount, credit_amount))

            if is_posted:
                _execute_with_cursor(cursor, """
                    UPDATE journal_entries
                    SET is_posted = TRUE
                    WHERE id = ?
                """, (journal_entry_id,))

                for line in lines:
                    account_id = line.get('account_id')
                    debit_amount = float(line.get('debit_amount', 0))
                    credit_amount = float(line.get('credit_amount', 0))

                    account_info = _execute_with_cursor(cursor, """
                        SELECT coa.balance, at.normal_balance
                        FROM chart_of_accounts coa
                        JOIN account_types at ON coa.account_type_id = at.id
                        WHERE coa.id = ?
                    """, (account_id,)).fetchone()

                    if not account_info:
                        continue

                    current_balance = account_info['balance']
                    normal_balance = account_info['normal_balance']

                    if normal_balance == 'debit':
                        new_balance = current_balance + debit_amount - credit_amount
                    else:  # normal_balance == 'credit'
                        new_balance = current_balance - debit_amount + credit_amount

                    _execute_with_cursor(cursor, """
                        UPDATE chart_of_accounts
                        SET balance = ?
                        WHERE id = ?
                    """, (new_balance, account_id))

                    _execute_with_cursor(cursor, """
                        INSERT INTO account_activity_log
                        (account_id, journal_entry_id, transaction_date, debit_amount, credit_amount, balance_after)
                        VALUES (?, ?, ?, ?, ?, ?)
                    """, (account_id, journal_entry_id, entry_date, debit_amount, credit_amount, new_balance))

        return jsonify(success=True, journal_entry_id=journal_entry_id), 201

    except Exception as e:
        return jsonify(success=False, error=str(e)), 500


@finance_bp.route('/journal-entries/<int:journal_entry_id>', methods=['GET'])
def view_journal_entry(journal_entry_id):
    """View a specific journal entry."""
    with db_cursor() as cursor:
        journal_entry = _execute_with_cursor(cursor, """
            SELECT je.id, je.entry_date, je.reference_number, je.description,
                   je.is_posted, je.fiscal_period_id, je.journal_entry_type_id,
                   je.currency_id, je.exchange_rate,
                   jet.type_name, fp.period_name, fy.year_name,
                   c.currency_code, c.symbol,
                   u.username as created_by, je.created_at
            FROM journal_entries je
            JOIN journal_entry_types jet ON je.journal_entry_type_id = jet.id
            JOIN fiscal_periods fp ON je.fiscal_period_id = fp.id
            JOIN fiscal_years fy ON fp.fiscal_year_id = fy.id
            JOIN users u ON je.created_by = u.id
            JOIN currencies c ON je.currency_id = c.id
            WHERE je.id = ?
        """, (journal_entry_id,)).fetchone()

        if not journal_entry:
            flash('Journal entry not found', 'error')
            return redirect(url_for('finance.list_journal_entries'))

        journal_entry_lines = _execute_with_cursor(cursor, """
            SELECT jel.id, jel.account_id, jel.description,
                   jel.debit_amount, jel.credit_amount,
                   coa.account_number, coa.account_name
            FROM journal_entry_lines jel
            JOIN chart_of_accounts coa ON jel.account_id = coa.id
            WHERE jel.journal_entry_id = ?
            ORDER BY jel.id
        """, (journal_entry_id,)).fetchall()

    total_debit = sum(line['debit_amount'] for line in journal_entry_lines)
    total_credit = sum(line['credit_amount'] for line in journal_entry_lines)

    return render_template('finance/journal_entry_detail.html',
                           journal_entry=journal_entry,
                           journal_entry_lines=journal_entry_lines,
                           total_debit=total_debit,
                           total_credit=total_credit)


@finance_bp.route('/journal-entries/<int:journal_entry_id>/post', methods=['POST'])
def post_journal_entry(journal_entry_id):
    """Post a journal entry, updating account balances."""
    try:
        with db_cursor() as cursor:
            je_info = _execute_with_cursor(cursor, """
                SELECT is_posted, entry_date 
                FROM journal_entries 
                WHERE id = ?
            """, (journal_entry_id,)).fetchone()

        if not je_info:
            return jsonify(success=False, error="Journal entry not found"), 404

        if je_info['is_posted']:
            return jsonify(success=False, error="Journal entry is already posted"), 400

        entry_date = je_info['entry_date']

        with db_cursor(commit=True) as cursor:
            lines = _execute_with_cursor(cursor, """
                SELECT jel.id, jel.account_id, jel.debit_amount, jel.credit_amount
                FROM journal_entry_lines jel
                WHERE jel.journal_entry_id = ?
            """, (journal_entry_id,)).fetchall()

            for line in lines:
                account_id = line['account_id']
                debit_amount = line['debit_amount']
                credit_amount = line['credit_amount']

                account_info = _execute_with_cursor(cursor, """
                    SELECT coa.balance, at.normal_balance
                    FROM chart_of_accounts coa
                    JOIN account_types at ON coa.account_type_id = at.id
                    WHERE coa.id = ?
                """, (account_id,)).fetchone()

                if not account_info:
                    continue

                current_balance = account_info['balance']
                normal_balance = account_info['normal_balance']

                if normal_balance == 'debit':
                    new_balance = current_balance + debit_amount - credit_amount
                else:  # normal_balance == 'credit'
                    new_balance = current_balance - debit_amount + credit_amount

                _execute_with_cursor(cursor, """
                    UPDATE chart_of_accounts
                    SET balance = ?
                    WHERE id = ?
                """, (new_balance, account_id))

                _execute_with_cursor(cursor, """
                    INSERT INTO account_activity_log
                    (account_id, journal_entry_id, transaction_date, debit_amount, credit_amount, balance_after)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (account_id, journal_entry_id, entry_date, debit_amount, credit_amount, new_balance))

            _execute_with_cursor(cursor, """
                UPDATE journal_entries
                SET is_posted = TRUE
                WHERE id = ?
            """, (journal_entry_id,))

        return jsonify(success=True, message="Journal entry posted successfully"), 200

    except Exception as e:
        return jsonify(success=False, error=str(e)), 500


@finance_bp.route('/accounts/search', methods=['GET'])
def search_accounts():
    """Search for accounts by number or name."""
    search_term = request.args.get('term', '')

    if not search_term or len(search_term) < 2:
        return jsonify([])

    with db_cursor() as cursor:
        accounts = _execute_with_cursor(cursor, """
            SELECT id, account_number, account_name, 
                   (account_number || ' - ' || account_name) as display_name
            FROM chart_of_accounts
            WHERE is_active = TRUE
              AND (account_number LIKE ? OR account_name LIKE ?)
            ORDER BY account_number
            LIMIT 20
        """, (f'%{search_term}%', f'%{search_term}%')).fetchall()

    return jsonify([{
        'id': account['id'],
        'account_number': account['account_number'],
        'account_name': account['account_name'],
        'display_name': account['display_name']
    } for account in accounts])


# ---------------------- Financial Reports Routes ----------------------

@finance_bp.route('/reports/trial-balance', methods=['GET'])
def trial_balance_report():
    """Generate a trial balance report."""
    # Get parameters
    as_of_date = request.args.get('as_of_date', date.today().isoformat())
    fiscal_period_id = request.args.get('fiscal_period_id')

    with db_cursor() as cursor:
        fiscal_periods = _execute_with_cursor(cursor, """
            SELECT fp.id, fp.period_name, fy.year_name
            FROM fiscal_periods fp
            JOIN fiscal_years fy ON fp.fiscal_year_id = fy.id
            ORDER BY fp.start_date DESC
        """).fetchall()

        query = """
            SELECT coa.account_number, coa.account_name, 
                   at.name as account_type, at.normal_balance,
                   coa.balance,
                   cur.currency_code, cur.symbol
            FROM chart_of_accounts coa
            JOIN account_types at ON coa.account_type_id = at.id
            JOIN currencies cur ON coa.currency_id = cur.id
            WHERE coa.is_active = TRUE
        """

        params = []

        if fiscal_period_id:
            period_info = _execute_with_cursor(cursor, """
                SELECT end_date FROM fiscal_periods WHERE id = ?
            """, (fiscal_period_id,)).fetchone()
            if period_info:
                as_of_date = period_info['end_date']

        query += " ORDER BY coa.account_number"

        accounts = _execute_with_cursor(cursor, query, params).fetchall()

    # Calculate report totals
    total_debit = 0
    total_credit = 0

    for account in accounts:
        balance = account['balance']
        if balance == 0:
            account['debit_balance'] = 0
            account['credit_balance'] = 0
        elif account['normal_balance'] == 'debit':
            account['debit_balance'] = balance if balance > 0 else 0
            account['credit_balance'] = -balance if balance < 0 else 0
            total_debit += account['debit_balance']
            total_credit += account['credit_balance']
        else:  # normal_balance == 'credit'
            account['debit_balance'] = -balance if balance < 0 else 0
            account['credit_balance'] = balance if balance > 0 else 0
            total_debit += account['debit_balance']
            total_credit += account['credit_balance']

    return render_template('finance/reports/trial_balance.html',
                           accounts=accounts,
                           fiscal_periods=fiscal_periods,
                           selected_fiscal_period=fiscal_period_id,
                           as_of_date=as_of_date,
                           total_debit=total_debit,
                           total_credit=total_credit)


@finance_bp.route('/reports/income-statement', methods=['GET'])
def income_statement_report():
    """Generate an income statement report."""
    # Get parameters
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    fiscal_period_id = request.args.get('fiscal_period_id')

    with db_cursor() as cursor:
        fiscal_periods = _execute_with_cursor(cursor, """
            SELECT fp.id, fp.period_name, fy.year_name, fp.start_date, fp.end_date
            FROM fiscal_periods fp
            JOIN fiscal_years fy ON fp.fiscal_year_id = fy.id
            ORDER BY fp.start_date DESC
        """).fetchall()

        if fiscal_period_id:
            period_info = _execute_with_cursor(cursor, """
                SELECT start_date, end_date FROM fiscal_periods WHERE id = ?
            """, (fiscal_period_id,)).fetchone()
            if period_info:
                start_date = period_info['start_date']
                end_date = period_info['end_date']

    if not start_date or not end_date:
        today = date.today()
        start_date = date(today.year, today.month, 1).isoformat()
        end_date = date(today.year, 12, 31).isoformat() if today.month == 12 else date(today.year, today.month + 1, 1).isoformat()

    with db_cursor() as cursor:
        revenue_accounts = _execute_with_cursor(cursor, """
            SELECT coa.id, coa.account_number, coa.account_name, 
                   COALESCE(SUM(
                       CASE 
                           WHEN je.entry_date BETWEEN ? AND ? THEN 
                               CASE WHEN at.normal_balance = 'credit' 
                                    THEN jel.credit_amount - jel.debit_amount
                                    ELSE jel.debit_amount - jel.credit_amount
                               END
                           ELSE 0
                       END
                   ), 0) as period_amount
            FROM chart_of_accounts coa
            JOIN account_types at ON coa.account_type_id = at.id
            LEFT JOIN journal_entry_lines jel ON coa.id = jel.account_id
            LEFT JOIN journal_entries je ON jel.journal_entry_id = je.id AND je.is_posted = TRUE
            WHERE at.name = 'Revenue'
            GROUP BY coa.id
            ORDER BY coa.account_number
        """, (start_date, end_date)).fetchall()

        expense_accounts = _execute_with_cursor(cursor, """
            SELECT coa.id, coa.account_number, coa.account_name, 
                   COALESCE(SUM(
                       CASE 
                           WHEN je.entry_date BETWEEN ? AND ? THEN 
                               CASE WHEN at.normal_balance = 'debit' 
                                    THEN jel.debit_amount - jel.credit_amount
                                    ELSE jel.credit_amount - jel.debit_amount
                               END
                           ELSE 0
                       END
                   ), 0) as period_amount
            FROM chart_of_accounts coa
            JOIN account_types at ON coa.account_type_id = at.id
            LEFT JOIN journal_entry_lines jel ON coa.id = jel.account_id
            LEFT JOIN journal_entries je ON jel.journal_entry_id = je.id AND je.is_posted = TRUE
            WHERE at.name = 'Expense'
            GROUP BY coa.id
            ORDER BY coa.account_number
        """, (start_date, end_date)).fetchall()

    total_revenue = sum(account['period_amount'] for account in revenue_accounts)
    total_expenses = sum(account['period_amount'] for account in expense_accounts)
    net_income = total_revenue - total_expenses

    return render_template('finance/reports/income_statement.html',
                           revenue_accounts=revenue_accounts,
                           expense_accounts=expense_accounts,
                           total_revenue=total_revenue,
                           total_expenses=total_expenses,
                           net_income=net_income,
                           start_date=start_date,
                           end_date=end_date,
                           fiscal_periods=fiscal_periods,
                           selected_fiscal_period=fiscal_period_id)


@finance_bp.route('/reports/balance-sheet', methods=['GET'])
def balance_sheet_report():
    """Generate a balance sheet report."""
    # Get parameters
    as_of_date = request.args.get('as_of_date', date.today().isoformat())
    fiscal_period_id = request.args.get('fiscal_period_id')

    with db_cursor() as cursor:
        fiscal_periods = _execute_with_cursor(cursor, """
            SELECT fp.id, fp.period_name, fy.year_name, fp.end_date
            FROM fiscal_periods fp
            JOIN fiscal_years fy ON fp.fiscal_year_id = fy.id
            ORDER BY fp.start_date DESC
        """).fetchall()

        if fiscal_period_id:
            period_info = _execute_with_cursor(cursor, """
                SELECT end_date FROM fiscal_periods WHERE id = ?
            """, (fiscal_period_id,)).fetchone()
            if period_info:
                as_of_date = period_info['end_date']

        asset_accounts = _execute_with_cursor(cursor, """
            SELECT coa.id, coa.account_number, coa.account_name, coa.balance
            FROM chart_of_accounts coa
            JOIN account_types at ON coa.account_type_id = at.id
            WHERE at.name = 'Asset' AND coa.is_active = TRUE
            ORDER BY coa.account_number
        """).fetchall()

        liability_accounts = _execute_with_cursor(cursor, """
            SELECT coa.id, coa.account_number, coa.account_name, coa.balance
            FROM chart_of_accounts coa
            JOIN account_types at ON coa.account_type_id = at.id
            WHERE at.name = 'Liability' AND coa.is_active = TRUE
            ORDER BY coa.account_number
        """).fetchall()

        equity_accounts = _execute_with_cursor(cursor, """
            SELECT coa.id, coa.account_number, coa.account_name, coa.balance
            FROM chart_of_accounts coa
            JOIN account_types at ON coa.account_type_id = at.id
            WHERE at.name = 'Equity' AND coa.is_active = TRUE
            ORDER BY coa.account_number
        """).fetchall()

    total_assets = sum(account['balance'] for account in asset_accounts)
    total_liabilities = sum(account['balance'] for account in liability_accounts)
    total_equity = sum(account['balance'] for account in equity_accounts)

    # Calculate retained earnings (if needed)
    # This is a simplification. In a real system, you'd have a more complex calculation
    net_income = 0

    # Calculate liabilities + equity
    total_liabilities_equity = total_liabilities + total_equity + net_income

    return render_template('finance/reports/balance_sheet.html',
                           asset_accounts=asset_accounts,
                           liability_accounts=liability_accounts,
                           equity_accounts=equity_accounts,
                           total_assets=total_assets,
                           total_liabilities=total_liabilities,
                           total_equity=total_equity,
                           net_income=net_income,
                           total_liabilities_equity=total_liabilities_equity,
                           as_of_date=as_of_date,
                           fiscal_periods=fiscal_periods,
                           selected_fiscal_period=fiscal_period_id)


# ---------------------- Fiscal Period Management Routes ----------------------

@finance_bp.route('/fiscal-years', methods=['GET'])
def list_fiscal_years():
    """List all fiscal years and periods."""
    with db_cursor() as cursor:
        fiscal_years = [dict(row) for row in _execute_with_cursor(cursor, """
            SELECT id, year_name, start_date, end_date, is_closed
            FROM fiscal_years
            ORDER BY start_date DESC
        """).fetchall()]

        for year in fiscal_years:
            year['periods'] = _execute_with_cursor(cursor, """
                SELECT id, period_name, start_date, end_date, is_closed
                FROM fiscal_periods
                WHERE fiscal_year_id = ?
                ORDER BY start_date
            """, (year['id'],)).fetchall()

    return render_template('finance/fiscal_years.html', fiscal_years=fiscal_years)


@finance_bp.route('/fiscal-years', methods=['POST'])
def add_fiscal_year():
    """Add a new fiscal year with periods."""
    year_name = request.form.get('year_name')
    start_date = request.form.get('start_date')
    end_date = request.form.get('end_date')
    period_type = request.form.get('period_type', 'monthly')  # 'monthly', 'quarterly'

    if not all([year_name, start_date, end_date]):
        flash('Missing required fields', 'error')
        return redirect(url_for('finance.list_fiscal_years'))

    # Parse dates
    try:
        start_date_obj = datetime.strptime(start_date, '%Y-%m-%d').date()
        end_date_obj = datetime.strptime(end_date, '%Y-%m-%d').date()

        if end_date_obj <= start_date_obj:
            flash('End date must be after start date', 'error')
            return redirect(url_for('finance.list_fiscal_years'))

    except ValueError:
        flash('Invalid date format', 'error')
        return redirect(url_for('finance.list_fiscal_years'))

    try:
        with db_cursor(commit=True) as cursor:
            fiscal_year_row = _execute_with_cursor(cursor, """
                INSERT INTO fiscal_years (year_name, start_date, end_date)
                VALUES (?, ?, ?)
                RETURNING id
            """, (year_name, start_date, end_date)).fetchone()

            fiscal_year_id = _get_inserted_id(fiscal_year_row, cursor)

            if period_type == 'monthly':
                current_date = start_date_obj
                month_count = 1

                while current_date <= end_date_obj:
                    next_month = date(current_date.year + 1, 1, 1) if current_date.month == 12 else date(current_date.year, current_date.month + 1, 1)
                    period_end = next_month - timedelta(days=1)
                    if period_end > end_date_obj:
                        period_end = end_date_obj

                    period_name = f"Month {month_count}"
                    _execute_with_cursor(cursor, """
                        INSERT INTO fiscal_periods 
                        (fiscal_year_id, period_name, start_date, end_date)
                        VALUES (?, ?, ?, ?)
                    """, (fiscal_year_id, period_name, current_date.isoformat(), period_end.isoformat()))

                    month_count += 1
                    current_date = next_month
                    if current_date > end_date_obj:
                        break

            elif period_type == 'quarterly':
                current_date = start_date_obj
                quarter_count = 1

                while current_date <= end_date_obj:
                    month = current_date.month
                    quarter_end_month = ((month - 1) // 3 * 3) + 3
                    next_quarter = date(current_date.year + 1, 1, 1) if quarter_end_month == 12 else date(current_date.year, quarter_end_month + 1, 1)
                    period_end = next_quarter - timedelta(days=1)

                    if period_end > end_date_obj:
                        period_end = end_date_obj

                    period_name = f"Quarter {quarter_count}"
                    _execute_with_cursor(cursor, """
                        INSERT INTO fiscal_periods 
                        (fiscal_year_id, period_name, start_date, end_date)
                        VALUES (?, ?, ?, ?)
                    """, (fiscal_year_id, period_name, current_date.isoformat(), period_end.isoformat()))

                    quarter_count += 1
                    current_date = next_quarter
                    if current_date > end_date_obj:
                        break

        flash('Fiscal year created successfully', 'success')

    except Exception as e:
        flash(f'Error creating fiscal year: {str(e)}', 'error')

    return redirect(url_for('finance.list_fiscal_years'))


@finance_bp.route('/fiscal-periods/<int:period_id>/close', methods=['POST'])
def close_fiscal_period(period_id):
    """Close a fiscal period to prevent further posting."""
    try:
        with db_cursor(commit=True) as cursor:
            period = _execute_with_cursor(cursor, "SELECT id, period_name FROM fiscal_periods WHERE id = ?", (period_id,)).fetchone()
            if not period:
                return jsonify(success=False, error="Fiscal period not found"), 404

            _execute_with_cursor(cursor, """
                UPDATE fiscal_periods
                SET is_closed = TRUE
                WHERE id = ?
            """, (period_id,))

            return jsonify(success=True, message=f"Period '{period['period_name']}' closed successfully")

    except Exception as e:
        return jsonify(success=False, error=str(e)), 500
