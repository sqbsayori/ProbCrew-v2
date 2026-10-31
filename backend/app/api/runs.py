"""运行快照 —— W3 的 `GET /api/runs/{id}`（F2 的第二处落点）。

★ run 只在**进程内存**（`docs/02 §6.1` 已裁定）：查不到 / 不是自己的 ⇒ 404 `not_found`，
  不做持久化、也不区分"不存在"与"别人的"（`docs/02 §6.3` 的越权语义）。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Path

from app.api.deps import password_gate
from app.core.errors import AppError
from app.graph.runner import RUNS
from app.identity.auth import AuthContext

__all__ = ["router"]

router = APIRouter(tags=["运行"])


@router.get("/api/runs/{id}")
def read_run(
    id: str = Path(min_length=1, max_length=64),
    context: AuthContext = Depends(password_gate("run")),
) -> "dict[str, Any]":
    """返回 `runSnapshot`；不存在或不属于当前用户 ⇒ 404。"""
    snapshot = RUNS.snapshot_for(id, context.user_id)
    if snapshot is None:
        raise AppError("not_found", "运行不存在或已过期")
    return snapshot
