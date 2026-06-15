#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import pymysql  # type: ignore[import-not-found]

from import_raw_data import choose_header_row, non_empty_cells, read_tables, row_payload, table_headers


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def env_value(name: str, default: str) -> str:
    return os.environ.get(name) or default


def env_int(name: str, default: int) -> int:
    return int(env_value(name, str(default)))


DEFAULT_RAW_DIR = Path(env_value("SQL_WORKFLOW_RAW_DATA_DIR", str(PROJECT_ROOT / "data" / "raw_unzipped")))
DEFAULT_DB_HOST = env_value("SQL_WORKFLOW_DB_HOST", "127.0.0.1")
DEFAULT_DB_PORT = env_int("SQL_WORKFLOW_DB_PORT", 3307)
DEFAULT_DB_USER = env_value("SQL_WORKFLOW_DB_USER", "workflow_user")
DEFAULT_DB_PASSWORD = env_value("SQL_WORKFLOW_DB_PASSWORD", "")
DEFAULT_DB_NAME = env_value("SQL_WORKFLOW_DB_NAME", "approval_workflow")
SUPPORTED_EXTENSIONS = {".csv", ".xlsx"}
TARGET_TABLES = ("DA", "SA")
INSERT_BATCH_SIZE = 200

DEVICE_ALIASES = {"기기카테고리", "기기", "디바이스", "pc/모바일매체"}
DATE_ALIASES = {"일", "일자", "일별", "날짜"}
START_DATE_ALIASES = {"시작일", "보고시작"}
END_DATE_ALIASES = {"종료일", "보고종료"}
IMPRESSION_ALIASES = {"노출", "노출수", "impression", "impressions", "impession", "impessions"}
CLICK_ALIASES = {"클릭수", "클릭(전체)"}
COST_ALIASES = {"비용", "총비용", "지출금액", "지출금액(krw)"}
CAMPAIGN_ALIASES = {"캠페인", "캠페인이름"}
CAMPAIGN_TYPE_ALIASES = {"캠페인유형"}
AD_GROUP_ALIASES = {"광고그룹", "광고그룹이름", "광고그룹명", "광고세트", "광고세트이름"}
CREATIVE_ALIASES = {"광고소재", "광고소재이름", "광고소재명", "광고이름", "소재이름"}


@dataclass(frozen=True)
class DbConfig:
    host: str
    port: int
    user: str
    password: str
    database: str


@dataclass(frozen=True)
class LoadPlan:
    table_name: str
    path: Path
    source_channel: str
    headers: list[str]
    rows: list[list[str]]


def quote_identifier(value: str) -> str:
    return f"`{value.replace('`', '``')}`"


def quote_identifier_for_params(value: str) -> str:
    return quote_identifier(value).replace("%", "%%")


def connect(config: DbConfig, database: str | None = None) -> pymysql.connections.Connection:
    return pymysql.connect(
        host=config.host,
        port=config.port,
        user=config.user,
        password=config.password,
        database=database,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False,
    )


def iter_source_files(raw_dir: Path) -> Iterable[Path]:
    for path in sorted(raw_dir.rglob("*")):
        if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS:
            yield path


def table_for_path(path: Path, raw_dir: Path) -> str | None:
    relative_parts = path.relative_to(raw_dir).parts[:-1]
    if any("DA" in part for part in relative_parts):
        return "DA"
    if any("SA" in part for part in relative_parts):
        return "SA"
    return None


def compact_identifier(raw_name: str, used: set[str]) -> str:
    base = re.sub(r"[\x00-\x1f\x7f]", " ", raw_name).strip() or "unnamed"
    if base.lower() == "source_channel":
        base = "raw_source_channel"
    if len(base) > 64:
        digest = hashlib.sha1(base.encode("utf-8")).hexdigest()[:8]
        base = f"{base[:55]}_{digest}"

    candidate = base
    suffix = 2
    while candidate.lower() in used:
        marker = f"_{suffix}"
        candidate = f"{base[: 64 - len(marker)]}{marker}"
        suffix += 1
    used.add(candidate.lower())
    return candidate


