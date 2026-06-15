from __future__ import annotations

# pyright: reportTypedDictNotRequiredAccess=false

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.langgraph_workflow.db import build_schema_summary
from app.langgraph_workflow.state import ModificationWorkflowState


LOG_DIR = Path(__file__).resolve().parents[2] / "logs"
LLM_REQUEST_LOG = LOG_DIR / "llm_requests.jsonl"


AGGREGATE_INTENT = "SELECT_AGGREGATE"
UPDATE_INTENT = "UPDATE_NUMERIC_VALUE"
ADD_DERIVED_COLUMN_INTENT = "ADD_DERIVED_COLUMN"
ASK_CLARIFICATION_INTENT = "ASK_CLARIFICATION"


def compact_text(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).lower()


def log_llm_request(stage: str, prompt: str, response: Any = None, error: Exception | None = None) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    content = response.content if response is not None and hasattr(response, "content") else None
    payload = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "stage": stage,
        "prompt_chars": len(prompt),
        "response_content_chars": len(content or ""),
        "error": str(error) if error else None,
    }
    with LLM_REQUEST_LOG.open("a", encoding="utf-8") as file:
        file.write(json.dumps(payload, ensure_ascii=False) + "\n")


def invoke_llm_json(llm: Any, prompt: str, stage: str) -> dict[str, Any]:
    try:
        response = llm.invoke(prompt)
        log_llm_request(stage, prompt, response=response)
        return parse_json_response(response)
    except Exception as exc:
        log_llm_request(stage, prompt, error=exc)
        raise


def build_ir_prompt(selection_text: str, modification_text: str, schema_summary: str) -> str:
    return f"""
/no_think
너는 자연어 조회 조건과 수정 조건을 하나의 IR JSON으로 구조화한다.
추론 과정, 설명, markdown 없이 JSON 객체만 출력한다.
현재 고객사는 환경별 기본 고객사만 있다.
schema에 없는 table, column, source_channel을 임의로 만들지 않는다.
이 단계에서는 SQL을 만들지 않는다.
사용자는 컬럼명을 모른다. 업무 용어는 live schema와 아래 규칙으로 매핑하되, 확신이 없으면 unresolved_terms에 남긴다.

의도 분류 규칙:
- 합계, 평균, 전환율, 전환당 비용, 비교, 계산은 intent_type="SELECT_AGGREGATE"다.
- 0으로, 보정, 채워, 바꿔, 고정은 intent_type="UPDATE_NUMERIC_VALUE"다.
- 새 컬럼, 새 구분, 새 항목, 만들어, 기입은 intent_type="ADD_DERIVED_COLUMN"다.
- source_channel, 날짜, 원본 식별 컬럼은 수정 대상으로 선택하지 않는다.
- A나 B, 키워드나 소재명은 OR 조건으로 표현한다.
- 비어 있는, 없는, 빈값은 is_null_or_empty 조건으로 표현한다.
- 노출, 클릭, 광고비를 모두 0으로 바꾸는 요청은 actions 배열에 3개 action을 넣는다.

용어 매핑은 실시간 DB schema 요약의 business source_channel mappings, business column alias mappings,
business metric definitions만 사용한다. 코드에 없는 표현을 추측하지 말고 unresolved_terms에 남긴다.

실시간 DB schema 요약:
{schema_summary}

사용자 조회 조건:
{selection_text}

사용자 수정 조건:
{modification_text}

반환 형식:
{{
  "intent_type": "UPDATE_NUMERIC_VALUE" 또는 "ADD_DERIVED_COLUMN" 또는 "SELECT_AGGREGATE" 또는 "ASK_CLARIFICATION",
  "selection": {{"customer": "default", "period": object, "source_channels": list, "tables": ["DA" 또는 "SA"], "unresolved_terms": list}},
  "modification": {{
    "condition_groups": [
      {{
        "group_id": string,
        "dependency": "independent",
        "depends_on": [],
        "conditions": [{{"field": string, "operator": "eq/in/contains/is_null_or_empty/gt/gte/lt/lte/between/or", "values": list, "conditions": list}}],
        "actions": [{{"target_field": string, "operation": "set_literal", "value": string}}]
      }}
    ],
    "group_by": [{{"field_alias": string, "resolved_column": string}}],
    "metrics": [{{"alias": string, "expression_type": "sum/avg/conversion_rate/cost_per_conversion/ctr", "source_column": string}}],
    "derived_column": object
  }}
}}
""".strip()


