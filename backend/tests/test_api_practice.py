"""刷题链路的接口用例 —— W2 判定闭环（F4 · F5 · F6 · F7 · F8 · F11 · F19）。

归属：`docs/05 §3` ⇒ **后端与数据**（被测对象是 `api/practice.py` · `api/me.py` 的接线
与 `learning/` 的读写）。文件名与被测模块同名（`test_api_practice.py`）。

★ 每条断言都**读回**（`docs/02 §3.6` · `README` 硬约束 5）：判完就查 `attempt` 视图、
查 `progress`、查错题本 —— 只断言 HTTP 200 的测试在旧仓漏掉过「写库没提交」的 bug。

★ 响应体一律**过契约**（`problem` / `pagination` / `gradeResult` / `hintResponse` / `mastery`）：
  契约是 `additionalProperties: false`，因此「多返回一个 `expected`」这类泄漏会当场变红。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft7Validator

from app.core import db
from app.identity import users
from app.main import app

REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACTS = REPO_ROOT / "contracts"
PROBLEMS = json.loads((REPO_ROOT / "content" / "problems.json").read_text(encoding="utf-8"))
PROBLEM_SCHEMA = json.loads((CONTRACTS / "problem.schema.json").read_text(encoding="utf-8"))
PAGINATION = json.loads((CONTRACTS / "pagination.schema.json").read_text(encoding="utf-8"))
ATTEMPT = json.loads((CONTRACTS / "attempt.schema.json").read_text(encoding="utf-8"))
HINT = json.loads((CONTRACTS / "hint.schema.json").read_text(encoding="utf-8"))
MASTERY = json.loads((CONTRACTS / "mastery.schema.json").read_text(encoding="utf-8"))
GRADE_SCHEMA = {"$ref": "#/definitions/gradeResult", "definitions": ATTEMPT["definitions"]}

GOOD_PASSWORD = "pw-123456"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """一个**独立库文件**的 HTTP 客户端（与 `test_api_auth.py` 同形）。"""
    monkeypatch.setenv("PROBCREW_DB_PATH", str(tmp_path / "w2.sqlite"))
    with TestClient(app) as test_client:
        yield test_client


def _seed(username: str = "stu01", *, must_change_pw: bool = False):
    conn = db.initialize()
    try:
        return users.create_user(
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


def _token(client: TestClient, username: str = "stu01") -> str:
    response = client.post(
        "/api/auth/login", json={"username": username, "password": GOOD_PASSWORD}
    )
    assert response.status_code == 200, response.text
    return response.json()["token"]


def _auth(token: str) -> "dict[str, str]":
    return {"Authorization": f"Bearer {token}"}


def _gradable_item(require_solution: bool = True) -> "dict":
    for item in PROBLEMS["items"]:
        if item["gradable"] and (item.get("solution") or not require_solution):
            return item
    raise AssertionError("题库里没有可判定题目")


def _not_gradable_item() -> "dict":
    for item in PROBLEMS["items"]:
        if not item["gradable"]:
            return item
    raise AssertionError("题库里没有不可判定题目")


# ---- ① 题目列表：形状 · 不泄露答案 · 分页 · kc 过滤 ---------------------------


def test_problems_shape_matches_contract_and_leaks_no_answer(client):
    """`GET /api/problems` 过 `pagination` + `problem` 契约，且**绝不带** `expected` / `solution`。"""
    _seed()
    token = _token(client)
    response = client.get("/api/problems?limit=5", headers=_auth(token))
    assert response.status_code == 200, response.text
    body = response.json()
    Draft7Validator(PAGINATION).validate(body)
    assert len(body["items"]) == 5
    for item in body["items"]:
        Draft7Validator(PROBLEM_SCHEMA).validate(item)
        assert "expected" not in item
        assert "solution" not in item
        assert "gradable" not in item


def test_problems_pagination_is_stable_and_kc_filter_works(client):
    """游标翻页不重复、不漏项；`?kc=` 只回该知识点的题。"""
    _seed()
    token = _token(client)
    first = client.get("/api/problems?limit=3", headers=_auth(token)).json()
    assert first["next_cursor"]
    second = client.get(
        f"/api/problems?limit=3&cursor={first['next_cursor']}", headers=_auth(token)
    ).json()
    ids_first = [item["prob_id"] for item in first["items"]]
    ids_second = [item["prob_id"] for item in second["items"]]
    assert not set(ids_first) & set(ids_second)

    kc = first["items"][0]["kc_ids"][0]
    filtered = client.get(f"/api/problems?kc={kc}&limit=100", headers=_auth(token)).json()
    assert filtered["items"]
    assert all(kc in item["kc_ids"] for item in filtered["items"])


def test_bad_cursor_is_rejected_not_silently_restarted(client):
    """游标不可解码 ⇒ 400 `invalid_argument`（不能悄悄回到第一页）。"""
    _seed()
    token = _token(client)
    response = client.get("/api/problems?cursor=not-a-cursor", headers=_auth(token))
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_argument"


# ---- ② 判定：正确 / 错误 / 不可判定 ------------------------------------------


def test_grade_correct_writes_attempt_and_returns_solution(client):
    """判对：`gradeResult` 可判定支 + 解析出口 + 读回进度（F4 · F7 · F11）。"""
    _seed()
    token = _token(client)
    item = _gradable_item()
    response = client.post(
        "/api/practice/grade",
        json={"item_id": item["prob_id"], "answer": item["expected"], "session_id": "s1", "duration_ms": 120},
        headers=_auth(token),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    Draft7Validator(GRADE_SCHEMA).validate(body)
    assert body["graded"] is True and body["correct"] is True
    assert body["attempt_no"] == 1 and body["hint_used"] == 0
    assert body["solution"] == item["solution"]

    progress = client.get("/api/me/progress", headers=_auth(token)).json()
    Draft7Validator(MASTERY).validate(progress)
    assert progress["total_attempts"] == 1
    assert sum(topic["attempts"] for topic in progress["topics"]) == len(item["kc_ids"])
    assert progress["weak"] == {"available": False, "note": "数据不足，暂不显示薄弱点"}


def test_grade_wrong_then_wrong_book_reads_it_back(client):
    """判错 ⇒ 错题本跨会话读回（F5 · F8）；每条过 `attempt` 契约。"""
    _seed()
    token = _token(client)
    item = _gradable_item()
    response = client.post(
        "/api/practice/grade",
        json={"item_id": item["prob_id"], "answer": "__definitely_wrong__", "session_id": "s1"},
        headers=_auth(token),
    )
    body = response.json()
    Draft7Validator(GRADE_SCHEMA).validate(body)
    assert body["graded"] is True and body["correct"] is False

    wrong = client.get("/api/me/wrong", headers=_auth(token)).json()
    Draft7Validator(PAGINATION).validate(wrong)
    assert len(wrong["items"]) == 1
    row = wrong["items"][0]
    Draft7Validator(ATTEMPT).validate(row)
    assert row["item_id"] == item["prob_id"]
    assert row["correct"] is False
    assert row["attempt_no"] == 1 and row["hint_used_total"] == 0


def test_grade_not_gradable_writes_attempt_but_not_mastery(client):
    """`gradable=false`：显式降级、仍写 attempt（`correct` 为 null）、**掌握度不累加**。"""
    _seed()
    token = _token(client)
    item = _not_gradable_item()
    response = client.post(
        "/api/practice/grade",
        json={"item_id": item["prob_id"], "answer": "随便写", "session_id": "s1"},
        headers=_auth(token),
    )
    body = response.json()
    Draft7Validator(GRADE_SCHEMA).validate(body)
    assert body["graded"] is False and body["correct"] is None
    assert body["grader"] == "not_gradable" and body["solution"] is None

    progress = client.get("/api/me/progress", headers=_auth(token)).json()
    assert progress["total_attempts"] == 1
    assert progress["topics"] == []
    assert client.get("/api/me/wrong", headers=_auth(token)).json()["items"] == []


def test_client_supplied_hint_used_is_ignored(client):
    """请求体里的 `hint_used` **忽略、不采信**（`docs/02 §4.3-6`）。"""
    _seed()
    token = _token(client)
    item = _gradable_item()
    response = client.post(
        "/api/practice/grade",
        json={"item_id": item["prob_id"], "answer": item["expected"], "session_id": "s1", "hint_used": 9},
        headers=_auth(token),
    )
    assert response.status_code == 200, response.text
    assert response.json()["hint_used"] == 0


def test_grade_unknown_item_is_404(client):
    _seed()
    token = _token(client)
    response = client.post(
        "/api/practice/grade",
        json={"item_id": "prob_not_in_bank", "answer": "1", "session_id": "s1"},
        headers=_auth(token),
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


# ---- ③ 提示阶梯：服务端推进 · 第 3 级要已提交 ---------------------------------


def test_hint_ladder_advances_by_server_and_level3_needs_submission(client):
    """1 → 2 → （未提交时）409 → 提交后 3；`hint_used` 每次**精确 +1**（F6）。"""
    _seed()
    token = _token(client)
    item = _gradable_item()
    payload = {"item_id": item["prob_id"], "session_id": "s1"}

    first = client.post("/api/practice/hint", json=payload, headers=_auth(token))
    assert first.status_code == 200, first.text
    hint_schema = HINT["definitions"]["hintResponse"]
    Draft7Validator(hint_schema).validate(first.json())
    assert (first.json()["level"], first.json()["hint_used"]) == (1, 1)

    second = client.post("/api/practice/hint", json=payload, headers=_auth(token)).json()
    Draft7Validator(hint_schema).validate(second)
    assert (second["level"], second["hint_used"]) == (2, 2)

    blocked = client.post("/api/practice/hint", json=payload, headers=_auth(token))
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "conflict"

    client.post(
        "/api/practice/grade",
        json={"item_id": item["prob_id"], "answer": item["expected"], "session_id": "s1"},
        headers=_auth(token),
    )
    third = client.post("/api/practice/hint", json=payload, headers=_auth(token)).json()
    Draft7Validator(hint_schema).validate(third)
    assert (third["level"], third["hint_used"]) == (3, 3)
    assert third["content"]
    assert third["hint_used_total"] == 3


def test_hint_level3_requires_submission_in_same_session(client):
    """★ 2026-10-20 裁定（`docs/02 §11-18`）：第 3 级的判据是**本会话已提交**。

    旧判据（「该 `(user,item)` 有过任一次作答」）会让「重做一道做过的题」在**新会话**里
    连点三次就拿到完整解析 —— F6 的伦理约束被**正常路径**绕过。这里逐条钉住：
    ① 会话 `s1` 提交过 → ② 新会话 `s2` 取第 3 级仍 **409** → ③ `s2` 提交后才放行。
    """
    _seed()
    token = _token(client)
    item = _gradable_item()

    first = client.post(
        "/api/practice/grade",
        json={"item_id": item["prob_id"], "answer": item["expected"], "session_id": "s1"},
        headers=_auth(token),
    )
    assert first.status_code == 200, first.text

    payload = {"item_id": item["prob_id"], "session_id": "s2"}
    for expected_level in (1, 2):
        response = client.post("/api/practice/hint", json=payload, headers=_auth(token))
        assert response.status_code == 200, response.text
        assert response.json()["level"] == expected_level

    blocked = client.post("/api/practice/hint", json=payload, headers=_auth(token))
    assert blocked.status_code == 409, blocked.text
    assert blocked.json()["error"]["code"] == "conflict"

    client.post(
        "/api/practice/grade",
        json={"item_id": item["prob_id"], "answer": item["expected"], "session_id": "s2"},
        headers=_auth(token),
    )
    third = client.post("/api/practice/hint", json=payload, headers=_auth(token))
    assert third.status_code == 200, third.text
    assert (third.json()["level"], third.json()["hint_used"]) == (3, 3)


# ---- ④ 改密闸门与越权隔离 -----------------------------------------------------


def test_must_change_pw_blocks_learning_endpoints_but_allows_me(client):
    """`must_change_pw`：`/api/me` 放行、学习端点 403 `must_change_pw`（`docs/02 §5.3`）。"""
    _seed(must_change_pw=True)
    token = _token(client)
    assert client.get("/api/me", headers=_auth(token)).status_code == 200
    blocked = client.get("/api/problems", headers=_auth(token))
    assert blocked.status_code == 403
    assert blocked.json()["error"]["code"] == "must_change_pw"


def test_wrong_book_carries_stem_and_matches_contract(client):
    """错题本带 `stem`（`attempt.schema.json` v1.1 · 2026-10-20）—— 复习视图要能显示题干。

    ★ `stem` 是**视图字段**：来自 `content/problems.json`，不落 `student_attempt` 行
      ⇒ 断言「与资产逐字相同」，而不是「某个默认值」。
    """
    _seed()
    token = _token(client)
    item = _gradable_item()
    client.post(
        "/api/practice/grade",
        json={"item_id": item["prob_id"], "answer": "__wrong__", "session_id": "s1"},
        headers=_auth(token),
    )
    response = client.get("/api/me/wrong", headers=_auth(token))
    assert response.status_code == 200, response.text
    body = response.json()
    Draft7Validator(PAGINATION).validate(body)
    assert body["next_cursor"] is None
    assert body["items"][0]["item_id"] == item["prob_id"]
    assert body["items"][0]["stem"] == item["stem"]
    # ★ `additionalProperties: false` 的根对象 + 新增可选字段 ⇒ 这条校验同时证明
    #   `stem` 已在契约里（否则它会当场变红）。
    Draft7Validator(ATTEMPT).validate(body["items"][0])


def test_wrong_book_is_per_user(client):
    """学生 A 的错题不会出现在 B 的错题本里（F15 —— 身份只来自令牌）。"""
    _seed("stu01")
    _seed("stu02")
    token_a = _token(client, "stu01")
    token_b = _token(client, "stu02")
    item = _gradable_item()
    client.post(
        "/api/practice/grade",
        json={"item_id": item["prob_id"], "answer": "__wrong__", "session_id": "s1"},
        headers=_auth(token_a),
    )
    assert len(client.get("/api/me/wrong", headers=_auth(token_a)).json()["items"]) == 1
    assert client.get("/api/me/wrong", headers=_auth(token_b)).json()["items"] == []
    assert client.get("/api/me/progress", headers=_auth(token_b)).json()["total_attempts"] == 0


