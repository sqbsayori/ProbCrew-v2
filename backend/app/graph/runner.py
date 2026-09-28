"""编排运行器 —— W3 · S4（`docs/06 §14.3` 卡 W3 的交付第 2 / 3 项）。

职责：把三个节点按 `docs/02 §4.2` 的流水线串起来、**边跑边发帧**，并把 run 的快照留在
**进程内存**里（★ 不持久化：重启后 `get_snapshot` 返回 `None` ⇒ 由域2 的 api 层转
**404 `not_found`** —— `docs/02 §6.1` 的 run 生命周期已裁定）。

**帧的顺序**（契约只规定帧信封与"终帧必须显式"，**不规定各帧的相对顺序**）::

    run.start → context.resolved → [route 的 node.*] → plan
              → [solve 的 node.* / tool.* / artifact] → [verify 的 node.* / verification.report]
              →（验证未通过时**再跑一轮** solve + verify：重算上限 1 次）
              → done / error

★ **为什么 `plan` 排在 `route` 之后**：`plan.intent` 由路由节点**分类得出**（计划 §4.2③）——
先发 plan 就只能**编一个意图**，那是 N2 明令不许的。`plan.steps` 列的是**还没跑的**两个节点。

★ **同步工作一律丢线程池**（`docs/03 §3.2` 第 3 条 · `docs/02 §4.2-8`）：节点用
`asyncio.to_thread` 执行 —— 否则 N20 承诺的 10 路并发会退化成串行。

★ **取消**（`docs/02 §4.2-2`）：客户端断开 ⇒ 不再往下走 · run **从活跃集合移除** · 快照置
**`cancelled`**。★ 这个取值是 `_plan/提案-01` 提的契约增补，**R1 批准前它只是内部状态**，
不外发（见 `_plan/S0-事件payload核对表.md` 的 §四 第 2 条）。
★ 已知边界（诚实登记）：`to_thread` 里那一跳**无法被强杀**（Python 没有可移植的取消机制）——
`cancelled` 的语义是"**我不再往下走**"，不是"那一跳停了"。

**本轮不引入 LangGraph 的 `StateGraph` / checkpointer**（登记为偏差，待域1 裁决）：
S4 的判据（节点级用例 · `steps` 两处逐值一致 · 断开后活跃图清空）都不依赖它；HITL 的
`interrupt()` 与超时取值属**阶段门 C 第 1 项**（`docs/06 §5`）；而 checkpointer 会带进
"重启后 run 可否恢复"的语义面，与 `docs/02 §5.1` 的「内存态 · 重启 404」**直接冲突**。

依赖方向（`docs/03 §3.1`）：`graph/` 允许 import `core/` / `domain/` / `tools/`，**禁止 import `api/`**。
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable, Mapping

from app.core.errors import ERROR_CODES
from app.graph.events import Frame, FrameSink
from app.graph.nodes import route, solve, verify
from app.graph.state import NodeContext

#: 已终结快照的**上限**（首次取值，见 `_plan/提案-01`）：单 worker 下防无界增长。
MAX_TERMINAL_SNAPSHOTS = 20

STATUS_RUNNING = "running"
STATUS_DONE = "done"
STATUS_ERROR = "error"

#: `hitl_pending` 是契约枚举里的取值之一，但 HITL 的挂起与超时属**阶段门 C 第 1 项**
#: （`docs/06 §5`）⇒ 本轮不会产生它；留着常量是为了让快照形状与契约对得上。
STATUS_HITL_PENDING = "hitl_pending"

#: ★ **内部状态**：提案 01（`runSnapshot.status` 增补 `cancelled`）批准前不外发。
STATUS_CANCELLED = "cancelled"

#: 终态（进这个集合就参与"最多 20 条"的淘汰）。
TERMINAL_STATUSES = (STATUS_DONE, STATUS_ERROR, STATUS_CANCELLED)

#: 没有上下文时的**显式降级原因**（F1：不静默）。前端只渲染，不拼文案（R-I 的精神）。
NO_CONTEXT_REASON = "请求未携带上下文（context 缺省）—— 按无上下文作答。"

#: 节点名 → 入口。★ 与 `graph/registry.py` 的注册名**一一对应**（有用例在守）——
#: 而 R-Q ① 的允许集合仍由 `NodeContext.for_node` 经注册表强制。
NODE_ENTRIES: "dict[str, Any]" = {
    "route": route.run,
    "solve": solve.run,
    "verify": verify.run,
}

#: 流水线里**还没跑**的两跳（`route` 先跑，见模块头）。
PLAN_NODES = ("solve", "verify")
_NODE_LABELS = {"route": route.LABEL, "solve": solve.LABEL, "verify": verify.LABEL}

#: 对外 `code` → 给用户看的中文（`error.message`，1–200 字）。
ERROR_MESSAGES = {
    "model_key_missing": "还没有配置模型凭据：请到设置页填你自己的端点与 Key（N14）。",
    "model_key_invalid": "模型凭据被端点拒绝了：请检查 Key 是否正确。",
    "model_quota_exceeded": "模型额度不足：请检查你的账户余额。",
    "model_unreachable": "模型端点不可达：请检查网络或端点地址。",
    "server_busy": "当前同时答题的同学较多，请稍后重试。",
    "dependency_unavailable": "答疑所需的依赖暂时不可用，请稍后重试。",
    "internal_error": "服务出了点问题，请稍后重试。",
}
_MESSAGE_MAX = 200


@dataclass
class RunRecord:
    """一次 run 的进程内快照（★ 不持久化 —— 重启后查不到就是 404）。"""

    run_id: str
    session_id: str
    status: str = STATUS_RUNNING
    steps: "list[Any]" = field(default_factory=list)
    verification: "Mapping[str, Any] | None" = None
    hitl: "Mapping[str, Any] | None" = None
    updated_at: float = 0.0


class RunStore:
    """进程内的 run 表。

    ★ **只在内存**：`snapshot()` 查不到就返回 `None`（⇒ 域2 转 404 `not_found`）。
    ★ 淘汰规则（首次取值）：**只淘汰已终结的**，超过 `max_terminal` 条时按 `updated_at`
    淘汰最旧的；**活跃的 run 一条都不淘汰**。
    """

    def __init__(
        self,
        *,
        max_terminal: int = MAX_TERMINAL_SNAPSHOTS,
        clock: "Callable[[], float]" = time.time,
    ) -> None:
        self._records: "dict[str, RunRecord]" = {}
        self._max_terminal = max_terminal
        self._clock = clock

    def start(self, *, run_id: str, session_id: str) -> RunRecord:
        record = RunRecord(run_id=run_id, session_id=session_id, updated_at=self._clock())
        self._records[run_id] = record
        return record

    def get(self, run_id: str) -> "RunRecord | None":
        return self._records.get(run_id)

    def update(self, run_id: str, **fields: Any) -> None:
        record = self._records.get(run_id)
        if record is None:
            return
        for key, value in fields.items():
            setattr(record, key, value)
        record.updated_at = self._clock()

    def finish(self, run_id: str, *, status: str) -> None:
        self.update(run_id, status=status)
        self._evict()

    def snapshot(self, run_id: str) -> "dict[str, Any] | None":
        """`GET /api/runs/{id}` 的响应形状（契约 `definitions.runSnapshot`）。

        ★ `steps` **不复制** —— 契约要求它与该次解答事件的 `payload.steps` 是
        **同一次生成的同一份数据**（F2 的两处落点）。
        """
        record = self._records.get(run_id)
        if record is None:
            return None
        snapshot: "dict[str, Any]" = {
            "run_id": record.run_id,
            "status": record.status,
            "steps": record.steps,
            "updated_at": record.updated_at,
        }
        if record.hitl is not None:
            snapshot["hitl"] = record.hitl
        if record.verification is not None:
            snapshot["verification"] = record.verification
        return snapshot

    @property
    def active(self) -> "list[str]":
        """还没终结的 run —— 客户端断开后**必须**从集合里消失（`docs/02 §4.2-2`）。"""
        return [
            run_id
            for run_id, record in self._records.items()
            if record.status not in TERMINAL_STATUSES
        ]

    def _evict(self) -> None:
        terminal = [
            record for record in self._records.values() if record.status in TERMINAL_STATUSES
        ]
        if len(terminal) <= self._max_terminal:
            return
        terminal.sort(key=lambda record: record.updated_at)
        for record in terminal[: len(terminal) - self._max_terminal]:
            self._records.pop(record.run_id, None)


#: 进程内的默认 run 表（★ 单 worker 的前提，见 `docs/02 §8-15`）。
RUNS = RunStore()


def get_snapshot(run_id: str) -> "dict[str, Any] | None":
    """`GET /api/runs/{id}` 的薄接口：`None` ⇒ 由域2 的 api 层转 **404 `not_found`**。"""
    return RUNS.snapshot(run_id)


def error_code_for(reason: "str | None") -> str:
    """对内原因 → 对外 `code`（只能是 `error.schema.json` 的 16 个之一，**不自造**）。

    四个模型类 code 与 `ERROR_CODES` **同名同值**，原样透传；其余对内原因
    （`parse_error` / `timeout` / `not_implemented` / `invalid_response` / `payload_rejected` 等）
    一律合并成 **`dependency_unavailable`**（503）—— 它们都表示"某个依赖这一跳没走通"，
    而原样发给前端会让对外的 `code` 枚举失控。
    """
    if reason is None:
        return "internal_error"
    if reason in ERROR_CODES:
        return reason
    return "dependency_unavailable"


def message_for(code: str) -> str:
    """`error.message`（给人看的中文，1–200 字；**不回显内部细节**）。"""
    return ERROR_MESSAGES.get(code, ERROR_MESSAGES["internal_error"])[:_MESSAGE_MAX]


class FrameCollector:
    """节点看到的 ``events`` —— 转发给真正的 :class:`FrameSink`，并把帧**收下来**。

    ★ **为什么需要它**：节点是同步函数、帧又由节点自己发（``ctx.events.emit``），
    而"发给客户端的帧"只能由**异步生成器** ``stream_run`` yield 出去 ⇒ 中间必须有一个人
    把节点发出去的帧带回给 runner。本类就是这个搬运工；**不是重放缓冲**
    （``docs/02 §4.2-1``：服务端不做缓冲），这些帧**只用于这一次 yield**。

    ★ **本轮的顺序与代价（诚实登记）**：帧在**这一跳跑完之后**按序 yield ⇒ **同一跳内部不是
    逐帧流式的**（`node.delta` 会被攒到跳末）。计划第七章第 7 条已写明"本轮不要求上游流式、
    N7 的'讲解首字'是 SHOULD 且本轮无门"；要做真流式就得让工作线程把帧经
    ``loop.call_soon_threadsafe`` 推给事件循环，属后续批次。

    ★ **公开**：域2 若要自己驱动节点（例如调试端点）、或后续批次换成真流式，都从这一个口子走 ——
    这样"帧由谁转发"只有一处实现。
    """

    def __init__(self, sink: FrameSink) -> None:
        self._sink = sink
        self.frames: "list[Frame]" = []

    def emit(self, event_type: str, /, **payload: Any) -> Frame:
        frame = self._sink.emit(event_type, **payload)
        self.frames.append(frame)
        return frame


async def stream_run(
    *,
    run_id: str,
    session_id: str,
    query: str,
    context: "Mapping[str, Any] | None" = None,
    endpoint: "Mapping[str, Any] | None" = None,
    credential: "str | None" = None,
    store: "RunStore | None" = None,
) -> "AsyncIterator[Frame]":
    """跑一次答疑并逐帧 yield；**最后一帧一定是 `done` 或 `error`**（取消是唯一例外）。

    `endpoint` / `credential` 是**请求级**的（N14）：由域2 的 `api/` 从请求里取出后传进来；
    它们**不进任何帧**（`tool.call.args` 只用 `state.endpoint_label` 那种只含标识的视图）。
    """
    runs = store if store is not None else RUNS
    runs.start(run_id=run_id, session_id=session_id)
    sink = FrameSink()
    state: "dict[str, Any]" = {
        "run_id": run_id,
        "session_id": session_id,
        "query": query,
        "context": context,
        "recompute_attempt": 0,
    }
    finished = False
    try:
        yield sink.emit("run.start", run_id=run_id, session_id=session_id, query=query)
        yield sink.emit(
            "context.resolved",
            context=context,
            reason=None if context else NO_CONTEXT_REASON,
        )

        # ① 路由：意图分类（★ 它失败就没有意图可发 —— 不编一个往下走，N2）
        route_update, route_frames = await _drive(
            "route", state, sink, endpoint=endpoint, credential=credential
        )
        for frame in route_frames:
            yield frame
        state.update(route_update)
        if not state.get("intent"):
            code = error_code_for(_first_reason(route_update))
            runs.finish(run_id, status=STATUS_ERROR)
            yield sink.emit("error", code=code, message=message_for(code))
            finished = True
            return

        # ② 计划（★ 排在 route 之后，理由见模块头）
        yield sink.emit(
            "plan",
            intent=str(state["intent"]),
            steps=[{"node": name, "label": _NODE_LABELS[name]} for name in PLAN_NODES],
            reason=state.get("route_reason") or None,
        )

        # ③ 求解 → ④ 验证；验证不通过 ⇒ **换一条策略重算一次**（上限 1 次）
        verification: "Mapping[str, Any]" = {}
        for attempt in (0, 1):
            state["recompute_attempt"] = attempt
            if attempt:
                state["strategy_hint"] = solve.STRATEGY_HINTS[1]

            solve_update, solve_frames = await _drive(
                "solve", state, sink, endpoint=endpoint, credential=credential
            )
            for frame in solve_frames:
                yield frame
            state.update(solve_update)
            if not state.get("answer_text"):
                code = error_code_for(_first_reason(solve_update))
                runs.finish(run_id, status=STATUS_ERROR)
                yield sink.emit("error", code=code, message=message_for(code))
                finished = True
                return
            runs.update(run_id, steps=state.get("steps") or [])

            verify_update, verify_frames = await _drive(
                "verify", state, sink, endpoint=endpoint, credential=credential
            )
            for frame in verify_frames:
                yield frame
            state.update(verify_update)
            verification = state.get("verification") or {}
            runs.update(run_id, verification=verification)
            if verification.get("passed") or attempt == 1:
                break

        # ⑤ 终帧（`uncertain` 也是 `done`：它是"如实标注"，不是错误）
        final_answer = str(state.get("answer_text") or "")[:8000] or None
        runs.finish(run_id, status=STATUS_DONE)
        yield sink.emit(
            "done",
            status="uncertain" if verification.get("uncertain") else "ok",
            final_answer=final_answer,
        )
        finished = True
    except (GeneratorExit, asyncio.CancelledError):
        # ★ 客户端断开：不再往下走 + 从活跃集合移除 + 快照如实标 `cancelled`（内部状态）。
        runs.finish(run_id, status=STATUS_CANCELLED)
        sink.close(cancelled=True)
        raise
    except Exception as exc:  # noqa: BLE001 —— 终帧必须发出去（`docs/02 §6.5-2`）
        code = error_code_for(getattr(exc, "reason", None))
        runs.finish(run_id, status=STATUS_ERROR)
        yield sink.emit("error", code=code, message=message_for(code))
        finished = True
    finally:
        if finished:
            # 正常收口：**没发过终帧就是实现缺陷**（`FrameSink.close` 会当场抛）。
            sink.close()


async def _drive(
    node_name: str,
    state: "Mapping[str, Any]",
    sink: FrameSink,
    *,
    endpoint: "Mapping[str, Any] | None",
    credential: "str | None",
) -> "tuple[Mapping[str, Any], list[Frame]]":
    """跑一跳 —— 同步节点**丢线程池**（`docs/03 §3.2` 第 3 条）；返回 `(状态更新, 这一跳发的帧)`。"""
    entry = NODE_ENTRIES.get(node_name)
    if entry is None:
        raise KeyError(f"未登记的节点 {node_name!r} —— 先写进 runner.NODE_ENTRIES 与注册表")
    collector = FrameCollector(sink)
    ctx = NodeContext.for_node(node_name, events=collector, endpoint=endpoint, credential=credential)
    update = await asyncio.to_thread(entry, state, ctx)
    return update, collector.frames


def _first_reason(update: "Mapping[str, Any]") -> "str | None":
    """从节点的状态更新里取出**第一个失败工具的 reason**（`tool_update` 的形状）。"""
    for group in update.values():
        if not isinstance(group, Mapping):
            continue
        for item in group.values():
            if isinstance(item, Mapping) and not item.get("ok"):
                reason = item.get("reason")
                if isinstance(reason, str) and reason:
                    return reason
    return None


__all__ = [
    "MAX_TERMINAL_SNAPSHOTS",
    "NODE_ENTRIES",
    "NO_CONTEXT_REASON",
    "PLAN_NODES",
    "RUNS",
    "FrameCollector",
    "RunRecord",
    "RunStore",
    "STATUS_CANCELLED",
    "STATUS_DONE",
    "STATUS_ERROR",
    "STATUS_HITL_PENDING",
    "STATUS_RUNNING",
    "TERMINAL_STATUSES",
    "error_code_for",
    "get_snapshot",
    "message_for",
    "stream_run",
]
