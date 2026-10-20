"""账号存储层 —— W1 · 最小身份闭环（docs/02 §4.1 · §5.3 · contracts/user.schema.json）。

三条纪律在本文件的落点：

- **N6 · 只存哈希**：`student_user.password_hash` 只放 bcrypt 哈希（:func:`hash_password`）；
  **明文密码在本模块里没有任何存放处**。
- **R-A① · 上界按字节**：契约 `loginRequest.password` 的 `maxLength: 72` 是 **bcrypt 的输入上限**，
  按 **UTF-8 字节**计。Pydantic 的 `max_length` 按**字符**计 ⇒ 二者不等价，
  必须在入口另作一次字节校验（:func:`check_password_bytes`），否则「25 个中文字符」
  会绕过 schema 校验、被 bcrypt 静默截断。
- **R-M · 七面不落**：对外一律经 :func:`to_public` 白名单投影（契约 `additionalProperties:false`
  把"多一个字段"本身判成违约）—— `password_hash` / `failed_count` / `locked_until`
  **不在**契约的 `properties` 里，因此它们**不可能**出现在任何响应体里。
  ★ **例外只有一处、且是显式的**：:func:`to_admin_row` 是 `adminUserRow` 的投影 ——
  那个契约**要求** `failed_count` / `locked_until`（管理页面正是要用它判断「这孩子锁住了」），
  但**仍然不含 `password_hash`**：R-M 的「七面不落」管的是凭据，不是锁状态。

★ **管理端的读面**（:func:`list_users_page` · :func:`to_admin_row`）与 :func:`to_public`
  是**两个投影、两个契约**（`docs/04 §3`：`admin/users` 是**账号管理**，`me` 是**账号自陈**）。
  不要把两者合并成"一个函数多返回几个字段"—— 那会让 `GET /api/me` 顺手把锁状态暴露给学生。
"""
from __future__ import annotations

import math
import re
import secrets
import sqlite3
import time
from typing import Any

import bcrypt

from app.core import paging
from app.core.errors import AppError

__all__ = [
    "ADMIN_ROW_KEYS",
    "LOCK_SECONDS",
    "LOCK_THRESHOLD",
    "MAX_PASSWORD_BYTES",
    "USERNAME_PATTERN",
    "anonymize_user",
    "burn_time",
    "check_password_bytes",
    "check_username",
    "clear_lock",
    "create_user",
    "find_by_id",
    "find_by_username",
    "hash_password",
    "list_users_page",
    "new_user_id",
    "record_login_failure",
    "record_login_success",
    "set_password",
    "to_admin_row",
    "to_public",
    "update_user_fields",
    "verify_password",
]

#: 连续失败上限（`docs/02 §4.1` 口径 5：**失败 5 次锁 60 秒**）。
LOCK_THRESHOLD = 5
#: 锁定时长（秒）。
LOCK_SECONDS = 60
#: 密码上界（**字节**，契约原文；bcrypt 的输入上限）。
MAX_PASSWORD_BYTES = 72

#: 登录名的形状（契约顶层 `username` 的 `pattern`：`^[A-Za-z0-9_.-]{3,32}$`）。
USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{3,32}$")

#: 对外可见的字段白名单 = `contracts/user.schema.json` 顶层的 `properties`（10 个键）。
_PUBLIC_KEYS: tuple[str, ...] = (
    "user_id",
    "username",
    "display_name",
    "role",
    "status",
    "created_at",
    "created_by",
    "last_login_at",
    "must_change_pw",
    "notice_ack_at",
)

#: `adminUserRow` 的字段白名单（**含锁状态、不含凭据** —— 见模块说明的例外）。
ADMIN_ROW_KEYS: tuple[str, ...] = (
    "user_id",
    "username",
    "display_name",
    "role",
    "status",
    "created_at",
    "last_login_at",
    "failed_count",
    "locked_until",
    "must_change_pw",
)


#: 固定开销的假校验目标（:func:`burn_time` 用）。★ 它不是任何账号的真实哈希 ——
#: 是一个**每次进程启动随机生成**的哈希，因此无人能用某个已知口令"提前通过"它。
#: 类型是 **bytes**（bcrypt 的 API 收 bytes，不收 str —— 与 :func:`verify_password` 里
#: 那次 `encode("ascii")` 是同一件事）。
_DUMMY_HASH: bytes = bcrypt.hashpw(secrets.token_bytes(32), bcrypt.gensalt())