def normalize_condition(condition: dict[str, Any]) -> dict[str, Any]:
    field = condition.get("field") or condition.get("column")
    operator = str(condition.get("operator", "eq")).lower()
    if operator in {"or", "and", "or_group", "and_group"}:
        nested = condition.get("conditions", [])
        if not isinstance(nested, list):
            raise ValueError("SQL_WORKFLOW_LLM_IR_NESTED_CONDITIONS_MUST_BE_LIST")
        return {"field": field or "__group__", "operator": "or" if operator == "or_group" else "and" if operator == "and_group" else operator, "values": [], "conditions": [normalize_condition(item) for item in nested if isinstance(item, dict)]}
    if not field:
        raise ValueError("SQL_WORKFLOW_LLM_IR_CONDITION_FIELD_MISSING")
    if operator in {"like", "contains"}:
        operator = "contains"
    if operator in {"empty", "missing", "is_empty"}:
        operator = "is_null_or_empty"
    raw_values = condition.get("values")
    if raw_values is None and "value" in condition:
        raw_values = [condition["value"]]
    if isinstance(raw_values, str):
        raw_values = [raw_values]
    values = list(raw_values or [])
    if operator == "=":
        operator = "eq"
    if operator == "contains":
        values = [str(value).strip("%") for value in values]
    return {"field": field, "operator": operator, "values": values}


def normalize_actions(raw_actions: Any) -> list[dict[str, Any]]:
    if raw_actions is None:
        return []
    if isinstance(raw_actions, dict):
        raw_actions = [raw_actions]
    if not isinstance(raw_actions, list):
        raise ValueError("SQL_WORKFLOW_LLM_IR_ACTIONS_MUST_BE_LIST")
    actions: list[dict[str, Any]] = []
    for action in raw_actions:
        if isinstance(action, list):
            actions.extend(normalize_actions(action))
            continue
        if not isinstance(action, dict):
            raise ValueError("SQL_WORKFLOW_LLM_IR_ACTION_MUST_BE_OBJECT")
        if "target_column" in action and "target_field" not in action:
            action = {**action, "target_field": action["target_column"]}
        if "action_type" in action and "operation" not in action:
            action = {**action, "operation": "set_literal" if action.get("action_type") == "set_value" else action["action_type"]}
        actions.append(action)
    return actions


def classify_intent(selection_text: str, modification_text: str) -> str:
    text = f"{selection_text} {modification_text}".lower()
    if any(keyword in text for keyword in ["합계", "평균", "전환율", "전환당", "비교", "계산", "ctr", "cpc", "cpa"]):
        return AGGREGATE_INTENT
    if any(keyword in text for keyword in ["새 컬럼", "새 구분", "새 항목", "만들", "기입"]):
        return ADD_DERIVED_COLUMN_INTENT
    if any(keyword in text for keyword in ["0으로", "보정", "채워", "바꿔", "고정"]):
        return UPDATE_INTENT
    return ASK_CLARIFICATION_INTENT


def normalize_ir_structured_json(parsed: dict[str, Any]) -> dict[str, Any]:
    if "selection" not in parsed or "modification" not in parsed:
        raise ValueError("SQL_WORKFLOW_LLM_IR_PARSE_INVALID_SHAPE")
    selection = dict(parsed["selection"])
    period = selection.get("period")
    if not isinstance(period, dict) or not {"field", "start", "end"}.issubset(period):
        selection["period"] = {}
    intent_type = str(parsed.get("intent_type") or parsed.get("modification", {}).get("intent_type") or "").upper()
    condition_groups: list[dict[str, Any]] = []
    for index, group in enumerate(parsed["modification"].get("condition_groups", [])):
        actions = normalize_actions(group.get("actions", []))
        condition_groups.append(
            {
                "group_id": group.get("group_id", f"group_{index + 1}"),
                "dependency": group.get("dependency", "independent"),
                "depends_on": group.get("depends_on", []),
                "conditions": [normalize_condition(condition) for condition in group.get("conditions", [])],
                "actions": actions,
            }
        )
    modification = dict(parsed["modification"])
    modification["condition_groups"] = condition_groups
    return {"intent_type": intent_type, "selection": selection, "modification": modification}


