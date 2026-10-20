"""鉴权内核（**纯函数**，不碰 FastAPI）—— W1 · 最小身份闭环（docs/02 §4.1 · §5.3）。

★ **为什么这里没有 ``Depends`` / ``Header``**：`docs/03 §3.1` 对 `identity/` 的**明确禁止**里
含 ``fastapi``，且 `scripts/verify.sh` 的 `import_direction()` 用白名单硬拦。⇒ 请求头解析与
依赖注入留在 `app/api/deps.py`，本模块只暴露「**明文令牌字符串 → AuthContext**」这样的纯函数。
代价为零、收益是真：**鉴权链可以不必起 HTTP 就被测**。

**登录次序（`docs/02 §4.1`，顺序不可调）**：查 user → ① 不存在 / 已删除 / 空哈希 ⇒ 烧时间 + 401
→ ② ``bcrypt.checkpw``（失败也烧时间）→ 失败计数 +1，达 5 次置 ``locked_until``
→ ③ **锁定中？423** → ④ **``status=disabled``？403** → ⑤ 发令牌。

★ **③④ 必须在密码验证之后**，这不是风格问题：把"锁定中 → 423"放到验证之前，
  "不存在的用户名"给 401 而"已锁定账号"给 423 ⇒ **无需密码即可枚举账号是否存在**
  （`docs/02 §4.1` 的反例原文）。信息量不变量是这两个 `code` 拆分成立的前提，
  也是它唯一的正当性依据。
"""
from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from typing import Any

from app.core.errors import AppError
from app.identity import tokens, users

__all__ = [
    "PASSWORD_CHANGE_ALLOWLIST",
    "AuthContext",
    "Session",
    "admin_reset_password",
    "assert_password_changed",
    "authenticate",
    "change_password",
    "resolve_bearer",
]

#: ``must_change_pw`` 置位时**仍然放行**的接口区（`docs/02 §5.3` 只说"只放行
#: ``POST /api/auth/password``"，但 ``GET /api/me`` 被拦则前端拿不到"要改密"这个事实，
#: 而 ``logout`` 被拦会让用户被困在改密页里 —— 四个取值为本仓首次写死，见待写死清单）。
PASSWORD_CHANGE_ALLOWLIST = frozenset({"login", "logout", "password", "me"})

_BEARER_PREFIX = "bearer "


@dataclass(frozen=True)
class AuthContext:
    """一次请求的身份上下文（由令牌解析而来，**不含任何凭据明文**）。"""

    user_id: str
    role: str
    status: str
    must_change_pw: bool
    token_hash: str
    expires_at: float


@dataclass(frozen=True)
class Session:
    """登录成功的会话：明文令牌 + 到期时刻 + **公开视图**的用户。"""

    token: str
    expires_at: float
    user: "dict[str, Any]"


def authenticate(
    conn: sqlite3.Connection,
    username: str,
    password: str,
    now: "float | None" = None,
) -> Session:
    """按 `docs/02 §4.1` 的次序完成登录，返回 :class:`Session`；失败抛 `AppError`。"""
    stamp = time.time() if now is None else now
    users.check_password_bytes(password)

    found = users.find_by_username(conn, username)
    # ① 不存在 / 已删除 / 空哈希 —— 三种情形**同一个**结果，且**烧等长的时间**。
    if found is None or found["status"] == "deleted" or not found["password_hash"]:
        users.burn_time()
        raise AppError("bad_credentials", "用户名或密码不正确")

    # ② 校验口令 —— **bcrypt 调用本身就是那段时间**，不在此处再补一次 :func:`users.burn_time`：
    #    补了会让"口令错"耗时约等于"账号不存在"的两倍，而这两条路径**必须同阶**
    #    （精确地说是**相等**：两边各跑一次同 cost 的 checkpw）。补的那一次是常见误写，
    #    它防的"快路径"只存在于"账号不存在"这一侧，而那侧已经单独烧过了。
    if not users.verify_password(password, found["password_hash"]):
        users.record_login_failure(conn, found["user_id"], stamp)
        raise AppError("bad_credentials", "用户名或密码不正确")

    # ③ 锁定中 ⇒ 423（**只在这里**，即已经知道对方口令正确之后）。
    remaining = users.lock_remaining(found, stamp)
    if remaining > 0:
        raise AppError("locked", users.locked_message(remaining))

    # ④ 已禁用 ⇒ 403（同样在验证之后）。
    if found["status"] == "disabled":
        raise AppError("account_disabled", "账号已停用，请联系老师")

    # ⑤ 发令牌。
    users.record_login_success(conn, found["user_id"], stamp)
    plaintext, expires_at = tokens.issue_token(conn, found["user_id"], stamp)
    refreshed = users.find_by_id(conn, found["user_id"]) or found
    return Session(token=plaintext, expires_at=expires_at, user=users.to_public(refreshed))


