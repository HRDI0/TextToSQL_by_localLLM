from __future__ import annotations

# pyright: reportTypedDictNotRequiredAccess=false

import hashlib
import json
import re
from typing import Any

from app.langgraph_workflow.db import build_schema_summary, quote_identifier
from app.langgraph_workflow.import_rules import resolve_column_from_import_rules
from app.langgraph_workflow.stage_01_parse import invoke_llm_json
from app.langgraph_workflow.state import (
    ALLOWED_SQL_TYPES,
    ALLOWED_TABLES,
    DANGEROUS_SQL_TOKENS,
    PREDICATE_SQL_TYPES,
    PROTECTED_WRITE_COLUMNS,
    ModificationWorkflowState,
    append_error,
)


def fingerprint_sql(sql: str, params: list[Any]) -> str:
    payload = json.dumps({"sql": sql, "params": params}, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


AGGREGATE_INTENT = "SELECT_AGGREGATE"
SELECT_DETAIL_INTENT = "SELECT_DETAIL"
UPDATE_INTENT = "UPDATE_NUMERIC_VALUE"
ADD_DERIVED_COLUMN_INTENT = "ADD_DERIVED_COLUMN"
ASK_CLARIFICATION_INTENT = "ASK_CLARIFICATION"
NUMERIC_COLUMNS = {"노출수", "클릭수", "비용", "세션수"}
DERIVED_VALUE_TABLE = "rule_engine_derived_value"
FIELD_NAME_FALLBACKS = {
    "campaign": ["캠페인", "세션 캠페인"],
    "campaignname": ["캠페인", "세션 캠페인"],
    "campaign_name": ["캠페인", "세션 캠페인"],
    "campaigntype": ["캠페인 유형", "캠페인"],
    "campaign_type": ["캠페인 유형", "캠페인"],
    "keyword": ["세션 수동 검색어", "광고소재요소"],
    "creative": ["광고 소재", "세션 수동 광고 콘텐츠", "광고소재요소"],
    "creativename": ["광고 소재", "세션 수동 광고 콘텐츠", "광고소재요소"],
    "creative_name": ["광고 소재", "세션 수동 광고 콘텐츠", "광고소재요소"],
    "impressions": ["노출수"],
    "impression": ["노출수"],
    "clicks": ["클릭수"],
    "click": ["클릭수"],
    "cost": ["비용"],
    "spend": ["비용"],
    "sessions": ["세션수"],
    "session": ["세션수"],
    "conversions": ["주요 이벤트", "세션수"],
    "conversion": ["주요 이벤트", "세션수"],
}


def is_numeric_literal(value: Any) -> bool:
    try:
        float(str(value).replace(",", ""))
        return True
    except ValueError:
        return False


def numeric_sql(column: str) -> str:
    return f"CAST(NULLIF({quote_identifier(column)}, '') AS DECIMAL(18,2))"


def column_sql_for_comparison(column: str, values: list[Any]) -> str:
    if column in NUMERIC_COLUMNS or any(is_numeric_literal(value) for value in values):
        return numeric_sql(column)
    return quote_identifier(column)


def is_protected_write_column(column: str) -> bool:
    normalized = re.sub(r"\s+", "", column).lower()
    if column in PROTECTED_WRITE_COLUMNS:
        return True
    if normalized in {"id", "아이디"}:
        return True
    if normalized.endswith("id") or normalized.endswith("아이디"):
        return True
    if "sourcechannel" in normalized or "rawsource" in normalized:
        return True
    return False


def is_protected_by_policy(column: str, table_name: str, policies: list[dict[str, Any]] | None = None) -> bool:
    if is_protected_write_column(column):
        return True
    for policy in policies or []:
        if str(policy.get("target_table")) != table_name:
            continue
        if str(policy.get("column_name")) != column:
            continue
        if str(policy.get("protection_level") or "") == "block_update":
            return True
    return False


def resolve_workflow_column(
    field: str,
    table_name: str,
    table_columns: dict[str, list[str]],
    column_alias_mappings: list[dict[str, Any]] | None = None,
) -> str:
    resolved = resolve_column_from_import_rules(field, table_name, table_columns)
    if resolved in table_columns.get(table_name, []):
        return resolved
    normalized_field = re.sub(r"\s+", "", field.split(".", 1)[-1].strip("`")).lower()
    for item in column_alias_mappings or []:
        if str(item.get("target_table")) != table_name:
            continue
        user_term = re.sub(r"\s+", "", str(item.get("user_term", ""))).lower()
        target_column = str(item.get("target_column", ""))
        if user_term and user_term == normalized_field and target_column in table_columns.get(table_name, []):
            return target_column
    for candidate in FIELD_NAME_FALLBACKS.get(normalized_field, []):
        if candidate in table_columns.get(table_name, []):
            return candidate
    return resolved


def alias_candidate_columns(
    field: str,
    table_name: str,
    table_columns: dict[str, list[str]],
    column_alias_mappings: list[dict[str, Any]] | None = None,
) -> list[str]:
    normalized_field = re.sub(r"\s+", "", field.split(".", 1)[-1].strip("`")).lower()
    columns = table_columns.get(table_name, [])
    candidates: list[str] = []
    for item in column_alias_mappings or []:
        if str(item.get("target_table")) != table_name:
            continue
        user_term = re.sub(r"\s+", "", str(item.get("user_term", ""))).lower()
        target_column = str(item.get("target_column", ""))
        if user_term and user_term == normalized_field and target_column in columns:
            candidates.append(target_column)
    for candidate in FIELD_NAME_FALLBACKS.get(normalized_field, []):
        if candidate in columns:
            candidates.append(candidate)
    return unique_preserve_order(candidates)


def requested_action_columns_from_text(
    text: str,
    table_name: str,
    table_columns: dict[str, list[str]],
    column_alias_mappings: list[dict[str, Any]] | None = None,
    protected_column_policies: list[dict[str, Any]] | None = None,
) -> list[str]:
    action_text = text
    for marker in ["데이터는", "건은", "row는", "행은"]:
        if marker in action_text:
            action_text = action_text.split(marker, 1)[1]
            break
    compact = compact_text(action_text)
    columns = table_columns.get(table_name, [])
    requested: list[str] = []
    for item in column_alias_mappings or []:
        if str(item.get("target_table")) != table_name:
            continue
        if str(item.get("semantic_role") or "") != "metric":
            continue
        user_term = compact_text(item.get("user_term"))
        target_column = str(item.get("target_column") or "")
        if user_term and user_term in compact and target_column in columns and not is_protected_by_policy(target_column, table_name, protected_column_policies):
            requested.append(target_column)
    for column in columns:
        column_token = compact_text(column)
        if column_token and column_token in compact and not is_protected_by_policy(column, table_name, protected_column_policies):
            requested.append(column)
    return unique_preserve_order(requested)


def build_selection_sql(
    selection_request: dict[str, Any],
    table_columns: dict[str, list[str]],
    source_channel_values: dict[str, list[str]],
) -> dict[str, Any]:
    table_name = selection_request.get("tables", ["SA"])[0]
    if table_name not in ALLOWED_TABLES:
        raise ValueError("Only DA and SA are allowed.")

    compiled_scope = compile_selection_scope_to_where(selection_request, table_columns, source_channel_values)
    where_sql = compiled_scope.get("sql", "1 = 1")
    params = list(compiled_scope.get("params", []))
    sql = f"SELECT * FROM {quote_identifier(table_name)} WHERE {where_sql}"
    return {
        "sql_type": "SELECT",
        "sql": sql,
        "params": params,
        "target_table": table_name,
        "sql_fingerprint": fingerprint_sql(sql, params),
    }


def generate_selection_sql_node(state: ModificationWorkflowState) -> dict[str, Any]:
    return {
        "selection_sql_plan": build_selection_sql(
            state["selection_request"],
            state.get("table_columns", {}),
            state.get("source_channel_values", {}),
        )
    }


def validate_selection_sql_node(state: ModificationWorkflowState) -> dict[str, Any]:
    plan = state["selection_sql_plan"]
    selection_request = state["selection_request"]
    table_name = plan.get("target_table")
    table_columns = state.get("table_columns", {})
    errors: list[str] = []
    checks: list[str] = []
    unresolved_terms = unresolved_selection_terms(
        selection_request.get("unresolved_terms", []),
        str(table_name or ""),
        table_columns,
        state.get("column_alias_mappings", []),
    )

    if plan.get("sql_type") == "SELECT":
        checks.append("select_only")
    else:
        errors.append("selection_must_be_select")
    if unresolved_terms:
        errors.append(f"selection_has_unresolved_terms: {unresolved_terms}")
    else:
        checks.append("selection_terms_resolved")
    if table_name in table_columns:
        checks.append("table_allowed_by_live_schema")
    else:
        errors.append("unknown_selection_table_in_live_schema")
    if source_channel_filter_is_valid(selection_request, table_columns, state.get("source_channel_values", {})):
        checks.append("source_channel_filter_valid")
    else:
        errors.append("source_channel_filter_not_in_live_schema_or_candidates")
    try:
        compile_selection_scope_to_where(selection_request, table_columns, state.get("source_channel_values", {}))
        checks.append("selection_scope_compilable")
    except ValueError as error:
        errors.append(str(error))
    if plan.get("sql", "").count("%s") == len(plan.get("params", [])):
        checks.append("parameter_count_matches")
    else:
        errors.append("parameter_count_mismatch")

    return {"selection_validation_result": {"status": "passed" if not errors else "failed", "checks": checks, "errors": errors}}


def unresolved_selection_terms(
    unresolved_terms: list[Any],
    table_name: str,
    table_columns: dict[str, list[str]],
    column_alias_mappings: list[dict[str, Any]] | None = None,
) -> list[Any]:
    remaining: list[Any] = []
    for term in unresolved_terms:
        text = str(term or "").strip()
        compact = compact_text(text)
        if not text:
            continue
        if table_name == "SA" and "검색광고" in compact:
            continue
        if table_name == "DA" and "디스플레이" in compact:
            continue
        resolved = resolve_workflow_column(text, table_name, table_columns, column_alias_mappings)
        if resolved in table_columns.get(table_name, []):
            continue
        remaining.append(term)
    return remaining


def source_channel_filter_is_valid(
    selection_request: dict[str, Any],
    table_columns: dict[str, list[str]],
    source_channel_values: dict[str, list[str]],
) -> bool:
    source_channels = list(selection_request.get("source_channels", []))
    if not source_channels:
        return True
    table_name = (selection_request.get("tables") or [None])[0]
    if not isinstance(table_name, str):
        return False
    live_values = set(source_channel_values.get(table_name, []))
    return "source_channel" in table_columns.get(table_name, []) and all(value in live_values for value in source_channels)


def fetch_target_dataset_node(state: ModificationWorkflowState, connection: Any = None) -> dict[str, Any]:
    if state.get("target_rows"):
        return {"target_rows": state["target_rows"]}
    if state.get("selection_validation_result", {}).get("status") != "passed":
        return {"target_rows": [], "errors": append_error(state, "selection_sql_validation_failed")}
    if connection is None:
        return {"target_rows": [], "errors": append_error(state, "target_dataset_query_skipped_no_connection")}

    with connection.cursor() as cursor:
        plan = state["selection_sql_plan"]
        cursor.execute(plan["sql"], tuple(plan.get("params", [])))
        return {"target_rows": list(cursor.fetchall())}


def compile_condition_to_where(
    condition: dict[str, Any],
    table_name: str,
    table_columns: dict[str, list[str]],
    column_alias_mappings: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    operator = str(condition.get("operator", "eq")).lower()
    raw_field_for_group = str(condition.get("field") or "")
    if operator in {"or", "and"}:
        compiled_children = [
            compile_condition_to_where(child, table_name, table_columns, column_alias_mappings)
            for child in condition.get("conditions", [])
            if isinstance(child, dict)
        ]
        compiled_children = [child for child in compiled_children if child.get("sql") and child.get("sql") != "1 = 1"]
        if not compiled_children and raw_field_for_group and raw_field_for_group != "__group__":
            recovered = {**condition, "operator": "in" if len(condition.get("values", [])) > 1 else "eq"}
            recovered.pop("conditions", None)
            return compile_condition_to_where(recovered, table_name, table_columns, column_alias_mappings)
        if not compiled_children:
            raise ValueError(f"{operator}_condition_group_requires_children")
        joiner = " OR " if operator == "or" else " AND "
        return {
            "sql": "(" + joiner.join(child["sql"] for child in compiled_children) + ")",
            "params": [param for child in compiled_children for param in child.get("params", [])],
            "columns": [column for child in compiled_children for column in child.get("columns", [])],
            "predicate": [item for child in compiled_children for item in child.get("predicate", [])],
        }

    raw_field = condition["field"]
    values = list(condition.get("values", []))
    alias_candidates = [] if raw_field in table_columns.get(table_name, []) else alias_candidate_columns(raw_field, table_name, table_columns, column_alias_mappings)
    if len(alias_candidates) > 1:
        values = list(condition.get("values", []))
        if not values:
            raise ValueError(f"Condition has no values: {raw_field}")
        children = [{"field": column, "operator": operator, "values": values} for column in alias_candidates]
        return compile_condition_to_where({"operator": "or", "conditions": children}, table_name, table_columns, column_alias_mappings)
    column = resolve_workflow_column(raw_field, table_name, table_columns, column_alias_mappings)
    if column == "source_channel":
        values = [str(value).split(".", 1)[1] if str(value).startswith(f"{table_name}.") else value for value in values]
    if column not in table_columns.get(table_name, []):
        raise ValueError(f"Unknown condition column in live schema: {raw_field}")

    sql = ""
    params: list[Any] = []
    if operator == "eq" and len(values) == 1:
        left_sql = column_sql_for_comparison(column, values)
        sql = f"{left_sql} = %s"
        params = values
    elif operator in {"eq", "in"}:
        if not values:
            raise ValueError(f"Condition has no values: {raw_field}")
        placeholders = ", ".join(["%s"] * len(values))
        sql = f"{quote_identifier(column)} IN ({placeholders})"
        params = values
        operator = "in"
    elif operator in {"contains", "like"} and len(values) == 1:
        sql = f"{quote_identifier(column)} LIKE %s"
        params = [f"%{values[0]}%"]
        operator = "contains"
    elif operator == "not_contains" and len(values) == 1:
        sql = f"{quote_identifier(column)} NOT LIKE %s"
        params = [f"%{values[0]}%"]
    elif operator == "neq" and len(values) == 1:
        sql = f"{quote_identifier(column)} <> %s"
        params = values
    elif operator in {"gt", "gte", "lt", "lte"} and len(values) == 1:
        symbol = {"gt": ">", "gte": ">=", "lt": "<", "lte": "<="}[operator]
        sql = f"{column_sql_for_comparison(column, values)} {symbol} %s"
        params = values
    elif operator == "between" and len(values) == 2:
        sql = f"{column_sql_for_comparison(column, values)} BETWEEN %s AND %s"
        params = values
    elif operator == "is_null":
        sql = f"{quote_identifier(column)} IS NULL"
    elif operator == "is_empty":
        sql = f"TRIM(COALESCE({quote_identifier(column)}, '')) = ''"
    elif operator == "is_null_or_empty":
        sql = f"({quote_identifier(column)} IS NULL OR TRIM({quote_identifier(column)}) = '')"
    elif operator in {"is_not_null_or_empty", "not_null_or_empty"}:
        sql = f"({quote_identifier(column)} IS NOT NULL AND TRIM({quote_identifier(column)}) <> '')"
    else:
        raise ValueError(f"Unsupported condition operator: {operator}")

    return {
        "sql": sql,
        "params": params,
        "columns": [column],
        "predicate": [{"column": column, "operator": operator, "values": params, "source": "modification"}],
    }


def compile_conditions_to_where(
    modification_logic: dict[str, Any],
    selection_request: dict[str, Any],
    table_columns: dict[str, list[str]],
    column_alias_mappings: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    table_name = selection_request.get("tables", ["SA"])[0]
    compiled_parts: list[dict[str, Any]] = []
    for group in modification_logic.get("condition_groups", []):
        for condition in group.get("conditions", []):
            compiled_parts.append(compile_condition_to_where(condition, table_name, table_columns, column_alias_mappings))
    if not compiled_parts:
        return {"sql": "1 = 1", "params": [], "columns": [], "predicate": []}
    return combine_where_predicates(*compiled_parts)


def compact_text(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).lower()


def normalize_condition_values_from_rows(modification_logic: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
    normalized_groups: list[dict[str, Any]] = []
    for group in modification_logic.get("condition_groups", []):
        normalized_conditions: list[dict[str, Any]] = []
        for condition in group.get("conditions", []):
            field = condition.get("field")
            values = list(condition.get("values", []))
            if field:
                row_values = [row.get(field) for row in rows if row.get(field) not in {None, ""}]
                remapped: list[Any] = []
                for value in values:
                    compact_value = compact_text(value)
                    match = next((row_value for row_value in row_values if compact_text(row_value) == compact_value), value)
                    remapped.append(match)
                values = remapped
            normalized_conditions.append({**condition, "values": values})
        normalized_groups.append({**group, "conditions": normalized_conditions})
    return {**modification_logic, "condition_groups": normalized_groups}


def recover_condition_values_from_text(modification_logic: dict[str, Any], text: str) -> dict[str, Any]:
    recovered_groups: list[dict[str, Any]] = []
    explicit_tokens = re.findall(r"`([^`]+)`", text)
    if not explicit_tokens:
        campaign_match = re.search(r"캠페인\s*이름이\s*([^\s]+)\s*이거나\s*([^\s]+)", text)
        if campaign_match:
            explicit_tokens = [campaign_match.group(1), campaign_match.group(2)]
    for group in modification_logic.get("condition_groups", []):
        recovered_conditions: list[dict[str, Any]] = []
        for condition in group.get("conditions", []):
            field = str(condition.get("field", ""))
            values = list(condition.get("values", []))
            if not values and "캠페인" in field and explicit_tokens:
                condition = {**condition, "operator": "in" if len(explicit_tokens) > 1 else "eq", "values": explicit_tokens}
            recovered_conditions.append(condition)
        recovered_groups.append({**group, "conditions": recovered_conditions})
    return {**modification_logic, "condition_groups": recovered_groups}


def recover_or_conditions_from_text(modification_logic: dict[str, Any], text: str) -> dict[str, Any]:
    lowered = text.lower()
    if not any(token in lowered for token in [" 또는 ", " or ", "이거나", "거나", "나 ", "나`"]):
        return modification_logic
    recovered_groups: list[dict[str, Any]] = []
    for group in modification_logic.get("condition_groups", []):
        conditions = [condition for condition in group.get("conditions", []) if isinstance(condition, dict)]
        grouped: dict[tuple[str, tuple[str, ...]], list[dict[str, Any]]] = {}
        for condition in conditions:
            values_key = tuple(compact_text(value) for value in condition.get("values", []))
            grouped.setdefault((str(condition.get("operator", "eq")).lower(), values_key), []).append(condition)
        replacement: list[dict[str, Any]] = []
        consumed: set[int] = set()
        for candidates in grouped.values():
            if len(candidates) < 2:
                continue
            candidate_ids = {id(candidate) for candidate in candidates}
            replacement.append({"operator": "or", "conditions": candidates})
            consumed.update(candidate_ids)
        replacement.extend(condition for condition in conditions if id(condition) not in consumed)
        recovered_groups.append({**group, "conditions": replacement})
    return {**modification_logic, "condition_groups": recovered_groups}


def compile_selection_scope_to_where(
    selection_request: dict[str, Any],
    table_columns: dict[str, list[str]],
    source_channel_values: dict[str, list[str]],
) -> dict[str, Any]:
    table_name = selection_request.get("tables", ["SA"])[0]
    where_parts: list[str] = []
    params: list[Any] = []
    columns: list[str] = []
    predicate: list[dict[str, Any]] = []

    source_channels = list(selection_request.get("source_channels", []))
    if source_channels:
        live_values = set(source_channel_values.get(table_name, []))
        invalid = [value for value in source_channels if value not in live_values]
        if invalid:
            raise ValueError(f"unknown_source_channel_values: {invalid}")
        if "source_channel" not in table_columns.get(table_name, []):
            raise ValueError("source_channel_column_missing_in_live_schema")
        placeholders = ", ".join(["%s"] * len(source_channels))
        where_parts.append(f"`source_channel` IN ({placeholders})")
        params.extend(source_channels)
        columns.append("source_channel")
        predicate.append({"column": "source_channel", "operator": "in", "values": source_channels, "source": "selection"})

    period = selection_request.get("period") or {}
    if period and not isinstance(period, dict):
        raise ValueError("selection_period_must_be_object")
    if period:
        raw_period_column = selection_request.get("period_column") or selection_request.get("date_column") or period.get("field")
        if not raw_period_column:
            raise ValueError("selection_period_requires_explicit_live_column")
        period_column = resolve_column_from_import_rules(str(raw_period_column), table_name, table_columns)
        if period_column not in table_columns.get(table_name, []):
            raise ValueError(f"unknown_period_column_in_live_schema: {raw_period_column}")
        year = period.get("year")
        month = period.get("month")
        if year and month:
            numeric_month = int(month)
            month_patterns = [
                f"{int(year):04d}-{numeric_month:02d}-%",
                f"{int(year):04d}.{numeric_month:02d}.%",
                f"{int(year):04d}. {numeric_month}.%",
                f"{int(year):04d}.{numeric_month}.%",
            ]
            placeholders = " OR ".join(f"{quote_identifier(period_column)} LIKE %s" for _ in month_patterns)
            where_parts.append(f"({placeholders})")
            params.extend(month_patterns)
            columns.extend([period_column] * len(month_patterns))
            predicate.extend({"column": period_column, "operator": "contains", "values": [pattern], "source": "selection"} for pattern in month_patterns)
            return {"sql": " AND ".join(where_parts) if where_parts else "1 = 1", "params": params, "columns": columns, "predicate": predicate}
        start = period.get("start")
        end = period.get("end")
        if start and end:
            where_parts.append(f"{quote_identifier(period_column)} >= %s")
            where_parts.append(f"{quote_identifier(period_column)} <= %s")
            params.extend([start, end])
            columns.extend([period_column, period_column])
            predicate.append({"column": period_column, "operator": "gte", "values": [start], "source": "selection"})
            predicate.append({"column": period_column, "operator": "lte", "values": [end], "source": "selection"})
        else:
            raise ValueError("selection_period_requires_start_and_end")

    return {"sql": " AND ".join(where_parts) if where_parts else "1 = 1", "params": params, "columns": columns, "predicate": predicate}


def read_only_conditions_to_where(state: ModificationWorkflowState, table_columns: dict[str, list[str]]) -> dict[str, Any]:
    selection_request = state["selection_request"]
    modification_logic = normalize_condition_values_from_rows(state.get("modification_logic", {}), state.get("target_rows", []))
    modification_logic = recover_condition_values_from_text(modification_logic, state.get("modification_text", ""))
    modification_logic = recover_or_conditions_from_text(modification_logic, state.get("modification_text", ""))
    try:
        return compile_conditions_to_where(
            modification_logic,
            selection_request,
            table_columns,
            state.get("column_alias_mappings", []),
        )
    except ValueError:
        return {"sql": "1 = 1", "params": [], "columns": [], "predicate": []}


def combine_where_predicates(*parts: dict[str, Any]) -> dict[str, Any]:
    sql_parts = [part["sql"] for part in parts if part.get("sql") and part.get("sql") != "1 = 1"]
    params: list[Any] = []
    columns: list[str] = []
    predicate: list[dict[str, Any]] = []
    for part in parts:
        params.extend(part.get("params", []))
        columns.extend(part.get("columns", []))
        predicate.extend(part.get("predicate", []))
    return {
        "sql": " AND ".join(sql_parts) if sql_parts else "1 = 1",
        "params": params,
        "columns": columns,
        "predicate": predicate,
    }


def iter_actions(modification_logic: dict[str, Any]) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    for group in modification_logic.get("condition_groups", []):
        raw_actions = group.get("actions", [])
        if isinstance(raw_actions, dict):
            raw_actions = [raw_actions]
        if not isinstance(raw_actions, list):
            raise ValueError("actions must be list")
        for action in raw_actions:
            if isinstance(action, list):
                actions.extend(item for item in action if isinstance(item, dict))
                continue
            if not isinstance(action, dict):
                raise ValueError("each action must be object")
            actions.append(action)
    if not actions:
        raise ValueError("No modification action found.")
    return actions


def action_to_set_clause(
    action: dict[str, Any],
    table_name: str,
    table_columns: dict[str, list[str]],
    column_alias_mappings: list[dict[str, Any]] | None = None,
    protected_column_policies: list[dict[str, Any]] | None = None,
) -> tuple[str, list[Any], str]:
    raw_target = action.get("target_field") or action.get("target_column") or ""
    target_field = resolve_workflow_column(raw_target, table_name, table_columns, column_alias_mappings)
    if is_protected_by_policy(target_field, table_name, protected_column_policies):
        raise ValueError(f"protected column cannot be modified: {target_field}")
    if target_field not in table_columns.get(table_name, []):
        raise ValueError(f"Unknown action target column in live schema: {raw_target}")
    operation = action.get("operation") or action.get("action_type")
    if operation == "set_value":
        operation = "set_literal"
    if operation == "set_literal":
        return f"{quote_identifier(target_field)} = %s", [action.get("value", "")], target_field
    if operation == "set_zero":
        return f"{quote_identifier(target_field)} = %s", [0], target_field
    raise ValueError(f"Unsupported operation: {operation}")


def actions_to_set_clause(
    actions: list[dict[str, Any]],
    table_name: str,
    table_columns: dict[str, list[str]],
    column_alias_mappings: list[dict[str, Any]] | None = None,
    requested_target_fields: list[str] | None = None,
    protected_column_policies: list[dict[str, Any]] | None = None,
) -> tuple[str, list[Any], list[str], list[dict[str, Any]]]:
    clauses: list[str] = []
    params: list[Any] = []
    target_fields: list[str] = []
    normalized_actions: list[dict[str, Any]] = []
    requested_fields = unique_preserve_order(requested_target_fields or [])
    if requested_target_fields is not None and not requested_fields:
        raise ValueError("update_target_requires_db_metric_alias")
    for action in actions:
        set_sql, set_params, target_field = action_to_set_clause(action, table_name, table_columns, column_alias_mappings, protected_column_policies)
        if requested_fields and target_field not in requested_fields:
            continue
        if target_field in target_fields:
            continue
        clauses.append(set_sql)
        params.extend(set_params)
        target_fields.append(target_field)
        normalized_actions.append({**action, "target_field": target_field})
    if requested_fields:
        missing = [field for field in requested_fields if field not in target_fields]
        if missing:
            raise ValueError(f"requested_action_target_missing: {missing}")
    if not clauses:
        raise ValueError("No requested modification action found.")
    return ", ".join(clauses), params, target_fields, normalized_actions


def build_blocked_sql_candidate(reason: str, target_table: str | None = None) -> dict[str, Any]:
    return {
        "source": "blocked_before_sql_generation",
        "sql_type": "UNKNOWN",
        "sql": "",
        "params": [],
        "referenced_columns": [],
        "target_table": target_table,
        "reason": reason,
    }


def first_requested_update_column(
    state: ModificationWorkflowState,
    target_table: str,
    table_columns: dict[str, list[str]],
) -> str | None:
    requested = requested_action_columns_from_text(
        state.get("modification_text", ""),
        target_table,
        table_columns,
        state.get("column_alias_mappings", []),
        state.get("protected_column_policies", []),
    )
    if requested:
        return requested[0]
    for group in state.get("modification_logic", {}).get("condition_groups", []):
        for action in group.get("actions", []):
            if not isinstance(action, dict):
                continue
            target = resolve_workflow_column(
                str(action.get("target_field") or action.get("target_column") or ""),
                target_table,
                table_columns,
                state.get("column_alias_mappings", []),
            )
            if target in table_columns.get(target_table, []) and not is_protected_by_policy(target, target_table, state.get("protected_column_policies", [])):
                return target
    return None


def build_review_only_sql_candidate_after_error(
    state: ModificationWorkflowState,
    table_columns: dict[str, list[str]],
    target_table: str,
    reason: str,
) -> dict[str, Any]:
    selection_where = compile_selection_scope_to_where(state.get("selection_request", {"tables": [target_table]}), table_columns, state.get("source_channel_values", {}))
    target_field = first_requested_update_column(state, target_table, table_columns)
    if target_field:
        where_sql = selection_where.get("sql") or "1 = 1"
        sql = f"UPDATE {quote_identifier(target_table)} SET {quote_identifier(target_field)} = %s WHERE {where_sql}"
        params = ["0", *selection_where.get("params", [])]
        return {
            "source": "review_only_fallback_after_generation_error",
            "sql_type": "UPDATE",
            "sql": sql,
            "params": params,
            "referenced_columns": unique_preserve_order([target_field, *selection_where.get("columns", [])]),
            "target_table": target_table,
            "reason": f"query_generation_error_review_only_sql: {reason}",
            "actions": [{"target_field": target_field, "operation": "set_literal", "value": "0"}],
            "predicate": selection_where.get("predicate", []),
            "execution_allowed": False,
            "sql_fingerprint": fingerprint_sql(sql, params),
        }
    where_sql = selection_where.get("sql") or "1 = 1"
    sql = f"SELECT * FROM {quote_identifier(target_table)} WHERE {where_sql}"
    params = list(selection_where.get("params", []))
    return {
        "source": "review_only_fallback_after_generation_error",
        "sql_type": "SELECT",
        "sql": sql,
        "params": params,
        "referenced_columns": selection_where.get("columns", []),
        "target_table": target_table,
        "reason": f"query_generation_error_review_only_sql: {reason}",
        "actions": [],
        "predicate": selection_where.get("predicate", []),
        "execution_allowed": False,
        "sql_fingerprint": fingerprint_sql(sql, params),
    }


def compile_mongodb_rule_to_sql_candidate(
    rule: dict[str, Any],
    precompiled_where: dict[str, Any],
    table_columns: dict[str, list[str]],
    column_alias_mappings: list[dict[str, Any]] | None = None,
    requested_target_fields: list[str] | None = None,
    protected_column_policies: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    target_table = rule["target_table"]
    actions = iter_actions({"condition_groups": rule.get("condition_groups", [])})
    set_sql, set_params, target_fields, normalized_actions = actions_to_set_clause(
        actions,
        target_table,
        table_columns,
        column_alias_mappings,
        requested_target_fields,
        protected_column_policies,
    )
    if precompiled_where["sql"] == "1 = 1":
        raise ValueError("write_sql_requires_compiled_selection_or_modification_predicate")
    where_sql = precompiled_where["sql"]
    sql = f"UPDATE {quote_identifier(target_table)} SET {set_sql} WHERE {where_sql}"
    params = set_params + list(precompiled_where.get("params", []))
    return {
        "source": "mongodb_rule",
        "sql_type": "UPDATE",
        "sql": sql,
        "params": params,
        "referenced_columns": [*target_fields, *precompiled_where.get("columns", [])],
        "target_table": target_table,
        "reason": f"MongoDB rule 재사용: {rule.get('rule_id', 'unknown_rule')}",
        "actions": normalized_actions,
        "predicate": precompiled_where.get("predicate", []),
        "sql_fingerprint": fingerprint_sql(sql, params),
    }


def build_crud_sql_prompt(
    modification_logic: dict[str, Any],
    schema_summary: str,
    sanitized_rows_sample: list[dict[str, Any]],
) -> str:
    return f"""
/no_think
너는 MariaDB DA/SA raw 데이터 수정 action 후보를 작성한다.
추론 과정, 설명, markdown 없이 JSON 객체만 출력한다.
SQL 문자열은 만들지 않는다. Python allowlisted compiler가 action을 SQL로 변환한다.
아래 실시간 DB schema에 존재하는 table/column만 사용한다.
import 스크립트의 canonicalization 규칙 외 alias를 임의로 만들지 않는다.
source_channel, 날짜, 원본 식별 컬럼은 target_field로 선택하지 않는다.

실시간 DB schema 요약:
{schema_summary}

수정 논리 구조:
{json.dumps(modification_logic, ensure_ascii=False)}

민감/불필요 필드를 제거한 대상 row 샘플:
{json.dumps(sanitized_rows_sample, ensure_ascii=False)}

    반환 필드: action[target_field, operation, value], reason
""".strip()


def minimize_rows_for_llm(rows: list[dict[str, Any]], allowed_fields: list[str]) -> list[dict[str, Any]]:
    return [{field: row.get(field) for field in allowed_fields if field in row} for row in rows]


def unique_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def group_by_columns_from_dictionary(
    text: str,
    target_table: str,
    columns: list[str],
    column_alias_mappings: list[dict[str, Any]],
) -> list[str]:
    lowered = text.lower()
    group_by: list[str] = []
    for item in column_alias_mappings:
        if str(item.get("target_table")) != target_table:
            continue
        if str(item.get("semantic_role") or "") != "dimension":
            continue
        user_term = str(item.get("user_term") or "")
        target_column = str(item.get("target_column") or "")
        if user_term and user_term.lower() in lowered and target_column in columns:
            group_by.append(target_column)
    compact = compact_text(text)
    if ("날짜별" in compact or "날짜기준" in compact) and "날짜" in columns:
        group_by.append("날짜")
    if ("캠페인별" in compact or "캠페인기준" in compact) and "캠페인" in columns:
        group_by.append("캠페인")
    if ("기기별" in compact or "기기기준" in compact or "디바이스별" in compact or "디바이스기준" in compact) and "디바이스" in columns:
        group_by.append("디바이스")
    return unique_preserve_order(group_by)


def metric_spec_from_definition(item: dict[str, Any], columns: list[str]) -> dict[str, Any] | None:
    source_column = str(item.get("source_column") or "")
    denominator_column = str(item.get("denominator_column") or "")
    if source_column not in columns:
        return None
    if denominator_column and denominator_column not in columns:
        return None
    user_term = str(item.get("user_term", ""))
    return {
        "metric_code": item.get("metric_code"),
        "alias": user_term.replace(" ", "_") if user_term else str(item.get("metric_code") or item.get("expression_type") or "metric"),
        "expression_type": item.get("expression_type"),
        "source_column": source_column,
        "denominator_column": denominator_column,
        "zero_fallback": None if item.get("zero_fallback") in {None, ""} else item.get("zero_fallback"),
        "source_channel_scope": item.get("source_channel_scope"),
        "event_filter": item.get("event_filter"),
        "business_definition": item.get("business_definition"),
    }


def metric_specs_from_dictionary(text: str, target_table: str, columns: list[str], metric_definitions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = []
    lowered = text.lower()
    for item in metric_definitions:
        user_term = str(item.get("user_term", ""))
        metric_table = str(item.get("target_table") or "")
        if not user_term or user_term.lower() not in lowered:
            continue
        if metric_table and metric_table != target_table:
            continue
        spec = metric_spec_from_definition(item, columns)
        if spec:
            specs.append(spec)
    return specs


def metric_specs_from_obvious_words(text: str, columns: list[str]) -> list[dict[str, Any]]:
    compact = compact_text(text)
    candidates = [
        ("비용", "sum", ["비용합계", "광고비합계", "costsum", "spendsum"]),
        ("비용", "avg", ["비용평균", "광고비평균", "costavg", "spendavg"]),
        ("클릭수", "sum", ["클릭합계", "클릭수합계", "clicksum"]),
        ("클릭수", "avg", ["클릭평균", "클릭수평균", "clickavg"]),
        ("노출수", "sum", ["노출합계", "노출수합계", "impressionsum"]),
        ("노출수", "avg", ["노출평균", "노출수평균", "impressionavg"]),
        ("세션수", "sum", ["세션합계", "세션수합계", "sessionsum"]),
        ("세션수", "avg", ["세션평균", "세션수평균", "sessionavg"]),
    ]
    specs: list[dict[str, Any]] = []
    for column, expression_type, tokens in candidates:
        if column not in columns:
            continue
        if any(compact_text(token) in compact for token in tokens):
            suffix = "평균" if expression_type == "avg" else "합계"
            specs.append({"alias": f"{column} {suffix}", "expression_type": expression_type, "source_column": column})
    return specs


def merge_metric_specs(primary: list[dict[str, Any]], fallback: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for metric in [*primary, *fallback]:
        source_column = str(metric.get("source_column") or metric.get("column") or "")
        if "." in source_column:
            source_column = source_column.split(".", 1)[1].strip("`")
        expression_type = str(metric.get("expression_type") or "sum").lower()
        key = (source_column, expression_type)
        if key in seen:
            continue
        seen.add(key)
        merged.append(metric)
    return merged


def metric_matches_definition(metric: dict[str, Any], item: dict[str, Any], text: str) -> bool:
    metric_code = str(metric.get("metric_code") or "")
    user_term = str(metric.get("user_term") or "")
    alias = str(metric.get("alias") or "")
    expression_type = str(metric.get("expression_type") or "").lower()
    item_code = str(item.get("metric_code") or "")
    item_user_term = str(item.get("user_term") or "")
    item_expression_type = str(item.get("expression_type") or "").lower()
    if metric_code and metric_code == item_code:
        return True
    if user_term and user_term == item_user_term:
        return True
    if alias and compact_text(alias) == compact_text(item_user_term):
        return True
    return bool(expression_type and expression_type == item_expression_type and item_user_term and item_user_term.lower() in text.lower())


def hydrate_metric_from_dictionary(
    metric: dict[str, Any],
    text: str,
    target_table: str,
    columns: list[str],
    metric_definitions: list[dict[str, Any]],
) -> dict[str, Any]:
    for item in metric_definitions:
        metric_table = str(item.get("target_table") or "")
        if metric_table and metric_table != target_table:
            continue
        item_user_term = str(item.get("user_term") or "")
        direct_text_match = bool(item_user_term and item_user_term.lower() in text.lower())
        if not direct_text_match and not metric_matches_definition(metric, item, text):
            continue
        spec = metric_spec_from_definition(item, columns)
        if not spec:
            continue
        if direct_text_match:
            return {**metric, **spec}
        hydrated = {**spec, **metric}
        for key in ("source_column", "denominator_column", "expression_type"):
            if hydrated.get(key) is None or hydrated.get(key) == "":
                hydrated[key] = spec.get(key)
        if "zero_fallback" not in metric or metric.get("zero_fallback") == "":
            hydrated["zero_fallback"] = spec.get("zero_fallback")
        return hydrated
    return metric


def metric_filter_sql(metric_filter: Any, table_columns: list[str]) -> tuple[str, list[Any], list[str]]:
    if not isinstance(metric_filter, dict):
        return "", [], []
    column = str(metric_filter.get("column") or metric_filter.get("field") or "")
    operator = str(metric_filter.get("operator") or "eq").lower()
    values = metric_filter.get("values")
    if values is None and "value" in metric_filter:
        values = [metric_filter.get("value")]
    if not isinstance(values, list):
        values = []
    if column not in table_columns:
        raise ValueError(f"Unknown metric filter column in live schema: {column}")
    if operator == "eq" and len(values) == 1:
        return f"{quote_identifier(column)} = %s", values, [column]
    if operator in {"eq", "in"} and values:
        placeholders = ", ".join(["%s"] * len(values))
        return f"{quote_identifier(column)} IN ({placeholders})", values, [column]
    if operator in {"contains", "like"} and len(values) == 1:
        return f"{quote_identifier(column)} LIKE %s", [f"%{values[0]}%"], [column]
    raise ValueError(f"Unsupported metric filter operator: {operator}")


def metric_expression(metric: dict[str, Any], table_columns: list[str]) -> tuple[str, list[str], list[Any]]:
    expression_type = str(metric.get("expression_type", "sum")).lower()
    source_column = str(metric.get("source_column") or metric.get("column") or "")
    denominator_column = str(metric.get("denominator_column") or "")
    if "." in source_column:
        source_column = source_column.split(".", 1)[1].strip("`")
    if "." in denominator_column:
        denominator_column = denominator_column.split(".", 1)[1].strip("`")
    if " OR " in source_column:
        source_column = source_column.split(" OR ", 1)[0].strip().split(".")[-1].strip("`")
    if " OR " in denominator_column:
        denominator_column = denominator_column.split(" OR ", 1)[0].strip().split(".")[-1].strip("`")
    alias = str(metric.get("alias") or source_column or expression_type)
    if not source_column:
        raise ValueError(f"metric_source_column_required_for_expression_type: {expression_type}")
    if source_column not in table_columns:
        raise ValueError(f"Unknown metric source column in live schema: {source_column}")
    alias_sql = quote_identifier(alias)
    source_sum = f"SUM({numeric_sql(source_column)})"
    if expression_type == "sum":
        return f"{source_sum} AS {alias_sql}", [source_column], []
    if expression_type == "avg":
        return f"AVG({numeric_sql(source_column)}) AS {alias_sql}", [source_column], []
    if expression_type == "event_count":
        filter_sql, filter_params, filter_columns = metric_filter_sql(metric.get("event_filter"), table_columns)
        if not filter_sql:
            raise ValueError("event_count_metric_requires_event_filter")
        return f"SUM(CASE WHEN {filter_sql} THEN {numeric_sql(source_column)} ELSE 0 END) AS {alias_sql}", [source_column, *filter_columns], filter_params
    if expression_type in {"conversion_rate", "ctr", "cost_per_conversion"}:
        if not denominator_column:
            raise ValueError(f"metric_denominator_column_required_for_expression_type: {expression_type}")
        if denominator_column not in table_columns:
            raise ValueError(f"Unknown metric denominator column in live schema: {denominator_column}")
        denominator_sum = f"SUM({numeric_sql(denominator_column)})"
        zero_fallback = metric.get("zero_fallback", 0)
        if zero_fallback is None:
            fallback_sql = "NULL"
            fallback_params: list[Any] = []
        elif is_numeric_literal(zero_fallback):
            fallback_sql = "%s"
            fallback_params = [zero_fallback]
        else:
            raise ValueError("metric_zero_fallback_must_be_null_or_numeric")
        multiplier = " * 100" if expression_type in {"conversion_rate", "ctr"} else ""
        return (
            f"CASE WHEN {denominator_sum} = 0 THEN {fallback_sql} ELSE {source_sum} / {denominator_sum}{multiplier} END AS {alias_sql}",
            [source_column, denominator_column],
            fallback_params,
        )
    raise ValueError(f"Unsupported metric expression_type: {expression_type}")


def build_select_aggregate_sql_candidate(state: ModificationWorkflowState, table_columns: dict[str, list[str]]) -> dict[str, Any]:
    selection_request = state["selection_request"]
    target_table = selection_request.get("tables", ["SA"])[0]
    columns = table_columns.get(target_table, [])
    if target_table not in ALLOWED_TABLES or not columns:
        raise ValueError("aggregate_sql_requires_live_target_table")

    selection_where = compile_selection_scope_to_where(selection_request, table_columns, state.get("source_channel_values", {}))
    modification_where = read_only_conditions_to_where(state, table_columns)
    precompiled_where = combine_where_predicates(selection_where, modification_where)
    modification_logic = state.get("modification_logic", {})
    text = " ".join(
        [
            str(state.get("selection_text", "")),
            str(state.get("modification_text", "")),
            json.dumps(modification_logic, ensure_ascii=False),
        ]
    )
    column_alias_mappings = state.get("column_alias_mappings", [])
    group_by_columns = [
        resolve_workflow_column(str(item.get("resolved_column") or item.get("field") or item.get("field_alias") or ""), target_table, table_columns, column_alias_mappings)
        for item in modification_logic.get("group_by", [])
        if isinstance(item, dict)
    ] or group_by_columns_from_dictionary(text, target_table, columns, column_alias_mappings)
    group_by_columns = [column for column in unique_preserve_order(group_by_columns) if column in columns]

    metric_definitions = state.get("metric_definitions", [])
    dictionary_metrics = metric_specs_from_dictionary(text, target_table, columns, metric_definitions)
    parsed_metrics = [item for item in modification_logic.get("metrics", []) if isinstance(item, dict)]
    metrics = dictionary_metrics or parsed_metrics
    if not metrics:
        metrics = metric_specs_from_obvious_words(text, columns)
    if metrics and not dictionary_metrics:
        metrics = [hydrate_metric_from_dictionary(metric, text, target_table, columns, metric_definitions) for metric in metrics]
    metrics = merge_metric_specs(metrics, metric_specs_from_obvious_words(text, columns))
    if not metrics:
        raise ValueError("select_aggregate_requires_metrics")

    select_parts: list[str] = [f"{quote_identifier(column)} AS {quote_identifier(column)}" for column in group_by_columns]
    referenced_columns: list[str] = [*group_by_columns, *precompiled_where.get("columns", [])]
    metric_params: list[Any] = []
    for metric in metrics:
        if str(metric.get("expression_type", "")).lower() == "cost_per_conversion" and any(token in text for token in ["빈값", "null", "NULL"]):
            metric = {**metric, "zero_fallback": None}
        expression_sql, metric_columns, expression_params = metric_expression(metric, columns)
        select_parts.append(expression_sql)
        referenced_columns.extend(metric_columns)
        metric_params.extend(expression_params)

    where_sql = precompiled_where.get("sql", "1 = 1")
    group_sql = ""
    if group_by_columns:
        group_sql = " GROUP BY " + ", ".join(quote_identifier(column) for column in group_by_columns)
    sql = f"SELECT {', '.join(select_parts)} FROM {quote_identifier(target_table)} WHERE {where_sql}{group_sql}"
    params = [*metric_params, *precompiled_where.get("params", [])]
    return {
        "source": "deterministic_select_aggregate_renderer",
        "sql_type": "SELECT",
        "sql": sql,
        "params": params,
        "referenced_columns": unique_preserve_order(referenced_columns),
        "target_table": target_table,
        "reason": "intent_type=SELECT_AGGREGATE; deterministic code rendered aggregate SELECT from IR and live schema.",
        "actions": [],
        "predicate": precompiled_where.get("predicate", []),
        "sql_fingerprint": fingerprint_sql(sql, params),
    }


def build_select_detail_sql_candidate(state: ModificationWorkflowState, table_columns: dict[str, list[str]]) -> dict[str, Any]:
    selection_request = state["selection_request"]
    target_table = selection_request.get("tables", ["SA"])[0]
    columns = table_columns.get(target_table, [])
    if target_table not in ALLOWED_TABLES or not columns:
        raise ValueError("select_detail_requires_live_target_table")
    selection_where = compile_selection_scope_to_where(selection_request, table_columns, state.get("source_channel_values", {}))
    modification_where = read_only_conditions_to_where(state, table_columns)
    precompiled_where = combine_where_predicates(selection_where, modification_where)
    where_sql = precompiled_where.get("sql", "1 = 1")
    params = list(precompiled_where.get("params", []))
    sql = f"SELECT * FROM {quote_identifier(target_table)} WHERE {where_sql}"
    return {
        "source": "deterministic_select_detail_renderer",
        "sql_type": "SELECT",
        "sql": sql,
        "params": params,
        "referenced_columns": unique_preserve_order(precompiled_where.get("columns", [])),
        "target_table": target_table,
        "reason": "intent_type=SELECT_DETAIL; rendered row-level SELECT inside the first requested scope.",
        "actions": [],
        "predicate": precompiled_where.get("predicate", []),
        "sql_fingerprint": fingerprint_sql(sql, params),
    }


def infer_derived_column_spec(modification_logic: dict[str, Any], modification_text: str) -> dict[str, str]:
    raw_derived = modification_logic.get("derived_column")
    derived = raw_derived if isinstance(raw_derived, dict) else {}
    derived_key = str(derived.get("derived_key") or derived.get("column_name") or derived.get("name") or "").strip()
    derived_value = str(derived.get("derived_value") or derived.get("value") or "").strip()

    actions = [item for group in modification_logic.get("condition_groups", []) for item in group.get("actions", []) if isinstance(item, dict)]
    for action in actions:
        if not derived_key:
            derived_key = str(action.get("target_field") or action.get("target_column") or "").strip()
        if not derived_value and "value" in action:
            derived_value = str(action.get("value") or "").strip()

    quoted_tokens = re.findall(r"`([^`]+)`", modification_text)
    if not derived_value and quoted_tokens:
        derived_value = quoted_tokens[-1].strip()
    assignment_match = re.search(
        r"(?:^|[\s에])(?P<key>[가-힣A-Za-z0-9 _-]{2,40}?(?:분류값|구분값|상태값|상태 값|값))\s*(?:을|를)\s*"
        r"(?P<value>[가-힣A-Za-z0-9 _-]{2,40}?)\s*(?:으로|로)\s*(?:붙|분류|기입|만들)",
        modification_text,
    )
    if assignment_match:
        if not derived_key:
            derived_key = re.sub(r"\s+", " ", assignment_match.group("key")).strip()
        if not derived_value:
            derived_value = re.sub(r"\s+", " ", assignment_match.group("value")).strip()
    label_match = re.search(
        r"(?P<value>[가-힣A-Za-z0-9 _-]{2,40}?)\s*(?P<key>분류값|구분값|상태값|상태 값|확인 값|값)\s*(?:을|를)\s*(?:붙|기입|만들)",
        modification_text,
    )
    if label_match:
        key = re.sub(r"\s+", " ", label_match.group("key")).strip()
        value = re.sub(r"\s+", " ", label_match.group("value")).strip()
        if not derived_key:
            derived_key = key if key != "값" else "derived_label"
        if not derived_value:
            derived_value = value
    if not derived_key:
        if len(quoted_tokens) >= 2:
            derived_key = quoted_tokens[0].strip()
        elif "광고상품" in modification_text:
            derived_key = "광고상품명"
        elif "구분 항목" in modification_text:
            derived_key = "구분 항목"
        else:
            derived_key = "derived_label"

    if not derived_key:
        raise ValueError("derived_column_key_required")
    if not derived_value:
        raise ValueError("derived_column_value_required")
    return {"derived_key": derived_key, "derived_value": derived_value}


def build_derived_value_insert_sql_candidate(state: ModificationWorkflowState, table_columns: dict[str, list[str]]) -> dict[str, Any]:
    selection_request = state["selection_request"]
    target_table = selection_request.get("tables", ["SA"])[0]
    columns = table_columns.get(target_table, [])
    if target_table not in ALLOWED_TABLES or not columns:
        raise ValueError("derived_sql_requires_live_target_table")
    for required_column in ["row_id", "source_row_hash"]:
        if required_column not in columns:
            raise ValueError(f"derived_sql_requires_source_identity_column: {required_column}")

    selection_where = compile_selection_scope_to_where(selection_request, table_columns, state.get("source_channel_values", {}))
    modification_logic = normalize_condition_values_from_rows(state["modification_logic"], state.get("target_rows", []))
    modification_logic = recover_condition_values_from_text(modification_logic, state.get("modification_text", ""))
    modification_logic = recover_or_conditions_from_text(modification_logic, state.get("modification_text", ""))
    try:
        modification_where = compile_conditions_to_where(
            modification_logic,
            selection_request,
            table_columns,
            state.get("column_alias_mappings", []),
        )
    except ValueError:
        modification_where = {"sql": "1 = 1", "params": [], "columns": [], "predicate": []}
    precompiled_where = combine_where_predicates(selection_where, modification_where)
    if precompiled_where["sql"] == "1 = 1":
        raise ValueError("derived_sql_requires_selection_or_modification_predicate")

    spec = infer_derived_column_spec(modification_logic, state.get("modification_text", ""))
    rule_id = "generated_add_derived_column"
    sql = (
        f"INSERT INTO {quote_identifier(DERIVED_VALUE_TABLE)} "
        "(`target_table`, `source_row_id`, `source_row_hash`, `derived_key`, `derived_value`, `rule_id`) "
        f"SELECT %s, `row_id`, `source_row_hash`, %s, %s, %s FROM {quote_identifier(target_table)} "
        f"WHERE {precompiled_where['sql']} "
        "ON DUPLICATE KEY UPDATE `derived_value` = VALUES(`derived_value`), `rule_id` = VALUES(`rule_id`)"
    )
    params = [target_table, spec["derived_key"], spec["derived_value"], rule_id, *precompiled_where.get("params", [])]
    return {
        "source": "derived_value_insert_renderer",
        "sql_type": "INSERT",
        "sql": sql,
        "params": params,
        "referenced_columns": unique_preserve_order(["row_id", "source_row_hash", *precompiled_where.get("columns", [])]),
        "target_table": target_table,
        "insert_table": DERIVED_VALUE_TABLE,
        "reason": "intent_type=ADD_DERIVED_COLUMN; generated preview-only derived-value storage SQL without mutating raw tables.",
        "actions": [{"target_field": spec["derived_key"], "operation": "set_literal", "value": spec["derived_value"], "storage": DERIVED_VALUE_TABLE}],
        "predicate": precompiled_where.get("predicate", []),
        "where_sql": precompiled_where["sql"],
        "where_params": list(precompiled_where.get("params", [])),
        "derived_key": spec["derived_key"],
        "derived_value": spec["derived_value"],
        "execution_allowed": False,
        "sql_fingerprint": fingerprint_sql(sql, params),
    }


def generate_crud_sql_candidate(
    llm: Any,
    modification_logic: dict[str, Any],
    schema_summary: str,
    sanitized_rows_sample: list[dict[str, Any]],
    target_table: str,
    precompiled_where: dict[str, Any],
    table_columns: dict[str, list[str]],
    column_alias_mappings: list[dict[str, Any]] | None = None,
    requested_target_fields: list[str] | None = None,
    protected_column_policies: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if llm is None:
        raise RuntimeError("SQL_WORKFLOW_LLM_SQL_GENERATION_REQUIRED")

    parsed = invoke_llm_json(llm, build_crud_sql_prompt(modification_logic, schema_summary, sanitized_rows_sample), "query_generation")
    if "action" in parsed:
        modification_logic = {"condition_groups": [{"actions": [parsed["action"]], "conditions": modification_logic.get("condition_groups", [{}])[0].get("conditions", [])}]}

    actions = iter_actions(modification_logic)
    set_sql, set_params, target_fields, normalized_actions = actions_to_set_clause(
        actions,
        target_table,
        table_columns,
        column_alias_mappings,
        requested_target_fields,
        protected_column_policies,
    )
    if precompiled_where["sql"] == "1 = 1":
        raise ValueError("write_sql_requires_compiled_selection_or_modification_predicate")
    sql = f"UPDATE {quote_identifier(target_table)} SET {set_sql} WHERE {precompiled_where['sql']}"
    params = set_params + list(precompiled_where.get("params", []))
    return {
        "source": "structured_llm_plan_sql_builder",
        "sql_type": "UPDATE",
        "sql": sql,
        "params": params,
        "referenced_columns": [*target_fields, *precompiled_where.get("columns", [])],
        "target_table": target_table,
        "reason": "LLM generated a structured action plan; deterministic code compiled the allowlisted SQL shape.",
        "actions": normalized_actions,
        "predicate": precompiled_where.get("predicate", []),
        "sql_fingerprint": fingerprint_sql(sql, params),
    }


def compile_structured_actions_sql_candidate(
    modification_logic: dict[str, Any],
    target_table: str,
    precompiled_where: dict[str, Any],
    table_columns: dict[str, list[str]],
    column_alias_mappings: list[dict[str, Any]] | None = None,
    requested_target_fields: list[str] | None = None,
    protected_column_policies: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    actions = iter_actions(modification_logic)
    set_sql, set_params, target_fields, normalized_actions = actions_to_set_clause(
        actions,
        target_table,
        table_columns,
        column_alias_mappings,
        requested_target_fields,
        protected_column_policies,
    )
    if precompiled_where["sql"] == "1 = 1":
        raise ValueError("write_sql_requires_compiled_selection_or_modification_predicate")
    sql = f"UPDATE {quote_identifier(target_table)} SET {set_sql} WHERE {precompiled_where['sql']}"
    params = set_params + list(precompiled_where.get("params", []))
    return {
        "source": "structured_ir_action_sql_builder",
        "sql_type": "UPDATE",
        "sql": sql,
        "params": params,
        "referenced_columns": [*target_fields, *precompiled_where.get("columns", [])],
        "target_table": target_table,
        "reason": "IR already contained structured actions; deterministic code compiled the allowlisted SQL shape.",
        "actions": normalized_actions,
        "predicate": precompiled_where.get("predicate", []),
        "sql_fingerprint": fingerprint_sql(sql, params),
    }


def generate_or_reuse_sql_node(state: ModificationWorkflowState, llm: Any = None) -> dict[str, Any]:
    table_columns = state.get("table_columns", {})
    target_table = state.get("selection_request", {}).get("tables", ["SA"])[0]
    intent_type = str(state.get("ir_structured_json", {}).get("intent_type") or state.get("modification_logic", {}).get("intent_type") or "").upper()
    try:
        if intent_type == ASK_CLARIFICATION_INTENT:
            raise ValueError("intent_requires_clarification_before_sql_generation")
        if intent_type == SELECT_DETAIL_INTENT:
            sql_candidate = build_select_detail_sql_candidate(state, table_columns)
            return {"precompiled_where": {"sql": "1 = 1", "params": [], "columns": [], "predicate": []}, "sql_candidate": sql_candidate}
        if intent_type == ADD_DERIVED_COLUMN_INTENT:
            sql_candidate = build_derived_value_insert_sql_candidate(state, table_columns)
            return {
                "precompiled_where": {
                    "sql": sql_candidate.get("where_sql", "1 = 1"),
                    "params": sql_candidate.get("where_params", []),
                    "columns": sql_candidate.get("referenced_columns", []),
                    "predicate": sql_candidate.get("predicate", []),
                },
                "sql_candidate": sql_candidate,
            }
        if intent_type == AGGREGATE_INTENT:
            sql_candidate = build_select_aggregate_sql_candidate(state, table_columns)
            return {"precompiled_where": {"sql": "1 = 1", "params": [], "columns": [], "predicate": []}, "sql_candidate": sql_candidate}
        selection_where = compile_selection_scope_to_where(
            state["selection_request"],
            table_columns,
            state.get("source_channel_values", {}),
        )
        modification_logic = normalize_condition_values_from_rows(state["modification_logic"], state.get("target_rows", []))
        modification_logic = recover_condition_values_from_text(modification_logic, state.get("modification_text", ""))
        modification_logic = recover_or_conditions_from_text(modification_logic, state.get("modification_text", ""))
        column_alias_mappings = state.get("column_alias_mappings", [])
        protected_column_policies = state.get("protected_column_policies", [])
        requested_target_fields = requested_action_columns_from_text(
            state.get("modification_text", ""),
            target_table,
            table_columns,
            column_alias_mappings,
            protected_column_policies,
        )
        modification_where = compile_conditions_to_where(modification_logic, state["selection_request"], table_columns, column_alias_mappings)
        if not modification_where.get("predicate"):
            raise ValueError("write_sql_requires_resolved_modification_predicate")
        precompiled_where = combine_where_predicates(selection_where, modification_where)
        plan = state["effective_modification_plan"]
        if not plan.get("requires_sql_generation"):
            sql_candidate = compile_mongodb_rule_to_sql_candidate(
                plan["rule"],
                precompiled_where,
                table_columns,
                column_alias_mappings,
                None,
                protected_column_policies,
            )
        elif any(group.get("actions") for group in modification_logic.get("condition_groups", []) if isinstance(group, dict)):
            sql_candidate = compile_structured_actions_sql_candidate(
                modification_logic,
                target_table,
                precompiled_where,
                table_columns,
                column_alias_mappings,
                None,
                protected_column_policies,
            )
        else:
            allowed_fields = table_columns.get(target_table, [])[:5]
            sql_candidate = generate_crud_sql_candidate(
                llm=llm,
                modification_logic=modification_logic,
                schema_summary=state.get("schema_summary", build_schema_summary(table_columns, state.get("source_channel_values", {}))),
                sanitized_rows_sample=minimize_rows_for_llm(rows=state.get("target_rows", [])[:3], allowed_fields=allowed_fields),
                target_table=target_table,
                precompiled_where=precompiled_where,
                table_columns=table_columns,
                column_alias_mappings=column_alias_mappings,
                requested_target_fields=requested_target_fields,
                protected_column_policies=protected_column_policies,
            )
        return {"modification_logic": modification_logic, "precompiled_where": precompiled_where, "sql_candidate": sql_candidate}
    except Exception as exc:
        try:
            sql_candidate = build_review_only_sql_candidate_after_error(state, table_columns, target_table, str(exc))
        except Exception:
            sql_candidate = build_blocked_sql_candidate(str(exc), target_table)
        return {
            "precompiled_where": {"sql": "1 = 1", "params": [], "columns": [], "predicate": []},
            "sql_candidate": sql_candidate,
            "errors": append_error(state, f"sql_candidate_generation_blocked: {exc}"),
        }


def split_top_level(sql: str, separator: str) -> list[str]:
    parts: list[str] = []
    current: list[str] = []
    depth = 0
    index = 0
    pattern = f" {separator} "
    upper_sql = sql.upper()
    while index < len(sql):
        char = sql[index]
        if char == "(":
            depth += 1
        elif char == ")" and depth > 0:
            depth -= 1
        if depth == 0 and upper_sql[index : index + len(pattern)] == pattern:
            parts.append("".join(current).strip())
            current = []
            index += len(pattern)
            continue
        current.append(char)
        index += 1
    trailing = "".join(current).strip()
    if trailing:
        parts.append(trailing)
    return parts


def parse_where_clause(where_sql: str) -> list[dict[str, Any]]:
    conditions: list[dict[str, Any]] = []
    for raw_clause in split_top_level(where_sql.strip(), "AND"):
        clause = raw_clause.strip()
        null_or_empty_match = re.fullmatch(r"\(`([^`]+)`\s+IS\s+NULL\s+OR\s+TRIM\(`([^`]+)`\)\s*=\s*''\)", clause, flags=re.IGNORECASE)
        if null_or_empty_match and null_or_empty_match.group(1) == null_or_empty_match.group(2):
            conditions.append({"column": null_or_empty_match.group(1), "operator": "is_null_or_empty", "placeholder_count": 0})
            continue
        not_null_or_empty_match = re.fullmatch(r"\(`([^`]+)`\s+IS\s+NOT\s+NULL\s+AND\s+TRIM\(`([^`]+)`\)\s*<>\s*''\)", clause, flags=re.IGNORECASE)
        if not_null_or_empty_match and not_null_or_empty_match.group(1) == not_null_or_empty_match.group(2):
            conditions.append({"column": not_null_or_empty_match.group(1), "operator": "is_not_null_or_empty", "placeholder_count": 0})
            continue
        if clause.startswith("(") and clause.endswith(")") and " OR " in clause.upper():
            nested_conditions: list[dict[str, Any]] = []
            for nested_clause in split_top_level(clause[1:-1].strip(), "OR"):
                nested_conditions.extend(parse_where_clause(nested_clause))
            conditions.extend(nested_conditions)
            continue
        equals_match = re.fullmatch(r"(?:`([^`]+)`|CAST\(NULLIF\(`([^`]+)`,\s*''\)\s+AS\s+DECIMAL\(18,2\)\))\s*=\s*%s", clause, flags=re.IGNORECASE)
        if equals_match:
            conditions.append({"column": equals_match.group(1) or equals_match.group(2), "operator": "eq", "placeholder_count": 1})
            continue
        comparison_match = re.fullmatch(r"(?:`([^`]+)`|CAST\(NULLIF\(`([^`]+)`,\s*''\)\s+AS\s+DECIMAL\(18,2\)\))\s*(>=|<=|>|<)\s*%s", clause, flags=re.IGNORECASE)
        if comparison_match:
            column = comparison_match.group(1) or comparison_match.group(2)
            symbol = comparison_match.group(3)
            operator = {">=": "gte", "<=": "lte", ">": "gt", "<": "lt"}[symbol]
            conditions.append(
                {
                    "column": column,
                    "operator": operator,
                    "placeholder_count": 1,
                }
            )
            continue
        in_match = re.fullmatch(r"`([^`]+)`\s+IN\s*\((%s(?:\s*,\s*%s)*)\)", clause, flags=re.IGNORECASE)
        if in_match:
            conditions.append({"column": in_match.group(1), "operator": "in", "placeholder_count": in_match.group(2).count("%s")})
            continue
        like_match = re.fullmatch(r"`([^`]+)`\s+LIKE\s*%s", clause, flags=re.IGNORECASE)
        if like_match:
            conditions.append({"column": like_match.group(1), "operator": "contains", "placeholder_count": 1})
            continue
        not_like_match = re.fullmatch(r"`([^`]+)`\s+NOT\s+LIKE\s*%s", clause, flags=re.IGNORECASE)
        if not_like_match:
            conditions.append({"column": not_like_match.group(1), "operator": "not_contains", "placeholder_count": 1})
            continue
        between_match = re.fullmatch(r"(?:`([^`]+)`|CAST\(NULLIF\(`([^`]+)`,\s*''\)\s+AS\s+DECIMAL\(18,2\)\))\s+BETWEEN\s+%s\s+AND\s+%s", clause, flags=re.IGNORECASE)
        if between_match:
            conditions.append({"column": between_match.group(1) or between_match.group(2), "operator": "between", "placeholder_count": 2})
            continue
        null_match = re.fullmatch(r"`([^`]+)`\s+IS\s+NULL", clause, flags=re.IGNORECASE)
        if null_match:
            conditions.append({"column": null_match.group(1), "operator": "is_null", "placeholder_count": 0})
            continue
        empty_match = re.fullmatch(r"TRIM\(COALESCE\(`([^`]+)`,\s*''\)\)\s*=\s*''", clause, flags=re.IGNORECASE)
        if empty_match:
            conditions.append({"column": empty_match.group(1), "operator": "is_empty", "placeholder_count": 0})
            continue
        trim_empty_match = re.fullmatch(r"TRIM\(`([^`]+)`\)\s*=\s*''", clause, flags=re.IGNORECASE)
        if trim_empty_match:
            conditions.append({"column": trim_empty_match.group(1), "operator": "is_empty", "placeholder_count": 0})
            continue
        raise ValueError(f"Unsupported WHERE clause: {clause}")
    return conditions


def parse_sql_with_script(sql: str) -> dict[str, Any]:
    stripped = sql.strip()
    has_dangerous_tokens = any(token in stripped for token in DANGEROUS_SQL_TOKENS)
    update_match = re.fullmatch(r"UPDATE\s+`([^`]+)`\s+SET\s+(.+)\s+WHERE\s+(.+)", stripped, flags=re.IGNORECASE)
    derived_insert_match = re.fullmatch(
        r"INSERT\s+INTO\s+`rule_engine_derived_value`\s+\(`target_table`,\s*`source_row_id`,\s*`source_row_hash`,\s*`derived_key`,\s*`derived_value`,\s*`rule_id`\)\s+"
        r"SELECT\s+%s,\s*`row_id`,\s*`source_row_hash`,\s*%s,\s*%s,\s*%s\s+FROM\s+`([^`]+)`\s+WHERE\s+(.+)\s+"
        r"ON\s+DUPLICATE\s+KEY\s+UPDATE\s+`derived_value`\s*=\s*VALUES\(`derived_value`\),\s*`rule_id`\s*=\s*VALUES\(`rule_id`\)",
        stripped,
        flags=re.IGNORECASE,
    )
    select_match = re.fullmatch(r"SELECT\s+.+\s+FROM\s+`([^`]+)`\s+WHERE\s+(.+?)(?:\s+GROUP\s+BY\s+.+)?", stripped, flags=re.IGNORECASE)

    if update_match:
        table_name = update_match.group(1)
        set_sql = update_match.group(2)
        set_columns = re.findall(r"`([^`]+)`\s*=\s*%s", set_sql)
        if not set_columns:
            raise ValueError("Unsupported UPDATE SET clause")
        where_sql = update_match.group(3)
        where_conditions = parse_where_clause(where_sql)
        identifiers = re.findall(r"`([^`]+)`", stripped)
        return {
            "sql_type": "UPDATE",
            "target_table": table_name,
            "set_column": set_columns[0],
            "set_columns": set_columns,
            "where_conditions": where_conditions,
            "referenced_columns": [identifier for identifier in identifiers[1:] if identifier != table_name],
            "has_where": bool(where_sql),
            "broad_predicate": where_sql.strip() == "1 = 1",
            "has_dangerous_tokens": has_dangerous_tokens,
        }
    if derived_insert_match:
        table_name = derived_insert_match.group(1)
        where_sql = derived_insert_match.group(2)
        where_conditions = parse_where_clause(where_sql)
        return {
            "sql_type": "INSERT",
            "target_table": table_name,
            "insert_table": DERIVED_VALUE_TABLE,
            "referenced_columns": ["row_id", "source_row_hash", *[item["column"] for item in where_conditions]],
            "where_conditions": where_conditions,
            "has_where": bool(where_sql),
            "broad_predicate": where_sql.strip() == "1 = 1",
            "has_dangerous_tokens": has_dangerous_tokens,
        }
    if select_match:
        table_name = select_match.group(1)
        where_sql = select_match.group(2)
        if where_sql.strip() != "1 = 1":
            parse_where_clause(where_sql)
        identifiers = re.findall(r"`([^`]+)`", stripped)
        return {
            "sql_type": "SELECT",
            "target_table": table_name,
            "referenced_columns": [identifier for identifier in identifiers[1:] if identifier != table_name],
            "has_where": bool(where_sql),
            "broad_predicate": False,
            "has_dangerous_tokens": has_dangerous_tokens,
        }
    raise ValueError("Unsupported SQL shape for deterministic tutorial parser.")


def validate_sql_with_script(
    sql_candidate: dict[str, Any],
    table_columns: dict[str, list[str]],
    protected_column_policies: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    sql = sql_candidate.get("sql", "")
    checks: list[str] = []
    errors: list[str] = []

    try:
        parsed_sql = parse_sql_with_script(sql)
        checks.append("deterministic_sql_parse")
    except ValueError as error:
        return {"passed": False, "checks": checks, "errors": [str(error)]}

    sql_type = parsed_sql.get("sql_type", "").upper()
    table_name = parsed_sql.get("target_table")

    if sql_type in ALLOWED_SQL_TYPES:
        checks.append("sql_type_allowed")
    else:
        errors.append("unsupported_sql_type")
    if table_name in ALLOWED_TABLES and table_name in table_columns:
        checks.append("table_allowed_by_live_schema")
    else:
        errors.append("unknown_target_table")
    if table_name == sql_candidate.get("target_table"):
        checks.append("sql_targets_declared_table")
    else:
        errors.append("sql_text_target_table_mismatch")

    referenced_columns = parsed_sql.get("referenced_columns", [])
    declared_columns = set(sql_candidate.get("referenced_columns", []))
    parsed_columns = set(referenced_columns)
    known_columns = set(table_columns.get(table_name, [])) if table_name else set()
    if sql_type == "SELECT":
        unknown_columns = [column for column in declared_columns if column not in known_columns]
        column_check_passed = bool(declared_columns) and not unknown_columns
    else:
        unknown_columns = [column for column in referenced_columns if column not in known_columns]
        column_check_passed = declared_columns == parsed_columns and bool(referenced_columns) and not unknown_columns
    if column_check_passed:
        checks.append("columns_exist_in_live_schema")
    else:
        errors.append("missing_or_unknown_referenced_columns")
    if parsed_sql.get("has_dangerous_tokens"):
        errors.append("dangerous_sql_token")
    else:
        checks.append("single_statement_no_comments")
    if sql_type in PREDICATE_SQL_TYPES and (not parsed_sql.get("has_where") or parsed_sql.get("broad_predicate")):
        errors.append("write_sql_requires_where_review")
    else:
        checks.append("where_policy_checked")
    if sql.count("%s") == len(sql_candidate.get("params", [])):
        checks.append("parameter_count_matches")
    else:
        errors.append("parameter_count_mismatch")

    if sql_type == "UPDATE":
        protected_set_columns = [
            column
            for column in parsed_sql.get("set_columns", [parsed_sql.get("set_column")])
            if is_protected_by_policy(str(column), str(table_name), protected_column_policies)
        ]
        if protected_set_columns:
            errors.append(f"protected_column_modified: {protected_set_columns}")
        else:
            checks.append("protected_columns_not_modified")
        if sql_candidate.get("actions") and sql_candidate.get("predicate"):
            checks.append("preview_metadata_present")
        else:
            errors.append("missing_preview_metadata")
        predicate_columns = [item.get("column") for item in sql_candidate.get("predicate", [])]
        parsed_where_columns = [item.get("column") for item in parsed_sql.get("where_conditions", [])]
        if predicate_columns == parsed_where_columns:
            checks.append("predicate_columns_match_sql")
        else:
            errors.append("predicate_columns_do_not_match_sql")
        if any(item.get("source") == "modification" for item in sql_candidate.get("predicate", [])):
            checks.append("modification_predicate_present")
        else:
            errors.append("write_sql_requires_resolved_modification_predicate")

    if sql_type == "INSERT":
        if parsed_sql.get("insert_table") == DERIVED_VALUE_TABLE and sql_candidate.get("insert_table") == DERIVED_VALUE_TABLE:
            checks.append("derived_value_insert_target_allowed")
        else:
            errors.append("unsupported_insert_target")
        if parsed_sql.get("has_where") and not parsed_sql.get("broad_predicate"):
            checks.append("derived_insert_scope_present")
        else:
            errors.append("derived_insert_requires_where_review")
        if sql_candidate.get("actions") and sql_candidate.get("predicate"):
            checks.append("preview_metadata_present")
        else:
            errors.append("missing_preview_metadata")
        predicate_columns = [item.get("column") for item in sql_candidate.get("predicate", [])]
        parsed_where_columns = [item.get("column") for item in parsed_sql.get("where_conditions", [])]
        if predicate_columns == parsed_where_columns:
            checks.append("predicate_columns_match_sql")
        else:
            errors.append("predicate_columns_do_not_match_sql")

    return {"passed": not errors, "checks": checks, "errors": errors, "parsed_sql": parsed_sql}


def validate_generated_sql_node(state: ModificationWorkflowState, llm: Any = None) -> dict[str, Any]:
    script_review = validate_sql_with_script(state["sql_candidate"], state.get("table_columns", {}), state.get("protected_column_policies", []))
    validation_passed = script_review["passed"]
    return {
        "parsed_sql": script_review.get("parsed_sql", {}),
        "validation_result": {
            "status": "passed" if validation_passed else "failed",
            "user_review_required": True,
            "review_note": "LLM validation is disabled; user verifies the query and examples.",
            "script_review": script_review,
        },
    }
