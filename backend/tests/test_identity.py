"""`identity/` 的用例 —— W1 · 最小身份闭环（P2）。

归属：`docs/05 §3`「其余测试文件按**被测模块**归属，**文件名与被测模块同名**」⇒ 本文件归**后端与数据**。

★ 本文件**不起 HTTP**：`identity/` 是纯函数层（禁 import fastapi，`docs/03 §3.1`），
  因此登录次序、两把锁、信息量不变量都在这里直接断言。接口层见 `test_api_auth.py`。
★ 凡涉及写路径，一律**读回断言**（`docs/02 §3.6`）。
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from app.core import db
from app.core.errors import AppError
from app.identity import audit, auth, tokens, users

REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACT = json.loads((REPO_ROOT / "contracts" / "user.schema.json").read_text(encoding="utf-8"))

GOOD_PASSWORD = "pw-123456"


@pytest.fixture()
def conn(tmp_path):
    connection = db.initialize(tmp_path / "identity.sqlite")
    yield connection
    connection.close()


@pytest.fixture()
def account(conn):
    """一个正常账号（``created_by='cli'`` —— 建号这件事在真实链路上只能由 CLI 做，F15）。"""
    return users.create_user(
        conn, username="stu01", password=GOOD_PASSWORD, display_name="学生一", created_by="cli"
    )


def _login(conn, username="stu01", password=GOOD_PASSWORD, now=None):
    return auth.authenticate(conn, username, password, now)


def _code(callable_) -> str:
    """取 AppError 的 code（失败时直接断言"是哪种失败"，而不是断言状态码字符串）。"""
    with pytest.raises(AppError) as caught:
        callable_()
    return caught.value.code


# ---- ① N6：只存哈希 ----------------------------------------------------------


def test_password_is_stored_as_bcrypt_hash_only(conn, account, tmp_path):
    """口令只存 bcrypt 哈希 —— 且**明文不出现在库文件的任何字节里**。

    ★ 第二半是这条纪律真正的形式：只断言"列里是哈希"会漏掉"明文被随手写进别的列/别的表"。
    """
    row = conn.execute(
        "SELECT password_hash FROM student_user WHERE user_id = ?", (account["user_id"],)
    ).fetchone()
    assert row["password_hash"].startswith("$2")
    assert GOOD_PASSWORD not in row["password_hash"]

    for artifact in tmp_path.glob("identity.sqlite*"):  # 含 -wal / -shm（WAL 模式下明文也可能落在那儿）
        assert GOOD_PASSWORD.encode() not in artifact.read_bytes(), f"{artifact.name} 里出现了口令明文"


def test_public_view_never_leaks_internal_columns(conn, account):
    """R-M：`to_public` 是**白名单**投影，内部列不可能出现在响应形状里。"""
    public = users.to_public(
        users.find_by_id(conn, account["user_id"])  # type: ignore[arg-type]
    )
    for forbidden in ("password_hash", "failed_count", "locked_until"):
        assert forbidden not in public


def test_to_public_matches_contract_properties(conn, account):
    """★ 白名单与**契约**逐字对齐：输出键 ⊆ `user.schema.json` 顶层 `properties`。

    契约是 `additionalProperties: false` ⇒ 多输出一个键就是违约。因此这条断言不是"风格检查"，
    它是"响应体合法"的机器形式；将来加列而忘了收窄 `to_public` 时，它会失败。
    """
    allowed = set(CONTRACT["properties"])
    required = set(CONTRACT["required"])
    public = users.to_public(users.find_by_id(conn, account["user_id"]))  # type: ignore[arg-type]
    assert set(public) <= allowed
    assert required <= set(public), "契约 required 的字段必须全部出现"
    assert isinstance(public["must_change_pw"], bool)


# ---- ② 令牌 ------------------------------------------------------------------


def test_token_is_stored_as_sha256_and_ttl_is_seven_days(conn, account):
    """库里只存 sha256 · 有效期 7 天 · 明文长度在契约区间内。"""
    now = 1_800_000_000.0
    session = _login(conn, now=now)
    stored = conn.execute("SELECT token_hash, expires_at FROM student_token").fetchone()

    assert stored["token_hash"] == tokens.hash_token(session.token)
    assert session.token not in stored["token_hash"]
    assert 32 <= len(session.token) <= 128
    assert stored["expires_at"] == now + tokens.TOKEN_TTL_SECONDS
    assert tokens.TOKEN_TTL_SECONDS == 7 * 24 * 3600
    assert session.expires_at == stored["expires_at"]


def test_revoked_and_expired_tokens_all_resolve_to_none(conn, account):
    """撤销 / 过期 / 无此行 ⇒ **一律 None**（`docs/02 §6.3`：不区分，避免成为可探测事实）。"""
    session = _login(conn)
    assert tokens.resolve_token(conn, session.token) is not None
    assert tokens.resolve_token(conn, "not-a-real-token") is None

    plaintext, _ = tokens.issue_token(conn, account["user_id"])
    assert tokens.revoke_token(conn, plaintext) == 1
    assert tokens.resolve_token(conn, plaintext) is None

    assert tokens.resolve_token(conn, session.token, now=time.time() + tokens.TOKEN_TTL_SECONDS + 1) is None


def test_change_password_revokes_every_token(conn, account):
    """改密 ⇒ **全部**令牌失效（含本次，契约原文："改完密码旧令牌还能用，等于没改"）。"""
    first = _login(conn)
    second = _login(conn)
    context = auth.resolve_bearer(conn, f"Bearer {first.token}")
    assert context is not None

    revoked = auth.change_password(conn, context, GOOD_PASSWORD, "pw-654321")
    assert revoked == 2
    assert tokens.active_token_count(conn, account["user_id"]) == 0
    for stale in (first.token, second.token):
        assert auth.resolve_bearer(conn, f"Bearer {stale}") is None
    assert _login(conn, password="pw-654321").token  # 新口令可用


def test_password_byte_upper_bound(conn, account):
    """★ 上界按**字节**：25 个中文字符 = 75 字节 ⇒ 400 `invalid_argument`（而 Pydantic 会放行）。"""
    assert _code(lambda: _login(conn, password="密" * 25)) == "invalid_argument"
    assert _code(lambda: users.hash_password("密" * 25)) == "invalid_argument"
    # 24 个中文字符 = 72 字节，正好在上界内 ⇒ 不该被拦（拦住才是假的严格）。
    assert len(("密" * 24).encode("utf-8")) == 72
    assert _code(lambda: _login(conn, password="密" * 24)) == "bad_credentials"


# ---- ③ 信息量不变量 ----------------------------------------------------------


def test_information_invariant_same_code_and_comparable_time(conn, account):
    """★ **在口令通过之前，一切失败都返回同一个 `401 bad_credentials`，且都烧等长的时间。**

    四条断言逐条落地（`docs/02 §4.1` · §8-4b）：
    ① 账号不存在与口令错 → **同一 code / 同一状态码**；
    ② 两条路径耗时**同阶**（这里断的是比值 —— 只抓"差一个数量级"这种真泄露）；
    ③ **已禁用账号 + 错误口令 → 401**（不是 403）：否则"禁用"这个状态无需口令即可探到；
    ④ 已脱敏账号与空哈希走同一条路。
    """
    start = time.perf_counter()
    missing = _code(lambda: _login(conn, username="nobody99"))
    missing_elapsed = time.perf_counter() - start

    start = time.perf_counter()
    wrong = _code(lambda: _login(conn, password="wrong-password"))
    wrong_elapsed = time.perf_counter() - start

    assert missing == wrong == "bad_credentials"
    slow, fast = max(missing_elapsed, wrong_elapsed), min(missing_elapsed, wrong_elapsed)
    assert fast > 0
    assert slow / fast < 3, f"存在性可被耗时区分：{missing_elapsed:.4f}s vs {wrong_elapsed:.4f}s"

    disabled = users.create_user(conn, username="dis01", password=GOOD_PASSWORD, created_by="cli")
    conn.execute("UPDATE student_user SET status = 'disabled' WHERE user_id = ?", (disabled["user_id"],))
    conn.commit()
    assert _code(lambda: _login(conn, username="dis01", password="wrong-password")) == "bad_credentials"


# ---- ④ 两把锁 ----------------------------------------------------------------


def test_five_failures_lock_but_never_leak_before_password_check(conn, account):
    """★ 失败 5 次 ⇒ 锁定；**但第 5 次本身仍返回 401**（锁定只在口令通过之后才可能被看到）。

    这条断言是 `docs/02 §4.1` 的 ③④ 次序的机器形式：若把"锁定中 → 423"提到口令校验**之前**，
    则"不存在的用户名"给 401、而"已锁定账号"给 423 ⇒ **无需口令即可枚举账号是否存在**。
    因此这里同时断言：口令**错误**时永远拿不到 423。
    """
    for _ in range(users.LOCK_THRESHOLD):
        assert _code(lambda: _login(conn, password="wrong-password")) == "bad_credentials"

    now = time.time()
    assert _code(lambda: _login(conn, now=now)) == "locked"  # 口令正确 ⇒ 才看得到锁定
    assert _code(lambda: _login(conn, password="wrong-password", now=now)) == "bad_credentials"

    # 60 秒后自动解除（`docs/02 §5.3`）。
    assert _login(conn, now=now + users.LOCK_SECONDS + 1).token
    assert users.find_by_id(conn, account["user_id"])["failed_count"] == 0  # 成功即清零
    assert _code(lambda: _login(conn, password="wrong-password")) == "bad_credentials"


def test_disabled_account_403_only_after_password_verified(conn, account):
    """已禁用账号：口令正确 ⇒ 403 `account_disabled`（且**不**记成功登录）。"""
    conn.execute("UPDATE student_user SET status = 'disabled' WHERE user_id = ?", (account["user_id"],))
    conn.commit()
    assert _code(lambda: _login(conn)) == "account_disabled"
    assert users.find_by_id(conn, account["user_id"])["last_login_at"] is None


def test_must_change_pw_gate_allowlist(conn):
    """`must_change_pw` 闸门：放行集合内可用，集合外一律 403 `must_change_pw`（**不复用 forbidden**）。"""
    created = users.create_user(
        conn, username="must01", password=GOOD_PASSWORD, created_by="cli", must_change_pw=True
    )
    session = _login(conn, username="must01")
    assert session.user["must_change_pw"] is True
    context = auth.resolve_bearer(conn, f"Bearer {session.token}")
    assert context is not None and context.must_change_pw is True

    for area in ("login", "logout", "password", "me"):
        auth.assert_password_changed(context, area)  # 不抛
    for area in ("practice", "grade", "chat", "wrong"):
        assert _code(lambda: auth.assert_password_changed(context, area)) == "must_change_pw"

    # 改密 ⇒ 清位（`docs/02 §5.3` 的自环）
    auth.change_password(conn, context, GOOD_PASSWORD, "pw-abcdef")
    assert users.find_by_id(conn, created["user_id"])["must_change_pw"] == 0


# ---- ⑤ 锚点：越权与脱敏 -------------------------------------------------------


def test_resolve_bearer_rejects_malformed_headers(conn, account):
    """没有 / 空 / 前缀不对 / 明文不对 ⇒ 一律 None（调用方据此给 401 `unauthenticated`）。"""
    session = _login(conn)
    for header in (None, "", "token-only", "Basic abc", "Bearer ", "Bearer not-a-token"):
        assert auth.resolve_bearer(conn, header) is None
    assert auth.resolve_bearer(conn, f"Bearer {session.token}") is not None
    assert auth.resolve_bearer(conn, f"bearer {session.token}") is not None  # 大小写不敏感


def test_anonymize_user_revokes_tokens_and_keeps_audit(conn, account):
    """F16 的**账号侧**：脱敏后口令全废、令牌全撤；**审计保留**（`docs/02 §3.1` 的裁定）。"""
    session = _login(conn)
    audit.write_audit(
        conn,
        actor_id=account["user_id"],
        actor_role="student",
        action="delete_own_data",
        target_id=account["user_id"],
        detail={"deleted_rows": {"attempt": 3}},
    )

    revoked = users.anonymize_user(conn, account["user_id"])
    assert revoked == 1

    row = users.find_by_id(conn, account["user_id"])
    assert row is not None
    assert row["status"] == "deleted"
    assert row["password_hash"] == ""
    assert row["username"].startswith("deleted_")
    assert auth.resolve_bearer(conn, f"Bearer {session.token}") is None
    assert _code(lambda: _login(conn, username="stu01")) == "bad_credentials"  # 原用户名已不可登录
    assert audit.count_rows(conn, target_id=account["user_id"]) == 1  # ★ 审计**没有**被删

    # 原 username 可以再次被注册（契约的复用规则，不引入"退休用户名表"）。
    assert users.create_user(conn, username="stu01", password=GOOD_PASSWORD, created_by="cli")


# ---- ⑥ 审计的三条构造约束 -----------------------------------------------------


def test_audit_enforces_action_enum_and_sensitive_keys(conn, account):
    """审计：动作必须在契约的 8 个取值内 · `detail` 拒绝敏感键名 · **登录成功不写审计**。"""
    assert audit.AUDIT_ACTIONS == set(
        CONTRACT["definitions"]["auditRow"]["properties"]["action"]["enum"]
    )
    audit_id = audit.write_audit(
        conn,
        actor_id=account["user_id"],
        actor_role="student",
        action="delete_own_data",
        target_id=account["user_id"],
    )
    assert audit_id.startswith("aud_") and len(audit_id) == 16

    with pytest.raises(ValueError):
        audit.write_audit(
            conn,
            actor_id=account["user_id"],
            actor_role="student",
            action="delete_own_data",
            target_id=account["user_id"],
            detail={"token": "must-not-be-stored"},
        )
    # ★ "登录"不是契约里的动作 —— 想"顺便留个痕"就必须走 docs/04 §6 的提案，不能自加枚举值。
    with pytest.raises(ValueError):
        audit.write_audit(
            conn, actor_id=account["user_id"], actor_role="student", action="login"
        )
