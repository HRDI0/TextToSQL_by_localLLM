from __future__ import annotations

# pyright: reportTypedDictNotRequiredAccess=false

import json
import re
import calendar
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.langgraph_workflow.db import build_schema_summary
from app.langgraph_workflow.state import ModificationWorkflowState


LOG_DIR = Path(__file__).resolve().parents[2] / "logs"
LLM_REQUEST_LOG = LOG_DIR / "llm_requests.jsonl"


AGGREGATE_INTENT = "SELECT_AGGREGATE"
SELECT_DETAIL_INTENT = "SELECT_DETAIL"
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
너는 자연어 조회 조건과 수정/계산 조건을 실행 가능한 범용 IR JSON으로 구조화한다.
추론 과정, 설명, markdown 없이 JSON 객체만 출력한다.
현재 고객사는 환경별 기본 고객사만 있다.
schema에 없는 table, column, source_channel을 임의로 만들지 않는다.
이 단계에서는 SQL을 만들지 않는다.
사용자는 컬럼명을 모른다. 업무 용어는 live schema와 아래 규칙으로 매핑하되, 확신이 없으면 unresolved_terms에 남긴다.

의도 분류 규칙:
- 보고 싶어, 조회, 확인, 목록, 샘플처럼 원본 데이터를 보는 요청은 intent_type="SELECT_DETAIL"다.
- 합계, 평균, 전환율, 전환당 비용, 클릭률, 비교, 계산처럼 지표 계산을 명시한 경우만 intent_type="SELECT_AGGREGATE"다.
- 0으로, 보정, 채워, 바꿔, 고정은 intent_type="UPDATE_NUMERIC_VALUE"다.
- 새 컬럼, 새 구분, 새 항목, 만들어, 기입, 붙여줘, 분류값, 상태값, 임시 상태값은 intent_type="ADD_DERIVED_COLUMN"다.
- "비용 있음 분류값", "노출 상태 값", "임시 상태값", "값으로 분류"처럼 사용자가 새 label/value를 붙이는 요청은 실제 테이블 numeric UPDATE가 아니라 ADD_DERIVED_COLUMN이다.
- source_channel, 날짜, 원본 식별 컬럼은 수정 대상으로 선택하지 않는다.
- A나 B, 키워드나 소재명은 OR 조건으로 표현한다.
- 숫자 지표(노출, 클릭, 비용, 세션수)가 있는/나온/잡힌/들어간 데이터는 gt 0 조건으로 표현한다.
- 숫자 지표(노출, 클릭, 비용, 세션수)가 없는 데이터는 eq 0 조건으로 표현한다.
- 비어 있는, 빈값, 공란, 누락, null, none은 is_null_or_empty 조건으로 표현한다.
- 노출, 클릭, 광고비를 모두 0으로 바꾸는 요청은 actions 배열에 3개 action을 넣는다.

다중 step 구조화 규칙:
- 서로 독립적으로 적용 가능한 자연어 줄/문장은 condition_groups의 독립 group으로 분리한다.
- 앞 group의 수정 결과를 뒤 group의 조건/계산이 참조하면 뒤 group은 dependency="dependent"로 표시하고 depends_on에 선행 group_id를 넣는다.
- 예: 먼저 값을 수정한 뒤 그 수정 결과의 합계/평균을 날짜별로 계산하는 요청은 선행 UPDATE group과 후행 SELECT_AGGREGATE 성격 group으로 분리한다.
- group_id는 안정적인 문자열(step_1, step_2 등)로 작성한다.
- 독립 group은 서로의 sample preview에 영향을 주지 않아야 하며, dependent group은 depends_on step이 취소되면 재검토가 필요하다.
- 각 group의 conditions와 actions는 해당 group에만 속한 조건/동작만 포함한다. 다른 group 조건을 섞지 않는다.
- sample row나 적용 결과를 상상해서 만들지 않는다. 적용 예시는 후속 Python code가 DB row와 action metadata로 계산한다.

