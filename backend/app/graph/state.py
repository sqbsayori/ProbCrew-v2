"""节点输入 / 输出约定 —— W0c · 卡3 · 第②步（``docs/06 §12.3``）。

本模块只定三样形状，不含任何业务逻辑（``docs/03 §3.2`` 第 2 条）：

- :class:`GraphState` —— 图状态的约定键（只列 W0 用到的）；
- :class:`EventSink` —— 落事件的唯一口子：只记 ID 与动作（R-O 口径，``docs/02 §1.4``
  规则 1；字段名规则与 ``app.core.logging`` 是同一条）；
- :class:`ToolBox` —— 按节点的允许集合放行工具调用（R-Q ① 的运行时载体：声明之外的
  调用当场拒绝，而不是「约定好不要调」）。

★ 14 类事件的 payload 形状属 W3（卡3「不做什么」）—— 本模块只保证「事件落了」与
「工具失败可见」（R-Q ③）。

★ 只允许单 worker：图检查点是内存态（``docs/02 §5.1``）。启动检测在卡1 的 ``main.py``
（``docs/02 §8-15``），不在本模块。

依赖方向（``docs/03 §3.1``）：``graph/`` 禁止 import ``api/``。
"""
from __future__ import annotations

import importlib
from dataclasses import dataclass
from typing import Any, Mapping, Protocol, TypedDict

from app.tools import ToolResult

#: R-O 口径（``docs/02 §1.4`` 规则 1）：事件里除 ``*_id`` 之外只允许这几个短代码字段
#: —— 与 ``app.core.logging`` 的 ``ALLOWED_NON_ID_FIELDS`` 是同一条口径。
ALLOWED_NON_ID_FIELDS = frozenset({"status", "reason", "level"})

#: 单字段值长度上界（与 ``app.core.logging`` 的 ``_VALUE_MAX`` 同口径）。
VALUE_MAX_LENGTH = 128

EVENT_TYPE_MAX_LENGTH = 64


class GraphState(TypedDict, total=False):
    """图状态的约定键 —— 只列 W0 已用到的（其余键属 W2 / W3）。"""

    run_id: str
    session_id: str
    query: str


class EventFieldViolation(ValueError):
    """事件里出现了非 ID / 非短代码字段 —— R-O 的机器可执行形式。"""


@dataclass(frozen=True)
class EventRecord:
    """一条已落地的事件：类型 + 只含 ID 与短代码的字段。"""

    event_type: str
    fields: Mapping[str, str]


class EventSink(Protocol):
    """落事件的唯一口子。★ 14 类事件的 payload 形状属 W3（卡3「不做什么」）。"""

    def emit(self, event_type: str, /, **fields: Any) -> None:
        ...


class RecordingEventSink:
    """把事件记在内存里 —— W0 的落地形态（真正的 SSE 帧属 W3）。"""

    def __init__(self) -> None:
        self.records: list[EventRecord] = []

    def emit(self, event_type: str, /, **fields: Any) -> None:
        _check_event(event_type, fields)
        self.records.append(
            EventRecord(event_type, {key: str(value) for key, value in fields.items()})
        )

    @property
    def event_types(self) -> list[str]:
        return [record.event_type for record in self.records]


def _check_event(event_type: str, fields: Mapping[str, Any]) -> None:
    if not isinstance(event_type, str) or not 1 <= len(event_type) <= EVENT_TYPE_MAX_LENGTH:
        raise EventFieldViolation(f"event_type 必须是 1–{EVENT_TYPE_MAX_LENGTH} 字的字符串")
    for key, value in fields.items():
        if not (key.endswith("_id") or key in ALLOWED_NON_ID_FIELDS):
            raise EventFieldViolation(
                f"事件字段 {key!r} 既不是 *_id 也不是 {sorted(ALLOWED_NON_ID_FIELDS)} —— "
                f"R-O 只记 ID 与动作（内容既不进日志、也不进事件）"
            )
        if isinstance(value, (dict, list, tuple, set)):
            raise EventFieldViolation(f"事件字段 {key!r} 的值不能是容器 —— 那是内容，不是标识")
        if len(str(value)) > VALUE_MAX_LENGTH:
            raise EventFieldViolation(
                f"事件字段 {key!r} 的值超过 {VALUE_MAX_LENGTH} 字 —— 疑似内容而非标识"
            )


class ToolNotAllowed(RuntimeError):
    """调用了该节点未声明的工具 —— R-Q ① 在运行时的拒绝形状。"""


@dataclass(frozen=True)
class ToolBox:
    """按节点的允许集合放行工具调用（R-Q ① 的运行时载体）。"""

    node: str
    allowed: frozenset[str]

    @classmethod
    def for_node(cls, node: str) -> "ToolBox":
        """从显式注册表取该节点的允许集合 —— 集合只有一处来源（``graph/registry.py``）。"""
        from app.graph.registry import RegistryError, load_registry  # 局部 import：避免包内循环

        for item in load_registry():
            if item.name == node:
                return cls(node=node, allowed=frozenset(item.tools or ()))
        raise RegistryError(
            f"节点 {node!r} 未注册 —— 先写进 graph/registry.py 的显式注册表（docs/03 §7①）"
        )

    def call(self, tool: str, /, **kwargs: Any) -> ToolResult:
        """调一个工具；未声明即拒绝（R-Q ①），工具没有 ``ENTRY`` 也拒绝。"""
        if tool not in self.allowed:
            raise ToolNotAllowed(
                f"节点 {self.node} 的允许集合是 {sorted(self.allowed)}，不含 {tool!r} —— "
                f"R-Q ①：允许集合是非空显式清单，声明之外的调用一律拒绝"
            )
        module = importlib.import_module(f"app.tools.{tool}")
        entry = getattr(module, "ENTRY", None)
        if not callable(entry):
            raise ToolNotAllowed(
                f"工具 {tool!r} 没有可调用的 ENTRY —— 工具模块必须暴露 ENTRY（app/tools/__init__.py）"
            )
        return entry(**kwargs)


@dataclass(frozen=True)
class NodeContext:
    """节点运行时能拿到的两样东西 —— 别的都不给（越权即 R-Q ①）。"""

    node: str
    tools: ToolBox
    events: EventSink

    @classmethod
    def for_node(cls, node: str, events: "EventSink | None" = None) -> "NodeContext":
        return cls(node=node, tools=ToolBox.for_node(node), events=events or RecordingEventSink())


def emit_node_start(ctx: NodeContext, state: Mapping[str, Any]) -> None:
    ctx.events.emit("node.start", **_ids_of(state))


def emit_node_end(ctx: NodeContext, state: Mapping[str, Any], *, ok: bool) -> None:
    ctx.events.emit("node.end", status="ok" if ok else "failed", **_ids_of(state))


def emit_tool_result(ctx: NodeContext, result: ToolResult) -> None:
    """工具结果落事件 —— ★ 失败也进事件流（R-Q ③：不得静默吞掉）。"""
    if result.ok:
        ctx.events.emit("tool.result", status="ok")
    else:
        ctx.events.emit("tool.result", status="failed", reason=result.reason or "unknown")


def tool_update(*results: "tuple[str, ToolResult]") -> dict[str, dict[str, Any]]:
    """把工具结果收成状态更新 —— 字段语义属 W3，本轮只保证形状可断言。"""
    return {name: {"ok": result.ok, "reason": result.reason} for name, result in results}


def _ids_of(state: Mapping[str, Any]) -> dict[str, str]:
    fields: dict[str, str] = {}
    for key in ("run_id", "session_id"):
        value = state.get(key)
        if isinstance(value, str) and value:
            fields[key] = value
    return fields
