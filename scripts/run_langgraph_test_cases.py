#!/usr/bin/env python3
from __future__ import annotations

import re
import sys
import argparse
from pathlib import Path
from typing import Any

from dotenv import load_dotenv  # type: ignore[reportMissingImports]

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
load_dotenv(PROJECT_ROOT / ".env")

from app.langgraph_workflow import connect_db  # noqa: E402
from app.langgraph_workflow.stage_04_output import ensure_linked_plan, update_delta_status  # noqa: E402
from app.streamlit_langgraph_test import effective_preview_context_for_step, filtered_ir_for_step, preview_delta_items_from_result, run_graph  # noqa: E402


TEST_FILE = PROJECT_ROOT / "test" / "test.md"
DEFAULT_RESULT_FILE = PROJECT_ROOT / "test" / "test_result.md"

EXPECTED_CASES: dict[str, dict[str, Any]] = {}


def _find_keyword_outside_quotes(sql: str, keyword: str, start: int = 0) -> int:
    in_single_quote = False
    in_double_quote = False
    in_backtick = False
    index = start
    keyword_upper = keyword.upper()
    while index < len(sql):
        char = sql[index]
        if char == "'" and not in_double_quote and not in_backtick:
            in_single_quote = not in_single_quote
        elif char == '"' and not in_single_quote and not in_backtick:
            in_double_quote = not in_double_quote
        elif char == "`" and not in_single_quote and not in_double_quote:
            in_backtick = not in_backtick
        elif not in_single_quote and not in_double_quote and not in_backtick:
            if sql[index : index + len(keyword)].upper() == keyword_upper:
                before = sql[index - 1] if index > 0 else " "
                after_index = index + len(keyword)
                after = sql[after_index] if after_index < len(sql) else " "
                if not (before.isalnum() or before == "_") and not (after.isalnum() or after == "_"):
                    return index
        index += 1
    return -1


def _split_assignments(set_clause: str) -> list[str]:
    assignments: list[str] = []
    in_single_quote = False
    in_double_quote = False
    in_backtick = False
    paren_depth = 0
    start = 0
    for index, char in enumerate(set_clause):
        if char == "'" and not in_double_quote and not in_backtick:
            in_single_quote = not in_single_quote
        elif char == '"' and not in_single_quote and not in_backtick:
            in_double_quote = not in_double_quote
        elif char == "`" and not in_single_quote and not in_double_quote:
            in_backtick = not in_backtick
        elif not in_single_quote and not in_double_quote and not in_backtick:
            if char == "(":
                paren_depth += 1
            elif char == ")" and paren_depth > 0:
                paren_depth -= 1
            elif char == "," and paren_depth == 0:
                assignments.append(set_clause[start:index].strip())
                start = index + 1
    tail = set_clause[start:].strip()
    if tail:
        assignments.append(tail)
    return assignments


def _assignment_column(assignment: str) -> str | None:
    match = re.match(r"\s*`([^`]+)`\s*=", assignment)
    if match:
        return match.group(1)
    match = re.match(r"\s*([A-Za-z0-9_가-힣 ]+?)\s*=", assignment)
    if match:
        return match.group(1).strip()
    return None


def extract_update_set_columns(sql: str) -> list[str]:
    update_index = _find_keyword_outside_quotes(sql, "UPDATE")
    if update_index < 0:
        return []
    set_index = _find_keyword_outside_quotes(sql, "SET", update_index + len("UPDATE"))
    if set_index < 0:
        return []
    where_index = _find_keyword_outside_quotes(sql, "WHERE", set_index + len("SET"))
    set_end = where_index if where_index >= 0 else len(sql)
    assignments = _split_assignments(sql[set_index + len("SET") : set_end])
    columns: list[str] = []
    for assignment in assignments:
        column = _assignment_column(assignment)
        if column:
            columns.append(column)
    return columns


def extract_zero_update_set_columns(sql: str) -> list[str]:
    update_index = _find_keyword_outside_quotes(sql, "UPDATE")
    if update_index < 0:
        return []
    set_index = _find_keyword_outside_quotes(sql, "SET", update_index + len("UPDATE"))
    if set_index < 0:
        return []
    where_index = _find_keyword_outside_quotes(sql, "WHERE", set_index + len("SET"))
    set_end = where_index if where_index >= 0 else len(sql)
    columns: list[str] = []
    for assignment in _split_assignments(sql[set_index + len("SET") : set_end]):
        column = _assignment_column(assignment)
        if not column:
            continue
        if re.search(r"=\s*(?:'0'|\"0\"|0)(?:\s|$)", assignment):
            columns.append(column)
    return columns


