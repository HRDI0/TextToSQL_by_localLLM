from __future__ import annotations

import difflib
import re
import unicodedata
from typing import Any

from app.langgraph_workflow.state import ModificationWorkflowState


def normalize_term(value: Any) -> str:
    text = unicodedata.normalize("NFC", str(value or "")).lower()
    return re.sub(r"\s+", "", text)


def similarity(left: str, right: str) -> float:
    return difflib.SequenceMatcher(None, normalize_term(left), normalize_term(right)).ratio()


def recommendation_text(original: str, candidate: dict[str, Any]) -> str:
    if candidate.get("candidate_type") == "column":
        return f"'{original}' 조건을 '{candidate['recommended_term']}' 기준으로 다시 확인해보세요."
    if candidate.get("candidate_type") == "value":
        return f"'{original}' 값을 '{candidate['recommended_term']}' 조건으로 다시 확인해보세요."
    return f"'{original}' 대신 '{candidate['recommended_term']}' 조건을 확인해보세요."


def column_candidates(term: str, state: ModificationWorkflowState, limit: int = 5) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for item in state.get("column_catalog", []):
        target_table = str(item.get("target_table") or "")
        column = str(item.get("column_name") or "")
        normalized = str(item.get("normalized_column_name") or column)
        if not target_table or not column:
            continue
        score = max(similarity(term, column), similarity(term, normalized))
        if score >= 0.45:
            candidates.append({"candidate_type": "column", "target_table": target_table, "recommended_term": column, "column_name": column, "semantic_role": item.get("semantic_role"), "score": round(score, 3)})
    for table_name, columns in state.get("table_columns", {}).items():
        for column in columns:
            score = similarity(term, column)
            if normalize_term(term) in normalize_term(column) or normalize_term(column) in normalize_term(term):
                score = max(score, 0.82)
            if score >= 0.45:
                candidates.append({"candidate_type": "column", "target_table": table_name, "recommended_term": column, "column_name": column, "score": round(score, 3)})
    for item in state.get("column_alias_mappings", []):
        user_term = str(item.get("user_term") or "")
        target_column = str(item.get("target_column") or "")
        target_table = str(item.get("target_table") or "")
        if not user_term or not target_column:
            continue
        score = similarity(term, user_term)
        if score >= 0.45:
            candidates.append({"candidate_type": "column", "target_table": target_table, "recommended_term": target_column, "column_name": target_column, "matched_alias": user_term, "score": round(max(score, 0.7), 3)})
    return sorted(candidates, key=lambda item: item["score"], reverse=True)[:limit]


def value_candidates(term: str, state: ModificationWorkflowState, limit: int = 5) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for item in state.get("value_catalog", []):
        target_table = str(item.get("target_table") or "")
        column_name = str(item.get("column_name") or "")
        raw_value = str(item.get("raw_value") or item.get("normalized_value") or "")
        normalized = str(item.get("normalized_value") or raw_value)
        if not target_table or not column_name or not raw_value:
            continue
        score = max(similarity(term, raw_value), similarity(term, normalized))
        if score >= 0.45:
            candidates.append({"candidate_type": "value", "target_table": target_table, "column_name": column_name, "recommended_term": raw_value, "frequency": item.get("frequency"), "score": round(score, 3)})
    for table_name, values in state.get("source_channel_values", {}).items():
        for value in values:
            score = similarity(term, value)
            if score >= 0.45:
                candidates.append({"candidate_type": "value", "target_table": table_name, "column_name": "source_channel", "recommended_term": value, "score": round(score, 3)})
    for item in state.get("source_channel_mappings", []):
        user_term = str(item.get("user_term") or "")
        source_channel = str(item.get("source_channel") or "")
        target_table = str(item.get("target_table") or "")
        if not user_term or not source_channel:
            continue
        score = similarity(term, user_term)
        if score >= 0.45:
            candidates.append({"candidate_type": "value", "target_table": target_table, "column_name": "source_channel", "recommended_term": source_channel, "matched_alias": user_term, "score": round(max(score, 0.7), 3)})
    return sorted(candidates, key=lambda item: item["score"], reverse=True)[:limit]


def ambiguous_condition_candidates(state: ModificationWorkflowState) -> list[dict[str, Any]]:
    recommendations: list[dict[str, Any]] = []
    selection = state.get("selection_request", {})
    modification = state.get("modification_logic", {})
    terms = [(str(term), "selection") for term in selection.get("unresolved_terms", []) if str(term).strip()]
    for group in modification.get("condition_groups", []):
        for condition in group.get("conditions", []):
            if not isinstance(condition, dict):
                continue
            field = str(condition.get("field") or "")
            if field and not any(field in columns for columns in state.get("table_columns", {}).values()):
                terms.append((field, "modification"))
            terms.extend((str(value), "modification") for value in condition.get("values", []) if str(value).strip())
    seen: set[str] = set()
    for term, origin in terms:
        key = normalize_term(term)
        if not key or key in seen:
            continue
        seen.add(key)
        emitted: set[tuple[str, str, str, str]] = set()
        for candidate in [*column_candidates(term, state), *value_candidates(term, state)]:
            identity = (str(candidate.get("candidate_type")), str(candidate.get("target_table")), str(candidate.get("column_name")), str(candidate.get("recommended_term")))
            if identity in emitted:
                continue
            emitted.add(identity)
            recommendations.append({
                "original_term": term,
                "input_origin": origin,
                "confidence": "high" if candidate["score"] >= 0.82 else "medium" if candidate["score"] >= 0.65 else "low",
                "action": "rerun_preview",
                "recommendation_text": recommendation_text(term, candidate),
                **candidate,
            })
    return recommendations


def build_resolution_recommendations_node(state: ModificationWorkflowState) -> dict[str, Any]:
    recommendations = ambiguous_condition_candidates(state)
    warnings = [] if recommendations else []
    return {
        "resolution_candidates": recommendations,
        "query_recommendations": recommendations,
        "resolution_warnings": warnings,
    }
