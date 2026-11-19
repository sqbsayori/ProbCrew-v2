#!/usr/bin/env python3
"""ProbCrew 评测运行器 —— sample / dev / frozen 三层评测。

★ 报告只含 case_id、判定结果、错因集合差异与汇总；不回显学生答案、凭据或正式集答案。
★ dev 是阶段 B 的 20 道入仓开发集。默认严格比较 `expected_correct` 与
  `expected_mistake_ids`，输出首次判定正确率；全部符合时退出 0，否则退出 1。
★ frozen 目录须由非实现者提供；运行器本身不含正式集答案。若冻结案例缺少结构化
  `item`（或 `items.json`），报告明确列为人工复核，不伪造自动结论。
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.domain import judge, mistakes  # noqa: E402

CONTENT_DIR = REPO_ROOT / "content"


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _problems() -> "dict[str, dict[str, Any]]":
    path = CONTENT_DIR / "problems.json"
    if not path.exists():
        return {}
    return {item["prob_id"]: item for item in _load(path)["items"]}


def _mistakes() -> "dict[str, str]":
    path = CONTENT_DIR / "mistakes.json"
    if not path.exists():
        return {}
    return {item["mp_id"]: item["category"] for item in _load(path)["items"]}


def _resolve_item(case: Mapping[str, Any], problems: "Mapping[str, Mapping[str, Any]]") -> Mapping[str, Any]:
    inline = case.get("item")
    if isinstance(inline, Mapping):
        if inline.get("prob_id") != case.get("item_id"):
            raise ValueError(f"{case.get('case_id')}: item.prob_id 与 item_id 不一致")
        return inline
    item = problems.get(str(case.get("item_id") or ""))
    if item is None:
        raise ValueError(f"{case.get('case_id')}: item_id 不在题库中且没有内联 item")
    return item


def _judge_case(case: Mapping[str, Any], item: Mapping[str, Any]) -> "dict[str, Any]":
    result = judge.grade(str(case.get("answer") or ""), dict(item))
    hits = sorted(mistakes.match_mistake(str(case.get("answer") or ""), dict(item)))
    expected_correct = case.get("expected_correct")
    expected_ids = sorted(str(value) for value in case.get("expected_mistake_ids", []))
    actual_correct = result.get("correct") if result.get("graded") else None
    correct_ok = expected_correct is None or actual_correct == expected_correct
    mistakes_ok = not expected_ids or set(hits) == set(expected_ids)
    passed = bool(result.get("graded")) and correct_ok and mistakes_ok
    return {
        "case_id": case.get("case_id"),
        "item_id": case.get("item_id"),
        "status": "pass" if passed else "fail",
        "expected_correct": expected_correct,
        "actual_correct": actual_correct,
        "expected_mistake_ids": expected_ids,
        "actual_mistake_ids": hits,
        "grader": result.get("grader"),
    }


def _summary(rows: "list[dict[str, Any]]") -> "dict[str, Any]":
    total = len(rows)
    passed = sum(row["status"] == "pass" for row in rows)
    return {
        "total": total,
        "executed": total,
        "passed": passed,
        "failed": total - passed,
        "first_pass_accuracy": round(passed / total, 4) if total else None,
        "status": "pass" if total and passed == total else "fail",
    }


def _category_breakdown(
    cases: "list[Mapping[str, Any]]", rows: "list[dict[str, Any]]", mp_categories: "Mapping[str, str]"
) -> "dict[str, dict[str, int]]":
    row_by_id = {row["case_id"]: row for row in rows}
    output: "dict[str, dict[str, int]]" = defaultdict(lambda: {"total": 0, "passed": 0})
    for case in cases:
        categories = {
            mp_categories.get(mid, "unknown") for mid in case.get("expected_mistake_ids", []) if mid
        } or {"uncategorized"}
        for category in categories:
            output[category]["total"] += 1
            output[category]["passed"] += int(row_by_id[case["case_id"]]["status"] == "pass")
    return dict(sorted(output.items()))


def run_sample() -> "dict[str, Any]":
    paths = sorted((CONTENT_DIR / "eval" / "sample").glob("*.json"))
    cases = [case for path in paths for case in _load(path)["cases"]]
    problems = _problems()
    rows = [_judge_case(case, _resolve_item(case, problems)) for case in cases]
    # 样例集只做端到端回归：有结构化字段时严格比较，否则要求判定链真实执行。
    if not any("expected_correct" in case for case in cases):
        for row in rows:
            row["status"] = "pass" if row["actual_correct"] is not None else "fail"
    summary = _summary(rows)
    return {"suite": "sample", "summary": summary, "cases": rows}


def run_dev() -> "dict[str, Any]":
    path = CONTENT_DIR / "eval" / "dev" / "cases.json"
    document = _load(path)
    cases = document["cases"]
    problems = _problems()
    rows = [_judge_case(case, _resolve_item(case, problems)) for case in cases]
    summary = _summary(rows)
    return {
        "suite": "dev",
        "summary": summary,
        "categories": _category_breakdown(cases, rows, _mistakes()),
        "cases": rows,
    }


def run_frozen(frozen_dir: Path) -> "dict[str, Any]":
    if not frozen_dir.is_dir():
        raise FileNotFoundError(f"冻结目录不存在：{frozen_dir}")
    documents = [_load(path) for path in sorted(frozen_dir.rglob("*.json"))]
    criteria = next((doc for doc in documents if "answer_injection" in doc), None)
    if criteria is None:
        raise ValueError("冻结目录中没有 answer_injection 判据表")
    problems = _problems()
    for doc in documents:
        if isinstance(doc, Mapping) and "items" in doc:
            problems.update({item["prob_id"]: item for item in doc["items"]})
    answer_cases = list(criteria.get("answer_injection", {}).get("cases", []))
    rows = [_judge_case(case, _resolve_item(case, problems)) for case in answer_cases]
    answer_summary = _summary(rows)
    solution_cases = list(criteria.get("solution_injection", {}).get("cases", []))
    manual = [case.get("case_id") for case in solution_cases]
    summary = {
        **answer_summary,
        "status": "manual_review_required" if manual else answer_summary["status"],
        "solution_cases_manual": len(manual),
        "human_signature_required": True,
    }
    return {
        "suite": "frozen",
        "summary": summary,
        "answer_injection": rows,
        "manual_solution_case_ids": manual,
    }


def main(argv: "list[str] | None" = None) -> int:
    parser = argparse.ArgumentParser(prog="scripts/eval_verify.py")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--suite", choices=("sample", "dev"))
    group.add_argument("--frozen-dir", type=Path)
    args = parser.parse_args(argv)

    if args.frozen_dir is not None:
        report = run_frozen(args.frozen_dir)
        exit_code = 0 if report["summary"].get("status") == "pass" else 1
    elif args.suite == "dev":
        report = run_dev()
        exit_code = 0 if report["summary"]["status"] == "pass" else 1
    else:
        report = run_sample()
        exit_code = 0 if report["summary"]["status"] == "pass" else 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
