from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from typing_extensions import TypedDict


ALLOWED_TABLES = ("DA", "SA")
ALLOWED_SQL_TYPES = {"SELECT", "INSERT", "UPDATE", "DELETE"}
PREDICATE_SQL_TYPES = {"UPDATE", "DELETE"}
DANGEROUS_SQL_TOKENS = (";", "--", "/*", "*/")
PROTECTED_WRITE_COLUMNS = {"source_channel", "날짜", "세션 소스/매체", "세션 캠페인", "캠페인", "광고 그룹"}

def env_value(name: str, default: str) -> str:
    return os.environ.get(name) or default


def env_int(name: str, default: int) -> int:
    return int(env_value(name, str(default)))


DEFAULT_DB_HOST = env_value("SQL_WORKFLOW_DB_HOST", "127.0.0.1")
DEFAULT_DB_PORT = env_int("SQL_WORKFLOW_DB_PORT", 3307)
DEFAULT_DB_USER = env_value("SQL_WORKFLOW_DB_USER", "workflow_user")
DEFAULT_DB_PASSWORD = env_value("SQL_WORKFLOW_DB_PASSWORD", "")
DEFAULT_DB_NAME = env_value("SQL_WORKFLOW_DB_NAME", "approval_workflow")


@dataclass(frozen=True)
class DbConfig:
    host: str = DEFAULT_DB_HOST
    port: int = DEFAULT_DB_PORT
    user: str = DEFAULT_DB_USER
    password: str = DEFAULT_DB_PASSWORD
    database: str = DEFAULT_DB_NAME


class ModificationWorkflowState(TypedDict, total=False):
    selection_text: str
    schema_summary: str
    table_columns: dict[str, list[str]]
    source_channel_values: dict[str, list[str]]
    source_channel_mappings: list[dict[str, Any]]
    column_alias_mappings: list[dict[str, Any]]
    metric_definitions: list[dict[str, Any]]
    protected_column_policies: list[dict[str, Any]]
    stored_rules: list[dict[str, Any]]
    selection_request: dict[str, Any]
    ir_structured_json: dict[str, Any]
    selection_sql_plan: dict[str, Any]
    selection_validation_result: dict[str, Any]
    target_rows: list[dict[str, Any]]
    preview_rows: list[dict[str, Any]]
    modification_text: str
    modification_logic: dict[str, Any]
    mongo_query: dict[str, Any]
    matched_rules: list[dict[str, Any]]
    effective_modification_plan: dict[str, Any]
    precompiled_where: dict[str, Any]
    sql_candidate: dict[str, Any]
    parsed_sql: dict[str, Any]
    validation_result: dict[str, Any]
    change_preview_json: dict[str, Any]
    user_confirmation: dict[str, Any]
    execution_result: dict[str, Any]
    output_json: dict[str, Any]
    approved_sql_fingerprint: str
    approved_preview_fingerprint: str
    errors: list[str]


def append_error(state: ModificationWorkflowState, message: str) -> list[str]:
    return [*state.get("errors", []), message]
