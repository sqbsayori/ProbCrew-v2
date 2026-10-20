"""刷题端点 —— W2 判定闭环（`docs/02 §4.3` · §5.2 · §6.1 的「刷题」行）。

| 端点 | 认证 | 成功 | 失败 |
|---|---|---|---|
| `GET /api/problems` | 是 | 200 `pagination` 信封 + `problem` 视图 | 400 `invalid_argument`（limit / cursor）· 403 `must_change_pw` |
| `POST /api/practice/grade` | 是 | 200 `gradeResult`（两支） | 404 `not_found` · 400 `invalid_argument`（answer > 200） |
| `POST /api/practice/hint` | 是 | 200 `hintResponse` | 404 `not_found` · 409 `conflict`（未提交就取第 3 级） |

★ **两条答案暴露面的封口**：① `GET /api/problems` 只投影 `problem.schema.json` 的字段，
  `expected` / `solution` 一个都不带（`docs/02 §4.3-5`）；② `solution` 只在**确实写入了
  一条 attempt** 的 `grade` 响应里出现 —— 「未提交前不给解析」因此是接口事实，不是前端纪律。

★ **`hint_used` / `attempt_no` 的权威在服务端**（`docs/02 §4.3-⑥`）：请求体里没有这两个字段；
  即使客户端硬塞，`GradeRequest` 的 `extra="ignore"` 也只会忽略、不采信。

★ 本文件**没有 SQL**、**没有业务分支**（判定在 `domain/judge.py`、写库在 `learning/`）——
  `scripts/verify.sh` 的 `api_is_thin()` 会逐条检查。
"""
from __future__ import annotations

import asyncio
import sqlite3
from typing import Any

from fastapi import APIRouter, Depends, Query

from app.api.deps import get_conn, password_gate
from app.api.schemas import GradeRequest, HintRequest
from app.core.errors import AppError
from app.domain import judge
from app.identity.auth import AuthContext
from app.learning import catalog, store

__all__ = ["router"]

router = APIRouter(tags=["刷题"])


@router.get("/api/problems")
def list_problems(
    kc: "str | None" = Query(default=None, max_length=64),
    limit: int = Query(default=20, ge=1, le=100),
    cursor: "str | None" = Query(default=None, max_length=256),
    context: AuthContext = Depends(password_gate("practice")),
) -> "dict[str, Any]":
    """题目列表：**绝不包含 `expected` / 解析 / 错因**（`docs/02 §4.3-5`）。"""
    items, next_cursor = catalog.problems_page(kc=kc, limit=limit, cursor=cursor)
    return {"items": items, "next_cursor": next_cursor}


@router.post("/api/practice/grade")
async def grade_answer(
    payload: GradeRequest,
    context: AuthContext = Depends(password_gate("grade")),
    conn: sqlite3.Connection = Depends(get_conn),
) -> "dict[str, Any]":
    """判定：确定性 + `to_thread`；派生值由服务端从流水数出来（`docs/02 §4.3`）。"""
    item = catalog.find_problem(payload.item_id)
    if item is None:
        raise AppError("not_found", "题目不存在")
    result = await asyncio.to_thread(judge.grade, payload.answer, item)
    hint_used = store.count_hints(conn, context.user_id, payload.item_id, payload.session_id)
    attempt_no = store.next_attempt_no(conn, context.user_id, payload.item_id)
    store.record_attempt(
        conn,
        user_id=context.user_id,
        item=item,
        answer=payload.answer,
        session_id=payload.session_id,
        duration_ms=payload.duration_ms,
        result=result,
        hint_used=hint_used,
        attempt_no=attempt_no,
        asset_version=catalog.content_rev(),
    )
    return _grade_response(result, item, hint_used, attempt_no)


@router.post("/api/practice/hint")
def take_hint(
    payload: HintRequest,
    context: AuthContext = Depends(password_gate("hint")),
    conn: sqlite3.Connection = Depends(get_conn),
) -> "dict[str, Any]":
    """提示阶梯：级别由**流水条数**决定；第 3 级只在已提交之后（`docs/02 §5.2`）。

    ★ 未提交就请求第 3 级 ⇒ **409 `conflict`**，且**不写流水**（否则 ``level = 条数 + 1``
      这条 invariant 当场破掉）。客户端提交后再请求，就能拿到完整解析。
    """
    item = catalog.find_problem(payload.item_id)
    if item is None:
        raise AppError("not_found", "题目不存在")
    used = store.count_hints(conn, context.user_id, payload.item_id, payload.session_id)
    level = min(used + 1, 3)
    if level == 3 and not store.has_attempt(conn, context.user_id, payload.item_id):
        raise AppError("conflict", "请先提交作答，再查看完整解析")
    store.record_hint(
        conn,
        user_id=context.user_id,
        item_id=payload.item_id,
        session_id=payload.session_id,
        level=level,
        attempt_id=store.latest_attempt_id(
            conn, context.user_id, payload.item_id, payload.session_id
        ),
    )
    return {
        "level": level,
        "content": catalog.hint_content(item, level),
        "hint_used": used + 1,
        "hint_used_total": store.count_hints_total(conn, context.user_id, payload.item_id),
    }


def _grade_response(
    result: "dict[str, Any]", item: "dict[str, Any]", hint_used: int, attempt_no: int
) -> "dict[str, Any]":
    """把 domain 的判定结果装配成契约 `gradeResult`（`docs/02 §4.3` 的响应形状）。"""
    if not result.get("graded"):
        return result
    return {
        **result,
        "hint_used": hint_used,
        "attempt_no": attempt_no,
        "solution": item.get("solution"),
    }