용어 매핑은 실시간 DB schema 요약의 table columns, source_channel candidates, business source_channel mappings,
business column alias mappings, business metric definitions만 사용한다. 코드에 없는 표현을 추측하지 말고 unresolved_terms에 남긴다.

실시간 DB schema 요약:
{schema_summary}

사용자 조회 조건:
{selection_text}

사용자 수정 조건:
{modification_text}

반환 형식:
{{
  "intent_type": "SELECT_DETAIL" 또는 "UPDATE_NUMERIC_VALUE" 또는 "ADD_DERIVED_COLUMN" 또는 "SELECT_AGGREGATE" 또는 "ASK_CLARIFICATION",
  "selection": {{"customer": "default", "period": object, "source_channels": list, "tables": ["DA" 또는 "SA"], "unresolved_terms": list}},
  "modification": {{
    "condition_groups": [
        {{
        "intent_type": "SELECT_DETAIL" 또는 "UPDATE_NUMERIC_VALUE" 또는 "ADD_DERIVED_COLUMN" 또는 "SELECT_AGGREGATE" 또는 "ASK_CLARIFICATION",
        "group_id": string,
        "dependency": "independent" 또는 "dependent",
        "depends_on": [],
        "conditions": [{{"field": string, "operator": "eq/in/contains/is_null_or_empty/gt/gte/lt/lte/between/or", "values": list, "conditions": list}}],
        "actions": [{{"target_field": string, "operation": "set_literal", "value": string}}],
        "group_by": [{{"field_alias": string, "resolved_column": string}}],
        "metrics": [{{"alias": string, "expression_type": "sum/avg/conversion_rate/cost_per_conversion/ctr", "source_column": string}}],
        "derived_column": object
      }}
    ],
    "group_by": [{{"field_alias": string, "resolved_column": string}}],
    "metrics": [{{"alias": string, "expression_type": "sum/avg/conversion_rate/cost_per_conversion/ctr", "source_column": string}}],
    "derived_column": object
  }}
}}
""".strip()


EARLY_FIELD_ALIASES = {
    "조회수": "노출수",
    "노출량": "노출수",
    "노출": "노출수",
    "클리크": "클릭수",
    "클릭": "클릭수",
    "광고비": "비용",
    "비요": "비용",
    "켐페인": "캠페인",
    "기기": "디바이스",
    "광고그룹": "광고 그룹",
}


def normalize_field_alias(value: Any) -> str:
    text = str(value or "").strip()
    token = compact_text(text)
    return EARLY_FIELD_ALIASES.get(token, text)


def normalize_condition(condition: dict[str, Any]) -> dict[str, Any]:
    field = normalize_field_alias(condition.get("field") or condition.get("column"))
    operator = str(condition.get("operator", "eq")).lower()
    if "/" in operator:
        operator = "in" if len(condition.get("values", []) or []) > 1 else "eq"
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
    if operator == "is_null_or_empty" and any(value is False or str(value).lower() == "false" for value in values):
        operator = "is_not_null_or_empty"
        values = []
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
        if "target_field" in action:
            action = {**action, "target_field": normalize_field_alias(action["target_field"])}
        actions.append(action)
    return actions


NUMERIC_FIELD_ALIASES = {
    "노출수": ("노출", "노출수", "노출 수", "노출량", "조회수", "impression", "impressions"),
    "클릭수": ("클릭", "클릭수", "클릭 수", "클리크", "click", "clicks"),
    "비용": ("비용", "광고비", "비요", "cost", "spend"),
    "세션수": ("세션", "세션수", "세션 수", "session", "sessions"),
}
DIMENSION_FIELD_ALIASES = {
    "캠페인": ("캠페인", "켐페인", "campaign"),
    "디바이스": ("디바이스", "기기", "device"),
    "광고 그룹": ("광고 그룹", "광고그룹", "ad group"),
    "날짜": ("날짜", "date"),
}
NUMERIC_PRESENCE_TERMS = ("있는", "있고", "있으며", "있는데", "들어간", "나온", "잡힌", "발생", "기록된", "찍힌", "1회이상", "1원이상", "0보다큰", "양수")
NUMERIC_ZERO_TERMS = ("없는", "없고", "없으며", "없는데", "0인", "0회", "0원")
EMPTY_TERMS = ("비어", "빈값", "누락", "공란", "null", "none")
MEDIA_TABLE_TERMS = {
    "SA": ("검색광고", "검색 광고"),
    "DA": ("디스플레이광고", "디스플레이 광고", "배너광고", "배너 광고"),
}
STRUCTURAL_UNRESOLVED_TERMS = ("적혀있고", "적혀있는", "적혀", "있는", "있고", "1회이상", "1원이상")


def canonical_numeric_field(value: Any) -> str:
    token = compact_text(value)
    for field, aliases in NUMERIC_FIELD_ALIASES.items():
        if token == compact_text(field) or token in {compact_text(alias) for alias in aliases}:
            return field
    return ""


def text_mentions_field_with_terms(text: str, field: str, terms: tuple[str, ...]) -> bool:
    compact = compact_text(text)
    aliases = NUMERIC_FIELD_ALIASES.get(field, (field,))
    for alias in aliases:
        alias_token = compact_text(alias)
        if not alias_token:
            continue
        for term in terms:
            term_token = compact_text(term)
            if re.search(re.escape(alias_token) + r".{0,8}" + re.escape(term_token), compact):
                return True
    return False


def infer_media_table_from_text(text: str) -> str | None:
    compact = compact_text(text)
    matched_tables: set[str] = set()
    for table_name, terms in MEDIA_TABLE_TERMS.items():
        if any(compact_text(term) in compact for term in terms):
            matched_tables.add(table_name)
    if len(matched_tables) == 1:
        return next(iter(matched_tables))
    return None


def is_media_table_term(value: Any, table_name: str) -> bool:
    token = compact_text(value)
    return bool(token) and any(token == compact_text(term) for term in MEDIA_TABLE_TERMS.get(table_name, ()))


def selection_with_media_table_hint(selection: dict[str, Any], text: str) -> dict[str, Any]:
    hinted_table = infer_media_table_from_text(text)
    if hinted_table:
        unresolved_terms = [term for term in selection.get("unresolved_terms", []) if not is_media_table_term(term, hinted_table)]
        return {**selection, "tables": [hinted_table], "unresolved_terms": unresolved_terms}
    return selection


def numeric_presence_requested(text: str, field: str) -> bool:
    return text_mentions_field_with_terms(text, field, NUMERIC_PRESENCE_TERMS) and not text_mentions_field_with_terms(text, field, EMPTY_TERMS)


def numeric_zero_requested(text: str, field: str) -> bool:
    return text_mentions_field_with_terms(text, field, NUMERIC_ZERO_TERMS) and not text_mentions_field_with_terms(text, field, EMPTY_TERMS)


def coerce_condition_numeric_presence(condition: dict[str, Any], source_text: str) -> dict[str, Any]:
    operator = str(condition.get("operator", "")).lower()
    if operator in {"or", "and"}:
        children = [
            coerce_condition_numeric_presence(child, source_text)
            for child in condition.get("conditions", [])
            if isinstance(child, dict)
        ]
        return {**condition, "conditions": children}
    field = canonical_numeric_field(condition.get("field"))
    if not field:
        return condition
    if numeric_zero_requested(source_text, field):
        if operator in {"is_null_or_empty", "is_not_null_or_empty", "not_null_or_empty", "is_empty", "is_null"}:
            return {**condition, "field": field, "operator": "eq", "values": ["0"]}
        return condition
    if not numeric_presence_requested(source_text, field):
        return condition
    if operator in {"is_null_or_empty", "is_not_null_or_empty", "not_null_or_empty", "is_empty", "is_null"}:
        return {**condition, "field": field, "operator": "gt", "values": ["0"]}
    return condition


def action_numeric_targets(group: dict[str, Any]) -> set[str]:
    targets: set[str] = set()
    for action in group.get("actions", []):
        if not isinstance(action, dict):
            continue
        field = canonical_numeric_field(action.get("target_field"))
        if field:
            targets.add(field)
    return targets


def condition_targets_field(condition: dict[str, Any], field: str) -> bool:
    operator = str(condition.get("operator", "")).lower()
    if operator in {"or", "and"}:
        return any(condition_targets_field(child, field) for child in condition.get("conditions", []) if isinstance(child, dict))
    return canonical_numeric_field(condition.get("field")) == field


def coerce_numeric_presence_conditions(ir: dict[str, Any], source_text: str) -> dict[str, Any]:
    modification = dict(ir.get("modification", {}))
    groups: list[dict[str, Any]] = []
    for group in modification.get("condition_groups", []):
        if not isinstance(group, dict):
            continue
        conditions = [
            coerce_condition_numeric_presence(condition, source_text)
            for condition in group.get("conditions", [])
            if isinstance(condition, dict)
        ]
        for target in sorted(action_numeric_targets(group)):
            if numeric_zero_requested(source_text, target) and not any(condition_targets_field(condition, target) for condition in conditions):
                conditions.append({"field": target, "operator": "eq", "values": ["0"]})
            if numeric_presence_requested(source_text, target) and not any(condition_targets_field(condition, target) for condition in conditions):
                conditions.append({"field": target, "operator": "gt", "values": ["0"]})
        groups.append({**group, "conditions": conditions})
    modification["condition_groups"] = groups
    return {**ir, "modification": modification}


def classify_intent(selection_text: str, modification_text: str) -> str:
    text = f"{selection_text} {modification_text}".lower()
    if any(keyword in text for keyword in ["합계", "평균", "전환율", "전환당", "클릭률", "비교", "계산", "ctr", "cpc", "cpa"]):
        return AGGREGATE_INTENT
    if any(keyword in text for keyword in ["새 컬럼", "새 구분", "새 항목", "만들", "기입", "분류값", "구분값", "상태값", "상태 값", "임시 상태", "붙", "값으로 분류"]):
        return ADD_DERIVED_COLUMN_INTENT
    if any(keyword in text for keyword in ["0으로", "보정", "채워", "바꿔", "고정", "preview", "미리보기", "승인"]):
        return UPDATE_INTENT
    if any(keyword in text for keyword in ["보고", "조회", "확인", "보여", "목록", "샘플", "데이터"]):
        return SELECT_DETAIL_INTENT
    return ASK_CLARIFICATION_INTENT


def force_classified_intent(ir: dict[str, Any], intent_type: str) -> dict[str, Any]:
    updated = dict(ir)
    modification = dict(updated.get("modification", {}))
    groups: list[dict[str, Any]] = []
    for group in modification.get("condition_groups", []):
        if isinstance(group, dict):
            group_intent = str(group.get("intent_type") or "").upper()
            if group.get("derived_column"):
                group_intent = ADD_DERIVED_COLUMN_INTENT
            elif group.get("metrics") or group.get("group_by"):
                group_intent = AGGREGATE_INTENT
            elif group.get("actions"):
                group_intent = UPDATE_INTENT
            elif not group_intent or group_intent == ASK_CLARIFICATION_INTENT:
                group_intent = intent_type
            groups.append({**group, "intent_type": group_intent})
    modification["condition_groups"] = groups
    updated["intent_type"] = intent_type
    updated["modification"] = modification
    return updated


def normalize_ir_structured_json(parsed: dict[str, Any]) -> dict[str, Any]:
    if "selection" not in parsed or "modification" not in parsed:
        raise ValueError("SQL_WORKFLOW_LLM_IR_PARSE_INVALID_SHAPE")
    selection = dict(parsed["selection"])
    period = selection.get("period")
    if not isinstance(period, dict) or not {"field", "start", "end"}.issubset(period):
        selection["period"] = {}
    intent_type = str(parsed.get("intent_type") or parsed.get("modification", {}).get("intent_type") or "").upper()
    top_level_group_by = parsed["modification"].get("group_by", [])
    top_level_metrics = parsed["modification"].get("metrics", [])
    top_level_derived = parsed["modification"].get("derived_column", {})
    condition_groups: list[dict[str, Any]] = []
    for index, group in enumerate(parsed["modification"].get("condition_groups", [])):
        actions = normalize_actions(group.get("actions", []))
        dependency = str(group.get("dependency", "independent")).lower()
        if dependency not in {"independent", "dependent"}:
            dependency = "independent"
        group_intent = str(group.get("intent_type") or "").upper()
        if group.get("derived_column") and group_intent == UPDATE_INTENT:
            group_intent = ADD_DERIVED_COLUMN_INTENT
        if not group_intent:
            if group.get("metrics") or group.get("group_by"):
                group_intent = AGGREGATE_INTENT
            elif group.get("derived_column"):
                group_intent = ADD_DERIVED_COLUMN_INTENT
            elif actions:
                group_intent = UPDATE_INTENT
            else:
                group_intent = intent_type or ASK_CLARIFICATION_INTENT
        condition_groups.append(
            {
                "group_id": group.get("group_id", f"group_{index + 1}"),
                "intent_type": group_intent,
                "dependency": dependency,
                "depends_on": list(group.get("depends_on", [])) if isinstance(group.get("depends_on", []), list) else [],
                "conditions": [normalize_condition(condition) for condition in group.get("conditions", [])],
                "actions": actions,
                "group_by": group.get("group_by") or (top_level_group_by if group_intent == AGGREGATE_INTENT else []),
                "metrics": group.get("metrics") or (top_level_metrics if group_intent == AGGREGATE_INTENT else []),
                "derived_column": group.get("derived_column") or (top_level_derived if group_intent == ADD_DERIVED_COLUMN_INTENT else {}),
            }
        )
    modification = dict(parsed["modification"])
    modification["condition_groups"] = condition_groups
    return {"intent_type": intent_type, "selection": selection, "modification": modification}


def infer_month_period_from_text(text: str) -> dict[str, str]:
    match = re.search(r"(?:(20\d{2})\s*년\s*)?(\d{1,2})\s*월", text)
    if not match:
        return {}
    year = int(match.group(1) or datetime.now().year)
    month = int(match.group(2))
    if month < 1 or month > 12:
        return {}
    last_day = calendar.monthrange(year, month)[1]
    return {
        "field": "날짜",
        "start": f"{year}-{month:02d}-01",
        "end": f"{year}-{month:02d}-{last_day:02d}",
        "year": str(year),
        "month": str(month),
    }


def normalize_period_from_text(selection: dict[str, Any], text: str, table_columns: dict[str, list[str]]) -> dict[str, Any]:
    period = selection.get("period")
    if isinstance(period, dict) and {"field", "start", "end"}.issubset(period):
        if not period.get("field") and "날짜" in table_columns.get((selection.get("tables") or ["SA"])[0], []):
            return {**selection, "period": {**period, "field": "날짜"}}
        return selection
    inferred = infer_month_period_from_text(text)
    table_name = (selection.get("tables") or ["SA"])[0]
    if inferred and inferred["field"] in table_columns.get(table_name, []):
        return {**selection, "period": inferred}
    return selection


def build_workflow_steps(modification: dict[str, Any]) -> list[dict[str, Any]]:
    known_group_ids = {str(group.get("group_id")) for group in modification.get("condition_groups", []) if group.get("group_id")}
    steps: list[dict[str, Any]] = []
    for index, group in enumerate(modification.get("condition_groups", [])):
        group_id = str(group.get("group_id") or f"group_{index + 1}")
        depends_on = [str(item) for item in group.get("depends_on", []) if str(item) in known_group_ids and str(item) != group_id]
        dependency = "dependent" if depends_on or group.get("dependency") == "dependent" else "independent"
        status = "pending" if dependency == "independent" or depends_on else "blocked"
        steps.append({
            "step_id": group_id,
            "group_id": group_id,
            "intent_type": group.get("intent_type"),
            "dependency": dependency,
            "depends_on": depends_on,
            "conditions": group.get("conditions", []),
            "actions": group.get("actions", []),
            "group_by": group.get("group_by", []),
            "metrics": group.get("metrics", []),
            "derived_column": group.get("derived_column", {}),
            "status": status,
        })
    return steps


def build_linked_step_plan(selection: dict[str, Any], modification: dict[str, Any]) -> list[dict[str, Any]]:
    groups = [group for group in modification.get("condition_groups", []) if isinstance(group, dict)]
    selection_scope = {
        "customer": selection.get("customer", "default"),
        "tables": list(selection.get("tables", [])),
        "source_channels": list(selection.get("source_channels", [])),
        "period": selection.get("period", {}),
    }
    plan: list[dict[str, Any]] = []
    for index, group in enumerate(groups):
        step_id = str(group.get("group_id") or f"step_{index + 1}")
        depends_on = [str(item) for item in group.get("depends_on", [])]
        dependency = "dependent" if depends_on or group.get("dependency") == "dependent" else "independent"
        plan.append(
            {
                "step_id": step_id,
                "group_id": step_id,
                "step_order": index + 1,
                "intent_type": group.get("intent_type"),
                "dependency": dependency,
                "depends_on": depends_on,
                "selection_scope": selection_scope,
                "conditions": group.get("conditions", []),
                "actions": group.get("actions", []),
                "group_by": group.get("group_by", []),
                "metrics": group.get("metrics", []),
                "derived_column": group.get("derived_column", {}),
                "expected_artifacts": ["sql_candidate", "validation_result", "change_preview_json", "user_confirmation"],
                "execution_gate": "preview_first_approval_required",
                "status": "planned" if dependency == "independent" or depends_on else "blocked",
            }
        )
    return plan


def validate_linked_step_plan(plan: list[dict[str, Any]]) -> dict[str, Any]:
    if len(plan) <= 1:
        return {
            "status": "not_applicable",
            "checks": ["single_step_or_no_linked_plan"],
            "warnings": [],
            "errors": [],
            "step_count": len(plan),
            "dependent_step_count": 0,
        }

    checks: list[str] = []
    warnings: list[str] = []
    errors: list[str] = []
    known = {str(step.get("step_id")) for step in plan if step.get("step_id")}
    step_ids = [str(step.get("step_id")) for step in plan if step.get("step_id")]
    duplicate_step_ids = sorted({step_id for step_id in step_ids if step_ids.count(step_id) > 1})
    for duplicate_step_id in duplicate_step_ids:
        errors.append(f"linked_step_duplicate_id:{duplicate_step_id}")
    seen: set[str] = set()
    dependent_step_count = 0
    for step in plan:
        step_id = str(step.get("step_id") or "")
        dependency = str(step.get("dependency") or "independent")
        depends_on = [str(item) for item in step.get("depends_on", [])]
        if dependency == "dependent":
            dependent_step_count += 1
        if not step_id:
            errors.append("linked_step_id_missing")
            continue
        if step_id in depends_on:
            errors.append(f"linked_step_self_dependency:{step_id}")
        missing = [item for item in depends_on if item not in known]
        if missing:
            errors.append(f"linked_step_missing_dependency:{step_id}:{missing}")
        future = [item for item in depends_on if item in known and item not in seen]
        if future:
            errors.append(f"linked_step_dependency_order_violation:{step_id}:{future}")
        if dependency == "dependent" and not depends_on:
            errors.append(f"linked_step_dependent_without_depends_on:{step_id}")
        if dependency == "independent" and depends_on:
            warnings.append(f"linked_step_independent_has_depends_on:{step_id}")
        seen.add(step_id)

    if dependent_step_count:
        checks.append("dependent_steps_declared")
    else:
        warnings.append("multi_step_plan_has_no_declared_dependencies")
    if not errors:
        checks.append("linked_step_dependencies_valid")
    return {
        "status": "passed" if not errors else "failed",
        "checks": checks,
        "warnings": warnings,
        "errors": errors,
        "step_count": len(plan),
        "dependent_step_count": dependent_step_count,
    }


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
    selection = selection_with_media_table_hint(selection, selection_text)
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
    condition_fields = {
        compact_text(condition.get("field"))
        for group in modification.get("condition_groups", [])
        for condition in group.get("conditions", [])
        if isinstance(condition, dict)
    }
    for field in list(condition_fields):
        for alias in NUMERIC_FIELD_ALIASES.get(str(field), ()):  # direct canonical fields are already compacted
            condition_fields.add(compact_text(alias))
    for field, aliases in NUMERIC_FIELD_ALIASES.items():
        if compact_text(field) in condition_fields:
            condition_fields.update(compact_text(alias) for alias in aliases)
    for field, aliases in DIMENSION_FIELD_ALIASES.items():
        if compact_text(field) in condition_fields:
            condition_fields.update(compact_text(alias) for alias in aliases)
    condition_values = {
        compact_text(value)
        for group in modification.get("condition_groups", [])
        for condition in group.get("conditions", [])
        if isinstance(condition, dict)
        for value in condition.get("values", [])
    }
    structural_terms = {compact_text(term) for term in STRUCTURAL_UNRESOLVED_TERMS}
    remaining = [term for term in unresolved_terms if compact_text(term) not in condition_values and compact_text(term) not in condition_fields and compact_text(term) not in structural_terms]
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


def response_content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        return "".join(parts)
    return str(content)


def parse_json_response(response: Any) -> dict[str, Any]:
    content = response_content_text(response.content if hasattr(response, "content") else response)
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
    if "없는" in modification_text and target_field:
        conditions.append({"field": target_field, "operator": "eq", "values": [0]})
    if ("비어" in modification_text or "빈값" in modification_text or "공란" in modification_text or "누락" in modification_text) and target_field:
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
    classified_intent = classify_intent(selection_text, modification_text)
    if classified_intent != ASK_CLARIFICATION_INTENT and normalized.get("intent_type") != classified_intent:
        normalized = force_classified_intent(normalized, classified_intent)
    elif not normalized.get("intent_type") or (normalized.get("intent_type") == ASK_CLARIFICATION_INTENT and classified_intent != ASK_CLARIFICATION_INTENT):
        normalized = force_classified_intent(normalized, classified_intent)
    normalized = coerce_numeric_presence_conditions(normalized, f"{selection_text} {modification_text}")
    return normalized


def parse_ir_request_node(state: ModificationWorkflowState, llm: Any = None) -> dict[str, Any]:
    if state.get("ir_structured_json") and state.get("selection_request") and state.get("modification_logic"):
        ir_structured_json = {
            "intent_type": state.get("ir_structured_json", {}).get("intent_type") or state.get("modification_logic", {}).get("intent_type"),
            "selection": state["selection_request"],
            "modification": state["modification_logic"],
        }
    else:
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
    ir_structured_json["selection"] = normalize_period_from_text(
        selection=ir_structured_json["selection"],
        text=f"{state['selection_text']} {state['modification_text']}",
        table_columns=state.get("table_columns", {}),
    )
    ir_structured_json["selection"] = remove_resolved_modification_terms(ir_structured_json["selection"], ir_structured_json["modification"])
    ir_structured_json = coerce_numeric_presence_conditions(ir_structured_json, f"{state['selection_text']} {state['modification_text']}")
    ir_structured_json["selection"]["intent_type"] = ir_structured_json.get("intent_type")
    ir_structured_json["modification"]["intent_type"] = ir_structured_json.get("intent_type")
    workflow_steps = build_workflow_steps(ir_structured_json["modification"])
    linked_step_plan = build_linked_step_plan(ir_structured_json["selection"], ir_structured_json["modification"])
    return {
        "selection_request": ir_structured_json["selection"],
        "modification_logic": ir_structured_json["modification"],
        "ir_structured_json": ir_structured_json,
        "workflow_steps": workflow_steps,
        "linked_step_plan": linked_step_plan,
        "linked_step_validation": validate_linked_step_plan(linked_step_plan),
    }
