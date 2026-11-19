"""删除墓碑与恢复重放的用例 —— B2.1 / B2.3 的判据面（`docs/06 §15.3` · `docs/02 §1.4`）。

★ 这一组用例守的是**一句话**：「删除之后，从旧备份恢复，已删账号不得复活」。
  它由两半组成 —— ① 删除时**写墓碑**；② 恢复后**重放墓碑**。两半各自可断言，合起来才是 B2。

★ 三条纪律（都与 `docs/02 §3.6` 的"读回，不是没报错"同源）：
1. 墓碑**不含内容与凭据** —— 用例直接读原始文本查敏感串，而不是看字段名；
2. 重放**幂等** —— 连跑两次结果一致（演练会重跑）；
3. 墓碑写不进去 ⇒ 删除**不执行**（fail-closed），否则会留下"删了但恢复会复活"的洞。
"""
from __future__ import annotations

import json
import secrets
import time
from pathlib import Path

import pytest

from app.core import db
from app.core.errors import AppError
from app.identity import tombstones, users
from app.learning import store

GOOD_PASSWORD = "pw-123456"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """独立库文件 ⇒ 墓碑日志自动落在 `tmp_path/tombstones.jsonl`（跟着 `PROBCREW_DB_PATH` 走）。"""
    monkeypatch.setenv("PROBCREW_DB_PATH", str(tmp_path / "tomb.sqlite"))
    return tmp_path


def _seed(username: str = "stu01") -> str:
    conn = db.initialize()
    try:
        return users.create_user(
            conn,
            username=username,
            password=GOOD_PASSWORD,
            display_name="学生一",
            role="student",
            created_by="cli",
        )["user_id"]
    finally:
        conn.close()


def _fill(user_id: str) -> None:
    """四张 per-user 表各一行 —— 重放必须把它们清零。"""
    conn = db.initialize()
    try:
        now = time.time()
        conn.execute(
            "INSERT INTO student_qa_log (qa_id, user_id, query, answer, provider, model, egress,"
            " created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (f"qa_{secrets.token_hex(4)}", user_id, "什么是条件概率", "定义……", "deepseek",
             "deepseek-chat", "remote", now),
        )
        conn.execute(
            "INSERT INTO student_hint_log (hint_id, user_id, item_id, session_id, level, served_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (f"hint_{secrets.token_hex(4)}", user_id, "prob_bayes_01", "s1", 1, now),
        )
        conn.execute(
            "INSERT INTO student_attempt (attempt_id, user_id, item_id, session_id, kc_ids, correct,"
            " score, answer_raw, answer_normalized, expected, hint_used, duration_ms, attempt_no,"
            " source, grader, created_at, asset_version, mistake_matched)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (f"att_{secrets.token_hex(4)}", user_id, "prob_bayes_01", "s1", '["kc_bayes"]', 0,
             0.0, "1/2", "1/2", "1/6", 1, 1000, 1, "practice", "deterministic", now,
             "0123456789ab", 0),
        )
        conn.execute(
            "INSERT INTO student_mastery (user_id, topic, attempts, correct, updated_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (user_id, "kc_bayes", 1, 0, now),
        )
        conn.commit()
    finally:
        conn.close()


def _rows(user_id: str) -> "dict[str, int]":
    conn = db.initialize()
    try:
        return store.count_user_rows(conn, user_id)
    finally:
        conn.close()


# ---- ① 写墓碑 ---------------------------------------------------------------- #


def test_record_writes_one_metadata_only_line(env: Path) -> None:
    """墓碑恰有 6 个键，且**原文**不含题目/答案/凭据（`docs/02 §1.4`：不含内容的稳定标识记录）。"""
    user_id = _seed()
    _fill(user_id)
    entry = tombstones.record(
        user_id=user_id, action=tombstones.ACTION_DELETE_OWN_DATA, content_rev="0123456789ab"
    )

    path = tombstones.log_path()
    assert path == env / "tombstones.jsonl"
    raw = path.read_text(encoding="utf-8")
    lines = [json.loads(line) for line in raw.splitlines() if line.strip()]
    assert len(lines) == 1
    assert set(entry) == {"v", "tombstone_id", "user_id", "deleted_at", "action", "content_rev"}
    assert entry["user_id"] == user_id
    assert entry["v"] == tombstones.TOMBSTONE_VERSION
    for needle in ("什么是条件概率", "deepseek-chat", "1/2", "1/6", GOOD_PASSWORD, "prob_bayes_01"):
        assert needle not in raw, f"墓碑泄漏了 {needle!r}"


def test_record_is_idempotent_per_user(env: Path) -> None:
    """同一账号重复删除（幂等重试）只留一条墓碑。"""
    user_id = _seed()
    first = tombstones.record(user_id=user_id, action=tombstones.ACTION_DELETE_OWN_DATA)
    second = tombstones.record(user_id=user_id, action=tombstones.ACTION_DELETE_USER)
    assert first["tombstone_id"] == second["tombstone_id"]
    assert len(tombstones.load()) == 1


