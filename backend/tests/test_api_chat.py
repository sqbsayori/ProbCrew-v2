"""W3 答疑接线用例 —— `POST /api/chat` · `GET /api/runs/{id}` · model / notice / qa。

★ 不是"HTTP 200 就算过"：每条都**读回**流水 / 快照 / 契约形状
  （`docs/02 §3.6` 的同一条纪律）。
★ 假 provider 只替换 `tools.llm._TRANSPORT` —— 产品路径不含 mock；
  这里替换的是"出网那一段"，端点校验、帧构造、落库都走真实现。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft7Validator
from referencing import Registry, Resource

from app.core import db
from app.graph import runner
from app.identity import users
from app.main import app
from app.tools import llm

REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACTS = REPO_ROOT / "contracts"

GOOD_PASSWORD = "pw-123456"
REMOTE = {
    "provider": "deepseek",
    "model": "deepseek-chat",
    "base_url": "https://api.deepseek.com/v1",
}


def _registry() -> Registry:
    registry = Registry()
    for path in CONTRACTS.glob("*.json"):
        schema = json.loads(path.read_text(encoding="utf-8"))
        registry = registry.with_resource(
            schema["$id"], Resource.from_contents(schema)
        )
    return registry


EVENTS = json.loads((CONTRACTS / "events.schema.json").read_text(encoding="utf-8"))
EVENT_VALIDATOR = Draft7Validator(EVENTS, registry=_registry())
CHAT = json.loads((CONTRACTS / "chat.schema.json").read_text(encoding="utf-8"))
QA_LOG = json.loads((CONTRACTS / "qa-log.schema.json").read_text(encoding="utf-8"))
USER = json.loads((CONTRACTS / "user.schema.json").read_text(encoding="utf-8"))
PAGINATION = json.loads((CONTRACTS / "pagination.schema.json").read_text(encoding="utf-8"))
HINT = json.loads((CONTRACTS / "hint.schema.json").read_text(encoding="utf-8"))


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBCREW_DB_PATH", str(tmp_path / "w3.sqlite"))
    with TestClient(app) as test_client:
        yield test_client


def _seed(username: str = "stu01"):
    conn = db.initialize()
    try:
        return users.create_user(
            conn,
            username=username,
            password=GOOD_PASSWORD,
            display_name="学生",
            role="student",
            created_by="cli",
        )
    finally:
        conn.close()


def _token(client: TestClient, username: str = "stu01") -> str:
    response = client.post(
        "/api/auth/login", json={"username": username, "password": GOOD_PASSWORD}
    )
    assert response.status_code == 200, response.text
    return response.json()["token"]


def _auth(token: str, *, key: "str | None" = "test-key") -> "dict[str, str]":
    headers = {"Authorization": f"Bearer {token}"}
    if key is not None:
        headers["X-Model-Key"] = key
    return headers


class FakeTransport:
    """按系统提示区分 route / solve 两跳，返回可解析的结构。"""

    def __init__(
        self,
        *,
        solve_text: "str | None" = None,
        status: int = 200,
        decomposition: "dict | None" = None,
    ) -> None:
        self.requests = 0
        self.bodies: "list[str]" = []
        self.solve_text = solve_text
        self.status = status
        self.decomposition = decomposition

    def __call__(self, request, *, timeout_s, max_bytes):
        self.requests += 1
        self.bodies.append(request.body.decode("utf-8"))
        if self.status != 200:
            return llm.TransportResponse(status=self.status, body=b"")
        body = json.loads(request.body.decode("utf-8"))
        system = body["messages"][0]["content"]
        if "归到下面四类" in system:
            text = "solve"
        else:
            payload = {
                "title": "示例",
                "answer": "1/2",
                "expression": "1/2",
                "steps": [
                    {"explanation": "写样本空间", "formula": "Ω"},
                    {"explanation": "取有利事件", "formula": "A"},
                    {"explanation": "按定义求比", "formula": "1/2"},
                ],
            }
            if self.decomposition is not None:
                payload["decomposition"] = self.decomposition
            text = self.solve_text or json.dumps(payload, ensure_ascii=False)
        payload = json.dumps(
            {"model": "fake", "choices": [{"message": {"content": text}}]},
            ensure_ascii=False,
        ).encode("utf-8")
        return llm.TransportResponse(status=200, body=payload)


def _frames(text: str) -> "list[dict]":
    frames = []
    for line in text.splitlines():
        if line.startswith("data: "):
            frames.append(json.loads(line[6:]))
    return frames


def test_chat_streams_contract_frames_and_reads_back_run_and_qa(client, monkeypatch):
    _seed()
    monkeypatch.setattr(llm, "_TRANSPORT", FakeTransport())
    token = _token(client)

    response = client.post(
        "/api/chat",
        json={"query": "抛硬币正面概率？", "endpoint": REMOTE},
        headers=_auth(token),
    )
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/event-stream")

    frames = _frames(response.text)
    assert [frame["seq"] for frame in frames] == list(range(1, len(frames) + 1))
    for frame in frames:
        EVENT_VALIDATOR.validate(frame)
    assert frames[-1]["type"] == "done"
    assert frames[-1]["payload"]["status"] == "ok"
    assert frames[-1]["payload"]["final_answer"] == "1/2"
    artifact = next(frame for frame in frames if frame["type"] == "artifact")
    assert artifact["payload"]["kind"] == "solution"

    run_id = frames[0]["payload"]["run_id"]
    snapshot = client.get(f"/api/runs/{run_id}", headers=_auth(token))
    assert snapshot.status_code == 200, snapshot.text
    body = snapshot.json()
    assert body["status"] == "done"
    # F2 两处逐值一致：快照的 steps 与解答事件是同一份数据。
    assert body["steps"] == artifact["payload"]["payload"]["steps"]

    qa = client.get("/api/me/qa", headers=_auth(token))
    assert qa.status_code == 200, qa.text
    Draft7Validator(PAGINATION).validate(qa.json())
    item = qa.json()["items"][0]
    Draft7Validator(QA_LOG).validate(item)
    assert item["provider"] == "deepseek" and item["egress"] == "remote"
    assert item["answer"] == "1/2"


def test_chat_failure_keeps_a_qa_row_with_null_answer(client, monkeypatch):
    _seed()
    monkeypatch.setattr(llm, "_TRANSPORT", FakeTransport(status=500))
    token = _token(client)

    response = client.post(
        "/api/chat",
        json={"query": "坏回答", "endpoint": REMOTE},
        headers=_auth(token),
    )
    assert response.status_code == 200
    frames = _frames(response.text)
    assert frames[-1]["type"] == "error"

    qa = client.get("/api/me/qa", headers=_auth(token)).json()["items"][0]
    assert qa["answer"] is None


def test_chat_without_a_key_is_503_and_writes_no_qa_row(client):
    _seed()
    token = _token(client)
    response = client.post(
        "/api/chat",
        json={"query": "没 key", "endpoint": REMOTE},
        headers=_auth(token, key=None),
    )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "model_key_missing"
    assert client.get("/api/me/qa", headers=_auth(token)).json()["items"] == []


@pytest.mark.parametrize(
    ("kind", "ref_id"),
    [
        ("problem", "prob_bayes_0012"),
        ("kc", "kc_bayes_formula"),
        ("corpus", "src_demo_001"),
    ],
)
def test_f1_context_text_reaches_the_model_payload(client, monkeypatch, kind, ref_id):
    _seed()
    transport = FakeTransport()
    monkeypatch.setattr(llm, "_TRANSPORT", transport)
    token = _token(client)
    marker = f"F1_CONTEXT_{kind}_9f3a"
    response = client.post(
        "/api/chat",
        json={
            "query": "请结合参考资料解释",
            "context": {"text": marker, "refs": [{"kind": kind, "id": ref_id}]},
            "endpoint": REMOTE,
        },
        headers=_auth(token),
    )
    assert response.status_code == 200
    assert len(transport.bodies) == 2
    assert all(marker in body for body in transport.bodies)
    resolved = _frames(response.text)[1]
    assert resolved["type"] == "context.resolved"
    assert resolved["payload"]["context"]["text"] == marker
    assert resolved["payload"]["reason"] is None


def test_f1_missing_context_still_degrades_explicitly(client, monkeypatch):
    _seed()
    monkeypatch.setattr(llm, "_TRANSPORT", FakeTransport())
    token = _token(client)
    response = client.post(
        "/api/chat",
        json={"query": "没有上下文", "endpoint": REMOTE},
        headers=_auth(token),
    )
    resolved = _frames(response.text)[1]
    assert resolved["payload"]["context"] is None
    assert resolved["payload"]["reason"]


def test_cancelled_run_snapshot_is_public_contract_valid(client):
    user = _seed()
    token = _token(client)
    runner.RUNS.start(run_id="qa_cancelled_contract", session_id="ses_1", owner_id=user["user_id"])
    runner.RUNS.finish("qa_cancelled_contract", status=runner.STATUS_CANCELLED)
    snap = client.get("/api/runs/qa_cancelled_contract", headers=_auth(token))
    assert snap.status_code == 200
    Draft7Validator(EVENTS["definitions"]["runSnapshot"], registry=_registry()).validate(snap.json())
    assert snap.json()["status"] == "cancelled"


def test_run_snapshot_is_owned_by_the_token_user(client, monkeypatch):
    _seed("stu01")
    _seed("stu02")
    monkeypatch.setattr(llm, "_TRANSPORT", FakeTransport())
    owner = _token(client, "stu01")
    other = _token(client, "stu02")

    response = client.post(
        "/api/chat",
        json={"query": "谁的 run", "endpoint": REMOTE},
        headers=_auth(owner),
    )
    run_id = _frames(response.text)[0]["payload"]["run_id"]

    assert client.get(f"/api/runs/{run_id}", headers=_auth(other)).status_code == 404
    assert client.get("/api/runs/run_missing", headers=_auth(owner)).status_code == 404
    assert client.get(f"/api/runs/{run_id}").status_code == 401


def test_notice_tracks_the_last_actual_egress_and_acks_once(client, monkeypatch):
    _seed()
    monkeypatch.setattr(llm, "_TRANSPORT", FakeTransport())
    token = _token(client)

    before = client.get("/api/me/notice", headers=_auth(token))
    assert before.status_code == 200
    Draft7Validator(USER["definitions"]["noticeStatus"]).validate(before.json())
    assert before.json()["egress"] is None and before.json()["ask"] is True

    client.post(
        "/api/chat",
        json={"query": "会外传吗", "endpoint": REMOTE},
        headers=_auth(token),
    )
    after = client.get("/api/me/notice", headers=_auth(token)).json()
    assert after["egress"] == "remote"
    assert "第三方" in after["message"] and "删除权边界" in after["message"]

    acked = client.post("/api/me/notice", json={"ack": True}, headers=_auth(token))
    assert acked.status_code == 200 and acked.json()["ask"] is False
    again = client.post("/api/me/notice", json={"ack": True}, headers=_auth(token))
    assert again.json()["ack_at"] == acked.json()["ack_at"]


def test_providers_and_ping_use_the_same_endpoint_check(client, monkeypatch):
    _seed()
    token = _token(client)
    providers = client.get("/api/model/providers", headers=_auth(token))
    assert providers.status_code == 200
    Draft7Validator(CHAT).validate(providers.json())

    monkeypatch.setattr(llm, "_TRANSPORT", FakeTransport())
    ok = client.post("/api/model/ping", json=REMOTE, headers=_auth(token))
    assert ok.status_code == 200 and ok.json()["ok"] is True
    Draft7Validator(CHAT).validate(ok.json())

    rejected = client.post(
        "/api/model/ping",
        json={**REMOTE, "base_url": "http://10.0.0.1/v1"},
        headers=_auth(token),
    )
    assert rejected.status_code == 503
    assert rejected.json()["error"]["code"] == "model_unreachable"


def test_qa_log_answer_is_nullable_in_the_schema(tmp_path):
    conn = db.connect(tmp_path / "old.sqlite")
    conn.execute(
        """
        CREATE TABLE student_qa_log (
          qa_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, query TEXT NOT NULL,
          answer TEXT NOT NULL, provider TEXT, model TEXT,
          egress TEXT NOT NULL, created_at REAL NOT NULL
        )
        """
    )
    db.ensure_schema(conn)
    columns = {row["name"]: row for row in conn.execute("PRAGMA table_info(student_qa_log)")}
    assert columns["answer"]["notnull"] == 0
    conn.close()


def test_f17_decomposition_is_an_artifact_with_an_asset_model_id(client, monkeypatch):
    _seed()
    monkeypatch.setattr(
        llm,
        "_TRANSPORT",
        FakeTransport(
            decomposition={
                "known": "两个事件 A、B",
                "asked": "A 发生且 B 不发生",
                "model_id": "pt_event_operations",
            }
        ),
    )
    token = _token(client)
    response = client.post(
        "/api/chat",
        json={"query": "用 A、B 表示 A 发生且 B 不发生", "endpoint": REMOTE},
        headers=_auth(token),
    )
    frames = _frames(response.text)
    decomposition = next(
        frame
        for frame in frames
        if frame["type"] == "artifact" and frame["payload"]["kind"] == "decomposition"
    )
    Draft7Validator(HINT["definitions"]["decomposition"]).validate(
        decomposition["payload"]["payload"]
    )
    assert decomposition["payload"]["payload"]["model_id"] == "pt_event_operations"


def test_f17_invented_model_id_becomes_explicitly_null(client, monkeypatch):
    _seed()
    monkeypatch.setattr(
        llm,
        "_TRANSPORT",
        FakeTransport(
            decomposition={
                "known": "已知",
                "asked": "所求",
                "model_id": "pt_made_up",
            }
        ),
    )
    token = _token(client)
    response = client.post(
        "/api/chat",
        json={"query": "随便问问", "endpoint": REMOTE},
        headers=_auth(token),
    )
    decomposition = next(
        frame["payload"]["payload"]
        for frame in _frames(response.text)
        if frame["type"] == "artifact" and frame["payload"]["kind"] == "decomposition"
    )
    assert decomposition["model_id"] is None
    assert decomposition["note"]


def test_chat_loopback_endpoint_requires_single_deployment(client, monkeypatch):
    """★ 回归：chat 传下去的必须是**已校验**的 EndpointConfig，不能降回原始字典。

    2026-10-20 浏览器验收抓到的缺陷：ping 通过、chat 却在 `llm.call_model` 的
    二次校验里丢掉 loopback 许可 ⇒ 一律 `model_unreachable`。
    """
    _seed()
    monkeypatch.setattr(llm, "_TRANSPORT", FakeTransport())
    token = _token(client)
    local = {
        "provider": "custom",
        "model": "local-model",
        "base_url": "http://127.0.0.1:8899/v1",
    }

    monkeypatch.delenv("PROBCREW_DEPLOYMENT", raising=False)
    rejected = client.post(
        "/api/chat",
        json={"query": "hosted 形态", "endpoint": local},
        headers=_auth(token, key=None),
    )
    assert rejected.status_code == 503
    assert rejected.json()["error"]["code"] == "model_unreachable"

    monkeypatch.setenv("PROBCREW_DEPLOYMENT", "single")
    accepted = client.post(
        "/api/chat",
        json={"query": "single 形态", "endpoint": local},
        headers=_auth(token, key=None),
    )
    assert accepted.status_code == 200, accepted.text
    assert _frames(accepted.text)[-1]["type"] == "done"
