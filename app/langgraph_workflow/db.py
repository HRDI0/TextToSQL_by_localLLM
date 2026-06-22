from __future__ import annotations

# pyright: reportTypedDictNotRequiredAccess=false

import os
import json
from typing import Any

import pymysql  # type: ignore[import-not-found]

from app.langgraph_workflow.state import (
    ALLOWED_TABLES,
    DEFAULT_DB_HOST,
    DEFAULT_DB_NAME,
    DEFAULT_DB_PASSWORD,
    DEFAULT_DB_PORT,
    DEFAULT_DB_USER,
    DbConfig,
    ModificationWorkflowState,
    append_error,
)


def db_config_from_env() -> DbConfig:
    return DbConfig(
        host=os.environ.get("SQL_WORKFLOW_DB_HOST") or os.environ.get("KTM_DB_HOST") or DEFAULT_DB_HOST,
        port=int(os.environ.get("SQL_WORKFLOW_DB_PORT") or os.environ.get("KTM_DB_PORT") or str(DEFAULT_DB_PORT)),
        user=os.environ.get("SQL_WORKFLOW_DB_USER") or os.environ.get("KTM_DB_USER") or DEFAULT_DB_USER,
        password=os.environ.get("SQL_WORKFLOW_DB_PASSWORD") or os.environ.get("KTM_DB_PASSWORD") or DEFAULT_DB_PASSWORD,
        database=os.environ.get("SQL_WORKFLOW_DB_NAME") or os.environ.get("KTM_DB_NAME") or DEFAULT_DB_NAME,
    )


def connect_db(config: DbConfig | None = None) -> pymysql.connections.Connection:
    selected = config or db_config_from_env()
    if not selected.password:
        raise ValueError("SQL_WORKFLOW_DB_PASSWORD must be set before connecting to MariaDB.")
    return pymysql.connect(
        host=selected.host,
        port=selected.port,
        user=selected.user,
        password=selected.password,
        database=selected.database,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False,
    )


def quote_identifier(identifier: str) -> str:
    return f"`{identifier.replace('`', '``')}`"


def fetch_table_columns(connection: Any, database: str, table_names: tuple[str, ...] = ALLOWED_TABLES) -> dict[str, list[str]]:
    placeholders = ", ".join(["%s"] * len(table_names))
    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            SELECT TABLE_NAME, COLUMN_NAME
            FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = %s
              AND TABLE_NAME IN ({placeholders})
            ORDER BY TABLE_NAME, ORDINAL_POSITION
            """,
            (database, *table_names),
        )
        rows = list(cursor.fetchall())

    columns: dict[str, list[str]] = {table_name: [] for table_name in table_names}
    for row in rows:
        table_name = row["TABLE_NAME"]
        if table_name in columns:
            columns[table_name].append(row["COLUMN_NAME"])
    return {table: names for table, names in columns.items() if names}


def fetch_source_channel_values(
    connection: Any,
    table_columns: dict[str, list[str]],
    limit_per_table: int = 100,
) -> dict[str, list[str]]:
    values: dict[str, list[str]] = {}
    for table_name in ALLOWED_TABLES:
        if "source_channel" not in table_columns.get(table_name, []):
            continue
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT DISTINCT `source_channel` AS source_channel
                FROM {quote_identifier(table_name)}
                WHERE `source_channel` IS NOT NULL AND `source_channel` <> ''
                ORDER BY `source_channel`
                LIMIT %s
                """,
                (limit_per_table,),
            )
            values[table_name] = [row["source_channel"] for row in cursor.fetchall()]
    return values


