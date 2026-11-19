"""`scripts/backup.py` 的用例 —— B2.3「恢复后重放墓碑，已删账号不复活」。

归属：被测对象是**脚本行为**（`docs/05 §3` 规则 1），本文件由**后端与数据**提交、
按「经 架构与集成 同意」记账（`scripts/backup.py` 属架构与集成）。

★ 为什么必须**真跑脚本函数**而不是只测 `replay()`：B2.3 的判据是"**从旧备份恢复后**仍不可登录"。
  只测重放函数会漏掉"恢复根本没把墓碑带进来 / 顺序反了"这类集成缺陷 —— 而那正是这条需求的全部意义。

★ 场景顺序刻意与真实演练一致：**先备份 → 再删除 → 再恢复**。删除发生在备份**之后**，
  所以备份里仍有这个人的数据；恢复必须靠墓碑把"删除之后的事实"补回去。
"""
from __future__ import annotations

import importlib.util
import json
import secrets
import time
from pathlib import Path

import pytest

from app.core import db
from app.identity import tombstones, users
from app.learning import store

REPO_ROOT = Path(__file__).resolve().parents[2]
GOOD_PASSWORD = "pw-123456"


def _load_backup_script():
    path = REPO_ROOT / "scripts" / "backup.py"
    spec = importlib.util.spec_from_file_location("backup_under_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def data_dir(tmp_path, monkeypatch) -> Path:
    directory = tmp_path / "data"
    directory.mkdir()
    monkeypatch.setenv("PROBCREW_DB_PATH", str(directory / "probcrew.sqlite"))
    return directory


def _seed() -> str:
    conn = db.initialize()
    try:
        return users.create_user(
            conn,
            username="stu01",
            password=GOOD_PASSWORD,
            display_name="学生一",
            role="student",
            created_by="cli",
        )["user_id"]
    finally:
        conn.close()


def _fill(user_id: str) -> None:
    conn = db.initialize()
    try:
        now = time.time()
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


def _status(user_id: str) -> str:
    conn = db.initialize()
    try:
        return users.find_by_id(conn, user_id)["status"]
    finally:
        conn.close()


def _delete_after_backup(user_id: str) -> None:
    """模拟"备份之后，学生删号"：写墓碑 → 清四表 → 脱敏（与 `DELETE /api/me/data` 同序）。"""
    tombstones.record(user_id=user_id, action=tombstones.ACTION_DELETE_OWN_DATA)
    conn = db.initialize()
    try:
        store.delete_user_data(conn, user_id)
        users.anonymize_user(conn, user_id)
    finally:
        conn.close()


def test_restore_replays_tombstones_so_a_deleted_account_cannot_resurrect(data_dir: Path) -> None:
    backup = _load_backup_script()
    user_id = _seed()
    _fill(user_id)
    assert backup.run_backup(data_dir, REPO_ROOT / "content") == 0

    _delete_after_backup(user_id)
    assert _rows(user_id)["attempt"] == 0
    assert _status(user_id) == "deleted"

    assert backup.run_restore(data_dir) == 0

    # 备份里这个人的数据"回来了"，但墓碑必须再把它清掉 —— 这就是 B2.3 的判据本身。
    assert _rows(user_id) == {"qa_log": 0, "hint_log": 0, "attempt": 0, "mastery": 0}
    assert _status(user_id) == "deleted"


def test_restore_without_tombstones_brings_the_backup_back_intact(data_dir: Path) -> None:
    """反向：没有墓碑的旧备份照常恢复（重放**不得误伤**从未删过的人）。"""
    backup = _load_backup_script()
    user_id = _seed()
    _fill(user_id)
    assert backup.run_backup(data_dir, REPO_ROOT / "content") == 0

    conn = db.initialize()
    try:
        store.delete_user_data(conn, user_id)
    finally:
        conn.close()
    assert _rows(user_id)["attempt"] == 0

    assert backup.run_restore(data_dir) == 0
    assert _rows(user_id) == {"qa_log": 0, "hint_log": 0, "attempt": 1, "mastery": 1}
    assert _status(user_id) == "active"


def test_restore_is_idempotent_and_keeps_the_snapshot_counts(data_dir: Path) -> None:
    """连跑两次恢复：结果一致（演练会重跑），且逐表读回与备份时刻一致（N19 判据）。"""
    backup = _load_backup_script()
    user_id = _seed()
    _fill(user_id)
    assert backup.run_backup(data_dir, REPO_ROOT / "content") == 0
    _delete_after_backup(user_id)

    for _ in range(2):
        assert backup.run_restore(data_dir) == 0
        assert _rows(user_id) == {"qa_log": 0, "hint_log": 0, "attempt": 0, "mastery": 0}
        assert _status(user_id) == "deleted"


def test_backup_status_file_matches_the_health_contract(data_dir: Path) -> None:
    """B0.2 的消费者与生产者对同一份文件的形状理解一致（`last_backup.json`）。"""
    backup = _load_backup_script()
    user_id = _seed()
    _fill(user_id)
    assert backup.run_backup(data_dir, REPO_ROOT / "content") == 0

    payload = json.loads((data_dir / "backups" / "last_backup.json").read_text(encoding="utf-8"))
    assert payload["last_result"] == "ok"
    assert payload["last_success_at"].endswith("Z")
    assert set(payload) == {"last_success_at", "last_result", "last_failure_at"}