MEDIA_TABLES = {"검색광고": "SA", "디스플레이 광고": "DA", "디스플레이": "DA"}
METRIC_COLUMNS = {"클릭": "클릭수", "노출": "노출수", "비용": "비용"}
DIMENSION_COLUMNS = {"날짜": "날짜", "캠페인": "캠페인", "기기": "디바이스", "디바이스": "디바이스", "광고 그룹": "광고 그룹"}


def compact_text(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).lower()


def parse_number(value: Any) -> float | None:
    try:
        text = str(value).replace(",", "").strip()
        if text == "":
            return None
        return float(text)
    except (TypeError, ValueError):
        return None


def value_is_empty(value: Any) -> bool:
    return value is None or str(value).strip() == ""


def value_is_zero_or_empty(value: Any) -> bool:
    number = parse_number(value)
    if number is not None:
        return number == 0
    return value_is_empty(value)


def value_is_numeric_zero(value: Any) -> bool:
    number = parse_number(value)
    return number is not None and number == 0


def media_segments(request_text: str) -> list[dict[str, str]]:
    matches: list[tuple[int, str, str]] = []
    for marker, table in MEDIA_TABLES.items():
        for match in re.finditer(re.escape(marker), request_text):
            matches.append((match.start(), marker, table))
    matches.sort(key=lambda item: item[0])
    segments: list[dict[str, str]] = []
    for index, (start, marker, table) in enumerate(matches):
        end = matches[index + 1][0] if index + 1 < len(matches) else len(request_text)
        segment = request_text[start:end]
        if segments and segments[-1]["table"] == table and segments[-1]["text"] == segment:
            continue
        segments.append({"table": table, "marker": marker, "text": segment})
    return segments


def summary_columns(summary: dict[str, Any]) -> list[str]:
    columns: list[str] = []
    columns.extend(extract_update_set_columns(str(summary.get("sql") or "")))
    columns.extend(str(item.get("column")) for item in summary.get("predicate", []) if isinstance(item, dict) and item.get("column"))
    columns.extend(str(item) for item in summary.get("referenced_columns", []) if item)
    for row in summary.get("sample_rows", []):
        columns.extend(str(column).split(".", 1)[-1] for column in row.keys())
    return unique_preserve_order([column for column in columns if column and column != "None"])


def summary_signal_columns(summary: dict[str, Any]) -> list[str]:
    columns: list[str] = []
    columns.extend(extract_update_set_columns(str(summary.get("sql") or "")))
    columns.extend(str(item.get("column")) for item in summary.get("predicate", []) if isinstance(item, dict) and item.get("column"))
    columns.extend(str(item) for item in summary.get("referenced_columns", []) if item)
    if str(summary.get("sql_type") or "").upper() == "SELECT" and any(str(key).endswith(("합계", "평균")) for row in summary.get("sample_rows", []) for key in row.keys()):
        for row in summary.get("sample_rows", []):
            columns.extend(str(column).split(".", 1)[-1] for column in row.keys())
    return unique_preserve_order([column for column in columns if column and column != "None"])


def unique_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def metric_tokens_for_columns(columns: list[str]) -> list[str]:
    tokens: list[str] = []
    for token, column in {**METRIC_COLUMNS, **DIMENSION_COLUMNS}.items():
        if column in columns:
            tokens.append(token)
    return unique_preserve_order(tokens)


