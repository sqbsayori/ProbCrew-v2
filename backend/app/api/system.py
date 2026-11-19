"""`GET /api/health` —— **唯一无需登录的 API**（`docs/02 §4.1` 口径 6 · `docs/04 §3` 的系统行）。

| 端点 | 认证 | 成功 | 失败 |
|---|---|---|---|
| `GET /api/health` | **否** | 200 自检体（`contracts/registry.schema.json` 的 health 分支） | 无（单项不健康降级为 `degraded`，仍 200） |

★ **它不是"多一条接口"，而是清单里本来就该有的那条**：`docs/04 §3` 的 25 条 path 覆盖表把
`GET /api/health` 记为 C14，因此本轮实现它**不改 path 数量**、不需要动 `contracts/**` 与 `docs/**`。

★ **形状的唯一真相在契约里**（`registry.schema.json` v1.2 的第二个 `oneOf` 分支）：
`status` / `db` / `corpus` / `asset_rev` / `backup{last_success_at,last_result,last_failure_at}`。
本文件不复制字段说明，只聚合 —— 聚合规则写在与本文件同域的 `app/core/health.py`。

★ **本文件必须保持薄**（`docs/03 §3.2` 第 1 条 · `verify.sh` 的 `api_thinness`）：不写 SQL、单函数 ≤ 40 行。
所以四个探针在 `core/health.py`，这里只做"取 → 聚合 → 返回"。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from app.core import health
from app.core.errors import AppError
from app.learning import catalog

__all__ = ["router"]

router = APIRouter(tags=["系统"])


def _asset_rev() -> "str | None":
    """当前 `content_rev`；资产缺失/不一致时按契约返回 `null`（**不抛 5xx**，否则探针只能看到一个错误页）。

    ★ 捕获 `AppError` 是刻意的：`catalog.content_rev()` 在 `MANIFEST` 对不上时抛
      `dependency_unavailable`（它是对的 —— 业务请求该被拒绝），但 health 的职责是**报告**这件事，
      因此把"资产不可复算"折成 `asset_rev=null` + `degraded`，而不是让端点返回 503。
    """
    try:
        return catalog.content_rev()
    except (AppError, OSError, KeyError, ValueError):
        return None


@router.get("/api/health")
def read_health() -> "dict[str, Any]":
    """只报本地事实：DB · L1 缓存 · 资产 `rev` · 备份新鲜度（不出网、不含用户信息）。"""
    db_state = health.db_status()
    corpus_state = health.corpus_status()
    asset_rev = _asset_rev()
    backup = health.backup_status()
    return {
        "status": health.aggregate_status(
            db=db_state, corpus=corpus_state, asset_rev=asset_rev, backup=backup
        ),
        "db": db_state,
        "corpus": corpus_state,
        "asset_rev": asset_rev,
        "backup": backup,
    }
