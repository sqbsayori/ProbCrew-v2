"""身份端点 —— W1 · 最小身份闭环（`docs/02 §6.1` 的身份行 · §4.1 · §5.3）。

三个端点，全部薄到"翻译一层"：

| 端点 | 认证 | 成功 | 失败（`docs/02 §6.3`） |
|---|---|---|---|
| `POST /api/auth/login` | 否 | 200 `session{token, expires_at, user}` | 400 `invalid_argument`（字段超长 / 多传字段 / 密码超 72 字节）· 401 `bad_credentials`（不存在 / 已脱敏 / 空哈希 / 口令错，**同一个 code**）· 403 `account_disabled` · 423 `locked`（后两者**只在口令验证通过之后**） |
| `POST /api/auth/logout` | 是 | 204 | 401 `unauthenticated` |
| `POST /api/auth/password` | 是 | 204（成功 ⇒ 撤销该用户**全部**令牌） | 401 `bad_credentials`（旧口令错）· 401 `unauthenticated` · 403 `must_change_pw` 不适用（它就在放行集合里） |

★ **本文件一行 SQL 都没有**（查询全在 `identity/`）：`scripts/verify.sh` 的 `api_is_thin()`
  是**纯字符串匹配**，连注释里出现那几个字面量都会被判失败 —— 这不是刁难，它保证"薄"不是
  口头承诺。同理，本文件的每个函数都在 40 行以内。

★ **login 没有响应模型类**：成功响应是契约 `definitions.session` 的形状，而它由
  `identity/users.py` 的 :func:`to_public` 白名单投影产出 —— 再套一层 Pydantic 模型
  就等于**第二处**形状定义，两处迟早不一致。
"""
from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import APIRouter, Depends, Response

from app.api.deps import current_context, get_conn
from app.api.schemas import LoginRequest, PasswordChangeRequest
from app.identity import tokens
from app.identity.auth import AuthContext, authenticate, change_password

__all__ = ["router"]

router = APIRouter(prefix="/api/auth", tags=["身份"])


@router.post("/login")
def login(
    payload: LoginRequest,
    conn: sqlite3.Connection = Depends(get_conn),
) -> "dict[str, Any]":
    """登录：成功返回契约 `session`（**明文令牌只在这里出现一次**，库里只存它的 sha256）。"""
    session = authenticate(conn, payload.username, payload.password)
    return {"token": session.token, "expires_at": session.expires_at, "user": session.user}


@router.post("/logout", status_code=204, response_class=Response)
def logout(
    context: AuthContext = Depends(current_context),
    conn: sqlite3.Connection = Depends(get_conn),
) -> Response:
    """登出：撤销**本次请求用的那一个**令牌（其余会话不受影响 —— 换台设备登出不该影响这台）。"""
    tokens.revoke_hash(conn, context.token_hash)
    return Response(status_code=204)


@router.post("/password", status_code=204, response_class=Response)
def change_own_password(
    payload: PasswordChangeRequest,
    context: AuthContext = Depends(current_context),
    conn: sqlite3.Connection = Depends(get_conn),
) -> Response:
    """改密：成功后**该用户全部令牌作废**（含本次）⇒ 前端必须重新登录（契约原文如此）。"""
    change_password(conn, context, payload.old_password, payload.new_password)
    return Response(status_code=204)
