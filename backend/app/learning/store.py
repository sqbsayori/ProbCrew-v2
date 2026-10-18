"""学习留痕存储层（P1 只含计数与删除）—— W1 · 首发建表项。

**为什么先有"计数与删除"而没有"写入"**：F16 的验收断言本身是「**删除后逐表读回 0 行**」
（`contracts/user.schema.json#/definitions/deleteResult`），而 N19 的恢复演练也是"逐表读回"。
⇒ 删除面与读回面是**先行可断言**的东西；写面（作答 / 提示 / 掌握度）属 W2 的判定链
（`docs/06 §14.3 卡 W2`）。

**四张 per-user 表**（`docs/02 §3.1`）：``student_qa_log`` · ``student_hint_log`` ·
``student_attempt`` · ``student_mastery``。★ ``student_audit`` **不在此列** ——
它天然跨用户（`actor_id` / `target_id` 都可能指向别人），且已裁定**删除时保留**
（`docs/02 §3.1`；契约里对应 `deleteResult.audit_rows_retained`）。

**键名口径**：对外一律用契约的短键（``qa_log`` / ``hint_log`` / ``attempt`` / ``mastery``），
不把 ``student_`` 前缀漏到响应里 —— `deleteResult.deleted_rows` 的键是这四个。
"""
from __future__ import annotations

import sqlite3

__all__ = ["COUNTER_TABLES", "count_user_rows", "delete_user_data"]

#: 契约 `deleteResult.deleted_rows` 的键（顺序即输出顺序，便于逐条对账）。
COUNTER_TABLES: tuple[str, ...] = ("qa_log", "hint_log", "attempt", "mastery")

_TABLE_BY_KEY: dict[str, str] = {
    "qa_log": "student_qa_log",
    "hint_log": "student_hint_log",
    "attempt": "student_attempt",
    "mastery": "student_mastery",
}


def count_user_rows(conn: sqlite3.Connection, user_id: str) -> "dict[str, int]":
    """逐表读回该用户四张表的行数（F16 / N19 的读回面）。"""
    counted: dict[str, int] = {}
    for key in COUNTER_TABLES:
        row = conn.execute(
            f"SELECT COUNT(*) AS n FROM {_TABLE_BY_KEY[key]} WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        counted[key] = int(row["n"])
    return counted


def delete_user_data(conn: sqlite3.Connection, user_id: str) -> "dict[str, int]":
    """**硬删**该用户四张表的数据（同一事务），并**读回**逐表剩余行数。

    ★ 返回的是**删除之后**重新数的行数，恒应为 0 —— 这正是 F16 的断言对象：
    「删了」这件事必须由**读回的数字**证明，不能由"没报错"或"HTTP 200"证明
    （`docs/02 §3.6`：旧仓的落地 bug 就是写函数漏一次提交、作答被静默回滚）。

    ★ **四表同一事务**（`docs/02 §3.6` 的"复合写同事务"）：否则会出现"问答流水删了、
      作答还在"的半成品状态，而 F16 的读回会**部分通过** —— 部分通过比全失败更难查。
    ★ 账号行**不在这里删**：`student_user` 走**脱敏态**（`status='deleted'`、空哈希、
      令牌全撤），那是 F16 四类处置里 `identity/` 那一半，P3 的 `DELETE /api/me/data` 组合两者。
    """
    for key in COUNTER_TABLES:
        conn.execute(f"DELETE FROM {_TABLE_BY_KEY[key]} WHERE user_id = ?", (user_id,))
    conn.commit()
    return count_user_rows(conn, user_id)