def expected_table_from_request(request_text: str, summary: dict[str, Any]) -> str | None:
    segments = media_segments(request_text)
    tables = sorted({segment["table"] for segment in segments})
    if len(tables) == 1:
        return tables[0]
    if len(tables) != 2:
        return None

    columns = summary_signal_columns(summary)
    update_columns = extract_update_set_columns(str(summary.get("sql") or ""))
    tokens = metric_tokens_for_columns(columns)
    scores: dict[str, int] = {"SA": 0, "DA": 0}
    for segment in segments:
        segment_text = compact_text(segment["text"])
        score = 0
        for token in tokens:
            if compact_text(token) in segment_text:
                score += 2
        for column in update_columns:
            token = next((name for name, metric_column in METRIC_COLUMNS.items() if metric_column == column), "")
            if token and compact_text(token) in segment_text:
                score += 3
        scores[segment["table"]] += score
    best_table, best_score = max(scores.items(), key=lambda item: item[1])
    other_score = scores["DA" if best_table == "SA" else "SA"]
    return best_table if best_score > 0 and best_score > other_score else None


def relevant_request_segment(request_text: str, table_name: str | None) -> str:
    if table_name not in {"SA", "DA"}:
        return request_text
    segments = [segment["text"] for segment in media_segments(request_text) if segment["table"] == table_name]
    return " ".join(segments) if segments else request_text


def request_clauses(text: str) -> list[str]:
    clauses = [item.strip() for item in re.split(r"\n+|(?:\s*이어서\s*)|(?:\s*그리고\s*)|(?<=[.!?])\s+|,", text) if item.strip()]
    return clauses or [text]


def semantic_request_scope(segment: str, summary: dict[str, Any]) -> str:
    clauses = request_clauses(segment)
    if len(clauses) <= 1:
        return segment

    columns = summary_columns(summary)
    column_tokens = metric_tokens_for_columns(columns)
    set_columns = extract_update_set_columns(str(summary.get("sql") or ""))
    set_tokens = [token for token, column in METRIC_COLUMNS.items() if column in set_columns]
    predicates = [item for item in summary.get("predicate", []) if isinstance(item, dict)]
    sql_type = str(summary.get("sql_type") or "").upper()

    best_clause = segment
    best_score = 0
    for clause in clauses:
        compact_clause = compact_text(clause)
        score = 0
        for token in column_tokens:
            if compact_text(token) in compact_clause:
                score += 2
                if sql_type in {"UPDATE", "INSERT"} and re.search(compact_text(token) + r".{0,8}(0으로|보정|정리|맞추|바꾸)", compact_clause):
                    score += 5
        for token in set_tokens:
            token_compact = compact_text(token)
            if token_compact in compact_clause:
                score += 5
            if sql_type == "UPDATE" and re.search(token_compact + r".{0,8}0으로", compact_clause):
                score += 5
        for predicate in predicates:
            column = str(predicate.get("column") or "")
            token = next((name for name, metric_column in METRIC_COLUMNS.items() if metric_column == column), "")
            if not token:
                continue
            token_compact = compact_text(token)
            if token_compact not in compact_clause:
                continue
            operator = str(predicate.get("operator") or "").lower()
            values = [str(value).strip() for value in predicate.get("values", [])]
            if operator in {"eq", "="} and values == ["0"] and any(marker in compact_clause for marker in ["없는", "0인", "비어"]):
                score += 6
            elif operator in {"gt", "gte", ">", ">="} and any(marker in compact_clause for marker in ["있는", "이상"]):
                score += 4
        if sql_type == "SELECT" and any(word in compact_clause for word in ["합계", "평균", "계산", "률"]):
            score += 3
        if score > best_score:
            best_score = score
            best_clause = clause
    return best_clause if best_score > 0 else segment


def expected_zero_update_columns(text: str) -> list[str]:
    compact = compact_text(text)
    expected: list[str] = []
    for token, column in METRIC_COLUMNS.items():
        token_compact = compact_text(token)
        if (
            re.search(token_compact + r"(을|를).{0,8}0으로", compact)
            or re.search(token_compact + r"0으로", compact)
            or re.search(token_compact + r".{0,8}(보정|정리|맞추|바꾸)", compact)
        ):
            expected.append(column)
    if "세값" in compact and all(token in compact for token in ["노출", "클릭", "비용"]):
        expected.extend(["노출수", "클릭수", "비용"])
    return unique_preserve_order(expected)


def expected_group_columns(text: str) -> list[str]:
    compact = compact_text(text)
    expected: list[str] = []
    if "날짜별" in compact or "날짜기준" in compact:
        expected.append("날짜")
    if "캠페인별" in compact or "캠페인기준" in compact:
        expected.append("캠페인")
    if "기기기준" in compact or "기기별" in compact or "디바이스기준" in compact:
        expected.append("디바이스")
    return expected


