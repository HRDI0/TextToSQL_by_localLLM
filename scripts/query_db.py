#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from dotenv import load_dotenv  # type: ignore[reportMissingImports]
import pymysql  # type: ignore[import-not-found]


load_dotenv(Path(__file__).resolve().parents[1] / ".env")


def env_value(name: str, default: str, *fallback_names: str) -> str:
    for candidate in (name, *fallback_names):
        value = os.environ.get(candidate)
        if value:
            return value
    return default


def env_int(name: str, default: int) -> int:
    return int(env_value(name, str(default)))


DEFAULT_DB_HOST = env_value("SQL_WORKFLOW_DB_HOST", "127.0.0.1")
DEFAULT_DB_PORT = int(env_value("SQL_WORKFLOW_DB_PORT", "3307"))
DEFAULT_DB_USER = env_value("SQL_WORKFLOW_DB_USER", "workflow_user")
DEFAULT_DB_PASSWORD = env_value("SQL_WORKFLOW_DB_PASSWORD", "")
DEFAULT_DB_NAME = env_value("SQL_WORKFLOW_DB_NAME", "approval_workflow")

SUMMARY_TABLES = [
    "import_batch",
    "media_source",
    "source_schema_profile",
    "standard_field",
    "source_field_mapping",
    "source_file",
    "raw_record",
    "dim_date",
    "dim_media",
    "dim_campaign",
    "dim_ad_group",
    "dim_creative",
    "dim_device",
    "dim_event",
    "fact_ad_daily",
    "fact_ga_daily",
    "metric_registry",
    "fact_metric_daily",
    "report_daily_row",
    "rule_apply_audit",
]


@dataclass(frozen=True)
class DbConfig:
    host: str
    port: int
    user: str
    password: str
    database: str


def connect(config: DbConfig) -> pymysql.connections.Connection:
    return pymysql.connect(
        host=config.host,
        port=config.port,
        user=config.user,
        password=config.password,
        database=config.database,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=True,
    )


def print_rows(rows: list[dict[str, Any]]) -> None:
    if not rows:
        print("(no rows)")
        return
    headers = list(rows[0].keys())
    print("\t".join(headers))
    for row in rows:
        print("\t".join("" if row[header] is None else str(row[header]) for header in headers))


def fetch_all(connection: pymysql.connections.Connection, statement: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    with connection.cursor() as cursor:
        if params:
            cursor.execute(statement, params)
        else:
            cursor.execute(statement)
        return cast(list[dict[str, Any]], list(cursor.fetchall()))


def fetch_count(connection: pymysql.connections.Connection, table: str) -> int:
    rows = fetch_all(connection, f"SELECT COUNT(*) AS row_count FROM `{table}`")
    return int(rows[0]["row_count"])


def summary(connection: pymysql.connections.Connection) -> None:
    print("Table counts:")
    print_rows([{ "table_name": table, "row_count": fetch_count(connection, table)} for table in SUMMARY_TABLES])
    print("\nRows by source file:")
    print_rows(
        fetch_all(
            connection,
            """
            SELECT sf.source_part, sf.source_group, sf.source_name, ms.media_name,
                   sf.original_file_name, sf.file_type, sf.imported_rows,
                   COUNT(rr.raw_record_id) AS raw_record_rows,
                   SUM(CASE WHEN fa.ad_fact_id IS NOT NULL THEN 1 ELSE 0 END) AS ad_fact_rows,
                   SUM(CASE WHEN fg.ga_fact_id IS NOT NULL THEN 1 ELSE 0 END) AS ga_fact_rows
            FROM source_file sf
            LEFT JOIN media_source ms ON ms.media_source_id = sf.media_source_id
            LEFT JOIN raw_record rr ON rr.file_id = sf.file_id
            LEFT JOIN fact_ad_daily fa ON fa.raw_record_id = rr.raw_record_id
            LEFT JOIN fact_ga_daily fg ON fg.raw_record_id = rr.raw_record_id
            GROUP BY sf.file_id, sf.source_part, sf.source_group, sf.source_name, ms.media_name,
                     sf.original_file_name, sf.file_type, sf.imported_rows
            ORDER BY sf.source_part, sf.source_group, sf.source_name, sf.original_file_name
            """,
        )
    )


def files(connection: pymysql.connections.Connection) -> None:
    rows = fetch_all(
        connection,
        """
        SELECT sf.file_id, sf.source_part, sf.source_group, sf.source_name,
               ms.media_code, ms.media_name, sf.original_file_name, sf.file_path,
               sf.file_type, sf.sheet_name, sf.encoding, sf.delimiter, sf.header_row_no,
               sf.total_rows, sf.imported_rows, sf.import_status
        FROM source_file sf
        LEFT JOIN media_source ms ON ms.media_source_id = sf.media_source_id
        ORDER BY sf.file_path, sf.file_id
        """,
    )
    print_rows(rows)


def sample(connection: pymysql.connections.Connection, limit: int) -> None:
    rows = fetch_all(
        connection,
        """
        SELECT rr.raw_record_id, sf.file_path, rr.row_no, sf.source_part,
               sf.source_group, sf.source_name, rr.raw_payload
        FROM raw_record rr
        JOIN source_file sf ON sf.file_id = rr.file_id
        ORDER BY rr.raw_record_id
        LIMIT %s
        """,
        (limit,),
    )
    print_rows(rows)


def sql(connection: pymysql.connections.Connection, statement: str) -> None:
    lowered = statement.lstrip().lower()
    allowed_prefixes = ("select", "show", "describe", "desc", "explain")
    if not lowered.startswith(allowed_prefixes):
        raise ValueError("Only read-only SELECT/SHOW/DESCRIBE/EXPLAIN statements are allowed by query_db.py")
    rows = fetch_all(connection, statement)
    print_rows(rows)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Inspect the configured layered MariaDB database.")
    parser.add_argument("--host", default=DEFAULT_DB_HOST, help="MariaDB host")
    parser.add_argument("--port", type=int, default=DEFAULT_DB_PORT, help="MariaDB port")
    parser.add_argument("--user", default=DEFAULT_DB_USER, help="MariaDB user")
    parser.add_argument("--password", default=DEFAULT_DB_PASSWORD, help="MariaDB password")
    parser.add_argument("--database", default=DEFAULT_DB_NAME, help="MariaDB database name")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("summary", help="Print layered table counts and source-file fact counts")
    subparsers.add_parser("files", help="Print source_file registry rows")
    sample_parser = subparsers.add_parser("sample", help="Print sample raw_record rows")
    sample_parser.add_argument("--limit", type=int, default=10)
    sql_parser = subparsers.add_parser("sql", help="Run a read-only SQL statement")
    sql_parser.add_argument("statement")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    config = DbConfig(
        host=args.host,
        port=args.port,
        user=args.user,
        password=args.password,
        database=args.database,
    )
    try:
        with connect(config) as connection:
            if args.command == "summary":
                summary(connection)
            elif args.command == "files":
                files(connection)
            elif args.command == "sample":
                sample(connection, args.limit)
            elif args.command == "sql":
                sql(connection, args.statement)
    except Exception as exc:
        print(f"Query failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
