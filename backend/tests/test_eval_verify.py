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


def test_sample_runner_executes_all_public_cases(capsys) -> None:
    runner = _load_runner()
    assert runner.main(["--suite", "sample"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["summary"]["total"] == 3
    assert report["summary"]["passed"] == 3
