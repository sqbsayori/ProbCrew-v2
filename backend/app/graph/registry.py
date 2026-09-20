"""编排注册表 —— W0c · 卡3 · 第①步（``docs/06 §12.3``）。

**形状权威**：``contracts/registry.schema.json`` 的「注册表（graph/registry）」分支
（``name`` / ``entry`` / ``tools`` 三项必填 + ``x-startup-checks`` 五条）。
**裁定出处**：``docs/03 §7①`` —— 显式注册 + 启动校验。

**为什么显式**：旧仓靠扫描目录做自动发现（``kernel/registry.py``），结果是名字冲突与
漏注册只在运行时暴露，且「到底注册了什么」无法 grep。本表因此写成字面量：注册名 ·
入口函数 · ★ 该节点允许调用的工具集合 —— 最后一项是 R-Q ①（``docs/02 §1.6``）的
结构化形式，不是一句纪律。

★ ``entry`` 的写法：契约的 ``examples`` 里是 ``graph.nodes.solve:run``（省了包名前缀），
本仓实际可导入的路径是 ``app.graph.nodes.solve:run`` —— 本表存可导入的完整路径，
因为启动校验要真的把入口取出来并检查签名（「grep 得到」与「导得进」必须是同一个字符串）。

**不做**（卡3「不做什么」）：不接真实模型调用 · 不写 14 类事件 payload · 不写检索算法。
「并发度 > 1 拒绝启动」属卡1 的 ``main.py``（``docs/02 §8-15``），不在本模块。

**CLI**（判据「启动时能打印已注册清单」与「故意注册空集合会报错」的可演示面）::

    python -m app.graph.registry                   # 打印已注册清单（退出码 0）
    python -m app.graph.registry --demo-bad-tools   # 空集合 / 通配被拒（退出码 2）

★ ``--demo-bad-tools`` 的退出码 2 是演示的结论（坏输入被拒），不是失败。
"""
from __future__ import annotations

import argparse
import importlib
import inspect
import re
import sys
from dataclasses import dataclass
from typing import Sequence

#: 契约 ``name`` / ``tools[]`` 的 pattern（``contracts/registry.schema.json``）。
NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
NAME_MAX_LENGTH = 64
ENTRY_MAX_LENGTH = 200

#: 通配符 —— R-Q ①：「通配不行」（契约的 pattern 同样不接受）。
WILDCARDS = ("*", "?")


class RegistryError(RuntimeError):
    """注册表不合法 —— 启动校验的失败形状：坏就起不来（``docs/03 §7①``）。"""


@dataclass(frozen=True)
class RegistryEntry:
    """一条显式登记：注册名 · 入口函数 · ★ 该节点允许调用的工具集合。"""

    name: str
    entry: str
    tools: "Sequence[str] | None"


#: ★ 显式注册表本体 —— 三个节点取自 ``contracts/registry.schema.json`` 的 ``examples``。
#: 每个节点的工具集合都从 ``tools/`` 里已存在的形状文件里取，不出现悬空引用；
#: W3 / W4 接真实链路时再加集合成员（先加 ``tools/`` 的实现，再加这里的名字）。
REGISTRY: tuple[RegistryEntry, ...] = (
    # 路由：意图识别要走模型 ⇒ 只允许唯一出网口。
    RegistryEntry(name="route", entry="app.graph.nodes.route:run", tools=("llm",)),
    # 求解：检索教材语料 + 出解答 ⇒ retrieval + llm。
    RegistryEntry(name="solve", entry="app.graph.nodes.solve:run", tools=("llm", "retrieval")),
    # 验证：回查教材出处做 grounding ⇒ retrieval（符号重算类工具属 W3 / W4）。
    RegistryEntry(name="verify", entry="app.graph.nodes.verify:run", tools=("retrieval",)),
)


def validate_registry(entries: "Sequence[RegistryEntry]") -> tuple["RegistryEntry", ...]:
    """五条启动校验（``contracts/registry.schema.json`` 的 ``x-startup-checks``）。

    1. 注册名重复 ⇒ 报错退出；
    2. ★ ``tools`` 为空 / 为 ``None`` / 含通配 ⇒ 报错退出（R-Q ①）；
    3. ``entry`` 不存在或签名不符 ⇒ 报错退出；
    4. 注册名不合 ``^[a-z][a-z0-9_]*$`` ⇒ 报错退出（契约的 pattern）；
    5. 「已注册清单」可导出 —— 见 :func:`describe_registry`。

    坏输入一律抛 :class:`RegistryError`（不返回半成品表）。
    """
    if not entries:
        raise RegistryError("注册表为空 —— 图里至少要有一个节点（契约 nodes 的 minItems = 1）")
    seen: set[str] = set()
    for item in entries:
        _check_name(item)
        _check_tools(item)
        _check_entry(item)
        if item.name in seen:
            raise RegistryError(f"注册名重复: {item.name!r} —— 重复即报错退出（docs/03 §7①）")
        seen.add(item.name)
    return tuple(entries)


