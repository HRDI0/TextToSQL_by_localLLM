from __future__ import annotations

import json
import logging
import os
import re
import sys
from pathlib import Path
from typing import Any, cast

import streamlit as st  # type: ignore[import-not-found]

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

try:
    from dotenv import load_dotenv
except ImportError as exc:
    raise RuntimeError("SQL_WORKFLOW_ENV_LOADER_IMPORT_FAILED: install python-dotenv in .venv") from exc

load_dotenv(PROJECT_ROOT / ".env")

from app.langgraph_workflow import (
    ModificationWorkflowState,
    build_demo_state,
    build_modification_workflow_graph,
    connect_db,
)
from app.langgraph_workflow.stage_04_output import build_final_output_json, can_execute, execute_confirmed_sql


SELECTION_PLACEHOLDER = "예: 5월 검색광고를 확인하고 싶어."
MODIFICATION_PLACEHOLDER = "예: 검색광고에서 클릭이 있는 데이터는 클릭을 0으로 맞춰줘."
STORED_RULES_PLACEHOLDER = "개발용 저장 규칙이 없으면 비워두세요."
DEFAULT_LLM_BASE_URL = "http://127.0.0.1:8000/v1"
DEFAULT_LLM_MODEL = "qwen3-14b"
DEFAULT_GEMINI_MODEL = "gemini-3.1-flash-lite"
GEMINI_OPENAI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
LOGGER = logging.getLogger(__name__)


def has_streamlit_context() -> bool:
    try:
        from streamlit.runtime.scriptrunner import get_script_run_ctx  # type: ignore[import-not-found]
    except Exception:
        return False
    return get_script_run_ctx(suppress_warning=True) is not None


def session_value(key: str, default: Any = None) -> Any:
    if not has_streamlit_context():
        return default
    return st.session_state.get(key, default)


def env_flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def parse_stored_rules(raw_json: str) -> list[dict[str, Any]]:
    parsed = json.loads(raw_json or "[]")
    if not isinstance(parsed, list):
        raise ValueError("stored_rules JSON must be a list.")
    for index, item in enumerate(parsed):
        if not isinstance(item, dict):
            raise ValueError(f"stored_rules[{index}] must be an object.")
    return parsed


