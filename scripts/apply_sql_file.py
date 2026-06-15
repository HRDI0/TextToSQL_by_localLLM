#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
from pathlib import Path

import pymysql  # type: ignore[import-not-found]


def env_value(name: str, default: str) -> str:
    return os.environ.get(name) or default


def env_int(name: str, default: int) -> int:
    return int(env_value(name, str(default)))


DEFAULT_DB_HOST = env_value("SQL_WORKFLOW_DB_HOST", "127.0.0.1")
DEFAULT_DB_PORT = env_int("SQL_WORKFLOW_DB_PORT", 3307)
DEFAULT_DB_USER = env_value("SQL_WORKFLOW_DB_USER", "workflow_user")
DEFAULT_DB_PASSWORD = env_value("SQL_WORKFLOW_DB_PASSWORD", "")
DEFAULT_DB_NAME = env_value("SQL_WORKFLOW_DB_NAME", "approval_workflow")


def split_sql_statements(sql_text: str) -> list[str]:
    statements: list[str] = []
    current: list[str] = []
    in_single_quote = False
    in_double_quote = False
    index = 0
    while index < len(sql_text):
        char = sql_text[index]
        current.append(char)
        if char == "'" and not in_double_quote:
            in_single_quote = not in_single_quote
        elif char == '"' and not in_single_quote:
            in_double_quote = not in_double_quote
        elif char == ";" and not in_single_quote and not in_double_quote:
            statement = "".join(current).strip().rstrip(";").strip()
            if statement:
                statements.append(statement)
            current = []
        index += 1
    trailing = "".join(current).strip()
    if trailing:
        statements.append(trailing)
    return statements


def apply_sql_file(path: Path, args: argparse.Namespace) -> int:
    statements = split_sql_statements(path.read_text(encoding="utf-8"))
    connection = pymysql.connect(
        host=args.host,
        port=args.port,
        user=args.user,
        password=args.password,
        database=args.database,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False,
    )
    try:
        with connection.cursor() as cursor:
            for statement in statements:
                cursor.execute(statement)
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    return len(statements)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Apply a SQL file to the configured MariaDB database.")
    parser.add_argument("sql_file", type=Path)
    parser.add_argument("--host", default=DEFAULT_DB_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_DB_PORT)
    parser.add_argument("--user", default=DEFAULT_DB_USER)
    parser.add_argument("--password", default=DEFAULT_DB_PASSWORD)
    parser.add_argument("--database", default=DEFAULT_DB_NAME)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    count = apply_sql_file(args.sql_file, args)
    print(f"Applied {count} SQL statements from {args.sql_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
