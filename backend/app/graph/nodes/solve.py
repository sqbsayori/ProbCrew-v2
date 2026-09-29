"""求解节点 —— W3 · S4 的**真实现**（`docs/02 §4.2` 的"检索 → 求解 → `steps[]`"）。

职责边界照旧（`docs/03 §3.2` 第 2 条）：本节点**不算数、不判定对错、不匹配错因** ——
它只做「取输入 → 调 `retrieval` / `llm` → 把回答装成 `artifact` → 落事件」。
真正的计算在 ``tools/`` 的 SymPy 手段里，由 **verify 节点**调用（`docs/03 §4`）。

**两类产出，按意图分支**（`docs/02 §4.2-7`：只有 `solve` 是解答，受"步骤 ≥ 3"约束）：

| 情形 | `artifact.kind` | 形状 |
|---|---|---|
| 意图 = `solve` **且**解析出 ≥ 3 步 | `solution` | `{title?, steps[], cite_id?}` —— ★ **F2 结构断言的第一处落点** |
| 其余（含 `solve` 但模型没给出可用步骤） | `explanation` | `{title?, body}` —— **降级如实发生在事件里**（`node.end.summary` 写明原因） |

★ **绝不用占位步骤凑数**（`docs/06 §14.3` 卡 W4「不做什么」的同一条精神：下界是地板，
凑数等于把地板作废）—— 凑不出 3 步就不发 `solution`。

**出处**：`cite_id` 取自 `retrieval` 的命中；**无命中一律 `None`**，**不得凭记忆编造章节号**
（F10 · N2）。检索算法属 W4，因此现在 `retrieval` 恒 `cite_unavailable` ⇒ 这一格现在恒 `None`。

**重算（上限 1 次）**：`state["recompute_attempt"]=1` 时换一条策略（`docs/02 §4.2-4`：
重算 = **换一条策略重新求解**，不是同一次调用的重试采样）。

允许调用的工具由注册表决定（`graph/registry.py`）：`retrieval` · `llm`。
"""
from __future__ import annotations

import json
import time
from typing import Any, Mapping

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

LABEL = "求解"

#: F2 的结构断言下界（契约 `artifactPayload(kind="solution").steps.minItems`）。
MIN_STEPS = 3

#: `solutionStep` 的 `explanation` / `formula` 上界（契约 maxLength）。
STEP_FIELD_MAX = 300

#: `explanation.body` 的上界（契约 maxLength）。
BODY_MAX = 8000

#: `title` 的上界（契约 maxLength）。
TITLE_MAX = 120

#: 交给 verify 的表达式 / 答案的宽度上限（与 `tools.expression.MAX_EXPRESSION_LENGTH` 对齐）。
EXPRESSION_MAX = 2000

_SYSTEM_PROMPT = (
    "你是概率论助教。**只输出一个 JSON 对象**，不要输出任何其它字符：\n"
    '{"title": "一句话标题", "answer": "最终结果", '
    '"expression": "可被 SymPy 独立重算的表达式（纯数值或代数式，不含中文、不含单位）", '
    '"steps": [{"explanation": "这一步的说明", "formula": "这一步的式子"}]}\n'
    f"steps 至少 {MIN_STEPS} 步；每步的 explanation 与 formula 都必须非空。\n"
)

#: 重算时的"换一条策略"提示（`docs/02 §4.2-4`）。
STRATEGY_HINTS: "dict[int, str]" = {
    0: "用最直接的定义与已知公式求解。",
    1: "换一条策略：用另一种等价变形重新求解（例如先求补事件的概率、或换用另一条公式），"
    "不要重复上一条路径。",
}


