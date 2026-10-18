"""`core/db.py` 的用例 —— W1 · 首发建表项（P1）。

归属：`docs/05 §3`「其余测试文件按**被测模块**归属，**文件名与被测模块同名**」
（`test_db.py` ↔ `app/core/db.py`）⇒ 本文件归**后端与数据**（域2）。

★ 每条写路径都**读回断言**（`docs/02 §3.6`：测试必须读回，不得只断言"没报错"）。
"""
from __future__ import annotations

import sqlite3

import pytest

from app.core import db
from app.learning import store


@pytest.fixture()
def conn(tmp_path):
    """一个**独立的库文件**（tmp_path）——用例之间不许共享状态，也不许碰 `backend/data/`。"""
    connection = db.initialize(tmp_path / "probe.sqlite")
    yield connection
    connection.close()


def _indexes(connection: sqlite3.Connection, table: str) -> set[str]:
    return {row["name"] for row in connection.execute(f"PRAGMA index_list({table})")}


def _insert_user(connection: sqlite3.Connection, user_id: str = "usr_000000000001") -> str:
    connection.execute(
        "INSERT INTO student_user (user_id, username, password_hash, role, status, created_at)"
        " VALUES (?, ?, ?, 'student', 'active', 1.0)",
        (user_id, user_id[-6:], "x"),
    )
    connection.commit()
    return user_id


# ---- ① 七张表齐 -------------------------------------------------------------


