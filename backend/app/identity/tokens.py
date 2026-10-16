"""会话令牌存储层 —— W1 · 最小身份闭环（docs/02 §4.1 · §5.4）。

**口径 2：库里只存 ``sha256``**（`docs/02 §4.1` 表第 2 行）—— 明文只在登录响应里出现一次。
后果是**库被拿走 ≠ 所有会话被劫持**：拿到的哈希不能当令牌用（服务端只按哈希查行，
但明文无法从哈希反推，攻击者要伪造会话必须找到能过校验的明文）。

**口径 3：7 天有效，可即时撤销**（``revoked``）。``docs/02 §5.4`` 的令牌状态机：
``issued → active → {expired | revoked | orphan}`` —— ``orphan`` 是账号被删除后
被 ``ON DELETE CASCADE`` 清掉的行（v2 的账号是脱敏不物理删，所以那条路只在真正物理删时走）。

★ 本模块**不做**"删除用户 → 令牌行消失"这件事：脱敏态下走的是 :func:`revoke_all_for_user`
  （`revoked=1`，行还在），因为账号行本身还在（`docs/02 §5.3`）。
"""
from __future__ import annotations

import hashlib
import secrets
import sqlite3
import time
from typing import Any

__all__ = [
    "TOKEN_TTL_SECONDS",
    "active_token_count",
    "hash_token",
    "issue_token",
    "resolve_token",
    "revoke_all_for_user",
    "revoke_hash",
    "revoke_token",
]

#: 有效期：7 天（`docs/02 §4.1` 口径 3 首次取值）。
TOKEN_TTL_SECONDS = 7 * 24 * 3600

#: 明文长度：``secrets.token_urlsafe(32)`` ⇒ 43 个字符（契约要求 32–128）。
_TOKEN_BYTES = 32


def hash_token(plaintext: str) -> str:
    """令牌的存储形式：``sha256`` 十六进制。"""
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


def issue_token(
    conn: sqlite3.Connection, user_id: str, now: "float | None" = None
) -> "tuple[str, float]":
    """签发一个会话令牌，返回 ``(明文, 到期时刻)``。**明文不落库**。"""
    stamp = time.time() if now is None else now
    plaintext = secrets.token_urlsafe(_TOKEN_BYTES)
    expires_at = stamp + TOKEN_TTL_SECONDS
    conn.execute(
        "INSERT INTO student_token (token_hash, user_id, issued_at, expires_at, revoked)"
        " VALUES (?, ?, ?, ?, 0)",
        (hash_token(plaintext), user_id, stamp, expires_at),
    )
    conn.commit()
    return plaintext, expires_at


def resolve_token(
    conn: sqlite3.Connection, plaintext: str, now: "float | None" = None
) -> "dict[str, Any] | None":
    """把明文令牌解析成令牌行。**不区分失败原因**（撤销 / 过期 / 无此行 ⇒ 一律 ``None``）。

    ★ 不区分理由是 `docs/02 §6.3`：这三种情形在前端是**同一个动作**（清本地令牌 + 跳登录），
      而给它们编不同的 `code` 只会让"令牌是否曾经存在"变成一个可探测的事实。
    """
    stamp = time.time() if now is None else now
    if not plaintext:
        return None
    row = conn.execute(
        "SELECT * FROM student_token WHERE token_hash = ?", (hash_token(plaintext),)
    ).fetchone()
    if row is None:
        return None
    token = {key: row[key] for key in row.keys()}
    if int(token["revoked"]) != 0:
        return None
    if float(token["expires_at"]) <= stamp:
        return None
    return token


def revoke_hash(conn: sqlite3.Connection, token_hash: str) -> int:
    """按**哈希**撤销（登出用：调用方手里只有上下文里的哈希，没有明文）。"""
    changed = conn.execute(
        "UPDATE student_token SET revoked = 1 WHERE token_hash = ? AND revoked = 0", (token_hash,)
    ).rowcount
    conn.commit()
    return int(changed)


def revoke_token(conn: sqlite3.Connection, plaintext: str) -> int:
    """按**明文**撤销（有明文的场景，如 CLI 或测试）。"""
    return revoke_hash(conn, hash_token(plaintext))


def revoke_all_for_user(conn: sqlite3.Connection, user_id: str) -> int:
    """撤销该用户**全部**未撤销令牌 —— 改密 / 禁用 / 脱敏三处都走它（`docs/02 §4.1` 口径 4）。

    ★ 为什么它是"三处共用"而不是各写一条 SQL：漏掉任何一处都会留下一个仍然有效的旧会话，
      而症状是"密码改了但旧设备还能用"—— 这类洞在测试里只表现为**某个用例忘了断言**。
    """
    revoked = conn.execute(
        "UPDATE student_token SET revoked = 1 WHERE user_id = ? AND revoked = 0", (user_id,)
    ).rowcount
    conn.commit()
    return int(revoked)


def active_token_count(
    conn: sqlite3.Connection, user_id: str, now: "float | None" = None
) -> int:
    """**派生字段**：未撤销且未过期的令牌数（契约 `adminUserRow.active_tokens` 明确"不是列"）。"""
    stamp = time.time() if now is None else now
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM student_token WHERE user_id = ? AND revoked = 0"
        " AND expires_at > ?",
        (user_id, stamp),
    ).fetchone()
    return int(row["n"])
