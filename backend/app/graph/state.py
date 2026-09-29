"""节点输入 / 输出约定 —— W0c · 卡3 · 第②步（``docs/06 §12.3``）。

本模块只定三样形状，不含任何业务逻辑（``docs/03 §3.2`` 第 2 条）：

- :class:`GraphState` —— 图状态的约定键（只列 W0 用到的）；
- :class:`EventSink` —— 落事件的唯一口子；**两种口径各有实现，别互相顶替**：
  ``RecordingEventSink`` = **R-O 的 ID-only**（只记 ``*_id`` 与 ``status``/``reason``/``level``，
  与 ``app.core.logging`` 同一条字段规则）· ``graph.events.FrameSink`` = **发给浏览器的契约帧**
  （``query`` / ``steps`` / 报告正文都在里面，形状由 ``contracts/events.schema.json`` 定）；
- :class:`ToolBox` —— 按节点的允许集合放行工具调用（R-Q ① 的运行时载体：声明之外的
  调用当场拒绝，而不是「约定好不要调」）。

★ **本模块的 emit 助手发的是契约帧**（W3 · S3 落地后改的）：字段名 = 契约 payload 的字段名
（``node`` / ``ok`` / ``tool`` / ``duration_ms`` / ``summary``），因为 S3 之后
``contracts/events.schema.json`` 是**唯一权威**，而这些帧的接收方是**前端**。
⇒ 它们**只对 ``FrameSink`` 成立**；把 ``RecordingEventSink`` 传进来会在第一个 ``node.start``
上抛 :class:`EventFieldViolation`（那是**故意**的：日志口径不接受内容，日志由
``core/logging.py`` 写）。

★ 14 类事件的 payload 形状在 ``graph/events.py``（卡3「不做什么」只到 W0c 为止）；
本模块只负责「取输入 → 调工具 → 落事件」这一层，**判定 / 分类逻辑一行都没有**。

★ 只允许单 worker：图检查点是内存态（``docs/02 §5.1``）。启动检测在卡1 的 ``main.py``
（``docs/02 §8-15``），不在本模块。

依赖方向（``docs/03 §3.1``）：``graph/`` 禁止 import ``api/``。
"""
from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, Sequence, TypedDict

from app.tools import ToolResult


def elapsed_ms(started: float) -> float:
    """`perf_counter()` 起点 → 毫秒（保留了 `duration_ms` 这一类字段要的小数）。"""
    import time

    return round((time.perf_counter() - started) * 1000, 3)


def endpoint_label(ctx: "NodeContext") -> "dict[str, Any] | None":
    """给 ``tool.call.args`` 用的端点**标识**视图（``provider`` / ``model``）—— **不含凭据**。

    与 ``tools.endpoint.EndpointConfig.redacted()`` 同一精神：帧里可以出现"用的哪个供应商与模型"，
    但不允许出现凭据（R-M 第 ⑦ 面）。
    """
    endpoint = ctx.endpoint
    if not isinstance(endpoint, Mapping):
        return None
    label = {key: endpoint[key] for key in ("provider", "model") if key in endpoint}
    return label or None

#: R-O 口径（``docs/02 §1.4`` 规则 1）：事件里除 ``*_id`` 之外只允许这几个短代码字段
#: —— 与 ``app.core.logging`` 的 ``ALLOWED_NON_ID_FIELDS`` 是同一条口径。
ALLOWED_NON_ID_FIELDS = frozenset({"status", "reason", "level"})

#: 单字段值长度上界（与 ``app.core.logging`` 的 ``_VALUE_MAX`` 同口径）。
VALUE_MAX_LENGTH = 128

EVENT_TYPE_MAX_LENGTH = 64


