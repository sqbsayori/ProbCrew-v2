"""删除墓碑 —— F16 的"恢复不复活"那一半（`docs/02 §1.4` 的 2026-11-08 裁定 · §11 第 12 项）。

**裁定原文（`docs/02 §1.4`）**：在线数据**立即删除**；每次删除**同时写删除墓碑**；备份最多保留 **7 天**；
恢复备份后必须**重放删除墓碑**，已删除账号在恢复后**仍不可登录、学习数据仍为 0**；
`deleteResult` 与 N15 文案必须显式声明这条边界。墓碑是**不含内容的稳定标识记录**。

**为什么墓碑是旁挂的 JSONL，而不是第 8 张表**（本仓结构上的结论，不是偏好）：

1. **恢复会回滚 `*.sqlite`**：`scripts/backup.py` 的 `restore` 用备份覆盖库文件。墓碑若在库里，
   恢复旧备份时会被一起回滚 —— 那正是本任务要防的"删除复活"。旁挂文件不在恢复范围内。
2. 新增第 8 张 `student_*` 表会同时撞 `docs/02 §3.1`（七张表）、`core/db.py` 的 `TABLE_NAMES`
   与门禁口径 ⇒ 那是 R1 的结构改动，本轮不走。
3. 备份快照由 `scripts/backup.py` 在 B2.2 里**复制同名文件**（`backups/tombstones-<stamp>.jsonl`），
   恢复时作为**额外来源**并集读回 —— 这样"换一台机器只有备份"也能重放。

**位置**：`Path(PROBCREW_DB_PATH).parent / "tombstones.jsonl"`（默认 `backend/data/tombstones.jsonl`）。
不新增环境变量：它跟着 L2 库走，于是**用例天然隔离**（`tmp_path/me.sqlite` ⇒ `tmp_path/tombstones.jsonl`），
`backend/data/**` 也已被 `.gitignore` 整目录忽略。

**行格式**（冻结；备份/恢复算法与 `docs/03 §8.3` 的唯一依据）::

    {"v":1,"tombstone_id":"tb_1f0c9a7d2b3e","user_id":"u_...",
     "deleted_at":1763539200.512,"action":"delete_own_data","content_rev":"5c6c6ae242d6"}

★ **不含** `username` / `display_name` / 任何答案 / `password_hash` / 令牌 —— 墓碑是标识记录，不是数据副本。
★ `action` 与审计动作同名（`delete_own_data` / `delete_user`），便于两边对账。

**失败语义（fail-closed，两处都必须写进评审）**：

- 墓碑**写失败**（磁盘满 / 权限）⇒ 抛 `AppError("dependency_unavailable")`，**不执行删除**。
  宁可这次删不掉，也不留"删了但恢复会复活"的洞。
- 墓碑**写成功、后续步骤崩溃** ⇒ 墓碑留存，下一次重放会把该账号清零。这是**有意选择**：
  「删除请求一旦记入墓碑，就必须被执行」。

**依赖方向**：本模块只 import `app.core` / `app.identity`（`verify.sh` 的 `import_direction` 白名单
对 `identity` 只放行这两个）。因此"重放时清四张学习表"必须由调用方**注入** `purge`
（`scripts/backup.py` 传 `learning.store.delete_user_data`），本模块不 import `learning`。
"""
from __future__ import annotations

import json
import os
import secrets
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from app.core import db
from app.core.errors import AppError
from app.identity import users

__all__ = [
    "ACTION_DELETE_OWN_DATA",
    "ACTION_DELETE_USER",
    "ACTIONS",
    "TOMBSTONE_FILENAME",
    "TOMBSTONE_VERSION",
    "TombstoneError",
    "load",
    "log_path",
    "mark_account_deleted",
    "record",
    "replay",
]

#: 行格式版本（改字段必须同时改版本，并回写 `docs/03 §8.3`）。
TOMBSTONE_VERSION = 1

#: 墓碑日志的默认文件名（与 L2 库同目录）。
TOMBSTONE_FILENAME = "tombstones.jsonl"

#: 两种删除动作 —— 与 `student_audit.action` 同名（`contracts/user.schema.json#auditRow`）。
ACTION_DELETE_OWN_DATA = "delete_own_data"
ACTION_DELETE_USER = "delete_user"
ACTIONS: "tuple[str, ...]" = (ACTION_DELETE_OWN_DATA, ACTION_DELETE_USER)

#: 追加写的进程内串行化。单 worker 下足够；跨进程并发不在本轮边界内（`docs/06 §15.6`）。
_lock = threading.Lock()


class TombstoneError(RuntimeError):
    """墓碑格式/解析错误（**不静默跳过**：丢一条墓碑 = 恢复后多一个复活的账号）。"""


def log_path(explicit: "str | Path | None" = None) -> Path:
    """墓碑日志路径：显式值 > `L2 库同目录 / tombstones.jsonl`。"""
    if explicit is not None:
        return Path(explicit)
    return Path(db.db_path()).parent / TOMBSTONE_FILENAME


