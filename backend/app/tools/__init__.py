"""app.tools —— 工具层接口形状（W0c · 卡3 · 第③步，``docs/06 §12.3``）。

约定（R-Q 的施加点，``docs/03 §7②``）：

- 一个工具 = 一个模块，模块名就是注册表 ``tools`` 里的工具名（``llm`` / ``retrieval``）；
- 每个工具模块必须暴露 ``ENTRY``（唯一入口，可调用）—— ``graph.state.ToolBox`` 按它取；
- 入口返回 :class:`ToolResult`：``ok`` 必须来自真实执行结果，禁止硬编码 ``true``（R-Q ②）；
- 失败不静默：失败也返回 ``ToolResult(ok=False, reason=<短代码>)``，由节点落进事件流（R-Q ③）；
- ★ ``tools/llm.py`` 是全仓唯一运行期出网口（R-K，``docs/02 §1.6`` / ``ADR-0005 §3``）
  —— ``httpx`` / ``requests`` / ``urllib`` / ``socket`` 只能出现在那里。

依赖方向（``docs/03 §3.1``）：``tools/`` 允许 import ``core/`` / ``domain/``；
禁止 import ``api/`` / ``graph/``。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

#: 失败原因用的短代码（R-O 同口径：进日志 / 事件 / 错误体的只能是短代码，不是内容）。
#:
#: ★ 前四个与 `contracts/error.schema.json` 的 `code` **同名同值**（W3 起真的会用到）——
#:   它们是**对外**的错误码；其余是**对内**的工具失败原因（进事件流，由节点/运行器决定对外的 code）。
NOT_IMPLEMENTED = "not_implemented"  # 形状已定、实现未落（W0 期间的默认返回值）
MODEL_KEY_MISSING = "model_key_missing"
MODEL_UNREACHABLE = "model_unreachable"
MODEL_KEY_INVALID = "model_key_invalid"
MODEL_QUOTA_EXCEEDED = "model_quota_exceeded"

#: 对内原因（不外泄内容，只给机器读的短代码）。
INVALID_REQUEST = "invalid_request"      # 入参形状不对（消息序列 / 字段类型）
INVALID_RESPONSE = "invalid_response"    # 上游 2xx 但没有可用的回答正文
PAYLOAD_REJECTED = "payload_rejected"    # R-L：出网载荷命中反向禁止清单
PARSE_ERROR = "parse_error"              # S2：表达式无法解析（不算通过、也不算不通过）
TIMEOUT = "timeout"                      # S2：验证手段在截止时间内没跑完（同上：未执行，不是不通过）
CITE_UNAVAILABLE = "cite_unavailable"    # W4/F10：无命中或语料缓存缺失，如实降级

#: 对外的四个模型类 `code`（与 `app.core.errors` 的枚举同源；有一条用例守着不漂移）。
MODEL_CODES: tuple[str, ...] = (
    MODEL_KEY_MISSING,
    MODEL_UNREACHABLE,
    MODEL_KEY_INVALID,
    MODEL_QUOTA_EXCEEDED,
)

__all__ = [
    "CITE_UNAVAILABLE",
    "INVALID_REQUEST",
    "INVALID_RESPONSE",
    "MODEL_CODES",
    "MODEL_KEY_INVALID",
    "MODEL_KEY_MISSING",
    "MODEL_QUOTA_EXCEEDED",
    "MODEL_UNREACHABLE",
    "NOT_IMPLEMENTED",
    "PARSE_ERROR",
    "PAYLOAD_REJECTED",
    "TIMEOUT",
    "ToolResult",
]


@dataclass(frozen=True)
class ToolResult:
    """工具调用结果。

    ``ok`` 是真实执行结果（R-Q ②）；失败时 ``reason`` 给机器读的短代码，由节点写进
    事件流（R-Q ③：不得静默吞掉）。
    """

    ok: bool
    value: Any = None
    reason: "str | None" = None
