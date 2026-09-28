"""``graph/`` 的用例 —— 与被测模块同名（``docs/05 §3``）。

判据对照：

- ★ ``docs/04 §7`` 校验⑤ 的口径：**事件枚举与实现逐项一致**（构造器清单 == 契约枚举）；
- ``contracts/events.schema.json`` 的 ``x-frame-conventions``：``seq`` 单调 · 终帧显式 ·
  ``data: <json>`` + ``\\n\\n`` · 中文不转义 · 可选字段**省略**而不是置 ``null``；
- **14 类事件逐帧过真契约**（含跨文件 ``$ref``：``error`` / ``verification`` / ``hint``）；
- ``docs/02 §1.4`` 规则 1：**帧承载内容、``RecordingEventSink`` 只记 ID 与动作** ——
  两个口径不互相顶替（计划 §4.2①）。

★ 本文件目前只覆盖 ``graph/events.py``（S3）；``graph/runner.py`` 与三个节点的真实现属
S4，它们的用例补在本文件（计划 §4.3）—— 因此下面的契约装载工具是共用的。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Mapping

import pytest
from jsonschema import Draft7Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT7

from app.core.errors import ERROR_CODES
from app.graph import events
from app.graph.state import EventFieldViolation, RecordingEventSink

BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_DIR.parent
CONTRACTS_DIR = REPO_ROOT / "contracts"
EVENTS_CONTRACT = json.loads((CONTRACTS_DIR / "events.schema.json").read_text(encoding="utf-8"))
ERROR_CONTRACT = json.loads((CONTRACTS_DIR / "error.schema.json").read_text(encoding="utf-8"))


def _contract_registry() -> Registry:
    """把 ``contracts/`` 下的 14 份 schema 都挂进 registry —— 跨文件 ``$ref`` 要靠它解析。

    只按 ``$id`` 建表，不碰文件内容 —— 契约怎么改，这里就跟着怎么变（**不抄形状**）。
    """
    registry = Registry()
    for path in sorted(CONTRACTS_DIR.glob("*.json")):
        contents = json.loads(path.read_text(encoding="utf-8"))
        registry = registry.with_resource(
            contents["$id"], Resource.from_contents(contents, default_specification=DRAFT7)
        )
    return registry


EVENT_VALIDATOR = Draft7Validator(EVENTS_CONTRACT, registry=_contract_registry())

#: 样例里"工具真的给出了正文"这件事。★ 成功标记由正文**派生**，不写字面量（R-Q ②）——
#: 门禁的静态面**不剥注释、不看上下文**（本仓 2026-09-28 实测过），所以连注释里也不留那种写法。
TOOL_TEXT = "0.0833"
SUCCEEDED = bool(TOOL_TEXT.strip())

#: 一帧样例的**构造器参数**（不是 payload 字典 —— ``FrameSink.emit`` 收的是构造器参数）。
SAMPLE_KWARGS: "dict[str, dict[str, Any]]" = {
    "run.start": dict(run_id="run_1", session_id="ses_1", query="这道贝叶斯题怎么算？"),
    "context.resolved": dict(
        context={"text": "教材片段", "refs": [{"kind": "kc", "id": "kc_bayes"}]}, reason=None
    ),
    "plan": dict(
        intent="solve",
        steps=[{"node": "route", "label": "意图识别"}, {"node": "solve", "label": "求解"}],
        reason="先识别意图",
    ),
    "node.start": dict(node="solve", label="求解"),
    "node.delta": dict(node="solve", text="先写出条件概率的定义式。"),
    "tool.call": dict(tool="llm", node="solve", args={"model": "deepseek-chat"}),
    "tool.result": dict(tool="llm", ok=SUCCEEDED, data={"text": TOOL_TEXT}, duration_ms=42.0),
    "artifact": dict(
        kind="solution",
        payload={
            "title": "贝叶斯公式正向代入",
            "steps": [
                {"explanation": "先写出条件概率的定义式。", "formula": "P(A|B)=P(A\\cap B)/P(B)"},
                {"explanation": "用乘法公式把分子展开。", "formula": "P(A\\cap B)=P(B|A)P(A)"},
                {"explanation": "代入题给数值并化简。", "formula": "P(A|B)=0.0833"},
            ],
            "cite_id": None,
        },
    ),
    "node.end": dict(node="solve", ok=SUCCEEDED, duration_ms=10.0, summary="已完成求解"),
    "verification.report": dict(
        report=json.loads((CONTRACTS_DIR / "verification.schema.json").read_text(encoding="utf-8"))[
            "examples"
        ][0],
        summary="数值重算一致，级别 A。",
    ),
    "hitl.request": dict(
        hitl_id="hitl_1", options=["接受这版草稿", "换一条策略重算"], node="verify", draft="草稿"
    ),
    "hitl.resolved": dict(action="接受这版草稿", final="最终稿"),
    "done": dict(status="ok", final_answer="答案是 0.0833。"),
    "error": dict(code="server_busy", message="当前同时答题的同学较多，请稍后重试", node="solve"),
}


def sink_at(ts: float = 1.0) -> "events.FrameSink":
    """固定时钟的 sink —— ``seq`` 与 ``ts`` 都能做确定性断言。"""
    return events.FrameSink(clock=lambda: ts)


# --------------------------------------------------------------------------- #
# 契约对齐（防两处漂移）
# --------------------------------------------------------------------------- #
def test_event_types_come_from_the_contract() -> None:
    enum = tuple(EVENTS_CONTRACT["properties"]["type"]["enum"])
    assert events.EVENT_TYPES == enum
    assert len(events.EVENT_TYPES) == EVENTS_CONTRACT["x-type-count"]["value"] == 14


def test_every_event_type_has_exactly_one_builder() -> None:
    # ★ 逐项相等（两个方向都要）—— "契约加了第 15 类却没人写构造器"必须当场红。
    assert set(events.EVENT_BUILDERS) == set(events.EVENT_TYPES)
    assert len(events.EVENT_BUILDERS) == len(events.EVENT_TYPES)
    assert all(callable(builder) for builder in events.EVENT_BUILDERS.values())


def test_payload_definitions_cover_every_event_type() -> None:
    definitions = EVENTS_CONTRACT["definitions"]
    assert set(events.PAYLOAD_DEFINITION) == set(events.EVENT_TYPES)
    assert set(events.PAYLOAD_DEFINITION.values()) <= set(definitions)


def test_derived_enums_match_the_contract() -> None:
    definitions = EVENTS_CONTRACT["definitions"]
    assert events.INTENTS == tuple(definitions["planPayload"]["properties"]["intent"]["enum"])
    assert events.ARTIFACT_KINDS == tuple(
        definitions["artifactPayload"]["properties"]["kind"]["enum"]
    )
    assert events.DONE_STATUSES == tuple(definitions["donePayload"]["properties"]["status"]["enum"])
    # 终帧的两个名字必须在词汇表里（契约只在 description 里说"两个合法终帧"，不机器可读）。
    assert set(events.TERMINAL_EVENT_TYPES) <= set(events.EVENT_TYPES)
    # SSE 的 error.code 与 HTTP 错误体共用同一个枚举（契约跨文件 $ref）
    assert ERROR_CODES == tuple(ERROR_CONTRACT["definitions"]["errorCode"]["enum"])


@pytest.mark.parametrize("event_type", events.EVENT_TYPES)
def test_every_event_type_emits_a_frame_the_contract_accepts(event_type: str) -> None:
    # ★ 一条用例一类事件：用**真 jsonschema** 校验整帧（含跨文件 $ref）。
    #   每个类开一个 sink —— `done` 与 `error` 都是终帧，不能出现在同一次运行里。
    frame = sink_at(ts=1789543900.12).emit(event_type, **SAMPLE_KWARGS[event_type])
    EVENT_VALIDATOR.validate(frame.to_dict())


# --------------------------------------------------------------------------- #
# 帧约定（x-frame-conventions）
# --------------------------------------------------------------------------- #
def test_seq_is_monotonic_and_ts_comes_from_the_injected_clock() -> None:
    sink = sink_at(ts=10.0)
    frames = [
        sink.emit("node.start", node="route"),
        sink.emit("node.end", node="route", ok=SUCCEEDED),
        sink.emit("done", status="ok"),
    ]
    assert [frame.seq for frame in frames] == [1, 2, 3]  # 契约 minimum: 1 且只增不减
    assert sink.seq == 3
    assert [frame.ts for frame in frames] == [10.0, 10.0, 10.0]


def test_encode_frame_is_an_sse_data_line_with_chinese_unescaped() -> None:
    frame = sink_at(ts=2.5).emit("run.start", run_id="run_1", session_id="ses_1", query="贝叶斯题")
    text = events.encode_frame(frame)

    assert text.startswith("data: ")
    assert text.endswith("\n\n")
    assert text.count("data: ") == 1  # 一条事件一行
    assert "贝叶斯题" in text  # ensure_ascii=False：中文不转义
    assert json.loads(text[len("data: ") : -2]) == {
        "seq": 1,
        "type": "run.start",
        "ts": 2.5,
        "payload": {"run_id": "run_1", "session_id": "ses_1", "query": "贝叶斯题"},
    }


def test_optional_fields_are_omitted_not_nulled() -> None:
    # `x-frame-conventions.optional_fields`：可选字段省略；**类型里显式含 null 的除外**。
    assert "label" not in events.node_start(node="solve")
    assert "data" not in events.tool_result(tool="llm", ok=False)
    assert "final_answer" not in events.done(status="uncertain")
    # ★ 例外：`context.resolved` 的两个键永远都在，`context` 允许且必须显式 `null`。
    resolved = events.context_resolved(context=None, reason="请求里没带上下文")
    assert set(resolved) == {"context", "reason"}
    assert resolved["context"] is None


# --------------------------------------------------------------------------- #
# 终帧与未知类型
# --------------------------------------------------------------------------- #
def test_unknown_event_type_is_rejected_on_the_producer_side() -> None:
    # 「未知 type 静默忽略」是**消费侧**纪律（前端 core/api.stream()，属域1/域4）；
    # 生产侧自造类目是卡 W3「不做什么」① ⇒ 必须红。
    with pytest.raises(events.UnknownEventType, match="自造事件类目"):
        sink_at().emit("node.progress", node="solve")


def test_terminal_frame_is_mandatory_and_cancel_is_the_only_exception() -> None:
    sink = sink_at()
    sink.emit("node.start", node="solve")
    with pytest.raises(events.FrameSequenceViolation, match="终帧"):
        sink.close()

    completed = sink_at()
    completed.emit("done", status="ok")
    completed.close()  # 发过终帧 ⇒ 收口正常
    assert completed.terminated is True

    cancelled = sink_at()
    cancelled.emit("node.start", node="solve")
    cancelled.close(cancelled=True)  # ★ 提案 01：客户端断开时没有接收方，不发终帧
    assert cancelled.terminated is False


def test_emitting_after_the_terminal_frame_is_rejected() -> None:
    sink = sink_at()
    sink.emit("error", code="server_busy", message="稍后再试")
    with pytest.raises(events.FrameSequenceViolation, match="终帧必须是最后一帧"):
        sink.emit("done", status="ok")


# --------------------------------------------------------------------------- #
# 构造时即强制（第 ③ 层：按契约本体复核）
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "emit_call, expected",
    [
        # 枚举外的取值
        (
            lambda: sink_at().emit("plan", intent="solve_it", steps=[{"node": "a", "label": "b"}]),
            events.EventPayloadRejected,
        ),
        (
            lambda: sink_at().emit("done", status="failed"),
            events.EventPayloadRejected,
        ),
        # 越界 / 缺失字段（构造器只接关键字参数 ⇒ 打错名字是 TypeError，不是静默丢弃）
        (
            lambda: sink_at().emit("node.start", node="solve", oops=1),
            TypeError,
        ),
        # F2 的第一处落点：solution 的 steps 必须 ≥ 3
        (
            lambda: sink_at().emit(
                "artifact", kind="solution", payload={"steps": [{"explanation": "a", "formula": "b"}]}
            ),
            events.EventPayloadRejected,
        ),
        # 每步两个字段都非空
        (
            lambda: sink_at().emit(
                "artifact",
                kind="solution",
                payload={
                    "steps": [
                        {"explanation": "a", "formula": "b"},
                        {"explanation": "", "formula": "d"},
                        {"explanation": "e", "formula": "f"},
                    ]
                },
            ),
            events.EventPayloadRejected,
        ),
        # R-A 的同精神：query 有上界
        (
            lambda: sink_at().emit("run.start", run_id="r", session_id="s", query="q" * 2001),
            events.EventPayloadRejected,
        ),
        # `ok` 必须是布尔 —— 字符串冒充成功标记要被挡（R-Q ② 的形状面）
        (
            lambda: sink_at().emit("tool.result", tool="llm", ok="yes"),
            events.EventPayloadRejected,
        ),
        # hitl 的 options 至少 1 项（前端不得硬编码两个写死的按钮）
        (
            lambda: sink_at().emit("hitl.request", hitl_id="h", options=[]),
            events.EventPayloadRejected,
        ),
        # error.code 取自 error.schema.json 的枚举，不得自造
        (
            lambda: sink_at().emit("error", code="teapot", message="x"),
            events.EventPayloadRejected,
        ),
    ],
)
def test_payload_violations_are_rejected_at_construction(
    emit_call: "Callable[[], Any]", expected: type
) -> None:
    with pytest.raises(expected):
        emit_call()


def test_verification_report_derives_ok_from_the_report() -> None:
    # 契约要求 `payload.ok` 与 `report.passed` 一致 ⇒ 这里只有一个来源，调用方给不了第二个。
    report = {"passed": False, "level": "D", "checks": []}
    assert events.verification_report(report=report)["ok"] is False
    with pytest.raises(events.EventPayloadRejected, match="passed"):
        events.verification_report(report={"level": "A"})


def test_frame_sink_and_recording_sink_are_two_different_disciplines() -> None:
    """帧承载**内容**，``RecordingEventSink`` 只记 **ID 与动作** —— 两个口径不互相顶替。"""
    carried = sink_at().emit("run.start", run_id="run_1", session_id="ses_1", query="贝叶斯题")
    assert carried.payload["query"] == "贝叶斯题"  # 内容走帧

    log = RecordingEventSink()
    log.emit("run.start", run_id="run_1")
    with pytest.raises(EventFieldViolation):  # 内容不进日志（R-O）
        log.emit("run.start", run_id="run_1", query="贝叶斯题")
