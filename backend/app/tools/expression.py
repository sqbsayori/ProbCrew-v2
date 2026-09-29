"""表达式守卫与解析 —— S2 两个验证手段（``docs/06 §4`` 第 11 项）的共用前置。

★ **本模块不是工具**：它不暴露 ``ENTRY``、不进 ``graph/registry.py`` —— 与
``tools/endpoint.py`` 同一性质：**被两个手段共用的一段校验，不该有第二份实现**
（``docs/03 §3.2`` 第 2 条要挡的正是"同一套规则在两处各有一份"）。

**为什么必须有守卫**：表达式来自**模型输出**（不可信输入），而 SymPy 的 ``parse_expr``
底层就是 ``eval`` —— 直接丢进去等于把任意代码执行面开在答疑链上。三层防线，逐层都不依赖
下一层：

1. **字符白名单**（:data:`_ALLOWED_CHARS_RE`）：只收数字、标识符、运算符、括号、逗号与空白
   —— 下标、字符串、属性访问、内置名字**都在白名单之外**，遇到即拒；
2. **标识符白名单**：函数与常量只认 :data:`ALLOWED_NAMES`；其余合法标识符**一律当自由符号**
   （``n`` / ``k`` / ``p`` / ``x`` 是概率题的日常写法，拒掉它们等于拒掉正常题目）；
   ``__`` 一律拒（挡 dunder 与 ``__import__`` 这一类）；
3. **空 builtins**：``global_dict`` 里显式放 ``{"__builtins__": {}}`` ⇒ 即便前两层被绕过，
   ``eval`` 也拿不到任何内置函数（用例里有一条负样本在守这件事）。

**两类拒绝**（都由调用方转成 ``ToolResult(ok=False, reason=…)``）：

- ``invalid_request``：形状不对 / 出现白名单外的字符（**输入不可接受**）；
- ``parse_error``：字符在白名单内，但 SymPy 解析不了（``sin(`` · ``(1`` · ``1,2``）。

两者在下游**都是"未执行"**（``docs/02 §4.4②``：不算通过、也不算不通过 ⇒ 由装配方写进
``missing_methods``）；分开只为让排障看得见是哪一层拒的。

**有界计算**（:func:`run_bounded`）：解析与化简必须在**截止时间内**收口，否则一次答疑会被
一个慢式子拖住（``docs/02 §4.2-8`` 的"同步 CPU 工作"与 N20 的并发承诺都指向这件事）。
到点抛 ``timeout`` —— 它同样是"未执行"，**不是**"不通过"。

**已知边界（诚实登记）**：

- 自由符号一律无假设（``Symbol('x')``，不给 positive / real）—— 因此 ``sqrt(x**2)`` 化简成
  ``x`` 这一类"需要假设才成立的化简"**不会**发生，判定偏保守；
- 白名单**不含** ``Piecewise`` / ``Integral`` / ``Derivative``：本轮的手段是"数值重算"与
  "符号等价"，用不到的构造器不进白名单（与 ``contracts/verification.schema.json`` 不收
  ``constraint`` / ``multi_path`` 同一口径：**没发布过的不进枚举**）；
- ★ ``run_bounded`` 到点后**无法强杀**那个工作线程（Python 没有可移植的取消机制）——
  ``timeout_ms`` 的语义是"**我不再等它**"，不是"**它停了**"。与 ``tools/llm.py`` 的
  DNS rebinding 边界同一体例：如实登记，不假装已解决。

依赖方向（``docs/03 §3.1``）：``tools/`` 只允许 import ``core/`` / ``domain/``。
"""
from __future__ import annotations

import keyword
import re
import threading
import tokenize
from typing import Any, Callable

import sympy
from sympy.parsing.sympy_parser import (
    convert_xor,
    parse_expr,
    standard_transformations,
)

from app.tools import INVALID_REQUEST, PARSE_ERROR, TIMEOUT