def normalized_key(value: str) -> str:
    return re.sub(r"\s+", "", value).strip().lower()


def is_id_column(header: str) -> bool:
    normalized = normalized_key(header)
    return "아이디" in normalized or bool(re.search(r"(^|[^0-9A-Za-z])id($|[^0-9A-Za-z])", header, re.IGNORECASE))


def canonical_header(header: str) -> str:
    base = re.sub(r"\s+", " ", header).strip()
    if is_id_column(base):
        return base

    key = normalized_key(base)
    if key in DEVICE_ALIASES:
        return "디바이스"
    if key in DATE_ALIASES:
        return "날짜"
    if key in START_DATE_ALIASES:
        return "시작일"
    if key in END_DATE_ALIASES:
        return "종료일"
    if key in IMPRESSION_ALIASES:
        return "노출수"
    if key in CLICK_ALIASES:
        return "클릭수"
    if key in COST_ALIASES:
        return "비용"
    if key in CAMPAIGN_ALIASES:
        return "캠페인"
    if key in CAMPAIGN_TYPE_ALIASES:
        return "캠페인 유형"
    if key in AD_GROUP_ALIASES:
        return "광고 그룹"
    if key in CREATIVE_ALIASES:
        return "광고 소재"
    return base


def build_column_sources(plans: list[LoadPlan]) -> OrderedDict[str, list[str]]:
    sources: OrderedDict[str, list[str]] = OrderedDict()
    canonical_to_column: dict[str, str] = {}
    used = {"source_channel"}
    for plan in plans:
        for header in plan.headers:
            canonical = canonical_header(header)
            if canonical not in canonical_to_column:
                column_name = compact_identifier(canonical, used)
                canonical_to_column[canonical] = column_name
                sources[column_name] = []
            column_name = canonical_to_column[canonical]
            if header not in sources[column_name]:
                sources[column_name].append(header)
    return sources


def discover_load_plans(raw_dir: Path) -> list[LoadPlan]:
    plans: list[LoadPlan] = []
    for path in iter_source_files(raw_dir):
        table_name = table_for_path(path, raw_dir)
        if table_name is None:
            continue
        for parsed_table in read_tables(path):
            header_idx = choose_header_row(parsed_table.rows)
            if header_idx is None:
                raise ValueError(f"Could not identify a header row in {path}")
            headers = table_headers(parsed_table, header_idx)
            rows = [row for row in parsed_table.rows[header_idx + 1 :] if non_empty_cells(row)]
            plans.append(
                LoadPlan(
                    table_name=table_name,
                    path=path,
                    source_channel=path.stem,
                    headers=headers,
                    rows=rows,
                )
            )
    return plans


def ensure_database(config: DbConfig) -> None:
    with connect(config) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"CREATE DATABASE IF NOT EXISTS {quote_identifier(config.database)} "
                "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )
        connection.commit()


def table_exists(cursor: Any, database: str, table_name: str) -> bool:
    cursor.execute(
        """
        SELECT COUNT(*) AS cnt
        FROM information_schema.tables
        WHERE table_schema = %s AND table_name = %s
        """,
        (database, table_name),
    )
    return int(cursor.fetchone()["cnt"]) > 0


def create_table(cursor: Any, config: DbConfig, table_name: str, columns: OrderedDict[str, list[str]], replace: bool) -> None:
    if table_exists(cursor, config.database, table_name):
        if not replace:
            raise ValueError(f"Table {table_name} already exists. Rerun with --replace to recreate it.")
        cursor.execute(f"DROP TABLE {quote_identifier(table_name)}")

    raw_columns = [f"{quote_identifier(column_name)} LONGTEXT NULL" for column_name in columns]
    ddl = ",\n        ".join(
        [
            "`row_id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT",
            "`import_batch_id` BIGINT UNSIGNED NULL",
            "`source_row_hash` CHAR(64) NULL",
            "`source_channel` VARCHAR(255) NOT NULL",
            *raw_columns,
            "PRIMARY KEY (`row_id`)",
            "INDEX `idx_source_channel` (`source_channel`)",
            "INDEX `idx_source_row_hash` (`source_row_hash`)",
        ]
    )
    cursor.execute(
        f"""
        CREATE TABLE {quote_identifier(table_name)} (
        {ddl}
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """
    )


