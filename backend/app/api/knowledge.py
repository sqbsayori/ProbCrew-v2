"""知识图谱与公式检索 —— W4 的两个只读端点。

| 端点 | 认证 | 成功 | 失败 |
|---|---|---|---|
| `GET /api/knowledge/tree` | 是 | DAG 节点 + 规模统计 | 401 · 403 · 503（资产与 MANIFEST 不一致） |
| `GET /api/knowledge/formula` | 是 | 唯一 `formulaEntry` | 400 · 401 · 403 · 404 |

★ 本层只做查询参数与 HTTP 映射；文件读取和内容校验在 `learning/catalog.py`。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query

from app.api.deps import password_gate
from app.core.errors import AppError
from app.identity.auth import AuthContext
from app.learning import catalog

__all__ = ["router"]

router = APIRouter(tags=["知识"])


@router.get("/api/knowledge/tree")
def read_tree(
    context: AuthContext = Depends(password_gate("knowledge")),
) -> "dict[str, Any]":
    """返回全部知识点节点与服务端统计。"""
    return catalog.knowledge_tree()


@router.get("/api/knowledge/formula")
def read_formula(
    name: str = Query(min_length=1, max_length=64),
    kc: "str | None" = Query(default=None, pattern=r"^kc_[a-z0-9_]+$", max_length=64),
    context: AuthContext = Depends(password_gate("formula")),
) -> "dict[str, Any]":
    """按名称精确检索公式，可选按知识点筛选。"""
    item = catalog.find_formula(name, kc)
    if item is None:
        raise AppError("not_found", "未找到匹配的公式或定理")
    return item
