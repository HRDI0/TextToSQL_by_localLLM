#!/usr/bin/env python3
from __future__ import annotations

import argparse
import codecs
import csv
import hashlib
import io
import json
import os
import re
import sys
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from xml.etree import ElementTree as ET

from dotenv import load_dotenv  # type: ignore[reportMissingImports]
import pymysql  # type: ignore[import-not-found]


PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env")


def env_value(name: str, default: str) -> str:
    return os.environ.get(name) or default


def env_int(name: str, default: int) -> int:
    return int(env_value(name, str(default)))


DEFAULT_RAW_DATA_DIR = Path(env_value("SQL_WORKFLOW_RAW_DATA_DIR", str(PROJECT_ROOT / "data" / "raw")))
DEFAULT_SCHEMA_PATH = Path(env_value("SQL_WORKFLOW_IMPORT_SCHEMA_PATH", "path/to/local/schema.sql"))
DEFAULT_DB_HOST = env_value("SQL_WORKFLOW_DB_HOST", "127.0.0.1")
DEFAULT_DB_PORT = env_int("SQL_WORKFLOW_DB_PORT", 3307)
DEFAULT_DB_USER = env_value("SQL_WORKFLOW_DB_USER", "workflow_user")
DEFAULT_DB_PASSWORD = env_value("SQL_WORKFLOW_DB_PASSWORD", "")
DEFAULT_DB_NAME = env_value("SQL_WORKFLOW_DB_NAME", "approval_workflow")

CSV_ENCODINGS = ("utf-8-sig", "cp949", "euc-kr")
CSV_DELIMITERS = (",", "\t", ";", "|")
SUPPORTED_EXTENSIONS = {".csv", ".xlsx"}

DROP_TABLES = (
    "rule_apply_audit",
    "report_daily_row",
    "fact_metric_daily",
    "fact_ga_daily",
    "fact_ad_daily",
    "raw_record",
    "source_file",
    "source_field_mapping",
    "source_schema_profile",
    "standard_field",
    "metric_registry",
    "dim_event",
    "dim_device",
    "dim_creative",
    "dim_ad_group",
    "dim_campaign",
    "dim_media",
    "dim_date",
    "media_source",
    "import_batch",
    "raw_values",
    "normalized_report_rows",
    "raw_import_values",
    "raw_import_rows",
    "raw_import_files",
    "import_run_log",
)

MEDIA_CODES: dict[str, str] = {}
MEDIA_NAMES: dict[str, str] = {}

HEADER_TOKENS = {
    "날짜",
    "일",
    "일자",
    "일별",
    "기간",
    "보고 시작",
    "캠페인",
    "캠페인 이름",
    "광고그룹",
    "광고 그룹 이름",
    "광고그룹 이름",
    "광고 세트 이름",
    "광고 소재 이름",
    "광고 이름",
    "소재 이름",
    "기기",
    "기기 카테고리",
    "디바이스",
    "PC/모바일",
    "PC/모바일 매체",
    "노출",
    "노출수",
    "클릭수",
    "클릭(전체)",
    "비용",
    "총비용",
    "지출 금액 (KRW)",
    "세션수",
    "총 사용자",
    "주요 이벤트",
    "이벤트 이름",
    "세션 소스/매체",
    "세션 캠페인",
    "세션 수동 광고 콘텐츠",
    "세션 수동 검색어",
}

STANDARD_FIELDS = [
    ("report_date", "날짜", "date", "date", "Report date"),
    ("report_start_date", "보고 시작", "date", "date", "Report start date"),
    ("report_end_date", "보고 종료", "date", "date", "Report end date"),
    ("device_raw", "원천 디바이스", "string", "dimension", "Raw device value"),
    ("campaign_name", "캠페인명", "string", "dimension", "Campaign name"),
    ("campaign_code", "캠페인 코드", "string", "id", "Campaign identifier"),
    ("campaign_type", "캠페인 유형", "string", "dimension", "Campaign type"),
    ("ad_group_name", "광고그룹명", "string", "dimension", "Ad group name"),
    ("ad_group_code", "광고그룹 코드", "string", "id", "Ad group identifier"),
    ("creative_name", "소재명", "string", "dimension", "Creative name"),
    ("creative_code", "소재 코드", "string", "id", "Creative identifier"),
    ("keyword_name", "키워드명", "string", "dimension", "Keyword or term"),
    ("landing_url", "랜딩 URL", "string", "dimension", "Landing URL"),
    ("session_source_medium", "세션 소스/매체", "string", "dimension", "GA source/medium"),
    ("session_channel_group", "세션 기본 채널 그룹", "string", "dimension", "GA channel group"),
    ("session_campaign", "세션 캠페인", "string", "dimension", "GA session campaign"),
    ("session_content", "세션 콘텐츠", "string", "dimension", "GA session content"),
    ("session_term", "세션 검색어", "string", "dimension", "GA session term"),
    ("event_name", "이벤트 이름", "string", "dimension", "GA event name"),
    ("impressions", "노출수", "number", "metric", "Impressions"),
    ("clicks", "클릭수", "number", "metric", "Clicks"),
    ("ctr", "클릭률", "number", "metric", "Click-through rate"),
    ("cost_raw", "원천 비용", "number", "metric", "Raw cost"),
    ("sessions", "세션수", "number", "metric", "Sessions"),
    ("users", "총 사용자", "number", "metric", "Users"),
    ("key_events", "주요 이벤트", "number", "metric", "Key events"),
]

