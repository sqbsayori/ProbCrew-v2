"""app.graph —— 编排：显式注册表 + 节点空壳 + 输入/输出约定（W0c · 卡3）。

交给下游的形状（``docs/06 §12.8`` 卡3 的接口清单）：

- :mod:`app.graph.registry` —— 显式注册表（注册名 · 入口函数 · ★ 允许的工具集合）·
  启动校验 · 「已注册清单」的导出（R-Q ①）；
- :mod:`app.graph.state` —— 节点输入 / 输出约定：``GraphState`` · ``EventSink``
  （只记 ID 与动作）· ``ToolBox``（按允许集合放行工具调用）；
- :mod:`app.graph.nodes` —— 节点空壳，只做「取输入 → 调 ``domain`` / ``tools`` → 落事件」。

依赖方向（``docs/03 §3.1``）：``graph/`` 允许 import ``core/`` / ``domain/`` / ``tools/``，
**禁止 import ``api/``**（可静态核对：``backend/tests/test_registry.py``）。

边界（``docs/06 §12.3`` 卡3「不做什么」）：不接真实模型调用 · 不写 14 类事件 payload ·
不写检索算法 · 不写业务判定（判定只在 ``app.domain`` 里有一份，``docs/03 §3.2`` 第 2 条）。
"""
from __future__ import annotations

__all__: list[str] = []
