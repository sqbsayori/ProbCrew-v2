"""请求体形状（Pydantic）—— W1 · 最小身份闭环。

**形状权威是契约**，本文件只是它的 Python 形式（字段名 / 上下界逐项对齐
`contracts/user.schema.json#/definitions/{loginRequest,passwordChangeRequest}`）：

| 契约 | 本文件 |
|---|---|
| `additionalProperties: false` | `extra="forbid"` ⇒ 多传字段直接 400 `invalid_argument` |
| `username` 3–32 · `password` 1–72 | `Field(min_length=…, max_length=…)` |

★ **`extra="forbid"` 不是风格，是防提权的落点**：契约的 `loginRequest` 里**没有** `role`
  —— 若允许额外字段，`{"username":…, "password":…, "role":"admin"}` 这样的请求就会一路
  走到存储层。F15 的"接口不可创建管理员"因此不只是端点设计的自觉，而是**形状层面**的事实。

★ **R-A①**：`scripts/verify.sh` 会扫 `class X(BaseModel)` 里每个 `name: str` 声明**同一行**
  是否带 `max_length` ——
  （`docs/02 §6.1`：请求体的字符串字段必须有上界，否则超长输入就是一条免费的资源消耗面）。

★ **`max_length` 按"字符"计，而 bcrypt 的上限按"字节"计** ⇒ 二者不等价。
  因此字节校验必须**另做一次**，落点在 `identity/users.py` 的 `check_password_bytes()`
  （对"25 个中文字符"这类输入，schema 会放行、字节校验会拦下）。
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "AdminImportRequest",
    "AdminImportRow",
    "AdminUserPatch",
    "ChatContext",
    "ChatContextRef",
    "ChatEndpoint",
    "ChatRequest",
    "GradeRequest",
    "HintRequest",
    "LoginRequest",
    "NoticeAckRequest",
    "PasswordChangeRequest",
]


class LoginRequest(BaseModel):
    """`POST /api/auth/login` 的请求体（契约 `loginRequest`）。"""

    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=3, max_length=32)
    password: str = Field(min_length=1, max_length=72)


class PasswordChangeRequest(BaseModel):
    """`POST /api/auth/password` 的请求体（契约 `passwordChangeRequest`）。

    ★ 新密码的**强度下限不在契约内**（契约原文："强度要求不在契约内，由 `identity/` 决定"）
      ⇒ 本仓取"非空"（= 契约最小集），**不自造 8 位阈值** —— 自造阈值等于新口径，
      而新口径要回写文档才算数。
    """

    model_config = ConfigDict(extra="forbid")

    old_password: str = Field(min_length=1, max_length=72)
    new_password: str = Field(min_length=1, max_length=72)


class GradeRequest(BaseModel):
    """`POST /api/practice/grade` 的请求体（`docs/02 §4.3`）。

    ★ **`extra="ignore"` 而不是 `forbid`**：契约 invariant 写死「请求体里出现
      `hint_used` / `level` 时**一律忽略、不报错**（避免旧客户端崩），但绝不采信」。
      这两个值由服务端从流水派生，客户端传什么都不影响结果。
    """

    model_config = ConfigDict(extra="ignore")

    item_id: str = Field(pattern=r"^prob_[a-z0-9_]+$", max_length=64)
    answer: str = Field(min_length=1, max_length=200)
    session_id: str = Field(min_length=1, max_length=64)
    #: ★ 契约 `attempt.duration_ms` 是**非空整数**（`type: integer`，没有 null 支）⇒
    #:   缺省取 0（= 未提供）。它是**客户端自陈值，不参与任何验收**（`docs/01` F7 已记账）。
    duration_ms: int = Field(default=0, ge=0, le=86_400_000)


class HintRequest(BaseModel):
    """`POST /api/practice/hint` 的请求体 —— **没有 `level`**（级别由服务端按流水推进）。"""

    model_config = ConfigDict(extra="ignore")

    item_id: str = Field(pattern=r"^prob_[a-z0-9_]+$", max_length=64)
    session_id: str = Field(min_length=1, max_length=64)


class ChatContextRef(BaseModel):
    """`POST /api/chat` 的 `context.refs[]`（契约 `contextResolvedPayload.context`）。"""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["problem", "kc", "corpus"]
    id: str = Field(min_length=1, max_length=64)


class ChatContext(BaseModel):
    """答疑上下文：正文 + 最多 20 个标识（R-A 的嵌套深度 ≤ 3）。"""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(max_length=4000)
    refs: "list[ChatContextRef]" = Field(default_factory=list, max_length=20)


class ChatEndpoint(BaseModel):
    """用户提供的模型端点（`contracts/chat.schema.json#/definitions/endpoint`）。"""

    model_config = ConfigDict(extra="forbid")

    provider: Literal["deepseek", "qwen", "glm", "custom"]
    model: str = Field(min_length=1, max_length=128)
    base_url: str = Field(min_length=1, max_length=2048)


class ChatRequest(BaseModel):
    """`POST /api/chat` 的请求体（凭据不在这里 —— 只走 `X-Model-Key` 头）。"""

    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=2000)
    context: "ChatContext | None" = None
    endpoint: ChatEndpoint


class NoticeAckRequest(BaseModel):
    """`POST /api/me/notice` 的请求体：只能置位，不能清除。"""

    model_config = ConfigDict(extra="forbid")

    ack: Literal[True]


class AdminImportRow(BaseModel):
    """`POST /api/admin/users/import` 的一行（契约 `adminImportRequest.rows.items`）。

    ★ **`extra="forbid"` 在这里是制度**：契约的导入行里**没有** `role` / 学号 / 真实姓名 ——
      多传一个 `role: "admin"` 必须当场 400，而不是"忽略掉"（忽略等于让调用方以为生效了）。
      F15 的「接口不可创建管理员」因此是**形状层面**的事实，不是端点里的一句 if。
    """

    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=3, max_length=32, pattern=r"^[A-Za-z0-9_.-]{3,32}$")
    password: str = Field(min_length=1, max_length=72)
    display_name: str = Field(default="", max_length=64)


class AdminImportRequest(BaseModel):
    """`POST /api/admin/users/import` 的请求体（契约 `adminImportRequest`：`rows` 1–500）。"""

    model_config = ConfigDict(extra="forbid")

    rows: "list[AdminImportRow]" = Field(min_length=1, max_length=500)


class AdminUserPatch(BaseModel):
    """`PATCH /api/admin/users/{id}` 的请求体 —— **每个字段都可选，但至少要有一个**。

    ★ 字段名与 `adminUserRow` 对齐（`display_name` / `status` / `role` / 锁），**没有** `username`：
      改登录名会让"这是同一个人"变成需要另一张对照表的事（契约 `username` 的复用规则
      只在**删除**时改名为 `deleted_<8hex>`）。
    ★ `role` / `status` 收成 `str` 而**不是** `Literal[...]`：F15 要的拒绝理由
      （"接口不可创建管理员，请用 CLI"）用 `Literal` 会被 FastAPI 变成一句通用的
      "请求参数不合法" —— 400 对了，但**为什么**没了。校验放在 `api/admin.py` 里做。
    ★ `password` 在场 ⇒ **重置密码分支**（强制改密 + 令牌全撤 + 写审计），响应换成
      `adminPasswordResetResult`；其余字段任意组合 ⇒ `update_user` 分支，响应是 `adminUserRow`。
    """

    model_config = ConfigDict(extra="forbid")

    display_name: "str | None" = Field(default=None, max_length=64)
    status: "str | None" = Field(default=None, max_length=16)
    role: "str | None" = Field(default=None, max_length=16)
    password: "str | None" = Field(default=None, min_length=1, max_length=72)
    unlock: bool = False

    def is_empty(self) -> bool:
        """是否一个字段都没给（`unlock=False` 不算"给了"）—— 空 PATCH 是 400，不是 200 无事发生。"""
        return (
            self.display_name is None
            and self.status is None
            and self.role is None
            and self.password is None
            and not self.unlock
        )
