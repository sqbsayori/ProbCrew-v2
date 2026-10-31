"""W4 知识闭环 —— tree / formula 接口与资产一致性。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft7Validator
from referencing import Registry, Resource

from app.core import db
from app.identity import users
from app.learning import catalog
from app.main import app

REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACTS = REPO_ROOT / "contracts"
CONTENT = REPO_ROOT / "content"
PASSWORD = "pw-123456"


def _registry() -> Registry:
    registry = Registry()
    for path in CONTRACTS.glob("*.json"):
        schema = json.loads(path.read_text(encoding="utf-8"))
        registry = registry.with_resource(schema["$id"], Resource.from_contents(schema))
    return registry


KNOWLEDGE = json.loads((CONTRACTS / "knowledge.schema.json").read_text(encoding="utf-8"))
VALIDATOR = Draft7Validator(KNOWLEDGE, registry=_registry())


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBCREW_DB_PATH", str(tmp_path / "knowledge.sqlite"))
    with TestClient(app) as test_client:
        yield test_client


def _seed_and_login(client):
    conn = db.initialize()
    try:
        users.create_user(
            conn, username="know01", password=PASSWORD, display_name="学生",
            role="student", created_by="cli",
        )
    finally:
        conn.close()
    response = client.post("/api/auth/login", json={"username": "know01", "password": PASSWORD})
    assert response.status_code == 200, response.text
    return response.json()["token"]


def _auth(token: str) -> "dict[str, str]":
    return {"Authorization": f"Bearer {token}"}


def test_tree_is_authenticated_contract_shaped_and_acyclic(client):
    assert client.get("/api/knowledge/tree").status_code == 401
    token = _seed_and_login(client)
    response = client.get("/api/knowledge/tree", headers=_auth(token))
    assert response.status_code == 200, response.text
    body = response.json()
    VALIDATOR.validate(body)
    assert body["counts"]["nodes"] == len(body["nodes"]) == 81
    assert body["counts"]["chapters"] == 7
    ids = {node["kc_id"] for node in body["nodes"]}
    assert all(set(node["depends_on"]) <= ids for node in body["nodes"])
    visiting: set[str] = set()
    visited: set[str] = set()
    graph = {node["kc_id"]: node["depends_on"] for node in body["nodes"]}

    def visit(node_id: str) -> None:
        assert node_id not in visiting, f"cycle at {node_id}"
        if node_id in visited:
            return
        visiting.add(node_id)
        for dependency in graph[node_id]:
            visit(dependency)
        visiting.remove(node_id)
        visited.add(node_id)

    for node_id in graph:
        visit(node_id)


def test_formula_exact_match_and_optional_kc_filter(client):
    token = _seed_and_login(client)
    hit = client.get(
        "/api/knowledge/formula",
        params={"name": "贝叶斯公式"},
        headers=_auth(token),
    )
    assert hit.status_code == 200, hit.text
    VALIDATOR.validate(hit.json())
    assert hit.json()["fm_id"] == "fm_bayes"
    assert "conditions" in hit.json()

    filtered = client.get(
        "/api/knowledge/formula",
        params={"name": "贝叶斯公式", "kc": "kc_conditional_probability"},
        headers=_auth(token),
    )
    assert filtered.status_code == 200
    mismatch = client.get(
        "/api/knowledge/formula",
        params={"name": "贝叶斯公式", "kc": "kc_anova"},
        headers=_auth(token),
    )
    assert mismatch.status_code == 404
    assert mismatch.json()["error"]["code"] == "not_found"
    missing = client.get(
        "/api/knowledge/formula",
        params={"name": "不存在的公式"},
        headers=_auth(token),
    )
    assert missing.status_code == 404


def test_formula_query_validation_and_manifest_recompute(client):
    token = _seed_and_login(client)
    invalid_kc = client.get(
        "/api/knowledge/formula",
        params={"name": "贝叶斯公式", "kc": "bad"},
        headers=_auth(token),
    )
    assert invalid_kc.status_code == 400
    too_long = client.get(
        "/api/knowledge/formula",
        params={"name": "x" * 65},
        headers=_auth(token),
    )
    assert too_long.status_code == 400
    manifest = json.loads((CONTENT / "MANIFEST.json").read_text(encoding="utf-8"))
    assert catalog.content_rev() == manifest["content_rev"]


def test_all_content_kc_ids_are_covered_by_dag():
    dag = json.loads((CONTENT / "dag.json").read_text(encoding="utf-8"))
    ids = {node["kc_id"] for node in dag["items"]}
    for asset in ("problems", "types", "mistakes", "formula"):
        document = json.loads((CONTENT / f"{asset}.json").read_text(encoding="utf-8"))
        used = {kc for item in document["items"] for kc in item.get("kc_ids", [])}
        assert used <= ids, f"{asset} 仍有 DAG 外知识点：{sorted(used - ids)}"
