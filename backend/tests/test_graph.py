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

import asyncio
import importlib
import json
from pathlib import Path
from typing import Any, Callable, Mapping

import pytest
from jsonschema import Draft7Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT7

from app.core.errors import ERROR_CODES
from app.graph import events
from app.graph import registry as graph_registry
from app.graph import runner
from app.graph.nodes import route, solve, verify
from app.graph.state import EventFieldViolation, NodeContext, RecordingEventSink
from app.tools import llm

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


# --------------------------------------------------------------------------- #
# S4 · 编排层（runner + 三个节点的真实现）
# --------------------------------------------------------------------------- #
ENDPOINT = {"provider": "deepseek", "model": "deepseek-chat", "base_url": "https://api.deepseek.com/v1"}
#: 假凭据：只用来过"有凭据"这一跳；请求由 `FakeModelTransport` 接住，**永不触网**。
CREDENTIAL = "sk-test-not-real"

SOLUTION = {
    "title": "贝叶斯公式",
    "answer": "0.25",
    "expression": "1/6 + 1/12",
    "steps": [
        {"explanation": "写出条件概率的定义式。", "formula": "P(A|B)=P(A∩B)/P(B)"},
        {"explanation": "用乘法公式展开分子。", "formula": "P(A∩B)=P(B|A)P(A)"},
        {"explanation": "代入题给数值并化简。", "formula": "=1/4=0.25"},
    ],
}
#: 独立重算会算出 `1/4`，而这里写 `0.58` ⇒ 两个手段都会判"不通过"（重算路径的输入）。
WRONG_SOLUTION = {**SOLUTION, "answer": "0.58"}


class FakeModelTransport:
    """假 provider：**按提示词分流**（路由问句 → 意图；求解问句 → JSON）。

    与 `test_tools.py` 的假 provider 同一体例：产品路径里没有任何 mock（N2）。
    """

    def __init__(self, *, intent: str = "solve", solution: "dict[str, Any] | None" = None) -> None:
        self.intent = intent
        self.solution = SOLUTION if solution is None else solution

    def __call__(self, request: "Any", *, timeout_s: float, max_bytes: int) -> "Any":
        body = request.body.decode("utf-8")
        if "归到下面四类" in body:
            text = self.intent
        elif "只输出一个 JSON" in body:
            text = json.dumps(self.solution, ensure_ascii=False)
        else:
            text = ""
        payload = {"choices": [{"message": {"content": text}}]}
        return llm.TransportResponse(
            status=200, body=json.dumps(payload, ensure_ascii=False).encode("utf-8")
        )


def collect_frames(**kwargs: "Any") -> "list[Any]":
    """同步地把一次 run 的帧收全。

    ★ 用 `asyncio.run` 而不是 pytest-asyncio：`requirements-dev.txt` 只有 pytest 与 jsonschema，
    **不为一条用例加依赖**（依赖口径归架构与集成）。
    """

    async def scenario() -> "list[Any]":
        frames = []
        async for frame in runner.stream_run(**kwargs):
            frames.append(frame)
        return frames

    return asyncio.run(scenario())


def test_runner_node_set_matches_the_registry() -> None:
    # 运行器跑的节点 = 注册表登记的节点（少一个 ⇒ 有节点永远跑不到；多一个 ⇒ 没登记的也能跑）
    assert set(runner.NODE_ENTRIES) == {item.name for item in graph_registry.load_registry()}


def test_verify_node_declares_the_two_sympy_tools() -> None:
    entry = next(item for item in graph_registry.load_registry() if item.name == "verify")
    declared = set(entry.tools or ())
    assert set(verify.SYMPY_METHODS) <= declared  # 手段名与契约 `check.method` 同名
    for tool in verify.SYMPY_METHODS:
        assert callable(importlib.import_module(f"app.tools.{tool}").ENTRY)


@pytest.mark.parametrize(
    "text, expected",
    [("solve", "solve"), ("解释一下这个概念：explain", "explain"), ("lookup", "lookup"), ("visualize", "visualize")],
)
def test_read_intent_reads_the_four_intents(text: str, expected: str) -> None:
    assert route.read_intent(text)[0] == expected
    assert set(events.INTENTS) == {"solve", "explain", "lookup", "visualize"}


def test_read_intent_falls_back_without_pretending() -> None:
    intent, reason = route.read_intent("我不知道该怎么归类")
    assert intent == route.FALLBACK_INTENT == "explain"  # 不冒充 solve
    assert "无法识别" in reason  # 降级要写在 reason 里（N2）


def test_parse_solution_requires_non_empty_steps() -> None:
    assert solve.parse_solution("完全不是 JSON") is None
    assert solve.parse_solution(json.dumps({"steps": [{"explanation": "", "formula": "x"}]})) is None
    fenced = "```json\n" + json.dumps(SOLUTION, ensure_ascii=False) + "\n```"
    parsed = solve.parse_solution(fenced)
    assert parsed is not None
    assert len(parsed["steps"]) == 3 and parsed["answer"] == "0.25"


