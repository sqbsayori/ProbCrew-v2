"""`scripts/manage_users.py` 的用例 —— W1 · CLI（**F15：接口不可创建管理员**的唯一出口）。

归属：`docs/05 §3` ⇒ **后端与数据**。被测对象是脚本的**命令行行为**（子进程），
因此每条用例都真跑 `python scripts/manage_users.py …`，再**直连库读回**结果 ——
只断言退出码会漏掉"命令说成功了、其实没写进去"这一类事故（`docs/02 §3.6` 的旧仓教训）。

★ `PROBCREW_DB_PATH` 同时设进**父进程**（直连读回）与**子进程环境**（脚本），
  两边必须指向同一个库文件 —— 否则用例会变成一个"自己跟自己玩"的假绿。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from app.core import db
from app.identity import users

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "manage_users.py"

GOOD_PASSWORD = "pw-123456"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """独立库文件 + 同一路径的子进程环境。"""
    path = str(tmp_path / "cli.sqlite")
    monkeypatch.setenv("PROBCREW_DB_PATH", path)
    assert os.environ["PROBCREW_DB_PATH"] == path
    return dict(os.environ)


def _run(env: dict, *args: str, stdin: "str | None" = None) -> "subprocess.CompletedProcess[str]":
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        env=env,
        input=stdin,
        capture_output=True,
        text=True,
        timeout=120,
    )


def _fetch(username: str) -> "dict | None":
    conn = db.initialize()
    try:
        return users.find_by_username(conn, username)
    finally:
        conn.close()


def _fetch_id(user_id: str) -> "dict | None":
    """按主键读回（脱敏后 `username` 已改名，按名字查就查不到了）。"""
    conn = db.initialize()
    try:
        return users.find_by_id(conn, user_id)
    finally:
        conn.close()


def _audit_rows(action: str) -> "list[dict]":
    conn = db.initialize()
    try:
        rows = conn.execute("SELECT * FROM student_audit WHERE action = ?", (action,)).fetchall()
        return [{key: row[key] for key in row.keys()} for row in rows]
    finally:
        conn.close()


def test_create_makes_the_first_admin(env):
    """★ 本仓"第一个管理员账号"的唯一入口：建号 → 读回 → 哈希校验（明文不落库）。"""
    result = _run(env, "create", "--username", "teach01", "--role", "admin", "--password", GOOD_PASSWORD)
    assert result.returncode == 0, result.stderr
    assert "[已创建]" in result.stdout

    owner = _fetch("teach01")
    assert owner is not None
    assert owner["role"] == "admin"
    assert owner["created_by"] == "cli"
    assert owner["status"] == "active"
    assert owner["password_hash"] != GOOD_PASSWORD  # 只存哈希（N6）
    assert users.verify_password(GOOD_PASSWORD, owner["password_hash"])


def test_create_reports_conflict_and_bad_username(env):
    """两条失败路径都**如实退出**（退出码 1 + 统一 code），而不是静默成功。"""
    assert _run(env, "create", "--username", "stu01", "--password", GOOD_PASSWORD).returncode == 0

    duplicate = _run(env, "create", "--username", "stu01", "--password", GOOD_PASSWORD)
    assert duplicate.returncode == 1
    assert "[conflict]" in duplicate.stderr

    bad = _run(env, "create", "--username", "学号001", "--password", GOOD_PASSWORD)
    assert bad.returncode == 1
    assert "[invalid_argument]" in bad.stderr


def test_stdin_password_and_listing(env):
    """密码经管道给（不进 shell 历史）；`list` 打印账号但**不含**任何凭据。"""
    created = _run(env, "create", "--username", "stu02", "--password-stdin", stdin="pw-abcdef\n")
    assert created.returncode == 0, created.stderr
    assert users.verify_password("pw-abcdef", _fetch("stu02")["password_hash"])

    listing = _run(env, "list")
    assert listing.returncode == 0, listing.stderr
    assert "stu02" in listing.stdout
    assert "pw-abcdef" not in listing.stdout
    assert "password_hash" not in listing.stdout


def test_reset_password_forces_change_and_audits(env):
    """重置密码的三件事（契约 `adminPasswordResetResult`）+ CLI 审计（`actor_id=cli`）。"""
    assert _run(env, "create", "--username", "stu03", "--password", GOOD_PASSWORD).returncode == 0

    reset = _run(env, "reset-password", "--username", "stu03", "--generate")
    assert reset.returncode == 0, reset.stderr
    generated = reset.stdout.split("[生成密码]", 1)[1].split()[0]
    assert len(generated) >= 12

    owner = _fetch("stu03")
    assert users.verify_password(generated, owner["password_hash"])
    assert not users.verify_password(GOOD_PASSWORD, owner["password_hash"])
    assert bool(owner["must_change_pw"]) is True

    rows = _audit_rows("reset_password")
    assert len(rows) == 1
    assert rows[0]["actor_id"] == "cli"
    assert rows[0]["actor_role"] == "admin"
    assert json.loads(rows[0]["detail"]) == {}


def test_disable_and_enable(env):
    """`active ⇄ disabled`；`disable` 只改状态，不删数据、不撤令牌（那是删除/重置的事）。"""
    assert _run(env, "create", "--username", "stu04", "--password", GOOD_PASSWORD).returncode == 0
    assert _run(env, "disable", "--username", "stu04").returncode == 0
    assert _fetch("stu04")["status"] == "disabled"
    assert _run(env, "enable", "--username", "stu04").returncode == 0
    assert _fetch("stu04")["status"] == "active"


def test_delete_requires_confirmation_and_keeps_audit(env):
    """`--yes` 之前**一行都不删**；删除后逐表读回 0 行，审计保留（`actor_id=cli`）。"""
    assert _run(env, "create", "--username", "stu05", "--password", GOOD_PASSWORD).returncode == 0
    owner = _fetch("stu05")

    refused = _run(env, "delete", "--username", "stu05")
    assert refused.returncode == 1
    assert "--yes" in refused.stderr
    assert _fetch("stu05")["status"] == "active"

    deleted = _run(env, "delete", "--username", "stu05", "--yes")
    assert deleted.returncode == 0, deleted.stderr
    assert _fetch_id(owner["user_id"])["status"] == "deleted"
    assert _fetch("stu05") is None  # 原用户名已释放（契约的复用规则）

    rows = _audit_rows("delete_user")
    assert len(rows) == 1
    assert rows[0]["target_id"] == owner["user_id"]
    assert rows[0]["actor_id"] == "cli"
