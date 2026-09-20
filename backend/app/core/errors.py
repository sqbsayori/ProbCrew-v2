"""统一错误体（ErrorEnvelope）构造 —— W0b · 卡2 · 第①步（docs/06 §12.3）。

形状权威是 ``contracts/error.schema.json``；语义权威（触发条件与前端行为）是 ``docs/02 §6.3``。
本模块把契约里的三条纪律变成**构造时即强制**：

① ``code`` 只允许枚举内 16 个稳定标识（只增不改，机器读）；
② ``message`` 给人看的中文，1–200 字，不含内部路径 / SQL / 堆栈 / 凭据（调用方自律，构造器只管长度）；
③ ``detail`` 的键名命中敏感模式（key/token/secret/password/credential/authorization/api_key）时**直接拒绝构造**
   —— 这是 R-M 第③面「凭据不得进错误信息」的机器可执行形式（契约 propertyNames 的 not-pattern）。

纯标准库；HTTP 状态映射取自契约 ``x-http-status-by-code``（由 verify.sh 门禁对账，docs/04 §7 校验③）。
"""
from __future__ import annotations

import re
from typing import Any

#: 与 contracts/error.schema.json definitions.errorCode.enum 逐项一致（16 个）。
ERROR_CODES: tuple[str, ...] = (
    "invalid_argument",
    "unauthenticated",
    "bad_credentials",
    "forbidden",
    "must_change_pw",
    "account_disabled",
    "not_found",
    "conflict",
    "locked",
    "internal_error",
    "dependency_unavailable",
    "model_key_missing",
    "model_unreachable",
    "model_key_invalid",
    "model_quota_exceeded",
    "server_busy",
)

#: 与 contracts/error.schema.json x-http-status-by-code 逐项一致。
HTTP_STATUS_BY_CODE: dict[str, int] = {
    "invalid_argument": 400,
    "unauthenticated": 401,
    "bad_credentials": 401,
    "forbidden": 403,
    "must_change_pw": 403,
    "account_disabled": 403,
    "not_found": 404,
    "conflict": 409,
    "locked": 423,
    "internal_error": 500,
    "dependency_unavailable": 503,
    "model_key_missing": 503,
    "model_unreachable": 503,
    "model_key_invalid": 400,
    "model_quota_exceeded": 402,
    "server_busy": 503,
}

#: 契约 detail.propertyNames 的 not-pattern：命中即拒绝构造。
_SENSITIVE_KEY = re.compile(r"(?i)(key|token|secret|password|passwd|credential|authorization|api_?key)")

_MESSAGE_MAX = 200


class AppError(Exception):
    """业务可预期错误的统一载体。

    ``code`` / ``message`` / ``detail`` 与 ErrorEnvelope 一一对应；
    ``http_status`` 由 code 派生，API 层（W1+）据此设置响应状态码。
    构造即校验：任何违反契约纪律的用法在**抛出点**就失败，而不是等到响应序列化。
    """

    def __init__(self, code: str, message: str, detail: "dict[str, Any] | None" = None) -> None:
        if code not in ERROR_CODES:
            raise ValueError(f"未知错误码: {code!r}（必须在 ERROR_CODES 枚举内，只增不改）")
        if not isinstance(message, str) or not (1 <= len(message) <= _MESSAGE_MAX):
            raise ValueError(f"message 必须是 1–{_MESSAGE_MAX} 字的字符串")
        if detail is not None:
            if not isinstance(detail, dict):
                raise ValueError("detail 必须是对象（dict）")
            for key in detail:
                if _SENSITIVE_KEY.search(str(key)):
                    raise ValueError(f"detail 键名 {key!r} 命中敏感模式，凭据不得进错误信息（R-M）")
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.message = message
        self.detail = detail
        self.http_status = HTTP_STATUS_BY_CODE[code]

    def to_envelope(self) -> dict[str, Any]:
        """序列化为 contracts/error.schema.json 的 ErrorEnvelope 形状。"""
        error: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.detail is not None:
            error["detail"] = self.detail
        return {"error": error}


def error_envelope(code: str, message: str, detail: "dict[str, Any] | None" = None) -> dict[str, Any]:
    """一次性构造 ErrorEnvelope（不抛异常路径使用，如通用 500 处理器）。"""
    return AppError(code, message, detail).to_envelope()
