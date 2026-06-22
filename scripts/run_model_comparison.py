#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TEST_OUTPUT_DIR = PROJECT_ROOT / "test" / "50_test_api"
COMPARISON_DIR = PROJECT_ROOT / "test" / "비교분석"
FLOWCHART_SOURCE = PROJECT_ROOT / "docs" / "langgraph_architecture" / "workflow_architecture.svg"


@dataclass(frozen=True)
class ModelProfile:
    key: str
    label: str
    env: dict[str, str]


@dataclass(frozen=True)
class RunMetrics:
    model_key: str
    model_label: str
    suite: str
    run_number: int
    raw_path: Path
    analysis_path: Path
    total: int
    pass_count: int
    semantic_pass_count: int
    semantic_fail_count: int
    semantic_pending_count: int
    step_semantic_pending_count: int
    step_count: int
    step_semantic_reviewed_count: int

    @property
    def pass_rate(self) -> float:
        return percentage(self.pass_count, self.total)

    @property
    def semantic_pass_rate(self) -> float:
        return percentage(self.semantic_pass_count, self.total)


def percentage(part: int, total: int) -> float:
    return part / total * 100 if total else 0.0


def timestamp() -> str:
    return datetime.now().strftime("%Y_%m_%d_%H_%M")


def relative(path: Path) -> str:
    try:
        return str(path.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


def safe_stem(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_").lower()


def parse_raw_report(path: Path, model_key: str, model_label: str, suite: str, run_number: int, analysis_path: Path) -> RunMetrics:
    text = path.read_text(encoding="utf-8")
    total = 0
    pass_count = 0
    for line in text.splitlines():
        match = re.match(r"^\|\s*(\d+)\s*\|.*?\|\s*(PASS|CHECK|ERROR)\s*\|", line)
        if not match:
            continue
        total += 1
        if match.group(2) == "PASS":
            pass_count += 1
    semantic_pass_count = len(re.findall(r"^\*\*Semantic\*\*:\s*passed\b", text, flags=re.MULTILINE))
    semantic_fail_count = len(re.findall(r"^\*\*Semantic\*\*:\s*failed\b", text, flags=re.MULTILINE))
    semantic_pending_count = len(re.findall(r"^\*\*Semantic\*\*:\s*(?:omo_review_required|pending|not_evaluated)\b", text, flags=re.MULTILINE))
    step_semantic_pending_count = len(
        re.findall(r"^\*\*Step Semantic\*\*:\s*(?:omo_review_required|pending|not_evaluated)\b", text, flags=re.MULTILINE)
    )
    step_count = len(re.findall(r"^### 요청 묶음\s+", text, flags=re.MULTILINE))
    step_semantic_reviewed_count = len(re.findall(r"^\*\*Step Semantic\*\*:\s*(?:passed|failed)\b", text, flags=re.MULTILINE))
    return RunMetrics(
        model_key=model_key,
        model_label=model_label,
        suite=suite,
        run_number=run_number,
        raw_path=path,
        analysis_path=analysis_path,
        total=total,
        pass_count=pass_count,
        semantic_pass_count=semantic_pass_count,
        semantic_fail_count=semantic_fail_count,
        semantic_pending_count=semantic_pending_count,
        step_semantic_pending_count=step_semantic_pending_count,
        step_count=step_count,
        step_semantic_reviewed_count=step_semantic_reviewed_count,
    )


def metadata_from_raw_path(path: Path) -> tuple[str, str, int]:
    match = re.search(r"_(?P<model>.+)_(?P<suite>test\d+)_run_(?P<run>\d+)_raw\.md$", path.name)
    if not match:
        raise ValueError(f"cannot_parse_raw_report_name: {path}")
    return match.group("model"), match.group("suite"), int(match.group("run"))


def metrics_from_reviewed_raw(paths: list[Path]) -> list[RunMetrics]:
    metrics: list[RunMetrics] = []
    for raw_path in sorted(paths):
        model_key, suite, run_number = metadata_from_raw_path(raw_path)
        analysis_path = raw_path.with_name(raw_path.name.replace("_raw.md", "_analysis.md"))
        metrics.append(parse_raw_report(raw_path, model_key, model_key, suite, run_number, analysis_path))
    return metrics


def render_analysis(metrics: RunMetrics) -> str:
    check_count = metrics.total - metrics.pass_count
    return "\n".join(
        [
            f"# {metrics.model_label} {metrics.suite} run {metrics.run_number} 분석 보고서",
            "",
            "## 테스트 결과",
            "",
            f"- 실행 모델: `{metrics.model_label}`",
            f"- 테스트 세트: `{metrics.suite}`",
            f"- 원본 결과 파일: `{relative(metrics.raw_path)}`",
            "- 실행 방식: 승인값 없이 적용 전 확인만 수행. DB write 실행 없음.",
            f"- 전체 결과: {metrics.total}개 중 {metrics.pass_count}개 PASS, {check_count}개 CHECK/ERROR",
            f"- 성공률: {metrics.pass_rate:.1f}%",
            f"- semantic PASS: {metrics.semantic_pass_count}/{metrics.total} ({metrics.semantic_pass_rate:.1f}%)",
            f"- semantic FAIL: {metrics.semantic_fail_count}/{metrics.total}",
            f"- OMO semantic review pending: {metrics.semantic_pending_count}",
            f"- OMO step semantic review pending: {metrics.step_semantic_pending_count}",
            f"- OMO step semantic reviewed: {metrics.step_semantic_reviewed_count}/{metrics.step_count}",
            "",
        ]
    )


def run_suite(profile: ModelProfile, suite: str, input_path: Path, run_number: int) -> RunMetrics:
    TEST_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    prefix = timestamp()
    stem = f"{prefix}_{safe_stem(profile.key)}_{suite}_run_{run_number:02d}"
    raw_path = TEST_OUTPUT_DIR / f"{stem}_raw.md"
    analysis_path = TEST_OUTPUT_DIR / f"{stem}_analysis.md"
    env = os.environ.copy()
    env.update(profile.env)
    command = [
        sys.executable,
        str(PROJECT_ROOT / "scripts" / "run_langgraph_test_cases.py"),
        "--input",
        str(input_path),
        "--output",
        str(raw_path),
        "--model-label",
        profile.label,
    ]
    result = subprocess.run(command, cwd=PROJECT_ROOT, env=env, text=True)
    if result.returncode not in {0, 1}:
        raise RuntimeError(f"runner_failed: {profile.key} {suite} run {run_number} exit={result.returncode}")
    metrics = parse_raw_report(raw_path, profile.key, profile.label, suite, run_number, analysis_path)
    analysis_path.write_text(render_analysis(metrics), encoding="utf-8")
    return metrics


def ensure_semantic_review_complete(metrics: list[RunMetrics]) -> None:
    problems: list[str] = []
    for item in metrics:
        reviewed_count = item.semantic_pass_count + item.semantic_fail_count
        if item.semantic_pending_count:
            problems.append(f"{item.model_key}/{item.suite}/run{item.run_number:02d}: {item.semantic_pending_count} pending top-level")
        if item.step_semantic_pending_count:
            problems.append(f"{item.model_key}/{item.suite}/run{item.run_number:02d}: {item.step_semantic_pending_count} pending step")
        if item.step_semantic_reviewed_count != item.step_count:
            problems.append(
                f"{item.model_key}/{item.suite}/run{item.run_number:02d}: reviewed step semantic {item.step_semantic_reviewed_count}/{item.step_count}"
            )
        if reviewed_count != item.total:
            problems.append(
                f"{item.model_key}/{item.suite}/run{item.run_number:02d}: reviewed top-level semantic {reviewed_count}/{item.total}"
            )
    if problems:
        raise RuntimeError(
            "semantic_review_incomplete: OMO must update every top-level Semantic line to passed or failed, "
            "and every Step Semantic line must be reviewed before comparison graphs are generated. "
            + "; ".join(problems)
        )


def summarize(metrics: list[RunMetrics]) -> dict[tuple[str, str], dict[str, float]]:
    summary: dict[tuple[str, str], dict[str, float]] = {}
    for item in metrics:
        key = (item.model_key, item.suite)
        bucket = summary.setdefault(key, {"runs": 0, "pass_rate": 0.0, "semantic_pass_rate": 0.0})
        bucket["runs"] += 1
        bucket["pass_rate"] += item.pass_rate
        bucket["semantic_pass_rate"] += item.semantic_pass_rate
    for bucket in summary.values():
        runs = bucket["runs"] or 1
        bucket["pass_rate"] /= runs
        bucket["semantic_pass_rate"] /= runs
    return summary


def plot_metrics(metrics: list[RunMetrics], output_dir: Path) -> list[Path]:
    try:
        pd = importlib.import_module("pandas")
        plt = importlib.import_module("matplotlib.pyplot")
        sns = importlib.import_module("seaborn")
    except ImportError as exc:
        raise RuntimeError("plot_dependencies_missing: install matplotlib and seaborn inside .venv") from exc

    rows = [
        {
            "model": item.model_key,
            "suite": item.suite,
            "run": f"run {item.run_number:02d}",
            "PASS rate": item.pass_rate,
            "Semantic PASS rate": item.semantic_pass_rate,
        }
        for item in metrics
    ]
    data = pd.DataFrame(rows)
    sns.set_theme(style="whitegrid")
    outputs: list[Path] = []
    for metric in ["PASS rate", "Semantic PASS rate"]:
        suites = sorted(data["suite"].unique())
        figure, axes = plt.subplots(1, len(suites), figsize=(15, 7.5), sharey=True)
        if len(suites) == 1:
            axes = [axes]
        for index, (axis, suite) in enumerate(zip(axes, suites, strict=True)):
            suite_data = data[data["suite"] == suite]
            sns.barplot(data=suite_data, x="run", y=metric, hue="model", errorbar=None, ax=axis)
            axis.set_ylim(0, 100)
            axis.set_title(f"{suite} - {metric}")
            axis.set_ylabel("Rate (%)" if index == 0 else "")
            axis.set_xlabel("Run")
            for container in axis.containers:
                axis.bar_label(container, fmt="%.1f", padding=3)
            if index == len(suites) - 1:
                sns.move_legend(axis, "upper left", bbox_to_anchor=(1.01, 1), borderaxespad=0)
            else:
                legend = axis.get_legend()
                if legend:
                    legend.remove()
        figure.suptitle(f"Model comparison by run - {metric}")
        figure.tight_layout()
        output = output_dir / f"{safe_stem(metric)}_by_model_run.png"
        figure.savefig(output, dpi=180)
        plt.close(figure)
        outputs.append(output)
    return outputs


def copy_flowchart(output_dir: Path) -> Path | None:
    if not FLOWCHART_SOURCE.exists():
        return None
    target = output_dir / "langgraph_workflow_architecture.svg"
    shutil.copyfile(FLOWCHART_SOURCE, target)
    return target


def render_comparison_report(metrics: list[RunMetrics], plot_paths: list[Path], flowchart_path: Path | None) -> str:
    lines = [
        "# 모델 성능 비교 분석",
        "",
        "## 테스트 판정 기준",
        "",
        "- PASS는 코드가 생성 SQL의 validation 통과와 preview 준비 상태만 확인한 쿼리 실행 가능성 지표입니다.",
        "- semantic PASS는 OMO가 자연어 요청, SQL, 샘플 결과를 직접 보고 기록한 판단만 집계합니다.",
        "- 여러 step이 있는 연계 질문은 step별 query 결과와 semantic 판단을 기록하고, 모든 step이 올바를 때만 semantic PASS로 봅니다.",
        "- 그래프는 전체 평균이 아니라 모델별, 테스트별, run별 백분율을 표시합니다.",
    ]
    lines.extend(["", "## 실행별 성능", "", "| 모델 | 테스트 | 회차 | PASS | semantic PASS | semantic pending | step semantic reviewed | raw | analysis |", "| --- | --- | ---: | ---: | ---: | ---: | ---: | --- | --- |"])
    for item in metrics:
        lines.append(
            f"| {item.model_key} | {item.suite} | {item.run_number} | {item.pass_rate:.1f}% | {item.semantic_pass_rate:.1f}% | {item.semantic_pending_count} | {item.step_semantic_reviewed_count}/{item.step_count} | "
            f"[{item.raw_path.name}](../50_test_api/{item.raw_path.name}) | [{item.analysis_path.name}](../50_test_api/{item.analysis_path.name}) |"
        )
    lines.extend(["", "## 그래프", ""])
    for path in plot_paths:
        lines.append(f"![{path.stem}]({path.name})")
        lines.append("")
    if flowchart_path:
        lines.extend(["## LangGraph 구조도", "", f"![LangGraph workflow]({flowchart_path.name})", ""])
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Compare Gemini and Qwen LangGraph test performance.")
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--output-dir", type=Path, default=COMPARISON_DIR)
    parser.add_argument(
        "--reviewed-raw",
        type=Path,
        nargs="*",
        help="Build comparison report/graphs from existing OMO-reviewed raw markdown files without rerunning tests.",
    )
    parser.add_argument(
        "--allow-external-llm",
        action="store_true",
        help="Allow the Gemini API run. This can send live schema metadata and preview context to an external LLM.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    output_dir = args.output_dir if args.output_dir.is_absolute() else PROJECT_ROOT / args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    if args.reviewed_raw:
        raw_paths = [path if path.is_absolute() else PROJECT_ROOT / path for path in args.reviewed_raw]
        metrics = metrics_from_reviewed_raw(raw_paths)
        ensure_semantic_review_complete(metrics)
        plot_paths = plot_metrics(metrics, output_dir)
        flowchart_path = copy_flowchart(output_dir)
        report_path = output_dir / f"{timestamp()}_model_comparison.md"
        report_path.write_text(render_comparison_report(metrics, plot_paths, flowchart_path), encoding="utf-8")
        print(f"Wrote {relative(report_path)}")
        return 0
    if not args.allow_external_llm:
        raise SystemExit(
            "Refusing to run Gemini comparison without --allow-external-llm. "
            "Gemini runs can send live schema metadata and preview context to an external LLM."
        )
    profiles = [
        ModelProfile(
            key="gemini_3_1_flash_lite",
            label="gemini-3.1-flash-lite (native)",
            env={"SQL_WORKFLOW_LLM_PROVIDER": "gemini_native", "SQL_WORKFLOW_GEMINI_MODEL": "gemini-3.1-flash-lite"},
        ),
        ModelProfile(
            key="qwen3_14b_ud_q6_k",
            label="qwen3-14b UD Q6_K GGUF (llama.cpp)",
            env={
                "SQL_WORKFLOW_LLM_PROVIDER": "openai_compatible",
                "SQL_WORKFLOW_LLM_BASE_URL": "http://127.0.0.1:8000/v1",
                "SQL_WORKFLOW_LLM_MODEL": "qwen3-14b",
                "SQL_WORKFLOW_LLM_API_KEY": "EMPTY",
            },
        ),
    ]
    suites = {"test1": PROJECT_ROOT / "test" / "test.md", "test2": PROJECT_ROOT / "test" / "test2.md"}
    metrics: list[RunMetrics] = []
    for run_number in range(1, args.runs + 1):
        for profile in profiles:
            for suite, input_path in suites.items():
                metrics.append(run_suite(profile, suite, input_path, run_number))
    ensure_semantic_review_complete(metrics)
    plot_paths = plot_metrics(metrics, output_dir)
    flowchart_path = copy_flowchart(output_dir)
    report_path = output_dir / f"{timestamp()}_model_comparison.md"
    report_path.write_text(render_comparison_report(metrics, plot_paths, flowchart_path), encoding="utf-8")
    print(f"Wrote {relative(report_path)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
