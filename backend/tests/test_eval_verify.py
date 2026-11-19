"""阶段 B/C 评测运行器用例。"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from jsonschema import Draft7Validator

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_runner():
    path = REPO_ROOT / "scripts" / "eval_verify.py"
    spec = importlib.util.spec_from_file_location("eval_verify_under_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_dev_set_matches_schema_and_has_four_categories() -> None:
    schema = json.loads((REPO_ROOT / "contracts" / "content.schema.json").read_text(encoding="utf-8"))
    document = json.loads(
        (REPO_ROOT / "content" / "eval" / "dev" / "cases.json").read_text(encoding="utf-8")
    )
    Draft7Validator(schema).validate(document)
    assert len(document["cases"]) == 20
    counts: "dict[str, int]" = {}
    for case in document["cases"]:
        for mistake_id in case["expected_mistake_ids"]:
            counts[mistake_id] = counts.get(mistake_id, 0) + 1
    assert counts == {
        "mp_direction_reversed": 5,
        "mp_unit_error": 5,
        "mp_missing_partition": 5,
        "mp_independence_confusion": 5,
    }


def test_dev_runner_reports_first_pass_baseline_without_answers(capsys) -> None:
    runner = _load_runner()
    assert runner.main(["--suite", "dev"]) == 0
    output = capsys.readouterr().out
    report = json.loads(output)
    assert report["summary"]["first_pass_accuracy"] == 1.0
    assert report["summary"]["passed"] == 20
    assert set(report["categories"]) == {
        "direction_reversed",
        "unit_error",
        "missing_partition",
        "independence_confusion",
    }
    assert all(item["total"] == item["passed"] == 5 for item in report["categories"].values())
    assert '"answer"' not in output
    assert "5厘米" not in output and "P(B|A)" not in output


def test_frozen_solution_schema_allows_structured_verification_fields() -> None:
    schema = json.loads((REPO_ROOT / "contracts" / "content.schema.json").read_text(encoding="utf-8"))
    frozen = next(branch for branch in schema["oneOf"] if branch.get("title") == "期望判据表（与冻结正式集同处，不入主仓）")
    case_schema = frozen["properties"]["solution_injection"]["properties"]["cases"]["items"]
    Draft7Validator(case_schema).validate(
        {
            "case_id": "solution_01",
            "item_id": "prob_negative_dev",
            "wrong_solution": "错误地把两个概率直接相乘",
            "expression": "1/2",
            "answer": "1/3",
            "ab_reachable": True,
            "category": "direction_reversed",
            "expected": "recompute_mismatch",
        }
    )


def test_structured_expected_correct_is_enforced() -> None:
    runner = _load_runner()
    item = {
        "prob_id": "prob_sample_regression",
        "stem": "已知条件概率",
        "expected": "1/2",
        "kc_ids": ["kc_conditional_probability"],
        "pt_id": "pt_conditional_probability",
        "gradable": True,
        "answer_kind": "value",
        "difficulty": 1,
    }
    row = runner._judge_case(
        {
            "case_id": "sample_regression",
            "item_id": "prob_sample_regression",
            "answer": "1/2",
            "expected_correct": False,
        },
        item,
    )
    assert row["actual_correct"] is True
    assert row["status"] == "fail"


def test_negative_mistake_expectation_requires_an_empty_actual_set() -> None:
    runner = _load_runner()
    item = {
        "prob_id": "prob_negative_dev",
        "stem": "求条件概率",
        "expected": "P(A|B)",
        "kc_ids": ["kc_conditional_probability"],
        "pt_id": "pt_conditional_probability",
        "gradable": True,
        "answer_kind": "text",
        "difficulty": 1,
    }
    row = runner._judge_case(
        {
            "case_id": "negative_01",
            "item_id": "prob_negative_dev",
            "answer": "P(B|A)",
            "expected_correct": False,
            "expected_mistake_ids": [],
        },
        item,
    )
    assert row["actual_mistake_ids"] == ["mp_direction_reversed"]
    assert row["status"] == "fail"


def test_frozen_reports_structured_solution_and_required_audit_fields(capsys, tmp_path) -> None:
    runner = _load_runner()
    frozen = tmp_path / "frozen"
    frozen.mkdir()
    criteria = {
        "version": "frozen-review-test",
        "frozen_at": "2026-11-19",
        "answer_injection": {
            "cases": [
                {
                    "case_id": "negative_01",
                    "item_id": "prob_negative_dev",
                    "answer": "P(B|A)",
                    "expected_mistake_ids": [],
                    "category": "direction_reversed",
                    "negative": True,
                    "item": {
                        "prob_id": "prob_negative_dev",
                        "stem": "求条件概率",
                        "expected": "P(A|B)",
                        "kc_ids": ["kc_conditional_probability"],
                        "pt_id": "pt_conditional_probability",
                        "gradable": True,
                        "answer_kind": "text",
                        "difficulty": 1,
                    },
                }
            ]
        },
        "solution_injection": {
            "cases": [
                {
                    "case_id": "solution_01",
                    "item_id": "prob_negative_dev",
                    "expression": "1/2",
                    "answer": "1/3",
                    "expected": "recompute_mismatch",
                    "ab_reachable": True,
                    "category": "direction_reversed",
                }
            ]
        },
    }
    (frozen / "criteria.json").write_text(json.dumps(criteria), encoding="utf-8")
    report = runner.run_frozen(frozen, executor="reviewer")
    assert report["executor"] == "reviewer"
    assert report["executed_at"] and report["content_rev"]
    assert report["answer_injection"]["negative_false_positives"] == 1
    assert report["solution_injection"]["manual"] == 0
    assert report["solution_injection"]["cases"][0]["actual"] == "recompute_mismatch"
    assert report["conclusion"] == "fail"


def test_sample_runner_executes_all_public_cases(capsys) -> None:
    runner = _load_runner()
    assert runner.main(["--suite", "sample"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["summary"]["total"] == 3
    assert report["summary"]["passed"] == 3