def admin_reset_password(
    conn: sqlite3.Connection,
    user_id: str,
    new_password: str,
    *,
    must_change_pw: bool = True,
) -> int:
    """管理员重置密码：写新哈希 → 置 ``must_change_pw`` → **撤销该用户全部令牌**。

    返回被撤销的令牌数（契约 `adminPasswordResetResult.tokens_revoked` 是必填 ——
    「撤销了几条」必须由**读回**给出；`tokens.revoke_all_for_user()` 的 ``rowcount`` 就是它）。

    ★ **三件事是一个动作**（契约 `adminPasswordResetResult` 把三件全列为必填且取值固定）：
      只改哈希 = 管理员**知道**学生的新密码（旧会话还能用、学生也不会去改）——
      那条"重置密码等于降级安全性"的洞正是这样形成的（`docs/02 §5.3` 的自环）。
    ★ 目标不存在 / 已脱敏 ⇒ 404 `not_found`：已删除的账号**不接受**重置（否则它会被"复活"成一个
      密码已知的不可登录账号，而 `status='deleted'` 的语义是终点）。
    """
    users.check_password_bytes(new_password)
    owner = users.find_by_id(conn, user_id)
    if owner is None or owner["status"] == "deleted":
        raise AppError("not_found", "账号不存在")
    users.set_password(conn, user_id, users.hash_password(new_password), must_change_pw=must_change_pw)
    return tokens.revoke_all_for_user(conn, user_id)


def resolve_bearer(
    conn: sqlite3.Connection,
    header_value: "str | None",
    now: "float | None" = None,
) -> "AuthContext | None":
    """``Authorization: Bearer <明文>`` → :class:`AuthContext`；无效一律 ``None``。

    ★ **账号被禁用时抛 403 而不是返回 None**（`docs/02 §4.1` 图末行："revoked/过期/账号禁用
      ⇒ 401/403"）：禁用是**账号级**事实，与"这个令牌还有没有效"不同 —— 若也归 401，
      前端会"清令牌 + 跳登录"，用户就会反复登录同一个被停用的账号。
    ★ 已删除的账号返回 ``None``：脱敏后 ``password_hash`` 为空，且令牌已被 :func:`revoke_all_for_user`
      撤销，两条路都到不了这里；留这个判断是为了让"删除后拿着旧令牌"**必然**是 401。
    """
    stamp = time.time() if now is None else now
    if not header_value or not header_value.strip().lower().startswith(_BEARER_PREFIX):
        return None
    plaintext = header_value.strip()[len(_BEARER_PREFIX):].strip()
    token = tokens.resolve_token(conn, plaintext, stamp)
    if token is None:
        return None
    owner = users.find_by_id(conn, token["user_id"])
    if owner is None or owner["status"] == "deleted":
        return None
    if owner["status"] == "disabled":
        raise AppError("account_disabled", "账号已停用，请联系老师")
    return AuthContext(
        user_id=owner["user_id"],
        role=owner["role"],
        status=owner["status"],
        must_change_pw=bool(owner["must_change_pw"]),
        token_hash=token["token_hash"],
        expires_at=float(token["expires_at"]),
    )


def assert_password_changed(ctx: AuthContext, area: str) -> None:
    """``must_change_pw`` 闸门（`docs/02 §5.3`）：非放行区一律 **403 ``must_change_pw``**。

    ★ **不复用 ``forbidden``**：那个 code 的语义是"角色不足"，混用会让前端把"去改密码"
      显示成"无权访问"（契约 `must_change_pw` 的原文）。
    ★ 它必须由**服务端在每个请求上**判定 —— 只在登录响应里回一次的话，
      直接调任意学习类接口就绕过去了，于是管理员**始终知道学生的密码**。
    """
    if ctx.must_change_pw and area not in PASSWORD_CHANGE_ALLOWLIST:
        raise AppError("must_change_pw", "请先设置新密码，再继续使用")


def change_password(
    conn: sqlite3.Connection,
    ctx: AuthContext,
    old_password: str,
    new_password: str,
    now: "float | None" = None,
) -> int:
    """改密：校验旧口令 → 写新哈希 → 清 ``must_change_pw`` → **撤销该用户全部令牌**。

    返回被撤销的令牌数（含**本次请求用的那一个** —— 契约原文："改完密码旧令牌还能用，
    等于没改"，因此当前令牌也必须失效，前端必须重新登录）。

    ★ 旧口令错误按**登录同源**处理：401 ``bad_credentials`` **并且计入失败计数**
      （与 `docs/02 §4.1` 口径 5 同一条理由 —— 否则改密接口就成了一个不计次数的爆破面）。
    """
    stamp = time.time() if now is None else now
    users.check_password_bytes(new_password)
    owner = users.find_by_id(conn, ctx.user_id)
    if owner is None or owner["status"] == "deleted":
        raise AppError("unauthenticated", "未登录或登录已过期，请重新登录")
    if not users.verify_password(old_password, owner["password_hash"]):
        users.record_login_failure(conn, ctx.user_id, stamp)
        raise AppError("bad_credentials", "用户名或密码不正确")
    users.set_password(conn, ctx.user_id, users.hash_password(new_password), must_change_pw=False)
    return tokens.revoke_all_for_user(conn, ctx.user_id)
