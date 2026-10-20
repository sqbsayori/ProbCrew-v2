"""游标编解码 —— **已迁至 `app/core/paging.py`**（2026-10-20），本文件只是再导出。

★ 迁移理由：W1 的管理端列表（`/api/admin/users` · `/api/admin/audit`）也要同一种游标，
  而 `docs/03 §3.1` 把 `identity/` 的允许依赖限成 `core/` —— 它不得 import `app.learning`。
  游标的线上形态（base64url 的 JSON · 256 字符上界 · 解不开就 400）是**跨域共享的协议细节**，
  因此唯一实现搬进 `core/`；保留本文件是为了让 `learning/` 既有的
  `from app.learning import paging` 一行不改（`catalog.py` / `store.py`）。
"""
from __future__ import annotations

from app.core.paging import CURSOR_MAX_LENGTH, decode, encode

__all__ = ["CURSOR_MAX_LENGTH", "decode", "encode"]
