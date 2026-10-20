"""`DELETE /api/me/data` 的接口用例 —— W1 · F16 的**四类处置 + 两条边界**。

归属：`docs/05 §3` ⇒ **后端与数据**（被测对象是 `api/me.py` 的删除分支与
`identity/` + `learning/` 的两半存储层）。文件名与被测模块同名（`test_api_me.py`）。

★ **F16 的失败模式正是"测试只断言 HTTP 200"**（`docs/02 §3.1`）：旧仓删完读回还有行、
  或者写库漏一次提交 —— 两者都不会报错。因此本文件的每条用例都**逐表读回**：
  响应里的 `deleted_rows` 全 0 · 库里 `count_user_rows()` 全 0 · 令牌真的失效（401）·
  账号真的脱敏（空哈希 + `deleted_` 前缀）· 审计**真的还在**。

★ `student_qa_log` 的**写入端点属 W3**（`POST /api/chat`），因此这里直连库填一行 ——
  本用例测的是**删除面**，不是问答面；这不改变"删除必须覆盖四张表"的断言。
"""
from __future__ import annotations

import json
import secrets
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft7Validator

from app.core import db
from app.identity import users
from app.learning import store
from app.main import app

REPO_ROOT = Path(__file__).resolve().parents[2]
USER_SCHEMA = json.loads((REPO_ROOT / "contracts" / "user.schema.json").read_text(encoding="utf-8"))
DELETE_SCHEMA = {"$ref": "#/definitions/deleteResult", "definitions": USER_SCHEMA["definitions"]}

GOOD_PASSWORD = "pw-123456"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """一个**独立库文件**的 HTTP 客户端（与 `test_api_auth.py` / `test_api_practice.py` 同形）。"""
    monkeypatch.setenv("PROBCREW_DB_PATH", str(tmp_path / "me.sqlite"))
    with TestClient(app) as test_client:
        yield test_client


def _seed(username: str = "stu01", *, must_change_pw: bool = False) -> str:
    """直连存储层建号，返回 `user_id`（**测试路径** —— F15 拦的是接口路径）。"""
    conn = db.initialize()
    try:
        owner = users.create_user(
            conn,
            username=username,
            password=GOOD_PASSWORD,
            display_name="学生一",
            role="student",
            created_by="cli",
            must_change_pw=must_change_pw,
        )
    finally:
        conn.close()
    return owner["user_id"]


def _token(client: TestClient, username: str = "stu01", password: str = GOOD_PASSWORD) -> str:
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return response.json()["token"]


def _auth(token: str) -> "dict[str, str]":
    return {"Authorization": f"Bearer {token}"}


def _counts(user_id: str) -> "dict[str, int]":
    conn = db.initialize()
    try:
        return store.count_user_rows(conn, user_id)
    finally:
        conn.close()



def _fill_rows(user_id: str) -> None:
    """四张 per-user 表各写一行 —— 删除面的读回对象（`docs/02 §3.1` 的四类处置之一）。"""
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
            "INSERT INTO student_hint_log (hint_id, user_id, item_id, session_id, level, served_at,"
            " attempt_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (f"hint_{secrets.token_hex(4)}", user_id, "prob_bayes_01", "s1", 1, now, None),
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


def test_delete_own_data_returns_contract_shaped_receipt(client):
    """200 的响应体逐字段过契约（`deleteResult`）—— 四张表的读回数必须是 0。"""
    user_id = _seed()
    _fill_rows(user_id)
    token = _token(client)

    response = client.delete("/api/me/data", headers=_auth(token))
    assert response.status_code == 200, response.text
    body = response.json()
    Draft7Validator(DELETE_SCHEMA).validate(body)

    assert body["user_id"] == user_id
    assert body["status"] == "deleted"
    assert body["deleted_rows"] == {"qa_log": 0, "hint_log": 0, "attempt": 0, "mastery": 0}
    assert body["tokens_deleted"] == 1  # 本次请求用的那一个
    assert body["audit_rows_retained"] >= 1
    assert body["logs_in_scope"] is False
    assert body["third_party_irrevocable"] is True


def test_delete_is_read_back_on_every_table_and_keeps_other_users(client):
    """逐表读回：自己的四张表 0 行、别人的一行不少；令牌与登录同时失效。"""
    doomed, spared = _seed("stu01"), _seed("stu02")
    _fill_rows(doomed)
    _fill_rows(spared)
    token = _token(client)

    assert client.delete("/api/me/data", headers=_auth(token)).status_code == 200

    assert _counts(doomed) == {"qa_log": 0, "hint_log": 0, "attempt": 0, "mastery": 0}
    assert _counts(spared) == {"qa_log": 1, "hint_log": 1, "attempt": 1, "mastery": 1}
    assert client.get("/api/me", headers=_auth(token)).status_code == 401
    assert client.post(
        "/api/auth/login", json={"username": "stu01", "password": GOOD_PASSWORD}
    ).status_code == 401


def test_delete_anonymizes_account_and_retains_audit(client):
    """账号脱敏（不可登录）+ 审计**保留**：`delete_own_data` 一条，`actor_role=student`。"""
    user_id = _seed()
    token = _token(client)
    assert client.delete("/api/me/data", headers=_auth(token)).status_code == 200

    conn = db.initialize()
    try:
        owner = users.find_by_id(conn, user_id)
        assert owner is not None  # 脱敏态，不是物理删
        assert owner["status"] == "deleted"
        assert owner["password_hash"] == ""
        assert owner["display_name"] == ""
        assert owner["username"].startswith("deleted_")
        assert users.find_by_username(conn, "stu01") is None  # 原用户名可再次注册
        rows = conn.execute(
            "SELECT action, actor_role, target_id, detail FROM student_audit"
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["action"] == "delete_own_data"
        assert rows[0]["actor_role"] == "student"
        assert rows[0]["target_id"] == user_id
        assert json.loads(rows[0]["detail"]) == {"deleted_rows": 0}
    finally:
        conn.close()

    assert _seed("stu01") != user_id  # 脱敏后原用户名可复用（契约的复用规则）


def test_delete_requires_authentication_and_a_changed_password(client):
    """无令牌 401；`must_change_pw` 未改密 ⇒ 403 `must_change_pw`，且**一行都不该被删**。"""
    user_id = _seed(must_change_pw=True)
    _fill_rows(user_id)
    token = _token(client)

    assert client.delete("/api/me/data").status_code == 401
    blocked = client.delete("/api/me/data", headers=_auth(token))
    assert blocked.status_code == 403
    assert blocked.json()["error"]["code"] == "must_change_pw"
    assert _counts(user_id)["attempt"] == 1
