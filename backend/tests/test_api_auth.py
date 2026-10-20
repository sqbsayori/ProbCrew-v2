"""接口层用例 —— W1 · 最小身份闭环（P2）。

归属：`docs/05 §3` ⇒ **后端与数据**（被测对象是 `api/` 与 `identity/` 的接线）。

★ **为什么这一层非测不可**（`docs/02 §8-8` 的教训）：旧仓的写库漏提交 bug 之所以没被发现，
  是因为测试只断言了"HTTP 200"。本文件因此**每条分支都断言 `code` 与读回结果**，
  而不是只断言状态码。
★ 本文件依赖 `httpx`（`fastapi.testclient.TestClient` 的传输实现）—— 它已按跨域记账
  加进 `requirements-dev.txt`（本批的另一处跨域改动）。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft7Validator

from app.api import deps
from app.core import db
from app.core.errors import AppError
from app.identity import auth, users
from app.main import app

REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACT = json.loads((REPO_ROOT / "contracts" / "user.schema.json").read_text(encoding="utf-8"))

GOOD_PASSWORD = "pw-123456"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """一个**独立库文件**的 HTTP 客户端。

    ★ 用环境变量换库路径（`PROBCREW_DB_PATH`）而不是改代码：这正是 `core/config.py`
      "显式收 env 便于测试"那个设计的用处。`monkeypatch` 保证用例结束即还原。
    """
    monkeypatch.setenv("PROBCREW_DB_PATH", str(tmp_path / "api.sqlite"))
    with TestClient(app) as test_client:
        yield test_client


def _seed(username: str = "stu01", *, role: str = "student", must_change_pw: bool = False):
    """直连存储层建号（**测试路径**，不是接口路径 —— F15 拦的是接口）。"""
    conn = db.initialize()
    try:
        return users.create_user(
            conn,
            username=username,
            password=GOOD_PASSWORD,
            display_name="学生一",
            role=role,
            created_by="cli",
            must_change_pw=must_change_pw,
        )
    finally:
        conn.close()


def _login(client: TestClient, username: str = "stu01", password: str = GOOD_PASSWORD):
    return client.post("/api/auth/login", json={"username": username, "password": password})


def _auth(token: str) -> "dict[str, str]":
    return {"Authorization": f"Bearer {token}"}


# ---- ① 登录的五条分支 --------------------------------------------------------


def test_login_success_shape_matches_contract(client):
    """200 的响应体：**逐字段过契约**（`session` 信封 + 账号对象过 `user.schema.json`）。

    ★ 账号对象用 jsonschema **真校验**而不是"看着像"：契约是 `additionalProperties: false`
      ⇒ 这条断言会在"多返回一个内部列"时立刻失败，而那种洞正是 R-M 的第 ③ 面。
    """
    _seed()
    response = _login(client)
    assert response.status_code == 200
    body = response.json()

    assert set(body) == {"token", "expires_at", "user"}
    assert isinstance(body["expires_at"], float)
    assert len(body["token"]) >= 32
    Draft7Validator(CONTRACT).validate(body["user"])
    assert body["user"]["username"] == "stu01"
    assert body["user"]["role"] == "student"


def test_login_bad_credentials_for_missing_and_wrong_password(client):
    """不存在与口令错 ⇒ **同一个** 401 `bad_credentials`（信息量不变量的接口面）。"""
    _seed()
    for payload in ({"username": "nobody99", "password": GOOD_PASSWORD},
                    {"username": "stu01", "password": "wrong-password"}):
        response = client.post("/api/auth/login", json=payload)
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "bad_credentials"


def test_login_invalid_argument_for_shape_errors(client):
    """400 `invalid_argument`：字段超长 · 密码超 72 **字节** · 多传字段（含 `role`）。"""
    _seed()
    cases = [
        {"username": "ab", "password": GOOD_PASSWORD},                      # 用户名过短
        {"username": "s" * 33, "password": GOOD_PASSWORD},                  # 用户名过长
        {"username": "stu01"},                                              # 缺字段
        {"username": "stu01", "password": "密" * 25},                        # 75 字节
        {"username": "stu01", "password": GOOD_PASSWORD, "role": "admin"},  # ★ 防提权：形状层就传不进来
    ]
    for payload in cases:
        response = client.post("/api/auth/login", json=payload)
        assert response.status_code == 400, payload
        assert response.json()["error"]["code"] == "invalid_argument"


def test_login_locked_and_disabled_codes(client, monkeypatch):
    """423 `locked` 与 403 `account_disabled` 的**接口面**（都只在口令验证之后）。"""
    _seed()
    for _ in range(users.LOCK_THRESHOLD):
        assert _login(client, password="wrong-password").status_code == 401
    locked = _login(client)
    assert locked.status_code == 423
    assert locked.json()["error"]["code"] == "locked"
    assert "秒" in locked.json()["error"]["message"]

    conn = db.initialize()
    try:
        users.create_user(conn, username="dis01", password=GOOD_PASSWORD, created_by="cli")
        conn.execute("UPDATE student_user SET status = 'disabled' WHERE username = 'dis01'")
        conn.commit()
    finally:
        conn.close()
    disabled = _login(client, username="dis01")
    assert disabled.status_code == 403
    assert disabled.json()["error"]["code"] == "account_disabled"


# ---- ② /api/me 与登出 --------------------------------------------------------


def test_me_requires_a_token_and_returns_current_account(client):
    """无令牌 ⇒ 401 `unauthenticated`；带令牌 ⇒ 200 且是**令牌所属**那个账号。"""
    _seed()
    assert client.get("/api/me").status_code == 401
    assert client.get("/api/me", headers=_auth("not-a-token")).json()["error"]["code"] == "unauthenticated"

    token = _login(client).json()["token"]
    me = client.get("/api/me", headers=_auth(token))
    assert me.status_code == 200
    Draft7Validator(CONTRACT).validate(me.json())
    assert me.json()["username"] == "stu01"


def test_logout_revokes_only_this_session(client):
    """登出 ⇒ 204，且**只**撤销本次令牌（另一台设备上的会话不受影响）。"""
    _seed()
    first = _login(client).json()["token"]
    second = _login(client).json()["token"]

    assert client.post("/api/auth/logout", headers=_auth(first)).status_code == 204
    assert client.get("/api/me", headers=_auth(first)).status_code == 401
    assert client.get("/api/me", headers=_auth(second)).status_code == 200
    assert client.post("/api/auth/logout").status_code == 401  # 无令牌 ⇒ 401


def test_password_change_returns_204_and_invalidates_tokens(client):
    """改密 ⇒ 204 + 旧令牌全部失效 + 新口令可登录（契约的"前端必须重新登录"）。"""
    _seed()
    token = _login(client).json()["token"]
    changed = client.post(
        "/api/auth/password",
        json={"old_password": GOOD_PASSWORD, "new_password": "pw-654321"},
        headers=_auth(token),
    )
    assert changed.status_code == 204
    assert changed.content == b""
    assert client.get("/api/me", headers=_auth(token)).status_code == 401
    assert _login(client, password="pw-654321").status_code == 200

    # 旧口令错 ⇒ 401 bad_credentials（与登录同源，计入失败计数）。
    token2 = _login(client, password="pw-654321").json()["token"]
    wrong = client.post(
        "/api/auth/password",
        json={"old_password": "nope", "new_password": "pw-999999"},
        headers=_auth(token2),
    )
    assert wrong.status_code == 401
    assert wrong.json()["error"]["code"] == "bad_credentials"


def test_must_change_pw_does_not_block_the_identity_endpoints(client):
    """`must_change_pw` 置位时四个身份端点**都可用**（放行集合的接口面）。

    ★ `GET /api/me` 必须在放行集合里：否则前端拿不到"要改密"这个事实；
      `logout` 同理 —— 被拦会把用户困在改密页里。放行集合的取值登记在
      `identity/auth.py` 的 `PASSWORD_CHANGE_ALLOWLIST`（文档未写死的首次取值）。
    ★ 学习类接口的 403 由 `assert_password_changed` 负责（本批没有学习类端点，
      那条闸门在 `test_identity.py` 里逐接口过）。
    """
    _seed(username="must01", must_change_pw=True)
    token = _login(client, username="must01").json()["token"]

    me = client.get("/api/me", headers=_auth(token))
    assert me.status_code == 200
    assert me.json()["must_change_pw"] is True

    changed = client.post(
        "/api/auth/password",
        json={"old_password": GOOD_PASSWORD, "new_password": "pw-abcdef"},
        headers=_auth(token),
    )
    assert changed.status_code == 204

    fresh = _login(client, username="must01", password="pw-abcdef")
    assert fresh.status_code == 200
    assert fresh.json()["user"]["must_change_pw"] is False  # 改密即清位


# ---- ③ F15 的结构性断言面 -----------------------------------------------------


def test_no_endpoint_accepts_an_identity_parameter(client):
    """★ **除 login 与 `/api/admin/*` 外，没有任何端点接受 `user_id` / `username` / `role` 入参。**

    这是 F15「以 A 的令牌访问 B 的资源」的**结构性**断言面：越权不是"记得校验"的问题，
    而是**没有可越权的入参**。若将来有人给某端点加上 `user_id` 查询参数，这条用例会立刻失败 ——
    那时正确的做法是回到"身份只来自 `Authorization`"，而不是放宽这条断言。

    ★ **2026-10-20 的唯一例外**：`/api/admin/*` 的**管理入参**（`/users/{user_id}` · `?user=`）
      只存在于 `require_admin` 之后 —— 它管的就是别人。例外因此**不是放宽**：本用例同时断言
      这些路径**全部**挂在管理员闸门下（学生令牌一律 403 `forbidden`，见
      `test_api_admin.py::test_student_is_forbidden_on_every_admin_endpoint`）。
      学生可见面（`me/*` · `practice/*` · `problems`）仍然一个身份入参都没有。
    """
    spec = app.openapi()
    schemas = spec.get("components", {}).get("schemas", {})
    offenders: list[str] = []
    for path, methods in spec["paths"].items():
        if path == "/api/auth/login" or path.startswith("/api/admin/"):
            continue  # 登录必须收 username；管理端的管理入参由 role 闸门守着
        for method, operation in methods.items():
            names = {parameter["name"] for parameter in operation.get("parameters", [])}
            body = operation.get("requestBody")
            if body:
                ref = body["content"]["application/json"]["schema"].get("$ref", "")
                model = schemas.get(ref.rsplit("/", 1)[-1], {})
                names |= set(model.get("properties", {}))
            for forbidden in ("user_id", "username", "role"):
                if forbidden in names:
                    offenders.append(f"{method.upper()} {path} 接受 {forbidden}")
    assert not offenders, "；".join(offenders)


def test_forbidden_code_is_reserved_for_role_shortage():
    """`403 forbidden` 只留给"角色不足"（`docs/02 §6.3`）：`require_admin` 是它的唯一出处。

    ★ 本用例直接调用依赖函数断言"这个 code 的语义没有被别的事情借用"；
      `/api/admin/*` 的**端点行为**在 `test_api_admin.py` 里逐条断言（学生令牌 403 · 五个端点）。
    """
    student = auth.AuthContext(
        user_id="usr_000000000001", role="student", status="active",
        must_change_pw=False, token_hash="x", expires_at=0.0,
    )
    with pytest.raises(AppError) as caught:
        deps.require_admin(student)
    assert caught.value.code == "forbidden"

    admin = auth.AuthContext(
        user_id="usr_000000000002", role="admin", status="active",
        must_change_pw=False, token_hash="y", expires_at=0.0,
    )
    assert deps.require_admin(admin).role == "admin"
