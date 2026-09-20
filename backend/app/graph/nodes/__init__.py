"""app.graph.nodes —— 节点空壳（W0c · 卡3 · 第②步）。

每个节点只做三件事：取输入 → 调 ``domain`` / ``tools`` → 落事件。

★ 节点里没有判定 / 错因 / 分类逻辑（``docs/03 §3.2`` 第 2 条）：那些规则只在
``app.domain`` 里存在一份；写进节点会让同一套规则在 ``domain`` 与图里各有一份，
而 N1 的注入集只测 ``domain``。

新增一个节点的三处落点（``docs/03 §7①``）：本目录下的实现 · ``graph/registry.py``
的显式登记 · ``backend/tests/`` 的一个节点级用例。
"""
from __future__ import annotations

__all__: list[str] = []
