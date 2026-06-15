from __future__ import annotations

import json
import os
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


SELECTION_PLACEHOLDER = "예: 5월 검색광고 원본 데이터만 조회해줘."
MODIFICATION_PLACEHOLDER = "예: 매체명이 '검색매체A'이고 캠페인명이 'campaign_alpha' 또는 'campaign_beta'라면 광고상품 컬럼은 '검색광고 상품'으로 기입한다."
STORED_RULES_PLACEHOLDER = "예: []"
DEFAULT_LLM_BASE_URL = "http://127.0.0.1:8000/v1"
DEFAULT_LLM_MODEL = "qwen3-14b"


def parse_stored_rules(raw_json: str) -> list[dict[str, Any]]:
    parsed = json.loads(raw_json or "[]")
    if not isinstance(parsed, list):
        raise ValueError("stored_rules JSON must be a list.")
    for index, item in enumerate(parsed):
        if not isinstance(item, dict):
            raise ValueError(f"stored_rules[{index}] must be an object.")
    return parsed


def build_local_llm() -> Any:
    try:
        from langchain_openai import ChatOpenAI
    except ImportError as exc:
        raise RuntimeError("SQL_WORKFLOW_LLM_CLIENT_IMPORT_FAILED: install langchain-openai in .venv") from exc

    base_url = os.environ.get("SQL_WORKFLOW_LLM_BASE_URL", DEFAULT_LLM_BASE_URL).strip()
    model = os.environ.get("SQL_WORKFLOW_LLM_MODEL", DEFAULT_LLM_MODEL).strip()
    if not base_url:
        raise RuntimeError("SQL_WORKFLOW_LLM_BASE_URL_MISSING")
    if not model:
        raise RuntimeError("SQL_WORKFLOW_LLM_MODEL_MISSING")

    return ChatOpenAI(
        base_url=base_url,
        api_key=lambda: os.environ.get("SQL_WORKFLOW_LLM_API_KEY", "EMPTY"),
        model=model,
        temperature=float(os.environ.get("SQL_WORKFLOW_LLM_TEMPERATURE", "0")),
        max_tokens=int(os.environ.get("SQL_WORKFLOW_LLM_MAX_TOKENS", "2048")),
        timeout=float(os.environ.get("SQL_WORKFLOW_LLM_TIMEOUT", "180")),
        extra_body={"chat_template_kwargs": {"enable_thinking": os.environ.get("SQL_WORKFLOW_LLM_ENABLE_THINKING", "false").lower() == "true"}},
    )


def run_graph(
    selection_text: str,
    modification_text: str,
    stored_rules: list[dict[str, Any]],
    approved: bool,
    approved_sql_fingerprint: str | None = None,
    approved_preview_fingerprint: str | None = None,
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
    llm = build_local_llm()
    with connect_db() as connection:
        graph = build_modification_workflow_graph(connection=connection, llm=llm)
        return graph.invoke(state)


def apply_existing_preview_result(result: dict[str, Any], approved: bool) -> dict[str, Any]:
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
    elif can_execute(updated["validation_result"], user_confirmation, change_preview_json):
        with connect_db() as connection:
            updated["execution_result"] = execute_confirmed_sql(connection, cast(ModificationWorkflowState, updated))
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
    st.subheader("1. IR 구조화 JSON")
    st.json(output.get("ir_structured_json", {}))

    st.subheader("2. IR 구조화 JSON 기반 Query")
    query_from_ir = output.get("query_from_ir", {})
    sql_text = query_from_ir.get("sql") or f"-- SQL not generated\n-- reason: {query_from_ir.get('reason') or result.get('errors', [])}"
    st.code(sql_text, language="sql")
    st.caption("위 SQL은 MariaDB에 적용될 실제 SQL 미리보기입니다. 사용자 승인 전에는 실행되지 않습니다.")

    st.subheader("3. 샘플 데이터 적용 결과")
    row_examples = output.get("row_modification_examples", {})
    sample_rows = row_examples.get("sample_rows", []) if isinstance(row_examples, dict) else []
    if sample_rows:
        st.dataframe(sample_rows, use_container_width=True)
    else:
        st.info("표시할 샘플 row가 없습니다.")


def main() -> None:
    st.title("LangGraph Rule Pipeline Test")
    st.caption("Docs 기반 LangGraph workflow를 preview-first로 수동 검수하는 페이지입니다.")

    with st.form("pipeline_input_form"):
        selection_text = st.text_area("조회 조건", value="", placeholder=SELECTION_PLACEHOLDER, key="selection_text")
        modification_text = st.text_area("수정 조건", value="", placeholder=MODIFICATION_PLACEHOLDER, key="modification_text")
        stored_rules_json = st.text_area("MongoDB placeholder stored_rules JSON", value="", placeholder=STORED_RULES_PLACEHOLDER, key="stored_rules_json")
        submitted = st.form_submit_button("Run to Preview")

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
        except Exception as exc:
            st.error(f"Pipeline failed: {exc}")

    result = st.session_state.get("pipeline_result")
    if not result:
        st.info("Run to Preview 버튼을 누르면 IR 구조화 JSON, IR 기반 Query, Row 수정 예시를 확인할 수 있습니다.")
        return

    show_final_result(result)

    validation_passed = result.get("validation_result", {}).get("status") == "passed"
    st.subheader("Confirmation")
    if not validation_passed:
        st.warning("Validation이 통과하지 않아 승인/실행할 수 없습니다.")
        return

    col_approve, col_reject = st.columns(2)
    with col_approve:
        approve_clicked = st.button("Approve")
    with col_reject:
        reject_clicked = st.button("Reject")

    if approve_clicked or reject_clicked:
        st.session_state.pipeline_result = apply_existing_preview_result(st.session_state.pipeline_result, approve_clicked)
        st.rerun()


if __name__ == "__main__":
    main()
