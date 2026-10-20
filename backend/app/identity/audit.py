"""审计唯一写入点 —— W1 · 最小身份闭环（docs/02 §3.1 · contracts/user.schema.json#/definitions/auditRow）。

**审计是什么**：谁在何时看了 / 改了谁的数据。`actor_id` 与 `target_id` **天然跨用户**
（都可指向别人）⇒ 它不是 per-user 表，删除用户时**保留**（已裁定，`docs/02 §3.1`）。
保留它的正当性建立在"账号本身是化名（N6）+ 审计只记 ID 与动作（R-O）"上 ——
因此本模块把 R-O 落成**构造约束**：

- ``action`` 必须在契约的 8 个取值内（**不自行扩张枚举**；要加值走 `docs/04 §6` 提案）；
- ``detail`` 的键名命中敏感模式 ⇒ **当场拒绝**（与 `error.detail` **同一个模式、同一份实现**，
  见 `app/core/errors.py` 的 :func:`is_sensitive_key`）；
- ``detail`` 只用于"哪个字段从什么变成什么"，**不放内容**（提问原文 / 学生答案 / 教材片段）。

★ **登录成功不写审计**：契约 `auditRow.action` 的 8 个取值里**没有会话类动作** ——
  不自行加一个 `login` 值来"顺便留个痕"，那是把动作枚举当成日志用的开端。
"""
from __future__ import annotations

import json
import secrets
import sqlite3
import time
from typing import Any

from app.core import paging
from app.core.errors import is_sensitive_key

__all__ = ["AUDIT_ACTIONS", "audit_page", "count_rows", "count_rows_for_user", "write_audit"]

#: 契约 `auditRow.action` 的 8 个取值（**只增不改**）。
AUDIT_ACTIONS = frozenset(
    {
        "view_students",
        "view_users",
        "view_audit",
        "import_users",
        "update_user",
        "reset_password",
        "delete_user",
        "delete_own_data",
    }
)

_ACTOR_ROLES = frozenset({"student", "admin"})
_ID_MAX = 64


def write_audit(
    conn: sqlite3.Connection,
    *,
    actor_id: str,
    actor_role: str,
    action: str,
    target_id: "str | None" = None,
    detail: "dict[str, Any] | None" = None,
    now: "float | None" = None,
) -> str:
    """写一条审计，返回 ``audit_id``。

    ``target_id`` **必填但可空**（契约原文：``null`` = 该动作没有对象）——
    它写成关键字参数带 ``None`` 默认值，是为了让调用方**显式表态**：
      看 / 改**自己**时写 ``actor_id``（契约明令：**不要**另立"null 表示自己"的第二套口径）。
    """
    if action not in AUDIT_ACTIONS:
        raise ValueError(f"未知审计动作 {action!r} —— 取值集合由契约只增不改（docs/04 §6）")
    if actor_role not in _ACTOR_ROLES:
        raise ValueError(f"actor_role 必须是 {sorted(_ACTOR_ROLES)} 之一")
    if not (isinstance(actor_id, str) and 1 <= len(actor_id) <= _ID_MAX):
        raise ValueError("actor_id 必须是 1–64 字的字符串")
    if target_id is not None and not (isinstance(target_id, str) and 1 <= len(target_id) <= _ID_MAX):
        raise ValueError("target_id 必须是 null 或 1–64 字的字符串")
    payload: dict[str, Any] = dict(detail or {})
    for key in payload:
        if is_sensitive_key(key):
            raise ValueError(f"审计 detail 键名 {key!r} 命中敏感模式 —— 凭据不得进审计（R-J / R-M）")

    audit_id = f"aud_{secrets.token_hex(6)}"
    conn.execute(
        "INSERT INTO student_audit (audit_id, actor_id, actor_role, target_id, action, detail,"
        " created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            audit_id,
            actor_id,
            actor_role,
            target_id,
            action,
            json.dumps(payload, ensure_ascii=False, sort_keys=True),
            time.time() if now is None else now,
        ),
    )
    conn.commit()
    return audit_id


