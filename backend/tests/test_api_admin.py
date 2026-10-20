"""管理端 5 条端点的接口用例 —— W1 · 教师侧（F15 · `docs/02 §6.1` 的管理行）。

归属：`docs/05 §3` ⇒ **后端与数据**（被测对象是 `api/admin.py` 的接线 +
`identity/` 的账号管理与 `learning/` 的数据视图读面）。

★ **本文件承担 F15 的「管理员可见全体学生数据」断言面**：`docs/06 §11` 的 W1 行把它列为
  MUST，而此前 `#/admin` 与 5 个端点**在交付清单里 0 命中**（`docs/06 §11` 记了四处"没人造它"）。
  因此每条用例都断言**两件事**：① 管理员**真的看得到**（读回数字）；② **留了痕**（审计行）。
★ 响应体逐条过契约（`adminUserRow` / `adminStudentRow` / `adminImportResult` /
  `adminPasswordResetResult` / `auditRow` / `deleteResult` / `pagination`）——
  契约是 `additionalProperties: false`，"多返回一个 `password_hash`"会当场变红。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft7Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT7

from app.core import db
from app.identity import users
from app.main import app

REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACTS_DIR = REPO_ROOT / "contracts"
SCHEMAS = {
    path.name: json.loads(path.read_text(encoding="utf-8"))
    for path in sorted(CONTRACTS_DIR.glob("*.json"))
}
_REGISTRY = Registry().with_resources(
    [
        (document["$id"], Resource.from_contents(document, default_specification=DRAFT7))
        for document in SCHEMAS.values()
    ]
)
USER = "https://probcrew.local/contracts/user.schema.json"
PAGINATION = "https://probcrew.local/contracts/pagination.schema.json"

GOOD_PASSWORD = "pw-123456"


def _validate(instance, ref: str) -> None:
    """按 `$ref` 校验一个响应片段（`registry` 让跨文件 `$ref` 不必联网）。"""
    Draft7Validator({"$ref": ref}, registry=_REGISTRY).validate(instance)


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """一个**独立库文件**的 HTTP 客户端（与其他接口用例同形）。"""
    monkeypatch.setenv("PROBCREW_DB_PATH", str(tmp_path / "admin.sqlite"))
    with TestClient(app) as test_client:
        yield test_client


def _seed(username: str = "stu01", *, role: str = "student", password: str = GOOD_PASSWORD) -> str:
    """直连存储层建号（**测试路径**）；`admin` 角色只有 CLI 与夹具能建（F15）。"""
    conn = db.initialize()
    try:
        owner = users.create_user(
            conn,
            username=username,
            password=password,
            display_name=f"账号 {username}",
            role=role,
            created_by="cli",
        )
    finally:
        conn.close()
    return owner["user_id"]


def _token(client: TestClient, username: str, password: str = GOOD_PASSWORD) -> str:
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return response.json()["token"]


def _auth(token: str) -> "dict[str, str]":
    return {"Authorization": f"Bearer {token}"}


def _practice_once(client: TestClient, token: str) -> "dict[str, Any]":
    """做一次**可判定**的题（走真实判定链），返回 `{item_id, attempts}`。

    ★ 题库的第一条是 `gradable=false`（`test_api_practice.py` 有一条用例专测它）——
      逐条试到 `graded=True` 为止，并**如实返回试了几次**：掌握度只在可判定时累加，
      所以"试了几次"必须由调用方断言，不能假设 1 次。
    """
    page = client.get("/api/problems", headers=_auth(token)).json()
    attempts = 0
    for item in page["items"]:
        attempts += 1
        graded = client.post(
            "/api/practice/grade",
            headers=_auth(token),
            json={"item_id": item["prob_id"], "answer": "__definitely_wrong__", "session_id": "s1"},
        )
        assert graded.status_code == 200, graded.text
        if graded.json()["graded"]:
            return {"item_id": item["prob_id"], "attempts": attempts}
    raise AssertionError("题库里没有可判定题目")


def _audit_rows(action: str) -> "list[dict]":
    conn = db.initialize()
    try:
        rows = conn.execute(
            "SELECT * FROM student_audit WHERE action = ? ORDER BY created_at ASC", (action,)
        ).fetchall()
        return [{key: row[key] for key in row.keys()} for row in rows]
    finally:
        conn.close()


# ---- ① 角色闸门：五个端点逐条 403 --------------------------------------------


def test_student_is_forbidden_on_every_admin_endpoint(client):
    """F15 的**结构面**：学生令牌访问五个管理端点一律 `403 forbidden`（且**没有任何副作用**）。"""
    target = _seed("stu02")
    _seed("stu01")
    token = _token(client, "stu01")
    calls = [
        ("GET", "/api/admin/students", None),
        ("GET", "/api/admin/users", None),
        ("GET", "/api/admin/audit", None),
        ("POST", "/api/admin/users/import", {"rows": [{"username": "imp99", "password": "pw-123456"}]}),
        ("PATCH", f"/api/admin/users/{target}", {"display_name": "改名"}),
        ("DELETE", f"/api/admin/users/{target}", None),
    ]
    for method, path, body in calls:
        response = client.request(method, path, headers=_auth(token), json=body)
        assert response.status_code == 403, f"{method} {path} → {response.status_code}"
        assert response.json()["error"]["code"] == "forbidden"
    assert _audit_rows("view_users") == []  # 被拦的请求不写审计
    assert _audit_rows("delete_user") == []


def test_every_admin_endpoint_requires_a_token(client):
    """没有令牌 ⇒ 401（与角色不足的 403 是**两件事**，前端动作也不同）。"""
    for path in ("/api/admin/students", "/api/admin/users", "/api/admin/audit"):
        assert client.get(path).status_code == 401


# ---- ② 账号管理视图 -----------------------------------------------------------


def test_admin_users_page_matches_contract_and_writes_view_audit(client):
    """`adminUserRow` 逐行过契约 · 游标翻页不重不漏 · `view_users` 留痕。"""
    _seed("stu01")
    _seed("stu02")
    _seed("teach01", role="admin")
    token = _token(client, "teach01")

    first = client.get("/api/admin/users?limit=1", headers=_auth(token))
    assert first.status_code == 200, first.text
    body = first.json()
    _validate(body, PAGINATION)
    assert body["next_cursor"] is not None
    _validate(body["items"][0], f"{USER}#/definitions/adminUserRow")
    assert "password_hash" not in body["items"][0]  # 白名单投影

    second = client.get(
        f"/api/admin/users?limit=1&cursor={body['next_cursor']}", headers=_auth(token)
    ).json()
    assert second["items"][0]["user_id"] != body["items"][0]["user_id"]
    assert len(_audit_rows("view_users")) == 2  # 看一次记一次


def test_admin_students_view_is_per_student_and_filterable(client):
    """数据视图：**逐人**的作答 / 掌握度 / 最近作答；`?user=` 只筛不合并。"""
    student_id = _seed("stu01")
    _seed("stu02")
    _seed("teach01", role="admin")
    student_token = _token(client, "stu01")
    admin_token = _token(client, "teach01")
    practiced = _practice_once(client, student_token)

    page = client.get("/api/admin/students", headers=_auth(admin_token)).json()
    _validate(page, PAGINATION)
    row = next(item for item in page["items"] if item["user_id"] == student_id)
    _validate(row, f"{USER}#/definitions/adminStudentRow")
    assert row["attempt_count"] == practiced["attempts"]
    assert row["mastery"] and sum(topic["attempts"] for topic in row["mastery"]) >= 1
    assert row["recent_attempts"][0]["item_id"] == practiced["item_id"]
    assert row["recent_attempts"][0]["hint_used"] == 0
    # ★ 边界：形状里没有跨学生聚合字段（契约 additionalProperties=false 会拒绝）
    assert not {"average", "rank", "distribution", "heatmap"} & set(row)

    filtered = client.get(f"/api/admin/students?user={student_id}", headers=_auth(admin_token)).json()
    assert [item["user_id"] for item in filtered["items"]] == [student_id]
    assert _audit_rows("view_students")


# ---- ③ 审计列表 ---------------------------------------------------------------


def test_admin_audit_page_shape_and_action_filter(client):
    """`auditRow` 逐行过契约 · `action` 过滤 · 非法 `action` ⇒ 400（不静默全量返回）。"""
    _seed("stu01")
    _seed("teach01", role="admin")
    token = _token(client, "teach01")
    client.get("/api/admin/users", headers=_auth(token))

    page = client.get("/api/admin/audit?action=view_users", headers=_auth(token)).json()
    _validate(page, PAGINATION)
    for row in page["items"]:
        _validate(row, f"{USER}#/definitions/auditRow")
    assert {row["action"] for row in page["items"]} == {"view_users"}
    assert _audit_rows("view_audit")

    bad = client.get("/api/admin/audit?action=export_students", headers=_auth(token))
    assert bad.status_code == 400
    assert bad.json()["error"]["code"] == "invalid_argument"



# ---- ④ 批量导入 ---------------------------------------------------------------


def test_import_creates_students_only_and_forces_password_change(client):
    """`adminImportResult` · 逐行拒绝（不静默跳过）· 导入的账号**必须改密** · 留痕。"""
    _seed("teach01", role="admin")
    token = _token(client, "teach01")
    response = client.post(
        "/api/admin/users/import",
        headers=_auth(token),
        json={
            "rows": [
                {"username": "imp01", "password": "pw-123456", "display_name": "导入一"},
                {"username": "imp01", "password": "pw-123456"},  # 批内重复 ⇒ 逐行拒绝
            ]
        },
    )
    assert response.status_code == 200, response.text
    result = response.json()
    _validate(result, f"{USER}#/definitions/adminImportResult")
    assert result["created"] == 1
    assert result["rejected"][0]["row"] == 1
    assert "已存在" in result["rejected"][0]["reason"]
    assert result["role"] == "student"

    # ★ 导入的账号与"重置密码"是同一个信息不对称 ⇒ 必须下次登录改密
    login = client.post("/api/auth/login", json={"username": "imp01", "password": "pw-123456"})
    assert login.status_code == 200
    assert login.json()["user"]["must_change_pw"] is True
    blocked = client.get("/api/me/wrong", headers=_auth(login.json()["token"]))
    assert blocked.status_code == 403 and blocked.json()["error"]["code"] == "must_change_pw"

    assert _audit_rows("import_users")
    # ★ F15：形状层面传不进 role（`extra="forbid"`）
    smuggled = client.post(
        "/api/admin/users/import",
        headers=_auth(token),
        json={"rows": [{"username": "imp02", "password": "pw-123456", "role": "admin"}]},
    )
    assert smuggled.status_code == 400
    assert smuggled.json()["error"]["code"] == "invalid_argument"


# ---- ⑤ PATCH：重置密码分支 ----------------------------------------------------


def test_patch_resets_password_and_revokes_tokens(client):
    """`adminPasswordResetResult` 的三个必填全部为真：强制改密 · 令牌全撤 · 审计已写。"""
    student_id = _seed("stu01")
    _seed("teach01", role="admin")
    student_token = _token(client, "stu01")
    admin_token = _token(client, "teach01")

    response = client.patch(
        f"/api/admin/users/{student_id}",
        headers=_auth(admin_token),
        json={"password": "new-pw-123"},
    )
    assert response.status_code == 200, response.text
    result = response.json()
    _validate(result, f"{USER}#/definitions/adminPasswordResetResult")
    assert result == {
        "user_id": student_id,
        "must_change_pw": True,
        "tokens_revoked": 1,
        "audit_written": True,
    }
    # 旧会话立刻失效；新密码能登录，但只能去改密
    assert client.get("/api/me", headers=_auth(student_token)).status_code == 401
    fresh = client.post("/api/auth/login", json={"username": "stu01", "password": "new-pw-123"})
    assert fresh.status_code == 200
    assert fresh.json()["user"]["must_change_pw"] is True
    assert _audit_rows("reset_password")


def test_patch_updates_fields_and_refuses_privilege_escalation(client):
    """停用立即生效（403 `account_disabled`）· `role=admin` 被拒（F15）· 自我停用被拒 · 空 PATCH 400。"""
    student_id = _seed("stu01")
    admin_id = _seed("teach01", role="admin")
    student_token = _token(client, "stu01")
    admin_token = _token(client, "teach01")

    disabled = client.patch(
        f"/api/admin/users/{student_id}", headers=_auth(admin_token), json={"status": "disabled"}
    )
    assert disabled.status_code == 200, disabled.text
    _validate(disabled.json(), f"{USER}#/definitions/adminUserRow")
    assert disabled.json()["status"] == "disabled"
    blocked = client.get("/api/me", headers=_auth(student_token))
    assert blocked.status_code == 403 and blocked.json()["error"]["code"] == "account_disabled"
    assert _audit_rows("update_user")

    escalate = client.patch(
        f"/api/admin/users/{student_id}", headers=_auth(admin_token), json={"role": "admin"}
    )
    assert escalate.status_code == 400
    assert "manage_users" in escalate.json()["error"]["message"]

    itself = client.patch(
        f"/api/admin/users/{admin_id}", headers=_auth(admin_token), json={"status": "disabled"}
    )
    assert itself.status_code == 400
    assert client.patch(
        f"/api/admin/users/{student_id}", headers=_auth(admin_token), json={}
    ).status_code == 400
    assert client.patch(
        "/api/admin/users/usr_000000000000", headers=_auth(admin_token), json={"unlock": True}
    ).status_code == 404


def test_patch_unlock_clears_failed_count_and_lock(client):
    """解锁并入 `update_user`（8 个动作不扩枚举）：读回 `failed_count=0` / `locked_until=null`。"""
    student_id = _seed("stu01")
    admin_id = _seed("teach01", role="admin")
    admin_token = _token(client, "teach01")

    conn = db.initialize()
    try:
        for _ in range(5):
            users.record_login_failure(conn, student_id)
        locked = users.find_by_id(conn, student_id)
        assert locked["locked_until"] is not None
    finally:
        conn.close()

    response = client.patch(
        f"/api/admin/users/{student_id}", headers=_auth(admin_token), json={"unlock": True}
    )
    assert response.status_code == 200, response.text
    assert response.json()["failed_count"] == 0
    assert response.json()["locked_until"] is None
    assert response.json()["user_id"] != admin_id
    assert _audit_rows("update_user")[-1]["detail"] == '{"locked_until": null}'



# ---- ⑥ DELETE：与 F16 同一套四类处置 ------------------------------------------


def test_admin_delete_user_reuses_delete_result_and_audits_delete_user(client):
    """管理员删学生：`deleteResult` 形状 · 逐表读回 0 行 · 令牌失效 · 审计动作可区分。"""
    student_id = _seed("stu01")
    admin_id = _seed("teach01", role="admin")
    student_token = _token(client, "stu01")
    _practice_once(client, student_token)
    admin_token = _token(client, "teach01")

    response = client.delete(f"/api/admin/users/{student_id}", headers=_auth(admin_token))
    assert response.status_code == 200, response.text
    result = response.json()
    _validate(result, f"{USER}#/definitions/deleteResult")
    assert result["status"] == "deleted"
    assert result["deleted_rows"] == {"qa_log": 0, "hint_log": 0, "attempt": 0, "mastery": 0}
    assert result["tokens_deleted"] == 1
    assert result["audit_rows_retained"] >= 1

    assert client.get("/api/me", headers=_auth(student_token)).status_code == 401
    assert client.post(
        "/api/auth/login", json={"username": "stu01", "password": GOOD_PASSWORD}
    ).status_code == 401

    rows = _audit_rows("delete_user")
    assert len(rows) == 1
    assert rows[0]["actor_id"] == admin_id      # 动作是管理员做的
    assert rows[0]["actor_role"] == "admin"
    assert rows[0]["target_id"] == student_id
    assert rows[0]["detail"] != "{}"
    assert json.loads(rows[0]["detail"])["deleted_rows"] >= 1  # 删前的行数（作答 + 掌握度…）
    assert client.delete(f"/api/admin/users/{admin_id}", headers=_auth(admin_token)).status_code == 400



# ---- ⑦ 请求体形状也在契约里（v1.4 `adminUserPatch`）--------------------------


def test_admin_patch_request_shape_is_contracted():
    """`PATCH` 的**请求体**也有家（此前覆盖表只映射了响应）：空 PATCH / 提权 / 直接置 `deleted` 都不合法。

    ★ 这一条查的是**契约**，不是端点 —— 端点还会返回一句能读懂的话（"请用 manage_users.py"），
      而契约保证的是"**形状层面**就传不进去"（与 `adminImportRequest` 的 `extra="forbid"` 同型）。
    """
    from jsonschema.exceptions import ValidationError

    for body in ({}, {"role": "admin"}, {"status": "deleted"}, {"username": "hacker"}):
        with pytest.raises(ValidationError):
            _validate(body, f"{USER}#/definitions/adminUserPatch")
    for body in ({"unlock": True}, {"password": "pw-123456"}, {"display_name": "新名字"}, {"role": "student"}):
        _validate(body, f"{USER}#/definitions/adminUserPatch")