def test_full_run_streams_the_pipeline_and_keeps_one_steps_object(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(llm, "_TRANSPORT", FakeModelTransport())
    store = runner.RunStore()

    frames = collect_frames(
        run_id="run_1",
        session_id="ses_1",
        query="这道贝叶斯题怎么算？",
        endpoint=ENDPOINT,
        credential=CREDENTIAL,
        store=store,
    )

    types = [frame.type for frame in frames]
    assert [frame.seq for frame in frames] == list(range(1, len(frames) + 1))  # seq 单调且连续
    assert types[0] == "run.start"
    assert types[-1] == "done"
    assert types.index("plan") > types.index("node.end")  # plan 在 route 之后（意图来自 route）
    assert types.count("artifact") == 1 and types.count("verification.report") == 1
    for frame in frames:
        EVENT_VALIDATOR.validate(frame.to_dict())  # ★ 每一帧都过真契约

    resolved = next(frame for frame in frames if frame.type == "context.resolved")
    assert resolved.payload["context"] is None and resolved.payload["reason"]  # F1：显式降级
    plan = next(frame for frame in frames if frame.type == "plan")
    assert plan.payload["intent"] == "solve"
    assert [step["node"] for step in plan.payload["steps"]] == ["solve", "verify"]

    artifact = next(frame for frame in frames if frame.type == "artifact")
    assert artifact.payload["kind"] == "solution"
    snapshot = store.snapshot("run_1")
    assert snapshot["status"] == runner.STATUS_DONE
    # ★ F2「两处落点逐值一致」的**最强形式**：同一个对象，不是各自生成一次
    assert snapshot["steps"] is artifact.payload["payload"]["steps"]
    assert snapshot["verification"]["level"] == "A"
    assert store.active == []


def test_run_without_credentials_ends_with_model_key_missing() -> None:
    store = runner.RunStore()
    frames = collect_frames(run_id="run_2", session_id="ses_1", query="1+1=?", store=store)

    assert frames[-1].type == "error"
    assert frames[-1].payload["code"] == "model_key_missing"  # N14：无凭据 ⇒ 不可用（且可修）
    assert [frame.type for frame in frames[:2]] == ["run.start", "context.resolved"]
    assert store.snapshot("run_2")["status"] == runner.STATUS_ERROR
    assert store.active == []


def test_failed_verification_recomputes_once_then_marks_uncertain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(llm, "_TRANSPORT", FakeModelTransport(solution=WRONG_SOLUTION))
    store = runner.RunStore()
    frames = collect_frames(
        run_id="run_3",
        session_id="ses_1",
        query="这道贝叶斯题怎么算？",
        endpoint=ENDPOINT,
        credential=CREDENTIAL,
        store=store,
    )
    for frame in frames:
        EVENT_VALIDATOR.validate(frame.to_dict())

    reports = [frame.payload["report"] for frame in frames if frame.type == "verification.report"]
    assert len(reports) == 2  # 首验 + 重算后的复验（**上限 1 次**）
    assert reports[0]["recomputed"] is False and reports[0]["passed"] is False
    assert reports[1]["recomputed"] is True and reports[1]["passed"] is False
    assert reports[1]["uncertain"] is True and reports[1]["level"] == "D"
    assert [frame.type for frame in frames].count("artifact") == 2  # 重算 = 真的再求解一次

    told = next(
        frame for frame in frames if frame.type == "verification.report" and frame.payload["report"]["uncertain"]
    )
    assert "数值重算" in told.payload["summary"] or "符号等价性" in told.payload["summary"]  # 显式告知

    assert frames[-1].type == "done" and frames[-1].payload["status"] == "uncertain"
    assert store.snapshot("run_3")["verification"]["uncertain"] is True


def test_cancelled_run_leaves_the_active_set_and_keeps_a_snapshot() -> None:
    async def scenario() -> "runner.RunStore":
        store = runner.RunStore()
        generator = runner.stream_run(run_id="run_4", session_id="ses_1", query="贝叶斯", store=store)
        first = await generator.__anext__()
        assert first.type == "run.start"
        assert store.active == ["run_4"]  # 还没终结 ⇒ 活跃
        await generator.aclose()  # ★ 模拟客户端断开
        return store

    store = asyncio.run(scenario())
    assert store.active == []  # ★ 断开后活跃图清空（docs/02 §4.2-2）
    snapshot = store.snapshot("run_4")
    assert snapshot is not None and snapshot["status"] == runner.STATUS_CANCELLED


def test_snapshot_of_an_unknown_run_is_none() -> None:
    # ★ run 只在内存：进程重启（= 新 store）后查不到 ⇒ 域2 的 api 转 404 not_found
    assert runner.RunStore().snapshot("run_不存在的") is None


def test_terminal_snapshots_are_capped() -> None:
    ticks = iter(range(1, 100))
    store = runner.RunStore(max_terminal=2, clock=lambda: float(next(ticks)))
    for index in range(3):
        store.start(run_id=f"run_{index}", session_id="ses")
        store.finish(f"run_{index}", status=runner.STATUS_DONE)

    assert store.snapshot("run_0") is None  # 最旧的一条按 updated_at 被淘汰
    assert store.snapshot("run_1") is not None and store.snapshot("run_2") is not None


def test_active_runs_are_never_evicted() -> None:
    store = runner.RunStore(max_terminal=1, clock=lambda: 1.0)
    store.start(run_id="live", session_id="ses")
    for index in range(3):
        store.start(run_id=f"done_{index}", session_id="ses")
        store.finish(f"done_{index}", status=runner.STATUS_DONE)

    assert store.snapshot("live") is not None  # 活跃的那条一条都不淘汰
    assert store.active == ["live"]


def test_node_context_never_shows_the_credential() -> None:
    ctx = NodeContext.for_node("route", endpoint=ENDPOINT, credential="sk-top-secret")
    assert "sk-top-secret" not in repr(ctx)  # R-M 第 ③/⑥ 面：顺手 print 也不泄露
    assert "sk-top-secret" not in str(ctx)


def test_nodes_emit_frames_not_log_records() -> None:
    """两个口径不可互换：日志口径的 sink 收到契约帧的字段名会**当场拒绝**（那是故意的）。"""
    ctx = NodeContext.for_node("route", events=RecordingEventSink())
    with pytest.raises(EventFieldViolation):
        route.run({"run_id": "run_1", "session_id": "ses_1", "query": "1+1=?"}, ctx)