def new_user_id() -> str:
    """新账号主键：``usr_<12 hex>``（契约 `user_id` 的 pattern）。"""
    return f"usr_{secrets.token_hex(6)}"


def check_password_bytes(plain: str) -> None:
    """密码**按字节**判上界；超限 ⇒ 400 `invalid_argument`（不自造强度阈值，见模块说明）。"""
    if not isinstance(plain, str):
        raise AppError("invalid_argument", "密码必须是字符串")
    if len(plain.encode("utf-8")) > MAX_PASSWORD_BYTES:
        raise AppError(
            "invalid_argument",
            f"密码超过 {MAX_PASSWORD_BYTES} 个字节（中文字符一字三字节），请缩短后重试",
        )


def check_username(username: str) -> None:
    """登录名形状校验（契约顶层 `username` 的 `pattern`）。

    ★ **2026-10-20 加在存储层而不是只加在请求模型上**：建号有三条入口（CLI · 批量导入 ·
      测试夹具），只在 HTTP 模型上加 `pattern` 的话，CLI 与导入就是**绕过口**。
      症状不是报错，而是「库里有一个永远登不上的账号」（前端登录框的 `minLength` 也会拦）。
    """
    if not isinstance(username, str) or not USERNAME_PATTERN.fullmatch(username):
        raise AppError(
            "invalid_argument",
            "用户名只能是 3–32 位的字母 / 数字 / 下划线 / 点 / 连字符",
            {"field": "username"},
        )


def hash_password(plain: str) -> str:
    """bcrypt 哈希（`docs/01` N6：只存哈希）。"""
    check_password_bytes(plain)
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("ascii")


def verify_password(plain: str, stored: str) -> bool:
    """校验口令。``stored`` 为空串或非法格式 ⇒ 一律 **False**（不抛异常）。

    ★ 为什么"非法格式也算 False"：`docs/02 §5.3` 的脱敏态把 `password_hash` 置空串
      —— 那时**任何密码都登不进去**，而这条要靠"校验返回否"实现，不能靠调用方记得特判。
    """
    if not stored:
        return False
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), stored.encode("ascii"))
    except (ValueError, TypeError):
        return False


def burn_time() -> None:
    """烧掉一次与真实校验同阶的时间（`docs/02 §4.1` 的**信息量不变量**）。

    ★ 它防的不是"慢"，是**快**：账号不存在时若立刻返回，响应时间会显著短于"密码错误"，
      于是**无需密码即可枚举账号是否存在**。跑一次对固定 dummy 哈希的 ``checkpw``，
      让两条路径的开销同阶。
    """
    bcrypt.checkpw(b"probcrew-timing-equalizer", _DUMMY_HASH)


def _row_to_dict(row: sqlite3.Row | None) -> "dict[str, Any] | None":
    return None if row is None else {key: row[key] for key in row.keys()}


def find_by_username(conn: sqlite3.Connection, username: str) -> "dict[str, Any] | None":
    """按登录名查（列是 ``COLLATE NOCASE`` ⇒ 大小写不敏感，与契约描述一致）。"""
    return _row_to_dict(
        conn.execute("SELECT * FROM student_user WHERE username = ?", (username,)).fetchone()
    )


def find_by_id(conn: sqlite3.Connection, user_id: str) -> "dict[str, Any] | None":
    """按主键查。"""
    return _row_to_dict(
        conn.execute("SELECT * FROM student_user WHERE user_id = ?", (user_id,)).fetchone()
    )


