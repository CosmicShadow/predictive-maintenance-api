"""Run a .sql file against the local or Azure database.

Supports sqlcmd-style `GO` batch separators and `$(NAME)` variables:

    python -m rul.apply_sql sql/000_create_database.sql --target local --database master
    python -m rul.apply_sql sql/001_schema.sql --target local
    python -m rul.apply_sql sql/002_grant_api_identity.sql --target azure --var API_IDENTITY=<id>
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from rul import db

_GO = re.compile(r"^\s*GO\s*;?\s*$", re.IGNORECASE | re.MULTILINE)
_VAR = re.compile(r"\$\((\w+)\)")


def render(text: str, variables: dict[str, str]) -> str:
    def replace(match: re.Match) -> str:
        name = match.group(1)
        if name not in variables:
            raise KeyError(f"SQL variable $({name}) was not supplied (use --var {name}=...)")
        return variables[name]

    return _VAR.sub(replace, text)


def split_batches(text: str) -> list[str]:
    return [batch.strip() for batch in _GO.split(text) if batch.strip()]


def run_text(conn, text: str, variables: dict[str, str] | None = None) -> int:
    batches = split_batches(render(text, variables or {}))
    cursor = conn.cursor()
    for batch in batches:
        cursor.execute(batch)
    if not conn.autocommit:
        conn.commit()
    return len(batches)


def run_file(conn, path: str | Path, variables: dict[str, str] | None = None) -> int:
    return run_text(conn, Path(path).read_text(encoding="utf-8"), variables)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("files", nargs="+", type=Path)
    parser.add_argument("--target", choices=["local", "azure"], default="local")
    parser.add_argument("--database", help="override the database (e.g. master)")
    parser.add_argument("--var", action="append", default=[], metavar="NAME=VALUE")
    args = parser.parse_args(argv)

    variables = dict(v.split("=", 1) for v in args.var)
    conn = db.connect(args.target, args.database, autocommit=True)
    try:
        for path in args.files:
            n = run_file(conn, path, variables)
            print(f"{path}: ran {n} batch(es) on {args.target}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