#: 输入上界（首次取值）：远超一道概率题的式子，又足以容纳模型给出的整个表达式。
#: ★ R-A 的同精神（字符串字段必须有显式上界）—— 这里的输入虽不是请求体，但它**来自模型**，
#:   同样是不可信字符串。
MAX_EXPRESSION_LENGTH = 2000

#: 证据字段的截断宽度：进事件流与 run 快照的字符串不该无界（与 ``qa-log.schema.json`` 同精神）。
EVIDENCE_MAX_LENGTH = 200

#: 允许出现的字符：数字 · 标识符 · ``+ - * / ^ ( ) , !`` 与空白。**白名单，不是黑名单。**
_ALLOWED_CHARS_RE = re.compile(r"^[0-9A-Za-z_+\-*/^().,! \t\r\n]+$")

#: 数字字面量（含小数与科学计数法）—— 用来做"``.`` 只许出现在数字里"这一步。
_NUMBER_RE = re.compile(r"(?:\d+\.\d*|\.\d+|\d+)(?:[eE][+-]?\d+)?")
_IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

#: 允许的函数与常量 —— 逐项对应概率论课程里会出现的东西。**其余合法标识符一律当自由符号。**
ALLOWED_NAMES: "dict[str, Any]" = {
    # 常量
    "pi": sympy.pi,
    "E": sympy.E,
    "I": sympy.I,
    "oo": sympy.oo,
    # 指数与对数（`ln` 在中文教材里比 `log` 常见，两者同义）
    "sqrt": sympy.sqrt,
    "exp": sympy.exp,
    "log": sympy.log,
    "ln": sympy.log,
    # 三角
    "sin": sympy.sin,
    "cos": sympy.cos,
    "tan": sympy.tan,
    # 组合与阶乘 —— 概率题的日常
    "factorial": sympy.factorial,
    "binomial": sympy.binomial,
    "gamma": sympy.gamma,
    # 取整与绝对值
    "Abs": sympy.Abs,
    "Min": sympy.Min,
    "Max": sympy.Max,
    # 求和 / 连乘（分布与期望）
    "Sum": sympy.Sum,
    "Product": sympy.Product,
    # 构造器（`parse_expr` 的自动变换会生成这几个名字，必须可达）
    "Integer": sympy.Integer,
    "Float": sympy.Float,
    "Rational": sympy.Rational,
    "Symbol": sympy.Symbol,
}

#: 一律拒的标识符：Python 关键字 + 一切能碰到内置对象的名字。
_BANNED_NAMES = frozenset(keyword.kwlist) | frozenset(
    {
        "import",
        "exec",
        "eval",
        "open",
        "compile",
        "input",
        "print",
        "globals",
        "locals",
        "vars",
        "getattr",
        "setattr",
        "delattr",
        "object",
        "type",
        "super",
        "classmethod",
        "staticmethod",
        "property",
        "__builtins__",
    }
)

#: 解析时用的全局命名空间 —— ★ ``__builtins__`` 显式置空（第 3 层防线）。
_GLOBAL_DICT: "dict[str, Any]" = {**ALLOWED_NAMES, "__builtins__": {}}

#: ``^`` 按"乘方"解释（模型常写 ``x^2``；SymPy 默认把它当按位异或，会在 Symbol 上直接报错）。
#: ★ 这是一条**显式**变换，不是默认行为 —— 登记在此，免得下一个人以为 ``^`` 天生可用。
_TRANSFORMATIONS = standard_transformations + (convert_xor,)


class ExpressionRejected(RuntimeError):
    """表达式不可接受 / 解析失败。

    ``kind`` **就是**调用方要填进 ``ToolResult.reason`` 的那个短代码（本模块不含工具语义，
    因此只给出代码，不决定如何上报）。
    """

    def __init__(self, kind: str, detail: str) -> None:
        self.kind = kind
        self.detail = detail
        super().__init__(detail)


