"""管理端 5 条端点 —— W1 · 教师侧（`docs/02 §6.1` 的管理行 · `docs/04 §3` 的覆盖表）。

| 端点 | 成功 | 审计动作 | 失败 |
|---|---|---|---|
| `GET /api/admin/students` | 200 `pagination` 信封 + `adminStudentRow` | `view_students` | 401 · 403 `forbidden` · 400（limit / cursor） |
| `GET /api/admin/users` | 200 `pagination` 信封 + `adminUserRow` | `view_users` | 同上 |
| `GET /api/admin/audit` | 200 `pagination` 信封 + `auditRow` | `view_audit`（**看审计本身也要留痕**） | 同上 |
| `POST /api/admin/users/import` | 200 `adminImportResult`（逐行拒绝原因） | `import_users` | 400 `invalid_argument`（多传字段 / 超 500 行） |
| `PATCH /api/admin/users/{id}` | 200 `adminPasswordResetResult`（含 `password`）或 `adminUserRow` | `reset_password` / `update_user` | 400 · 404 `not_found` |
| `DELETE /api/admin/users/{id}` | 200 `deleteResult`（**复用** F16 的形状） | `delete_user` | 400（删自己）· 404 |

**为什么两个读端点是两个端点**（`docs/02 §6.1` 的原文）：`students` 是**数据视图**
（作答记录 + 掌握度计数，逐人），`users` 是**账号管理**（角色 / 状态 / 锁定 / 令牌）。
人是同一批、关注点不同 —— 合并会让"只是想看这个学生做了几道题"顺手看到他的令牌数。

**「不产出跨学生派生指标」的落点**：`adminStudentRow` 的每个数字都是**那个人自己的**
（`docs/01 §9` 把跨用户聚合列为排除项）。本文件与页面都**不得**由这些数字再算平均分 /
排名 / 分布 / 热力图 —— 那等于在实现里把排除项加回来。

**F15「接口不可创建管理员」的两处结构**：① `adminImportRequest` 的行形状里**没有** `role`
（`api/schemas.py` 的 `extra="forbid"` 让多传的 `role` 当场 400）；② `PATCH` 的 `role='admin'`
被本文件显式拒绝，并指路 `scripts/manage_users.py`（**唯一的**管理员创建入口）。
"形状挡住了创建"不等于"挡得住提权"，所以两处都要有。

**导入为什么强制下次登录改密**（`must_change_pw=True`）：导入是"管理员替学生设初始密码"，
与重置密码是**同一个信息不对称**（管理员知道密码）。契约只把三件事钉在
`adminPasswordResetResult` 上；导入路径不置位的话，学生可以永远用那个密码 ——
于是"重置密码必须改密"的自环被一条更省事的入口绕过去了（`docs/02 §5.3`）。

★ **本文件不出现一句 SQL**、单函数 ≤ 40 行（`scripts/verify.sh` 的 `api_is_thin()` 硬拦）。
"""
from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import APIRouter, Depends, Path, Query

from app.api.deps import get_conn, require_admin
from app.api.schemas import AdminImportRequest, AdminUserPatch
from app.core.errors import AppError
from app.identity import audit, auth, tokens, users
from app.identity.auth import AuthContext
from app.learning import store

__all__ = ["router"]

router = APIRouter(prefix="/api/admin", tags=["管理"])

#: 账号主键的形状（契约顶层 `user_id` 的 `pattern`）—— 路径参数也按它校验。
_USER_ID_PATTERN = r"^usr_[0-9a-f]{12}$"


def _admin_row(conn: sqlite3.Connection, user: "dict[str, Any]") -> "dict[str, Any]":
    """库行 → `adminUserRow`（`active_tokens` 是**派生字段**，由令牌表计数得出）。"""
    return users.to_admin_row(user, tokens.active_token_count(conn, user["user_id"]))


