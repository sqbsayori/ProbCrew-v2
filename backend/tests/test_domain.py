"""`domain/` 的用例（文件名与被测模块同名 —— `docs/05 §3`）。

覆盖：归一化（旧仓实测的解析顺序与不重写数值）· 判定的容差边界与不可判定分支 ·
错因匹配的正/负样本与同族互斥 · **`domain/` 的依赖方向静态断言**（`docs/03 §3.1` 那张表）。

★ 本文件**不依赖卡1 的 conftest**：自己把 `backend/` 放进 `sys.path`，
这样在"卡1 第 ⑤ 步尚未交付"时本模块也能自足跑绿（`docs/06 §12.2` 边②的另一半）。
"""

from __future__ import annotations

import ast
import sys
import time
from pathlib import Path
from typing import Any

import pytest

BACKEND = Path(__file__).resolve().parents[1]
DOMAIN_DIR = BACKEND / "app" / "domain"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.domain import DETECTORS, grade, match_mistake, normalize_answer  # noqa: E402


def _item(expected: str, kind: str = "value", gradable: bool = True) -> dict[str, Any]:
    """一个最小可用的题库条目（形状见 `contracts/content.schema.json`）。"""
    return {
        "prob_id": "prob_probe",
        "stem": "探测用题面",
        "expected": expected,
        "kc_ids": ["kc_probe"],
        "pt_id": "pt_probe",
        "gradable": gradable,
        "answer_kind": kind,
        "difficulty": 1,
    }


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2.6%", "0.026"),
        ("$0.026$", "0.026"),
        ("答案是 0.026。", "0.026"),
        ("P(D)=0.026", "0.026"),
        ("13/28", "(13)/(28)"),
        ("45*0.05**2*0.95**8", "45*0.05**2*0.95**8"),
        ("0.026", "0.026"),  # 不做数值重写：精度信息要留给判定读
        ("", ""),
    ],
)
def test_normalize_value_variants(raw: str, expected: str) -> None:
    assert normalize_answer(raw) == expected


def test_normalize_text_kind() -> None:
    assert normalize_answer("  条件  概率。", "text") == "条件概率"


def test_normalize_keeps_symbolic_form() -> None:
    """符号形态不能被"首尾标点"规则削掉（否则方向类错因永远匹配不上）。"""
    assert normalize_answer("P(B|A)") == "P(B|A)"


def test_normalize_rejects_invalid_kind() -> None:
    with pytest.raises(ValueError):
        normalize_answer("1", "nope")


def test_normalize_rejects_overlong_input() -> None:
    """先卡长度再跑正则 —— 长数字串上不许出现灾难性回溯（R-A）。"""
    start = time.perf_counter()
    with pytest.raises(ValueError):
        normalize_answer("9" * 100_000)
    assert time.perf_counter() - start < 1.0


def test_grade_tolerance_boundary() -> None:
    """旧仓的实测边界：`0.0265` 判对；`0.03` / `0.024` / `0` 判错。"""
    assert grade(_item("0.026"), "0.0265").correct is True
    assert grade(_item("0.026"), "0.026").correct is True
    assert grade(_item("0.026"), "0.03").correct is False
    assert grade(_item("0.024"), "0.026").correct is False
    assert grade(_item("0.026"), "0").correct is False


def test_grade_equivalent_forms() -> None:
    assert grade(_item("45*0.05**2*0.95**8"), "0.0746").correct is True
    assert grade(_item("0.026"), "2.6%").correct is True
    assert grade(_item("条件概率", "text"), " 条件 概率。").correct is True


def test_grade_not_gradable_branch() -> None:
    """`docs/02 §4.3-②` · `§8-18`：不可判定题不得被兜底判定。"""
    outcome = grade(_item("（需人工判定）", gradable=False), "见附纸")
    assert outcome.graded is False
    assert outcome.correct is None
    assert outcome.score is None
    assert outcome.grader == "not_gradable"
    assert outcome.reason == "not_gradable"
    assert outcome.message == "该题需人工判定"
    assert outcome.mistake_matched is False