def expected_metric_columns(text: str) -> list[str]:
    compact = compact_text(text)
    expected: list[str] = []
    for token, column in METRIC_COLUMNS.items():
        token_compact = compact_text(token)
        if token_compact + "합계" in compact or token_compact + "평균" in compact or token_compact + "률" in compact:
            expected.append(column)
    if "클릭률" in compact:
        expected.extend(["클릭수", "노출수"])
    return unique_preserve_order(expected)


def expected_row_conditions(text: str) -> list[dict[str, str]]:
    compact = compact_text(text)
    conditions: list[dict[str, str]] = []
    for token, column in METRIC_COLUMNS.items():
        token_compact = compact_text(token)
        if token_compact + "이없는" in compact or token_compact + "가없는" in compact:
            conditions.append({"column": column, "operator": "zero_or_empty"})
            continue
        if (
            token_compact + "이있는" in compact
            or token_compact + "가있는" in compact
            or token_compact + "도있는" in compact
            or re.search(token_compact + r".{0,4}(1회|1원|1)이상", compact)
        ):
            conditions.append({"column": column, "operator": "gt_zero"})
    if "캠페인이비어있거나" in compact:
        conditions.append({"column": "캠페인", "operator": "empty"})
    elif "캠페인이적혀" in compact or "캠페인이있" in compact or "캠페인이비어있지" in compact or "캠페인이름이적혀" in compact:
        conditions.append({"column": "캠페인", "operator": "not_empty"})
    if "광고그룹이비어있는" in compact:
        conditions.append({"column": "광고 그룹", "operator": "empty"})
    elif "광고그룹이비어있지" in compact:
        conditions.append({"column": "광고 그룹", "operator": "not_empty"})
    return conditions


def row_value_for_condition(row: dict[str, Any], column: str) -> Any:
    return row.get(f"before.{column}", row.get(column))


def row_satisfies_expected_condition(row: dict[str, Any], condition: dict[str, str]) -> bool:
    value = row_value_for_condition(row, condition["column"])
    operator = condition["operator"]
    if operator == "gt_zero":
        number = parse_number(value)
        return number is not None and number > 0
    if operator == "zero_or_empty":
        return value_is_zero_or_empty(value)
    if operator == "not_empty":
        return not value_is_empty(value)
    if operator == "empty":
        return value_is_empty(value)
    return True


def sample_rows_match_expected_conditions(
    sample_rows: list[dict[str, Any]],
    conditions: list[dict[str, str]],
    allowed_columns: set[str] | None = None,
) -> list[str]:
    errors: list[str] = []
    for condition in conditions:
        if allowed_columns is not None and condition["column"] not in allowed_columns:
            continue
        visible_rows = [row for row in sample_rows if condition["column"] in row or f"before.{condition['column']}" in row]
        if not visible_rows:
            continue
        mismatched = [index for index, row in enumerate(visible_rows) if not row_satisfies_expected_condition(row, condition)]
        if mismatched:
            errors.append(f"semantic_sample_condition_mismatch: {condition['column']} {condition['operator']}")
    return errors


