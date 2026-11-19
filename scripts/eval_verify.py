#!/usr/bin/env python3
"""ProbCrew 评测运行器 —— sample / dev / frozen 三层评测。

★ 报告只含 case_id、判定结果、错因集合差异与汇总；不回显学生答案、凭据或正式集答案。
★ sample / dev 是入仓回归资产；frozen 由非实现者放在主仓之外并给出结构化判据。
★ frozen 报告包含执行时间、执行人、content_rev、逐类命中数和结论；结论仍需非实现者签署。
"""
from __future__ import annotations

import argparse
import getpass
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.domain import judge, mistakes  # noqa: E402
from app.learning import catalog  # noqa: E402
from app.tools import symbolic_equivalence, sympy_recompute  # noqa: E402

CONTENT_DIR = REPO_ROOT / "content"
FROZEN_SOLUTION_MIN = 20
FROZEN_SOLUTION_PASS_MIN = 18
FROZEN_CATEGORY_PASS_MIN = 4
FROZEN_AB_PASS_MIN = 8


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
    has_mistake_expectation = "expected_mistake_ids" in case
    expected_ids = sorted(str(value) for value in case.get("expected_mistake_ids", []))
    actual_correct = result.get("correct") if result.get("graded") else None
    correct_ok = expected_correct is None or actual_correct == expected_correct
    # ★ 显式空数组表示“必须未识别错因”；字段缺失才表示不做错因断言。
    mistakes_ok = (not has_mistake_expectation) or set(hits) == set(expected_ids)
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


def _case_category(case: Mapping[str, Any], mp_categories: "Mapping[str, str]") -> "str | None":
    explicit = case.get("category")
    if isinstance(explicit, str) and explicit:
        return explicit
    for mid in case.get("expected_mistake_ids", []):
        category = mp_categories.get(str(mid))
        if category:
            return category
    return None


def _category_breakdown(
    cases: "list[Mapping[str, Any]]", rows: "list[dict[str, Any]]", mp_categories: "Mapping[str, str]"
) -> "dict[str, dict[str, int]]":
    row_by_id = {row["case_id"]: row for row in rows}
    output: "dict[str, dict[str, int]]" = defaultdict(lambda: {"total": 0, "passed": 0})
    for case in cases:
        category = _case_category(case, mp_categories) or "uncategorized"
        output[category]["total"] += 1
        output[category]["passed"] += int(row_by_id[case["case_id"]]["status"] == "pass")
    return dict(sorted(output.items()))


def run_sample() -> "dict[str, Any]":
    paths = sorted((CONTENT_DIR / "eval" / "sample").glob("*.json"))
    cases = [case for path in paths for case in _load(path)["cases"]]
    problems = _problems()
    rows = [_judge_case(case, _resolve_item(case, problems)) for case in cases]
    return {"suite": "sample", "summary": _summary(rows), "cases": rows}


def run_dev() -> "dict[str, Any]":
    path = CONTENT_DIR / "eval" / "dev" / "cases.json"
    cases = _load(path)["cases"]
    problems = _problems()
    rows = [_judge_case(case, _resolve_item(case, problems)) for case in cases]
    return {
        "suite": "dev",
        "summary": _summary(rows),
        "categories": _category_breakdown(cases, rows, _mistakes()),
        "cases": rows,
    }


def _solution_case(case: Mapping[str, Any]) -> "dict[str, Any]":
    case_id = case.get("case_id")
    expression = case.get("expression")
    answer = case.get("answer")
    expected = case.get("expected")
    if not isinstance(expression, str) or not isinstance(answer, str) or not isinstance(expected, str):
        return {
            "case_id": case_id,
            "status": "manual",
            "expected": expected,
            "actual": None,
            "reason": "缺少结构化 expression / answer / expected，转人工复核",
        }
    recompute = sympy_recompute.ENTRY(expression, expected=answer)
    equivalence = symbolic_equivalence.ENTRY(lhs=expression, rhs=answer)
    recompute_passed = bool(recompute.ok and isinstance(recompute.value, Mapping) and recompute.value.get("passed"))
    equivalence_passed = bool(equivalence.ok and isinstance(equivalence.value, Mapping) and equivalence.value.get("passed"))
    if recompute.ok and isinstance(recompute.value, Mapping) and recompute.value.get("passed") is False:
        actual = "recompute_mismatch"
    elif not recompute.ok or not equivalence.ok:
        actual = "level_downgraded"
    elif not recompute_passed or not equivalence_passed:
        actual = "flagged_uncertain"
    else:
        actual = "not_flagged"
    return {
        "case_id": case_id,
        "status": "pass" if actual == expected else "fail",
        "expected": expected,
        "actual": actual,
        "reason": None,
    }


