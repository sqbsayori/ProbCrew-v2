"""验证节点 —— W3 · S4 的**真实现**（`docs/02 §4.4` 的验证判定表）。

**为什么必须有这一跳**：`verification.report` **每答必发**（F3「无标签不出结果」），
而旧仓的病是"代码走了流程、却一步真验证都没做"（级别恒为 D）。本节点把
`docs/02 §4.4` 的判定表落成代码：

1. **适用性**：能从 state 里取到 `expression`（待重算的式子）**与** `answer`（解答给出的结果）
   ⇒ 两个 SymPy 手段**都适用**，都跑；任一个"未执行"（`parse_error` / `timeout` / 入参不可接受）
   ⇒ 记进 `missing_methods`（★ 未执行**不算通过、也不算不通过**，`docs/02 §4.4②`）。
   取不到其中任何一个（例如讲解类产出）⇒ 两个手段**不适用**，不进 `missing_methods`。
2. **`heuristic` 兜底**：永远有一条结构检查，但它**不抬高 `level`**（契约：只有前四项手段能抬高）。
3. **级别由手段封顶**（`docs/02 §4.4①`）：有 `sympy_recompute` ⇒ A · 只有
   `symbolic_equivalence` ⇒ B · `missing_methods` 非空 ⇒ **封顶到 C** · 否则 D。

★ **本节点不重算、不改答案**：重算（上限 1 次、换一条策略）由 **runner** 编排
（`docs/02 §4.2-4`）—— 节点只回答"这一版解答验证到了什么程度"。

允许调用的工具由注册表决定（`graph/registry.py`）：`retrieval` · `sympy_recompute` ·
`symbolic_equivalence`。
"""
from __future__ import annotations

import time
from typing import Any, Mapping

from app.graph.state import (
    NodeContext,
    elapsed_ms,
    emit_node_end,
    emit_node_start,
    emit_tool_call,
    emit_tool_result,
    tool_update,
)

LABEL = "验证"

#: 两个 SymPy 手段（名字取自契约 `check.method` 的枚举，**不自造**）。
SYMPY_METHODS = ("sympy_recompute", "symbolic_equivalence")

#: 兜底手段 —— 它**不抬高 level**（契约原文），只是让 `passed` 有个真实含义。
HEURISTIC_METHOD = "heuristic"
HEURISTIC_NAME = "结构检查"

#: `cap(level)`（照抄 `contracts/verification.schema.json` 的 `x-cap-by-level`，不重新定义）。
CAP_BY_LEVEL = {"A": 1.0, "B": 0.9, "C": 0.85, "D": 0.4}

#: 级别给人的中文名（契约只约束 `levelLabel` 的长度；取值照 `docs/02 §4.4` 的"含义"列）。
LEVEL_LABELS = {
    "A": "数值可完全验证",
    "B": "符号可强验证",
    "C": "应用题，可交叉验证",
    "D": "证明题/开放题，不做承诺",
}

_SUMMARY_MAX = 300


def run(state: Mapping[str, Any], ctx: NodeContext) -> Mapping[str, Any]:
    """取输入（`expression` / `answer`）→ 调手段 → **装配 `verification.report`** → 落事件。"""
    emit_node_start(ctx, label=LABEL)
    query = str(state.get("query") or "")
    started = time.perf_counter()

    # 回查教材出处做 grounding（W0c 的这一步保留；检索算法属 W4，现在恒 cite_unavailable）
    emit_tool_call(ctx, "retrieval", args={"query": query})
    grounding = ctx.tools.call("retrieval", query=query)
    emit_tool_result(ctx, "retrieval", grounding, duration_ms=elapsed_ms(started))

    checks, missing = _run_checks(state, ctx)
    report = build_report(
        checks,
        missing=missing,
        recomputed=int(state.get("recompute_attempt") or 0) >= 1,
    )
    summary = summarize(report)
    ctx.events.emit("verification.report", report=report, summary=summary)

    elapsed = elapsed_ms(started)
    # ★ `ok` 派生自"报告真的带上了检查项"（R-Q ②：不写字面量）
    emit_node_end(ctx, ok=bool(report.get("checks")), duration_ms=elapsed, summary=summary)
    update: "dict[str, Any]" = {"verify": tool_update(("retrieval", grounding))}
    update["verification"] = report
    return update