def test_seven_tables_exist(conn):
    """七张 ``student_*`` 表齐 —— **逐条**断言，不是数个数（数个数会在"多一张少一张"时照样通过）。"""
    found = {
        row["name"]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    for name in db.TABLE_NAMES:
        assert name in found, f"缺少表 {name}（docs/02 §3.1 的七张）"
    assert len(db.TABLE_NAMES) == 7


# ---- ② 九处索引 + 一处唯一约束 ----------------------------------------------


def test_indexes_and_unique_constraint(conn):
    """索引齐（`docs/02 §3.5`）· 唯一约束**是约束**（不是普通索引）。"""
    assert {"ix_user_role_status"} <= _indexes(conn, "student_user")
    assert {"ix_token_user_revoked"} <= _indexes(conn, "student_token")
    assert {"ix_audit_actor", "ix_audit_target"} <= _indexes(conn, "student_audit")
    assert {"ix_qa_user_created"} <= _indexes(conn, "student_qa_log")
    assert {"ix_hint_user_item_session", "ix_hint_user_item"} <= _indexes(conn, "student_hint_log")
    assert {"ix_attempt_user_created", "ix_attempt_user_item"} <= _indexes(conn, "student_attempt")

    unique = [
        row for row in conn.execute("PRAGMA index_list(student_attempt)") if row["unique"] == 1
    ]
    assert unique, "student_attempt 没有唯一索引 —— §3.5 的 UNIQUE(user_id,item_id,attempt_no) 缺失"
    columns = [
        row["name"]
        for row in conn.execute(f"PRAGMA index_info({unique[0]['name']})")
    ]
    assert columns == ["user_id", "item_id", "attempt_no"]


# ---- ③ 幂等 ------------------------------------------------------------------


def test_ensure_schema_is_idempotent(conn, tmp_path):
    """连跑两次不抛错、表数不变（`docs/02 §3.6`："只有一个建表入口，幂等"）。"""
    before = {
        row["name"]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    db.ensure_schema(conn)
    db.ensure_schema(conn)
    after = {
        row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    assert before == after

    # 第二次用**另一个连接**跑（模拟第二个进程）—— 幂等必须是库层面的事实，不是进程内的巧合。
    again = db.initialize(tmp_path / "probe.sqlite")
    try:
        same = {
            row["name"]
            for row in again.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        assert same == before
    finally:
        again.close()


# ---- ④ WAL 与 busy_timeout ---------------------------------------------------


def test_wal_and_busy_timeout(conn):
    """WAL 真的开着 · ``busy_timeout`` = 5000（`docs/02 §3.6`；备份纪律 `Connection.backup()`
    成立的前提就是 WAL 真的开着）。"""
    assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == db.BUSY_TIMEOUT_MS == 5000


def test_foreign_keys_enabled(conn):
    """``foreign_keys=ON`` —— 关掉时四张表的 ``ON DELETE CASCADE`` **静默不生效**。"""
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


# ---- ⑤ kc_ids 不接受空数组（F19 的第三层）-----------------------------------


def test_kc_ids_rejects_empty_array(conn):
    """``kc_ids = '[]'`` 被 CHECK 拒绝 —— 旧仓是 ``NOT NULL DEFAULT '[]'``，
    于是"非空"在库层面**看起来**被满足（`docs/02 §3.2` 点名的 F19 表面合规）。"""
    user_id = _insert_user(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO student_attempt (attempt_id, user_id, item_id, session_id, kc_ids,"
            " answer_raw, expected, attempt_no, source, grader, created_at, asset_version)"
            " VALUES ('att_1', ?, 'itm_1', 'ses_1', '[]', 'x', 'y', 1, 'practice', 'rule', 1.0, 'rev')",
            (user_id,),
        )
    conn.rollback()


# ---- ⑥ attempt_no 的唯一约束 -------------------------------------------------


def test_attempt_no_unique_conflict(conn):
    """同一 ``(user, item, attempt_no)`` 第二次写入被拒 —— `attempt_no` 由 ``COUNT(...)+1`` 派生，
    并发下两个请求会派生出同一个值（`docs/02 §3.5`）。"""
    user_id = _insert_user(conn)
    statement = (
        "INSERT INTO student_attempt (attempt_id, user_id, item_id, session_id, kc_ids,"
        " answer_raw, expected, attempt_no, source, grader, created_at, asset_version)"
        " VALUES (?, ?, 'itm_1', 'ses_1', '[\"kc_1\"]', 'x', 'y', 1, 'practice', 'rule', 1.0, 'rev')"
    )
    conn.execute(statement, ("att_1", user_id))
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(statement, ("att_2", user_id))
    conn.rollback()


# ---- ⑦⑧ 删除读回 + 计数一致性 ------------------------------------------------


def test_count_user_rows_reads_back_consistent(conn):
    """写 → 读回一致（R-B：计数函数读的是同一张表、同一个 ``user_id``）。"""
    user_id = _insert_user(conn)
    for index in range(3):
        conn.execute(
            "INSERT INTO student_attempt (attempt_id, user_id, item_id, session_id, kc_ids,"
            " answer_raw, expected, attempt_no, source, grader, created_at, asset_version)"
            " VALUES (?, ?, 'itm_1', 'ses_1', '[\"kc_1\"]', 'x', 'y', ?, 'practice', 'rule', 1.0, 'rev')",
            (f"att_{index}", user_id, index + 1),
        )
    conn.execute(
        "INSERT INTO student_mastery (user_id, topic, attempts, correct, updated_at)"
        " VALUES (?, 'kc_1', 3, 1, 1.0)",
        (user_id,),
    )
    conn.commit()

    counted = store.count_user_rows(conn, user_id)
    assert counted == {"qa_log": 0, "hint_log": 0, "attempt": 3, "mastery": 1}


def test_delete_user_data_reads_back_zero(conn):
    """★ F16 的核心断言：四表**硬删后逐表读回 0 行**（不是"没报错"）。

    顺带断言**别人的行不受影响** —— 只删 ``WHERE user_id = ?``，不是清表。
    """
    victim = _insert_user(conn, "usr_000000000001")
    other = _insert_user(conn, "usr_000000000002")
    for user_id in (victim, other):
        conn.execute(
            "INSERT INTO student_attempt (attempt_id, user_id, item_id, session_id, kc_ids,"
            " answer_raw, expected, attempt_no, source, grader, created_at, asset_version)"
            " VALUES (?, ?, 'itm_1', 'ses_1', '[\"kc_1\"]', 'x', 'y', 1, 'practice', 'rule', 1.0, 'rev')",
            (f"att_{user_id[-1]}", user_id),
        )
    conn.commit()
    assert store.count_user_rows(conn, victim)["attempt"] == 1

    remaining = store.delete_user_data(conn, victim)
    assert remaining == {"qa_log": 0, "hint_log": 0, "attempt": 0, "mastery": 0}
    # 读回是"删除之后重新数的"，所以再数一次也必须全是 0（防"返回常量"式的假通过）。
    assert store.count_user_rows(conn, victim) == remaining
    assert store.count_user_rows(conn, other)["attempt"] == 1