def create_user(
    conn: sqlite3.Connection,
    *,
    username: str,
    password: str,
    display_name: str = "",
    role: str = "student",
    created_by: "str | None" = None,
    must_change_pw: bool = False,
    now: "float | None" = None,
) -> "dict[str, Any]":
    """建号并返回**公开视图**。

    ★ 重名 ⇒ 409 `conflict`（`docs/02 §6.3`：`conflict` 的触发条件就是"用户名已存在"）。
      靠 **UNIQUE 约束**报出来、而不是先查后写：先查后写在中途没有事务保护时是竞态。
    ★ **接口不可创建管理员**（F15）由**调用方**保证：本函数接受 ``role``，是因为
      CLI（`scripts/manage_users.py`）与**测试夹具**都要建管理员 —— F15 拦的是**接口路径**，
      不是这个存储层函数。HTTP 面的 `api/auth.py` **不接受任何 role 入参**
      （`extra="forbid"` 让多传的字段直接 400）。
    """
    if role not in ("student", "admin"):
        raise AppError("invalid_argument", f"角色 {role!r} 不合法")
    check_username(username)
    created = time.time() if now is None else now
    user_id = new_user_id()
    try:
        conn.execute(
            "INSERT INTO student_user (user_id, username, password_hash, display_name, role,"
            " status, created_at, created_by, failed_count, must_change_pw)"
            " VALUES (?, ?, ?, ?, ?, 'active', ?, ?, 0, ?)",
            (
                user_id,
                username,
                hash_password(password),
                display_name,
                role,
                created,
                created_by,
                1 if must_change_pw else 0,
            ),
        )
        conn.commit()
    except sqlite3.IntegrityError as exc:
        conn.rollback()
        raise AppError("conflict", "用户名已存在，请换一个") from exc
    found = find_by_id(conn, user_id)
    assert found is not None  # 刚写入；这里 assert 是为了让"读回"成为纪律而不是可选
    return to_public(found)


def record_login_success(conn: sqlite3.Connection, user_id: str, now: "float | None" = None) -> None:
    """登录成功：记 ``last_login_at`` 并**清空失败计数与锁定**（`docs/02 §5.3`）。"""
    stamp = time.time() if now is None else now
    conn.execute(
        "UPDATE student_user SET last_login_at = ?, failed_count = 0, locked_until = NULL"
        " WHERE user_id = ?",
        (stamp, user_id),
    )
    conn.commit()


def record_login_failure(
    conn: sqlite3.Connection, user_id: str, now: "float | None" = None
) -> "float | None":
    """登录失败：``failed_count + 1``；**达到上限 ⇒ 置 ``locked_until = now + 60``**。

    返回当前的 ``locked_until``（未锁定为 ``None``），供日志与用例断言。

    ★ **达到上限之后仍继续失败会重新计时**（每次都是"再锁 60 秒"）：`docs/02 §5.3` 只写到
      「``failed_count≥5`` ⇒ ``locked_until`` 置未来时刻（60s 后自动解除）」，没说
      "持续攻击下是否累加"。取"继续锁"是**对攻击者更严、对忘记密码的学生等价**的一侧
      （正确口令在窗口内同样是 423，窗口一过就进得去，见 `§4.1` 的 ③④ 次序）。
    """
    stamp = time.time() if now is None else now
    row = conn.execute(
        "SELECT failed_count FROM student_user WHERE user_id = ?", (user_id,)
    ).fetchone()
    if row is None:
        return None
    failed = int(row["failed_count"]) + 1
    locked_until: "float | None" = None
    if failed >= LOCK_THRESHOLD:
        locked_until = stamp + LOCK_SECONDS
    conn.execute(
        "UPDATE student_user SET failed_count = ?, locked_until = ? WHERE user_id = ?",
        (failed, locked_until, user_id),
    )
    conn.commit()
    return locked_until


def set_password(
    conn: sqlite3.Connection,
    user_id: str,
    new_hash: str,
    *,
    must_change_pw: bool = False,
) -> None:
    """写入新哈希（已哈希的字符串）并设置/清除 ``must_change_pw``。

    ★ **撤销令牌不在这里**：它属于"改密的副作用"，由 `identity/auth.py` 的
      :func:`app.identity.auth.change_password` 组合 —— 单看本函数无法保证"改完密码旧令牌失效"，
      而那条（`docs/02 §4.1` 口径 4）恰恰是最容易漏、后果最重的一条。
    """
    conn.execute(
        "UPDATE student_user SET password_hash = ?, must_change_pw = ? WHERE user_id = ?",
        (new_hash, 1 if must_change_pw else 0, user_id),
    )
    conn.commit()


