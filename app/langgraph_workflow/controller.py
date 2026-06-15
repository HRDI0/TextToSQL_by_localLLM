from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph  # type: ignore[import-not-found]

from app.langgraph_workflow.db import load_schema_metadata_node
from app.langgraph_workflow.stage_01_parse import parse_ir_request_node
from app.langgraph_workflow.stage_02_rule_lookup import lookup_mongodb_rule_node
from app.langgraph_workflow.stage_03_sql import (
    fetch_target_dataset_node,
    generate_or_reuse_sql_node,
    generate_selection_sql_node,
    validate_generated_sql_node,
    validate_selection_sql_node,
)
from app.langgraph_workflow.stage_04_output import (
    build_change_preview_json_node,
    build_execution_result_json_node,
    execute_confirmed_sql_node,
    wait_for_user_confirmation_node,
)
from app.langgraph_workflow.state import DbConfig, ModificationWorkflowState


def build_modification_workflow_graph(llm: Any = None, connection: Any = None, db_config: DbConfig | None = None) -> Any:
    builder = StateGraph(ModificationWorkflowState)

    builder.add_node("load_schema_metadata", lambda state: load_schema_metadata_node(state, connection, db_config))
    builder.add_node("parse_ir_request", lambda state: parse_ir_request_node(state, llm))
    builder.add_node("generate_selection_sql", generate_selection_sql_node)
    builder.add_node("validate_selection_sql", validate_selection_sql_node)
    builder.add_node("fetch_target_dataset", lambda state: fetch_target_dataset_node(state, connection))
    builder.add_node("lookup_mongodb_rule", lookup_mongodb_rule_node)
    builder.add_node("choose_rule_or_generate_crud_sql", lambda state: generate_or_reuse_sql_node(state, llm))
    builder.add_node("validate_generated_sql", validate_generated_sql_node)
    builder.add_node("build_change_preview_json", lambda state: build_change_preview_json_node(state, connection))
    builder.add_node("wait_for_user_confirmation", wait_for_user_confirmation_node)
    builder.add_node("execute_confirmed_sql", lambda state: execute_confirmed_sql_node(state, connection))
    builder.add_node("build_execution_result_json", build_execution_result_json_node)

    builder.add_edge(START, "load_schema_metadata")
    builder.add_edge("load_schema_metadata", "parse_ir_request")
    builder.add_edge("parse_ir_request", "generate_selection_sql")
    builder.add_edge("generate_selection_sql", "validate_selection_sql")
    builder.add_edge("validate_selection_sql", "fetch_target_dataset")
    builder.add_edge("fetch_target_dataset", "lookup_mongodb_rule")
    builder.add_edge("lookup_mongodb_rule", "choose_rule_or_generate_crud_sql")
    builder.add_edge("choose_rule_or_generate_crud_sql", "validate_generated_sql")
    builder.add_edge("validate_generated_sql", "build_change_preview_json")
    builder.add_edge("build_change_preview_json", "wait_for_user_confirmation")
    builder.add_edge("wait_for_user_confirmation", "execute_confirmed_sql")
    builder.add_edge("execute_confirmed_sql", "build_execution_result_json")
    builder.add_edge("build_execution_result_json", END)

    return builder.compile()


def build_demo_state(approved: bool = False, stored_rules: list[dict[str, Any]] | None = None) -> ModificationWorkflowState:
    return {
        "selection_text": "5월 검색광고 원본 데이터만 조회해줘.",
        "modification_text": "매체명이 '검색매체A'이고 캠페인명이 'campaign_alpha' 또는 'campaign_beta'라면 광고상품 컬럼은 '검색광고 상품'으로 기입한다.",
        "stored_rules": stored_rules or [],
        "user_confirmation": {"approved": approved},
        "errors": [],
    }
