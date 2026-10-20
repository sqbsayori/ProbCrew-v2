"""学习留痕存储层（W1 计数与删除 · W2 判定链的写入与读回）。

**W1 那半份**（F16）：``count_user_rows`` / ``delete_user_data`` —— 删除面与读回面是
「删除后逐表读回 0 行」这条断言的先行件（`contracts/user.schema.json#/definitions/deleteResult`）。

**W2 这半份**（`docs/02 §4.3` ⑥⑦）：作答 / 提示 / 掌握度的**同一事务写入**与读回 ——

- ``record_attempt``：写 ``student_attempt`` + 按知识点累加 ``student_mastery``，**一次 commit**；
- ``record_hint``：写 ``student_hint_log``（`hint_used` 的唯一权威来源）；
- ``wrong_page`` / ``progress``：错题本（C6 + C2）与进度（C8）的读回面；
- ``count_hints`` / ``next_attempt_no``：**服务端派生值**，从不读请求体（`docs/02 §4.3-⑥`）。

★ **四张 per-user 表**（`docs/02 §3.1`）：``student_qa_log`` · ``student_hint_log`` ·
``student_attempt`` · ``student_mastery``。★ ``student_audit`` **不在此列** —— 它天然跨用户，
且已裁定**删除时保留**（`docs/02 §3.1`；契约里对应 `deleteResult.audit_rows_retained`）。

**键名口径**：对外一律用契约的短键（``qa_log`` / ``hint_log`` / ``attempt`` / ``mastery``），
不把 ``student_`` 前缀漏到响应里 —— `deleteResult.deleted_rows` 的键是这四个。
"""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from typing import Any

from app.core.errors import AppError
from app.domain.normalize import normalize
from app.learning import catalog, paging

__all__ = [
    "COUNTER_TABLES",
    "count_hints",
    "count_hints_total",
    "count_user_rows",
    "delete_user_data",
    "has_attempt",
    "latest_attempt_id",
    "next_attempt_no",
    "next_attempt_no",
    "progress",
    "record_attempt",
    "record_hint",
    "wrong_page",
]

#: 契约 `deleteResult.deleted_rows` 的键（顺序即输出顺序，便于逐条对账）。
COUNTER_TABLES: tuple[str, ...] = ("qa_log", "hint_log", "attempt", "mastery")

_TABLE_BY_KEY: dict[str, str] = {
    "qa_log": "student_qa_log",
    "hint_log": "student_hint_log",
    "attempt": "student_attempt",
    "mastery": "student_mastery",
}


def count_user_rows(conn: sqlite3.Connection, user_id: str) -> "dict[str, int]":
    """逐表读回该用户四张表的行数（F16 / N19 的读回面）。"""
    counted: dict[str, int] = {}
    for key in COUNTER_TABLES:
        row = conn.execute(
            f"SELECT COUNT(*) AS n FROM {_TABLE_BY_KEY[key]} WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        counted[key] = int(row["n"])
    return counted


def delete_user_data(conn: sqlite3.Connection, user_id: str) -> "dict[str, int]":
    """**硬删**该用户四张表的数据（同一事务），并**读回**逐表剩余行数。

    ★ 返回的是**删除之后**重新数的行数，恒应为 0 —— 这正是 F16 的断言对象：
    「删了」这件事必须由**读回的数字**证明，不能由"没报错"或"HTTP 200"证明
    （`docs/02 §3.6`：旧仓的落地 bug 就是写函数漏一次提交、作答被静默回滚）。

    ★ **四表同一事务**（`docs/02 §3.6` 的"复合写同事务"）：否则会出现"问答流水删了、
      作答还在"的半成品状态，而 F16 的读回会**部分通过** —— 部分通过比全失败更难查。
    ★ 账号行**不在这里删**：`student_user` 走**脱敏态**（`status='deleted'`、空哈希、
      令牌全撤），那是 F16 四类处置里 `identity/` 那一半，P3 的 `DELETE /api/me/data` 组合两者。
    """
    for key in COUNTER_TABLES:
        conn.execute(f"DELETE FROM {_TABLE_BY_KEY[key]} WHERE user_id = ?", (user_id,))
    conn.commit()
    return count_user_rows(conn, user_id)


# ---------------------------------------------------------------------------
# W2 · 提示流水 —— `hint_used` 的唯一权威来源（`docs/02 §3.1` · §4.3-⑥）
# ---------------------------------------------------------------------------


def count_hints(conn: sqlite3.Connection, user_id: str, item_id: str, session_id: str) -> int:
    """**本次会话**的提示次数（作用域 `user,item,session`）—— 不读请求体。"""
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM student_hint_log"
        " WHERE user_id = ? AND item_id = ? AND session_id = ?",
        (user_id, item_id, session_id),
    ).fetchone()
    return int(row["n"])


