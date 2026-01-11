import os
from contextlib import contextmanager
from typing import Iterator, Optional


def _database_url() -> str:
    url = os.getenv('DATABASE_URL', '')
    if not url:
        raise RuntimeError("DATABASE_URL environment variable is required for PostgreSQL connection.")
    return url


def _using_postgres() -> bool:
    """Always returns True since we're using PostgreSQL exclusively."""
    return True


# Currency rate column for PostgreSQL
CURRENCY_RATE_COLUMN = 'exchange_rate_to_base'


def get_currency_rate_column() -> str:
    """Returns the currency rate column name for PostgreSQL."""
    return CURRENCY_RATE_COLUMN


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
    """Return a PostgreSQL database connection.

    Returns a wrapper that auto-translates '?' placeholders to '%s',
    so existing code using SQLite-style queries works unchanged.
    """
    raw_conn, pool = _get_postgres_connection()
    return PostgresConnectionWrapper(raw_conn, pool=pool)


def _prepare_query(query: str) -> str:
    """
    Translate SQLite-style '?' placeholders to psycopg2 '%s' placeholders.
    """
    return query.replace('?', '%s')


def _execute_base(query: str, params=None, many: bool = False, fetch: Optional[str] = None, commit: bool = False):
    """
    Execute a query with optional fetch behavior.
    fetch: None (no fetch), 'one', or 'all'
    many: if True, use executemany
    """
    prepared_query = _prepare_query(query)
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
    prepared_query = _prepare_query(query)
    if params is None:
        cursor.execute(prepared_query)
    else:
        cursor.execute(prepared_query, params)
    return cursor


@contextmanager
def db_cursor(commit: bool = False) -> Iterator:
    """
    Context manager that yields a cursor and handles commit/rollback.
    Works with PostgreSQL connections provided by get_db_connection().
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
    """
    Legacy function for SQLite table creation.
    For PostgreSQL, use schema.sql instead.
    """
    raise NotImplementedError(
        "Table creation is handled by schema.sql for PostgreSQL. "
        "Run: psql $DATABASE_URL -f schema.sql"
    )
