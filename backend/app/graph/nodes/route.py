"""路由节点 —— W3 · S4 的**真实现**（`docs/02 §4.2` 的第一跳：意图分类）。

职责边界照旧（`docs/03 §3.2` 第 2 条）：本节点**不判定对错、不匹配错因、不做分布计算** ——
它只做「取输入 → 调 `llm` → 把回答归一成四个意图之一 → 落事件」。

**四个意图取自契约**（`app.graph.events.INTENTS` ← `contracts/events.schema.json` 的
`planPayload.intent`），不自造；本轮只有 `solve` 是"解答"（F2 的"步骤 ≥ 3"只约束它，
`explain` / `lookup` / `visualize` 不受约束）。

★ **认不出意图时不冒充**：模型没按格式回答 ⇒ 按 :data:`FALLBACK_INTENT`（`explain`）处理，
并把原因写进 `route_reason`（N2：如实说明降级，而不是假装认出来了）。

允许调用的工具由注册表决定（`graph/registry.py`）：`llm`。
"""
from __future__ import annotations

import re
import time
from typing import Any, Mapping

from app.graph.events import INTENTS
from app.graph.state import (
    NodeContext,
    elapsed_ms,
    emit_node_end,
    emit_node_start,
    emit_tool_call,
    emit_tool_result,
    endpoint_label,
    tool_update,
)

#: `node.start` 的 `label`（可读名；注册名仍是 `route`）。
LABEL = "意图识别"

#: 认不出时的兜底意图 —— ★ 取 `explain` 而不是 `solve`：`explain` 不声称"这是解答"，
#: 因此 F2 的"步骤 ≥ 3"对它不适用，降级不会伪装成解题。
FALLBACK_INTENT = "explain"

_SYSTEM_PROMPT = (
    "把学生的问题归到下面四类中的**一类**，只回答那个词，不要解释、不要标点：\n"
    "- solve：需要算出数值或代数结果的题\n"
    "- explain：问概念、定义、为什么\n"
    "- lookup：问某个公式或定理的条目\n"
    "- visualize：要图形、分布曲线、可视化\n"
)

#: 意图关键词（顺序无关 —— 取**在回答里出现得最早**的那一个）。
_INTENT_RE = re.compile("|".join(INTENTS))

_PREVIEW_MAX = 40


def run(state: Mapping[str, Any], ctx: NodeContext) -> Mapping[str, Any]:
    """取输入（``query``）→ 调 `llm` 分类 → 落事件 → 返回状态更新。"""
    emit_node_start(ctx, label=LABEL)
    query = str(state.get("query") or "")
    started = time.perf_counter()
    emit_tool_call(ctx, "llm", args=endpoint_label(ctx))

    result = ctx.call_model(
        (
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": query},
        )
    )
    elapsed = elapsed_ms(started)
    emit_tool_result(ctx, "llm", result, duration_ms=elapsed)

    if not result.ok:
        # ★ 缺凭据 / 端点不可达 ⇒ 这一跳就断了：**不编一个意图往下走**（N2 · N14）。
        emit_node_end(ctx, ok=False, duration_ms=elapsed, summary=f"意图识别失败（{result.reason}）")
        return {"route": tool_update(("llm", result))}

    text = result.value.get("text") if isinstance(result.value, Mapping) else ""
    intent, reason = read_intent(str(text or ""))
    # ★ `ok` 派生自那条工具结果（R-Q ②：成功标记必须来自真实执行结果，不写字面量）
    emit_node_end(ctx, ok=result.ok, duration_ms=elapsed, summary=f"意图：{intent}")
    update: "dict[str, Any]" = {"route": tool_update(("llm", result))}
    update.update({"intent": intent, "route_reason": reason})
    return update


def read_intent(text: str) -> "tuple[str, str]":
    """从模型回答里读出意图；**认不出就降级**（返回 `(意图, 原因)` 两件）。

    ★ 返回原因是为了让 `plan.reason` 能如实说明"这个意图是怎么来的" —— 尤其是兜底那一次。
    """
    lowered = text.lower()
    found = _INTENT_RE.search(lowered)
    if found is not None:
        return found.group(0), "模型回答里出现了意图关键词。"
    preview = text.strip()[:_PREVIEW_MAX] or "（空回答）"
    return FALLBACK_INTENT, f"模型回答无法识别为四个意图之一（“{preview}”），按讲解处理。"


__all__ = ["FALLBACK_INTENT", "LABEL", "read_intent", "run"]
