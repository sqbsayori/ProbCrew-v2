"""符号等价性 —— W3 · S2（``docs/06 §4`` 第 11 项：**B 级可达**的那个手段）。

**为什么必须有它**：``docs/03 §9.2`` 第 10 行的技术债写着"A/B 级验证恒不可达"——
没有本模块，``level`` 的手段封顶（``docs/02 §4.4①``）就上不到 B，而 B 是**符号可强验证**
唯一的那条路径（``contracts/verification.schema.json`` 的 ``x-level-reachability``）。

公开接口::

    ENTRY = equivalent(lhs, rhs, *, timeout_ms=2000) -> ToolResult

``value`` **就是** ``contracts/verification.schema.json`` 的 ``definitions.check`` 形状 ——
与 ``sympy_recompute`` 同构（装配方只搬运、不重写形状）。

**三条结论**（不把三种情形混成一句话 —— N2 要的正是这种区分）：

1. 差化简为 **0** ⇒ ``passed`` 为真：判定为等价。
2. 差化简为**非零常数**（``x^2+1`` 与 ``x^2+2`` 之差是 ``-1``）⇒ ``passed`` 为假：
   **可判定为不等价**（这一支本身就是结论，不需要反例）。
3. 差化简后**仍含变量且不为 0** ⇒ ``passed`` 为假，措辞是"**未能判定为等价**"，
   并给一个**数值反例**填进 ``evidence.counterexample``（契约里那个字段就是为这件事留的）。
   ★ **保守取值（明说不藏）**：化简不动 ≠ 证明了不等价。理由：宁可多触发一次重算
   （``docs/02 §4.4③``），也不声称一个自己没验证过的"等价"—— 那是 N2（诚实）要挡的反面。

| 情形 | ``ok`` | 结果 |
|---|---|---|
| 判定完成（等价 / 未判定为等价） | 真 | ``value`` = check，``passed`` 为真或假 |
| 表达式不可接受（形状 / 字符 / 标识符） | 假 | ``reason="invalid_request"`` |
| 表达式解析失败 / 化简超时 | 假 | ``reason="parse_error"`` / ``"timeout"`` |

依赖方向（``docs/03 §3.1``）：``tools/`` 只允许 import ``core/`` / ``domain/``。
"""
from __future__ import annotations

from typing import Any

import sympy

from app.tools import ToolResult
from app.tools.expression import (
    EVIDENCE_MAX_LENGTH,
    ExpressionRejected,
    clip,
    parse,
    render,
    run_bounded,
)

#: ``contracts/verification.schema.json`` 的 ``check.method`` 枚举取值 —— **不自造**。
METHOD = "symbolic_equivalence"

#: 契约里 ``check.name`` 的例子之一（会直接展示给学生）。
CHECK_NAME = "符号等价性"

#: 首次取值：与 ``sympy_recompute`` 一致。
DEFAULT_TIMEOUT_MS = 2000

#: ``contracts/verification.schema.json`` 的 ``check.detail`` 的 ``maxLength``。
DETAIL_MAX_LENGTH = 500

#: 找反例的探针点（首次取值）：三个不同量级的点，足以戳破绝大多数"看起来像等价"的式子。
_PROBE_BASES = (0.731, 1.414, -2.5)

#: 反例判定用的相对阈值 —— 比 `sympy_recompute` 宽，因为这里只需要"数值上确实不同"。
_COUNTEREXAMPLE_TOLERANCE = 1e-9

#: 第 3 支（未判定）的措辞 —— 同时充当 `_build_check` 里"要不要去找反例"的判据。
_UNDECIDED_DETAIL = "两边之差化简后仍含变量且不为 0 —— 未能判定为等价（不等价于已证明两者不同）。"


def equivalent(lhs: str, rhs: str, *, timeout_ms: int = DEFAULT_TIMEOUT_MS) -> ToolResult:
    """判定 ``lhs`` 与 ``rhs`` 是否符号等价，返回一条 ``check``。"""
    try:
        check = run_bounded(lambda: _build_check(lhs, rhs), timeout_ms)
    except ExpressionRejected as exc:
        return ToolResult(ok=False, reason=exc.kind)
    # ★ `ok` 由**结果里真的带上了结论字段**派生，不写成字面量（R-Q ②）。
    executed = isinstance(check, dict) and "passed" in check
    return ToolResult(ok=executed, value=check)


def _build_check(lhs: str, rhs: str) -> "dict[str, Any]":
    """真的判定一遍 —— 只在拿到结论时返回；未执行的情形一律抛 :class:`ExpressionRejected`。"""
    left = parse(lhs)
    right = parse(rhs)
    difference = _simplify(left - right)
    passed, detail = _judge(difference)
    check: "dict[str, Any]" = {
        "name": CHECK_NAME,
        "passed": passed,
        "method": METHOD,
        "detail": detail[:DETAIL_MAX_LENGTH],
    }
    if not passed and detail == _UNDECIDED_DETAIL:
        # 只有第 3 支（未判定）才去找反例：第 2 支（非零常数）本身已是结论。
        example = _counterexample(left, right, difference)
        if example:
            check["evidence"] = {"counterexample": example}
    return check


def _judge(difference: Any) -> "tuple[bool, str]":
    """差 → 结论（三条分支见模块头）。``is_number`` 判"已化简成常数"这一支。"""
    if difference == 0:
        return True, "两边之差化简为 0 —— 判定为符号等价。"
    if getattr(difference, "is_number", False):
        return False, f"两边之差化简为非零常数（{render(difference)}）—— 可判定为不等价。"
    return False, _UNDECIDED_DETAIL


def _simplify(expression: Any) -> Any:
    try:
        return sympy.simplify(expression)
    except (TypeError, ValueError, ArithmeticError, RecursionError):
        # 化简不了就把原式当结论 —— 它同样 != 0，落到"未判定为等价"那条路上（保守）。
        return expression


def _counterexample(left: Any, right: Any, difference: Any) -> str:
    """给一组代入值，两边数值明显不同即作为反例；**找不到就返回空串**（不硬凑）。"""
    symbols = sorted(difference.free_symbols, key=lambda item: item.name)
    if not symbols:
        return ""
    for base in _PROBE_BASES:
        assignment = {symbol: sympy.Float(base * (index + 1)) for index, symbol in enumerate(symbols)}
        left_value = _numeric(left.subs(assignment))
        right_value = _numeric(right.subs(assignment))
        if left_value is None or right_value is None:
            continue
        threshold = _COUNTEREXAMPLE_TOLERANCE * max(1.0, abs(left_value), abs(right_value))
        if abs(left_value - right_value) > threshold:
            substituted = ", ".join(
                f"{symbol.name}={float(assignment[symbol]):.6g}" for symbol in symbols
            )
            return clip(
                f"{substituted} ⇒ {_format(left_value)} vs {_format(right_value)}",
                limit=EVIDENCE_MAX_LENGTH,
            )
    return ""


def _format(value: complex) -> str:
    """给反例用的数值格式：虚部为 0 就只印实部（``1.53436+0j`` 这种噪声对学生没有意义）。"""
    if abs(value.imag) < 1e-12:
        return f"{value.real:.6g}"
    return f"{value:.6g}"


def _numeric(value: Any) -> "complex | None":
    try:
        evaluated = sympy.N(value, 15)
        if not evaluated.is_number:
            return None
        return complex(evaluated)
    except (TypeError, ValueError, ArithmeticError):
        return None


#: 工具入口 —— ``graph.state.ToolBox`` 按这个名字取（``app/tools/__init__.py`` 的约定）。
ENTRY = equivalent