def match_media_source_channels(
    text: str,
    source_channel_values: dict[str, list[str]],
    source_channel_mappings: list[dict[str, Any]],
) -> tuple[str | None, list[str]]:
    compact = text.lower()
    compact_no_space = re.sub(r"\s+", "", compact)
    grouped: dict[tuple[str, str], list[str]] = {}
    for item in source_channel_mappings:
        user_term = str(item.get("user_term", ""))
        table_name = str(item.get("target_table", ""))
        source_channel = str(item.get("source_channel", ""))
        if not user_term or not table_name or not source_channel:
            continue
        grouped.setdefault((user_term, table_name), []).append(source_channel)
    matched_by_table: dict[str, list[str]] = {}
    for (user_term, table_name), candidates in grouped.items():
        user_term_lower = user_term.lower()
        term_tokens = [token for token in re.split(r"\s+", user_term_lower) if token]
        token_match = bool(term_tokens) and all(token in compact_no_space for token in term_tokens)
        if user_term_lower not in compact and not token_match:
            continue
        live = set(source_channel_values.get(table_name, []))
        matched = [candidate for candidate in candidates if candidate in live]
        if matched:
            matched_by_table.setdefault(table_name, []).extend(matched)
    matched_by_table = {table: sorted(set(values)) for table, values in matched_by_table.items() if values}
    if len(matched_by_table) == 1:
        table_name = next(iter(matched_by_table))
        return table_name, matched_by_table[table_name]
    if len(matched_by_table) > 1:
        return None, []
    return None, []


def normalize_source_channels_from_text(
    selection: dict[str, Any],
    selection_text: str,
    source_channel_values: dict[str, list[str]],
    source_channel_mappings: list[dict[str, Any]],
) -> dict[str, Any]:
    table_name = (selection.get("tables") or ["SA"])[0]
    mapped_table, mapped_channels = match_media_source_channels(selection_text, source_channel_values, source_channel_mappings)
    if mapped_table and mapped_channels:
        return {**selection, "tables": [mapped_table], "source_channels": mapped_channels}
    compact = selection_text.lower()
    selected_table_matches: list[str] = []
    live_values = set(source_channel_values.get(table_name, []))
    for item in source_channel_mappings:
        if str(item.get("target_table", "")) != table_name:
            continue
        user_term = str(item.get("user_term", ""))
        source_channel = str(item.get("source_channel", ""))
        if user_term and user_term.lower() in compact and source_channel in live_values:
            selected_table_matches.append(source_channel)
    if selected_table_matches:
        return {**selection, "tables": [table_name], "source_channels": sorted(set(selected_table_matches))}
    selected = [value for value in selection.get("source_channels", []) if value in live_values]
    return {**selection, "source_channels": selected}


def remove_resolved_modification_terms(selection: dict[str, Any], modification: dict[str, Any]) -> dict[str, Any]:
    unresolved_terms = [str(term) for term in selection.get("unresolved_terms", [])]
    if not unresolved_terms:
        return selection
    condition_values = {
        compact_text(value)
        for group in modification.get("condition_groups", [])
        for condition in group.get("conditions", [])
        if isinstance(condition, dict)
        for value in condition.get("values", [])
    }
    remaining = [term for term in unresolved_terms if compact_text(term) not in condition_values]
    return {**selection, "unresolved_terms": remaining}


def build_selection_prompt(selection_text: str, schema_summary: str) -> str:
    return f"""
/no_think
너는 RDB raw 데이터 조회 조건을 구조화한다.
추론 과정, 설명, markdown 없이 JSON 객체만 출력한다.
사용자 표현이 부정확하면 아래 실시간 DB schema와 source_channel 후보를 기준으로 판단한다.
현재 고객사는 환경별 기본 고객사만 있다.
schema에 없는 table, column, source_channel을 임의로 만들지 않는다.

실시간 DB schema 요약:
{schema_summary}

사용자 조회 조건:
{selection_text}

반환 형식은 JSON만 사용한다.
필드: customer, period, source_channels, tables, unresolved_terms
""".strip()


def build_modification_prompt(modification_text: str, schema_summary: str) -> str:
    return f"""
/no_think
너는 조건부 값 수정 요구사항을 논리 구조로 변환한다.
추론 과정, 설명, markdown 없이 JSON 객체만 출력한다.
조건 리스트를 분리하고, 서로 독립인지 앞 조건 결과에 종속되는지 표시한다.
아래 실시간 DB schema에 존재하는 table/column만 사용한다.
import 스크립트의 canonicalization 규칙 외 alias를 임의로 만들지 않는다.
이 단계에서는 SQL을 만들지 말고 condition_groups와 actions만 만든다.

실시간 DB schema 요약:
{schema_summary}

사용자 수정 요구사항:
{modification_text}

반환 형식은 JSON만 사용한다.
필드: condition_groups[group_id, dependency, depends_on, conditions, actions]
""".strip()


