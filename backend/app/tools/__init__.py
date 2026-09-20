"""app.tools —— 工具层接口形状（W0c · 卡3 · 第③步，``docs/06 §12.3``）。

约定（R-Q 的施加点，``docs/03 §7②``）：

- 一个工具 = 一个模块，模块名就是注册表 ``tools`` 里的工具名（``llm`` / ``retrieval``）；
- 每个工具模块必须暴露 ``ENTRY``（唯一入口，可调用）—— ``graph.state.ToolBox`` 按它取；
- 入口返回 :class:`ToolResult`：``ok`` 必须来自真实执行结果，禁止硬编码 ``true``（R-Q ②）；
- 失败不静默：失败也返回 ``ToolResult(ok=False, reason=<短代码>)``，由节点落进事件流（R-Q ③）；
- ★ ``tools/llm.py`` 是全仓唯一运行期出网口（R-K，``docs/02 §1.6`` / ``ADR-0005 §3``）
  —— ``httpx`` / ``requests`` / ``urllib`` / ``socket`` 只能出现在那里。

依赖方向（``docs/03 §3.1``）：``tools/`` 允许 import ``core/`` / ``domain/``；
禁止 import ``api/`` / ``graph/``。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

#: 失败原因用的短代码（R-O 同口径：进日志 / 事件 / 错误体的只能是短代码，不是内容）。
NOT_IMPLEMENTED = "not_implemented"
MODEL_KEY_MISSING = "model_key_missing"

__all__ = ["MODEL_KEY_MISSING", "NOT_IMPLEMENTED", "ToolResult"]


@dataclass(frozen=True)
class ToolResult:
    """工具调用结果。

    ``ok`` 是真实执行结果（R-Q ②）；失败时 ``reason`` 给机器读的短代码，由节点写进
    事件流（R-Q ③：不得静默吞掉）。
    """

    ok: bool
    value: Any = None
    reason: "str | None" = None