def count_hints_total(conn: sqlite3.Connection, user_id: str, item_id: str) -> int:
    """**跨会话**累计（作用域 `user,item`）—— 派生值，不落列（`docs/02 §3.2`）。"""
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM student_hint_log WHERE user_id = ? AND item_id = ?",
        (user_id, item_id),
    ).fetchone()
    return int(row["n"])


def has_attempt(
    conn: sqlite3.Connection, user_id: str, item_id: str, session_id: str
) -> bool:
    """该 `(user,item,session)` **本会话**是否已提交 —— 提示第 3 级的可取判据。

    ★ **2026-10-20 收紧**（`docs/02 §11-18` 的裁定）：判据从「该 `(user,item)` 有过
      任一次作答」改为「**本会话已提交**」。旧判据允许重做一道做过的题时，在**新会话**
      里连点三次就于本次未提交的情况下拿到完整解析 —— F6 的伦理约束在正常路径上被绕过。
    ★ 作用域与 `hint_used` 同为 `(user,item,session)`：**同一份流水、同一个「会话」定义**，
      不引入第二套口径（`docs/02 §3.2` 的 `hint_used` 行）。
    """
    row = conn.execute(
        "SELECT 1 AS hit FROM student_attempt"
        " WHERE user_id = ? AND item_id = ? AND session_id = ? LIMIT 1",
        (user_id, item_id, session_id),
    ).fetchone()
    return row is not None


def latest_attempt_id(
    conn: sqlite3.Connection, user_id: str, item_id: str, session_id: str
) -> "str | None":
    """本次会话最近一次作答的 `attempt_id` —— 写进提示流水，作为「先提示后提交」的对账依据。"""
    row = conn.execute(
        "SELECT attempt_id FROM student_attempt"
        " WHERE user_id = ? AND item_id = ? AND session_id = ?"
        " ORDER BY attempt_no DESC LIMIT 1",
        (user_id, item_id, session_id),
    ).fetchone()
    return None if row is None else str(row["attempt_id"])


def next_attempt_no(conn: sqlite3.Connection, user_id: str, item_id: str) -> int:
    """该 `(user,item)` 的下一个 `attempt_no` —— **服务端派生**，请求体里没有这个字段（F8）。"""
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM student_attempt WHERE user_id = ? AND item_id = ?",
        (user_id, item_id),
    ).fetchone()
    return int(row["n"]) + 1


def record_hint(
    conn: sqlite3.Connection,
    *,
    user_id: str,
    item_id: str,
    session_id: str,
    level: int,
    attempt_id: "str | None" = None,
    now: "float | None" = None,
) -> str:
    """写一条提示流水并提交 —— **发提示与记账必须同事务**（`hint.schema.json` 的 invariant）。"""
    hint_id = "hint_" + uuid.uuid4().hex
    conn.execute(
        "INSERT INTO student_hint_log"
        " (hint_id, user_id, item_id, session_id, level, served_at, attempt_id)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            hint_id,
            user_id,
            item_id,
            session_id,
            level,
            time.time() if now is None else now,
            attempt_id,
        ),
    )
    conn.commit()
    return hint_id


