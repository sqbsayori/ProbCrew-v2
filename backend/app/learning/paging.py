"""游标编解码 —— W2 判定链（`docs/02 §6.4`：**不透明、可解码、客户端不得解析**）。

★ 为什么单独一个文件：`GET /api/problems`（静态资产，按**文件顺序**翻页）与
  `GET /api/me/wrong`（追加写表，按 `created_at DESC, attempt_id DESC`）都需要
  「编码一段服务端私有状态、下页原样带回来」这同一件事。两处各写一遍必然漂移，
  而游标最怕的正是「两个端点的解码口径不一致」—— 一个报 400、一个静默从头开始。

★ **必须校验可解码**（`docs/02 §6.1` 的 R-A 表把 `cursor` 列进去了）：不校验就是一个注入面，
  且失败形态是「翻到第二页突然回到第一页」这种查不出来的行为差异。
"""
from __future__ import annotations

import base64
import json
from typing import Any

from app.core.errors import AppError

__all__ = ["decode", "encode"]

#: 契约 `pagination.schema.json#/definitions/cursor` 的 `maxLength`。
CURSOR_MAX_LENGTH = 256


def encode(payload: "dict[str, Any]") -> str:
    """把服务端私有状态编码成不透明串（base64url · 无填充）。"""
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode(token: str, *, field: str = "cursor") -> "dict[str, Any]":
    """解码游标；任何不合法形态一律 **400 `invalid_argument`**（不静默回退到第一页）。"""
    if not isinstance(token, str) or not 1 <= len(token) <= CURSOR_MAX_LENGTH:
        raise AppError("invalid_argument", "游标不合法", {"field": field})
    padded = token + "=" * (-len(token) % 4)
    try:
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
    except Exception:  # noqa: BLE001 —— 任何解码失败都是同一条对外语义
        raise AppError("invalid_argument", "游标不合法（无法解码）", {"field": field}) from None
    if not isinstance(payload, dict):
        raise AppError("invalid_argument", "游标不合法（不是对象）", {"field": field})
    return payload
