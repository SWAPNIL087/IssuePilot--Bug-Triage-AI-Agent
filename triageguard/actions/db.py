"""Run runbook queries in one transaction. SQLite for development and tests, Postgres (Neon etc.) or Azure SQL in the cloud."""
import re
import sqlite3
import struct
from datetime import date, datetime
from decimal import Decimal

from triageguard.actions.runbook import ActionError, Runbook

# Placeholder styles of the supported drivers.
NAMED, QMARK, PYFORMAT = "named", "qmark", "pyformat"


def connect(url: str):
    """Returns (connection, placeholder style, uses explicit BEGIN)."""
    scheme, _, rest = url.partition("://")
    if scheme == "sqlite":
        path = rest[1:] if rest.startswith("/") else rest  # sqlite:///relative.db, sqlite:///C:/abs.db, sqlite:////abs.db
        return sqlite3.connect(path, isolation_level=None), NAMED, True
    if scheme in ("postgresql", "postgres"):  # e.g. a Neon connection string (needs sslmode=require)
        import psycopg

        return psycopg.connect(url, prepare_threshold=None), PYFORMAT, False  # no prepared statements: pooler-safe
    if scheme == "azuresql":  # azuresql://<server>.database.windows.net/<database>, Managed Identity
        import pyodbc
        from azure.identity import DefaultAzureCredential

        server, _, database = rest.partition("/")
        token = DefaultAzureCredential().get_token("https://database.windows.net/.default").token.encode("utf-16-le")
        conn = pyodbc.connect(
            f"Driver={{ODBC Driver 18 for SQL Server}};Server={server};Database={database};Encrypt=yes;",
            attrs_before={1256: struct.pack(f"<I{len(token)}s", len(token), token)},  # SQL_COPT_SS_ACCESS_TOKEN
            autocommit=False,
        )
        return conn, QMARK, False
    raise ActionError(f"unsupported database url scheme: {scheme!r}")


def _bind(sql: str, params: dict, style: str):
    if style == NAMED:
        return sql, params
    names = re.findall(r":(\w+)", sql)
    if style == QMARK:
        return re.sub(r":\w+", "?", sql), [params[n] for n in names]
    return re.sub(r":(\w+)", r"%(\1)s", sql), {n: params[n] for n in names}


def _plain(value):
    """JSON-friendly values for rows returned to the caller."""
    return str(value) if isinstance(value, (datetime, date, Decimal, bytes)) else value


def run(url: str, book: Runbook, params: dict) -> list[dict]:
    """Execute every query of the runbook atomically. Any unexpected row count rolls everything back."""
    conn, style, explicit = connect(url)
    rows: list[dict] = []
    try:
        if explicit:
            conn.execute("BEGIN IMMEDIATE")
        for query in book.queries:
            sql, args = _bind(query.sql, params, style)
            cur = conn.cursor()
            cur.execute(sql, args)
            if query.returns_rows:
                columns = [c[0] for c in cur.description]
                rows = [{c: _plain(v) for c, v in zip(columns, r)} for r in cur.fetchall()]
                count = len(rows)
            else:
                count = cur.rowcount
            if query.expect_rows is not None and count != query.expect_rows:
                raise ActionError(f"query '{query.name}' matched {count} rows, expected {query.expect_rows}")
        conn.execute("COMMIT") if explicit else conn.commit()
        return rows
    except ActionError:
        _rollback(conn, explicit)
        raise
    except Exception as e:  # driver errors, including a duplicate request_id
        _rollback(conn, explicit)
        raise ActionError(f"database error: {e}") from e
    finally:
        conn.close()


def _rollback(conn, explicit: bool) -> None:
    try:
        conn.execute("ROLLBACK") if explicit else conn.rollback()
    except Exception:
        pass