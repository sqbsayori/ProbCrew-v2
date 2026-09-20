"""验证节点空壳 —— W0c · 卡3 · 第②步（``docs/06 §12.3``）。

只做「取输入 → 调 ``tools`` → 落事件」。

★ 这里没有验证逻辑、没有级别判定 —— 验证报告（级别 A–D · 逐项检查 · 置信度，``docs/02 §4.4``）
属 W3；本节点只把工具结果搬进状态并落事件。

允许调用的工具由注册表决定（``graph/registry.py``）：``retrieval``。
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
    """取输入（``query``）→ 调 ``retrieval`` → 落事件 → 返回状态更新。"""
    emit_node_start(ctx, state)
    query = str(state.get("query") or "")
    grounding = ctx.tools.call("retrieval", query=query)
    emit_tool_result(ctx, grounding)
    emit_node_end(ctx, state, ok=grounding.ok)
    return {"verify": tool_update(("retrieval", grounding))}
