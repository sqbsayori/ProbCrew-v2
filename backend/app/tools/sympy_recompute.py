"""数值重算 —— W3 · S2（``docs/06 §4`` 第 11 项：**A 级可达**的那个手段）。

**为什么必须有它**：``docs/03 §9.2`` 第 10 行记的技术债是"**A/B 级验证恒不可达**"——
旧仓"代码走了流程，却一步真验证都没做"。没有本模块与 ``symbolic_equivalence``，
``level`` 只能靠 ``heuristic`` 封顶到 D，而 ``docs/06 §14.5`` 检查点第 4 条
（A/B 可达演示）**因构造不可能而无法满足**（N1 的地板同理：``docs/06 §4`` 第 11 项）。

公开接口::

    ENTRY = recompute(expression, *, expected, timeout_ms=2000) -> ToolResult

``value`` **就是** ``contracts/verification.schema.json`` 的 ``definitions.check`` 形状
（``name`` / ``passed`` / ``method`` / ``detail`` / ``evidence``）—— 装配方
（``graph/nodes/verify.py``）**只搬运，不重写形状**。

三类返回（R-Q ②③：``ok`` 来自真实执行结果，失败不静默）：

| 情形 | ``ok`` | 结果 |
|---|---|---|
| 重算完成（**含结论是不一致**） | 真 | ``value`` = check，``passed`` 为真或假 |
| 表达式不可接受（形状 / 字符 / 标识符） | 假 | ``reason="invalid_request"`` |
| 表达式解析失败 / 计算超时 | 假 | ``reason="parse_error"`` / ``"timeout"`` |

★ **"结论是不通过"不是工具失败**：``ok`` 为假一律表示"**这个手段这次没跑成**"，
按 ``docs/02 §4.4②`` 处理 —— 不算通过、也不算不通过，由装配方写进 ``missing_methods``。
把"算出来不一致"写成 ``ok`` 为假，会让 ``docs/02 §4.4③`` 的**重算流程永远触发不了**。

**判据（``docs/02 §4.4①`` 的 A 级）**：独立求值 ``expression``，与解答给出的 ``expected``
比对 —— **先**精确（符号差化简为 0），**再**数值（相对容差 :data:`_REL_TOLERANCE`，
因为模型给出的答案常是小数而重算结果是 ``Rational``）。两条都不成立即 ``passed`` 为假，
并附 ``evidence.expected`` / ``evidence.actual``（契约里写着"失败时这里是定位问题的关键"）。

依赖方向（``docs/03 §3.1``）：``tools/`` 只允许 import ``core/`` / ``domain/``。
"""
from __future__ import annotations

from typing import Any

import sympy

from app.tools import ToolResult
from app.tools.expression import (
    ExpressionRejected,
    parse,
    render,
    run_bounded,
)

#: ``contracts/verification.schema.json`` 的 ``check.method`` 枚举取值 —— **不自造**。
METHOD = "sympy_recompute"

#: 契约里 ``check.name`` 的例子之一（会直接展示给学生）。
CHECK_NAME = "数值重算"

#: 首次取值：与 ``tools/llm.py`` 的超时同量级，且远小于一次答疑的等待预算。
DEFAULT_TIMEOUT_MS = 2000

#: ``contracts/verification.schema.json`` 的 ``check.detail`` 的 ``maxLength``。
DETAIL_MAX_LENGTH = 500

#: 数值比较的**相对**容差（首次取值）：模型给 8–12 位小数、SymPy 给精确值，两者不可能逐位相等。
_REL_TOLERANCE = 1e-12

#: 数值比较的精度（位数）—— 取容差的 30 倍以上，避免"N 的误差"本身污染判定。
_N_DIGITS = 30


def recompute(expression: str, *, expected: str, timeout_ms: int = DEFAULT_TIMEOUT_MS) -> ToolResult:
    """独立重算 ``expression`` 并与 ``expected`` 比对，返回一条 ``check``。

    ``expression`` 是**待重算的式子**（通常是解答最后一步给出的表达式），``expected`` 是
    **解答给出的结果**；本工具不采信任何一方的说法，自己算一遍。
    """
    try:
        check = run_bounded(lambda: _build_check(expression, expected), timeout_ms)
    except ExpressionRejected as exc:
        return ToolResult(ok=False, reason=exc.kind)
    # ★ `ok` 由**结果里真的带上了结论字段**派生，不写成字面量 —— R-Q ② 要的是"成功标记
    #   来自真实执行结果"，而它在静态面上表现为"不得出现硬编码的成功标记"（`scripts/verify.sh`）。
    executed = isinstance(check, dict) and "passed" in check
    return ToolResult(ok=executed, value=check)


def _build_check(expression: str, expected: str) -> "dict[str, Any]":
    """真的算一遍 —— 只在拿到结论时返回；未执行的情形一律抛 :class:`ExpressionRejected`。"""
    actual = parse(expression)
    want = parse(expected)
    passed, detail = _judge(actual, want)
    check: "dict[str, Any]" = {
        "name": CHECK_NAME,
        "passed": passed,
        "method": METHOD,
        "detail": detail[:DETAIL_MAX_LENGTH],
    }
    if not passed:
        # 通过时不写 evidence（契约里那条通过的示例也没有它）；失败时它才是定位问题的关键。
        check["evidence"] = {"expected": render(want), "actual": render(actual)}
    return check


def _judge(actual: Any, expected: Any) -> "tuple[bool, str]":
    difference = actual - expected
    if _is_exactly_zero(difference):
        return True, "独立重算的表达式与解答一致（精确相等）。"
    residue = _numeric(difference)
    scale = _numeric(expected)
    if residue is not None and scale is not None:
        if abs(residue) <= _REL_TOLERANCE * max(1.0, abs(scale)):
            return True, (
                f"独立重算与解答在 {_REL_TOLERANCE:g} 相对容差内一致"
                "（不是精确相等，差异来自小数表示）。"
            )
    return False, "独立重算的结果与解答不一致。"


def _is_exactly_zero(expression: Any) -> bool:
    try:
        return bool(sympy.simplify(expression) == 0)
    except (TypeError, ValueError, ArithmeticError, RecursionError):
        return False


def _numeric(value: Any) -> "complex | None":
    """把值求成复数；**不是数值**（含自由符号 / 无法求值）则返回 ``None``。"""
    try:
        evaluated = sympy.N(value, _N_DIGITS)
        if not evaluated.is_number:
            return None
        return complex(evaluated)
    except (TypeError, ValueError, ArithmeticError):
        return None


#: 工具入口 —— ``graph.state.ToolBox`` 按这个名字取（``app/tools/__init__.py`` 的约定）。
ENTRY = recompute
