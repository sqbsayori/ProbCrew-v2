"""路由节点空壳 —— W0c · 卡3 · 第②步（``docs/06 §12.3``）。

只做「取输入 → 调 ``tools`` → 落事件」。

★ 这里没有意图分类逻辑 —— 分类规则与路由枚举（``solve`` / ``explain`` / ``lookup`` /
``visualize``，``docs/02 §4.2``）属 W3；本节点不判定对错、不匹配错因（那些只在
``app.domain`` 里有一份）。

允许调用的工具由注册表决定（``graph/registry.py``）：``llm``。
"""
from __future__ import annotations

from typing import Any, Mapping

from app.graph.state import (
    NodeContext,
    emit_node_end,
    emit_node_start,
    emit_tool_result,
    tool_update,
)


def run(state: Mapping[str, Any], ctx: NodeContext) -> Mapping[str, Any]:
    """取输入（``query``）→ 调 ``llm`` → 落事件 → 返回状态更新。"""
    emit_node_start(ctx, state)
    query = str(state.get("query") or "")
    result = ctx.tools.call("llm", messages=({"role": "user", "content": query},))
    emit_tool_result(ctx, result)
    emit_node_end(ctx, state, ok=result.ok)
    return {"route": tool_update(("llm", result))}