def _record(
    conn: sqlite3.Connection,
    context: AuthContext,
    action: str,
    *,
    target_id: "str | None" = None,
    detail: "dict[str, Any] | None" = None,
) -> None:
    """写一条管理员审计 —— 五个端点**一律**留痕（`docs/02 §6.1` 的管理行）。"""
    audit.write_audit(
        conn,
        actor_id=context.user_id,
        actor_role=context.role,
        action=action,
        target_id=target_id,
        detail=detail,
    )


def _require_target(conn: sqlite3.Connection, user_id: str) -> "dict[str, Any]":
    """管理端的目标账号：不存在或已脱敏 ⇒ **404 `not_found`**（对外一律"不存在"）。"""
    owner = users.find_by_id(conn, user_id)
    if owner is None or owner["status"] == "deleted":
        raise AppError("not_found", "账号不存在")
    return owner


@router.get("/students")
def list_students(
    user: "str | None" = Query(default=None, max_length=32),
    limit: int = Query(default=20, ge=1, le=50),
    cursor: "str | None" = Query(default=None, max_length=256),
    context: AuthContext = Depends(require_admin),
    conn: sqlite3.Connection = Depends(get_conn),
) -> "dict[str, Any]":
    """数据视图：**逐人**的作答计数 + 掌握度 + 最近 20 条作答（`#/admin?tab=students`）。"""
    rows, next_cursor = users.list_users_page(
        conn,
        role="student",
        exclude_deleted=True,
        user_id=user,
        limit=limit,
        cursor=cursor,
    )
    stats = store.admin_student_stats(conn, [row["user_id"] for row in rows])
    items = [
        {
            "user_id": row["user_id"],
            "username": row["username"],
            "display_name": row["display_name"],
            "created_at": row["created_at"],
            **stats[row["user_id"]],
        }
        for row in rows
    ]
    _record(conn, context, "view_students", target_id=user, detail={"user_id": user})
    return {"items": items, "next_cursor": next_cursor}


@router.get("/users")
def list_users(
    role: "str | None" = Query(default=None, max_length=16),
    status: "str | None" = Query(default=None, max_length=16),
    limit: int = Query(default=20, ge=1, le=50),
    cursor: "str | None" = Query(default=None, max_length=256),
    context: AuthContext = Depends(require_admin),
    conn: sqlite3.Connection = Depends(get_conn),
) -> "dict[str, Any]":
    """账号管理视图：角色 / 状态 / 锁定 / 活跃令牌数（**含已脱敏账号**，便于解释"登不上"）。"""
    rows, next_cursor = users.list_users_page(
        conn, role=role, status=status, limit=limit, cursor=cursor
    )
    _record(conn, context, "view_users", detail={"role": role, "status": status})
    return {"items": [_admin_row(conn, row) for row in rows], "next_cursor": next_cursor}


@router.get("/audit")
def list_audit(
    actor_id: "str | None" = Query(default=None, max_length=64),
    action: "str | None" = Query(default=None, max_length=32),
    limit: int = Query(default=20, ge=1, le=50),
    cursor: "str | None" = Query(default=None, max_length=256),
    context: AuthContext = Depends(require_admin),
    conn: sqlite3.Connection = Depends(get_conn),
) -> "dict[str, Any]":
    """审计列表：`created_at DESC, audit_id DESC`；可按 `actor_id` / `action` 过滤。"""
    if action is not None and action not in audit.AUDIT_ACTIONS:
        raise AppError("invalid_argument", "action 不在契约的 8 个取值内", {"field": "action"})
    items, next_cursor = audit.audit_page(
        conn, limit=limit, cursor=cursor, actor_id=actor_id, action=action
    )
    _record(conn, context, "view_audit", target_id=actor_id, detail={"action": action})
    return {"items": items, "next_cursor": next_cursor}