def table_exists(connection: Any, database: str, table_name: str) -> bool:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT COUNT(*) AS table_count
            FROM information_schema.TABLES
            WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s
            """,
            (database, table_name),
        )
        return int(cursor.fetchone()["table_count"]) > 0


def fetch_business_dictionary_metadata(connection: Any, database: str) -> dict[str, list[dict[str, Any]]]:
    metadata = {
        "source_channel_mappings": [],
        "column_alias_mappings": [],
        "metric_definitions": [],
        "protected_column_policies": [],
        "column_catalog": [],
        "value_catalog": [],
    }
    if table_exists(connection, database, "rule_engine_source_channel_map"):
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT user_term, target_table, source_channel, priority
                FROM rule_engine_source_channel_map
                WHERE active = TRUE
                ORDER BY priority, user_term, source_channel
                """
            )
            metadata["source_channel_mappings"] = list(cursor.fetchall())
    if table_exists(connection, database, "rule_engine_column_alias_map"):
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT user_term, target_table, target_column, semantic_role, priority
                FROM rule_engine_column_alias_map
                WHERE active = TRUE
                ORDER BY priority, user_term, target_column
                """
            )
            metadata["column_alias_mappings"] = list(cursor.fetchall())
    if table_exists(connection, database, "rule_engine_metric_definition"):
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT metric_code, user_term, expression_type, source_column, denominator_column, zero_fallback,
                       target_table, source_channel_scope, event_filter, business_definition, priority
                FROM rule_engine_metric_definition
                WHERE active = TRUE
                ORDER BY priority, metric_code
                """
            )
            metric_rows = list(cursor.fetchall())
            for row in metric_rows:
                for key in ("source_channel_scope", "event_filter"):
                    if isinstance(row.get(key), str) and row[key]:
                        row[key] = json.loads(row[key])
            metadata["metric_definitions"] = metric_rows
    if table_exists(connection, database, "rule_engine_protected_column_policy"):
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT target_table, column_name, protection_level, reason
                FROM rule_engine_protected_column_policy
                WHERE active = TRUE
                ORDER BY target_table, column_name, protection_level
                """
            )
            metadata["protected_column_policies"] = list(cursor.fetchall())
    if table_exists(connection, database, "rule_engine_column_catalog"):
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT target_table, column_name, normalized_column_name, semantic_role, distinct_count
                FROM rule_engine_column_catalog
                ORDER BY target_table, column_name
                """
            )
            metadata["column_catalog"] = list(cursor.fetchall())
    if table_exists(connection, database, "rule_engine_value_catalog"):
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT target_table, column_name, normalized_value, raw_value, frequency
                FROM rule_engine_value_catalog
                ORDER BY frequency DESC, target_table, column_name
                LIMIT 500
                """
            )
            metadata["value_catalog"] = list(cursor.fetchall())
    return metadata


def load_schema_metadata(config: DbConfig | None = None, connection: Any = None) -> tuple[dict[str, list[str]], dict[str, list[str]], dict[str, list[dict[str, Any]]]]:
    selected = config or db_config_from_env()
    if connection is not None:
        table_columns = fetch_table_columns(connection, selected.database)
        return table_columns, fetch_source_channel_values(connection, table_columns), fetch_business_dictionary_metadata(connection, selected.database)

    with connect_db(selected) as new_connection:
        table_columns = fetch_table_columns(new_connection, selected.database)
        return table_columns, fetch_source_channel_values(new_connection, table_columns), fetch_business_dictionary_metadata(new_connection, selected.database)


def build_schema_summary(
    table_columns: dict[str, list[str]],
    source_channel_values: dict[str, list[str]] | None = None,
    dictionary_metadata: dict[str, list[dict[str, Any]]] | None = None,
) -> str:
    if not table_columns:
        return "No live DA/SA schema metadata is loaded. Query information_schema before constructing SQL."

    lines: list[str] = []
    for table_name in ALLOWED_TABLES:
        columns = table_columns.get(table_name, [])
        if not columns:
            continue
        lines.append(f"{table_name} columns: {', '.join(columns)}")
        source_channels = (source_channel_values or {}).get(table_name, [])
        if source_channels:
            lines.append(f"{table_name} source_channel candidates: {', '.join(source_channels)}")
    dictionaries = dictionary_metadata or {}
    source_maps = dictionaries.get("source_channel_mappings", [])
    if source_maps:
        rendered = [f"{item['user_term']} -> {item['target_table']}.{item['source_channel']}" for item in source_maps[:80]]
        lines.append(f"business source_channel mappings: {', '.join(rendered)}")
    alias_maps = dictionaries.get("column_alias_mappings", [])
    if alias_maps:
        rendered = [f"{item['user_term']} -> {item['target_table']}.{item['target_column']}" for item in alias_maps[:80]]
        lines.append(f"business column alias mappings: {', '.join(rendered)}")
    metric_defs = dictionaries.get("metric_definitions", [])
    if metric_defs:
        rendered = [f"{item['user_term']} -> {item['expression_type']}({item['source_column']})" for item in metric_defs[:80]]
        lines.append(f"business metric definitions: {', '.join(rendered)}")
    return "\n".join(lines)


def load_schema_metadata_node(
    state: ModificationWorkflowState,
    connection: Any = None,
    db_config: DbConfig | None = None,
) -> dict[str, Any]:
    if state.get("table_columns"):
        table_columns = state["table_columns"]
        source_channel_values = state.get("source_channel_values", {})
        dictionary_metadata = {
            "source_channel_mappings": state.get("source_channel_mappings", []),
            "column_alias_mappings": state.get("column_alias_mappings", []),
            "metric_definitions": state.get("metric_definitions", []),
            "protected_column_policies": state.get("protected_column_policies", []),
            "column_catalog": state.get("column_catalog", []),
            "value_catalog": state.get("value_catalog", []),
        }
        return {
            "table_columns": table_columns,
            "source_channel_values": source_channel_values,
            "schema_summary": build_schema_summary(table_columns, source_channel_values, dictionary_metadata),
        }

    try:
        table_columns, source_channel_values, dictionary_metadata = load_schema_metadata(config=db_config, connection=connection)
    except Exception as exc:
        return {
            "table_columns": {},
            "source_channel_values": {},
            "source_channel_mappings": [],
            "column_alias_mappings": [],
            "metric_definitions": [],
            "protected_column_policies": [],
            "column_catalog": [],
            "value_catalog": [],
            "schema_summary": build_schema_summary({}),
            "errors": append_error(state, f"schema_metadata_query_failed: {exc}"),
        }

    if not table_columns:
        return {
            "table_columns": {},
            "source_channel_values": source_channel_values,
            "source_channel_mappings": dictionary_metadata.get("source_channel_mappings", []),
            "column_alias_mappings": dictionary_metadata.get("column_alias_mappings", []),
            "metric_definitions": dictionary_metadata.get("metric_definitions", []),
            "protected_column_policies": dictionary_metadata.get("protected_column_policies", []),
            "column_catalog": dictionary_metadata.get("column_catalog", []),
            "value_catalog": dictionary_metadata.get("value_catalog", []),
            "schema_summary": build_schema_summary({}),
            "errors": append_error(state, "schema_metadata_query_returned_no_DA_SA_columns"),
        }
    return {
        "table_columns": table_columns,
        "source_channel_values": source_channel_values,
        "source_channel_mappings": dictionary_metadata.get("source_channel_mappings", []),
        "column_alias_mappings": dictionary_metadata.get("column_alias_mappings", []),
        "metric_definitions": dictionary_metadata.get("metric_definitions", []),
        "protected_column_policies": dictionary_metadata.get("protected_column_policies", []),
        "column_catalog": dictionary_metadata.get("column_catalog", []),
        "value_catalog": dictionary_metadata.get("value_catalog", []),
        "schema_summary": build_schema_summary(table_columns, source_channel_values, dictionary_metadata),
    }