class GraphState(TypedDict, total=False):
    """图状态的约定键 —— W0 定下前三个，**W3（S4）补上后九个**。

    ★ 约定键的意义：节点之间**只经这一层**交换（``docs/03 §7①``），因此键名在这里写一次、
    在三个节点与 runner 里引用。**不是**"随手往 dict 里塞东西"的借口 —— 新增键先改这里。
    """

    run_id: str
    session_id: str
    query: str
    # ---- W3（S4）----
    context: "Mapping[str, Any] | None"  # `POST /api/chat` 的显式可选参数（F1 · N12）
    intent: str                           # route 的产出：solve / explain / lookup / visualize
    route_reason: str                     # 意图是怎么定的（含"没认出来时的降级说明"）
    answer: str                           # 解答正文（进 done.final_answer）
    answer_text: str                      # 模型这一跳的**原始回答**（`done.final_answer` 取它）
    expression: str                       # 待重算的表达式（verify 的输入之一）
    steps: "list[Any]"                    # 解答步骤（★ 与解答事件的 payload.steps **同一个对象**）
    artifact_kind: str                    # solution / explanation …
    cite_id: "str | None"                 # 教材出处；无命中必须为 None（F10 不得编造章节号）
    verification: "Mapping[str, Any]"     # 最近一次 verify 的报告（快照要用）
    recompute_attempt: int                # 0 = 首解；1 = 重算（上限 1 次，docs/02 §4.2-4）
    strategy_hint: str                    # 重算时给求解的"换一条策略"提示


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
    """节点运行时能拿到的东西 —— **且只有这些**（越权即 R-Q ①）。

    ``endpoint`` / ``credential`` 是 **W3（S4）新增**：模型凭据由**用户自己提供**（N14），
    因此它们是**请求级**的 —— 由域2 的 ``api/`` 从请求里取出来、经 ``runner.stream_run`` 传进来。
    ★ ``credential`` 用 ``repr=False``：``NodeContext`` 被顺手 ``print`` / 进日志时不得带出凭据
    （R-M 第 ③/⑥ 面）。**本模块不读配置**（配置读取的唯一权威是 ``core/config.py``）。
    """

    node: str
    tools: ToolBox
    events: EventSink
    #: 本次请求的端点三件套（``provider`` / ``model`` / ``base_url``）—— **不含凭据**。
    endpoint: "Mapping[str, Any] | None" = None
    #: ★ 本次请求的凭据（``X-Model-Key``）—— 请求级、**不进帧 / 日志 / repr**。
    credential: "str | None" = field(default=None, repr=False)

    @classmethod
    def for_node(
        cls,
        node: str,
        events: "EventSink | None" = None,
        *,
        endpoint: "Mapping[str, Any] | None" = None,
        credential: "str | None" = None,
    ) -> "NodeContext":
        return cls(
            node=node,
            tools=ToolBox.for_node(node),
            events=events or RecordingEventSink(),
            endpoint=endpoint,
            credential=credential,
        )

    def call_model(self, messages: "Sequence[Mapping[str, Any]]", **kwargs: Any) -> ToolResult:
        """经 :class:`ToolBox` 调**唯一出网口**，自动带上本次请求的端点与凭据。

        ★ 仍然走 ``ToolBox.call`` ⇒ **R-Q ① 的允许集合照旧生效**（``route`` / ``solve`` 声明了
        ``llm`` 才调得动）。凭据**只在这里进**，节点代码里不出现它 —— 这样"顺手把凭据拼进
        事件 / 日志"就没有入口（R-M 第 ⑦ 面：凭据只走请求头）。
        """
        return self.tools.call(
            "llm",
            messages=messages,
            endpoint=self.endpoint,
            credential=self.credential,
            **kwargs,
        )


def emit_node_start(ctx: NodeContext, *, label: "str | None" = None) -> None:
    """``node.start``（契约 payload：``node`` + 可选 ``label``）。"""
    ctx.events.emit("node.start", node=ctx.node, label=label)


def emit_node_end(
    ctx: NodeContext,
    *,
    ok: bool,
    duration_ms: "float | None" = None,
    summary: "str | None" = None,
) -> None:
    """``node.end`` —— 与 :func:`emit_node_start` 成对（``summary`` 是给人看的一句话）。"""
    ctx.events.emit("node.end", node=ctx.node, ok=ok, duration_ms=duration_ms, summary=summary)


def emit_tool_call(
    ctx: NodeContext, tool: str, *, args: "Mapping[str, Any] | None" = None
) -> None:
    """``tool.call`` —— ★ ``args`` **不得放凭据**（R-M 第 ⑦ 面；``graph/events.py`` 的注释同此）。"""
    ctx.events.emit("tool.call", tool=tool, node=ctx.node, args=args)


def emit_tool_result(
    ctx: NodeContext,
    tool: str,
    result: ToolResult,
    *,
    duration_ms: "float | None" = None,
) -> None:
    """工具结果落事件 —— ★ 失败也进事件流（R-Q ③：不得静默吞掉）。

    ``ok`` **直接取自** :class:`~app.tools.ToolResult`，不在这里判断成功（R-Q ②）；
    契约要求 ``data`` 是**对象或 null** ⇒ ``value`` 不是 Mapping 时一律不进帧（失败必为 null）。
    """
    data = result.value if isinstance(result.value, Mapping) else None
    ctx.events.emit("tool.result", tool=tool, ok=result.ok, data=data, duration_ms=duration_ms)


def tool_update(*results: "tuple[str, ToolResult]") -> dict[str, dict[str, Any]]:
    """把工具结果收成状态更新 —— 字段语义属 W3，本轮只保证形状可断言。"""
    return {name: {"ok": result.ok, "reason": result.reason} for name, result in results}
