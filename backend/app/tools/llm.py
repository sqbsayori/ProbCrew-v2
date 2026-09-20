"""模型调用工具 —— W0c · 卡3 · 第③步（``docs/06 §12.3``）。

★ 本文件是全仓唯一的运行期出网口（R-K，``docs/02 §1.6`` / ``ADR-0005 §3``）：
``httpx`` / ``requests`` / ``urllib`` / ``socket`` 只允许出现在这里，出现在别处即红灯
（本地断言见 ``backend/tests/test_tools.py``）。

★ 本轮（W0）不接真实模型调用（卡3「不做什么」）：签名与返回形状先定死，实现留空
—— 因此本文件现在不 import 任何出网库，一次请求都不会发出去。

★ 出网载荷白名单（R-L）与端点信任边界（``docs/02 §4.2.1``）同样只留位置：W3 接真实
调用时在这里补上。
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from app.tools import MODEL_KEY_MISSING, NOT_IMPLEMENTED, ToolResult


def call_model(
    messages: "Sequence[Mapping[str, Any]]",
    *,
    endpoint: str = "",
    credential: str = "",
    timeout_s: float = 30.0,
) -> ToolResult:
    """调用用户自持凭据的模型端点（W0：只定形状，不发请求）。

    W0 恒为 ``ok=False``，两种原因都在返回值里，不静默成功（R-Q ②）：

    - 端点或凭据缺失 ⇒ ``reason="model_key_missing"``（对齐 ``app.core.errors`` 的错误码）；
    - 已配置 ⇒ ``reason="not_implemented"``（真实调用属 W3）。
    """
    if not endpoint or not credential:
        return ToolResult(ok=False, reason=MODEL_KEY_MISSING)
    return ToolResult(ok=False, reason=NOT_IMPLEMENTED)


#: 工具入口 —— ``graph.state.ToolBox`` 按这个名字取（``app/tools/__init__.py`` 的约定）。
ENTRY = call_model