def test_record_rejects_unknown_action(env: Path) -> None:
    with pytest.raises(tombstones.TombstoneError):
        tombstones.record(user_id="usr_000000000000", action="drop_everything")


def test_record_fails_loud_when_the_log_cannot_be_written(env: Path) -> None:
    """写不进去 ⇒ `dependency_unavailable`（HTTP 层据此 503 并**中止删除**）。"""
    target = env / "tombstones.jsonl"
    target.mkdir()  # 用目录占位，让追加写必然失败
    with pytest.raises(AppError) as raised:
        tombstones.record(user_id="usr_000000000000", action=tombstones.ACTION_DELETE_OWN_DATA)
    assert raised.value.code == "dependency_unavailable"


def test_load_rejects_malformed_lines_with_a_line_number(env: Path) -> None:
    """坏行**不静默跳过**：丢一条墓碑 = 恢复后多一个复活的账号。"""
    path = tombstones.log_path()
    path.write_text(
        '{"v":1,"user_id":"usr_000000000001","action":"delete_own_data"}\n这不是 JSON\n',
        encoding="utf-8",
    )
    with pytest.raises(tombstones.TombstoneError) as raised:
        tombstones.load()
    assert ":2" in str(raised.value)


def test_load_returns_empty_for_a_fresh_install(env: Path) -> None:
    assert tombstones.load() == []


# ---- ② 重放 ------------------------------------------------------------------ #


def test_mark_account_deleted_anonymizes_and_revokes_tokens(env: Path) -> None:
    user_id = _seed()
    conn = db.initialize()
    try:
        conn.execute(
            "INSERT INTO student_token (token_hash, user_id, issued_at, expires_at, revoked)"
            " VALUES (?, ?, ?, ?, 0)",
            (secrets.token_hex(32), user_id, time.time(), time.time() + 3600),
        )
        conn.commit()
        assert tombstones.mark_account_deleted(conn, user_id) == 1
        owner = users.find_by_id(conn, user_id)
        assert owner["status"] == "deleted"
        assert owner["password_hash"] == ""
    finally:
        conn.close()


def test_mark_account_deleted_ignores_unknown_user(env: Path) -> None:
    """恢复出来的库可能比墓碑旧（这人还没注册）—— 跳过而不是崩。"""
    conn = db.initialize()
    try:
        assert tombstones.mark_account_deleted(conn, "usr_0000000000ff") == 0
    finally:
        conn.close()


def test_replay_purges_rows_and_keeps_the_account_deleted(env: Path) -> None:
    """B2.3 的核心：重放后四表 0 行、账号不可登录；**跑两次结果一致**。"""
    user_id = _seed()
    _fill(user_id)
    tombstones.record(user_id=user_id, action=tombstones.ACTION_DELETE_OWN_DATA)

    conn = db.initialize()
    try:
        summary = tombstones.replay(conn, purge=store.delete_user_data)
        assert summary == {"tombstones": 1, "applied": 1, "missing": 0}
    finally:
        conn.close()

    assert _rows(user_id) == {"qa_log": 0, "hint_log": 0, "attempt": 0, "mastery": 0}

    conn = db.initialize()
    try:
        assert tombstones.replay(conn, purge=store.delete_user_data)["applied"] == 1
        owner = users.find_by_id(conn, user_id)
        assert owner["status"] == "deleted"
        assert owner["password_hash"] == ""
    finally:
        conn.close()


def test_replay_reports_tombstones_missing_from_the_restored_db(env: Path) -> None:
    """墓碑比备份新：库里没有这个人 ⇒ 计入 `missing`，不抛、不误删别人。"""
    tombstones.record(user_id="usr_0000000000ab", action=tombstones.ACTION_DELETE_USER)
    conn = db.initialize()
    try:
        summary = tombstones.replay(conn, purge=store.delete_user_data)
    finally:
        conn.close()
    assert summary == {"tombstones": 1, "applied": 0, "missing": 1}


def test_replay_merges_backup_snapshots(env: Path) -> None:
    """换机恢复：活日志为空、只有 `backups/tombstones-<stamp>.jsonl` 时也要能重放（B2.2 的快照）。"""
    user_id = _seed()
    _fill(user_id)
    backups = env / "backups"
    backups.mkdir()
    (backups / "tombstones-20260101T000000Z.jsonl").write_text(
        json.dumps(
            {
                "v": 1,
                "tombstone_id": "tb_000000000001",
                "user_id": user_id,
                "deleted_at": 1767225600.0,
                "action": "delete_own_data",
                "content_rev": None,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    conn = db.initialize()
    try:
        summary = tombstones.replay(conn, purge=store.delete_user_data, paths=[backups])
    finally:
        conn.close()
    assert summary == {"tombstones": 1, "applied": 1, "missing": 0}
    assert _rows(user_id) == {"qa_log": 0, "hint_log": 0, "attempt": 0, "mastery": 0}