@router.post("/users/import")
def import_users(
    payload: AdminImportRequest,
    context: AuthContext = Depends(require_admin),
    conn: sqlite3.Connection = Depends(get_conn),
) -> "dict[str, Any]":
    """批量建**学生**账号（`role` 固定 `student`）；逐行拒绝、**不静默跳过**。"""
    created = 0
    rejected: list[dict[str, Any]] = []
    for index, row in enumerate(payload.rows):
        try:
            users.create_user(
                conn,
                username=row.username,
                password=row.password,
                display_name=row.display_name,
                role="student",
                created_by=context.user_id,
                must_change_pw=True,
            )
        except AppError as exc:
            rejected.append({"row": index, "reason": exc.message[:200]})
            continue
        created += 1
    _record(conn, context, "import_users", detail={"created": created, "rejected": len(rejected)})
    return {"created": created, "rejected": rejected, "role": "student"}


@router.patch("/users/{user_id}")
def patch_user(
    payload: AdminUserPatch,
    user_id: str = Path(pattern=_USER_ID_PATTERN, max_length=32),
    context: AuthContext = Depends(require_admin),
    conn: sqlite3.Connection = Depends(get_conn),
) -> "dict[str, Any]":
    """改账号：`password` 在场走**重置密码**分支（响应形状不同），其余字段走 `update_user`。"""
    if payload.is_empty():
        raise AppError("invalid_argument", "请求体至少要有一个要修改的字段")
    if payload.role == "admin":
        raise AppError(
            "invalid_argument",
            "接口不可创建管理员 —— 请用 scripts/manage_users.py（F15）",
            {"field": "role"},
        )
    _require_target(conn, user_id)
    if user_id == context.user_id and (payload.status == "disabled" or payload.role is not None):
        raise AppError("invalid_argument", "不能停用或降级自己的账号", {"field": "user_id"})
    if payload.password is not None:
        revoked = auth.admin_reset_password(conn, user_id, payload.password)
        _record(conn, context, "reset_password", target_id=user_id)
        return {
            "user_id": user_id,
            "must_change_pw": True,
            "tokens_revoked": revoked,
            "audit_written": True,
        }
    detail: dict[str, Any] = {}
    if payload.unlock:
        users.clear_lock(conn, user_id)
        detail["locked_until"] = None
    if payload.display_name is not None or payload.status is not None or payload.role is not None:
        users.update_user_fields(
            conn,
            user_id,
            display_name=payload.display_name,
            status=payload.status,
            role=payload.role,
        )
        for field in ("display_name", "status", "role"):
            if getattr(payload, field) is not None:
                detail[field] = getattr(payload, field)
    owner = _require_target(conn, user_id)
    _record(conn, context, "update_user", target_id=user_id, detail=detail)
    return _admin_row(conn, owner)


@router.delete("/users/{user_id}")
def delete_user(
    user_id: str = Path(pattern=_USER_ID_PATTERN, max_length=32),
    context: AuthContext = Depends(require_admin),
    conn: sqlite3.Connection = Depends(get_conn),
) -> "dict[str, Any]":
    """管理员删除一个账号 —— **与 F16 同一套四类处置**，但审计动作是 `delete_user`。"""
    if user_id == context.user_id:
        raise AppError("invalid_argument", "不能删除自己的账号", {"field": "user_id"})
    _require_target(conn, user_id)
    before = store.count_user_rows(conn, user_id)
    deleted_rows = store.delete_user_data(conn, user_id)
    tokens_revoked = users.anonymize_user(conn, user_id)
    _record(
        conn,
        context,
        "delete_user",
        target_id=user_id,
        detail={"deleted_rows": sum(before.values())},
    )
    return {
        "user_id": user_id,
        "status": "deleted",
        "deleted_rows": deleted_rows,
        "tokens_deleted": tokens_revoked,
        "audit_rows_retained": audit.count_rows_for_user(conn, user_id),
        "logs_in_scope": False,
        "third_party_irrevocable": True,
    }

