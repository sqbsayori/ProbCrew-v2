"""``tools/`` 的用例 —— 与被测模块同名（``docs/05 §3``）。

判据对照（``docs/06 §12.3`` 卡3 · ``docs/02 §1.6``）：

- R-Q ②：``ok`` 必须来自真实执行结果，禁止硬编码 ``true``；
- R-Q ③：工具失败必须进入事件流，不得静默吞掉；
- R-K：全仓唯一的运行期出网口是 ``tools/llm.py``。
"""
from __future__ import annotations

import ast
from pathlib import Path

from app.graph.nodes import solve
from app.graph.state import NodeContext, RecordingEventSink
from app.tools import MODEL_KEY_MISSING, NOT_IMPLEMENTED, llm, retrieval

BACKEND_DIR = Path(__file__).resolve().parents[1]
APP_DIR = BACKEND_DIR / "app"

#: 出网 / 套接字类依赖 —— 只允许出现在 ``tools/llm.py``（R-K）。
EGRESS_LIBS = {"httpx", "requests", "urllib", "socket", "aiohttp", "http"}
ALLOWED_EGRESS_FILE = APP_DIR / "tools" / "llm.py"


def imported_roots(path: Path) -> set[str]:
    """静态取出一个文件 import 的模块根名（AST，不执行）。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return {name.split(".")[0] for name in modules}


def test_llm_without_credentials_is_not_ok() -> None:
    result = llm.call_model([{"role": "user", "content": "1+1=?"}])
    assert result.ok is False
    assert result.reason == MODEL_KEY_MISSING


def test_llm_with_credentials_still_reports_not_implemented() -> None:
    result = llm.call_model(
        [{"role": "user", "content": "1+1=?"}],
        endpoint="https://model.invalid/v1",
        credential="not-a-real-key",
    )
    assert result.ok is False
    assert result.reason == NOT_IMPLEMENTED


def test_retrieval_is_shape_only() -> None:
    result = retrieval.search("条件概率")
    assert result.ok is False
    assert result.reason == NOT_IMPLEMENTED


def test_tool_failures_enter_the_event_stream() -> None:
    sink = RecordingEventSink()
    ctx = NodeContext.for_node("solve", events=sink)

    solve.run({"run_id": "run_1", "session_id": "sess_1", "query": "求 2+2"}, ctx)

    failed = [
        record
        for record in sink.records
        if record.event_type == "tool.result" and record.fields["status"] == "failed"
    ]
    assert len(failed) == 2  # retrieval 与 llm 本轮都未落地 ⇒ 两条失败都可见
    assert all(record.fields.get("reason") for record in failed)


def test_egress_libraries_only_appear_in_the_llm_module() -> None:
    offenders: dict[str, list[str]] = {}
    for path in APP_DIR.rglob("*.py"):
        if path == ALLOWED_EGRESS_FILE:
            continue
        bad = sorted(imported_roots(path) & EGRESS_LIBS)
        if bad:
            offenders[str(path.relative_to(BACKEND_DIR))] = bad
    assert offenders == {}
