import os
import sqlite3
from contextlib import contextmanager
from typing import Iterator, Optional

# Default SQLite path; override with SQLITE_PATH or a DATABASE_URL for Postgres.
DATABASE = os.getenv('SQLITE_PATH', 'database.db')


def _database_url() -> str:
    return os.getenv('DATABASE_URL', '')  # e.g. postgresql://user:pass@host:5432/dbname


def _using_postgres() -> bool:
    database_url = _database_url()
    return bool(database_url and database_url.startswith(('postgres://', 'postgresql://')))


def get_currency_rate_column() -> str:
    return 'exchange_rate_to_base' if _using_postgres() else 'exchange_rate_to_eur'


CURRENCY_RATE_COLUMN = get_currency_rate_column()

def _get_sqlite_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row  # Return rows that behave like dicts
    # Keep some sensible defaults on for consistency
    conn.execute('PRAGMA foreign_keys = ON')
    return conn


def _get_postgres_connection():
    try:
        import psycopg2
        import psycopg2.extras
        import psycopg2.pool
    except ImportError as exc:
        raise RuntimeError(
            "Postgres requested but psycopg2/psycopg2-binary is not installed. "
            "Install it and set DATABASE_URL (postgresql://...)."
        ) from exc

    database_url = _database_url()
    if not database_url:
        raise RuntimeError("DATABASE_URL is required for Postgres connections.")

    pool = _get_postgres_pool(database_url)
    try:
        conn = pool.getconn()
    except Exception:
        # Pool exhausted; fall back to a direct connection to avoid hard failure.
        conn = psycopg2.connect(database_url, cursor_factory=psycopg2.extras.RealDictCursor)
        return conn, None
    if conn.closed:
        try:
            conn = pool.getconn()
        except Exception:
            conn = psycopg2.connect(database_url, cursor_factory=psycopg2.extras.RealDictCursor)
            return conn, None
    return conn, pool


_PG_POOL = None


def _get_postgres_pool(database_url: str):
    """Create or return a global PostgreSQL connection pool."""
    global _PG_POOL
    if _PG_POOL is None:
        import psycopg2.pool
        import psycopg2.extras
        _PG_POOL = psycopg2.pool.ThreadedConnectionPool(
            minconn=1,
            maxconn=30,
            dsn=database_url,
            cursor_factory=psycopg2.extras.RealDictCursor
        )
    return _PG_POOL


class PostgresConnectionWrapper:
    """
    Wrapper around a psycopg2 connection that auto-translates SQLite-style '?' placeholders
    to PostgreSQL '%s' placeholders, so existing code works unchanged.
    """
    def __init__(self, conn, pool=None):
        self._conn = conn
        self._pool = pool

    def _translate_query(self, query: str) -> str:
        """Convert ? placeholders to %s for PostgreSQL."""
        return query.replace('?', '%s')

    def execute(self, query: str, params=None):
        """Execute query with automatic placeholder translation."""
        translated = self._translate_query(query)
        cur = self._conn.cursor()
        if params is None:
            cur.execute(translated)
        else:
            cur.execute(translated, params)
        return cur

    def cursor(self):
        """Return a wrapped cursor that translates queries."""
        return PostgresCursorWrapper(self._conn.cursor(), self._translate_query)

    def commit(self):
        return self._conn.commit()

    def rollback(self):
        return self._conn.rollback()

    def close(self):
        if self._pool is not None:
            return self._pool.putconn(self._conn)
        return self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False


class PostgresCursorWrapper:
    """Wrapper around a psycopg2 cursor that auto-translates placeholders."""
    def __init__(self, cursor, translate_fn):
        self._cursor = cursor
        self._translate = translate_fn

    def execute(self, query: str, params=None):
        translated = self._translate(query)
        if params is None:
            return self._cursor.execute(translated)
        return self._cursor.execute(translated, params)

    def executemany(self, query: str, params_list):
        translated = self._translate(query)
        return self._cursor.executemany(translated, params_list)

    def fetchone(self):
        return self._cursor.fetchone()

    def fetchall(self):
        return self._cursor.fetchall()

    def fetchmany(self, size=None):
        return self._cursor.fetchmany(size)

    def close(self):
        return self._cursor.close()

    @property
    def lastrowid(self):
        # PostgreSQL doesn't have lastrowid, but we can try to get it from RETURNING clause
        # For compatibility, return None or implement RETURNING in queries
        return getattr(self._cursor, 'lastrowid', None)

    @property
    def rowcount(self):
        return self._cursor.rowcount

    @property
    def description(self):
        return self._cursor.description


def get_db_connection():
    """Return a database connection, SQLite by default, Postgres when DATABASE_URL is set.
    
    For PostgreSQL, returns a wrapper that auto-translates '?' placeholders to '%s',
    so existing code using SQLite-style queries works unchanged.
    """
    if _using_postgres():
        raw_conn, pool = _get_postgres_connection()
        return PostgresConnectionWrapper(raw_conn, pool=pool)
    return _get_sqlite_connection()


def _prepare_query(query: str, is_pg: bool) -> str:
    """
    Translate SQLite-style '?' placeholders to psycopg2 '%s' placeholders for Postgres.
    Leaves queries untouched for SQLite.
    """
    if not is_pg:
        return query
    return query.replace('?', '%s')


