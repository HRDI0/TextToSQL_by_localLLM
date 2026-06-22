#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import run_langgraph_test_cases as runner


@dataclass(frozen=True)
class SuiteMetrics:
    total: int
    pass_count: int
    semantic_pass_count: int
    semantic_fail_count: int
    semantic_pending_count: int

    @property
    def check_count(self) -> int:
        return self.total - self.pass_count

    @property
    def pass_rate(self) -> float:
        return percentage(self.pass_count, self.total)

    @property
    def semantic_pass_rate(self) -> float:
        return percentage(self.semantic_pass_count, self.total)


@dataclass(frozen=True)
class SuiteRun:
    label: str
    input_path: Path
    raw_path: Path
    analysis_path: Path
    rows: list[dict[str, Any]]
    metrics: SuiteMetrics


@dataclass(frozen=True)
class PairContext:
    cycle_number: int
    pass_average: float
    semantic_pass_average: float
    passed_threshold: bool
    consecutive_successes: int
    required_streak: int
    threshold: float


def percentage(part: int, total: int) -> float:
    if total <= 0:
        return 0.0
    return part / total * 100


def timestamp_prefix() -> str:
    return datetime.now().strftime("%Y_%m_%d_%H_%M")


def project_path(path: Path) -> Path:
    return path if path.is_absolute() else runner.PROJECT_ROOT / path


def relative_to_project(path: Path) -> str:
    try:
        return str(path.relative_to(runner.PROJECT_ROOT))
    except ValueError:
        return str(path)


def unique_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        unique.append(value)
    return unique


def suite_file_paths(output_dir: Path, cycle_number: int, label: str) -> tuple[Path, Path]:
    prefix = timestamp_prefix()
    stem = f"{prefix}_cycle_{cycle_number:03d}_{label}"
    return output_dir / f"{stem}_raw.md", output_dir / f"{stem}_analysis.md"


def exception_summary(exc: Exception) -> dict[str, Any]:
    return {
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
        "semantic_status": runner.semantic_review_status(),
        "semantic_errors": [],
        "semantic_review_note": runner.semantic_review_note(),
    }


