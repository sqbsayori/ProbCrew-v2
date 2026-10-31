"""`/api/me` —— W1 · 最小身份闭环（`docs/02 §6.1` 的身份行）。

| 端点 | 认证 | 成功 | 失败 |
|---|---|---|---|
| `GET /api/me` | 是 | 200 账号对象（`contracts/user.schema.json` 的**顶层**） | 401 `unauthenticated` |
| `GET /api/me/wrong` | 是 | 200 `pagination` 信封 + `attempt` 视图（C6 + C2） | 400 `invalid_argument`（limit / cursor） |
| `GET /api/me/progress` | 是 | 200 `mastery` 响应（C8：总作答数 + 逐知识点正确率 + 薄弱占位） | 401 `unauthenticated` |
| `DELETE /api/me/data` | 是 | 200 `deleteResult`（F16 的**四类处置 + 两条边界**，逐项读回） | 401 `unauthenticated` · 403 `must_change_pw` |

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

## `DELETE /api/me/data` —— F16 的四类处置（2026-10-20 接通）

次序不是任意的（`docs/02 §3.1` · §5.3）：① 先**读**四张表的行数（审计里的「删了几行」，
必须在删之前取）→ ② 四张 per-user 表**硬删**并**重新数**（契约的 `deleted_rows` 是
**删除之后**的数，恒应为 0 —— 这就是 F16 的验收断言，不是"没报错"）→ ③ 账号**脱敏**
（`status='deleted'` · 空哈希 · 令牌全撤）并返回被撤销的令牌数 → ④ 写一条
`delete_own_data` 审计（`actor_role` 可以是 `student`，`target_id` 是自己），再**读回**
保留的审计行数（审计**保留**是已裁定的 —— 删掉它等于删掉监督记录）。

★ **为什么先删数据、后写审计**：反过来会留下一条"声称删了、其实没删"的审计（更坏的谎）。
  中途失败的真实形态是「数据已删、账号还活着」—— 用户可以重跑一次（幂等），
  而 `tokens_deleted` 会是 0（真话：这次没有令牌可撤）。
★ **`must_change_pw` 也拦这个端点**（`password_gate("data")`，不在放行集合里）：
  临时密码下的会话不做任何持久化动作；改密只需一次，删除权不会因此少一分。
★ **不含管理员删除他人**：那是 `DELETE /api/admin/users/{id}`（`actor_role=admin` ·
  `action=delete_user`）—— 两个动作在审计里**必须可区分**（契约 `auditRow.action`）。
★ **两条边界恒为常量**（契约把它们写成 `const`）：`logs_in_scope=false`（日志按 R-O
  只记 ID 与动作，内容本来就没落盘）· `third_party_irrevocable=true`（第三方模型服务
  已经收到的请求删不掉）。`const` 是为了逼前端与告知文案**同屏声明**它们。
"""
from __future__ import annotations

import sqlite3
import time
from typing import Any

from fastapi import APIRouter, Depends, Query

from app.api.deps import current_context, get_conn, password_gate
from app.api.schemas import NoticeAckRequest
from app.core.errors import AppError
from app.identity import audit, users
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


@router.get("/api/me/qa")
def read_qa_history(
    limit: int = Query(default=20, ge=1, le=100),
    cursor: "str | None" = Query(default=None, max_length=256),
    context: AuthContext = Depends(password_gate("qa")),
    conn: sqlite3.Connection = Depends(get_conn),
) -> "dict[str, Any]":
    """我的问答历史（C2 + C9）：身份只来自令牌，不能读别人的流水。"""
    items, next_cursor = store.qa_page(
        conn, user_id=context.user_id, limit=limit, cursor=cursor
    )
    return {"items": items, "next_cursor": next_cursor}


@router.get("/api/me/notice")
def read_notice(
    context: AuthContext = Depends(current_context),
    conn: sqlite3.Connection = Depends(get_conn),
) -> "dict[str, Any]":
    """N15 的读取口：判据是**最近一次实际去向**（`docs/02 §1.5①`），不是部署形态。"""
    return _notice_status(conn, context.user_id)


@router.post("/api/me/notice")
def ack_notice(
    payload: NoticeAckRequest,
    context: AuthContext = Depends(current_context),
    conn: sqlite3.Connection = Depends(get_conn),
) -> "dict[str, Any]":
    """确认告知（只能置位；重复确认返回原时刻）。"""
    store.mark_notice_ack(conn, context.user_id, time.time())
    return _notice_status(conn, context.user_id)


_PAYLOAD_FIELDS = ["query", "context", "system_prompt", "corpus_excerpt"]
_DELETE_BOUNDARY = "删除权边界：可删除本账号的问答记录与作答数据，但已发送给第三方的请求无法撤回。"


def _notice_status(conn: sqlite3.Connection, user_id: str) -> "dict[str, Any]":
    latest = store.latest_qa(conn, user_id)
    ack_at = store.notice_ack_at(conn, user_id)
    if latest is None:
        egress = provider = model = None
        message = "当前还没有问答记录，未发生问答外传。" + _DELETE_BOUNDARY
    else:
        egress = latest["egress"]
        provider = latest["provider"]
        model = latest["model"]
        if egress == "remote":
            message = (
                f"提问会发送到第三方模型服务（{provider} / {model}）；"
                "发送范围：提问原文、上下文片段、系统提示、教材片段。" + _DELETE_BOUNDARY
            )
        else:
            message = (
                "当前使用本机模型端点，提问不会发送到第三方；"
                "发送范围：提问原文、上下文片段、系统提示、教材片段。" + _DELETE_BOUNDARY
            )
    return {
        "ask": ack_at is None,
        "egress": egress,
        "provider": provider,
        "model": model,
        "payload_fields": list(_PAYLOAD_FIELDS),
        "message": message[:512],
        "ack_at": ack_at,
    }


@router.delete("/api/me/data")
def delete_own_data(
    context: AuthContext = Depends(password_gate("data")),
    conn: sqlite3.Connection = Depends(get_conn),
) -> "dict[str, Any]":
    """删除**自己的**全部学习数据（F16）—— 四类处置逐项读回（契约 `deleteResult`）。

    见模块说明的次序与理由；本函数只做"读 → 删 → 脱敏 → 记账"四步，判定全在下层。
    """
    before = store.count_user_rows(conn, context.user_id)
    deleted_rows = store.delete_user_data(conn, context.user_id)
    if any(count != 0 for count in deleted_rows.values()):
        raise AppError("internal_error", "删除后仍有残留行，已中止（不返回成功回执）")
    tokens_revoked = users.anonymize_user(conn, context.user_id)
    audit.write_audit(
        conn,
        actor_id=context.user_id,
        actor_role=context.role,
        action="delete_own_data",
        target_id=context.user_id,
        detail={"deleted_rows": sum(before.values())},
    )
    return {
        "user_id": context.user_id,
        "status": "deleted",
        "deleted_rows": deleted_rows,
        "tokens_deleted": tokens_revoked,
        "audit_rows_retained": audit.count_rows_for_user(conn, context.user_id),
        "logs_in_scope": False,
        "third_party_irrevocable": True,
    }