def run(state: Mapping[str, Any], ctx: NodeContext) -> Mapping[str, Any]:
    """取输入（`query` / `intent` / `recompute_attempt`）→ 检索 + 求解 → 落事件 → 返回更新。"""
    emit_node_start(ctx, label=LABEL)
    query = str(state.get("query") or "")
    intent = str(state.get("intent") or "")
    attempt = int(state.get("recompute_attempt") or 0)
    hint = str(state.get("strategy_hint") or STRATEGY_HINTS.get(attempt, ""))

    # ① 检索（**可缺环**：无命中/语料未就绪 ⇒ 如实降级，不编造出处）
    started = time.perf_counter()
    emit_tool_call(ctx, "retrieval", args={"query": query})
    found = ctx.tools.call("retrieval", query=query)
    emit_tool_result(ctx, "retrieval", found, duration_ms=elapsed_ms(started))
    cite_id = first_cite(found)

    # ② 求解（唯一出网口；端点与凭据由 `NodeContext` 带进来）
    started = time.perf_counter()
    emit_tool_call(ctx, "llm", args=endpoint_label(ctx))
    answer = ctx.call_model(
        (
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": f"{hint}\n题目：{query}"},
        )
    )
    elapsed = elapsed_ms(started)
    emit_tool_result(ctx, "llm", answer, duration_ms=elapsed)

    if not answer.ok:
        emit_node_end(ctx, ok=False, duration_ms=elapsed, summary=f"求解失败（{answer.reason}）")
        update: "dict[str, Any]" = {"solve": tool_update(("retrieval", found), ("llm", answer))}
        return update

    text = str((answer.value or {}).get("text") or "") if isinstance(answer.value, Mapping) else ""
    parsed = parse_solution(text)
    steps = parsed["steps"] if parsed else []

    if intent == "solve" and len(steps) >= MIN_STEPS:
        kind = "solution"
        ctx.events.emit(
            "artifact",
            kind=kind,
            payload={"title": parsed["title"], "steps": steps, "cite_id": cite_id},
        )
        summary = f"已出解答（{len(steps)} 步 · 出处 {cite_id or '无'}）"
    else:
        kind = "explanation"
        ctx.events.emit("artifact", kind=kind, payload={"body": text[:BODY_MAX]})
        if intent == "solve":
            summary = f"模型没给出可用的 ≥{MIN_STEPS} 步过程 ⇒ 按讲解输出（不发 solution）"
        else:
            summary = f"按 {intent} 输出讲解"

    # ★ `ok` 派生自"真的产出了可展示的内容"（R-Q ②：不写字面量）
    emit_node_end(ctx, ok=bool(text), duration_ms=elapsed, summary=summary)
    update = {"solve": tool_update(("retrieval", found), ("llm", answer))}
    update.update(
        {
            "steps": steps,  # ★ 与解答事件的 payload.steps **同一个对象**（F2 两处逐值一致）
            "artifact_kind": kind,
            "answer_text": text[:BODY_MAX],  # 原始回答（`done.final_answer` 与结构检查都用它）
            "answer": (parsed or {}).get("answer", ""),
            "expression": (parsed or {}).get("expression", ""),
            "cite_id": cite_id,
        }
    )
    return update


def first_cite(result: Any) -> "str | None":
    """从检索命中里取第一条 `cite_id`；**没有命中就返回 `None`**（不得编造，F10 · N2）。"""
    value = getattr(result, "value", None)
    if not getattr(result, "ok", False) or not isinstance(value, Mapping):
        return None
    hits = value.get("hits")
    if not isinstance(hits, list) or not hits:
        return None
    first = hits[0]
    if isinstance(first, Mapping):
        cite = first.get("cite_id")
        if isinstance(cite, str) and cite:
            return cite
    return None


def parse_solution(text: str) -> "dict[str, Any] | None":
    """把模型回答解析成 ``{title, steps, answer, expression}``；**解析不了就返回 `None`**。

    ★ 返回 `None` 不是错误 —— 它意味着"模型没按约定给结构"，由调用方**如实降级**（见模块头）。
    每步两个字段都必须非空（F2），否则整份作废（**不用空步骤凑数**）。
    """
    payload = _extract_object(text)
    if payload is None:
        return None
    raw_steps = payload.get("steps")
    if not isinstance(raw_steps, list):
        return None
    steps: "list[dict[str, str]]" = []
    for item in raw_steps:
        if not isinstance(item, Mapping):
            return None
        explanation = str(item.get("explanation") or "").strip()
        formula = str(item.get("formula") or "").strip()
        if not explanation or not formula:
            return None
        steps.append(
            {"explanation": explanation[:STEP_FIELD_MAX], "formula": formula[:STEP_FIELD_MAX]}
        )
    return {
        "title": str(payload.get("title") or "").strip()[:TITLE_MAX] or None,
        "steps": steps,
        "answer": str(payload.get("answer") or "").strip()[:EXPRESSION_MAX],
        "expression": str(payload.get("expression") or "").strip()[:EXPRESSION_MAX],
    }


def _extract_object(text: str) -> "dict[str, Any] | None":
    """从（可能带 ``` 围栏或前后废话的）回答里取出第一个 JSON 对象。"""
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        payload = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


__all__ = ["LABEL", "MIN_STEPS", "STRATEGY_HINTS", "first_cite", "parse_solution", "run"]