def _execute_base(query: str, params=None, many: bool = False, fetch: Optional[str] = None, commit: bool = False):
    """
    Execute a query with optional fetch behavior.
    fetch: None (no fetch), 'one', or 'all'
    many: if True, use executemany
    """
    is_pg = _using_postgres()
    prepared_query = _prepare_query(query, is_pg)
    with db_cursor(commit=commit) as cur:
        if many:
            cur.executemany(prepared_query, params or [])
        else:
            if params is None:
                cur.execute(prepared_query)
            else:
                cur.execute(prepared_query, params)

        if fetch == 'one':
            return cur.fetchone()
        if fetch == 'all':
            return cur.fetchall()
        return None


def execute(query: str, params=None, *, fetch: Optional[str] = None, many: bool = False, commit: bool = False):
    """
    Convenience wrapper around _execute_base for legacy call sites.
    """
    return _execute_base(query, params=params, many=many, fetch=fetch, commit=commit)


def _execute_with_cursor(cursor, query: str, params=None):
    """
    Execute a query using an existing cursor with automatic placeholder translation.
    Used when you already have a cursor from db_cursor() context manager.
    """
    is_pg = _using_postgres()
    prepared_query = _prepare_query(query, is_pg)
    if params is None:
        cursor.execute(prepared_query)
    else:
        cursor.execute(prepared_query, params)
    return cursor


@contextmanager
def db_cursor(commit: bool = False) -> Iterator:
    """
    Context manager that yields a cursor and handles commit/rollback.
    Works for both SQLite and Postgres connections provided by get_db_connection().
    """
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        yield cur
        if commit:
            conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()

def create_tables():
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # Create customers table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS customers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                primary_contact_id INTEGER,
                payment_terms TEXT,
                incoterms TEXT,
                FOREIGN KEY (primary_contact_id) REFERENCES contacts(id)
            )
        ''')

        # Create contacts table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS contacts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                customer_id INTEGER,
                name TEXT NOT NULL,
                email TEXT NOT NULL,
                FOREIGN KEY (customer_id) REFERENCES customers(id)
            )
        ''')

        # Create salespeople table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS salespeople (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT
            )
        ''')

        # Create RFQs table with email column included
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS rfqs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entered_date TEXT NOT NULL,
                customer_id INTEGER NOT NULL,
                contact_id INTEGER,
                customer_ref TEXT DEFAULT '',
                currency TEXT DEFAULT 'EUR',
                status TEXT DEFAULT 'new',
                email TEXT,
                salesperson_id INTEGER,
                FOREIGN KEY (customer_id) REFERENCES customers(id),
                FOREIGN KEY (contact_id) REFERENCES contacts(id),
                FOREIGN KEY (salesperson_id) REFERENCES salespeople(id)
            )
        ''')

        # Create suppliers table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS suppliers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                contact_name TEXT,
                contact_email TEXT,
                contact_phone TEXT,
                buffer INTEGER DEFAULT 0,
                currency TEXT DEFAULT 'EUR'
            )
        ''')

        # Create RFQ lines table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS rfq_lines (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                rfq_id INTEGER,
                line_number TEXT NOT NULL,
                part_number TEXT NOT NULL,
                quantity INTEGER NOT NULL,
                manufacturer TEXT,
                suggested_suppliers TEXT,
                chosen_supplier INTEGER,
                cost REAL,
                supplier_lead_time INTEGER,
                margin REAL,
                price REAL,
                lead_time INTEGER,
                line_value REAL,
                note TEXT,
                internal_notes TEXT,
                FOREIGN KEY (rfq_id) REFERENCES rfqs(id),
                FOREIGN KEY (chosen_supplier) REFERENCES suppliers(id)
            )
        ''')

        # Create part_numbers table with composite unique constraint
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS part_numbers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                part_number TEXT NOT NULL,
                base_part_number TEXT NOT NULL,
                system_part_number TEXT,
                manufacturer TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(part_number, manufacturer)
            )
        ''')

        # Create alternative_part_numbers table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS alternative_part_numbers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                part_number_id INTEGER NOT NULL,
                customer TEXT NOT NULL,
                customer_part_number TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (part_number_id) REFERENCES part_numbers(id) ON DELETE CASCADE
            )
        ''')

        # Create manufacturers table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS manufacturers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL
            )
        ''')

        # Create part_manufacturers table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS part_manufacturers (
                part_id INTEGER,
                manufacturer_id INTEGER,
                FOREIGN KEY (part_id) REFERENCES part_numbers(id),
                FOREIGN KEY (manufacturer_id) REFERENCES manufacturers(id),
                PRIMARY KEY (part_id, manufacturer_id)
            )
        ''')

        # Create requisitions table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS requisitions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                rfq_id INTEGER,
                supplier_id INTEGER,
                date TEXT,
                base_part_number TEXT,
                quantity INTEGER,
                FOREIGN KEY (rfq_id) REFERENCES rfqs(id),
                FOREIGN KEY (supplier_id) REFERENCES suppliers(id)
            )
        ''')

        # Create customer_part_numbers table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS customer_part_numbers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                base_part_number TEXT NOT NULL,
                customer_part_number TEXT NOT NULL,
                customer_id INTEGER NOT NULL,
                FOREIGN KEY (customer_id) REFERENCES customers(id)
            )
        ''')

        conn.commit()
    except sqlite3.Error as e:
        print(f"An error occurred: {e}")
    finally:
        if conn:
            conn.close()

if __name__ == '__main__':
    create_tables()