def _check_name(item: RegistryEntry) -> None:
    name = item.name
    if not isinstance(name, str) or len(name) > NAME_MAX_LENGTH or not NAME_PATTERN.match(name):
        raise RegistryError(
            f"注册名不合法: {name!r} —— 必须匹配 {NAME_PATTERN.pattern}（契约 name 的 pattern）"
        )


def _check_tools(item: RegistryEntry) -> None:
    tools = item.tools
    if tools is None:
        raise RegistryError(f"节点 {item.name} 的 tools 为 None —— R-Q ①：允许集合必须是非空显式清单")
    if isinstance(tools, (str, bytes)):
        raise RegistryError(f"节点 {item.name} 的 tools 不能是字符串 —— R-Q ①：它必须是工具名清单")
    names = tuple(tools)
    if not names:
        raise RegistryError(f"节点 {item.name} 的 tools 为空 —— R-Q ①：「什么都不调用」不得用空集合表达")
    for tool in names:
        if not isinstance(tool, str):
            raise RegistryError(f"节点 {item.name} 的工具名不是字符串: {tool!r}")
        if any(char in tool for char in WILDCARDS):
            raise RegistryError(
                f"节点 {item.name} 的工具名含通配符: {tool!r} —— R-Q ①：允许集合不得为通配"
            )
        if len(tool) > NAME_MAX_LENGTH or not NAME_PATTERN.match(tool):
            raise RegistryError(
                f"节点 {item.name} 的工具名不合法: {tool!r} —— 必须匹配 {NAME_PATTERN.pattern}"
            )


def _check_entry(item: RegistryEntry) -> None:
    target = item.entry
    if not isinstance(target, str) or not target or len(target) > ENTRY_MAX_LENGTH:
        raise RegistryError(f"节点 {item.name} 的 entry 不合法: {target!r}")
    module_path, separator, attribute = target.partition(":")
    if not separator or not module_path or not attribute:
        raise RegistryError(f"节点 {item.name} 的 entry 写法不合法: {target!r} —— 形如 模块:函数")
    try:
        module = importlib.import_module(module_path)
    except ImportError as exc:
        raise RegistryError(f"节点 {item.name} 的 entry 不存在: {target!r}（{exc}）") from exc
    function = getattr(module, attribute, None)
    if function is None or not callable(function):
        raise RegistryError(f"节点 {item.name} 的 entry 不存在: {target!r}")
    try:
        inspect.signature(function).bind(None, None)
    except TypeError as exc:
        raise RegistryError(
            f"节点 {item.name} 的 entry 签名不符: {target!r} —— "
            f"节点入口必须能接 (state, ctx) 两个位置参数（{exc}）"
        ) from exc


def load_registry(entries: "Sequence[RegistryEntry] | None" = None) -> tuple["RegistryEntry", ...]:
    """校验并返回注册表；坏则抛 :class:`RegistryError`（默认取 :data:`REGISTRY`）。"""
    return validate_registry(REGISTRY if entries is None else entries)


def describe_registry(entries: "Sequence[RegistryEntry] | None" = None) -> str:
    """导出「已注册清单」—— 可 grep、可断言（``docs/03 §7①`` 的第 4 条校验）。"""
    loaded = load_registry(entries)
    lines = [f"registry: {len(loaded)} nodes"]
    for item in loaded:
        tools = ",".join(tuple(item.tools or ()))
        lines.append(f"node={item.name} entry={item.entry} tools={tools}")
    return "\n".join(lines)


def main(argv: "Sequence[str] | None" = None) -> int:
    """CLI：打印已注册清单；``--demo-bad-tools`` 演示 R-Q ① 的拒绝。"""
    parser = argparse.ArgumentParser(
        prog="python -m app.graph.registry",
        description="打印已注册清单；--demo-bad-tools 演示空集合 / 通配集合被拒（R-Q ①）",
    )
    parser.add_argument(
        "--demo-bad-tools",
        action="store_true",
        help="故意注册一个空集合与一个通配集合，演示启动校验报错退出",
    )
    args = parser.parse_args(argv)

    if not args.demo_bad_tools:
        print(describe_registry())
        return 0

    bad_entries = (
        ("空集合", RegistryEntry(name="demo_empty", entry="app.graph.nodes.route:run", tools=())),
        ("通配", RegistryEntry(name="demo_wildcard", entry="app.graph.nodes.route:run", tools=("*",))),
    )
    for label, bad in bad_entries:
        try:
            load_registry((bad,))
        except RegistryError as exc:
            print(f"[拒绝] tools 为{label} ⇒ {exc}")
        else:
            print(f"[!!] tools 为{label} 竟然通过了 —— R-Q ① 失效")
            return 1
    print("[结论] 两类坏注册表都被拒绝（退出码 2 是演示的结论，不是失败）")
    return 2


if __name__ == "__main__":
    sys.exit(main())