def build_local_llm() -> Any:
    provider = str(session_value("llm_provider") or os.environ.get("SQL_WORKFLOW_LLM_PROVIDER", "openai_compatible")).strip()
    base_url = str(os.environ.get("SQL_WORKFLOW_LLM_BASE_URL", DEFAULT_LLM_BASE_URL)).strip()
    model = str(session_value("llm_model") or os.environ.get("SQL_WORKFLOW_LLM_MODEL", DEFAULT_LLM_MODEL)).strip()
    api_key = str(session_value("llm_api_key") or os.environ.get("SQL_WORKFLOW_LLM_API_KEY", "EMPTY")).strip()
    temperature = float(session_value("llm_temperature") or os.environ.get("SQL_WORKFLOW_LLM_TEMPERATURE", "0"))
    max_tokens = int(session_value("llm_max_tokens") or os.environ.get("SQL_WORKFLOW_LLM_MAX_TOKENS", "2048"))
    timeout = float(session_value("llm_timeout") or os.environ.get("SQL_WORKFLOW_LLM_TIMEOUT", "180"))
    if provider == "gemini_native":
        try:
            from langchain_google_genai import ChatGoogleGenerativeAI
        except ImportError as exc:
            raise RuntimeError("SQL_WORKFLOW_GEMINI_CLIENT_IMPORT_FAILED: install langchain-google-genai in .venv") from exc
        gemini_key = str(
            session_value("llm_api_key")
            or os.environ.get("GOOGLE_API_KEY")
            or os.environ.get("GEMINI_API_KEY")
            or os.environ.get("SQL_WORKFLOW_LLM_API_KEY", "")
        ).strip()
        if not gemini_key or gemini_key == "EMPTY":
            raise RuntimeError("GEMINI_API_KEY_MISSING: set GOOGLE_API_KEY, GEMINI_API_KEY, or enter an admin API key.")
        gemini_model = str(session_value("llm_model") or os.environ.get("SQL_WORKFLOW_GEMINI_MODEL", DEFAULT_GEMINI_MODEL)).strip()
        restore_gemini_key = None
        if os.environ.get("GOOGLE_API_KEY") and os.environ.get("GEMINI_API_KEY"):
            restore_gemini_key = os.environ.pop("GEMINI_API_KEY")
        try:
            return ChatGoogleGenerativeAI(
                model=gemini_model,
                api_key=gemini_key,
                temperature=temperature,
                max_tokens=max_tokens,
                timeout=timeout,
            )
        finally:
            if restore_gemini_key is not None:
                os.environ["GEMINI_API_KEY"] = restore_gemini_key

    try:
        from langchain_openai import ChatOpenAI
    except ImportError as exc:
        raise RuntimeError("SQL_WORKFLOW_LLM_CLIENT_IMPORT_FAILED: install langchain-openai in .venv") from exc

    if provider == "gemini_openai_compatible":
        base_url = base_url if base_url != DEFAULT_LLM_BASE_URL else GEMINI_OPENAI_BASE_URL
        api_key = str(session_value("llm_api_key") or os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY") or api_key).strip()
        model = str(session_value("llm_model") or os.environ.get("SQL_WORKFLOW_GEMINI_MODEL", DEFAULT_GEMINI_MODEL)).strip()
        if not api_key or api_key == "EMPTY":
            raise RuntimeError("GEMINI_API_KEY_MISSING: set GOOGLE_API_KEY, GEMINI_API_KEY, or enter an admin API key.")
    if not base_url:
        raise RuntimeError("SQL_WORKFLOW_LLM_BASE_URL_MISSING")
    if not model:
        raise RuntimeError("SQL_WORKFLOW_LLM_MODEL_MISSING")

    return ChatOpenAI(
        base_url=base_url,
        api_key=lambda: api_key,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=timeout,
        extra_body={"chat_template_kwargs": {"enable_thinking": os.environ.get("SQL_WORKFLOW_LLM_ENABLE_THINKING", "false").lower() == "true"}},
    )


def run_graph(
    selection_text: str,
    modification_text: str,
    stored_rules: list[dict[str, Any]],
    approved: bool,
    approved_sql_fingerprint: str | None = None,
    approved_preview_fingerprint: str | None = None,
    ir_override: dict[str, Any] | None = None,
    active_step_id: str | None = None,
    effective_preview_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    state = build_demo_state(approved=approved, stored_rules=stored_rules)
    state.update(
        {
            "selection_text": selection_text,
            "modification_text": modification_text,
        }
    )
    if approved_sql_fingerprint:
        state["approved_sql_fingerprint"] = approved_sql_fingerprint
    if approved_preview_fingerprint:
        state["approved_preview_fingerprint"] = approved_preview_fingerprint
    if active_step_id:
        state["active_step_id"] = active_step_id
    if effective_preview_context:
        state["effective_preview_context"] = effective_preview_context
    if ir_override:
        state["ir_structured_json"] = ir_override
        state["selection_request"] = dict(ir_override.get("selection", {}))
        state["modification_logic"] = dict(ir_override.get("modification", {}))
    llm = build_local_llm()
    with connect_db() as connection:
        graph = build_modification_workflow_graph(connection=connection, llm=llm)
        return graph.invoke(state)


def apply_existing_preview_result(result: dict[str, Any], approved: bool, execute: bool = True) -> dict[str, Any]:
    updated = dict(result)
    change_preview_json = updated.get("change_preview_json", {})
    user_confirmation = {
        "status": "approved" if approved else "rejected",
        "preview": change_preview_json,
        "approved": approved,
        "approved_sql_fingerprint": change_preview_json.get("sql_fingerprint"),
        "approved_preview_fingerprint": change_preview_json.get("preview_fingerprint"),
    }
    updated["user_confirmation"] = user_confirmation

    if not approved:
        updated["execution_result"] = {
            "status": "skipped",
            "reason": "user_rejected",
            "operation": updated.get("sql_candidate", {}).get("sql_type"),
            "target_table": updated.get("sql_candidate", {}).get("target_table"),
        }
    elif execute and can_execute(updated["validation_result"], user_confirmation, change_preview_json):
        with connect_db() as connection:
            updated["execution_result"] = execute_confirmed_sql(connection, cast(ModificationWorkflowState, updated))
    elif not execute:
        updated["execution_result"] = {
            "status": "skipped",
            "reason": "linked_step_preview_approval_only",
            "operation": updated.get("sql_candidate", {}).get("sql_type"),
            "target_table": updated.get("sql_candidate", {}).get("target_table"),
        }
    else:
        updated["execution_result"] = {
            "status": "skipped",
            "reason": "validation_preview_or_approval_check_failed",
            "operation": updated.get("sql_candidate", {}).get("sql_type"),
            "target_table": updated.get("sql_candidate", {}).get("target_table"),
        }

    updated["output_json"] = build_final_output_json(cast(ModificationWorkflowState, updated))
    return updated


def show_state_section(title: str, state: dict[str, Any], key: str) -> None:
    with st.expander(title, expanded=key in {"change_preview_json", "validation_result", "output_json"}):
        st.json(state.get(key, {}))


def show_final_result(result: dict[str, Any]) -> None:
    output = result.get("output_json", {})
    st.subheader("1. 요청 해석 결과")
    st.json(output.get("ir_structured_json", {}))

    st.subheader("2. 실제 적용 전 확인용 명령")
    query_from_ir = output.get("query_from_ir", {})
    sql_text = query_from_ir.get("sql") or f"-- SQL not generated\n-- reason: {query_from_ir.get('reason') or result.get('errors', [])}"
    st.code(sql_text, language="sql", wrap_lines=True)
    st.caption("이 명령은 승인 버튼을 누르기 전에는 실행되지 않습니다. 먼저 아래 샘플 데이터로 대상이 맞는지 확인하세요.")

    st.subheader("3. 샘플 데이터")
    row_examples = output.get("row_modification_examples", {})
    sample_rows = row_examples.get("sample_rows", []) if isinstance(row_examples, dict) else []
    affected_count = row_examples.get("affected_row_count") if isinstance(row_examples, dict) else None
    shown_count = row_examples.get("previewed_row_count") if isinstance(row_examples, dict) else None
    if affected_count is not None or shown_count is not None:
        st.caption(f"대상 데이터 {affected_count or 0}건 중 화면 표시 {shown_count or 0}건입니다.")
    if sample_rows:
        st.dataframe(sample_rows, use_container_width=True)
    else:
        st.info("표시할 샘플 데이터가 없습니다.")


def step_ids_to_invalidate(steps: list[dict[str, Any]], step_id: str) -> set[str]:
    invalidated = {step_id}
    changed = True
    while changed:
        changed = False
        for step in steps:
            current = str(step.get("step_id") or step.get("group_id"))
            depends_on = {str(item) for item in step.get("depends_on", [])}
            if current not in invalidated and depends_on & invalidated:
                invalidated.add(current)
                changed = True
    return invalidated


def source_channel_field_table(field: Any) -> tuple[str | None, bool]:
    raw = str(field or "").strip().strip("`")
    if raw == "source_channel":
        return None, True
    if raw.endswith(".source_channel"):
        return raw.split(".", 1)[0].strip("`"), True
    return None, False


def split_source_channel_value(value: Any) -> tuple[str | None, str]:
    raw = str(value)
    if "." in raw:
        table, source = raw.split(".", 1)
        if table in {"DA", "SA"}:
            return table, source
    return None, raw


def compact_text(value: Any) -> str:
    return "".join(str(value or "").split()).lower()


def media_segments_from_text(text: str) -> list[dict[str, str]]:
    markers = [("검색광고", "SA"), ("디스플레이 광고", "DA"), ("디스플레이", "DA")]
    matches: list[tuple[int, str, str]] = []
    for marker, table in markers:
        for match in re.finditer(re.escape(marker), text):
            matches.append((match.start(), marker, table))
    matches.sort(key=lambda item: item[0])
    segments: list[dict[str, str]] = []
    for index, (start, marker, table) in enumerate(matches):
        end = matches[index + 1][0] if index + 1 < len(matches) else len(text)
        segments.append({"table": table, "marker": marker, "text": text[start:end]})
    return segments


def group_metric_tokens(group: dict[str, Any]) -> list[str]:
    tokens: list[str] = []
    metric_map = {"클릭수": "클릭", "노출수": "노출", "비용": "비용", "세션수": "세션"}
    for condition in group.get("conditions", []):
        if isinstance(condition, dict):
            raw_field = str(condition.get("field") or "")
            tokens.extend(token for column, token in metric_map.items() if column in raw_field or token in raw_field)
    for action in group.get("actions", []):
        if isinstance(action, dict):
            raw_target = str(action.get("target_field") or action.get("target_column") or "")
            tokens.extend(token for column, token in metric_map.items() if column in raw_target or token in raw_target)
    for metric in group.get("metrics", []):
        if isinstance(metric, dict):
            raw_metric = json.dumps(metric, ensure_ascii=False)
            tokens.extend(token for column, token in metric_map.items() if column in raw_metric or token in raw_metric)
    for item in group.get("group_by", []):
        if isinstance(item, dict):
            raw_group = json.dumps(item, ensure_ascii=False)
            if "날짜" in raw_group:
                tokens.append("날짜")
            if "캠페인" in raw_group:
                tokens.append("캠페인")
            if "디바이스" in raw_group or "기기" in raw_group:
                tokens.append("기기")
    derived = group.get("derived_column", {})
    if isinstance(derived, dict):
        raw_derived = json.dumps(derived, ensure_ascii=False)
        tokens.extend(token for column, token in metric_map.items() if column in raw_derived or token in raw_derived)
        for value in derived.values():
            text = compact_text(value)
            if text:
                tokens.append(text)
    seen: set[str] = set()
    return [token for token in tokens if not (token in seen or seen.add(token))]


def explicit_group_table(group: dict[str, Any]) -> str | None:
    tables: set[str] = set()
    containers = [group.get("conditions", []), group.get("actions", []), group.get("metrics", []), group.get("group_by", [])]
    for container in containers:
        for item in container if isinstance(container, list) else []:
            if not isinstance(item, dict):
                continue
            raw = json.dumps(item, ensure_ascii=False)
            tables.update(match.group(1) for match in re.finditer(r"\b(DA|SA)\.", raw))
    if len(tables) == 1:
        return next(iter(tables))
    return None


def infer_step_table_from_text(result: dict[str, Any], group: dict[str, Any]) -> str | None:
    explicit = explicit_group_table(group)
    if explicit:
        return explicit
    segments = media_segments_from_text(str(result.get("modification_text") or ""))
    if not segments:
        return None
    tokens = group_metric_tokens(group)
    scores: dict[str, int] = {"SA": 0, "DA": 0}
    for segment in segments:
        segment_text = compact_text(segment["text"])
        for token in tokens:
            if compact_text(token) in segment_text:
                scores[segment["table"]] += 1
    best_table, best_score = max(scores.items(), key=lambda item: item[1])
    other_score = scores["DA" if best_table == "SA" else "SA"]
    if best_score > 0 and best_score > other_score:
        return best_table
    group_id = str(group.get("group_id") or group.get("step_id") or "")
    step_number_match = re.search(r"(\d+)", group_id)
    if step_number_match:
        index = int(step_number_match.group(1)) - 1
        if 0 <= index < len(segments):
            return segments[index]["table"]
    return None


def infer_dependency_table(result: dict[str, Any], group: dict[str, Any], groups: list[dict[str, Any]]) -> str | None:
    by_id = {str(candidate.get("group_id") or candidate.get("step_id")): candidate for candidate in groups}
    for dependency_id in group.get("depends_on", []):
        parent = by_id.get(str(dependency_id))
        if not parent:
            continue
        table = infer_step_table_from_text(result, parent)
        if table:
            return table
    return None


def sanitize_step_selection(selection: dict[str, Any], source_values: dict[str, list[str]], table: str | None) -> dict[str, Any]:
    sanitized = dict(selection)
    if table in {"DA", "SA"}:
        sanitized["tables"] = [table]
    current_table = table or next((str(item) for item in sanitized.get("tables", []) if str(item) in {"DA", "SA"}), None)
    if current_table:
        live_values = set(source_values.get(current_table, []))
        source_channels: list[str] = []
        for value in sanitized.get("source_channels", []):
            value_table, source = split_source_channel_value(value)
            if value_table and value_table != current_table:
                continue
            if not live_values or source in live_values:
                source_channels.append(source)
        sanitized["source_channels"] = sorted(set(source_channels))
    sanitized["unresolved_terms"] = []
    return sanitized


def source_channel_scope_for_group(result: dict[str, Any], group: dict[str, Any]) -> tuple[str | None, list[str]]:
    source_values = result.get("source_channel_values", {})
    mappings = result.get("source_channel_mappings", [])
    aliases: dict[str, dict[str, list[str]]] = {}
    for item in mappings:
        table = str(item.get("target_table") or "")
        source = str(item.get("source_channel") or "")
        if not table or not source:
            continue
        live_values = set(source_values.get(table, []))
        if source not in live_values:
            continue
        for key in {str(item.get("user_term") or ""), source, f"{table}.{source}"}:
            if key:
                aliases.setdefault(key, {}).setdefault(table, []).append(source)

    matched_by_table: dict[str, list[str]] = {}
    for condition in group.get("conditions", []):
        if not isinstance(condition, dict):
            continue
        field_table, is_source_channel = source_channel_field_table(condition.get("field"))
        if not is_source_channel:
            continue
        for value in condition.get("values", []):
            value_table, raw = split_source_channel_value(value)
            if field_table or value_table:
                table = field_table or value_table
                if raw in source_values.get(str(table), []):
                    matched_by_table.setdefault(str(table), []).append(raw)
                continue
            for table, live_values in source_values.items():
                if raw in live_values:
                    matched_by_table.setdefault(str(table), []).append(raw)
            for table, mapped_values in aliases.get(raw, {}).items():
                matched_by_table.setdefault(table, []).extend(mapped_values)

    matched_by_table = {table: sorted(set(values)) for table, values in matched_by_table.items() if values}
    if len(matched_by_table) == 1:
        table = next(iter(matched_by_table))
        return table, matched_by_table[table]
    return None, []


def normalize_step_source_channel_conditions(group: dict[str, Any], table: str, source_channels: list[str]) -> dict[str, Any]:
    normalized = dict(group)
    conditions: list[dict[str, Any]] = []
    for condition in group.get("conditions", []):
        if not isinstance(condition, dict):
            continue
        field_table, is_source_channel = source_channel_field_table(condition.get("field"))
        if is_source_channel:
            values: list[str] = []
            for value in condition.get("values", []):
                value_table, source = split_source_channel_value(value)
                qualifier = field_table or value_table
                if qualifier and qualifier != table:
                    continue
                values.append(source)
            normalized_values = sorted(set(source_channels or values))
            if normalized_values:
                conditions.append({**condition, "field": "source_channel", "operator": "in", "values": normalized_values})
        else:
            conditions.append(condition)
    normalized["conditions"] = conditions
    return normalized


def inherit_dependent_select_conditions(group: dict[str, Any], groups: list[dict[str, Any]]) -> dict[str, Any]:
    intent_type = str(group.get("intent_type") or "").upper()
    if intent_type not in {"SELECT_DETAIL", "UPDATE_NUMERIC_VALUE", "SELECT_AGGREGATE", "ADD_DERIVED_COLUMN"}:
        return group
    by_id = {str(candidate.get("group_id") or candidate.get("step_id")): candidate for candidate in groups}
    pending = [str(item) for item in group.get("depends_on", [])]
    if not pending:
        return group
    inherited: list[dict[str, Any]] = []
    visited: set[str] = set()
    while pending:
        current = pending.pop(0)
        if current in visited:
            continue
        visited.add(current)
        candidate = by_id.get(current)
        if not candidate:
            continue
        inherited.extend(condition for condition in candidate.get("conditions", []) if isinstance(condition, dict))
        pending.extend(str(item) for item in candidate.get("depends_on", []))
    if not inherited:
        return group
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for condition in [*inherited, *[item for item in group.get("conditions", []) if isinstance(item, dict)]]:
        key = json.dumps(condition, ensure_ascii=False, sort_keys=True)
        if key in seen:
            continue
        seen.add(key)
        merged.append(condition)
    return {**group, "conditions": merged}


def filtered_ir_for_step(result: dict[str, Any], step_id: str) -> dict[str, Any]:
    ir = json.loads(json.dumps(result.get("ir_structured_json") or result.get("output_json", {}).get("ir_structured_json", {}), ensure_ascii=False))
    modification = dict(ir.get("modification", {}))
    selection = dict(ir.get("selection", {}))
    groups = modification.get("condition_groups", [])
    selected_groups = [group for group in groups if str(group.get("group_id")) == step_id]
    if selected_groups:
        selected = inherit_dependent_select_conditions(dict(selected_groups[0]), groups)
        scoped_table, scoped_channels = source_channel_scope_for_group(result, selected)
        inferred_table = infer_step_table_from_text(result, selected)
        dependency_table = infer_dependency_table(result, selected, groups)
        valid_selection_tables = [str(table) for table in selection.get("tables", []) if str(table) in {"DA", "SA"}]
        existing_table = valid_selection_tables[0] if len(valid_selection_tables) == 1 else None
        selected_table = scoped_table or inferred_table or dependency_table or existing_table
        if scoped_table and scoped_channels and (not existing_table or scoped_table == existing_table):
            selection["tables"] = [scoped_table]
            selection["source_channels"] = scoped_channels
        if selected_table:
            selection = sanitize_step_selection(selection, result.get("source_channel_values", {}), selected_table)
            selected = normalize_step_source_channel_conditions(selected, selected_table, list(selection.get("source_channels", [])))
        step_intent = str(selected.get("intent_type") or ir.get("intent_type") or modification.get("intent_type") or "")
        ir["intent_type"] = step_intent
        modification["intent_type"] = step_intent
        modification["group_by"] = selected.get("group_by", modification.get("group_by", []))
        modification["metrics"] = selected.get("metrics", modification.get("metrics", []))
        modification["derived_column"] = selected.get("derived_column", modification.get("derived_column", {}))
        modification["condition_groups"] = [selected]
    else:
        modification["condition_groups"] = []
    ir["modification"] = modification
    ir["selection"] = selection
    return ir


def ancestor_step_ids(steps: list[dict[str, Any]], step_id: str) -> set[str]:
    by_id = {str(step.get("step_id") or step.get("group_id")): step for step in steps}
    ancestors: set[str] = set()
    pending = [str(item) for item in by_id.get(step_id, {}).get("depends_on", [])]
    while pending:
        current = pending.pop()
        if current in ancestors:
            continue
        ancestors.add(current)
        pending.extend(str(item) for item in by_id.get(current, {}).get("depends_on", []))
    return ancestors


def preview_signature(step_id: str, steps: list[dict[str, Any]], accepted: set[str], cancelled: set[str]) -> str:
    current_order = step_order_for_id(steps, step_id)
    prior_ids = {
        str(step.get("step_id") or step.get("group_id"))
        for step in steps
        if step_order_for_id(steps, str(step.get("step_id") or step.get("group_id"))) < current_order
    }
    payload = {
        "step_id": step_id,
        "accepted_prior_steps": sorted(prior_ids & accepted),
        "cancelled_prior_steps": sorted(prior_ids & cancelled),
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def step_order_for_id(steps: list[dict[str, Any]], step_id: str) -> int:
    for index, step in enumerate(steps, start=1):
        if str(step.get("step_id") or step.get("group_id")) == step_id:
            return index
    return 0


def preview_delta_items_from_result(result: dict[str, Any], status: str = "approved") -> list[dict[str, Any]]:
    items = result.get("preview_delta_items") or result.get("change_preview_json", {}).get("preview_delta_items", [])
    if not isinstance(items, list):
        return []
    return [{**item, "status": status} for item in items if isinstance(item, dict) and item.get("source_row_id") not in {None, ""}]


def effective_preview_context_for_step(
    steps: list[dict[str, Any]],
    step_id: str,
    accepted: set[str],
    previews: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    current_order = step_order_for_id(steps, step_id)
    delta_items: list[dict[str, Any]] = []
    for step in steps:
        prior_id = str(step.get("step_id") or step.get("group_id"))
        if prior_id not in accepted or step_order_for_id(steps, prior_id) >= current_order:
            continue
        delta_items.extend(preview_delta_items_from_result(previews.get(prior_id, {}), "approved"))
    return {"active_step_order": current_order, "delta_items": delta_items}


def later_step_ids(steps: list[dict[str, Any]], step_id: str) -> set[str]:
    current_order = step_order_for_id(steps, step_id)
    return {
        str(step.get("step_id") or step.get("group_id"))
        for step in steps
        if step_order_for_id(steps, str(step.get("step_id") or step.get("group_id"))) > current_order
    }


def preview_fingerprint_from_result(result: dict[str, Any]) -> str | None:
    preview = result.get("change_preview_json") or result.get("output_json", {}).get("change_preview_json", {})
    if isinstance(preview, dict):
        value = preview.get("preview_fingerprint")
        return str(value) if value else None
    return None


def preview_changed(old_fingerprint: str | None, new_result: dict[str, Any] | None) -> bool | None:
    if not old_fingerprint or not new_result:
        return None
    new_fingerprint = preview_fingerprint_from_result(new_result)
    if not new_fingerprint:
        return None
    return old_fingerprint != new_fingerprint


def preview_can_be_approved(result: dict[str, Any] | None) -> bool:
    if not result:
        return False
    validation_result = result.get("validation_result", {})
    change_preview_json = result.get("change_preview_json", {})
    return (
        validation_result.get("status") == "passed"
        and change_preview_json.get("status") == "pending_user_confirmation"
        and bool(change_preview_json.get("sql_fingerprint"))
        and bool(change_preview_json.get("preview_fingerprint"))
    )


def step_is_available(step: dict[str, Any], accepted_step_ids: set[str], cancelled_step_ids: set[str]) -> bool:
    step_id = str(step.get("step_id") or step.get("group_id"))
    if step_id in cancelled_step_ids:
        return False
    return all(str(dep) in accepted_step_ids for dep in step.get("depends_on", []))


def show_recommendations(result: dict[str, Any]) -> None:
    recommendations = result.get("query_recommendations") or result.get("output_json", {}).get("query_recommendations", [])
    if not recommendations:
        return
    st.subheader("추천 조건")
    st.caption("원하는 결과가 아니면 아래 추천 조건으로 다시 확인할 수 있습니다. 추천은 자동 적용되지 않습니다.")
    last_input = st.session_state.get("last_valid_input", {})
    for index, item in enumerate(recommendations[:5]):
        st.info(item.get("recommendation_text") or f"{item.get('original_term')} -> {item.get('recommended_term')}")
        if st.button("추천 조건으로 다시 확인", key=f"recommendation_preview_{index}"):
            updated_selection = last_input.get("selection_text", "")
            updated_modification = last_input.get("modification_text", "")
            if item.get("input_origin") == "selection":
                updated_selection = recommendation_rerun_text(updated_selection, item)
            else:
                updated_modification = recommendation_rerun_text(updated_modification, item)
            st.session_state.pipeline_result = run_graph(
                selection_text=updated_selection,
                modification_text=updated_modification,
                stored_rules=last_input.get("stored_rules", []),
                approved=False,
            )
            st.session_state.last_valid_input = {**last_input, "selection_text": updated_selection, "modification_text": updated_modification}
            st.session_state.accepted_step_ids = []
            st.session_state.cancelled_step_ids = []
            st.session_state.step_preview_results = {}
            st.rerun()


def recommendation_rerun_text(modification_text: str, recommendation: dict[str, Any]) -> str:
    original = str(recommendation.get("original_term") or "").strip()
    recommended = str(recommendation.get("recommended_term") or "").strip()
    if original and recommended and original in modification_text:
        return modification_text.replace(original, recommended)
    if recommended:
        return "\n".join(part for part in [modification_text, f"추천 조건 확인: {recommended}"] if part)
    return modification_text


def show_llm_admin_controls() -> None:
    if not env_flag("SQL_WORKFLOW_ENABLE_LLM_ADMIN", False):
        return
    provider_options = {
        "openai_compatible": "llama.cpp / OpenAI 호환",
        "gemini_native": "Gemini 기본 API",
        "gemini_openai_compatible": "Gemini OpenAI 호환",
    }
    env_provider = os.environ.get("SQL_WORKFLOW_LLM_PROVIDER", "openai_compatible")
    default_provider = env_provider if env_provider in provider_options else "openai_compatible"
    with st.sidebar.expander("관리자 설정", expanded=False):
        provider = st.selectbox(
            "LLM provider",
            options=list(provider_options.keys()),
            format_func=lambda value: provider_options[value],
            index=list(provider_options.keys()).index(st.session_state.get("llm_provider", default_provider)),
            key="llm_provider",
        )
        default_model = os.environ.get("SQL_WORKFLOW_GEMINI_MODEL", DEFAULT_GEMINI_MODEL) if provider.startswith("gemini") else os.environ.get("SQL_WORKFLOW_LLM_MODEL", DEFAULT_LLM_MODEL)
        current_model = st.session_state.get("llm_model") or default_model
        if provider.startswith("gemini") and current_model == DEFAULT_LLM_MODEL:
            current_model = default_model
        st.text_input("Model", value=str(current_model), key="llm_model")
        st.text_input("API key", value="", type="password", key="llm_api_key", placeholder="환경 변수 사용 시 비워두세요")
        st.number_input("Temperature", min_value=0.0, max_value=2.0, value=float(st.session_state.get("llm_temperature") or os.environ.get("SQL_WORKFLOW_LLM_TEMPERATURE", "0")), step=0.1, key="llm_temperature")
        st.number_input("Max tokens", min_value=128, max_value=32768, value=int(st.session_state.get("llm_max_tokens") or os.environ.get("SQL_WORKFLOW_LLM_MAX_TOKENS", "2048")), step=128, key="llm_max_tokens")
        st.number_input("Timeout seconds", min_value=10, max_value=600, value=int(float(st.session_state.get("llm_timeout") or os.environ.get("SQL_WORKFLOW_LLM_TIMEOUT", "180"))), step=10, key="llm_timeout")
        st.caption("설정은 현재 화면 세션에서만 사용됩니다. 키 값은 파일에 저장하지 않습니다.")


def show_step_approval_controls(result: dict[str, Any]) -> None:
    steps = result.get("workflow_steps") or result.get("output_json", {}).get("workflow_steps", [])
    if len(steps) <= 1:
        return

    st.subheader("연결된 요청별 결과 확인")
    st.caption("각 요청은 샘플 데이터를 먼저 보여준 뒤 승인 버튼을 눌러야 적용됩니다. 앞 요청을 취소하면 그 결과에 이어진 요청도 함께 다시 확인해야 합니다.")

    accepted_key = "accepted_step_ids"
    cancelled_key = "cancelled_step_ids"
    preview_key = "step_preview_results"
    signature_key = "step_preview_signatures"
    stale_key = "step_invalidated_previews"
    st.session_state.setdefault(accepted_key, [])
    st.session_state.setdefault(cancelled_key, [])
    st.session_state.setdefault(preview_key, {})
    st.session_state.setdefault(signature_key, {})
    st.session_state.setdefault(stale_key, {})
    accepted = set(st.session_state[accepted_key])
    cancelled = set(st.session_state[cancelled_key])
    previews: dict[str, dict[str, Any]] = st.session_state[preview_key]
    signatures: dict[str, str] = st.session_state[signature_key]
    stale_previews: dict[str, dict[str, Any]] = st.session_state[stale_key]
    last_input = st.session_state.get("last_valid_input", {})

    for index, step in enumerate(steps, start=1):
        step_id = str(step.get("step_id") or step.get("group_id"))
        available = step_is_available(step, accepted, cancelled)
        if step_id in cancelled:
            status_label = "취소됨"
        elif step_id in accepted:
            status_label = "승인됨"
        elif available:
            status_label = "확인 가능"
        else:
            status_label = "앞 요청 대기"
        with st.expander(f"요청 {index} · {status_label}", expanded=step_id not in accepted and step_id not in cancelled):
            related_ids = sorted(step_ids_to_invalidate(steps, step_id) - {step_id})
            if step.get("depends_on"):
                st.caption(f"이 요청은 앞 요청 {', '.join(str(item) for item in step.get('depends_on', []))} 결과를 바탕으로 계산됩니다.")
            if related_ids and step_id not in cancelled:
                st.warning(f"이 요청을 취소하면 이어진 요청 {', '.join(related_ids)}도 함께 다시 확인해야 합니다.")
            if step_id in cancelled:
                st.error("이 요청은 취소되어 결과 목록에서 제외되었습니다. 이어진 요청도 함께 제외됩니다.")
            if step_id in accepted:
                st.caption("이미 승인된 요청은 이 화면에서 취소하지 않습니다. 변경이 필요하면 새로 확인을 시작하세요.")
            if step_id in stale_previews:
                stale = stale_previews[step_id]
                st.info("이전에 확인한 결과가 보관되어 있습니다. 앞 요청 상태가 바뀌면 다시 확인한 결과와 달라질 수 있습니다.")
                changed = preview_changed(stale.get("preview_fingerprint"), previews.get(step_id))
                if changed is True:
                    st.warning("다시 확인한 결과가 이전과 달라졌습니다.")
                elif changed is False:
                    st.caption("다시 확인한 결과가 이전과 같습니다.")
            st.json({"depends_on": step.get("depends_on", []), "conditions": step.get("conditions", []), "actions": step.get("actions", [])})
            available = step_is_available(step, accepted, cancelled)
            current_signature = preview_signature(step_id, steps, accepted, cancelled)
            preview_ready = step_id in previews and signatures.get(step_id) == current_signature and preview_can_be_approved(previews.get(step_id))
            if not available and step_id not in cancelled:
                st.warning("앞 요청이 아직 승인되지 않았거나 취소되어 이 결과는 다시 확인할 수 없습니다.")
            cols = st.columns(3)
            if cols[0].button("적용 전 결과 확인", key=f"preview_{step_id}", disabled=not available):
                previews[step_id] = run_graph(
                    selection_text=last_input.get("selection_text", ""),
                    modification_text=last_input.get("modification_text", ""),
                    stored_rules=last_input.get("stored_rules", []),
                    approved=False,
                    ir_override=filtered_ir_for_step(result, step_id),
                    active_step_id=step_id,
                    effective_preview_context=effective_preview_context_for_step(steps, step_id, accepted, previews),
                )
                signatures[step_id] = current_signature
                st.session_state[preview_key] = previews
                st.session_state[signature_key] = signatures
                st.rerun()
            if cols[1].button("이 요청 승인", key=f"approve_{step_id}", disabled=not preview_ready or not available):
                previews[step_id] = apply_existing_preview_result(previews[step_id], True, execute=False)
                accepted.add(step_id)
                cancelled.discard(step_id)
                for invalid_step in later_step_ids(steps, step_id):
                    if invalid_step in previews:
                        stale_previews[invalid_step] = {
                            "preview_fingerprint": preview_fingerprint_from_result(previews[invalid_step]),
                            "reason": "upstream_delta_changed",
                        }
                    previews.pop(invalid_step, None)
                    signatures.pop(invalid_step, None)
                st.session_state[accepted_key] = sorted(accepted)
                st.session_state[cancelled_key] = sorted(cancelled)
                st.session_state[preview_key] = previews
                st.session_state[signature_key] = signatures
                st.session_state[stale_key] = stale_previews
                st.rerun()
            if cols[2].button("이 요청 취소", key=f"cancel_{step_id}", disabled=step_id in accepted):
                invalidated = {step_id, *later_step_ids(steps, step_id)}
                cancelled.update(invalidated)
                accepted.difference_update(invalidated)
                for invalid_step in invalidated:
                    if invalid_step in previews:
                        stale_previews[invalid_step] = {
                            "preview_fingerprint": preview_fingerprint_from_result(previews[invalid_step]),
                            "reason": "cancelled_linked_request",
                        }
                    signatures.pop(invalid_step, None)
                st.session_state[accepted_key] = sorted(accepted)
                st.session_state[cancelled_key] = sorted(cancelled)
                st.session_state[preview_key] = previews
                st.session_state[signature_key] = signatures
                st.session_state[stale_key] = stale_previews
                st.rerun()
            if step_id in previews:
                show_final_result(previews[step_id])

    available_step_ids = [str(step.get("step_id") or step.get("group_id")) for step in steps if step_is_available(step, accepted, cancelled)]
    missing_preview_ids = [
        step_id
        for step_id in available_step_ids
        if step_id not in previews
        or signatures.get(step_id) != preview_signature(step_id, steps, accepted, cancelled)
        or not preview_can_be_approved(previews.get(step_id))
    ]
    preview_all_col, approve_all_col = st.columns(2)
    if preview_all_col.button("확인 가능한 요청 모두 보기", key="preview_all_steps", disabled=not missing_preview_ids):
        for step in steps:
            step_id = str(step.get("step_id") or step.get("group_id"))
            if step_id not in missing_preview_ids:
                continue
            previews[step_id] = run_graph(
                selection_text=last_input.get("selection_text", ""),
                modification_text=last_input.get("modification_text", ""),
                stored_rules=last_input.get("stored_rules", []),
                approved=False,
                ir_override=filtered_ir_for_step(result, step_id),
                active_step_id=step_id,
                effective_preview_context=effective_preview_context_for_step(steps, step_id, accepted, previews),
            )
            signatures[step_id] = preview_signature(step_id, steps, accepted, cancelled)
        st.session_state[preview_key] = previews
        st.session_state[signature_key] = signatures
        st.rerun()
    approve_all_col.button("확인된 요청 모두 승인", key="approve_all_steps", disabled=True)
    if missing_preview_ids:
        st.caption("전체 승인은 화면에 표시된 결과만 대상으로 합니다. 먼저 확인 가능한 요청을 모두 살펴보세요.")
    else:
        st.caption("연결된 요청은 앞 요청 승인 후 후속 결과가 바뀔 수 있으므로 요청별로 순서대로 승인하세요.")


def main() -> None:
    st.title("광고 데이터 적용 전 확인")
    st.caption("사용자가 입력한 마케팅 요청을 해석하고, 원본을 바꾸기 전에 샘플 데이터로 먼저 확인하는 화면입니다.")
    show_llm_admin_controls()

    with st.form("pipeline_input_form"):
        selection_text = st.text_area("어떤 광고 데이터를 볼까요?", value="", placeholder=SELECTION_PLACEHOLDER, key="selection_text")
        modification_text = st.text_area("무엇을 바꾸거나 계산할까요?", value="", placeholder=MODIFICATION_PLACEHOLDER, key="modification_text")
        stored_rules_json = st.text_area("저장된 규칙 입력 (개발용)", value="", placeholder=STORED_RULES_PLACEHOLDER, key="stored_rules_json")
        submitted = st.form_submit_button("적용 전 결과 확인")

    if submitted:
        try:
            stored_rules = parse_stored_rules(stored_rules_json)
            st.session_state.pipeline_result = run_graph(
                selection_text=selection_text,
                modification_text=modification_text,
                stored_rules=stored_rules,
                approved=False,
            )
            st.session_state.last_valid_input = {
                "selection_text": selection_text,
                "modification_text": modification_text,
                "stored_rules": stored_rules,
            }
            st.session_state.accepted_step_ids = []
            st.session_state.cancelled_step_ids = []
            st.session_state.step_preview_results = {}
            st.session_state.step_preview_signatures = {}
            st.session_state.step_invalidated_previews = {}
        except Exception as exc:
            LOGGER.exception("Streamlit workflow failed")
            st.error("요청을 확인하는 중 문제가 발생했습니다. 관리자에게 로그 확인을 요청하세요.")

    result = st.session_state.get("pipeline_result")
    if not result:
        st.info("적용 전 결과 확인 버튼을 누르면 요청 해석 결과, 실제 적용 전 명령, 샘플 데이터를 확인할 수 있습니다.")
        return

    if len(result.get("workflow_steps") or result.get("output_json", {}).get("workflow_steps", [])) > 1:
        st.subheader("1. 요청 해석 결과")
        st.json(result.get("output_json", {}).get("ir_structured_json", result.get("ir_structured_json", {})))
        show_recommendations(result)
        show_step_approval_controls(result)
        return

    show_final_result(result)
    show_recommendations(result)

    validation_passed = result.get("validation_result", {}).get("status") == "passed"
    st.subheader("최종 확인")
    if not validation_passed:
        st.warning("안전 검사가 통과하지 않아 적용할 수 없습니다.")
        return

    col_approve, col_reject = st.columns(2)
    with col_approve:
        approve_clicked = st.button("이대로 승인")
    with col_reject:
        reject_clicked = st.button("적용하지 않음")

    if approve_clicked or reject_clicked:
        st.session_state.pipeline_result = apply_existing_preview_result(st.session_state.pipeline_result, approve_clicked)
        st.rerun()


if __name__ == "__main__":
    main()