def anonymize_user(conn: sqlite3.Connection, user_id: str, now: "float | None" = None) -> int:
    """F16 的**账号侧脱敏**（`docs/02 §5.3`）：``status='deleted'`` · 空哈希 · 令牌全撤。

    返回被撤销的令牌数。★ 返回这个数字不是装饰：契约 `deleteResult.tokens_deleted` 是必填，
      而"级联删除了几行"必须由**读回**给出，不能由"应该删了吧"给出。

    ★ **原 username 可以再次被注册**（契约的复用规则）：改名成 ``deleted_<8hex>`` 之后
      原名字就不在占用状态 —— 本函数**不写"退休用户名表"**，那是契约明确不引入的东西。
    ★ ``now`` 参数保留而**未被使用**：`student_user` 在 v2 **没有** ``updated_at`` 列
      （`docs/02 §3.1` 的字段清单里没有），因此不新造列、也不假装记了一个时间。
      它留在签名里是为了让调用点（P3 的 `DELETE /api/me/data`）**不必为将来改签名**。
    """
    conn.execute(
        "UPDATE student_user SET username = ?, display_name = '', password_hash = '',"
        " status = 'deleted', must_change_pw = 0, notice_ack_at = NULL WHERE user_id = ?",
        (f"deleted_{secrets.token_hex(4)}", user_id),
    )
    revoked = conn.execute(
        "UPDATE student_token SET revoked = 1 WHERE user_id = ? AND revoked = 0", (user_id,)
    ).rowcount
    conn.commit()
    return int(revoked)


def lock_remaining(user: "dict[str, Any]", now: float) -> float:
    """锁定剩余秒数（>0 表示仍在锁定期）。API 层据此给 message 里的剩余秒数。"""
    locked_until = user.get("locked_until")
    if not locked_until:
        return 0.0
    return max(0.0, float(locked_until) - now)


def locked_message(remaining: float) -> str:
    """423 的 message：把剩余时间**如实**说出来（`docs/02 §6.3` 前端显示剩余秒数）。"""
    return f"连续失败次数过多，账号已被临时锁定，请在约 {int(math.ceil(remaining))} 秒后重试"


def to_admin_row(user: "dict[str, Any]", active_tokens: int = 0) -> "dict[str, Any]":
    """把库行投影成 `contracts/user.schema.json#/definitions/adminUserRow`。

    ★ **与 :func:`to_public` 是两个投影**：管理页面要 `failed_count` / `locked_until` /
      `active_tokens`（判断「锁住了」「手机丢了」），学生自陈面**一个都不给**。
      两者共用同一份白名单写法，**都不含 `password_hash`** —— 凭据只在 :func:`hash_password`
      与 :func:`verify_password` 之间流动。
    """
    row: dict[str, Any] = {}
    for key in ADMIN_ROW_KEYS:
        value = user.get(key)
        if key == "must_change_pw":
            row[key] = bool(value)
        elif key == "failed_count":
            row[key] = int(value or 0)
        elif key in ("last_login_at", "locked_until"):
            row[key] = None if value is None else float(value)
        else:
            row[key] = value
    row["active_tokens"] = int(active_tokens)
    return row


def list_users_page(
    conn: sqlite3.Connection,
    *,
    role: "str | None" = None,
    status: "str | None" = None,
    user_id: "str | None" = None,
    exclude_deleted: bool = False,
    limit: int,
    cursor: "str | None",
) -> "tuple[list[dict[str, Any]], str | None]":
    """账号列表一页（`GET /api/admin/users` / `GET /api/admin/students` 的读面）。

    排序 **`created_at DESC, user_id DESC`**（契约 `pagination` 的 `x-sort-order`）——
    ★ 只按 `created_at` 时同秒创建的账号顺序不确定，翻页会重复或漏项（批量导入正是一次写入多行）。

    ★ 游标只带 `(created_at, user_id)`，**不带筛选条件**：筛选条件由调用方重新传入
      （URL 里的 `?role=` 本来就在下一次请求里）—— 把筛选塞进游标会让「改了筛选但沿用旧游标」
      变成一次静默的跨筛选翻页。
    """
    clauses: list[str] = []
    params: list[Any] = []
    if role is not None:
        clauses.append("role = ?")
        params.append(role)
    if status is not None:
        clauses.append("status = ?")
        params.append(status)
    if user_id is not None:
        clauses.append("user_id = ?")
        params.append(user_id)
    if exclude_deleted:
        clauses.append("status <> 'deleted'")
    if cursor:
        payload = paging.decode(cursor)
        created, user_id = payload.get("t"), payload.get("id")
        if not isinstance(created, (int, float)) or not isinstance(user_id, str):
            raise AppError("invalid_argument", "游标不合法（缺少位置）", {"field": "cursor"})
        clauses.append("(created_at < ? OR (created_at = ? AND user_id < ?))")
        params.extend([created, created, user_id])
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(limit + 1)
    rows = conn.execute(
        f"SELECT * FROM student_user{where} ORDER BY created_at DESC, user_id DESC LIMIT ?",
        tuple(params),
    ).fetchall()
    has_more = len(rows) > limit
    page = rows[:limit]
    next_cursor = (
        paging.encode({"t": page[-1]["created_at"], "id": page[-1]["user_id"]})
        if has_more and page
        else None
    )
    return [{key: row[key] for key in row.keys()} for row in page], next_cursor