SOURCE_FIELD_CODES = {
    "날짜": "report_date",
    "일": "report_date",
    "일자": "report_date",
    "일별": "report_date",
    "기간": "report_date",
    "보고 시작": "report_start_date",
    "보고 종료": "report_end_date",
    "기기": "device_raw",
    "기기 카테고리": "device_raw",
    "디바이스": "device_raw",
    "PC/모바일": "device_raw",
    "PC/모바일 매체": "device_raw",
    "캠페인": "campaign_name",
    "캠페인 이름": "campaign_name",
    "세션 캠페인": "session_campaign",
    "캠페인 ID": "campaign_code",
    "캠페인 유형": "campaign_type",
    "캠페인유형": "campaign_type",
    "광고그룹": "ad_group_name",
    "광고 그룹 이름": "ad_group_name",
    "광고그룹 이름": "ad_group_name",
    "광고 세트 이름": "ad_group_name",
    "광고 그룹 ID": "ad_group_code",
    "광고 소재 이름": "creative_name",
    "소재 이름": "creative_name",
    "광고 이름": "creative_name",
    "광고소재요소": "creative_name",
    "광고 소재 ID": "creative_code",
    "세션 수동 광고 콘텐츠": "session_content",
    "세션 수동 검색어": "session_term",
    "광고 최종 도착 URL": "landing_url",
    "세션 소스/매체": "session_source_medium",
    "세션 기본 채널 그룹": "session_channel_group",
    "이벤트 이름": "event_name",
    "노출": "impressions",
    "노출수": "impressions",
    "클릭수": "clicks",
    "클릭(전체)": "clicks",
    "클릭률(%)": "ctr",
    "비용": "cost_raw",
    "총비용": "cost_raw",
    "지출 금액 (KRW)": "cost_raw",
    "세션수": "sessions",
    "총 사용자": "users",
    "주요 이벤트": "key_events",
}


@dataclass(frozen=True)
class ParsedTable:
    path: Path
    rows: list[list[str]]
    file_format: str
    encoding: str | None = None
    delimiter: str | None = None
    sheet_name: str | None = None


@dataclass(frozen=True)
class DbConfig:
    host: str
    port: int
    user: str
    password: str
    database: str


@dataclass(frozen=True)
class SourceMetadata:
    source_part: str | None
    source_group: str | None
    source_name: str
    media_code: str
    media_name: str
    media_category: str | None
    platform_type: str | None
    fact_target: str


@dataclass(frozen=True)
class ImportTable:
    table: ParsedTable
    metadata: SourceMetadata
    header_idx: int
    headers: list[str]


class ImportContext:
    def __init__(self, batch_id: int, report_month: str) -> None:
        self.batch_id = batch_id
        self.report_month = report_month
        self.media_sources: dict[str, int] = {}
        self.schema_profiles: dict[tuple[int, str], int] = {}
        self.standard_fields: dict[str, int] = {}
        self.dim_dates: dict[str | None, int] = {}
        self.dim_media: dict[int, int] = {}
        self.dim_campaigns: dict[tuple[int | None, str, str | None], int] = {}
        self.dim_ad_groups: dict[tuple[int | None, str | None, str | None], int] = {}
        self.dim_creatives: dict[tuple[int | None, str | None, str | None], int] = {}
        self.dim_devices: dict[str, int] = {}
        self.dim_events: dict[str, int] = {}


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def ensure_inside(child: Path, parent: Path) -> None:
    try:
        child.resolve().relative_to(parent.resolve())
    except ValueError as exc:
        raise ValueError(f"Refusing to ingest outside raw_data: {child}") from exc


def detect_csv_encoding(path: Path) -> str:
    sample = path.read_bytes()[:8192]
    if sample.startswith((b"\xff\xfe", b"\xfe\xff")):
        return "utf-16"
    if sample[:400].count(b"\x00") > 20:
        return "utf-16"
    for encoding in CSV_ENCODINGS:
        try:
            decoder = codecs.getincrementaldecoder(encoding)()
            decoder.decode(sample, final=False)
            return encoding
        except UnicodeDecodeError:
            continue
    return "utf-8-sig"


def parse_csv_with_delimiter(text: str, delimiter: str) -> list[list[str]]:
    return [list(row) for row in csv.reader(io.StringIO(text), delimiter=delimiter)]


def score_delimiter(rows: list[list[str]]) -> tuple[int, int, float]:
    sample = [row for row in rows[:100] if any(cell.strip() for cell in row)]
    if not sample:
        return (0, 0, 0.0)
    widths = [len(row) for row in sample]
    max_width = max(widths)
    wide_rows = sum(1 for width in widths if width == max_width and width > 1)
    avg_width = sum(widths) / len(widths)
    return (max_width, wide_rows, avg_width)


def read_csv_table(path: Path) -> ParsedTable:
    encoding = detect_csv_encoding(path)
    text = path.read_text(encoding=encoding, errors="replace")
    parsed = [(delimiter, parse_csv_with_delimiter(text, delimiter)) for delimiter in CSV_DELIMITERS]
    delimiter, rows = max(parsed, key=lambda item: score_delimiter(item[1]))
    return ParsedTable(path=path, rows=rows, file_format="csv", encoding=encoding, delimiter=delimiter)