def semantic_alignment_errors(case_number: str, request_text: str, summary: dict[str, Any]) -> list[str]:
    errors = semantic_errors(case_number, summary)
    target_table = str(summary.get("target_table") or "")
    if "," not in target_table:
        expected_table = expected_table_from_request(request_text, summary)
        if expected_table and target_table and target_table != expected_table:
            errors.append(f"semantic_table_mismatch: expected_{expected_table}_got_{target_table}")
    table_for_segment = target_table if target_table in {"SA", "DA"} else expected_table_from_request(request_text, summary)
    segment = semantic_request_scope(relevant_request_segment(request_text, table_for_segment), summary)
    sql = str(summary.get("sql") or "")
    sql_type = str(summary.get("sql_type") or "").upper()
    if not sql_type:
        sql_type = sql.lstrip().split(None, 1)[0].upper() if sql.strip() else ""
    sample_rows = summary.get("sample_rows", [])
    predicate_columns = {str(item.get("column")) for item in summary.get("predicate", []) if isinstance(item, dict) and item.get("column")}

    if sql_type == "UPDATE":
        set_columns = extract_update_set_columns(sql)
        zero_set_columns = extract_zero_update_set_columns(sql)
        expected_targets = unique_preserve_order([*expected_zero_update_columns(segment), *zero_set_columns])
        if expected_targets and set_columns and not set(expected_targets) & set(set_columns):
            errors.append(f"semantic_update_target_missing: {', '.join(expected_targets)}")
        for column in expected_targets:
            if column not in set_columns:
                errors.append(f"semantic_update_target_missing: {column}")
                continue
            if int(summary.get("previewed_row_count") or 0) > 0 and not any(f"after.{column}" in row for row in sample_rows):
                errors.append(f"semantic_update_after_missing: {column}")
                continue
            after_values = [row.get(f"after.{column}") for row in sample_rows if f"after.{column}" in row]
            if after_values and any(not value_is_numeric_zero(value) for value in after_values):
                errors.append(f"semantic_update_after_not_zero: {column}")
        for column in set_columns:
            if column not in expected_targets:
                continue
            after_values = [row.get(f"after.{column}") for row in sample_rows if f"after.{column}" in row]
            if after_values and any(not value_is_numeric_zero(value) for value in after_values):
                errors.append(f"semantic_update_after_not_zero: {column}")
        allowed_condition_columns = predicate_columns or set(set_columns)
        errors.extend(sample_rows_match_expected_conditions(sample_rows, expected_row_conditions(segment), allowed_condition_columns))
    elif sql_type == "INSERT":
        expected_update_targets = expected_zero_update_columns(segment)
        if expected_update_targets:
            errors.append(f"semantic_expected_update_got_insert: {', '.join(expected_update_targets)}")
        if not any(str(key).startswith("after.") for row in sample_rows for key in row.keys()):
            errors.append("semantic_derived_value_preview_missing_after_value")
        errors.extend(sample_rows_match_expected_conditions(sample_rows, expected_row_conditions(segment), predicate_columns or None))
    elif sql_type == "SELECT":
        columns = summary_columns(summary)
        aggregate_text = compact_text(segment)
        if any(token in aggregate_text for token in ["합계", "평균", "클릭률", "계산"]):
            for column in expected_group_columns(segment):
                if column not in columns:
                    errors.append(f"semantic_group_column_missing: {column}")
            for column in expected_metric_columns(segment):
                if column not in columns:
                    errors.append(f"semantic_metric_column_missing: {column}")
        else:
            errors.extend(sample_rows_match_expected_conditions(sample_rows, expected_row_conditions(segment), predicate_columns or None))
    return unique_preserve_order(errors)