def _validate_action(action: str) -> None:
    if action not in ACTIONS:
        raise TombstoneError(f"未知删除动作 {action!r} —— 只接受 {' / '.join(ACTIONS)}")


def load(*, path: "str | Path | None" = None) -> "list[dict[str, Any]]":
    """读回全部墓碑（按文件顺序）。缺文件 = 空列表；**坏行 = 抛错并指出行号**。"""
    target = log_path(path)
    try:
        text = target.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    except OSError as exc:
        raise TombstoneError(f"墓碑日志读不出：{target}（{exc}）") from exc

    entries: "list[dict[str, Any]]" = []
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError as exc:
            raise TombstoneError(f"{target}:{number} 不是合法 JSON（{exc.msg}）") from exc
        if not isinstance(entry, dict) or not entry.get("user_id"):
            raise TombstoneError(f"{target}:{number} 缺 user_id")
        _validate_action(str(entry.get("action", "")))
        entries.append(entry)
    return entries


def record(
    *,
    user_id: str,
    action: str,
    now: "float | None" = None,
    content_rev: "str | None" = None,
    path: "str | Path | None" = None,
) -> "dict[str, Any]":
    """把一次删除记入墓碑；**同一 `user_id` 只记一次**（重复删除是幂等的，不追加第二行）。

    ★ 写失败 ⇒ `AppError("dependency_unavailable")`（503），由调用方据此**中止删除**。
    ★ `content_rev` 由调用方尽力提供（资产不可复算时传 `None`）—— 它只用于排障，不影响重放判定。
    """
    _validate_action(action)
    target = log_path(path)
    with _lock:
        try:
            existing = load(path=target)
        except TombstoneError as exc:
            # 日志读不出来 = 无法确认"这个人是不是已经记过" ⇒ 与写失败同款：**中止删除**。
            # （静默继续会造出第二条墓碑，或在日志损坏时把删除当成"已完成"。）
            raise AppError(
                "dependency_unavailable",
                "删除记录不可读，已中止本次删除（不留下会在恢复时复活的删除）",
            ) from exc
        for entry in existing:
            if entry.get("user_id") == user_id:
                return entry
        entry: "dict[str, Any]" = {
            "v": TOMBSTONE_VERSION,
            "tombstone_id": f"tb_{secrets.token_hex(6)}",
            "user_id": user_id,
            "deleted_at": time.time() if now is None else float(now),
            "action": action,
            "content_rev": content_rev,
        }
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
        except OSError as exc:
            raise AppError(
                "dependency_unavailable",
                "删除记录无法落盘，已中止本次删除（不留下会在恢复时复活的删除）",
            ) from exc
        return entry


def mark_account_deleted(conn: sqlite3.Connection, user_id: str) -> int:
    """账号侧：`status='deleted'` · 空哈希 · 令牌全撤（复用 F16 的脱敏实现）；返回撤销数。

    ★ 账号不存在 ⇒ 返回 0，**不抛**：恢复出来的库可能本来就没有这个人（墓碑比备份新）。
    """
    row = conn.execute("SELECT 1 FROM student_user WHERE user_id = ?", (user_id,)).fetchone()
    if row is None:
        return 0
    return users.anonymize_user(conn, user_id)


def _iter_sources(paths: "Sequence[str | Path]"):
    """日志 + 备份快照（文件或目录）：目录取 `tombstones-*.jsonl`，按文件名（含时间戳）排序。"""
    yield log_path()
    for raw in paths:
        candidate = Path(raw)
        if candidate.is_dir():
            yield from sorted(candidate.glob("tombstones-*.jsonl"))
        elif candidate.exists():
            yield candidate


def replay(
    conn: sqlite3.Connection,
    *,
    purge: "Callable[[sqlite3.Connection, str], Mapping[str, int]]",
    paths: "Sequence[str | Path]" = (),
) -> "dict[str, Any]":
    """把墓碑重放到（刚恢复的）库上：先清四张学习表，再脱敏账号。

    ``purge`` 由调用方注入（`learning.store.delete_user_data`）；本模块不 import `learning`，
    理由见模块 docstring 的依赖方向。
    ★ 幂等：跑两次结果一致；同一 `user_id` 的多条墓碑按 `deleted_at` 去重。
    """
    merged: "dict[str, dict[str, Any]]" = {}
    for source in _iter_sources(paths):
        for entry in load(path=source):
            user_id = str(entry["user_id"])
            current = merged.get(user_id)
            if current is None or float(entry.get("deleted_at") or 0) < float(
                current.get("deleted_at") or 0
            ):
                merged[user_id] = entry

    applied = 0
    missing = 0
    for user_id in sorted(merged):
        purge(conn, user_id)
        existed = (
            conn.execute("SELECT 1 FROM student_user WHERE user_id = ?", (user_id,)).fetchone()
            is not None
        )
        if existed:
            mark_account_deleted(conn, user_id)
            applied += 1
        else:
            missing += 1
    conn.commit()
    return {"tombstones": len(merged), "applied": applied, "missing": missing}
