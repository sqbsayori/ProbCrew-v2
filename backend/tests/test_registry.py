"""``graph/registry.py`` 的用例 —— 与被测模块同名（``docs/05 §3``）。

判据对照（``docs/06 §12.3`` 卡3）：

- ★ R-Q ①：允许集合非空且非通配，且故意注册一个空集合时会报错；
- 启动时能导出「已注册清单」（可 grep、可断言）；
- ``graph/`` 不 import ``api/``；节点里没有手写的判定 / 错因逻辑（静态断言）。
"""
from __future__ import annotations

import ast
from pathlib import Path
from typing import Any, Sequence

import pytest

from app.graph import registry
from app.graph.nodes import route, solve, verify
from app.graph.state import NodeContext, RecordingEventSink, ToolBox, ToolNotAllowed

BACKEND_DIR = Path(__file__).resolve().parents[1]
GRAPH_DIR = BACKEND_DIR / "app" / "graph"

#: 节点里不允许出现的计算库（``docs/03 §3.2`` 第 2 条的启发式检查）。
COMPUTATION_LIBS = {"sympy", "numpy", "scipy", "pandas"}


def entry(
    name: str,
    target: str = "app.graph.nodes.route:run",
    tools: "Sequence[str] | None" = ("llm",),
) -> "registry.RegistryEntry":
    """造一条登记项 —— 只用于构造坏输入（正常表是 ``registry.REGISTRY``）。"""
    return registry.RegistryEntry(name=name, entry=target, tools=tools)


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


def test_registry_has_three_explicit_nodes() -> None:
    loaded = registry.load_registry()
    assert [item.name for item in loaded] == ["route", "solve", "verify"]


def test_describe_registry_is_exportable_and_greppable() -> None:
    text = registry.describe_registry()
    assert "registry: 3 nodes" in text
    assert "node=route entry=app.graph.nodes.route:run tools=llm" in text
    assert "node=solve entry=app.graph.nodes.solve:run tools=llm,retrieval" in text
    assert "node=verify entry=app.graph.nodes.verify:run tools=retrieval" in text


def test_duplicate_name_is_rejected() -> None:
    with pytest.raises(registry.RegistryError, match="重复"):
        registry.load_registry((entry("route"), entry("route")))


def test_empty_registry_is_rejected() -> None:
    with pytest.raises(registry.RegistryError, match="注册表为空"):
        registry.load_registry(())


def test_invalid_registration_name_is_rejected() -> None:
    with pytest.raises(registry.RegistryError, match="注册名不合法"):
        registry.load_registry((entry("Route"),))


@pytest.mark.parametrize("tools", [(), None])
def test_empty_or_missing_tool_set_is_rejected(tools: "Sequence[str] | None") -> None:
    with pytest.raises(registry.RegistryError, match="R-Q ①"):
        registry.load_registry((entry("route", tools=tools),))


@pytest.mark.parametrize("tool", ["*", "?*", "kb_*"])
def test_wildcard_tool_set_is_rejected(tool: str) -> None:
    with pytest.raises(registry.RegistryError, match="通配"):
        registry.load_registry((entry("route", tools=(tool,)),))


def test_missing_entry_is_rejected() -> None:
    with pytest.raises(registry.RegistryError, match="entry 不存在"):
        registry.load_registry((entry("ghost", target="app.graph.nodes.ghost:run"),))


def test_entry_with_unsupported_signature_is_rejected() -> None:
    # describe_registry 只接受 1 个位置参数 ⇒ 当不了节点入口 (state, ctx)
    with pytest.raises(registry.RegistryError, match="签名不符"):
        registry.load_registry(
            (entry("bad_signature", target="app.graph.registry:describe_registry"),)
        )


def test_toolbox_derives_allowed_set_from_registry() -> None:
    assert ToolBox.for_node("solve").allowed == frozenset({"llm", "retrieval"})


def test_undeclared_tool_call_is_rejected() -> None:
    box = ToolBox(node="route", allowed=frozenset({"llm"}))
    with pytest.raises(ToolNotAllowed, match="R-Q ①"):
        box.call("retrieval")


def test_graph_never_imports_api() -> None:
    offenders: dict[str, list[str]] = {}
    for path in GRAPH_DIR.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        bad = sorted(
            {
                node.module
                for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom)
                and node.module
                and (node.module == "app.api" or node.module.startswith("app.api."))
            }
        )
        if bad:
            offenders[path.name] = bad
    assert offenders == {}


def test_nodes_carry_no_handwritten_computation() -> None:
    offenders: dict[str, list[str]] = {}
    for path in (GRAPH_DIR / "nodes").glob("*.py"):
        bad = sorted(imported_roots(path) & COMPUTATION_LIBS)
        if bad:
            offenders[path.name] = bad
    assert offenders == {}


def _node_name(module: Any) -> str:
    return str(module.__name__).rsplit(".", 1)[-1]


@pytest.mark.parametrize("module", [route, solve, verify], ids=_node_name)
def test_node_shell_takes_input_emits_events_and_returns_update(module: Any) -> None:
    name = _node_name(module)
    sink = RecordingEventSink()
    ctx = NodeContext.for_node(name, events=sink)

    update = module.run({"run_id": "run_1", "session_id": "sess_1", "query": "求 2+2"}, ctx)

    assert name in update
    assert sink.event_types[0] == "node.start"
    assert sink.event_types[-1] == "node.end"
    assert "tool.result" in sink.event_types
