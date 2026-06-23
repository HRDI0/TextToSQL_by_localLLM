from __future__ import annotations

# pyright: reportTypedDictNotRequiredAccess=false

import hashlib
import json
import re
from datetime import datetime
from typing import Any

from app.langgraph_workflow.db import quote_identifier
from app.langgraph_workflow.state import ModificationWorkflowState


def row_matches_predicate(row: dict[str, Any], predicate: list[dict[str, Any]]) -> bool:
    for condition in predicate:
        row_value = str(row.get(condition["column"], ""))
        values = {str(value) for value in condition.get("values", [])}
        if condition.get("operator") == "eq" and row_value not in values:
            return False
        if condition.get("operator") == "in" and row_value not in values:
            return False
        if condition.get("operator") == "like" and not any(value.strip("%") in row_value for value in values):
            return False
    return True


def apply_action_value(row: dict[str, Any], action: dict[str, Any]) -> Any:
    if action["operation"] == "set_literal":
        return action.get("value", "")
    if action["operation"] == "set_zero":
        return 0
    return row.get(action["target_field"])


def preview_fingerprint(
    preview_rows: list[dict[str, Any]],
    affected_row_count: int,
    sql_fingerprint: str | None,
) -> str:
    payload = json.dumps(
        {
            "affected_row_count": affected_row_count,
            "preview_rows": preview_rows,
            "sql_fingerprint": sql_fingerprint,
        },
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def extract_update_where_sql(sql: str) -> str:
    match = re.fullmatch(r"UPDATE\s+`([^`]+)`\s+SET\s+.+\s+WHERE\s+(.+)", sql.strip(), flags=re.IGNORECASE)
    if not match:
        raise ValueError("Preview requires validated UPDATE SQL.")
    return match.group(2)


def build_preview_from_candidate(
    connection: Any,
    sql_candidate: dict[str, Any],
    limit: int = 2,
) -> tuple[list[dict[str, Any]], int, list[dict[str, Any]]]:
    if sql_candidate.get("sql_type") == "SELECT":
        if connection is None:
            raise ValueError("SELECT preview rows must be derived from the database with the validated SQL.")
        preview_sql = f"SELECT * FROM ({sql_candidate['sql']}) AS preview_query LIMIT %s"
        with connection.cursor() as cursor:
            cursor.execute(preview_sql, tuple([*sql_candidate.get("params", []), limit]))
            rows = list(cursor.fetchall())
        return [{"row_index": index, "db_row": row} for index, row in enumerate(rows)], len(rows), []
    if sql_candidate.get("sql_type") == "INSERT" and sql_candidate.get("source") == "derived_value_insert_renderer":
        if connection is None:
            raise ValueError("Derived-value preview rows must be derived from the database with the validated SQL predicate.")
        target_table = sql_candidate["target_table"]
        where_sql = sql_candidate.get("where_sql") or "1 = 1"
        where_params = list(sql_candidate.get("where_params", []))
        count_sql = f"SELECT COUNT(*) AS affected_row_count FROM {quote_identifier(target_table)} WHERE {where_sql}"
        select_sql = f"SELECT * FROM {quote_identifier(target_table)} WHERE {where_sql} LIMIT %s"
        with connection.cursor() as cursor:
            cursor.execute(count_sql, tuple(where_params))
            affected_row_count = int(cursor.fetchone()["affected_row_count"])
            cursor.execute(select_sql, tuple([*where_params, limit]))
            target_rows = list(cursor.fetchall())
        preview_rows = [
            {
                "row_index": index,
                "db_row": row,
                "before": {sql_candidate.get("derived_key"): None},
                "after": {sql_candidate.get("derived_key"): sql_candidate.get("derived_value")},
            }
            for index, row in enumerate(target_rows)
        ]
        return preview_rows, affected_row_count, []
    if sql_candidate.get("sql_type") != "UPDATE":
        return [], 0, []
    if connection is None:
        raise ValueError("Preview rows must be derived from the database with the validated SQL predicate.")

    actions = sql_candidate.get("actions", [])
    if not actions:
        raise ValueError("Preview requires validated action metadata.")
    target_table = sql_candidate["target_table"]
    where_sql = extract_update_where_sql(sql_candidate["sql"])
    where_params = list(sql_candidate.get("params", []))[len(actions) :]
    count_sql = f"SELECT COUNT(*) AS affected_row_count FROM {quote_identifier(target_table)} WHERE {where_sql}"
    select_sql = f"SELECT * FROM {quote_identifier(target_table)} WHERE {where_sql}"

    with connection.cursor() as cursor:
        cursor.execute(count_sql, tuple(where_params))
        affected_row_count = int(cursor.fetchone()["affected_row_count"])
        cursor.execute(select_sql, tuple(where_params))
        target_rows = list(cursor.fetchall())

    preview_rows: list[dict[str, Any]] = []
    delta_items: list[dict[str, Any]] = []
    for index, row in enumerate(target_rows):
        before = {action["target_field"]: row.get(action["target_field"]) for action in actions}
        after = {action["target_field"]: apply_action_value(row, action) for action in actions}
        delta_item = {
            "step_id": sql_candidate.get("active_step_id"),
            "step_order": sql_candidate.get("active_step_order"),
            "target_table": target_table,
            "source_row_id": row.get("row_id"),
            "source_row_hash": row.get("source_row_hash"),
            "delta_type": "preview_update",
            "before": before,
            "after": after,
            "delta": {column: {"old_value": before.get(column), "new_value": after.get(column)} for column in after},
            "status": sql_candidate.get("delta_status", "pending"),
        }
        delta_items.append(delta_item)
        if len(preview_rows) < limit:
            preview_rows.append({"row_index": index, "db_row": row, "before": before, "after": after, "delta_item": delta_item})
    return preview_rows, affected_row_count, delta_items


def render_sql_preview(connection: Any, sql_candidate: dict[str, Any]) -> str:
    sql = sql_candidate.get("sql", "")
    params = tuple(sql_candidate.get("params", []))
    if not sql or connection is None:
        return sql
    with connection.cursor() as cursor:
        rendered = cursor.mogrify(sql, params)
    return rendered.decode("utf-8") if isinstance(rendered, bytes) else str(rendered)


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


def update_where_params(sql_candidate: dict[str, Any]) -> list[Any]:
    actions = sql_candidate.get("actions", [])
    return list(sql_candidate.get("params", []))[len(actions) :]


def rollback_snapshot(connection: Any, sql_candidate: dict[str, Any]) -> dict[str, Any]:
    if sql_candidate.get("sql_type") != "UPDATE":
        return {"rows": [], "rollback_sql": None, "rollback_params": []}
    target_table = sql_candidate["target_table"]
    actions = sql_candidate.get("actions", [])
    target_fields = [action["target_field"] for action in actions]
    if not target_fields:
        return {"rows": [], "rollback_sql": None, "rollback_params": []}
    where_sql = extract_update_where_sql(sql_candidate["sql"])
    select_columns = ["row_id", *target_fields]
    select_sql = f"SELECT {', '.join(quote_identifier(column) for column in select_columns)} FROM {quote_identifier(target_table)} WHERE {where_sql}"
    with connection.cursor() as cursor:
        cursor.execute(select_sql, tuple(update_where_params(sql_candidate)))
        rows = list(cursor.fetchall())
    rollback_set_sql = ", ".join(f"{quote_identifier(column)} = %s" for column in target_fields)
    rollback_sql = f"UPDATE {quote_identifier(target_table)} SET {rollback_set_sql} WHERE `row_id` = %s"
    rollback_params = [[row.get(column) for column in target_fields] + [row.get("row_id")] for row in rows]
    return {"rows": rows, "rollback_sql": rollback_sql, "rollback_params": rollback_params}


def insert_execution_log(
    connection: Any,
    state: ModificationWorkflowState,
    status: str,
    affected_row_count: int | None = None,
    error_message: str | None = None,
    rollback: dict[str, Any] | None = None,
) -> int | None:
    if not table_exists(connection, "rule_engine_execution_log"):
        return None
    sql_candidate = state.get("sql_candidate", {})
    change_preview_json = state.get("change_preview_json", {})
    rollback = rollback or {}
    request_text = "\n".join(part for part in [state.get("selection_text", ""), state.get("modification_text", "")] if part)
    executed_at = datetime.now() if status == "executed" else None
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO rule_engine_execution_log (
                request_text, selection_text, modification_text, generated_ir, generated_sql,
                sql_params_json, sql_fingerprint, preview_row_count, affected_row_count,
                approval_status, approved_at, executed_at, rollback_sql, rollback_params_json,
                error_message
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), %s, %s, %s, %s)
            """,
            (
                request_text,
                state.get("selection_text"),
                state.get("modification_text"),
                json.dumps(state.get("ir_structured_json", {}), ensure_ascii=False, default=str),
                sql_candidate.get("sql"),
                json.dumps(sql_candidate.get("params", []), ensure_ascii=False, default=str),
                sql_candidate.get("sql_fingerprint"),
                int(change_preview_json.get("previewed_row_count") or 0),
                affected_row_count,
                status,
                executed_at,
                rollback.get("rollback_sql"),
                json.dumps(rollback.get("rollback_params", []), ensure_ascii=False, default=str),
                error_message,
            ),
        )
        return int(cursor.lastrowid or 0)


def build_change_preview_json(
    sql_candidate: dict[str, Any],
    validation_result: dict[str, Any],
    preview_rows: list[dict[str, Any]],
    affected_row_count: int,
    rendered_sql: str | None = None,
    preview_error: str | None = None,
    preview_delta_items: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if validation_result.get("status") != "passed":
        status = "blocked_by_validation"
    elif preview_error:
        status = "blocked_by_preview_generation"
    else:
        status = "pending_user_confirmation"
    return {
        "status": status,
        "operation": sql_candidate.get("sql_type"),
        "target_table": sql_candidate.get("target_table"),
        "rendered_sql": rendered_sql or sql_candidate.get("sql", ""),
        "preview_generation_source": "deterministic_python_script",
        "preview_generation_note": "Sample rows and before/after examples are derived by Python code from validated SQL metadata and database rows, not by the LLM.",
        "sql_fingerprint": sql_candidate.get("sql_fingerprint"),
        "preview_fingerprint": preview_fingerprint(preview_rows, affected_row_count, sql_candidate.get("sql_fingerprint")),
        "affected_row_count": affected_row_count,
        "previewed_row_count": len(preview_rows),
        "preview_limited": affected_row_count > len(preview_rows),
        "changes": preview_rows,
        "preview_delta_items": preview_delta_items or [],
        "validation_result": validation_result,
        "preview_error": preview_error,
    }


def build_change_preview_json_node(state: ModificationWorkflowState, connection: Any = None) -> dict[str, Any]:
    validation_result = state["validation_result"]
    if validation_result.get("status") != "passed":
        preview_rows: list[dict[str, Any]] = []
        return {
            "preview_rows": preview_rows,
            "change_preview_json": build_change_preview_json(
                state["sql_candidate"],
                validation_result,
                preview_rows,
                0,
            ),
        }

    preview_error = None
    try:
        rendered_sql = render_sql_preview(connection, state["sql_candidate"])
        sql_candidate = {
            **state["sql_candidate"],
            "active_step_id": state.get("active_step_id"),
            "active_step_order": state.get("effective_preview_context", {}).get("active_step_order"),
        }
        preview_rows, affected_row_count, preview_delta_items = build_preview_from_candidate(connection, sql_candidate)
    except Exception as exc:
        rendered_sql = state["sql_candidate"].get("sql", "")
        preview_rows = []
        preview_delta_items = []
        affected_row_count = 0
        preview_error = str(exc)
    linked_step_result = {
        "step_id": state.get("active_step_id"),
        "sql_fingerprint": state["sql_candidate"].get("sql_fingerprint"),
        "sql_type": state["sql_candidate"].get("sql_type"),
        "target_table": state["sql_candidate"].get("target_table"),
        "affected_row_count": affected_row_count,
        "previewed_row_count": len(preview_rows),
        "preview_delta_items": preview_delta_items,
    }
    linked_step_results = [*state.get("linked_step_results", [])]
    if state.get("active_step_id"):
        linked_step_results.append(linked_step_result)
    return {
        "preview_rows": preview_rows,
        "preview_delta_items": preview_delta_items,
        "linked_step_results": linked_step_results,
        "change_preview_json": build_change_preview_json(
            state["sql_candidate"],
            validation_result,
            preview_rows,
            affected_row_count,
            rendered_sql,
            preview_error,
            preview_delta_items,
        ),
    }


def require_user_confirmation(change_preview_json: dict[str, Any], existing_confirmation: dict[str, Any] | None = None) -> dict[str, Any]:
    if existing_confirmation and "approved" in existing_confirmation:
        approved = existing_confirmation.get("approved") is True
        return {
            "status": "approved" if approved else "rejected",
            "preview": change_preview_json,
            "approved": approved,
            "approved_sql_fingerprint": existing_confirmation.get("approved_sql_fingerprint"),
            "approved_preview_fingerprint": existing_confirmation.get("approved_preview_fingerprint"),
        }
    return {"status": "waiting", "preview": change_preview_json, "approved": False}


def can_execute(validation_result: dict[str, Any], user_confirmation: dict[str, Any], change_preview_json: dict[str, Any]) -> bool:
    return (
        validation_result.get("status") == "passed"
        and change_preview_json.get("status") == "pending_user_confirmation"
        and user_confirmation.get("approved") is True
        and user_confirmation.get("approved_sql_fingerprint") == change_preview_json.get("sql_fingerprint")
        and user_confirmation.get("approved_preview_fingerprint") == change_preview_json.get("preview_fingerprint")
    )


def wait_for_user_confirmation_node(state: ModificationWorkflowState) -> dict[str, Any]:
    existing = state.get("user_confirmation", {})
    if state.get("approved_sql_fingerprint"):
        existing = {**existing, "approved_sql_fingerprint": state.get("approved_sql_fingerprint")}
    if state.get("approved_preview_fingerprint"):
        existing = {**existing, "approved_preview_fingerprint": state.get("approved_preview_fingerprint")}
    return {"user_confirmation": require_user_confirmation(state["change_preview_json"], existing)}


def execute_confirmed_sql(connection: Any, state: ModificationWorkflowState) -> dict[str, Any]:
    sql_candidate = state["sql_candidate"]
    if state.get("active_step_id"):
        return {
            "status": "skipped",
            "reason": "linked_step_preview_approval_only",
            "operation": sql_candidate.get("sql_type"),
            "target_table": sql_candidate.get("target_table"),
            "affected_row_count": 0,
        }
    if sql_candidate.get("execution_allowed") is False:
        return {
            "status": "skipped",
            "reason": "preview_only_sql_candidate",
            "operation": sql_candidate.get("sql_type"),
            "target_table": sql_candidate.get("target_table"),
            "affected_row_count": 0,
        }
    rollback: dict[str, Any] = {"rows": [], "rollback_sql": None, "rollback_params": []}
    try:
        if sql_candidate["sql_type"] == "UPDATE":
            rollback = rollback_snapshot(connection, sql_candidate)
        with connection.cursor() as cursor:
            cursor.execute(sql_candidate["sql"], tuple(sql_candidate.get("params", [])))
            if sql_candidate["sql_type"] != "SELECT":
                affected_row_count = cursor.rowcount
            else:
                rows = list(cursor.fetchmany(100))
                execution_id = insert_execution_log(connection, state, "executed", 0, rollback=rollback)
                connection.commit()
                return {
                    "status": "executed",
                    "operation": sql_candidate["sql_type"],
                    "target_table": sql_candidate["target_table"],
                    "affected_row_count": 0,
                    "result_row_count": len(rows),
                    "execution_log_id": execution_id,
                }
        execution_id = insert_execution_log(connection, state, "executed", affected_row_count, rollback=rollback)
        connection.commit()
        return {
            "status": "executed",
            "operation": sql_candidate["sql_type"],
            "target_table": sql_candidate["target_table"],
            "affected_row_count": affected_row_count,
            "execution_log_id": execution_id,
            "rollback_row_count": len(rollback.get("rows", [])),
        }
    except Exception as exc:
        connection.rollback()
        insert_execution_log(connection, state, "failed", error_message=str(exc), rollback=rollback)
        connection.commit()
        raise


def execute_confirmed_sql_node(state: ModificationWorkflowState, connection: Any = None) -> dict[str, Any]:
    if state.get("active_step_id"):
        return {
            "execution_result": {
                "status": "skipped",
                "reason": "linked_step_preview_approval_only",
                "operation": state["sql_candidate"].get("sql_type"),
                "target_table": state["sql_candidate"].get("target_table"),
            }
        }
    if state["sql_candidate"].get("execution_allowed") is False:
        return {
            "execution_result": {
                "status": "skipped",
                "reason": "preview_only_sql_candidate",
                "operation": state["sql_candidate"].get("sql_type"),
                "target_table": state["sql_candidate"].get("target_table"),
            }
        }
    if not can_execute(state["validation_result"], state["user_confirmation"], state["change_preview_json"]):
        return {
            "execution_result": {
                "status": "skipped",
                "reason": "validation_preview_or_approval_check_failed",
                "operation": state["sql_candidate"].get("sql_type"),
                "target_table": state["sql_candidate"].get("target_table"),
            }
        }
    if connection is None:
        return {
            "execution_result": {
                "status": "skipped",
                "reason": "no_connection_configured",
                "operation": state["sql_candidate"].get("sql_type"),
                "target_table": state["sql_candidate"].get("target_table"),
            }
        }
    return {"execution_result": execute_confirmed_sql(connection, state)}


def build_execution_result_json(execution_result: dict[str, Any], effective_modification_plan: dict[str, Any]) -> dict[str, Any]:
    status = execution_result.get("status", "skipped")
    reason = execution_result.get("reason")
    result_message = "사용자 승인 후 SQL 실행 완료" if status == "executed" else f"SQL 실행 안 함: {reason}"
    return {
        "status": status,
        "operation": execution_result.get("operation"),
        "target_table": execution_result.get("target_table"),
        "affected_row_count": execution_result.get("affected_row_count", 0),
        "execution_log_id": execution_result.get("execution_log_id"),
        "rollback_row_count": execution_result.get("rollback_row_count", 0),
        "rule_source": effective_modification_plan.get("source"),
        "result_message": result_message,
    }


def build_ir_structured_json(state: ModificationWorkflowState) -> dict[str, Any]:
    if state.get("ir_structured_json"):
        return state["ir_structured_json"]
    return {
        "selection": state.get("selection_request", {}),
        "modification": state.get("modification_logic", {}),
        "effective_modification_plan": state.get("effective_modification_plan", {}),
    }


def build_query_from_ir(state: ModificationWorkflowState) -> dict[str, Any]:
    sql_candidate = state.get("sql_candidate", {})
    change_preview_json = state.get("change_preview_json", {})
    return {
        "sql_type": sql_candidate.get("sql_type"),
        "target_table": sql_candidate.get("target_table"),
        "sql": change_preview_json.get("rendered_sql") or sql_candidate.get("sql", ""),
        "sql_template": sql_candidate.get("sql", ""),
        "params": sql_candidate.get("params", []),
        "referenced_columns": sql_candidate.get("referenced_columns", []),
        "predicate": sql_candidate.get("predicate", []),
        "sql_fingerprint": sql_candidate.get("sql_fingerprint"),
        "validation_result": state.get("validation_result", {}),
        "reason": sql_candidate.get("reason"),
    }


def build_row_modification_examples(state: ModificationWorkflowState) -> dict[str, Any]:
    change_preview_json = state.get("change_preview_json", {})
    examples = state.get("preview_rows", [])
    sample_rows = filter_sample_rows([preview_row_to_sample_row(row) for row in examples])
    return {
        "status": change_preview_json.get("status"),
        "preview_generation_source": change_preview_json.get("preview_generation_source"),
        "preview_generation_note": change_preview_json.get("preview_generation_note"),
        "affected_row_count": change_preview_json.get("affected_row_count", 0),
        "previewed_row_count": change_preview_json.get("previewed_row_count", 0),
        "preview_limited": change_preview_json.get("preview_limited", False),
        "examples": examples,
        "sample_rows": sample_rows,
        "preview_error": change_preview_json.get("preview_error"),
    }


def flatten_value(prefix: str, value: Any, output: dict[str, Any]) -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            flatten_value(f"{prefix}.{key}" if prefix else str(key), nested, output)
        return
    output[prefix] = value


def preview_row_to_sample_row(row: dict[str, Any]) -> dict[str, Any]:
    output: dict[str, Any] = {"row_index": row.get("row_index")}
    db_row = row.get("db_row")
    if isinstance(db_row, dict):
        for key, value in db_row.items():
            output[str(key)] = value
    elif isinstance(row.get("result"), dict):
        for key, value in row["result"].items():
            output[str(key)] = value
    for key in ["before", "after"]:
        if isinstance(row.get(key), dict):
            flatten_value(key, row[key], output)
    return output


def is_hidden_sample_column(column: str) -> bool:
    normalized = re.sub(r"[\s_\-.]+", "", column).lower()
    return "hash" in normalized or "해시" in column


def is_empty_sample_value(value: Any) -> bool:
    if value is None:
        return True
    return isinstance(value, str) and value.strip() == ""


def filter_sample_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not rows:
        return []
    columns: list[str] = []
    for row in rows:
        for column in row.keys():
            if column not in columns:
                columns.append(column)

    visible_columns = [
        column
        for column in columns
        if column == "row_index" or (not is_hidden_sample_column(column) and not all(is_empty_sample_value(row.get(column)) for row in rows))
    ]
    return [{column: row.get(column) for column in visible_columns if column in row} for row in rows]


def build_final_output_json(state: ModificationWorkflowState) -> dict[str, Any]:
    ir_structured_json = build_ir_structured_json(state)
    return {
        "ir_structured_json": ir_structured_json,
        "query_from_ir": build_query_from_ir(state),
        "row_modification_examples": build_row_modification_examples(state),
        "workflow_steps": state.get("workflow_steps", []),
        "linked_step_plan": state.get("linked_step_plan", []),
        "linked_step_validation": state.get("linked_step_validation", {}),
        "preview_delta_items": state.get("preview_delta_items", []),
        "effective_preview_context": state.get("effective_preview_context", {}),
        "linked_step_results": state.get("linked_step_results", []),
        "query_recommendations": state.get("query_recommendations", []),
        "resolution_candidates": state.get("resolution_candidates", []),
        "resolution_warnings": state.get("resolution_warnings", []),
        "execution_result": build_execution_result_json(
            execution_result=state.get("execution_result", {}),
            effective_modification_plan=state.get("effective_modification_plan", {}),
        ),
    }


def build_execution_result_json_node(state: ModificationWorkflowState) -> dict[str, Any]:
    ir_structured_json = build_ir_structured_json(state)
    return {"ir_structured_json": ir_structured_json, "output_json": build_final_output_json({**state, "ir_structured_json": ir_structured_json})}