def update_user_fields(
    conn: sqlite3.Connection,
    user_id: str,
    *,
    display_name: "str | None" = None,
    status: "str | None" = None,
    role: "str | None" = None,
) -> "dict[str, Any]":
    """改账号的可管理字段（`PATCH /api/admin/users/{id}` 的非密码分支），返回**更新后的库行**。

    ★ **`status='deleted'` 不从这里走**：删除有它自己的四类处置（数据硬删 + 脱敏 + 令牌全撤），
      把它降级成「把 status 改成 deleted」会造出一个「看起来删了、数据还在」的账号 ——
      正是 F16 要堵的形态。删除请走 :func:`anonymize_user` 与 `learning.store.delete_user_data`。
    ★ `role` 在这里**不限制取值**（存储层不判断「接口能不能提权」）；F15 的「接口不可创建管理员」
      由 `api/admin.py` 拒绝 `role='admin'` —— 与 `create_user` 同一条分工。
    """
    assignments: list[str] = []
    params: list[Any] = []
    if display_name is not None:
        assignments.append("display_name = ?")
        params.append(display_name)
    if status is not None:
        if status == "deleted":
            raise AppError("invalid_argument", "删除账号请使用删除接口", {"field": "status"})
        if status not in ("active", "disabled"):
            raise AppError("invalid_argument", f"状态 {status!r} 不合法", {"field": "status"})
        assignments.append("status = ?")
        params.append(status)
    if role is not None:
        if role not in ("student", "admin"):
            raise AppError("invalid_argument", f"角色 {role!r} 不合法", {"field": "role"})
        assignments.append("role = ?")
        params.append(role)
    if not assignments:
        raise AppError("invalid_argument", "没有要修改的字段")
    params.append(user_id)
    conn.execute(f"UPDATE student_user SET {', '.join(assignments)} WHERE user_id = ?", tuple(params))
    conn.commit()
    updated = find_by_id(conn, user_id)
    if updated is None:
        raise AppError("not_found", "账号不存在")
    return updated


def clear_lock(conn: sqlite3.Connection, user_id: str) -> bool:
    """解锁（`failed_count=0` · `locked_until=NULL`），返回是否真的改动了行。

    ★ 管理员「解锁」在 `adminUserRow` 里有字段（`failed_count` / `locked_until`）却没有动作 ——
      `auditRow.action` 的 8 个取值里也没有 `unlock`。因此它**并入** `update_user` 这一动作的
      `detail`（`{"field": "locked_until", "to": null}`），不新增枚举值（`docs/04 §6`）。
    """
    changed = conn.execute(
        "UPDATE student_user SET failed_count = 0, locked_until = NULL WHERE user_id = ?",
        (user_id,),
    ).rowcount
    conn.commit()
    return bool(changed)


def to_public(user: "dict[str, Any]") -> "dict[str, Any]":
    """把库行投影成契约的**账号对外视图**（白名单，不是黑名单）。

    ★ **为什么必须是白名单**：契约 `additionalProperties:false` 决定了"多一个字段"就是违约。
      黑名单写法（"除 password_hash 外都返回"）会在**下一次加列时**自动把新列暴露出去，
      而那时的 review 看到的是"改了一行 SQL"，不是"改了一个对外形状"。
    """
    public: dict[str, Any] = {}
    for key in _PUBLIC_KEYS:
        value = user.get(key)
        if key == "must_change_pw":
            public[key] = bool(value)
        elif key == "created_by" and value is None:
            continue  # 契约里 created_by 是 string（无 null 分支）⇒ 无值就不出现这个键
        else:
            public[key] = value
    return public