def parse_cases(path: Path) -> list[dict[str, str]]:
    cases: list[dict[str, str]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.startswith("|") or "---" in line or "번호" in line:
            continue
        parts = [part.strip() for part in line.strip("|").split("|")]
        if len(parts) < 3:
            continue
        number = re.sub(r"\D", "", parts[0])
        if not number:
            continue
        request = parts[2].replace("`", "")
        request = re.sub(r"<br\s*/?>", "\n", request, flags=re.IGNORECASE).replace("\\n", "\n")
        cases.append({"number": number, "category": parts[1], "request": request})
    return cases


def split_request(request: str) -> tuple[str, str]:
    selection_markers = [" 데이터", " 광고", " row", "에서", "중"]
    split_at = -1
    for marker in selection_markers:
        index = request.find(marker)
        if index > 0:
            split_at = max(split_at, index + len(marker))
            break
    if split_at <= 0:
        return "DA/SA 데이터에서 대상 범위를 찾아줘.", request
    return f"{request[:split_at].strip()}만 조회해줘.", request


def semantic_errors(case_number: str, summary: dict[str, Any]) -> list[str]:
    expected = EXPECTED_CASES.get(case_number, {})
    errors: list[str] = []
    sql = str(summary.get("sql") or "")
    for fragment in expected.get("sql_contains", []):
        if fragment not in sql:
            errors.append(f"expected_sql_fragment_missing: {fragment}")
    expected_status = expected.get("expected_status")
    if expected_status and summary.get("status") != expected_status:
        errors.append(f"expected_status_{expected_status}_got_{summary.get('status')}")
    required_set_columns = set(expected.get("required_set_columns", []))
    allowed_set_columns = set(expected.get("allowed_set_columns", required_set_columns))
    if required_set_columns or allowed_set_columns:
        set_columns = set(extract_update_set_columns(sql))
        for column in sorted(set_columns - allowed_set_columns):
            errors.append(f"unexpected_set_column: {column}")
        for column in sorted(required_set_columns - set_columns):
            errors.append(f"missing_set_column: {column}")
    return errors


def is_expected_result(row: dict[str, Any]) -> bool:
    status = row["summary"].get("status")
    raw_status = row["summary"].get("raw_status", status)
    expected_status = EXPECTED_CASES.get(row["number"], {}).get("expected_status")
    if expected_status:
        return raw_status == expected_status and status == expected_status
    return status == "PASS"


def has_valid_zero_row_preview(summary: dict[str, Any]) -> bool:
    return (
        summary.get("validation_status") == "passed"
        and summary.get("preview_status") == "pending_user_confirmation"
        and int(summary.get("affected_row_count") or 0) == 0
        and int(summary.get("previewed_row_count") or 0) == 0
    )


def fallback_summary_error(summary: dict[str, Any]) -> str:
    if summary.get("reason"):
        return str(summary["reason"])
    validation_status = summary.get("validation_status") or "missing"
    preview_status = summary.get("preview_status") or "missing"
    if not summary.get("sql"):
        return f"query_not_generated: validation={validation_status}, preview={preview_status}"
    if preview_status != "pending_user_confirmation":
        return f"preview_not_ready: validation={validation_status}, preview={preview_status}"
    return f"step_not_passed: validation={validation_status}, preview={preview_status}"


def semantic_review_status() -> str:
    return "omo_review_required"


def semantic_review_note() -> str:
    return "OMO must judge natural-language request, SQL, and sample rows."


def summarize_result(case_number: str, result: dict[str, Any], request_text: str = "") -> dict[str, Any]:
    output = result.get("output_json", {})
    query = output.get("query_from_ir", {})
    examples = output.get("row_modification_examples", {})
    validation_status = query.get("validation_result", {}).get("status")
    preview_status = examples.get("status")
    if result.get("errors"):
        status = "CHECK"
    elif validation_status == "passed" and preview_status == "pending_user_confirmation":
        status = "PASS"
    else:
        status = "ERROR"
    summary = {
        "status": status,
        "raw_status": status,
        "intent": output.get("ir_structured_json", {}).get("intent_type"),
        "sql_type": query.get("sql_type"),
        "target_table": query.get("target_table"),
        "validation_status": validation_status,
        "preview_status": preview_status,
        "affected_row_count": examples.get("affected_row_count", 0),
        "previewed_row_count": examples.get("previewed_row_count", 0),
        "sql": query.get("sql", ""),
        "errors": result.get("errors", []),
        "reason": query.get("reason"),
        "sample_rows": examples.get("sample_rows", []),
        "predicate": query.get("predicate", []),
        "referenced_columns": query.get("referenced_columns", []),
    }
    summary["semantic_status"] = semantic_review_status()
    summary["semantic_errors"] = []
    summary["semantic_review_note"] = semantic_review_note()
    if not summary.get("sql"):
        summary["status"] = "CHECK"
        summary["errors"] = [*summary["errors"], "query_not_generated"]
    if summary["status"] == "PASS" and int(summary.get("previewed_row_count") or 0) <= 0 and not has_valid_zero_row_preview(summary):
        summary["status"] = "CHECK"
        summary["errors"] = [*summary["errors"], "sample_preview_missing"]
    if summary["status"] != "PASS" and not summary.get("errors"):
        summary["errors"] = [fallback_summary_error(summary)]
    return summary


def summarize_multistep_result(case_number: str, result: dict[str, Any], selection_text: str, modification_text: str) -> dict[str, Any] | None:
    steps = result.get("workflow_steps") or result.get("output_json", {}).get("workflow_steps", [])
    if len(steps) <= 1:
        return None
    step_summaries: list[dict[str, Any]] = []
    accepted_step_ids: set[str] = set()
    step_preview_results: dict[str, dict[str, Any]] = {}
    with connect_db() as connection:
        linked_plan_id = ensure_linked_plan(connection, selection_text, modification_text, steps)
    for index, step in enumerate(steps, start=1):
        step_id = str(step.get("step_id") or step.get("group_id"))
        try:
            step_result = run_graph(
                selection_text,
                modification_text,
                [],
                approved=False,
                ir_override=filtered_ir_for_step(result, step_id),
                active_step_id=step_id,
                effective_preview_context=effective_preview_context_for_step(steps, step_id, accepted_step_ids, step_preview_results, linked_plan_id),
                linked_plan_id=linked_plan_id,
            )
            step_summary = summarize_result(f"{case_number}.{index}", step_result, modification_text)
            if step_summary.get("status") == "PASS":
                step_preview_results[step_id] = {**step_result, "preview_delta_items": preview_delta_items_from_result(step_result, "approved")}
                accepted_step_ids.add(step_id)
                with connect_db() as connection:
                    update_delta_status(connection, linked_plan_id, step_id, "approved")
        except Exception as exc:
            step_summary = {
                "status": "ERROR",
                "raw_status": "ERROR",
                "intent": step.get("intent_type"),
                "sql_type": None,
                "target_table": None,
                "validation_status": "failed",
                "preview_status": "blocked_by_exception",
                "affected_row_count": 0,
                "previewed_row_count": 0,
                "sql": "",
                "errors": [str(exc)],
                "semantic_status": semantic_review_status(),
                "semantic_errors": [],
                "semantic_review_note": semantic_review_note(),
            }
        step_summaries.append({"step_id": step_id, **step_summary})
    errors = []
    for item in step_summaries:
        if item.get("status") == "PASS":
            continue
        step_errors = item.get("errors", []) or [fallback_summary_error(item)]
        item["errors"] = step_errors
        errors.append(f"{item['step_id']}: {'; '.join(step_errors)}")
    missing_samples = [
        str(item["step_id"])
        for item in step_summaries
        if int(item.get("previewed_row_count") or 0) <= 0 and not has_valid_zero_row_preview(item)
    ]
    if missing_samples:
        errors.append(f"step_sample_preview_missing: {', '.join(missing_samples)}")
    return {
        "status": "PASS" if not errors else "CHECK",
        "raw_status": "PASS" if not errors else "CHECK",
        "intent": "MULTI_STEP",
        "sql_type": "STEP_PREVIEW",
        "target_table": ",".join(sorted({str(item.get("target_table")) for item in step_summaries if item.get("target_table")})),
        "validation_status": "passed" if not errors else "check_step_results",
        "preview_status": "step_previews_generated",
        "affected_row_count": sum(int(item.get("affected_row_count") or 0) for item in step_summaries),
        "previewed_row_count": sum(int(item.get("previewed_row_count") or 0) for item in step_summaries),
        "sql": "\n\n".join(str(item.get("sql") or "") for item in step_summaries if item.get("sql")),
        "errors": errors,
        "semantic_status": semantic_review_status(),
        "semantic_errors": [],
        "semantic_review_note": "OMO must judge each step; all steps must be semantically correct for linked-question semantic PASS.",
        "step_summaries": step_summaries,
    }


def markdown_escape(value: Any) -> str:
    text = "" if value is None else str(value)
    return text.replace("|", "\\|").replace("\n", " ")


def render_sample_rows_table(sample_rows: list[dict[str, Any]], max_columns: int = 12) -> list[str]:
    if not sample_rows:
        return []
    columns: list[str] = []
    for row in sample_rows:
        for key in row.keys():
            if key not in columns:
                columns.append(key)
    change_columns = [column for column in columns if column.startswith("before.") or column.startswith("after.")]
    base_columns = [column for column in columns if column not in change_columns]
    columns = unique_preserve_order([*base_columns[:max_columns], *change_columns])
    lines = [
        "",
        "**샘플 데이터 row**",
        "",
        "로컬 확인 결과에서 생성된 값을 숨기지 않고 표시합니다.",
        "",
        "| " + " | ".join(markdown_escape(column) for column in columns) + " |",
        "| " + " | ".join("---" for _ in columns) + " |",
    ]
    for row in sample_rows:
        lines.append("| " + " | ".join(markdown_escape(row.get(column)) for column in columns) + " |")
    return lines


def render_markdown(rows: list[dict[str, Any]], model_label: str) -> str:
    lines = [
        "# LangGraph 적용 전 확인 테스트 결과",
        "",
        f"Model: {model_label}",
        "",
        "모든 케이스는 적용 전 확인만 수행했습니다. 승인 값은 전달하지 않았고 쓰기 명령은 실행하지 않았습니다.",
        "",
        "| 번호 | 구분 | 결과 | Intent | SQL Type | Table | 영향 데이터 수 | 표시 샘플 수 | Errors |",
        "| ---: | --- | --- | --- | --- | --- | ---: | ---: | --- |",
    ]
    for row in rows:
        errors = "; ".join(row["summary"].get("errors", []))
        lines.append(
            f"| {row['number']} | {row['category']} | {row['summary']['status']} | {row['summary'].get('intent')} | "
            f"{row['summary'].get('sql_type')} | {row['summary'].get('target_table')} | "
            f"{row['summary'].get('affected_row_count')} | {row['summary'].get('previewed_row_count')} | {errors} |"
        )
    lines.append("")
    for row in rows:
        sql_text = row["summary"].get("sql") or f"-- SQL not generated\n-- reason: {'; '.join(row['summary'].get('errors', [])) or row['summary'].get('reason') or 'unknown'}"
        lines.extend(
            [
                f"## {row['number']}. {row['category']}",
                "",
                f"**Request**: {row['request']}",
                "",
                f"**Result**: {row['summary']['status']} / intent={row['summary'].get('intent')} / validation={row['summary'].get('validation_status')}",
                f"**Semantic**: {row['summary'].get('semantic_status', 'not_evaluated')} / note={row['summary'].get('semantic_review_note', '')} / errors={'; '.join(row['summary'].get('semantic_errors', []))}",
                "",
                "```sql",
                sql_text,
                "```",
                "",
            ]
        )
        lines.extend(render_sample_rows_table(row["summary"].get("sample_rows", [])))
        if row["summary"].get("sample_rows"):
            lines.append("")
        for step in row["summary"].get("step_summaries", []):
            step_sql = str(step.get("sql") or "-- SQL not generated")
            lines.extend(
                [
                    f"### 요청 묶음 {step.get('step_id')}",
                    "",
                    f"**Step Query Result**: {step.get('status')} / intent={step.get('intent')} / validation={step.get('validation_status')} / 표시 샘플 수={step.get('previewed_row_count')} / errors={'; '.join(step.get('errors', []))}",
                    f"**Step Semantic**: {step.get('semantic_status', 'not_evaluated')} / note={step.get('semantic_review_note', '')} / errors={'; '.join(step.get('semantic_errors', []))}",
                    "",
                    "```sql",
                    step_sql,
                    "```",
                    "",
                ]
            )
            lines.extend(render_sample_rows_table(step.get("sample_rows", [])))
            if step.get("sample_rows"):
                lines.append("")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run preview-only LangGraph workflow test cases.")
    parser.add_argument("--input", type=Path, default=TEST_FILE)
    parser.add_argument("--output", type=Path, default=DEFAULT_RESULT_FILE)
    parser.add_argument("--model-label", default="local-model (llama.cpp port 8000)")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    rows: list[dict[str, Any]] = []
    for case in parse_cases(args.input):
        selection_text, modification_text = split_request(case["request"])
        try:
            result = run_graph(selection_text, modification_text, [], approved=False)
            summary = summarize_multistep_result(case["number"], result, selection_text, modification_text) or summarize_result(case["number"], result, modification_text)
        except Exception as exc:
            summary = {
                "status": "ERROR",
                "raw_status": "ERROR",
                "intent": None,
                "sql_type": None,
                "target_table": None,
                "validation_status": "failed",
                "preview_status": "blocked_by_exception",
                "affected_row_count": 0,
                "previewed_row_count": 0,
                "sql": "",
                "errors": [str(exc)],
            }
        rows.append({**case, "selection_text": selection_text, "summary": summary})
        print(f"{case['number']}: {summary['status']} {summary.get('intent')} {summary.get('sql_type')}")
    args.output.write_text(render_markdown(rows, args.model_label), encoding="utf-8")
    print(f"Wrote {args.output}")
    return 0 if all(is_expected_result(row) for row in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
