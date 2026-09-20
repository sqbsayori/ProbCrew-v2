"""本地语料检索工具 —— W0c · 卡3 · 第③步（``docs/06 §12.3``）。

★ 本轮只定形状（卡3「不做什么」：不写检索算法）—— 检索算法与 L1 语料缓存属 W4。

本文件不出网：运行期唯一的出网口是 ``tools/llm.py``（R-K）。
"""
from __future__ import annotations

from app.tools import NOT_IMPLEMENTED, ToolResult


def search(query: str, *, corpus_dir: str = "", limit: int = 5) -> ToolResult:
    """检索本地教材语料（W0：只定形状）。

    W0 恒为 ``ok=False / reason="not_implemented"`` —— 失败如实返回而不是假装命中
    （``docs/02 §4.2``：无命中要如实说明，N2 的诚实性从这里开始）。
    """
    return ToolResult(ok=False, reason=NOT_IMPLEMENTED)


#: 工具入口 —— ``graph.state.ToolBox`` 按这个名字取（``app/tools/__init__.py`` 的约定）。
ENTRY = search