def count_rows_for_user(conn: sqlite3.Connection, user_id: str) -> int:
    """该用户**相关**的审计行数（`actor_id` 或 `target_id` 指向他）—— `deleteResult` 的读回面。

    ★ 为什么是 **OR** 而不是两次相加：同一条审计可能既是他做的、对象也是他自己
      （`delete_own_data` 正是这种），相加会把同一行数成两行 —— 于是
      `audit_rows_retained` 会比真实值大，而它唯一的用途就是"读回一个真实的数字"。
    ★ 它是 F16「审计**保留**」这一半的断言：删除之后这个数字**不该变成 0**
      （`docs/02 §3.1` 的裁定 —— 删掉审计等于删掉监督记录）。
    """
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM student_audit WHERE actor_id = ? OR target_id = ?",
        (user_id, user_id),
    ).fetchone()
    return int(row["n"])


def audit_page(
    conn: sqlite3.Connection,
    *,
    limit: int,
    cursor: "str | None",
    actor_id: "str | None" = None,
    action: "str | None" = None,
) -> "tuple[list[dict[str, Any]], str | None]":
    """审计列表一页（`GET /api/admin/audit`）—— 排序 `created_at DESC, audit_id DESC`。

    ★ **看审计本身也要留痕**（`view_audit`）：写入点不在这里，而在 `api/admin.py` ——
      「读函数的副作用」不该藏在存储层（那会让任何一次内部调用都被记成管理员行为）。
    """
    clauses: list[str] = []
    params: list[Any] = []
    if actor_id is not None:
        clauses.append("actor_id = ?")
        params.append(actor_id)
    if action is not None:
        clauses.append("action = ?")
        params.append(action)
    if cursor:
        payload = paging.decode(cursor)
        created, audit_id = payload.get("t"), payload.get("id")
        if not isinstance(created, (int, float)) or not isinstance(audit_id, str):
            raise AppError("invalid_argument", "游标不合法（缺少位置）", {"field": "cursor"})
        clauses.append("(created_at < ? OR (created_at = ? AND audit_id < ?))")
        params.extend([created, created, audit_id])
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(limit + 1)
    rows = conn.execute(
        f"SELECT * FROM student_audit{where} ORDER BY created_at DESC, audit_id DESC LIMIT ?",
        tuple(params),
    ).fetchall()
    has_more = len(rows) > limit
    page = rows[:limit]
    items = [
        {
            "audit_id": row["audit_id"],
            "actor_id": row["actor_id"],
            "actor_role": row["actor_role"],
            "target_id": row["target_id"],
            "action": row["action"],
            "detail": json.loads(row["detail"]) if row["detail"] else {},
            "created_at": row["created_at"],
        }
        for row in page
    ]
    next_cursor = (
        paging.encode({"t": page[-1]["created_at"], "id": page[-1]["audit_id"]})
        if has_more and page
        else None
    )
    return items, next_cursor


def count_rows(
    conn: sqlite3.Connection, *, actor_id: "str | None" = None, target_id: "str | None" = None
) -> int:
    """按 ``actor_id`` / ``target_id`` 计数（F16 的 ``audit_rows_retained`` 读回面）。

    ★ 它是"审计**保留**"这一半的可断言形式：删除用户数据之后，这个数字**不该变成 0**
      （`docs/02 §3.1` 的裁定），而"不该变成 0"必须能用一条断言写出来。
    """
    clauses: list[str] = []
    params: list[str] = []
    if actor_id is not None:
        clauses.append("actor_id = ?")
        params.append(actor_id)
    if target_id is not None:
        clauses.append("target_id = ?")
        params.append(target_id)
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    row = conn.execute(f"SELECT COUNT(*) AS n FROM student_audit{where}", tuple(params)).fetchone()
    return int(row["n"])
