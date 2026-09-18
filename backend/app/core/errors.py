"""统一错误体 —— `core/` 的第二件东西。

★ **形状的唯一权威是 `docs/02 §6.2`，`code` 与 HTTP 状态的唯一权威是 `docs/02 §6.3`**：

    {"error": {"code": "unauthenticated", "message": "未登录或登录已过期，请重新登录",
               "detail": {"field": "password", "reason": "too_short"}}}

本模块只做两件事：把两张表落成**机器可用的常量**、把错误对象转成那个形状。
**不在这里复述每个 code 的语义**（复述就是第二处记载），要读语义请回 `docs/02 §6.3`。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

__all__ = ["ERROR_CODES", "ApiError", "error_body"]

#: `docs/02 §6.3` 的 code → HTTP 状态，逐条对齐（16 条）。
#: ★ `status` 由这张表派生，**不作为可传参的字段** —— 否则同一 code 会出现两个状态。
ERROR_CODES: Mapping[str, int] = MappingProxyType(
    {
        "invalid_argument": 400,
        "model_key_invalid": 400,
        "unauthenticated": 401,
        "bad_credentials": 401,
        "forbidden": 403,
        "must_change_pw": 403,
        "account_disabled": 403,
        "not_found": 404,
        "conflict": 409,
        "locked": 423,
        "internal_error": 500,
        "model_quota_exceeded": 402,
        "dependency_unavailable": 503,
        "server_busy": 503,
        "model_key_missing": 503,
        "model_unreachable": 503,
    }
)


@dataclass(frozen=True)
class ApiError(Exception):
    """可转成统一错误体的异常。

    * `code` —— 必须在 `ERROR_CODES` 里（写错字符串比"状态码写错"更难发现，所以这里直接抛）
    * `message` —— 给人看的中文说明，**不含内部路径 / SQL / 堆栈**
    * `detail` —— 可选的结构化补充（**值只允许字符串**，避免顺手塞进整个请求体）
    * `status` —— 派生属性，来自 `ERROR_CODES`
    """

    code: str
    message: str
    detail: Mapping[str, str] | None = field(default=None)

    def __post_init__(self) -> None:
        if self.code not in ERROR_CODES:
            raise ValueError(f"未知错误码 {self.code!r}（枚举见 docs/02 §6.3）")
        if not self.message:
            raise ValueError("message 不得为空（docs/02 §6.2）")
        if self.detail is not None:
            for key, value in self.detail.items():
                if not isinstance(value, str):
                    raise ValueError(f"detail[{key!r}] 必须是字符串（docs/02 §6.2）")
        Exception.__init__(self, f"{self.code}: {self.message}")

    @property
    def status(self) -> int:
        """HTTP 状态码 —— 由 `ERROR_CODES` 派生，不由调用方传。"""
        return ERROR_CODES[self.code]


def error_body(exc: ApiError) -> dict[str, object]:
    """把 `ApiError` 转成统一错误体（`docs/02 §6.2` 的形状）。

    `detail` 为空时**该键不出现**（不是 `null`）—— 前端据此区分"没有补充信息"与"补充信息是空的"。
    """
    body: dict[str, object] = {"code": exc.code, "message": exc.message}
    if exc.detail:
        body["detail"] = dict(exc.detail)
    return {"error": body}