def run_suite(input_path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for case in runner.parse_cases(input_path):
        selection_text, modification_text = runner.split_request(case["request"])
        try:
            result = runner.run_graph(selection_text, modification_text, [], approved=False)
            summary = runner.summarize_multistep_result(
                case["number"],
                result,
                selection_text,
                modification_text,
            ) or runner.summarize_result(case["number"], result, modification_text)
        except Exception as exc:
            summary = exception_summary(exc)
        rows.append({**case, "selection_text": selection_text, "summary": summary})
        print(f"{input_path.name} {case['number']}: {summary['status']} {summary.get('intent')} {summary.get('sql_type')}")
    return rows


def calculate_metrics(rows: list[dict[str, Any]]) -> SuiteMetrics:
    pass_count = sum(1 for row in rows if row["summary"].get("status") == "PASS")
    semantic_pass_count = sum(1 for row in rows if row["summary"].get("semantic_status") == "passed")
    semantic_fail_count = sum(1 for row in rows if row["summary"].get("semantic_status") == "failed")
    semantic_pending_count = sum(
        1
        for row in rows
        if row["summary"].get("semantic_status") in {"omo_review_required", "pending", "not_evaluated"}
    )
    return SuiteMetrics(
        total=len(rows),
        pass_count=pass_count,
        semantic_pass_count=semantic_pass_count,
        semantic_fail_count=semantic_fail_count,
        semantic_pending_count=semantic_pending_count,
    )


def render_check_cases(rows: list[dict[str, Any]]) -> list[str]:
    lines = [
        "## CHECK 케이스",
        "",
    ]
    check_rows = [row for row in rows if row["summary"].get("status") != "PASS" or row["summary"].get("semantic_status") != "passed"]
    if not check_rows:
        lines.append("없음.")
        return lines

    lines.extend(
        [
            "| 번호 | 구분 | 결과 | Semantic | 주요 오류 |",
            "| ---: | --- | --- | --- | --- |",
        ]
    )
    for row in check_rows:
        summary = row["summary"]
        errors = unique_preserve_order([*(summary.get("errors") or []), *(summary.get("semantic_errors") or [])])
        lines.append(
            f"| {row['number']} | {runner.markdown_escape(row['category'])} | "
            f"{summary.get('status')} | {summary.get('semantic_status', 'not_evaluated')} | "
            f"{runner.markdown_escape('; '.join(errors))} |"
        )
    return lines


def render_analysis(run: SuiteRun, model_label: str, pair_context: PairContext) -> str:
    metrics = run.metrics
    lines = [
        f"# {run.label} cycle {pair_context.cycle_number:03d} semantic 분석 보고서",
        "",
        "## 테스트 결과",
        "",
        f"- 실행 모델: `{model_label}`",
        f"- 입력 파일: `{relative_to_project(run.input_path)}`",
        f"- 원본 결과 파일: `{relative_to_project(run.raw_path)}`",
        "- 실행 방식: 승인값 없이 적용 전 확인만 수행. DB write 실행 없음.",
        f"- 전체 결과: {metrics.total}개 중 {metrics.pass_count}개 PASS, {metrics.check_count}개 CHECK",
        f"- 성공률: {metrics.pass_rate:.1f}%",
        f"- semantic PASS: {metrics.semantic_pass_count}/{metrics.total} ({metrics.semantic_pass_rate:.1f}%)",
        f"- semantic FAIL: {metrics.semantic_fail_count}/{metrics.total}",
        f"- OMO semantic review pending: {metrics.semantic_pending_count}/{metrics.total}",
        "",
        "## Paired cycle 판정",
        "",
        f"- paired PASS 평균: {pair_context.pass_average:.1f}%",
        f"- paired semantic PASS 평균: {pair_context.semantic_pass_average:.1f}% (OMO 검토 완료분만 PASS로 집계)",
        f"- 기준: query PASS 평균 {pair_context.threshold:.1f}% 초과. semantic PASS는 자동 threshold에 사용하지 않음.",
        f"- 이번 paired cycle 결과: {'PASS' if pair_context.passed_threshold else 'CHECK'}",
        f"- 연속 성공 횟수: {pair_context.consecutive_successes}/{pair_context.required_streak}",
        "",
    ]
    lines.extend(render_check_cases(run.rows))
    lines.extend(
        [
            "",
            "## 판정 기준",
            "",
            "- PASS 비율은 runner summary status가 `PASS`인 케이스 수를 전체 케이스 수로 나눈 값이다.",
            "- semantic PASS는 OMO가 raw를 읽고 `semantic_status == passed`로 기록한 케이스만 계산한다.",
            "- `omo_review_required`, `pending`, `not_evaluated`는 semantic PASS로 계산하지 않는다.",
        ]
    )
    return "\n".join(lines)


def execute_suite(label: str, input_path: Path, output_dir: Path, cycle_number: int, model_label: str) -> SuiteRun:
    raw_path, analysis_path = suite_file_paths(output_dir, cycle_number, label)
    rows = run_suite(input_path)
    raw_path.write_text(runner.render_markdown(rows, model_label), encoding="utf-8")
    metrics = calculate_metrics(rows)
    print(
        f"Wrote {relative_to_project(raw_path)} "
        f"PASS={metrics.pass_rate:.1f}% semantic={metrics.semantic_pass_rate:.1f}%"
    )
    return SuiteRun(
        label=label,
        input_path=input_path,
        raw_path=raw_path,
        analysis_path=analysis_path,
        rows=rows,
        metrics=metrics,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run alternating LangGraph semantic test cycles.")
    parser.add_argument("--first-input", type=Path, default=runner.PROJECT_ROOT / "test" / "test.md")
    parser.add_argument("--second-input", type=Path, default=runner.PROJECT_ROOT / "test" / "test2.md")
    parser.add_argument("--output-dir", type=Path, default=runner.PROJECT_ROOT / "test" / "50_test_api")
    parser.add_argument("--model-label", default="gemini-3.1-flash-lite (native, alternating semantic cycle)")
    parser.add_argument("--threshold", type=float, default=90.0)
    parser.add_argument("--required-streak", type=int, default=10)
    parser.add_argument("--max-cycles", type=int, default=0, help="Optional safety cap. 0 means run until the required streak is reached.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    output_dir = project_path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    first_input = project_path(args.first_input)
    second_input = project_path(args.second_input)

    cycle_number = 1
    consecutive_successes = 0
    while consecutive_successes < args.required_streak:
        if args.max_cycles > 0 and cycle_number > args.max_cycles:
            print(f"Stopped after max cycles: {args.max_cycles}")
            return 1
        print(f"Starting paired cycle {cycle_number:03d}")
        runs = [
            execute_suite("test", first_input, output_dir, cycle_number, args.model_label),
            execute_suite("test2", second_input, output_dir, cycle_number, args.model_label),
        ]
        pass_average = sum(run.metrics.pass_rate for run in runs) / len(runs)
        semantic_pass_average = sum(run.metrics.semantic_pass_rate for run in runs) / len(runs)
        passed_threshold = pass_average > args.threshold
        consecutive_successes = consecutive_successes + 1 if passed_threshold else 0
        pair_context = PairContext(
            cycle_number=cycle_number,
            pass_average=pass_average,
            semantic_pass_average=semantic_pass_average,
            passed_threshold=passed_threshold,
            consecutive_successes=consecutive_successes,
            required_streak=args.required_streak,
            threshold=args.threshold,
        )
        for run in runs:
            run.analysis_path.write_text(render_analysis(run, args.model_label, pair_context), encoding="utf-8")
            print(f"Wrote {relative_to_project(run.analysis_path)}")
        print(
            f"Cycle {cycle_number:03d}: paired PASS={pass_average:.1f}% "
            f"semantic={semantic_pass_average:.1f}% streak={consecutive_successes}/{args.required_streak}"
        )
        cycle_number += 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
