"""内容资产目录（**只读**）—— W2 判定链的题库 / 题型读取面（`docs/02 §4.3` · F20）。

★ 为什么放在 `learning/` 而不是 `api/`：`api/` 的纪律是「不写业务、不读文件、不写 SQL」
  （`docs/03 §3.2`），而「从 `content/*.json` 取一道题」属于判定链的数据面。
  `learning/` 的允许依赖是 `app.core` + `app.domain`（`docs/03 §3.1`），本文件只用到 `app.core`。

★ **资产是只读的**（`docs/02 §4.3` 硬性设计 4）：判定接口只读题目、不写题库。
  这里因此没有任何写路径，缓存也只需要按 `(路径, mtime)` 失效。

★ **题目视图与资产条目必须分开**（`contracts/problem.schema.json`）：资产里有 `expected`
  与 `solution`，视图里一个都不能带 —— 列表接口泄露答案就是「前端不显示」变成「没做」。
  投影只用 :data:`_VIEW_FIELDS` 的白名单，**不做 `item.pop` 式的减法**（减法会漏掉将来新增的字段）。
"""
from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.core.config import load_settings
from app.core.errors import AppError
from app.learning import paging

__all__ = ["content_rev", "find_problem", "hint_content", "problems_page", "stem_of"]

#: `contracts/problem.schema.json` 的**全部**字段（`additionalProperties: false`）。
_VIEW_FIELDS: tuple[str, ...] = ("prob_id", "stem", "kc_ids", "pt_id", "answer_kind", "difficulty")

#: 提示第 3 级的正文上界（`contracts/hint.schema.json` 的 `content.maxLength`）。
_HINT_CONTENT_MAX = 2000


@lru_cache(maxsize=8)
def _document(path: str, mtime_ns: int) -> "dict[str, Any]":
    """按 `(路径, mtime)` 缓存的资产文档 —— 资产更新后自动失效。"""
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _asset(key: str) -> "dict[str, Any]":
    path = load_settings().content_dir / f"{key}.json"
    if not path.exists():
        raise AppError("not_found", f"内容资产 content/{key}.json 不存在")
    return _document(str(path), path.stat().st_mtime_ns)


def _items(key: str) -> "list[dict[str, Any]]":
    return list(_asset(key)["items"])


def content_rev() -> str:
    """**作答快照里的资产版本**（`contracts/attempt.schema.json` 的 `asset_version`：12 位十六进制）。

    算法 = `docs/02 §3.3` 规则 2：``sha256("dag=..;formula=..;mistakes=..;problems=..;types=..")[:12]``
    （键按字母序、分号连接）。★ 对**当前已产出**的资产复算：`MANIFEST.json` 尚未派生（五类未齐），
    因此现在覆盖四类；`dag.json` 合入后输入集自动扩展，值与将来的 MANIFEST 一致 ——
    历史作答保留当时的值，这正是 `content_rev` 要的「可反解到资产版本」。
    """
    revs: dict[str, str] = {}
    for key in ("problems", "types", "mistakes", "dag", "formula"):
        path = load_settings().content_dir / f"{key}.json"
        if path.exists():
            revs[key] = str(_document(str(path), path.stat().st_mtime_ns)["rev"])
    payload = ";".join(f"{key}={revs[key]}" for key in sorted(revs))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def find_problem(item_id: str) -> "dict[str, Any] | None":
    """按 `prob_id` 取**资产条目**（含 `expected` / `solution`，只给服务端内部用）。"""
    for item in _items("problems"):
        if item["prob_id"] == item_id:
            return item
    return None


def stem_of(item_id: str) -> "str | None":
    """题干（只给**已提交记录**的复习视图用 —— 错题本要显示"错的是哪道题"）。

    ★ 它**不进** `problem` 列表投影之外的新暴露面：`GET /api/problems` 本来就带 `stem`，
      这里只是让 `/api/me/wrong` 能按 `item_id` 补上同一个字段（`attempt.schema.json`
      的 `stem` 是 **2026-10-20 新增的可选视图字段**，不落 `student_attempt` 行）。
    ★ 资产里找不到该 `prob_id` 时返回 ``None``（**省略字段**而不是编一个空题干）——
      题目被删除后历史记录仍要可读，但不得凭空造内容。
    """
    item = find_problem(item_id)
    return None if item is None else str(item["stem"])


def _view(item: "dict[str, Any]") -> "dict[str, Any]":
    """资产条目 → `problem.schema.json` 的视图（白名单投影）。"""
    return {field: item[field] for field in _VIEW_FIELDS}


def _resume(items: "list[dict[str, Any]]", payload: "dict[str, Any]") -> int:
    """把游标还原成**过滤后列表**的下标；资产变了就报 400，而不是静默错位。"""
    index = payload.get("i")
    if not isinstance(index, int) or index < 0 or index > len(items):
        raise AppError("invalid_argument", "游标不合法（位置越界）", {"field": "cursor"})
    if index > 0:
        previous = payload.get("id")
        if previous is not None and items[index - 1]["prob_id"] != previous:
            raise AppError(
                "invalid_argument",
                "游标与当前题库不一致（资产已更新，请重新从头翻页）",
                {"field": "cursor"},
            )
    return index


def problems_page(*, kc: "str | None", limit: int, cursor: "str | None") -> "tuple[list[dict[str, Any]], str | None]":
    """`GET /api/problems` 的一页：资产顺序稳定 ⇒ 游标 = `{i, id}`（`docs/02 §6.4`）。"""
    items = _items("problems")
    if kc:
        items = [item for item in items if kc in item["kc_ids"]]
    start = _resume(items, paging.decode(cursor)) if cursor else 0
    window = items[start:start + limit]
    has_more = start + limit < len(items)
    next_cursor = (
        paging.encode({"i": start + limit, "id": window[-1]["prob_id"]})
        if has_more and window
        else None
    )
    return [_view(item) for item in window], next_cursor


def _framework(pt_id: "str | None") -> str:
    """题型的解法框架 —— 提示第 1 级「方向」的来源（**只给方向，不给完整思路**）。"""
    if not pt_id:
        return ""
    for entry in _items("types"):
        if entry["pt_id"] == pt_id:
            return str(entry.get("framework", "")).strip()
    return ""


def hint_content(item: "dict[str, Any]", level: int) -> str:
    """按级取提示正文（`docs/02 §5.2`）：

    - 1 = 方向（题型框架）· 2 = 步骤骨架（**去掉最后一步**，避免带出最终答案）· 3 = 完整解析。
    """
    solution = item.get("solution") or {}
    steps = solution.get("steps") or []
    if level <= 1:
        framework = _framework(item.get("pt_id"))
        return f"先判断题型：{framework}" if framework else "先判断题目属于哪一类概率模型，再列出已知量与所求量。"
    if level == 2:
        skeleton = [str(step.get("explanation", "")).strip() for step in steps[:-1]]
        skeleton = [text for text in skeleton if text]
        if skeleton:
            return "步骤骨架：" + " → ".join(skeleton)
        return "步骤骨架：先写出样本空间，再确定所求事件，最后按定义计算概率。"
    summary = str(solution.get("explanation", "")).strip()
    detail = "；".join(
        f"{step.get('explanation', '')}（{step.get('formula', '')}）" for step in steps
    ).strip()
    body = " ".join(part for part in (summary, detail) if part)
    return (body or "完整解析暂缺。")[:_HINT_CONTENT_MAX]