def parse_json_response(response: Any) -> dict[str, Any]:
    content = response.content if hasattr(response, "content") else str(response)
    content = content.strip()
    if content.startswith("```"):
        content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content, flags=re.DOTALL).strip()
    return json.loads(content)


def metric_field_from_text(text: str) -> str:
    if "가입 완료" in text:
        return "주요 이벤트"
    if "광고비" in text or "비용" in text:
        return "비용"
    if "클릭" in text:
        return "클릭수"
    if "노출" in text:
        return "노출수"
    if "세션" in text:
        return "세션수"
    return ""


def deterministic_update_ir(selection_text: str, modification_text: str) -> dict[str, Any]:
    target_field = metric_field_from_text(modification_text)
    if not target_field:
        raise ValueError("SQL_WORKFLOW_LLM_IR_PARSE_REQUIRED")
    conditions: list[dict[str, Any]] = []
    if "노출 수는 있는데" in modification_text or "노출은 있는데" in modification_text:
        conditions.append({"field": "노출수", "operator": "is_not_null_or_empty", "values": []})
    if ("비어" in modification_text or "없는" in modification_text) and target_field:
        conditions.append({"field": target_field, "operator": "is_null_or_empty", "values": []})
    campaign_contains = re.search(r"캠페인\s*이름에\s*`?([^`\s]+)`?", modification_text)
    if campaign_contains:
        conditions.append({"field": "캠페인", "operator": "contains", "values": [campaign_contains.group(1)]})
    if "음수" in modification_text:
        conditions.append({"field": target_field, "operator": "lt", "values": [0]})
    return {
        "intent_type": UPDATE_INTENT,
        "selection": {"customer": "default", "period": {}, "source_channels": [], "tables": ["DA"], "unresolved_terms": []},
        "modification": {
            "condition_groups": [
                {
                    "group_id": "deterministic_fallback_1",
                    "dependency": "independent",
                    "depends_on": [],
                    "conditions": conditions,
                    "actions": [{"target_field": target_field, "operation": "set_literal", "value": "0"}],
                }
            ],
            "group_by": [],
            "metrics": [],
            "derived_column": {},
        },
    }


def parse_ir_with_llm(llm: Any, selection_text: str, modification_text: str, schema_summary: str) -> dict[str, Any]:
    if llm is None:
        raise RuntimeError("SQL_WORKFLOW_LLM_IR_PARSE_REQUIRED")
    try:
        parsed = invoke_llm_json(llm, build_ir_prompt(selection_text, modification_text, schema_summary), "ir_structuring")
    except json.JSONDecodeError:
        parsed = deterministic_update_ir(selection_text, modification_text)
    normalized = normalize_ir_structured_json(parsed)
    if not normalized.get("intent_type"):
        normalized["intent_type"] = classify_intent(selection_text, modification_text)
    return normalized


def parse_ir_request_node(state: ModificationWorkflowState, llm: Any = None) -> dict[str, Any]:
    ir_structured_json = parse_ir_with_llm(
        llm=llm,
        selection_text=state["selection_text"],
        modification_text=state["modification_text"],
        schema_summary=state.get("schema_summary", build_schema_summary(state.get("table_columns", {}), state.get("source_channel_values", {}))),
    )
    ir_structured_json["selection"] = normalize_source_channels_from_text(
        selection=ir_structured_json["selection"],
        selection_text=f"{state['selection_text']} {state['modification_text']}",
        source_channel_values=state.get("source_channel_values", {}),
        source_channel_mappings=state.get("source_channel_mappings", []),
    )
    ir_structured_json["selection"] = remove_resolved_modification_terms(ir_structured_json["selection"], ir_structured_json["modification"])
    ir_structured_json["selection"]["intent_type"] = ir_structured_json.get("intent_type")
    ir_structured_json["modification"]["intent_type"] = ir_structured_json.get("intent_type")
    return {
        "selection_request": ir_structured_json["selection"],
        "modification_logic": ir_structured_json["modification"],
        "ir_structured_json": ir_structured_json,
    }