def test_grade_requires_asset_fields() -> None:
    """缺 `gradable` / `answer_kind` 是资产错误（`docs/02 §8-14`），不许静默放行。"""
    with pytest.raises(ValueError):
        grade({"expected": "1", "answer_kind": "value"}, "1")
    with pytest.raises(ValueError):
        grade({"expected": "1", "gradable": True}, "1")


def test_grade_long_digit_string_is_fast() -> None:
    """旧仓 P0 事故同型：长数字串不许拖住判定。"""
    start = time.perf_counter()
    with pytest.raises(ValueError):
        grade(_item("0.026"), "9" * 100_000)
    assert time.perf_counter() - start < 1.0


def test_match_mistake_positive_samples() -> None:
    library = [{"mp_id": detector.mp_id} for detector in DETECTORS]
    assert match_mistake("2.6", _item("0.026"), mistakes=library) == {"mp_percent_not_converted"}
    assert match_mistake("0.26", _item("0.026"), mistakes=library) == {"mp_decimal_shift"}
    assert match_mistake("-0.026", _item("0.026"), mistakes=library) == {"mp_sign_error"}
    assert match_mistake("P(B|A)", _item("P(A|B)"), mistakes=library) == {"mp_direction_reversed"}


def test_match_mistake_never_fabricates() -> None:
    """F5 的负样本路径：没有对应错因就返回空集合。"""
    library = [{"mp_id": detector.mp_id} for detector in DETECTORS]
    assert match_mistake("0.9", _item("0.026"), mistakes=library) == frozenset()
    assert match_mistake("2.6", _item("0.026"), mistakes=[]) == frozenset()


def test_match_mistake_only_returns_ids_present_in_library() -> None:
    """库里没有的 `mp_id` 不许被返回 —— 响应的错因必须能在错因库里查到（F5 的"未识别"语义）。"""
    library = [{"mp_id": "mp_someone_else"}]
    assert match_mistake("2.6", _item("0.026"), mistakes=library) == frozenset()


def test_grade_failure_carries_mistake_for_known_case() -> None:
    """判错时把错因带出来（`attempt.mistake_id` 的来源）—— 这里用注入的错因库快照。"""
    library = [{"mp_id": "mp_percent_not_converted"}]
    outcome = grade(_item("0.026"), "2.6")
    assert outcome.correct is False
    assert match_mistake("2.6", _item("0.026"), mistakes=library) == {"mp_percent_not_converted"}
    assert outcome.mistake_matched is False, "本轮 content/mistakes.json 尚未产出 ⇒ 判定侧如实为未识别"


# `docs/03 §3.1` 的依赖方向表：`domain/` 只允许依赖 `core/`
FORBIDDEN_IMPORT_PREFIXES = (
    "fastapi",
    "langgraph",
    "sqlite3",
    "sqlalchemy",
    "httpx",
    "requests",
    "urllib",
    "socket",
    "aiohttp",
    "app.tools",
    "app.learning",
    "app.api",
    "app.graph",
    "app.identity",
)


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level >= 2:
                names.add("app." + (node.module or ""))
            elif node.module:
                names.add(node.module)
    return names


def test_domain_dependency_direction_is_static_clean() -> None:
    """可静态核对：`domain/` 不 import 数据库 / FastAPI / LangGraph / 网络 / `tools/` / `learning/`。"""
    offenders: list[str] = []
    for path in sorted(DOMAIN_DIR.glob("*.py")):
        for name in _imported_modules(path):
            if any(name == prefix or name.startswith(prefix + ".") for prefix in FORBIDDEN_IMPORT_PREFIXES):
                offenders.append(f"{path.name}: {name}")
    assert offenders == [], f"domain/ 出现了被禁止的依赖：{offenders}"


def test_domain_has_no_llm_call() -> None:
    """F4：判定链的入口不含任何 LLM 调用（唯一出网口是 `tools/llm.py`，这里连 `tools/` 都不许 import）。"""
    for path in sorted(DOMAIN_DIR.glob("*.py")):
        source = path.read_text(encoding="utf-8").lower()
        assert "openai" not in source
        assert "deepseek" not in source
