"""求解节点空壳 —— W0c · 卡3 · 第②步（``docs/06 §12.3``）。

只做「取输入 → 调 ``tools`` → 落事件」。

★ 这里没有求解逻辑、没有判定、没有错因匹配 —— 逐步过程（``steps[]``）与判定链属
W2 / W3；本节点只把工具结果搬进状态并落事件。

允许调用的工具由注册表决定（``graph/registry.py``）：``retrieval`` · ``llm``。
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
    """取输入（``query``）→ 调 ``retrieval`` 与 ``llm`` → 落事件 → 返回状态更新。"""
    emit_node_start(ctx, state)
    query = str(state.get("query") or "")

    found = ctx.tools.call("retrieval", query=query)
    emit_tool_result(ctx, found)

    answer = ctx.tools.call("llm", messages=({"role": "user", "content": query},))
    emit_tool_result(ctx, answer)

    emit_node_end(ctx, state, ok=found.ok and answer.ok)
    return {"solve": tool_update(("retrieval", found), ("llm", answer))}
