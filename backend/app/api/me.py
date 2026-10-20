"""`/api/me` —— W1 · 最小身份闭环（`docs/02 §6.1` 的身份行）。

| 端点 | 认证 | 成功 | 失败 |
|---|---|---|---|
| `GET /api/me` | 是 | 200 账号对象（`contracts/user.schema.json` 的**顶层**） | 401 `unauthenticated` |
| `GET /api/me/wrong` | 是 | 200 `pagination` 信封 + `attempt` 视图（C6 + C2） | 400 `invalid_argument`（limit / cursor） |
| `GET /api/me/progress` | 是 | 200 `mastery` 响应（C8：总作答数 + 逐知识点正确率 + 薄弱占位） | 401 `unauthenticated` |

★ **为什么这个端点值得单独一个文件与一条约束**：外壳的 "刷新页面" 这一步靠它 ——
  `frontend/core/auth.js` 的注释写明"刷新后角色由服务端 ``/api/me`` 重新取"，
  而当前用户**不落 ``localStorage``**（`docs/02 §7.4` 的表里没有它）。因此它是
  "越权隔离（F15）"在前端唯一能拿到的身份来源。

★ **它必须在 ``must_change_pw`` 的放行集合里**：否则学生登录后被强制改密，
  前端拿不到"要改密"这个事实（放行集合见 `identity/auth.py` 的
  :data:`~app.identity.auth.PASSWORD_CHANGE_ALLOWLIST`）。

★ 返回体一律经 :func:`app.identity.users.to_public` —— **白名单投影**，因此
  `password_hash` / `failed_count` / `locked_until` 不可能"顺手"出现在响应里
  （契约 `additionalProperties: false` 把"多一个字段"本身判成违约）。
"""
from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import APIRouter, Depends, Query

from app.api.deps import current_context, get_conn, password_gate
from app.core.errors import AppError
from app.identity import users
from app.identity.auth import AuthContext
from app.learning import store

__all__ = ["router"]

router = APIRouter(tags=["身份"])


@router.get("/api/me")
def read_me(
    context: AuthContext = Depends(current_context),
    conn: sqlite3.Connection = Depends(get_conn),
) -> "dict[str, Any]":
    """返回**当前令牌所属账号**的公开视图。

    ★ 身份只来自令牌（没有 ``user_id`` 入参）—— 因此"查到别人"在结构上不可能；
      这也是 F15「以 A 的令牌访问 B 的资源」这条断言面成立的方式：**没有入参可填**。
    ★ 每次**重新读库**而不是信 `AuthContext` 里的快照：`must_change_pw` 与 `status`
      可能在本次令牌签发之后被管理员改过，而"服务端每请求判定"正是那条纪律的要求。
    """
    owner = users.find_by_id(conn, context.user_id)
    if owner is None or owner["status"] == "deleted":
        raise AppError("unauthenticated", "未登录或登录已过期，请重新登录")
    return users.to_public(owner)


@router.get("/api/me/wrong")
def read_wrong(
    kc: "str | None" = Query(default=None, max_length=64),
    limit: int = Query(default=20, ge=1, le=100),
    cursor: "str | None" = Query(default=None, max_length=256),
    context: AuthContext = Depends(password_gate("wrong")),
    conn: sqlite3.Connection = Depends(get_conn),
) -> "dict[str, Any]":
    """错题本：跨会话读回**自己的**错题（F8）—— C6 + C2（`docs/04 §3` 的组合方式）。

    ★ 没有 `user_id` 入参：身份只来自令牌 ⇒「读到别人的错题」在结构上不可能（F15）。
    ★ 返回的是**已提交**的作答记录（复习场景）；`GET /api/problems` 才是「未提交」的暴露面，
      那里一个 `expected` 都不带（`docs/02 §4.3-5`）。
    """
    items, next_cursor = store.wrong_page(
        conn, user_id=context.user_id, kc=kc, limit=limit, cursor=cursor
    )
    return {"items": items, "next_cursor": next_cursor}


@router.get("/api/me/progress")
def read_progress(
    context: AuthContext = Depends(password_gate("progress")),
    conn: sqlite3.Connection = Depends(get_conn),
) -> "dict[str, Any]":
    """进度（F11）：总作答数按 `attempt` 去重 + 逐知识点正确率；`weak` 是显式占位（决策 7A）。"""
    return store.progress(conn, context.user_id)
