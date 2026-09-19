"""判定的确定性入口 —— W0b · 卡2 · 第②步（F4，docs/02 §4.3）。

判定链的 domain 段（docs/02 §4.3 ①–④）：

    ① 参数校验（answer ≤ 200 字，R-A）→ ② 读 ``gradable``（false ⇒ 显式降级）
    → ③ 归一化（★ 在本入口内，docs/06 §12.8）→ ④ 判定（**零 LLM 调用**）
    → 错误时错因匹配（吃规则表，F5）

输出形状对齐 ``contracts/attempt.schema.json`` 的 ``gradeResult``（oneOf 两支），
但**不含服务端派生字段** ``hint_used`` / ``attempt_no``（由服务端按流水计数派生，
docs/02 §4.3-⑥，domain 层无权知道）；``solution`` 由 API 层从题库条目透传
（完整解析只在 grade 之后可取，docs/02 §4.3-3）：

- 可判定支：``{graded, correct, score, mistake_id, mistake_matched}``
- 不可判定支：``{graded, reason, message, correct, grader, solution}``（与契约逐字一致）

``mistake_id`` 落库口径是**单值**（docs/02 §3.2 attempt.mistake_id），而错因匹配是
**集合语义**（F5）—— 本入口按规则注册表的显式顺序取第一个命中作为落库值，
完整命中集合只存在于当次响应之外评测运行器的复算里。

纯函数 + 纯计算：无 LLM、无网络、无落盘（docs/02 §4.3 硬性设计 1、4）。
同步 CPU 工作 —— 上层（API/graph）必须丢线程池执行（``to_thread``，docs/03 §3.2）。
"""
from __future__ import annotations

from fractions import Fraction
from typing import Any

from app.core.errors import AppError
from app.domain.mistakes import match_mistake
from app.domain.normalize import normalize

#: R-A：答案上界（docs/02 §4.3-①；旧仓 P0 事故：判定接口无上界被喂 100k 位数字）。
ANSWER_MAX_LENGTH = 200


def _as_fraction(text: str) -> "Fraction | None":
    """精确有理数解析（支持 ``50%`` / ``1/4`` / 小数）；失败返回 None。"""
    if text.endswith("%"):
        try:
            return Fraction(text[:-1]) / 100
        except (ValueError, ZeroDivisionError):
            return None
    try:
        return Fraction(text)
    except (ValueError, ZeroDivisionError):
        return None


def _value_equal(ans: str, exp: str) -> bool:
    """数值等价（精度口径）：精确有理数比较；非数值退化为字符串相等。"""
    value_ans = _as_fraction(ans)
    value_exp = _as_fraction(exp)
    if value_ans is not None and value_exp is not None:
        return value_ans == value_exp
    return ans == exp


def _answers_equal(normalized_answer: str, normalized_expected: str, answer_kind: str) -> bool:
    """按 ``answer_kind``（题库资产的唯一分类轴）判定等价。"""
    if answer_kind == "value":
        # 数值 / 闭式表达式：先按数值等价（0.5 ≡ 50% ≡ 1/2），再退化为形状相等
        return _value_equal(normalized_answer, normalized_expected) or normalized_answer == normalized_expected
    if answer_kind == "text":
        # 含单位 / 文字结论：归一化后形状相等才算对（单位差异交给错因匹配去解释）
        return normalized_answer == normalized_expected
    raise AppError(
        "invalid_argument",
        "题目条目的 answer_kind 取值非法（必须是 value 或 text）",
        {"field": "answer_kind"},
    )


def _not_gradable() -> dict[str, Any]:
    """不可判定支 —— 与 attempt.schema.json gradeResult 第二支逐字一致（docs/02 §8-18）。"""
    return {
        "graded": False,
        "reason": "not_gradable",
        "message": "该题需人工判定",
        "correct": None,
        "grader": "not_gradable",
        "solution": None,
    }


def grade(answer: str, item: "dict[str, Any]") -> "dict[str, Any]":
    """判定的确定性入口：``grade(answer, item) -> dict``。

    ★ 归一化在入口内（docs/06 §12.8）；判定不含任何 LLM 调用（F4）。
    """
    # ① 参数校验（R-A）—— API 层同样会校验，这里保证 domain 自身不可被绕过
    if not isinstance(answer, str):
        raise AppError("invalid_argument", "答案必须是字符串", {"field": "answer"})
    if len(answer) > ANSWER_MAX_LENGTH:
        raise AppError(
            "invalid_argument",
            f"答案长度超过上限（{ANSWER_MAX_LENGTH} 字）",
            {"field": "answer", "reason": "above_max", "max": ANSWER_MAX_LENGTH},
        )
    if not isinstance(item, dict) or not item:
        raise AppError("invalid_argument", "题目条目缺失或为空", {"field": "item"})

    # ② gradable：false ⇒ 显式降级（docs/02 §4.3-②；未标记视为资产错误）
    if "gradable" not in item:
        raise AppError(
            "invalid_argument",
            "题目条目缺少 gradable 标记（资产错误，应在加载期拦截）",
            {"field": "gradable"},
        )
    if item["gradable"] is not True:
        return _not_gradable()

    if "expected" not in item or "answer_kind" not in item:
        raise AppError(
            "invalid_argument",
            "可判定题目条目缺少 expected 或 answer_kind（资产错误，应在加载期拦截）",
            {"field": "item"},
        )

    # ③ 归一化（在入口内）
    normalized_answer = normalize(answer)
    normalized_expected = normalize(str(item["expected"]))

    # ④ 判定（确定性，零 LLM）
    if _answers_equal(normalized_answer, normalized_expected, str(item["answer_kind"])):
        return {"graded": True, "correct": True, "score": 1.0, "mistake_id": None, "mistake_matched": False}

    # 错误 → 错因匹配（集合语义）；mistake_id 按规则注册表顺序取首个命中落库
    hits = match_mistake(answer, item)
    mistake_id = hits[0] if hits else None
    return {
        "graded": True,
        "correct": False,
        "score": 0.0,
        "mistake_id": mistake_id,
        "mistake_matched": bool(hits),
    }
