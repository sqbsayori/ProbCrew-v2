"""`GET /api/health` 的本地探针 —— 契约 `registry.schema.json` v1.2 的 health 分支。

★ **为什么探针不写在 `api/` 里**：`scripts/verify.sh` 的「`api/` 薄」规则（`docs/03 §3.2` 第 1 条，
实现见 `verify.sh` 的 `api_thinness`）要求 `api/**` **不出现任何 SQL**、单函数 ≤ 40 行。健康探针要碰
SQLite，因此它落在 `core/`，端点只做聚合与返回。

★ **`health` 是唯一无需登录的 API**（`docs/02 §4.1` 口径 6），因此它只许报告**这个进程自己的事实**
（DB · L1 缓存 · 资产 `rev` · 备份新鲜度）。**不得**出现 `model` / `model_status` / `model_endpoint` /
`upstream` / `llm`（`registry.schema.json` 的 `x-forbidden-fields`）：模型状态需要一次出网调用，
而那既是第三个出网口，又会破坏「判定压测时 health 仍 < 100ms」（`docs/02 §8-7`）。

**状态聚合规则**（写死在这里，端点与用例共用同一处）：

- `degraded` ⟺ `db == "error"` ∨ `corpus == "missing"` ∨ `asset_rev is None` ∨ `backup.last_result == "failed"`；
- `backup.last_result == "never"` **不降级** —— 「还没排过」与「排了但坏了」处置不同（契约把它们拆成两个枚举值，
  见 `docs/03 §8.3` 的新鲜度行），把 `never` 判成不健康会让一个全新环境永远红着；
- 恒 200：任一项不健康只是 `degraded`，不是 5xx（运维探针要能拿到读数）。

**性能纪律**：无出网、无全树扫描（`corpus` 用 `next()` 早退）、`content_rev` 自带 `(路径, mtime)` 缓存。
`db` 探针走**唯一建表入口** `db.initialize()`，进程内已缓存，因此稳态成本是一次 `SELECT 1`。
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Mapping

from app.core import db
from app.core.config import Settings, load_settings

__all__ = [
    "BACKUP_STATUS_FILENAME",
    "aggregate_status",
    "backup_status",
    "corpus_status",
    "db_status",
]

#: 备份新鲜度状态文件（生产者 = `scripts/backup.py` 的 `_write_last`，`docs/03 §8.3`）。
BACKUP_STATUS_FILENAME = "last_backup.json"

#: 契约 `backup.last_result` 的三个枚举值。
_BACKUP_RESULTS = ("ok", "failed", "never")

#: 契约 `last_success_at` / `last_failure_at` 的 pattern（`…Z` 的秒级 UTC）。
_TS_RE = re.compile(r"\A\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z\Z")

_NEVER: "dict[str, Any]" = {
    "last_success_at": None,
    "last_result": "never",
    "last_failure_at": None,
}


def _settings(settings: "Settings | None") -> Settings:
    return load_settings() if settings is None else settings


def db_status(settings: "Settings | None" = None) -> str:
    """L2 库是否可用：``ok`` / ``error``。

    ★ 探针本体在 :func:`app.core.db.probe_readable`（唯一建表入口 + 读一次）；本函数只做结果映射。
      不在这里写 SQL 的两个理由：① 「唯一建表入口」是 `docs/02 §3.6` 的纪律，探针自己开连接就会出现
      "health 说 ok、业务请求却 `no such table`"的第二套真相；② 门禁的 R-B 要求"有 execute 的文件必须有
      commit" —— 探针是只读的，不该靠一句空 `commit()` 去糊弄那条规则。
    ★ **不写业务行**：health 会被运维探针高频打，插入审计/业务数据等于让探针污染 L2。
    """
    return "ok" if db.probe_readable(_settings(settings).db_path) else "error"


def corpus_status(settings: "Settings | None" = None) -> str:
    """L1 语料缓存是否可用：``ok`` / ``missing``。

    ★ `missing` **不是错误**：答疑照常可用，只是教材出处如实返回「出处暂不可用」（F10 · `docs/02 §8-13b`）
      —— 因此它让整体 `degraded`，但不让 health 变成 5xx。
    ★ 用 `next(...)` 早退：语料目录可能很大，health 不允许做全树 `rglob` 的物化。
    """
    root = Path(_settings(settings).corpus_dir)
    try:
        if not root.is_dir():
            return "missing"
        return "ok" if next(root.rglob("*.md"), None) is not None else "missing"
    except OSError:
        return "missing"


def _timestamp(value: Any) -> "str | None":
    """只放行契约 pattern 认可的 UTC 秒级时间串；其余一律 `None`（不把坏值透传给契约）。"""
    return value if isinstance(value, str) and _TS_RE.match(value) else None


def backup_status(settings: "Settings | None" = None) -> "dict[str, Any]":
    """备份新鲜度（N19 验收④ 的读面）：读 `backend/data/backups/last_backup.json`。

    ★ **消费者只读这个文件、不出网**（契约的 `x-forbidden-fields` 同样管这一条）。
    ★ 三态与契约一致：文件不存在 ⇒ `never`；文件坏了 ⇒ `failed`（**fail-closed**：读不出来就是"不可信"，
      不能报 `ok`，否则"备份停了三天"看起来一切正常 —— 这正是 N19 验收④ 要防的症状）。
    """
    resolved = _settings(settings)
    path = Path(resolved.db_path).parent / "backups" / BACKUP_STATUS_FILENAME
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return dict(_NEVER)
    except (OSError, json.JSONDecodeError):
        return {**_NEVER, "last_result": "failed"}
    if not isinstance(raw, Mapping):
        return {**_NEVER, "last_result": "failed"}
    result = raw.get("last_result")
    return {
        "last_success_at": _timestamp(raw.get("last_success_at")),
        "last_result": result if result in _BACKUP_RESULTS else "failed",
        "last_failure_at": _timestamp(raw.get("last_failure_at")),
    }


def aggregate_status(
    *,
    db: str,
    corpus: str,
    asset_rev: "str | None",
    backup: Mapping[str, Any],
) -> str:
    """四项本地事实 → `ok` / `degraded`（规则见模块 docstring，端点与用例共用）。"""
    if db != "ok" or corpus != "ok" or asset_rev is None:
        return "degraded"
    if backup.get("last_result") == "failed":
        return "degraded"
    return "ok"
