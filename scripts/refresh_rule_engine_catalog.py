#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import re
import unicodedata
from dataclasses import dataclass
from typing import Any

import pymysql  # type: ignore[import-not-found]


ALLOWED_TABLES = ("DA", "SA")
SKIP_VALUE_COLUMNS = {"row_id", "import_batch_id", "source_row_hash"}


def env_value(name: str, default: str, *fallback_names: str) -> str:
    for candidate in (name, *fallback_names):
        value = os.environ.get(candidate)
        if value:
            return value
    return default


def env_int(name: str, default: int) -> int:
    return int(env_value(name, str(default)))


@dataclass(frozen=True)
class DbConfig:
    host: str
    port: int
    user: str
    password: str
    database: str


def quote_identifier(identifier: str) -> str:
    return f"`{identifier.replace('`', '``')}`"


def normalize_term(value: Any, limit: int = 512) -> str:
    normalized = unicodedata.normalize("NFC", str(value or "")).lower()
    return re.sub(r"\s+", "", normalized)[:limit]


def infer_semantic_role(column_name: str) -> str | None:
    compact = normalize_term(column_name)
    if any(token in compact for token in ["노출", "클릭", "비용", "세션", "이벤트", "전환", "사용자", "률"]):
        return "metric"
    if any(token in compact for token in ["날짜", "기간", "시작일", "종료일"]):
        return "date"
    if any(token in compact for token in ["캠페인", "광고그룹", "광고소재", "디바이스", "매체", "채널", "상품"]):
        return "dimension"
    if "id" in compact or "아이디" in compact or "hash" in compact:
        return "identity"
    return None


def connect(config: DbConfig) -> pymysql.connections.Connection:
    return pymysql.connect(
        host=config.host,
        port=config.port,
        user=config.user,
        password=config.password,
        database=config.database,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False,
    )


def table_exists(connection: Any, table_name: str) -> bool:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT COUNT(*) AS table_count
            FROM information_schema.TABLES
            WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s
            """,
            (table_name,),
        )
        return int(cursor.fetchone()["table_count"]) > 0


def fetch_columns(connection: Any, table_name: str) -> list[str]:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT COLUMN_NAME
            FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s
            ORDER BY ORDINAL_POSITION
            """,
            (table_name,),
        )
        return [str(row["COLUMN_NAME"]) for row in cursor.fetchall()]


def refresh_column_catalog(connection: Any) -> int:
    inserted = 0
    with connection.cursor() as cursor:
        cursor.execute("DELETE FROM rule_engine_column_catalog WHERE target_table IN ('DA', 'SA')")
        for table_name in ALLOWED_TABLES:
            if not table_exists(connection, table_name):
                continue
            for column_name in fetch_columns(connection, table_name):
                cursor.execute(
                    f"SELECT COUNT(DISTINCT {quote_identifier(column_name)}) AS distinct_count FROM {quote_identifier(table_name)}"
                )
                distinct_count = int(cursor.fetchone()["distinct_count"] or 0)
                cursor.execute(
                    """
                    INSERT INTO rule_engine_column_catalog (
                        target_table, column_name, normalized_column_name, semantic_role, distinct_count, last_refreshed_at
                    ) VALUES (%s, %s, %s, %s, %s, NOW())
                    """,
                    (table_name, column_name, normalize_term(column_name, 255), infer_semantic_role(column_name), distinct_count),
                )
                inserted += 1
    return inserted


def refresh_value_catalog(connection: Any, per_column_limit: int) -> int:
    inserted = 0
    with connection.cursor() as cursor:
        cursor.execute("DELETE FROM rule_engine_value_catalog WHERE target_table IN ('DA', 'SA')")
        for table_name in ALLOWED_TABLES:
            if not table_exists(connection, table_name):
                continue
            for column_name in fetch_columns(connection, table_name):
                if column_name in SKIP_VALUE_COLUMNS:
                    continue
                column_sql = quote_identifier(column_name)
                cursor.execute(
                    f"""
                    SELECT CAST({column_sql} AS CHAR) AS raw_value, COUNT(*) AS frequency
                    FROM {quote_identifier(table_name)}
                    WHERE {column_sql} IS NOT NULL AND TRIM(CAST({column_sql} AS CHAR)) <> ''
                    GROUP BY CAST({column_sql} AS CHAR)
                    ORDER BY frequency DESC, raw_value
                    LIMIT %s
                    """,
                    (per_column_limit,),
                )
                for row in cursor.fetchall():
                    raw_value = str(row["raw_value"])
                    cursor.execute(
                        """
                        INSERT INTO rule_engine_value_catalog (
                            target_table, column_name, normalized_value, raw_value, frequency, last_refreshed_at
                        ) VALUES (%s, %s, %s, %s, %s, NOW())
                        ON DUPLICATE KEY UPDATE
                            raw_value = VALUES(raw_value),
                            frequency = VALUES(frequency),
                            last_refreshed_at = VALUES(last_refreshed_at)
                        """,
                        (table_name, column_name, normalize_term(raw_value), raw_value, int(row["frequency"] or 0)),
                    )
                    inserted += 1
    return inserted


def insert_refresh_log(connection: Any, status: str, row_count: int, error_message: str | None = None) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO rule_engine_catalog_refresh_log (refresh_type, status, row_count, error_message)
            VALUES (%s, %s, %s, %s)
            """,
            ("full", status, row_count, error_message),
        )


def refresh_catalog(config: DbConfig, per_column_limit: int) -> int:
    with connect(config) as connection:
        try:
            column_rows = refresh_column_catalog(connection)
            value_rows = refresh_value_catalog(connection, per_column_limit)
            total_rows = column_rows + value_rows
            insert_refresh_log(connection, "completed", total_rows)
            connection.commit()
            return total_rows
        except Exception as exc:
            connection.rollback()
            insert_refresh_log(connection, "failed", 0, str(exc))
            connection.commit()
            raise


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Refresh rule-engine column/value recommendation catalogs from DA/SA tables.")
    parser.add_argument("--host", default=env_value("SQL_WORKFLOW_DB_HOST", "127.0.0.1", "KTM_DB_HOST"))
    parser.add_argument("--port", type=int, default=int(env_value("SQL_WORKFLOW_DB_PORT", "3307", "KTM_DB_PORT")))
    parser.add_argument("--user", default=env_value("SQL_WORKFLOW_DB_USER", "workflow_user", "KTM_DB_USER"))
    parser.add_argument("--password", default=env_value("SQL_WORKFLOW_DB_PASSWORD", "", "KTM_DB_PASSWORD"))
    parser.add_argument("--database", default=env_value("SQL_WORKFLOW_DB_NAME", "approval_workflow", "KTM_DB_NAME"))
    parser.add_argument("--per-column-limit", type=int, default=50)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if not args.password:
        raise ValueError("SQL_WORKFLOW_DB_PASSWORD must be set before refreshing catalogs.")
    total_rows = refresh_catalog(
        DbConfig(args.host, args.port, args.user, args.password, args.database),
        args.per_column_limit,
    )
    print(f"Refreshed {total_rows} rule-engine catalog rows.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
