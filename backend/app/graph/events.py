"""SSE 帧信封与 14 类事件 payload —— W3 · S3（``docs/06 §14.3`` 卡 W3 的交付第 1 项）。

**唯一权威**：``contracts/events.schema.json``（C4 ``v1.2``）—— 事件词汇表、payload 形状、
帧约定（``x-frame-conventions``）都在那里。**本模块不新增任何形状**，它做的是：

1. :data:`EVENT_TYPES` / :data:`PAYLOAD_DEFINITION` / :data:`INTENTS` / :data:`ARTIFACT_KINDS` /
   :data:`DONE_STATUSES` —— **全部从契约文件读出来**（不是抄一份常量表：抄一份就等于
   `docs/03 §3.2` 第 2 条要挡的"同一套规则在两处各有一份"）；
2. 14 个 payload 构造器 + :data:`EVENT_BUILDERS` —— 一类一个，**显式写出**（这样"契约加了
   第 15 类事件却没人写构造器"会被用例当场抓住，而不是静默通过）；
3. :class:`Frame` / :class:`FrameSink` / :func:`encode_frame` —— 帧信封与线格式。

**三层"构造时即强制"**（与 ``app.core.errors`` 的 ``AppError`` 同一体例）：

① 构造器**只接关键字参数**，且只接受契约里有的字段 ⇒ 打错字段名当即 ``TypeError``；
② 可选字段**有值才放键**（``x-frame-conventions.optional_fields``：省略而不是置 ``null``；
   契约里显式含 ``null`` 的字段——如 ``context.resolved.context``——照常放 ``null``）；
③ :func:`_check_payload` 按**契约本体**复核一遍必填 / 越界键 / 枚举 / 字符串上下界 /
   数组最小条数 / 标量类型。

★ **第 ③ 层不是 jsonschema 的替代品**（``jsonschema`` 属 dev 依赖，**不进服务进程** ——
``requirements-dev.txt`` / ``docs/03 §8.4``）。它只覆盖本契约用到的那个子集，边界如实登记：
**嵌在 ``oneOf`` / ``$ref`` 里的深层字段不在这里判**（``artifact.payload`` 的逐支形状由
:func:`artifact` 单独判；``verification.report`` 的形状归 C12；``verification.schema.json``
自己的 ``x-invariants`` 由装配方与门禁判）。

**与 ``state.RecordingEventSink`` 是两个口径，别互相顶替**（计划 §4.2①）：
``RecordingEventSink`` 是 **R-O 的 ID-only** 落点（日志 / 审计口径，只记 ``*_id`` 与短代码）；
本模块的 :class:`FrameSink` 是**发给浏览器的内容**（``query`` · ``steps`` · 报告正文都在里面）。
帧承载内容、日志不承载内容 —— 这是 ``docs/02 §1.4`` 规则 1 的分工。

**不缓冲**：``FrameSink`` **不留帧历史** —— 断流的语义是**重新发起**（``docs/02 §4.2-1``），
服务端不做重放缓冲。要检查跑过哪些帧的调用方，自己接 :meth:`FrameSink.emit` 的返回值。

**未知 ``type``**：``x-frame-conventions.unknown_type`` 那条"静默忽略"是**消费者**纪律
（前端在 ``core/api.stream()``，属域1 / 域4）；**生产者**这边自造事件类目正是卡 W3 明令的
"不做什么"① ⇒ :meth:`FrameSink.emit` 遇到枚举外的 ``type`` **直接抛错**。

依赖方向（``docs/03 §3.1``）：``graph/`` 允许 import ``core/`` / ``domain/`` / ``tools/``，
**禁止 import ``api/``**。
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from app.core.errors import ERROR_CODES

# --------------------------------------------------------------------------- #
# 契约：事件词汇表与各 payload 的形状（**唯一权威**；本模块只读契约，不改契约）
# --------------------------------------------------------------------------- #

#: 事件契约的位置（``backend/app/graph/events.py`` 上溯三级 = 仓库根）。
#: ★ 路径在这里自己算，**不去动 `core/config.py`**（``core/**`` 属"后端与数据"域，
#:   ``docs/05 §3`` 的写作用域互斥）；`main.py` / `config.py` 各自算根目录，同一做法。
CONTRACT_PATH = Path(__file__).resolve().parents[3] / "contracts" / "events.schema.json"

try:
    _SCHEMA: Mapping[str, Any] = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
except (OSError, json.JSONDecodeError) as exc:  # pragma: no cover - 仓里缺契约即不可启动
    raise RuntimeError(
        f"事件契约读不出来：{CONTRACT_PATH}（{exc}）—— 事件词汇表的唯一权威就是它，缺它不得启动"
    ) from exc

_DEFINITIONS: Mapping[str, Any] = _SCHEMA["definitions"]

#: 恰好 14 类（``properties.type.enum``；``x-type-count.value`` 由门禁对账）。
EVENT_TYPES: "tuple[str, ...]" = tuple(_SCHEMA["properties"]["type"]["enum"])

#: ``type`` → 它的 payload 定义名（从 ``oneOf`` 的 ``$ref`` 派生，**不手写**）。
PAYLOAD_DEFINITION: "dict[str, str]" = {
    branch["properties"]["type"]["const"]: branch["properties"]["payload"]["$ref"].rsplit("/", 1)[-1]
    for branch in _SCHEMA["oneOf"]
}

#: ``plan.intent`` 的四值（``docs/02 §4.2`` 的路由枚举）。★ 只有 ``solve`` 受
#: "步骤 ≥ 3"约束（F2），其余三个意图**不受**。
INTENTS: "tuple[str, ...]" = tuple(_DEFINITIONS["planPayload"]["properties"]["intent"]["enum"])

#: ``artifact.kind`` 的六个取值（v1.1 加入 ``decomposition``）。
ARTIFACT_KINDS: "tuple[str, ...]" = tuple(
    _DEFINITIONS["artifactPayload"]["properties"]["kind"]["enum"]
)

#: ``done.status`` 的两值。
DONE_STATUSES: "tuple[str, ...]" = tuple(_DEFINITIONS["donePayload"]["properties"]["status"]["enum"])

#: ★ **两个合法终帧**（``docs/02 §6.5-2``：终帧必须显式，前端才能区分"跑完了"与"网断了"）。
#: 契约把这句话写在两处 ``description`` 里、**不是机器可读的** ⇒ 这里显式列出，
#: 并由用例断言"这两个名字确实在枚举内"。
TERMINAL_EVENT_TYPES: "tuple[str, ...]" = ("done", "error")


class UnknownEventType(ValueError):
    """``type`` 不在契约枚举内 —— 生产者不得自造事件类目（卡 W3「不做什么」①）。"""


class EventPayloadRejected(ValueError):
    """payload 与契约不符（缺必填 / 越界键 / 枚举外 / 超出上下界 / 类型不对）。"""


class FrameSequenceViolation(RuntimeError):
    """帧序列不合法：终帧之后再发，或收口时缺终帧（``x-frame-conventions.terminal``）。"""


# --------------------------------------------------------------------------- #
# payload 构造器（14 个，一类一个）
# --------------------------------------------------------------------------- #
def run_start(*, run_id: str, session_id: str, query: str) -> "dict[str, Any]":
    """``run.start`` —— 一次运行的开始（带提问原文的事件之一）。"""
    return {"run_id": run_id, "session_id": session_id, "query": query}


def context_resolved(
    *, context: "Mapping[str, Any] | None", reason: "str | None"
) -> "dict[str, Any]":
    """``context.resolved`` —— F1 的落点：没给上下文时**必须显式降级**（``context: null`` + ``reason``）。

    ★ 这两个键**永远都在**（它们是必填，且类型里显式含 ``null``）—— 省略会让"没给"与
    "后端忘了发"无法区分（契约原文）。
    """
    return {"context": dict(context) if context is not None else None, "reason": reason}


def plan(
    *, intent: str, steps: "list[Mapping[str, Any]]", reason: "str | None" = None
) -> "dict[str, Any]":
    """``plan`` —— 计划执行的节点序列（``steps[]`` 每项是 ``{node, label}``）。"""
    body: "dict[str, Any]" = {"intent": intent, "steps": [dict(step) for step in steps]}
    if reason is not None:
        body["reason"] = reason
    return body


def node_start(*, node: str, label: "str | None" = None) -> "dict[str, Any]":
    """``node.start`` —— 与 :func:`node_end` 成对。"""
    body: "dict[str, Any]" = {"node": node}
    if label is not None:
        body["label"] = label
    return body


def node_delta(*, node: str, text: str) -> "dict[str, Any]":
    """``node.delta`` —— 节点产出的**片段**（不是整段回答：那会同时坏掉首字延迟与断流代价）。"""
    return {"node": node, "text": text}


def tool_call(
    *, tool: str, node: "str | None" = None, args: "Mapping[str, Any] | None" = None
) -> "dict[str, Any]":
    """``tool.call`` —— ★ ``args`` **不得放凭据**（R-M 第 ⑦ 面）；``tool`` 在注册表允许集合内。"""
    body: "dict[str, Any]" = {"tool": tool}
    if node is not None:
        body["node"] = node
    if args is not None:
        body["args"] = dict(args)
    return body


def tool_result(
    *,
    tool: str,
    ok: bool,
    data: "Mapping[str, Any] | None" = None,
    duration_ms: "float | None" = None,
) -> "dict[str, Any]":
    """``tool.result`` —— ★ **R-Q ② 的落点**：``ok`` 必须来自**真实执行结果**，禁止硬编码。

    失败也必须发这一帧（R-Q ③：失败不得静默吞掉），契约规定失败时 ``data`` 为 ``null``。
    """
    body: "dict[str, Any]" = {"tool": tool, "ok": ok}
    if data is not None:
        body["data"] = dict(data)
    if duration_ms is not None:
        body["duration_ms"] = duration_ms
    return body


def artifact(*, kind: str, payload: "Mapping[str, Any]") -> "dict[str, Any]":
    """``artifact`` —— 结构化产出。★ 契约把 ``kind`` 与 ``payload`` **逐支绑定**（旧仓没绑）。

    ``kind="solution"`` 那一支是 **F2 结构断言的第一处落点**：``steps`` 至少 3 条，
    每条的 ``explanation`` / ``formula`` 都非空（形状 = ``#/definitions/solutionStep``）。
    ★ ``kind="decomposition"`` 的形状在 ``hint.schema.json``（F17，属 W4）⇒ 这里只要求它是对象。
    """
    body: "dict[str, Any]" = {"kind": kind, "payload": dict(payload)}
    _check_artifact(kind, body["payload"])
    return body


def node_end(
    *,
    node: str,
    ok: bool,
    duration_ms: "float | None" = None,
    summary: "str | None" = None,
) -> "dict[str, Any]":
    """``node.end`` —— 与 :func:`node_start` 成对。"""
    body: "dict[str, Any]" = {"node": node, "ok": ok}
    if duration_ms is not None:
        body["duration_ms"] = duration_ms
    if summary is not None:
        body["summary"] = summary
    return body


def verification_report(
    *, report: "Mapping[str, Any]", summary: "str | None" = None
) -> "dict[str, Any]":
    """``verification.report`` —— **每答必发**（F3「无标签不出结果」）。

    ``ok`` 由 ``report["passed"]`` **派生**（契约要求它与 ``report.passed`` 一致；冗余一个顶层
    布尔是为了让前端不必解包就能分流）⇒ 这里**不接受**调用方另给一个 ``ok``，避免两处打架。
    """
    if not isinstance(report, Mapping) or "passed" not in report:
        raise EventPayloadRejected(
            "verification.report 的 report 必须带 passed —— ok 由它派生（契约要求两者一致）"
        )
    body: "dict[str, Any]" = {"report": dict(report), "ok": bool(report["passed"])}
    if summary is not None:
        body["summary"] = summary
    return body


def hitl_request(
    *,
    hitl_id: str,
    options: "list[str]",
    node: "str | None" = None,
    draft: "str | None" = None,
) -> "dict[str, Any]":
    """``hitl.request`` —— 图在 ``interrupt()`` 处挂起；与 :func:`hitl_resolved` **必须成对**。

    ``options`` 至少 1 项：前端据此渲染按钮，**不得硬编码成两个写死的选项**（契约原文）。
    """
    body: "dict[str, Any]" = {"hitl_id": hitl_id, "options": [str(option) for option in options]}
    if node is not None:
        body["node"] = node
    if draft is not None:
        body["draft"] = draft
    return body


def hitl_resolved(*, action: str, final: "str | None" = None) -> "dict[str, Any]":
    """``hitl.resolved`` —— ``action`` 取自对应 ``hitl.request`` 的 ``options``。"""
    body: "dict[str, Any]" = {"action": action}
    if final is not None:
        body["final"] = final
    return body


def done(*, status: str, final_answer: "str | None" = None) -> "dict[str, Any]":
    """``done`` —— **终帧之一**。``status="uncertain"`` 时同一次运行里必有 ``uncertain`` 的报告。"""
    body: "dict[str, Any]" = {"status": status}
    if final_answer is not None:
        body["final_answer"] = final_answer
    return body


def error(*, code: str, message: str, node: "str | None" = None) -> "dict[str, Any]":
    """``error`` —— **终帧之一**。``code`` 取自 ``app.core.errors`` 的枚举（**不自造**）。

    ★ ``app.core.errors`` 与 ``error.schema.json`` 的 ``errorCode`` 已同源（门禁对账），
    因此读 core 的枚举就等于读契约那个枚举 —— 且 SSE 与 HTTP 错误体共用同一个词汇表。
    """
    if code not in ERROR_CODES:
        raise EventPayloadRejected(
            f"error.code {code!r} 不在 error.schema.json 的枚举内 —— 不得自造"
        )
    body: "dict[str, Any]" = {"code": code, "message": message}
    if node is not None:
        body["node"] = node
    return body


#: ``type`` → 构造器。★ **显式写出**（不按枚举生成）：契约"只增不改"，加了第 15 类事件
#: 而没人写构造器时，``set(EVENT_BUILDERS) == set(EVENT_TYPES)`` 会被用例当场抓住。
EVENT_BUILDERS: "dict[str, Callable[..., dict[str, Any]]]" = {
    "run.start": run_start,
    "context.resolved": context_resolved,
    "plan": plan,
    "node.start": node_start,
    "node.delta": node_delta,
    "tool.call": tool_call,
    "tool.result": tool_result,
    "artifact": artifact,
    "node.end": node_end,
    "verification.report": verification_report,
    "hitl.request": hitl_request,
    "hitl.resolved": hitl_resolved,
    "done": done,
    "error": error,
}


# --------------------------------------------------------------------------- #
# 帧信封与线格式
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Frame:
    """一帧：``seq``（只用于去重）· ``type`` · ``ts``（epoch 秒）· ``payload``。"""

    seq: int
    type: str
    ts: float
    payload: "dict[str, Any]"

    def to_dict(self) -> "dict[str, Any]":
        return {"seq": self.seq, "type": self.type, "ts": self.ts, "payload": dict(self.payload)}


class FrameSink:
    """发帧的唯一口子：造 payload → 复核 → 装信封 → 计数。

    ★ ``emit(type, **kwargs)`` 里的 ``**kwargs`` 是**该类的构造器参数**（例如
    ``emit("done", status="ok", final_answer="…")``），**不是**一个现成的 payload 字典 ——
    这样"payload 由构造器造"这件事在类型上无法绕过（否则调用方可以直接塞一个手写字典）。

    ``clock`` 可注入（缺省 ``time.time``）—— 用例要能对 ``ts`` 与 ``seq`` 做确定性断言。
    ★ **不留帧历史**（不做重放缓冲，``docs/02 §4.2-1``）：要检查的调用方接返回值。
    """

    def __init__(self, *, clock: "Callable[[], float]" = time.time) -> None:
        self._clock = clock
        self._seq = 0
        self._terminal_seen = False

    @property
    def seq(self) -> int:
        """已发出的帧数（= 最后一帧的 ``seq``；从 1 起，契约 ``minimum: 1``）。"""
        return self._seq

    @property
    def terminated(self) -> bool:
        """是否已经发过终帧（``done`` / ``error``）。"""
        return self._terminal_seen

    def emit(self, event_type: str, /, **payload: Any) -> Frame:
        """发一帧；``event_type`` 必须是契约枚举内的一类，``**payload`` 交给它的构造器。"""
        if event_type not in EVENT_TYPES:
            raise UnknownEventType(
                f"{event_type!r} 不在事件契约的 {len(EVENT_TYPES)} 类里 —— 生产者不得自造事件类目"
                "（消费侧的「未知 type 静默忽略」是前端纪律，不是生产者可以随便发的许可）"
            )
        if self._terminal_seen:
            raise FrameSequenceViolation(
                f"已经发过终帧，又来了 {event_type!r} —— 终帧必须是最后一帧（docs/02 §6.5-2）"
            )
        body = EVENT_BUILDERS[event_type](**payload)
        _check_payload(event_type, body)
        self._seq += 1
        frame = Frame(seq=self._seq, type=event_type, ts=float(self._clock()), payload=body)
        if event_type in TERMINAL_EVENT_TYPES:
            self._terminal_seen = True
        return frame

    def close(self, *, cancelled: bool = False) -> None:
        """收口：**没发终帧就是实现缺陷**（``x-frame-conventions.terminal``）。

        ★ ``cancelled=True`` 是**唯一例外**（``_plan/提案-01-events-runSnapshot-cancelled.md``）：
        客户端断开时**没有接收方**，发终帧没有意义 ⇒ 取消路径不该被这条断言挡住。
        该提案待 R1 审批，因此这个例外是**内部状态**、不外发（核对表 §四 第 2 条）。
        """
        if self._terminal_seen or cancelled:
            return
        raise FrameSequenceViolation(
            "这次运行没有发过终帧（done / error）—— 前端只能靠超时猜「跑完了没有」"
            "（x-frame-conventions.terminal · docs/02 §6.5-2）"
        )


def encode_frame(frame: Frame) -> str:
    """线格式：``data: <json>`` + ``\\n\\n``；**中文不转义**（``ensure_ascii=False``）。

    ★ 返回 ``str``（计划 §4.2① 的签名）；**UTF-8 编码由写出方完成**（域2 的 ``api/`` 侧）。
    ★ 线格式只有这一处实现 —— ``x-frame-conventions.transport`` 是它的唯一依据。
    """
    return "data: " + json.dumps(frame.to_dict(), ensure_ascii=False) + "\n\n"


# --------------------------------------------------------------------------- #
# 复核（第 ③ 层：按契约本体查一遍）
# --------------------------------------------------------------------------- #
_TYPE_CHECKS: "dict[str, tuple[type, ...]]" = {
    "string": (str,),
    "boolean": (bool,),
    "integer": (int,),
    "number": (int, float),
    "array": (list, tuple),
    "object": (dict, Mapping),
}


def _check_payload(event_type: str, payload: "Mapping[str, Any]") -> None:
    """按契约复核一条 payload（必填 / 越界键 / 类型 / 枚举 / 上下界 / 数组最小条数）。"""
    spec = _DEFINITIONS[PAYLOAD_DEFINITION[event_type]]
    properties = spec["properties"]
    for key in spec.get("required", ()):
        if key not in payload:
            raise EventPayloadRejected(f"{event_type} 缺必填字段 {key!r}（契约 required）")
    for key, value in payload.items():
        if key not in properties:
            raise EventPayloadRejected(
                f"{event_type} 出现契约之外的字段 {key!r}（additionalProperties: false）"
            )
        _check_property(event_type, key, value, properties[key])


def _check_property(event_type: str, key: str, value: Any, spec: Mapping[str, Any]) -> None:
    where = f"{event_type}.{key}"
    expected = spec.get("type")
    if isinstance(expected, list):
        if value is None and "null" in expected:
            return
        expected = next((item for item in expected if item != "null"), None)
    if expected in _TYPE_CHECKS:
        # ★ `bool` 是 `int` 的子类 ⇒ `number` / `integer` 必须把它排除，否则 True 冒充 1。
        if expected in ("integer", "number") and isinstance(value, bool):
            raise EventPayloadRejected(f"{where} 必须是 {expected}，收到 bool")
        if not isinstance(value, _TYPE_CHECKS[expected]):
            raise EventPayloadRejected(
                f"{where} 必须是 {expected}，收到 {type(value).__name__}"
            )
    if expected == "string":
        _check_string_bounds(where, value, spec)
    enum = spec.get("enum")
    if enum is not None and value not in enum:
        raise EventPayloadRejected(f"{where} 取值 {value!r} 不在契约枚举 {list(enum)} 内")
    if expected == "number" and "minimum" in spec and value < spec["minimum"]:
        raise EventPayloadRejected(f"{where} 不得小于 {spec['minimum']}")
    if expected == "array":
        minimum_items = spec.get("minItems")
        if minimum_items is not None and len(value) < minimum_items:
            raise EventPayloadRejected(f"{where} 至少 {minimum_items} 项（契约 minItems）")
        _check_items(where, value, spec.get("items", {}))


def _check_string_bounds(where: str, value: str, spec: Mapping[str, Any]) -> None:
    minimum = spec.get("minLength", 0)
    maximum = spec.get("maxLength")
    if len(value) < minimum:
        raise EventPayloadRejected(f"{where} 不得短于 {minimum} 字（契约 minLength）")
    if maximum is not None and len(value) > maximum:
        raise EventPayloadRejected(f"{where} 不得超过 {maximum} 字（契约 maxLength）")


def _check_items(where: str, values: "list[Any] | tuple[Any, ...]", items: Mapping[str, Any]) -> None:
    """数组元素只判**顶层类型**；元素内部的形状由构造器各自负责（见模块头登记的边界）。"""
    expected = items.get("type")
    if not isinstance(expected, str) or expected not in _TYPE_CHECKS:
        return
    allowed = _TYPE_CHECKS[expected]
    for index, item in enumerate(values):
        if expected in ("integer", "number") and isinstance(item, bool):
            raise EventPayloadRejected(f"{where}[{index}] 必须是 {expected}，收到 bool")
        if not isinstance(item, allowed):
            raise EventPayloadRejected(
                f"{where}[{index}] 必须是 {expected}，收到 {type(item).__name__}"
            )


# --------------------------------------------------------------------------- #
# artifact 的逐支绑定（契约用 title 标分支 ⇒ 按 title 前缀取，不手抄形状）
# --------------------------------------------------------------------------- #
def _artifact_branch(kind: str) -> Mapping[str, Any]:
    for branch in _DEFINITIONS["artifactPayload"]["properties"]["payload"]["oneOf"]:
        if str(branch.get("title", "")).startswith(kind):
            return branch
    return {}


def _check_artifact(kind: str, payload: "Mapping[str, Any]") -> None:
    if kind not in ARTIFACT_KINDS:
        raise EventPayloadRejected(
            f"artifact.kind {kind!r} 不在契约枚举 {list(ARTIFACT_KINDS)} 内（新增 kind 属兼容扩展）"
        )
    branch = _artifact_branch(kind)
    if not branch or "$ref" in branch:
        # `decomposition` 的形状在 `hint.schema.json`（F17 · W4）⇒ 这里只要求它是对象。
        return
    properties = branch["properties"]
    for key in branch.get("required", ()):
        if key not in payload:
            raise EventPayloadRejected(f"artifact(kind={kind}) 缺必填字段 {key!r}")
    for key, value in payload.items():
        if key not in properties:
            raise EventPayloadRejected(f"artifact(kind={kind}) 出现契约之外的字段 {key!r}")
        _check_property(f"artifact(kind={kind})", key, value, properties[key])
    if kind == "solution":
        _check_solution_steps(payload["steps"])


def _check_solution_steps(steps: "list[Any] | tuple[Any, ...]") -> None:
    """★ **F2 的第一处落点**：``steps`` ≥ 3（契约 ``minItems``），每步两个字段都非空。"""
    required = tuple(_DEFINITIONS["solutionStep"]["required"])
    for index, step in enumerate(steps):
        if not isinstance(step, Mapping):
            raise EventPayloadRejected(f"artifact(solution).steps[{index}] 必须是对象")
        for key in required:
            value = step.get(key)
            if not isinstance(value, str) or not value:
                raise EventPayloadRejected(
                    f"artifact(solution).steps[{index}].{key} 必须是非空字符串"
                    "（F2：说明与式子都非空）"
                )