def _run_checks(
    state: Mapping[str, Any], ctx: NodeContext
) -> "tuple[list[dict[str, Any]], list[str]]":
    """跑这一版能跑的手段；返回 `(checks, missing_methods)`。"""
    expression = str(state.get("expression") or "").strip()
    answer = str(state.get("answer") or "").strip()
    checks: "list[dict[str, Any]]" = []
    missing: "list[str]" = []

    if expression and answer:
        calls = (
            ("sympy_recompute", {"expression": expression, "expected": answer}),
            ("symbolic_equivalence", {"lhs": expression, "rhs": answer}),
        )
        for tool, args in calls:
            started = time.perf_counter()
            emit_tool_call(ctx, tool, args=args)
            result = ctx.tools.call(tool, **args)
            emit_tool_result(ctx, tool, result, duration_ms=elapsed_ms(started))
            check = result.value if result.ok else None
            if isinstance(check, Mapping):
                checks.append(dict(check))
            else:
                # ★ "手段没跑成" ≠ "检查不通过"：记进 missing，由级别封顶如实反映（§4.4②）。
                missing.append(tool)

    checks.append(_structure_check(state))
    return checks, missing


def _structure_check(state: Mapping[str, Any]) -> "dict[str, Any]":
    """兜底的结构检查（`heuristic`）—— **永不抬高 level**，只让 `passed` 有实际含义。"""
    steps = state.get("steps") or []
    kind = str(state.get("artifact_kind") or "")
    if kind == "solution":
        count = len(steps) if isinstance(steps, list) else 0
        passed = count >= 3
        detail = f"解答结构：{count} 步。"
    else:
        passed = bool(str(state.get("answer_text") or "").strip())
        detail = "讲解类产出：确认有可展示的正文。"
    return {
        "name": HEURISTIC_NAME,
        "passed": bool(passed),
        "method": HEURISTIC_METHOD,
        "detail": detail,
    }


def build_report(
    checks: "list[dict[str, Any]]",
    *,
    missing: "list[str]",
    recomputed: bool,
) -> "dict[str, Any]":
    """装配 `VerificationReport`（八项必填，**不多不少**）。

    四条 `x-invariants` 都落在这里：`level` ≤ 手段封顶 · `confidence` ≤ `cap(level)` ·
    `uncertain ⇒ level="D"` · `missing_methods` 非空 ⇒ `level ≤ "C"`。
    """
    passed = all(bool(check.get("passed")) for check in checks) if checks else False
    level = level_for(checks, missing)
    uncertain = bool(recomputed and not passed)
    if uncertain:
        level = "D"
    cap = CAP_BY_LEVEL[level]
    return {
        "level": level,
        "levelLabel": LEVEL_LABELS[level],
        "passed": passed,
        "confidence": round(cap if passed else cap * 0.5, 3),
        "checks": list(checks),
        "uncertain": uncertain,
        "missing_methods": list(missing),
        "recomputed": bool(recomputed),
    }


def level_for(checks: "list[dict[str, Any]]", missing: "list[str]") -> str:
    """按**手段封顶**算级别（`docs/02 §4.4①`）。"""
    methods = {str(check.get("method") or "") for check in checks}
    if "sympy_recompute" in methods:
        level = "A"
    elif "symbolic_equivalence" in methods:
        level = "B"
    else:
        level = "D"
    if missing and level in ("A", "B"):
        level = "C"
    return level


def summarize(report: Mapping[str, Any]) -> str:
    """给人看的一句话摘要（`verificationReportPayload.summary`，**直接展示给学生**）。"""
    if report.get("passed"):
        text = f"验证通过（{report['levelLabel']}）。"
    else:
        failed = (
            "、".join(
                str(check.get("name"))
                for check in report.get("checks") or []
                if not check.get("passed")
            )
            or "检查"
        )
        if report.get("uncertain"):
            text = f"验证未通过：{failed}；重算后仍不通过 —— 已按存疑输出（级别 D）。"
        else:
            text = f"验证未通过：{failed}；将换一条策略重算一次。"
    return text[:_SUMMARY_MAX]


__all__ = [
    "CAP_BY_LEVEL",
    "LABEL",
    "LEVEL_LABELS",
    "SYMPY_METHODS",
    "build_report",
    "level_for",
    "run",
    "summarize",
]
