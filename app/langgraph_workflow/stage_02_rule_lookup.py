from __future__ import annotations

# pyright: reportTypedDictNotRequiredAccess=false

from typing import Any

from app.langgraph_workflow.state import ModificationWorkflowState


def condition_signature(condition: dict[str, Any]) -> tuple[str, str, tuple[Any, ...]]:
    return (
        condition["field"],
        condition["operator"],
        tuple(sorted(condition.get("values", []))),
    )


def build_mongo_rule_query(selection_request: dict[str, Any], modification_logic: dict[str, Any]) -> dict[str, Any]:
    condition_filters: list[dict[str, Any]] = []
    for group in modification_logic.get("condition_groups", []):
        for condition in group.get("conditions", []):
            condition_filters.append(
                {
                    "condition_groups.conditions": {
                        "$elemMatch": {
                            "field": condition["field"],
                            "operator": condition["operator"],
                            "values": {"$all": condition.get("values", [])},
                        }
                    }
                }
            )

    return {
        "customer": selection_request.get("customer", "default"),
        "active": True,
        "$and": condition_filters,
    }


def find_rules_in_placeholder_store(
    selection_request: dict[str, Any],
    modification_logic: dict[str, Any],
    stored_rules: list[dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    query = build_mongo_rule_query(selection_request, modification_logic)
    expected = {
        condition_signature(condition)
        for group in modification_logic.get("condition_groups", [])
        for condition in group.get("conditions", [])
    }

    matched_rules: list[dict[str, Any]] = []
    for rule in stored_rules:
        if not rule.get("active", True):
            continue
        if rule.get("validation_status") != "approved_for_reuse":
            continue
        if rule.get("customer") != selection_request.get("customer", "default"):
            continue

        stored = {
            condition_signature(condition)
            for group in rule.get("condition_groups", [])
            for condition in group.get("conditions", [])
        }
        if expected.issubset(stored):
            matched_rules.append(rule)

    return query, matched_rules


def choose_rule_or_sql_fallback(matched_rules: list[dict[str, Any]]) -> dict[str, Any]:
    if matched_rules:
        return {"source": "mongodb_rule", "rule": matched_rules[0], "requires_sql_generation": False}
    return {"source": "llm_sql_fallback", "rule": {}, "requires_sql_generation": True}


def lookup_mongodb_rule_node(state: ModificationWorkflowState) -> dict[str, Any]:
    mongo_query, matched_rules = find_rules_in_placeholder_store(
        selection_request=state["selection_request"],
        modification_logic=state["modification_logic"],
        stored_rules=state.get("stored_rules", []),
    )
    return {
        "mongo_query": mongo_query,
        "matched_rules": matched_rules,
        "effective_modification_plan": choose_rule_or_sql_fallback(matched_rules),
    }
