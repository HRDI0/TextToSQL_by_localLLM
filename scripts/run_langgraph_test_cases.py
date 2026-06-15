#!/usr/bin/env python3
from __future__ import annotations

import re
import sys
import argparse
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
load_dotenv(PROJECT_ROOT / ".env")

from app.streamlit_langgraph_test import run_graph  # noqa: E402


TEST_FILE = PROJECT_ROOT / "test" / "test.md"
DEFAULT_RESULT_FILE = PROJECT_ROOT / "test" / "test_result.md"

EXPECTED_CASES: dict[str, dict[str, Any]] = {
    "1": {"required_set_columns": ["노출수"], "allowed_set_columns": ["노출수"]},
    "2": {"required_set_columns": ["클릭수"], "allowed_set_columns": ["클릭수"]},
    "3": {"required_set_columns": ["비용"], "allowed_set_columns": ["비용"]},
    "4": {"required_set_columns": ["클릭수"], "allowed_set_columns": ["클릭수"]},
    "5": {"required_set_columns": ["노출수"], "allowed_set_columns": ["노출수"]},
    "6": {"required_set_columns": ["클릭수"], "allowed_set_columns": ["클릭수"]},
    "7": {"required_set_columns": ["비용"], "allowed_set_columns": ["비용"]},
    "8": {"required_set_columns": ["노출수"], "allowed_set_columns": ["노출수"]},
    "9": {"required_set_columns": ["클릭수"], "allowed_set_columns": ["클릭수"]},
    "10": {"required_set_columns": ["비용"], "allowed_set_columns": ["비용"]},
    "11": {"sql_contains": ["INSERT INTO `rule_engine_derived_value`", "ON DUPLICATE KEY UPDATE"]},
    "12": {"sql_contains": ["INSERT INTO `rule_engine_derived_value`", "ON DUPLICATE KEY UPDATE"]},
    "13": {"sql_contains": ["INSERT INTO `rule_engine_derived_value`", "ON DUPLICATE KEY UPDATE"]},
    "14": {"sql_contains": ["INSERT INTO `rule_engine_derived_value`", "ON DUPLICATE KEY UPDATE"]},
    "15": {"sql_contains": ["INSERT INTO `rule_engine_derived_value`", "ON DUPLICATE KEY UPDATE"]},
}


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
        cases.append({"number": number, "category": parts[1], "request": parts[2].replace("`", "")})
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


def summarize_result(case_number: str, result: dict[str, Any]) -> dict[str, Any]:
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
    }
    semantic = semantic_errors(case_number, summary)
    if semantic:
        summary["status"] = "CHECK"
        summary["errors"] = [*summary["errors"], *semantic]
    return summary


def markdown_escape(value: Any) -> str:
    text = "" if value is None else str(value)
    return text.replace("|", "\\|").replace("\n", " ")


def redact_report_value(key: str, value: Any) -> Any:
    if value in {None, ""}:
        return value
    if key == "row_index" or key.startswith("before.") or key.startswith("after."):
        return value
    return "<redacted>"


def redact_sql_literals_for_report(sql: str) -> str:
    return re.sub(r"'([^']|'')*'", "'<redacted>'", sql)


def render_sample_rows_table(sample_rows: list[dict[str, Any]], max_columns: int = 12) -> list[str]:
    if not sample_rows:
        return []
    columns: list[str] = []
    for row in sample_rows:
        for key in row.keys():
            if key not in columns:
                columns.append(key)
    columns = columns[:max_columns]
    lines = [
        "",
        "**샘플 데이터 row**",
        "",
        "Persisted report values are redacted; use Streamlit for local, approval-gated row inspection.",
        "",
        "| " + " | ".join(markdown_escape(column) for column in columns) + " |",
        "| " + " | ".join("---" for _ in columns) + " |",
    ]
    for row in sample_rows:
        lines.append("| " + " | ".join(markdown_escape(redact_report_value(column, row.get(column))) for column in columns) + " |")
    return lines


def render_markdown(rows: list[dict[str, Any]], model_label: str) -> str:
    lines = [
        "# LangGraph Workflow Test Results",
        "",
        f"Model: {model_label}",
        "",
        "All cases are preview-only. No approval was sent and no write SQL was executed.",
        "",
        "| 번호 | 구분 | 결과 | Intent | SQL Type | Table | Affected | Previewed | Errors |",
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
        sql_text = redact_sql_literals_for_report(sql_text)
        lines.extend(
            [
                f"## {row['number']}. {row['category']}",
                "",
                f"**Request**: {row['request']}",
                "",
                f"**Result**: {row['summary']['status']} / intent={row['summary'].get('intent')} / validation={row['summary'].get('validation_status')}",
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
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run preview-only LangGraph workflow test cases.")
    parser.add_argument("--output", type=Path, default=DEFAULT_RESULT_FILE)
    parser.add_argument("--model-label", default="local-model (llama.cpp port 8000)")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    rows: list[dict[str, Any]] = []
    for case in parse_cases(TEST_FILE):
        selection_text, modification_text = split_request(case["request"])
        try:
            result = run_graph(selection_text, modification_text, [], approved=False)
            summary = summarize_result(case["number"], result)
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
