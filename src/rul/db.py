"""SQL Server / Azure SQL connections.

Two targets:
  local  - SQL Server in Docker, standing in for the on-prem database (SQL login, local only).
  azure  - Azure SQL Database with Microsoft Entra-only auth. We fetch an access token for the
           current identity (managed identity in Azure, `az login` on a laptop) and hand it to
           the ODBC driver, so there is no password anywhere.
"""

from __future__ import annotations

import logging
import os
import struct
import time
from collections.abc import Iterator
from contextlib import contextmanager

log = logging.getLogger("rul.db")

SQL_COPT_SS_ACCESS_TOKEN = 1256  # ODBC pre-connect attribute for an Entra access token
AZURE_SQL_SCOPE = "https://database.windows.net/.default"


def _driver() -> str:
    return os.getenv("SQL_DRIVER", "ODBC Driver 18 for SQL Server")


def _token_struct() -> bytes:
    from rul.azure_auth import get_credential

    token = get_credential().get_token(AZURE_SQL_SCOPE).token.encode("utf-16-le")
    return struct.pack(f"<I{len(token)}s", len(token), token)


def connect(target: str | None = None, database: str | None = None, autocommit: bool = False):
    """Open a pyodbc connection to the local or Azure database."""
    import pyodbc

    target = target or os.getenv("SQL_TARGET", "local")
    if target == "local":
        conn_str = (
            f"DRIVER={{{_driver()}}};"
            f"SERVER={os.getenv('LOCAL_SQL_SERVER', 'localhost,1433')};"
            f"DATABASE={database or os.getenv('LOCAL_SQL_DATABASE', 'rul')};"
            f"UID={os.getenv('LOCAL_SQL_USER', 'sa')};"
            f"PWD={os.environ['LOCAL_SQL_PASSWORD']};"
            "Encrypt=yes;TrustServerCertificate=yes;"
        )
        return pyodbc.connect(conn_str, autocommit=autocommit)

    if target != "azure":
        raise ValueError(f"unknown SQL target {target!r} (expected 'local' or 'azure')")

    conn_str = (
        f"DRIVER={{{_driver()}}};"
        f"SERVER=tcp:{os.environ['AZURE_SQL_SERVER']},1433;"
        f"DATABASE={database or os.environ['AZURE_SQL_DATABASE']};"
        "Encrypt=yes;TrustServerCertificate=no;Connection Timeout=60;"
    )
    # Serverless Azure SQL auto-pauses; the first connection after a pause fails while it resumes.
    for attempt in range(1, 5):
        try:
            return pyodbc.connect(
                conn_str,
                attrs_before={SQL_COPT_SS_ACCESS_TOKEN: _token_struct()},
                autocommit=autocommit,
            )
        except pyodbc.Error as exc:
            if attempt == 4:
                raise
            log.warning("Azure SQL connect attempt %d failed (%s); retrying", attempt, exc)
            time.sleep(15 * attempt)
    raise RuntimeError("unreachable")


@contextmanager
def session(target: str | None = None, database: str | None = None) -> Iterator:
    """Connection that commits on success, rolls back on error, and always closes."""
    conn = connect(target, database)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def fetch_dicts(conn, query: str, params: tuple = ()) -> list[dict]:
    cursor = conn.cursor()
    cursor.execute(query, params)
    columns = [c[0] for c in cursor.description]
    return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]