def parse(expression: str) -> Any:
    """把字符串解析成 SymPy 表达式；**坏输入一律抛 :class:`ExpressionRejected`**。

    顺序即防线顺序：形状（超长 / 空）→ 字符 → 标识符 → 解析结果是不是一个数学对象。
    """
    text = _check_shape(expression)
    _check_chars(text)
    _check_identifiers(text)
    try:
        value = parse_expr(
            text,
            local_dict={},
            global_dict=dict(_GLOBAL_DICT),
            transformations=_TRANSFORMATIONS,
        )
    except (SyntaxError, tokenize.TokenError, ValueError, TypeError, ArithmeticError, RecursionError) as exc:
        # ★ 异常类型随 SymPy 版本而变（`TokenError` / `SyntaxError` / `SympifyError`（ValueError 子类）
        #   / `TypeError`…），因此这里按"解析期可能抛出的这几族"收口，而不是逐个点名。
        raise ExpressionRejected(PARSE_ERROR, f"表达式无法解析（{type(exc).__name__}）") from exc
    if not isinstance(value, sympy.Basic):
        # `1,2` 会解析成 tuple 之类的非数学对象 ⇒ 同样按"解析失败"处理。
        raise ExpressionRejected(PARSE_ERROR, f"表达式解析结果不是数学对象（{type(value).__name__}）")
    return value


def render(value: Any, *, limit: int = EVIDENCE_MAX_LENGTH) -> str:
    """把 SymPy 对象渲染成**有界**字符串（进 ``evidence`` / ``detail`` 用）。

    超长即截断并补 ``…`` —— 证据要能定位问题，但不该把整个表达式塞进事件流与快照。
    """
    return clip(sympy.sstr(value), limit=limit)


def clip(text: str, *, limit: int = EVIDENCE_MAX_LENGTH) -> str:
    """给已经拼好的字符串截断（:func:`render` 的字符串版：反例一类"人拼出来的证据"用它）。"""
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)] + "…"


def run_bounded(call: "Callable[[], Any]", timeout_ms: int) -> Any:
    """在工作线程里跑 ``call``；到点抛 ``ExpressionRejected(TIMEOUT)``，线程内异常原样带回。

    ★ 用**裸 daemon 线程**而不是 ``ThreadPoolExecutor``：后者的工作线程是**非 daemon**，
    ``atexit`` 会 join 它 ⇒ 一次跑不完的化简会**挂住进程退出**（而它恰恰是要防的形态）。
    """
    box: "dict[str, Any]" = {}

    def worker() -> None:
        try:
            box["value"] = call()
        except BaseException as exc:  # noqa: BLE001 —— 线程内的异常必须带回主线程，不能静默丢
            box["error"] = exc

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    thread.join(max(0.0, timeout_ms / 1000.0))
    if thread.is_alive():
        raise ExpressionRejected(TIMEOUT, f"计算超过 {timeout_ms} ms 仍未结束（不再等待）")
    if "error" in box:
        raise box["error"]
    return box["value"]


def _check_shape(expression: Any) -> str:
    if not isinstance(expression, str) or not expression.strip():
        raise ExpressionRejected(INVALID_REQUEST, "表达式必须是非空字符串")
    if len(expression) > MAX_EXPRESSION_LENGTH:
        raise ExpressionRejected(
            INVALID_REQUEST, f"表达式超过上界 {MAX_EXPRESSION_LENGTH} 字（R-A 的同精神）"
        )
    return expression


def _check_chars(text: str) -> None:
    if _ALLOWED_CHARS_RE.match(text) is None:
        raise ExpressionRejected(
            INVALID_REQUEST,
            "表达式含白名单之外的字符（下标 / 字符串 / 属性访问 / 内置名字一律拒）",
        )
    if "." in _NUMBER_RE.sub(" ", text):
        raise ExpressionRejected(INVALID_REQUEST, "`.` 只允许出现在数字里（属性访问一律拒）")


def _check_identifiers(text: str) -> None:
    for name in sorted(set(_IDENTIFIER_RE.findall(text))):
        if "__" in name or name in _BANNED_NAMES:
            raise ExpressionRejected(INVALID_REQUEST, f"标识符 {name!r} 不在允许范围内")
