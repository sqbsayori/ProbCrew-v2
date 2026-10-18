"""FastAPI 依赖 —— W1（`docs/03 §3.2`：**`Depends` / `Header` 只住在 `api/`**）。

★ 为什么依赖注入不能下沉到 `identity/`：`docs/03 §3.1` 对 `identity/` 的**明确禁止**里含
  ``fastapi``，`scripts/verify.sh` 的 `import_direction()` 会直接判失败。⇒ 本文件是
  "HTTP 世界"与"纯函数世界"的**唯一接缝**：上面接请求头，下面调 :func:`app.identity.auth.resolve_bearer`。

**连接生命周期**（`docs/02 §3.6`：写事务集中在一个 ``closing`` 里）：
:func:`get_conn` 每次请求开一个连接、请求结束关掉。FastAPI 的依赖默认**按请求缓存**
（同一请求里多次 ``Depends(get_conn)`` 拿到的是**同一个**连接）—— 这正是
"作答 + 掌握度 + 提示计数读回"这类复合写能落在**同一事务**里的前提，也是它不会被顺手写成
"每个函数各开一个连接"的原因。
"""
from __future__ import annotations

import sqlite3
from typing import Iterator

from fastapi import Depends, Header

from app.core import db
from app.core.errors import AppError
from app.identity.auth import AuthContext, resolve_bearer

__all__ = ["current_context", "get_conn", "require_admin"]


def get_conn() -> Iterator[sqlite3.Connection]:
    """请求级连接（**唯一的建库入口**在 `core/db.py`，这里只是它的请求面）。"""
    connection = db.initialize()
    try:
        yield connection
    finally:
        connection.close()


def current_context(
    authorization: "str | None" = Header(default=None),
    conn: sqlite3.Connection = Depends(get_conn),
) -> AuthContext:
    """把 ``Authorization: Bearer <token>`` 解析成 :class:`AuthContext`；无效 ⇒ 401。"""
    context = resolve_bearer(conn, authorization)
    if context is None:
        raise AppError("unauthenticated", "未登录或登录已过期，请重新登录")
    return context


def require_admin(context: AuthContext = Depends(current_context)) -> AuthContext:
    """角色不足 ⇒ **403 ``forbidden``**（`docs/02 §6.3`；本批无消费方，是 P3 的接缝）。

    ★ ``forbidden`` **只**保留给"角色不足"这一件事：账号被停用走 ``account_disabled``、
      强制改密走 ``must_change_pw``、读他人资源走 ``not_found`` —— 四个 code 各管一事，
      前端才能给出四句不同的话（这一点由 `docs/02 §6.3` 的"前端行为"列写死）。
    """
    if context.role != "admin":
        raise AppError("forbidden", "无权访问")
    return context