def xlsx_shared_strings(zf: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in zf.namelist():
        return []
    root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
    namespace = {"main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    values: list[str] = []
    for item in root.findall("main:si", namespace):
        values.append("".join(text.text or "" for text in item.findall(".//main:t", namespace)))
    return values


def cell_index(cell_ref: str) -> int:
    letters = "".join(ch for ch in cell_ref if ch.isalpha())
    index = 0
    for char in letters.upper():
        index = index * 26 + (ord(char) - ord("A") + 1)
    return max(index - 1, 0)


def xlsx_cell_value(cell: ET.Element, shared_strings: list[str], namespace: dict[str, str]) -> str:
    cell_type = cell.attrib.get("t")
    if cell_type == "inlineStr":
        return "".join(text.text or "" for text in cell.findall(".//main:t", namespace)).strip()
    value = cell.find("main:v", namespace)
    raw = "" if value is None or value.text is None else value.text
    if cell_type == "s" and raw:
        return shared_strings[int(raw)].strip()
    return raw.strip()


def read_xlsx_tables(path: Path) -> list[ParsedTable]:
    namespace = {"main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    with zipfile.ZipFile(path) as zf:
        workbook = ET.fromstring(zf.read("xl/workbook.xml"))
        rels = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
        rel_map = {rel.attrib["Id"]: rel.attrib["Target"] for rel in rels}
        shared_strings = xlsx_shared_strings(zf)
        tables: list[ParsedTable] = []
        for sheet in workbook.findall("main:sheets/main:sheet", namespace):
            name = sheet.attrib["name"]
            relationship_id = sheet.attrib["{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"]
            target = rel_map[relationship_id]
            if target.startswith("/"):
                sheet_path = target.lstrip("/")
            elif target.startswith("xl/"):
                sheet_path = target
            else:
                sheet_path = f"xl/{target}"
            worksheet = ET.fromstring(zf.read(sheet_path))
            rows: list[list[str]] = []
            for row in worksheet.findall(".//main:sheetData/main:row", namespace):
                values: list[str] = []
                for cell in row.findall("main:c", namespace):
                    ref = cell.attrib.get("r", "A1")
                    idx = cell_index(ref)
                    while len(values) <= idx:
                        values.append("")
                    values[idx] = xlsx_cell_value(cell, shared_strings, namespace)
                rows.append(values)
            tables.append(ParsedTable(path=path, rows=rows, file_format="xlsx", sheet_name=name))
        return tables


def read_tables(path: Path) -> list[ParsedTable]:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return [read_csv_table(path)]
    if suffix == ".xlsx":
        return read_xlsx_tables(path)
    return []


def non_empty_cells(row: Iterable[str]) -> list[str]:
    return [cell.strip() for cell in row if cell and cell.strip()]


def header_signal(row: list[str]) -> int:
    signal = 0
    for cell in non_empty_cells(row):
        normalized = cell.strip()
        if normalized in HEADER_TOKENS:
            signal += 2
        elif any(token in normalized for token in HEADER_TOKENS):
            signal += 1
    return signal


def choose_header_row(rows: list[list[str]]) -> int | None:
    candidates: list[tuple[int, int, int, int]] = []
    for idx, row in enumerate(rows[:200]):
        width = len(non_empty_cells(row))
        if width < 2:
            continue
        signal = header_signal(row)
        candidates.append((1 if signal else 0, signal, width, -idx))
    if not candidates:
        return None
    best = max(candidates)
    return -best[3]


def unique_headers(header_row: list[str]) -> list[str]:
    seen: dict[str, int] = {}
    headers: list[str] = []
    for idx, raw_header in enumerate(header_row, start=1):
        base = raw_header.strip() or f"unnamed_{idx}"
        seen[base] = seen.get(base, 0) + 1
        headers.append(base if seen[base] == 1 else f"{base}_{seen[base]}")
    return headers


def table_headers(table: ParsedTable, header_idx: int) -> list[str]:
    headers = unique_headers(table.rows[header_idx])
    widest_row = max((len(row) for row in table.rows[header_idx + 1 :] if non_empty_cells(row)), default=len(headers))
    if widest_row > len(headers):
        headers.extend(f"extra_{idx}" for idx in range(len(headers) + 1, widest_row + 1))
    return headers


def row_payload(headers: list[str], row: list[str]) -> dict[str, str]:
    effective_headers = headers[:]
    if len(row) > len(effective_headers):
        effective_headers.extend(f"extra_{idx}" for idx in range(len(effective_headers) + 1, len(row) + 1))
    padded = row + [""] * max(len(effective_headers) - len(row), 0)
    return {header: value.strip() for header, value in zip(effective_headers, padded)}


def normalized_stem(path: Path) -> str:
    return re.sub(r"_\d{4,8}$", "", path.stem)


def source_metadata(path: Path, raw_data_dir: Path) -> SourceMetadata:
    relative = path.relative_to(raw_data_dir)
    parts = relative.parts
    source_part = "DA" if any("DA" in part for part in parts[:-1]) else "SA" if any("SA" in part for part in parts[:-1]) else None
    folder_group = parts[-2] if len(parts) >= 2 else "UNKNOWN"
    source_name = normalized_stem(path)
    is_ga = "GA" in folder_group
    is_bsa = "BSA" in source_name
    if is_ga and "organic" in source_name.lower():
        source_group = "organic"
    elif is_ga and "commerce" in source_name.lower():
        source_group = "commerce"
    elif is_ga:
        source_group = "GA"
    elif is_bsa:
        source_group = "BSA"
    else:
        source_group = "매체"

    media_code = MEDIA_CODES.get(source_name, re.sub(r"\W+", "_", source_name.lower()).strip("_") or "unknown")
    media_name = MEDIA_NAMES.get(source_name, source_name)
    fact_target = "fact_ga_daily" if is_ga else "fact_ad_daily"
    if source_group == "비광고":
        media_category = "NON_AD"
    elif source_group == "GA":
        media_category = "GA"
    elif source_group == "BSA":
        media_category = "BSA"
    else:
        media_category = source_part
    if fact_target == "fact_ga_daily":
        platform_type = "analytics"
    elif source_part == "SA":
        platform_type = "search_ad"
    else:
        platform_type = "ad_platform"
    return SourceMetadata(
        source_part=source_part,
        source_group=source_group,
        source_name=source_name,
        media_code=media_code,
        media_name=media_name,
        media_category=media_category,
        platform_type=platform_type,
        fact_target=fact_target,
    )


def profile_name(import_table: ImportTable) -> str:
    if import_table.table.sheet_name:
        return f"{import_table.metadata.source_name}:{import_table.table.sheet_name}"
    return import_table.metadata.source_name


def pick(payload: dict[str, str], names: Iterable[str]) -> str | None:
    for name in names:
        value = payload.get(name)
        if value is not None and value.strip() != "":
            return value.strip()
    return None


def parse_number(value: str | None) -> float | None:
    if value is None:
        return None
    text = value.strip()
    if text == "" or text.lower() == "total":
        return None
    cleaned = re.sub(r"[^0-9.\-]", "", text)
    if cleaned in {"", "-", "."}:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def parse_date(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip()
    if not text or text.lower() == "total":
        return None
    patterns = (
        r"(?P<y>20\d{2})[-.\s년]+(?P<m>\d{1,2})[-.\s월]+(?P<d>\d{1,2})",
        r"(?P<y>20\d{2})(?P<m>\d{2})(?P<d>\d{2})",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            year = int(match.group("y"))
            month = int(match.group("m"))
            day = int(match.group("d"))
            return f"{year:04d}-{month:02d}-{day:02d}"
    return None


def payload_hash(payload: dict[str, str]) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def hash_key(*parts: object) -> str:
    canonical = json.dumps(parts, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def connect_db(config: DbConfig) -> pymysql.connections.Connection:
    return pymysql.connect(
        host=config.host,
        port=config.port,
        user=config.user,
        password=config.password,
        database=config.database,
        charset="utf8mb4",
        autocommit=False,
    )


def require_lastrowid(cursor: Any) -> int:
    if cursor.lastrowid is None:
        raise RuntimeError("MariaDB did not return a lastrowid for the inserted row")
    return int(cursor.lastrowid)


def quote_identifier(identifier: str) -> str:
    return f"`{identifier.replace('`', '``')}`"


def schema_statements(schema_path: Path) -> list[str]:
    statements: list[str] = []
    current: list[str] = []
    for line in schema_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("--"):
            continue
        current.append(line)
        if stripped.endswith(";"):
            statement = "\n".join(current).strip().rstrip(";").strip()
            if statement:
                statements.append(statement)
            current = []
    if current:
        statements.append("\n".join(current).strip())
    return statements


def reset_schema(connection: pymysql.connections.Connection, schema_path: Path, reset: bool) -> None:
    with connection.cursor() as cursor:
        if reset:
            cursor.execute("SET FOREIGN_KEY_CHECKS = 0")
            for table_name in DROP_TABLES:
                cursor.execute(f"DROP TABLE IF EXISTS {quote_identifier(table_name)}")
            cursor.execute("SET FOREIGN_KEY_CHECKS = 1")
        for statement in schema_statements(schema_path):
            cursor.execute(statement)
    connection.commit()


def iter_source_files(raw_data_dir: Path) -> Iterable[Path]:
    for path in sorted(raw_data_dir.rglob("*")):
        if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS:
            yield path


def discover_import_tables(raw_data_dir: Path) -> tuple[list[ImportTable], dict[str, int]]:
    import_tables: list[ImportTable] = []
    stats = {"files": 0, "tables": 0, "skipped_tables": 0, "raw_records": 0, "bronze_only_rows": 0, "fact_ad_daily": 0, "fact_ga_daily": 0, "report_daily_row": 0}
    for path in iter_source_files(raw_data_dir):
        ensure_inside(path, raw_data_dir)
        metadata = source_metadata(path, raw_data_dir)
        stats["files"] += 1
        for table in read_tables(path):
            stats["tables"] += 1
            header_idx = choose_header_row(table.rows)
            if header_idx is None:
                stats["skipped_tables"] += 1
                continue
            import_tables.append(ImportTable(table=table, metadata=metadata, header_idx=header_idx, headers=table_headers(table, header_idx)))
    return import_tables, stats


def seed_standard_fields(connection: pymysql.connections.Connection) -> dict[str, int]:
    with connection.cursor() as cursor:
        for field_code, display_name, data_type, field_role, description in STANDARD_FIELDS:
            cursor.execute(
                """
                INSERT INTO standard_field (field_code, display_name, data_type, field_role, description, active)
                VALUES (%s, %s, %s, %s, %s, TRUE)
                ON DUPLICATE KEY UPDATE
                    display_name = VALUES(display_name),
                    data_type = VALUES(data_type),
                    field_role = VALUES(field_role),
                    description = VALUES(description),
                    active = TRUE
                """,
                (field_code, display_name, data_type, field_role, description),
            )
        cursor.execute("SELECT standard_field_id, field_code FROM standard_field")
        return {str(row[1]): int(row[0]) for row in cursor.fetchall()}


def seed_metric_registry(connection: pymysql.connections.Connection) -> None:
    metrics = [
        ("impressions", "노출수", "ad", "number", "count"),
        ("clicks", "클릭수", "ad", "number", "count"),
        ("ctr", "클릭률", "ad", "number", "percent"),
        ("cost_raw", "원천 비용", "ad", "number", "KRW"),
        ("sessions", "세션수", "ga", "number", "count"),
        ("users", "총 사용자", "ga", "number", "count"),
        ("key_events", "주요 이벤트", "ga", "number", "count"),
    ]
    with connection.cursor() as cursor:
        for metric_code, metric_name, metric_group, data_type, default_unit in metrics:
            cursor.execute(
                """
                INSERT INTO metric_registry (metric_code, metric_name, metric_group, data_type, default_unit)
                VALUES (%s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    metric_name = VALUES(metric_name),
                    metric_group = VALUES(metric_group),
                    data_type = VALUES(data_type),
                    default_unit = VALUES(default_unit)
                """,
                (metric_code, metric_name, metric_group, data_type, default_unit),
            )


def create_import_batch(
    connection: pymysql.connections.Connection,
    advertiser_code: str,
    report_month: str,
    batch_name: str | None,
    uploaded_by: str | None,
    raw_data_dir: Path,
) -> int:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO import_batch (advertiser_code, report_month, batch_name, uploaded_by, uploaded_at, status, message)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (advertiser_code, report_month, batch_name, uploaded_by, utc_now(), "running", str(raw_data_dir)),
        )
        return require_lastrowid(cursor)


def get_media_source_id(cursor: Any, context: ImportContext, metadata: SourceMetadata) -> int:
    cached = context.media_sources.get(metadata.media_code)
    if cached is not None:
        return cached
    cursor.execute(
        """
        INSERT INTO media_source (media_code, media_name, media_category, platform_type, active, created_at)
        VALUES (%s, %s, %s, %s, TRUE, %s)
        ON DUPLICATE KEY UPDATE
            media_name = VALUES(media_name),
            media_category = VALUES(media_category),
            platform_type = VALUES(platform_type),
            active = TRUE
        """,
        (metadata.media_code, metadata.media_name, metadata.media_category, metadata.platform_type, utc_now()),
    )
    cursor.execute("SELECT media_source_id FROM media_source WHERE media_code = %s", (metadata.media_code,))
    media_source_id = int(cursor.fetchone()[0])
    context.media_sources[metadata.media_code] = media_source_id
    return media_source_id


def get_schema_profile_id(cursor: Any, context: ImportContext, media_source_id: int, import_table: ImportTable) -> int:
    current_profile_name = profile_name(import_table)
    key = (media_source_id, current_profile_name)
    cached = context.schema_profiles.get(key)
    if cached is not None:
        return cached
    table = import_table.table
    cursor.execute(
        """
        INSERT INTO source_schema_profile (
            media_source_id, profile_name, file_type, encoding, delimiter,
            header_row_no, date_format, has_metadata_rows, active, created_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, TRUE, %s)
        ON DUPLICATE KEY UPDATE
            file_type = VALUES(file_type),
            encoding = VALUES(encoding),
            delimiter = VALUES(delimiter),
            header_row_no = VALUES(header_row_no),
            date_format = VALUES(date_format),
            has_metadata_rows = VALUES(has_metadata_rows),
            active = TRUE
        """,
        (
            media_source_id,
            current_profile_name,
            table.file_format,
            table.encoding,
            table.delimiter,
            import_table.header_idx + 1,
            "auto",
            import_table.header_idx > 0,
            utc_now(),
        ),
    )
    cursor.execute(
        "SELECT schema_profile_id FROM source_schema_profile WHERE media_source_id = %s AND profile_name = %s",
        (media_source_id, current_profile_name),
    )
    schema_profile_id = int(cursor.fetchone()[0])
    context.schema_profiles[key] = schema_profile_id
    return schema_profile_id


def insert_source_field_mappings(cursor: Any, context: ImportContext, schema_profile_id: int, headers: list[str]) -> int:
    count = 0
    for header in headers:
        field_code = SOURCE_FIELD_CODES.get(header)
        if field_code is None:
            continue
        standard_field_id = context.standard_fields.get(field_code)
        if standard_field_id is None:
            continue
        cast_type = next((field[2] for field in STANDARD_FIELDS if field[0] == field_code), None)
        cursor.execute(
            """
            INSERT IGNORE INTO source_field_mapping (
                schema_profile_id, source_field_name, standard_field_id,
                cast_type, transform_rule, required, active
            ) VALUES (%s, %s, %s, %s, %s, FALSE, TRUE)
            """,
            (schema_profile_id, header, standard_field_id, cast_type, None),
        )
        count += int(cursor.rowcount > 0)
    return count


def load_source_field_mapping(cursor: Any, schema_profile_id: int) -> dict[str, str]:
    cursor.execute(
        """
        SELECT sfm.source_field_name, sf.field_code
        FROM source_field_mapping sfm
        JOIN standard_field sf ON sf.standard_field_id = sfm.standard_field_id
        WHERE sfm.schema_profile_id = %s AND sfm.active = TRUE AND sf.active = TRUE
        """,
        (schema_profile_id,),
    )
    return {str(row[0]): str(row[1]) for row in cursor.fetchall()}


def canonical_payload(payload: dict[str, str], field_mapping: dict[str, str]) -> dict[str, str]:
    canonical: dict[str, str] = {}
    for source_field, field_code in field_mapping.items():
        value = payload.get(source_field)
        if value is None or value.strip() == "":
            continue
        canonical.setdefault(field_code, value.strip())
    return canonical


def canonical_report_date(canonical: dict[str, str]) -> str | None:
    return parse_date(canonical.get("report_date") or canonical.get("report_start_date"))


def insert_source_file(cursor: Any, context: ImportContext, import_table: ImportTable, raw_data_dir: Path, media_source_id: int) -> int:
    table = import_table.table
    relative_path = str(table.path.relative_to(raw_data_dir))
    cursor.execute(
        """
        INSERT INTO source_file (
            batch_id, media_source_id, source_part, source_group, source_name,
            original_file_name, file_path, file_type, sheet_name, encoding, delimiter,
            header_row_no, total_rows, imported_rows, import_status, created_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            context.batch_id,
            media_source_id,
            import_table.metadata.source_part,
            import_table.metadata.source_group,
            import_table.metadata.source_name,
            table.path.name,
            relative_path,
            table.file_format,
            table.sheet_name,
            table.encoding,
            table.delimiter,
            import_table.header_idx + 1,
            len(table.rows),
            0,
            "running",
            utc_now(),
        ),
    )
    return require_lastrowid(cursor)


def get_date_id(cursor: Any, context: ImportContext, date_text: str | None) -> int:
    if date_text is None:
        date_text = None
    cached = context.dim_dates.get(date_text)
    if cached is not None:
        return cached
    if date_text is None:
        cursor.execute(
            """
            INSERT IGNORE INTO dim_date (date_id, date_value, year_no, month_no, week_label, weekday_no, weekday_name)
            VALUES (0, '1000-01-01', NULL, NULL, 'unknown', NULL, 'unknown')
            """
        )
        context.dim_dates[None] = 0
        return 0
    parsed = datetime.strptime(date_text, "%Y-%m-%d")
    date_id = int(parsed.strftime("%Y%m%d"))
    cursor.execute(
        """
        INSERT IGNORE INTO dim_date (date_id, date_value, year_no, month_no, week_label, weekday_no, weekday_name)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
        (date_id, date_text, parsed.year, parsed.month, parsed.strftime("%G-W%V"), parsed.isoweekday(), parsed.strftime("%A")),
    )
    context.dim_dates[date_text] = date_id
    return date_id


def get_dim_media_id(cursor: Any, context: ImportContext, media_source_id: int, metadata: SourceMetadata) -> int:
    cached = context.dim_media.get(media_source_id)
    if cached is not None:
        return cached
    cursor.execute(
        """
        INSERT INTO dim_media (media_source_id, media_name, ad_media, ad_type, channel_group, active)
        VALUES (%s, %s, %s, %s, %s, TRUE)
        ON DUPLICATE KEY UPDATE
            media_name = VALUES(media_name),
            ad_media = VALUES(ad_media),
            ad_type = VALUES(ad_type),
            channel_group = VALUES(channel_group),
            active = TRUE
        """,
        (media_source_id, metadata.media_name, metadata.media_name, metadata.source_part, metadata.source_group),
    )
    cursor.execute("SELECT media_id FROM dim_media WHERE media_source_id = %s", (media_source_id,))
    media_id = int(cursor.fetchone()[0])
    context.dim_media[media_source_id] = media_id
    return media_id


def get_campaign_id(cursor: Any, context: ImportContext, media_source_id: int | None, campaign_name: str | None, campaign_code: str | None = None) -> int | None:
    if not campaign_name and not campaign_code:
        return None
    campaign_name = campaign_name or campaign_code or "unknown"
    key = (media_source_id, campaign_name, campaign_code)
    cached = context.dim_campaigns.get(key)
    if cached is not None:
        return cached
    campaign_hash = hash_key("campaign", media_source_id, campaign_name, campaign_code)
    cursor.execute(
        """
        INSERT IGNORE INTO dim_campaign (media_source_id, campaign_name, campaign_code, campaign_hash, active)
        VALUES (%s, %s, %s, %s, TRUE)
        """,
        (media_source_id, campaign_name, campaign_code, campaign_hash),
    )
    cursor.execute("SELECT campaign_id FROM dim_campaign WHERE campaign_hash = %s", (campaign_hash,))
    campaign_id = int(cursor.fetchone()[0])
    context.dim_campaigns[key] = campaign_id
    return campaign_id


def get_ad_group_id(cursor: Any, context: ImportContext, media_source_id: int | None, ad_group_name: str | None, ad_group_code: str | None = None) -> int | None:
    if not ad_group_name and not ad_group_code:
        return None
    key = (media_source_id, ad_group_name, ad_group_code)
    cached = context.dim_ad_groups.get(key)
    if cached is not None:
        return cached
    ad_group_hash = hash_key("ad_group", media_source_id, ad_group_name, ad_group_code)
    cursor.execute(
        """
        INSERT IGNORE INTO dim_ad_group (media_source_id, ad_group_name, ad_group_code, ad_group_hash)
        VALUES (%s, %s, %s, %s)
        """,
        (media_source_id, ad_group_name, ad_group_code, ad_group_hash),
    )
    cursor.execute("SELECT ad_group_id FROM dim_ad_group WHERE ad_group_hash = %s", (ad_group_hash,))
    ad_group_id = int(cursor.fetchone()[0])
    context.dim_ad_groups[key] = ad_group_id
    return ad_group_id


def get_creative_id(cursor: Any, context: ImportContext, media_source_id: int | None, creative_name: str | None, creative_code: str | None = None) -> int | None:
    if not creative_name and not creative_code:
        return None
    key = (media_source_id, creative_name, creative_code)
    cached = context.dim_creatives.get(key)
    if cached is not None:
        return cached
    creative_hash = hash_key("creative", media_source_id, creative_name, creative_code)
    cursor.execute(
        """
        INSERT IGNORE INTO dim_creative (media_source_id, creative_name, creative_code, creative_hash)
        VALUES (%s, %s, %s, %s)
        """,
        (media_source_id, creative_name, creative_code, creative_hash),
    )
    cursor.execute("SELECT creative_id FROM dim_creative WHERE creative_hash = %s", (creative_hash,))
    creative_id = int(cursor.fetchone()[0])
    context.dim_creatives[key] = creative_id
    return creative_id


def device_group(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    if normalized in {"mobile", "mo", "m", "휴대전화", "모바일"} or "mobile" in normalized:
        return "MO"
    if normalized in {"desktop", "pc", "p", "컴퓨터"} or "desktop" in normalized:
        return "PC"
    if "tablet" in normalized or "태블릿" in normalized:
        return "TABLET"
    return value.strip() or None


def get_device_id(cursor: Any, context: ImportContext, value: str | None) -> int | None:
    if not value:
        return None
    cached = context.dim_devices.get(value)
    if cached is not None:
        return cached
    group = device_group(value)
    cursor.execute(
        """
        INSERT IGNORE INTO dim_device (device_raw, device_group, device_label)
        VALUES (%s, %s, %s)
        """,
        (value, group, group or value),
    )
    cursor.execute("SELECT device_id FROM dim_device WHERE device_raw = %s", (value,))
    device_id = int(cursor.fetchone()[0])
    context.dim_devices[value] = device_id
    return device_id


def get_event_id(cursor: Any, context: ImportContext, event_name: str | None) -> int | None:
    if not event_name:
        return None
    cached = context.dim_events.get(event_name)
    if cached is not None:
        return cached
    lower = event_name.lower()
    is_conversion = any(token in lower for token in ("complete", "purchase", "conversion")) or "가입완료" in event_name
    category = "conversion" if is_conversion else event_name
    cursor.execute(
        """
        INSERT IGNORE INTO dim_event (event_name, event_category, conversion_type, is_conversion)
        VALUES (%s, %s, %s, %s)
        """,
        (event_name, category, None, is_conversion),
    )
    cursor.execute("SELECT event_id FROM dim_event WHERE event_name = %s", (event_name,))
    event_id = int(cursor.fetchone()[0])
    context.dim_events[event_name] = event_id
    return event_id


def json_text(value: dict[str, Any] | list[Any] | None) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def additional_metrics(payload: dict[str, str], consumed: set[str]) -> dict[str, str]:
    return {key: value for key, value in payload.items() if key not in consumed and value.strip() != ""}


def insert_raw_record(
    cursor: Any,
    file_id: int,
    row_no: int,
    payload: dict[str, str],
    parse_status: str,
    parse_error: str | None,
    now: str,
) -> int:
    cursor.execute(
        """
        INSERT INTO raw_record (file_id, row_no, raw_payload, raw_hash, parse_status, parse_error, created_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
        (file_id, row_no, json_text(payload), payload_hash(payload), parse_status, parse_error, now),
    )
    return require_lastrowid(cursor)


def insert_ad_fact(
    cursor: Any,
    context: ImportContext,
    import_table: ImportTable,
    file_id: int,
    raw_record_id: int,
    media_source_id: int,
    media_id: int,
    payload: dict[str, str],
    canonical: dict[str, str],
    consumed_fields: set[str],
    report_date: str,
    now: str,
) -> int:
    date_id = get_date_id(cursor, context, report_date)
    campaign_name = canonical.get("campaign_name")
    ad_group_name = canonical.get("ad_group_name")
    creative_name = canonical.get("creative_name")
    device_raw = canonical.get("device_raw")
    campaign_id = get_campaign_id(cursor, context, media_source_id, campaign_name, canonical.get("campaign_code"))
    ad_group_id = get_ad_group_id(cursor, context, media_source_id, ad_group_name, canonical.get("ad_group_code"))
    creative_id = get_creative_id(cursor, context, media_source_id, creative_name, canonical.get("creative_code"))
    device_id = get_device_id(cursor, context, device_raw)
    impressions = parse_number(canonical.get("impressions")) or 0
    clicks = parse_number(canonical.get("clicks")) or 0
    cost_raw = parse_number(canonical.get("cost_raw")) or 0
    ctr = parse_number(canonical.get("ctr"))
    landing_url = canonical.get("landing_url")
    cursor.execute(
        """
        INSERT INTO fact_ad_daily (
            raw_record_id, file_id, date_id, media_id, campaign_id, ad_group_id,
            creative_id, device_id, source_part, source_group, source_name,
            impressions, clicks, cost_raw, cost_currency, ctr, landing_url,
            additional_metrics, normalized_status, created_at, updated_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            raw_record_id,
            file_id,
            date_id,
            media_id,
            campaign_id,
            ad_group_id,
            creative_id,
            device_id,
            import_table.metadata.source_part,
            import_table.metadata.source_group,
            import_table.metadata.source_name,
            impressions,
            clicks,
            cost_raw,
            "KRW",
            ctr,
            landing_url,
            json_text(additional_metrics(payload, consumed_fields)),
            "normalized",
            now,
            now,
        ),
    )
    ad_fact_id = require_lastrowid(cursor)
    insert_report_row(
        cursor,
        context,
        import_table,
        report_date,
        media_name=import_table.metadata.media_name,
        device_raw=device_raw,
        campaign_name=campaign_name,
        ad_group_name=ad_group_name,
        creative_name=creative_name,
        keyword_name=canonical.get("keyword_name"),
        impressions=impressions,
        clicks=clicks,
        cost_raw=cost_raw,
        sessions=0,
        users=0,
        source_ad_fact_id=ad_fact_id,
        source_ga_fact_id=None,
        now=now,
    )
    return ad_fact_id


def insert_ga_fact(
    cursor: Any,
    context: ImportContext,
    import_table: ImportTable,
    file_id: int,
    raw_record_id: int,
    media_source_id: int,
    media_id: int,
    payload: dict[str, str],
    canonical: dict[str, str],
    consumed_fields: set[str],
    report_date: str,
    now: str,
) -> int:
    date_id = get_date_id(cursor, context, report_date)
    session_source_medium = canonical.get("session_source_medium")
    session_channel_group = canonical.get("session_channel_group")
    session_campaign = canonical.get("session_campaign") or canonical.get("campaign_name")
    session_content = canonical.get("session_content")
    session_term = canonical.get("session_term")
    event_name = canonical.get("event_name")
    device_raw = canonical.get("device_raw")
    campaign_id = get_campaign_id(cursor, context, media_source_id, session_campaign)
    creative_id = get_creative_id(cursor, context, media_source_id, session_content)
    device_id = get_device_id(cursor, context, device_raw)
    event_id = get_event_id(cursor, context, event_name)
    sessions = parse_number(canonical.get("sessions")) or 0
    users = parse_number(canonical.get("users")) or 0
    key_events = parse_number(canonical.get("key_events")) or 0
    cursor.execute(
        """
        INSERT INTO fact_ga_daily (
            raw_record_id, file_id, date_id, media_id, campaign_id, creative_id,
            device_id, event_id, session_source_medium, session_channel_group,
            session_campaign, session_content, session_term, sessions, users,
            key_events, source_part, source_group, source_name, additional_metrics,
            normalized_status, created_at, updated_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            raw_record_id,
            file_id,
            date_id,
            media_id,
            campaign_id,
            creative_id,
            device_id,
            event_id,
            session_source_medium,
            session_channel_group,
            session_campaign,
            session_content,
            session_term,
            sessions,
            users,
            key_events,
            import_table.metadata.source_part,
            import_table.metadata.source_group,
            import_table.metadata.source_name,
            json_text(additional_metrics(payload, consumed_fields)),
            "normalized",
            now,
            now,
        ),
    )
    ga_fact_id = require_lastrowid(cursor)
    insert_report_row(
        cursor,
        context,
        import_table,
        report_date,
        media_name=import_table.metadata.media_name,
        device_raw=device_raw,
        campaign_name=session_campaign,
        ad_group_name=None,
        creative_name=session_content,
        keyword_name=session_term,
        impressions=0,
        clicks=0,
        cost_raw=0,
        sessions=sessions,
        users=users,
        source_ad_fact_id=None,
        source_ga_fact_id=ga_fact_id,
        now=now,
    )
    return ga_fact_id


def insert_report_row(
    cursor: Any,
    context: ImportContext,
    import_table: ImportTable,
    report_date: str | None,
    media_name: str,
    device_raw: str | None,
    campaign_name: str | None,
    ad_group_name: str | None,
    creative_name: str | None,
    keyword_name: str | None,
    impressions: float,
    clicks: float,
    cost_raw: float,
    sessions: float,
    users: float,
    source_ad_fact_id: int | None,
    source_ga_fact_id: int | None,
    now: str,
) -> None:
    device = device_group(device_raw)
    cursor.execute(
        """
        INSERT INTO report_daily_row (
            report_month, report_date, source_part, source_group, source_name,
            type_group, ad_media, ad_type, device_group, media_raw, device_raw,
            campaign_name, ad_group_name, creative_name_raw, keyword_name,
            impressions, clicks, cost_raw, sessions, users, source_ad_fact_id,
            source_ga_fact_id, applied_rule_ids, transform_status, created_at, updated_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            context.report_month,
            report_date,
            import_table.metadata.source_part,
            import_table.metadata.source_group,
            import_table.metadata.source_name,
            import_table.metadata.source_group,
            media_name,
            import_table.metadata.source_part,
            device,
            import_table.metadata.source_name,
            device_raw,
            campaign_name,
            ad_group_name,
            creative_name,
            keyword_name,
            impressions,
            clicks,
            cost_raw,
            sessions,
            users,
            source_ad_fact_id,
            source_ga_fact_id,
            json_text([]),
            "generated",
            now,
            now,
        ),
    )


def insert_rows(connection: pymysql.connections.Connection, context: ImportContext, import_table: ImportTable, raw_data_dir: Path) -> dict[str, int]:
    stats = {"raw_records": 0, "bronze_only_rows": 0, "fact_ad_daily": 0, "fact_ga_daily": 0, "report_daily_row": 0, "source_field_mappings": 0}
    now = utc_now()
    with connection.cursor() as cursor:
        media_source_id = get_media_source_id(cursor, context, import_table.metadata)
        media_id = get_dim_media_id(cursor, context, media_source_id, import_table.metadata)
        schema_profile_id = get_schema_profile_id(cursor, context, media_source_id, import_table)
        stats["source_field_mappings"] += insert_source_field_mappings(cursor, context, schema_profile_id, import_table.headers)
        field_mapping = load_source_field_mapping(cursor, schema_profile_id)
        consumed_fields = set(field_mapping)
        file_id = insert_source_file(cursor, context, import_table, raw_data_dir, media_source_id)
        row_count = 0
        for offset, row in enumerate(import_table.table.rows[import_table.header_idx + 1 :], start=import_table.header_idx + 2):
            if not non_empty_cells(row):
                continue
            payload = row_payload(import_table.headers, row)
            canonical = canonical_payload(payload, field_mapping)
            report_date = canonical_report_date(canonical)
            if report_date is None:
                parse_error = "Missing daily report date; preserved in Bronze only and skipped from Silver/Gold daily layers"
                insert_raw_record(cursor, file_id, offset, payload, "bronze_only", parse_error, now)
                stats["raw_records"] += 1
                stats["bronze_only_rows"] += 1
                row_count += 1
                continue
            raw_record_id = insert_raw_record(cursor, file_id, offset, payload, "parsed", None, now)
            stats["raw_records"] += 1
            if import_table.metadata.fact_target == "fact_ga_daily":
                insert_ga_fact(
                    cursor,
                    context,
                    import_table,
                    file_id,
                    raw_record_id,
                    media_source_id,
                    media_id,
                    payload,
                    canonical,
                    consumed_fields,
                    report_date,
                    now,
                )
                stats["fact_ga_daily"] += 1
            else:
                insert_ad_fact(
                    cursor,
                    context,
                    import_table,
                    file_id,
                    raw_record_id,
                    media_source_id,
                    media_id,
                    payload,
                    canonical,
                    consumed_fields,
                    report_date,
                    now,
                )
                stats["fact_ad_daily"] += 1
            stats["report_daily_row"] += 1
            row_count += 1
        cursor.execute(
            "UPDATE source_file SET imported_rows = %s, import_status = %s WHERE file_id = %s",
            (row_count, "imported", file_id),
        )
    return stats


def import_raw_data(
    config: DbConfig,
    raw_data_dir: Path,
    schema_path: Path,
    reset: bool,
    advertiser_code: str,
    report_month: str,
    batch_name: str | None,
    uploaded_by: str | None,
) -> dict[str, int]:
    if not raw_data_dir.exists():
        raise FileNotFoundError(f"raw_data directory does not exist: {raw_data_dir}")
    if raw_data_dir.name != "raw_data":
        raise ValueError(f"Expected a directory named raw_data, got: {raw_data_dir}")

    import_tables, stats = discover_import_tables(raw_data_dir)
    connection = connect_db(config)
    batch_id: int | None = None
    try:
        reset_schema(connection, schema_path, reset)
        seed_metric_registry(connection)
        standard_fields = seed_standard_fields(connection)
        batch_id = create_import_batch(connection, advertiser_code, report_month, batch_name, uploaded_by, raw_data_dir)
        context = ImportContext(batch_id=batch_id, report_month=report_month)
        context.standard_fields = standard_fields
        connection.commit()
        for import_table in import_tables:
            table_stats = insert_rows(connection, context, import_table, raw_data_dir)
            for key, value in table_stats.items():
                stats[key] = stats.get(key, 0) + value
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE import_batch SET status = %s, message = %s WHERE batch_id = %s",
                ("success", json_text(stats), batch_id),
            )
        connection.commit()
    except Exception as exc:
        connection.rollback()
        if batch_id is not None:
            with connection.cursor() as cursor:
                cursor.execute(
                    "UPDATE import_batch SET status = %s, message = %s WHERE batch_id = %s",
                    ("failed", str(exc), batch_id),
                )
            connection.commit()
        raise
    finally:
        connection.close()
    return stats


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Load local raw data files into the layered MariaDB schema.")
    parser.add_argument("--host", default=DEFAULT_DB_HOST, help="MariaDB host")
    parser.add_argument("--port", type=int, default=DEFAULT_DB_PORT, help="MariaDB port")
    parser.add_argument("--user", default=DEFAULT_DB_USER, help="MariaDB user")
    parser.add_argument("--password", default=DEFAULT_DB_PASSWORD, help="MariaDB password")
    parser.add_argument("--database", default=DEFAULT_DB_NAME, help="MariaDB database name")
    parser.add_argument("--raw-data-dir", type=Path, default=DEFAULT_RAW_DATA_DIR, help="Directory named raw_data")
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA_PATH, help="MariaDB schema SQL file")
    parser.add_argument("--reset", action="store_true", help="Drop and recreate layered import tables before loading")
    parser.add_argument("--advertiser-code", default=env_value("SQL_WORKFLOW_IMPORT_ADVERTISER_CODE", "PUBLIC_DEMO"), help="Advertiser code stored on import_batch")
    parser.add_argument("--report-month", default=env_value("SQL_WORKFLOW_IMPORT_REPORT_MONTH", "YYYY-MM"), help="Report month stored on import_batch/report_daily_row")
    parser.add_argument("--batch-name", default=env_value("SQL_WORKFLOW_IMPORT_BATCH_NAME", "local_import"), help="Import batch name")
    parser.add_argument("--uploaded-by", default=env_value("SQL_WORKFLOW_IMPORT_UPLOADED_BY", "local"), help="Uploader label stored on import_batch")
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
        stats = import_raw_data(
            config,
            args.raw_data_dir,
            args.schema,
            args.reset,
            args.advertiser_code,
            args.report_month,
            args.batch_name,
            args.uploaded_by,
        )
    except Exception as exc:
        print(f"Import failed: {exc}", file=sys.stderr)
        return 1
    print(f"DB: {config.user}@{config.host}:{config.port}/{config.database}")
    print(f"raw_data: {args.raw_data_dir}")
    print(f"Scanned files: {stats['files']}")
    print(f"Imported source tables: {stats['tables'] - stats['skipped_tables']}")
    print(f"Skipped tables without headers: {stats['skipped_tables']}")
    print(f"Imported raw_record rows: {stats['raw_records']}")
    print(f"Bronze-only raw rows: {stats['bronze_only_rows']}")
    print(f"Imported fact_ad_daily rows: {stats['fact_ad_daily']}")
    print(f"Imported fact_ga_daily rows: {stats['fact_ga_daily']}")
    print(f"Generated report_daily_row rows: {stats['report_daily_row']}")
    print("Reference report XLSB was not ingested; only .csv/.xlsx files under raw_data were scanned.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