# ---------------------------------------------------------------------------
# W2 · 作答与掌握度 —— 同一事务（`docs/02 §3.6` · §4.3-⑦）
# ---------------------------------------------------------------------------


def record_attempt(
    conn: sqlite3.Connection,
    *,
    user_id: str,
    item: "dict[str, Any]",
    answer: str,
    session_id: str,
    duration_ms: int,
    result: "dict[str, Any]",
    hint_used: int,
    attempt_no: int,
    asset_version: str,
    now: "float | None" = None,
) -> str:
    """写一条作答快照 + 按知识点累加掌握度，**一次 ``commit()``**（R-B）。

    ★ `correct` 为 ``None``（不可判定）时**跳过掌握度**（`docs/02 §4.3-②`）——
      把 ``NULL`` 当成 0 会把「需要人工判定」污染成「做错了」。
    ★ `expected` 落的是**提交时刻的快照**（F7）：题库后来改答案，历史记录仍可复算。
    """
    attempt_id = "att_" + uuid.uuid4().hex
    correct = result.get("correct")
    graded = bool(result.get("graded"))
    kc_ids = list(item["kc_ids"])
    created = time.time() if now is None else now
    conn.execute(
        "INSERT INTO student_attempt (attempt_id, user_id, item_id, session_id, kc_ids, correct,"
        " score, answer_raw, answer_normalized, expected, hint_used, duration_ms, attempt_no,"
        " source, grader, created_at, asset_version, mistake_id, mistake_matched)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            attempt_id,
            user_id,
            str(item["prob_id"]),
            session_id,
            json.dumps(kc_ids, ensure_ascii=False),
            None if correct is None else int(bool(correct)),
            result.get("score"),
            answer,
            normalize(answer),
            str(item["expected"]),
            hint_used,
            duration_ms,
            attempt_no,
            "practice" if attempt_no == 1 else "wrong_review",
            "deterministic" if graded else "not_gradable",
            created,
            asset_version,
            result.get("mistake_id"),
            1 if result.get("mistake_matched") else 0,
        ),
    )
    if correct is not None:
        for topic in kc_ids:
            conn.execute(
                "INSERT INTO student_mastery (user_id, topic, attempts, correct, updated_at)"
                " VALUES (?, ?, 1, ?, ?)"
                " ON CONFLICT(user_id, topic) DO UPDATE SET"
                " attempts = attempts + 1,"
                " correct = correct + excluded.correct,"
                " updated_at = excluded.updated_at",
                (user_id, topic, int(bool(correct)), created),
            )
    conn.commit()
    return attempt_id


# ---------------------------------------------------------------------------
# W2 · 读回面：错题本（C6 + C2）与进度（C8）
# ---------------------------------------------------------------------------

#: `contracts/attempt.schema.json` 的**视图字段**（不含 `user_id` / `student_id`）。
_ATTEMPT_COLUMNS = (
    "attempt_id, item_id, session_id, kc_ids, correct, score, answer_raw, answer_normalized,"
    " expected, hint_used, duration_ms, attempt_no, source, grader, created_at, asset_version,"
    " mistake_id, mistake_matched"
)


def _attempt_view(conn: sqlite3.Connection, row: sqlite3.Row, user_id: str) -> "dict[str, Any]":
    """行 → C6 视图：整型/JSON 列转换 + 派生 `hint_used_total`。"""
    view: dict[str, Any] = {key: row[key] for key in row.keys()}
    view["kc_ids"] = json.loads(view["kc_ids"])
    view["correct"] = None if view["correct"] is None else bool(view["correct"])
    view["mistake_matched"] = bool(view["mistake_matched"])
    view["hint_used_total"] = count_hints_total(conn, user_id, str(view["item_id"]))
    #: ★ **2026-10-20 新增的视图字段**（`attempt.schema.json` v1.1）：错题本要显示题干。
    #:   来自题库资产、**不落 attempt 行**；资产里找不到就省略（不编空题干）。
    stem = catalog.stem_of(str(view["item_id"]))
    if stem is not None:
        view["stem"] = stem
    return view