def _frozen_answer_gate(
    cases: "list[Mapping[str, Any]]", rows: "list[dict[str, Any]]", mp_categories: "Mapping[str, str]"
) -> "tuple[bool, dict[str, dict[str, int]], int]":
    positive = [case for case in cases if not case.get("negative")]
    categories = _category_breakdown(positive, rows, mp_categories)
    categories_ok = bool(categories) and all(
        item["total"] >= 5 and item["passed"] >= FROZEN_CATEGORY_PASS_MIN
        for item in categories.values()
    )
    row_by_id = {row["case_id"]: row for row in rows}
    negative_false_positives = sum(
        bool(row_by_id[case["case_id"]]["actual_mistake_ids"])
        for case in cases
        if case.get("negative") and case["case_id"] in row_by_id
    )
    return (
        len(positive) >= 20 and categories_ok and negative_false_positives == 0,
        categories,
        negative_false_positives,
    )


def _frozen_solution_gate(
    cases: "list[Mapping[str, Any]]", rows: "list[dict[str, Any]]"
) -> "tuple[bool, dict[str, dict[str, int]], int]":
    passed = sum(row["status"] == "pass" for row in rows)
    manual = sum(row["status"] == "manual" for row in rows)
    counts: "dict[str, dict[str, int]]" = defaultdict(lambda: {"total": 0, "passed": 0})
    for case, row in zip(cases, rows):
        category = str(case.get("category") or "uncategorized")
        counts[category]["total"] += 1
        counts[category]["passed"] += int(row["status"] == "pass")
    categories_ok = bool(counts) and all(
        item["total"] >= 5 and item["passed"] >= FROZEN_CATEGORY_PASS_MIN
        for item in counts.values()
    )
    ab_passed = sum(
        row["status"] == "pass"
        for case, row in zip(cases, rows)
        if case.get("ab_reachable") is True and case.get("expected") == "recompute_mismatch"
    )
    gate = (
        len(cases) >= FROZEN_SOLUTION_MIN
        and passed >= FROZEN_SOLUTION_PASS_MIN
        and categories_ok
        and ab_passed >= FROZEN_AB_PASS_MIN
        and manual == 0
    )
    return gate, dict(sorted(counts.items())), ab_passed


def run_frozen(frozen_dir: Path, executor: "str | None" = None) -> "dict[str, Any]":
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
    answer_rows = [_judge_case(case, _resolve_item(case, problems)) for case in answer_cases]
    answer_gate, answer_categories, negative_false_positives = _frozen_answer_gate(
        answer_cases, answer_rows, _mistakes()
    )

    solution_cases = list(criteria.get("solution_injection", {}).get("cases", []))
    solution_rows = [_solution_case(case) for case in solution_cases]
    solution_gate, solution_categories, ab_passed = _frozen_solution_gate(
        solution_cases, solution_rows
    )

    manual = sum(row["status"] == "manual" for row in solution_rows)
    if manual:
        conclusion = "manual_review_required"
    elif answer_gate and solution_gate:
        conclusion = "pass"
    else:
        conclusion = "fail"
    return {
        "suite": "frozen",
        "executed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "executor": executor or getpass.getuser(),
        "content_rev": catalog.content_rev(),
        "conclusion": conclusion,
        "answer_injection": {
            "summary": _summary(answer_rows),
            "categories": answer_categories,
            "negative_false_positives": negative_false_positives,
            "threshold_pass": answer_gate,
            "cases": answer_rows,
        },
        "solution_injection": {
            "summary": _summary(solution_rows),
            "manual": manual,
            "categories": solution_categories,
            "ab_reachable_passed": ab_passed,
            "threshold_pass": solution_gate,
            "cases": solution_rows,
        },
    }


def main(argv: "list[str] | None" = None) -> int:
    parser = argparse.ArgumentParser(prog="scripts/eval_verify.py")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--suite", choices=("sample", "dev"))
    group.add_argument("--frozen-dir", type=Path)
    parser.add_argument("--executor", help="冻结集执行人；缺省取当前系统用户名")
    args = parser.parse_args(argv)

    if args.frozen_dir is not None:
        report = run_frozen(args.frozen_dir, args.executor)
        exit_code = 0 if report["conclusion"] == "pass" else 1
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
