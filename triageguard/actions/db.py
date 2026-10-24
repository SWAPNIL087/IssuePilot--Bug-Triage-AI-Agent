"""Run runbook queries in one transaction. SQLite for development and tests, Azure SQL (Managed Identity) in the cloud."""
import re
import sqlite3
import struct
from pathlib import Path

from triageguard.actions.runbook import ActionError, Runbook


def connect(url: str):
    """Returns (connection, qmark). qmark=True means the driver wants ? placeholders."""
    scheme, _, rest = url.partition("://")
    if scheme == "sqlite":
        path = rest[1:] if rest.startswith("/") else rest  # sqlite:///relative.db, sqlite:///C:/abs.db, sqlite:////abs.db
        return sqlite3.connect(path, isolation_level=None), False
    if scheme == "azuresql":  # azuresql://<server>.database.windows.net/<database>
        import pyodbc
        from azure.identity import DefaultAzureCredential  # Managed Identity in Azure, `az login` locally

        server, _, database = rest.partition("/")
        token = DefaultAzureCredential().get_token("https://database.windows.net/.default").token.encode("utf-16-le")
        conn = pyodbc.connect(
            f"Driver={{ODBC Driver 18 for SQL Server}};Server={server};Database={database};Encrypt=yes;",
            attrs_before={1256: struct.pack(f"<I{len(token)}s", len(token), token)},  # SQL_COPT_SS_ACCESS_TOKEN
            autocommit=False,
        )
        return conn, True
    raise ActionError(f"unsupported database url scheme: {scheme!r}")


def _bind(sql: str, params: dict, qmark: bool):
    if not qmark:
        return sql, params
    return re.sub(r":\w+", "?", sql), [params[n] for n in re.findall(r":(\w+)", sql)]


def run(url: str, book: Runbook, params: dict) -> list[dict]:
    """Execute every query of the runbook atomically. Any unexpected row count rolls everything back."""
    conn, qmark = connect(url)
    rows: list[dict] = []
    try:
        if not qmark:
            conn.execute("BEGIN IMMEDIATE")
        for query in book.queries:
            sql, args = _bind(query.sql, params, qmark)
            cur = conn.cursor()
            cur.execute(sql, args)
            if query.returns_rows:
                columns = [c[0] for c in cur.description]
                rows = [dict(zip(columns, r)) for r in cur.fetchall()]
                count = len(rows)
            else:
                count = cur.rowcount
            if query.expect_rows is not None and count != query.expect_rows:
                raise ActionError(f"query '{query.name}' matched {count} rows, expected {query.expect_rows}")
        if qmark:
            conn.commit()
        else:
            conn.execute("COMMIT")
        return rows
    except ActionError:
        _rollback(conn, qmark)
        raise
    except Exception as e:  # driver errors, including a duplicate request_id
        _rollback(conn, qmark)
        raise ActionError(f"database error: {e}") from e
    finally:
        conn.close()


def _rollback(conn, qmark: bool) -> None:
    try:
        if qmark:
            conn.rollback()
        else:
            conn.execute("ROLLBACK")
    except Exception:
        pass