def _like_pattern(kc: str) -> str:
    """把 `kc_xxx` 变成 JSON 文本里的**精确**命中模式（`_` 是 LIKE 通配符，必须转义）。"""
    escaped = kc.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f'%"{escaped}"%'


def wrong_page(
    conn: sqlite3.Connection,
    *,
    user_id: str,
    kc: "str | None",
    limit: int,
    cursor: "str | None",
) -> "tuple[list[dict[str, Any]], str | None]":
    """错题本一页：`correct = 0` 的作答，按 `created_at DESC, attempt_id DESC`（稳定序）。

    ★ 游标里是 `(created_at, attempt_id)` —— 只按 `created_at` 时同秒记录会乱序，
      翻页就会重复或漏项（`docs/02 §6.4`）。
    """
    clauses = ["user_id = ?", "correct = 0"]
    params: list[Any] = [user_id]
    if kc:
        clauses.append("kc_ids LIKE ? ESCAPE '\\'")
        params.append(_like_pattern(kc))
    if cursor:
        payload = paging.decode(cursor)
        created, attempt_id = payload.get("t"), payload.get("id")
        if not isinstance(created, (int, float)) or not isinstance(attempt_id, str):
            raise AppError("invalid_argument", "游标不合法（缺少位置）", {"field": "cursor"})
        clauses.append("(created_at < ? OR (created_at = ? AND attempt_id < ?))")
        params.extend([created, created, attempt_id])
    params.append(limit + 1)
    rows = conn.execute(
        f"SELECT {_ATTEMPT_COLUMNS} FROM student_attempt"
        f" WHERE {' AND '.join(clauses)}"
        " ORDER BY created_at DESC, attempt_id DESC LIMIT ?",
        tuple(params),
    ).fetchall()
    has_more = len(rows) > limit
    page = rows[:limit]
    items = [_attempt_view(conn, row, user_id) for row in page]
    next_cursor = (
        paging.encode({"t": page[-1]["created_at"], "id": page[-1]["attempt_id"]})
        if has_more and page
        else None
    )
    return items, next_cursor


def progress(conn: sqlite3.Connection, user_id: str) -> "dict[str, Any]":
    """`GET /api/me/progress`（C8）：总作答数**按 attempt 去重** + 逐知识点正确率。

    ★ `total_attempts` 必须是 ``COUNT(DISTINCT attempt_id)`` —— 对 ``topics[].attempts``
      求和会把「一道题挂多个知识点」重复计入，总量虚高（`docs/02 §3.2`）。
    ★ `weak` 是**决策 7A 的显式占位**（阈值不在 v2 范围）：`available` 恒 ``False``、
      `note` 写死那一句，各实现者不得自拟文案。
    """
    total = conn.execute(
        "SELECT COUNT(DISTINCT attempt_id) AS n FROM student_attempt WHERE user_id = ?",
        (user_id,),
    ).fetchone()
    rows = conn.execute(
        "SELECT topic, attempts, correct, updated_at FROM student_mastery"
        " WHERE user_id = ? ORDER BY topic ASC",
        (user_id,),
    ).fetchall()
    topics = [
        {
            "topic": row["topic"],
            "attempts": int(row["attempts"]),
            "correct": int(row["correct"]),
            "accuracy": round(row["correct"] / row["attempts"], 4) if row["attempts"] else 0.0,
            "updated_at": row["updated_at"],
        }
        for row in rows
    ]
    return {
        "total_attempts": int(total["n"]),
        "topics": topics,
        "weak": {"available": False, "note": "数据不足，暂不显示薄弱点"},
    }
