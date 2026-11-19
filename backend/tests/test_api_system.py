"""`GET /api/health` 的接口用例 —— B0.2 的判据面（`docs/06 §15.3` · `docs/04 §3` 的系统行）。

★ 本文件断言的是**契约**而不是"我们的形状"：期望值一律取自
  `contracts/registry.schema.json` 的 health 分支（`oneOf[1]`）与 `x-forbidden-fields`。
  「过 registry.schema.json 的 health 分支」是 B0.2 的原文判据，因此这里**直接拿契约当校验器**。

★ 三件必须钉住的事：
1. **免登录**（`docs/02 §4.1` 口径 6：除 `health` / `login` / 静态资源外全要登录）；
2. **四项可读**（db / corpus / asset_rev / backup）且**单项坏 ⇒ `degraded` 而不是 5xx**；
3. **不得出现** `model` / `model_status` / `model_endpoint` / `upstream` / `llm`
   —— 模型状态要一次出网调用，health 不在两个出网口里（`x-forbidden-fields`）。

★ 时延用"病态回归"口径（单次 < 500ms）：真正的 **10 路并发 < 100ms** 判据由
  `scripts/load_test.py` 在真实进程上实跑给出（N20 判据①），单元测试不冒充它。
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft7Validator

from app.core import db
from app.core.errors import AppError
from app.learning import catalog
from app.main import app

REPO_ROOT = Path(__file__).resolve().parents[2]
REGISTRY = json.loads((REPO_ROOT / "contracts" / "registry.schema.json").read_text(encoding="utf-8"))
HEALTH_SCHEMA = REGISTRY["oneOf"][1]
FORBIDDEN_FIELDS = REGISTRY["x-forbidden-fields"]["forbidden"]
VALIDATOR = Draft7Validator(HEALTH_SCHEMA)


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """独立库文件（`backend/data/` 是运行时目录，用例不许写进仓库）。"""
    monkeypatch.setenv("PROBCREW_DB_PATH", str(tmp_path / "health.sqlite"))
    with TestClient(app) as test_client:
        yield test_client


def _backup_file(tmp_path: Path) -> Path:
    return tmp_path / "backups" / "last_backup.json"


def _write_backup(tmp_path: Path, payload: "dict[str, object] | str") -> None:
    path = _backup_file(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    path.write_text(text, encoding="utf-8")


def test_health_is_public_and_matches_the_contract(client: TestClient) -> None:
    """免登录 200；响应**直接**过契约的 health 分支（不是"看着像"）。"""
    response = client.get("/api/health")
    assert response.status_code == 200, response.text
    body = response.json()
    VALIDATOR.validate(body)
    assert set(body) == {"status", "db", "corpus", "asset_rev", "backup"}
    assert body["status"] in ("ok", "degraded")
    assert body["db"] in ("ok", "error")
    assert body["corpus"] in ("ok", "missing")


def test_health_reports_no_model_fields(client: TestClient) -> None:
    """`x-forbidden-fields`：五个模型相关字段一个都不许出现（health 不出网）。"""
    body = client.get("/api/health").json()
    for field in FORBIDDEN_FIELDS:
        assert field not in body
    assert set(body["backup"]) == {"last_success_at", "last_result", "last_failure_at"}


def test_db_failure_degrades_instead_of_5xx(client: TestClient, monkeypatch) -> None:
    """库开不了 ⇒ `db=error` + `degraded`，仍是 200（探针要拿得到读数，而不是一个错误页）。"""

    def boom(*_args, **_kwargs):
        raise sqlite3.OperationalError("unable to open database file")

    monkeypatch.setattr(db, "initialize", boom)
    response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    VALIDATOR.validate(body)
    assert body["db"] == "error"
    assert body["status"] == "degraded"


def test_missing_corpus_degrades(client: TestClient, monkeypatch, tmp_path: Path) -> None:
    """L1 语料缺失 ⇒ `corpus=missing` + `degraded` —— 答疑仍可用，所以它不是 5xx（`docs/02 §8-13b`）。"""
    monkeypatch.setenv("PROBCREW_CORPUS_DIR", str(tmp_path / "no-corpus"))
    body = client.get("/api/health").json()
    assert body["corpus"] == "missing"
    assert body["status"] == "degraded"


def test_backup_three_states(client: TestClient, tmp_path: Path) -> None:
    """新鲜度三态：`never`（没排过）· `ok` · `failed`（读不出来 ⇒ fail-closed）。"""
    body = client.get("/api/health").json()
    assert body["backup"]["last_result"] == "never"
    assert body["backup"]["last_success_at"] is None

    _write_backup(
        tmp_path,
        {"last_success_at": "2026-11-19T02:00:05Z", "last_result": "ok", "last_failure_at": None},
    )
    ok = client.get("/api/health").json()
    VALIDATOR.validate(ok)
    assert ok["backup"]["last_result"] == "ok"
    assert ok["backup"]["last_success_at"] == "2026-11-19T02:00:05Z"

    _write_backup(tmp_path, "{ 这不是 JSON")
    broken = client.get("/api/health").json()
    VALIDATOR.validate(broken)          # 坏值不透传，仍是一份合法响应
    assert broken["backup"]["last_result"] == "failed"
    assert broken["status"] == "degraded"


def test_backup_rejects_non_utc_timestamps(client: TestClient, tmp_path: Path) -> None:
    """`last_success_at` 的 pattern 是 `…Z`：写坏了也**不许透传**成违约响应。"""
    _write_backup(
        tmp_path,
        {"last_success_at": "2026-11-19 02:00:05", "last_result": "ok", "last_failure_at": "x"},
    )
    body = client.get("/api/health").json()
    VALIDATOR.validate(body)
    assert body["backup"]["last_success_at"] is None
    assert body["backup"]["last_failure_at"] is None


def test_asset_rev_is_null_when_assets_cannot_be_recomputed(client: TestClient, monkeypatch) -> None:
    """资产与 MANIFEST 不一致 ⇒ `asset_rev=null` + `degraded`（业务侧仍由 `catalog` 拒绝，health 只报告）。"""

    def boom() -> str:
        raise AppError("dependency_unavailable", "内容资产与 MANIFEST 不一致")

    monkeypatch.setattr(catalog, "content_rev", boom)
    body = client.get("/api/health").json()
    VALIDATOR.validate(body)
    assert body["asset_rev"] is None
    assert body["status"] == "degraded"


def test_health_is_cheap_enough_for_a_probe(client: TestClient) -> None:
    """病态回归守卫：暖机后 20 次调用，最坏一次 < 500ms（真判据见 `scripts/load_test.py`）。"""
    client.get("/api/health")  # 暖机：首次调用可能要建表
    worst = 0.0
    for _ in range(20):
        started = time.perf_counter()
        assert client.get("/api/health").status_code == 200
        worst = max(worst, (time.perf_counter() - started) * 1000)
    assert worst < 500, f"单次 health 用了 {worst:.1f}ms"
