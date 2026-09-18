"""判定的确定性入口 —— `domain/` 的入口之二（`docs/02 §4.3-④` · F4）。

★ **能算的不要问模型**：本入口不含任何 LLM 调用、不 import `tools/`，全部是同步 CPU 计算
（调用方负责把它丢进线程池 —— `docs/03 §3.2` 第 3 条；实现点在 `api/`，属 W2）。

★ 容差口径**继承旧仓实测结论**（v2 未定义；旧仓为它踩过三次坑并有测试钉死边界）：

    tol = 0.6 × 10^(−min(学生有效位, 标准答案有效位)) × max(1, |标准答案|)

* 标准答案 `0.026` ⇒ `0.0265` **判对**（同量级四舍五入）；`0.03`（差 15%）与 `0.024`（差 8%）**判错**
* ★ **方向不能反**：精度取**两边最小值** —— 只按学生位数算会把对的判错，用 `1e-3` 相对误差一刀切会把错的判对
* 标准答案非零而学生写 `0` ⇒ **容差 0**（那不是四舍五入，是没做出来）
* 先符号等价（`simplify(a-b)==0`），失败再按**文本精度**做数值近似
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import sympy as sp

from .mistake import match_mistake
from .normalize import normalize_answer

__all__ = ["MAX_EXPRESSION_LENGTH", "GradeOutcome", "grade"]

# 表达式长度上界 —— 与契约 expected / answer_normalized 的 maxLength 一致
MAX_EXPRESSION_LENGTH: int = 400

# 解析表达式时禁止出现的片段（继承旧仓 math_tools._parse 的安全清单，防任意代码执行）
_FORBIDDEN = ("__", "import", "eval", "exec", "lambda", "open", "os.", "sys.")

# 教材与题目里到处在用的 Phi（标准正态分布函数），SymPy 没有 —— 用 erf 表达（继承旧仓）
_SAFE_LOCALS: dict[str, Any] = {"Phi": lambda x: (1 + sp.erf(x / sp.sqrt(2))) / 2}

_PLAIN_NUMBER = re.compile(r"[+-]?\d*\.?\d+")
_INTEGER_TEXT = re.compile(r"[+-]?\d+")


def _parse(expr: str) -> Any:
    """解析表达式（白名单符号，禁任意代码执行；长度有上界）。"""
    if len(expr) > MAX_EXPRESSION_LENGTH:
        raise ValueError(f"表达式过长（>{MAX_EXPRESSION_LENGTH} 字符），已拒绝")
    for bad in _FORBIDDEN:
        if bad in expr:
            raise ValueError(f"表达式包含被禁止的片段：{bad}")
    return sp.sympify(expr, locals=_SAFE_LOCALS)


def _fmt(value: float, source: str) -> str:
    """取用于判断精度的文本：原文本是纯数字就用它，否则用计算结果（旧仓同此）。"""
    text = str(source).strip()
    if _PLAIN_NUMBER.fullmatch(text) is not None:
        return text
    return f"{value:.6g}"


def _precision(text: str, value: float) -> int:
    """有效数字位数（旧仓口径：数值 >=1 或纯整数文本按整数位数，其余统一按 3 位）。"""
    if abs(value) >= 1 or _INTEGER_TEXT.fullmatch(str(text).strip()) is not None:
        return max(1, len(str(int(abs(value)))) if abs(value) >= 1 else 1)
    return 3


def _tol_for(student_text: str, student_value: float, expect_text: str, expect_value: float) -> float:
    """容差 = 0.6 × 10^(−min(学生有效位, 标准答案有效位)) × max(1, |标准答案|)。"""
    if abs(expect_value) > 1e-12 and abs(student_value) <= 1e-12:
        return 0.0
    digits = min(_precision(student_text, student_value), _precision(expect_text, expect_value))
    return 0.6 * (10.0**-digits) * max(1.0, abs(expect_value))


def _expr_equal(student: str, expect: str) -> bool:
    """先符号等价，再按文本精度做数值近似。约定：第一个参数是学生的作答。"""
    try:
        left = _parse(student)
        right = _parse(expect)
    except Exception:  # noqa: BLE001 —— 解析不了就没有"等价"可言，交给文本比较
        return False
    try:
        if sp.simplify(left - right) == 0:
            return True
    except Exception:  # noqa: BLE001 —— 某些表达式 simplify 会失败，退到数值
        pass
    try:
        left_value = float(sp.N(left))
        right_value = float(sp.N(right))
    except Exception:  # noqa: BLE001
        return False
    tolerance = _tol_for(_fmt(left_value, student), left_value, _fmt(right_value, expect), right_value)
    return abs(left_value - right_value) <= tolerance


@dataclass(frozen=True)
class GradeOutcome:
    """一次判定的结果。

    ★ 字段与 `contracts/attempt.schema.json` 的 `gradeResult` 两支一一对应：
    可判定支 = graded / correct / score / mistake_ids / mistake_matched；
    不可判定支 = graded / reason / message。

    本对象是**领域层的值对象**；把它转成接口视图（`expected` 快照 / `attempt_id` / `grader` 落库）
    是 W2 的落库与路由层的事，不在本模块。
    """

    graded: bool
    correct: bool | None
    score: float | None
    grader: str
    normalized: str
    mistake_ids: frozenset[str] = field(default_factory=frozenset)
    mistake_matched: bool = False
    reason: str | None = None
    message: str | None = None


def _require(item: Mapping[str, Any], key: str) -> Any:
    if key not in item:
        # docs/02 §8-14：缺 gradable / 缺 answer_kind 都按资产错误处理
        raise ValueError(f"题库条目缺 {key!r}（docs/02 §8-14 的启动校验清单）")
    return item[key]


def grade(item: Mapping[str, Any], raw_answer: str) -> GradeOutcome:
    """判定一次作答。

    * **不可判定题**（`gradable=False`）⇒ `graded=False` · `correct=None` · `grader="not_gradable"`
      （`docs/02 §4.3-②` · `§8-18`：仍可写 attempt，但 `correct=NULL`、掌握度不累加，
      **不得用模型兜底判定**）
    * **可判定题** ⇒ 确定性判定；判错时走错因匹配（命中不了就如实为空集）
    """
    gradable = _require(item, "gradable")
    answer_kind = _require(item, "answer_kind")
    expected_raw = _require(item, "expected")
    if not isinstance(expected_raw, str):
        raise ValueError("expected 必须是字符串（contracts/content.schema.json）")

    normalized = normalize_answer(raw_answer, answer_kind)

    if gradable is False:
        return GradeOutcome(
            graded=False,
            correct=None,
            score=None,
            grader="not_gradable",
            normalized=normalized,
            reason="not_gradable",
            message="该题需人工判定",
        )

    expected = normalize_answer(expected_raw, answer_kind)
    if answer_kind == "value":
        correct = _expr_equal(normalized, expected)
    else:
        correct = normalized == expected

    if correct:
        return GradeOutcome(
            graded=True,
            correct=True,
            score=1.0,
            grader="deterministic",
            normalized=normalized,
        )

    hits = match_mistake(raw_answer, item)
    return GradeOutcome(
        graded=True,
        correct=False,
        score=0.0,
        grader="deterministic",
        normalized=normalized,
        mistake_ids=hits,
        mistake_matched=bool(hits),
    )