def merged_value(payload: dict[str, str], source_headers: list[str]) -> str | None:
    empty_value_found = False
    for source_header in source_headers:
        if source_header not in payload:
            continue
        value = payload[source_header]
        if value != "":
            return value
        empty_value_found = True
    return "" if empty_value_found else None


def row_hash(source_channel: str, values: list[str | None]) -> str:
    payload = {"source_channel": source_channel, "values": values}
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def insert_plan(cursor: Any, table_name: str, columns: OrderedDict[str, list[str]], plan: LoadPlan) -> int:
    sql_columns = ["import_batch_id", "source_row_hash", "source_channel", *columns.keys()]
    column_sql = ", ".join(quote_identifier_for_params(column) for column in sql_columns)
    placeholders = ", ".join(["%s"] * len(sql_columns))
    insert_sql = f"INSERT INTO {quote_identifier_for_params(table_name)} ({column_sql}) VALUES ({placeholders})"

    inserted = 0
    batch: list[tuple[str | None, ...]] = []
    for row in plan.rows:
        payload = row_payload(plan.headers, row)
        raw_values = [merged_value(payload, source_headers) for source_headers in columns.values()]
        values: list[str | None] = [None, row_hash(plan.source_channel, raw_values), plan.source_channel]
        values.extend(raw_values)
        batch.append(tuple(values))
        if len(batch) >= INSERT_BATCH_SIZE:
            cursor.executemany(insert_sql, batch)
            inserted += len(batch)
            batch.clear()
    if batch:
        cursor.executemany(insert_sql, batch)
        inserted += len(batch)
    return inserted


def load_tables(config: DbConfig, plans: list[LoadPlan], replace: bool) -> dict[str, int]:
    plans_by_table = {table_name: [plan for plan in plans if plan.table_name == table_name] for table_name in TARGET_TABLES}
    row_counts = {table_name: 0 for table_name in TARGET_TABLES}

    ensure_database(config)
    with connect(config, config.database) as connection:
        with connection.cursor() as cursor:
            for table_name in TARGET_TABLES:
                table_plans = plans_by_table[table_name]
                if not table_plans:
                    raise ValueError(f"No source files discovered for {table_name}")
                columns = build_column_sources(table_plans)
                create_table(cursor, config, table_name, columns, replace)
                for plan in table_plans:
                    inserted = insert_plan(cursor, table_name, columns, plan)
                    row_counts[table_name] += inserted
                    print(f"{table_name}\t{plan.path.name}\t{inserted}")
        connection.commit()
    return row_counts


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Load extracted raw files into simple DA and SA MariaDB tables.")
    parser.add_argument("--raw-dir", type=Path, default=DEFAULT_RAW_DIR, help="Extracted raw data directory")
    parser.add_argument("--host", default=DEFAULT_DB_HOST, help="MariaDB host")
    parser.add_argument("--port", type=int, default=DEFAULT_DB_PORT, help="MariaDB port")
    parser.add_argument("--user", default=DEFAULT_DB_USER, help="MariaDB user")
    parser.add_argument("--password", default=DEFAULT_DB_PASSWORD, help="MariaDB password")
    parser.add_argument("--database", default=DEFAULT_DB_NAME, help="MariaDB database")
    parser.add_argument("--replace", action="store_true", help="Drop existing DA/SA tables before loading")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    raw_dir = args.raw_dir.resolve()
    if not raw_dir.exists():
        print(f"Raw directory does not exist: {raw_dir}", file=sys.stderr)
        return 1

    config = DbConfig(args.host, args.port, args.user, args.password, args.database)
    try:
        plans = discover_load_plans(raw_dir)
        row_counts = load_tables(config, plans, args.replace)
    except Exception as exc:
        print(f"Load failed: {exc}", file=sys.stderr)
        return 1

    print("Loaded rows:")
    for table_name in TARGET_TABLES:
        print(f"{table_name}\t{row_counts[table_name]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
