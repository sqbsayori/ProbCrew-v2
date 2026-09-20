"""日志入口 —— W0b · 卡2 · 第①步（docs/06 §12.3）。

规则 R-O（docs/02 §1.4 规则 1 / ADR-0005）：**日志与审计只记 ID 与动作，不记内容**——
不得出现提问原文、学生答案、教材片段、凭据。理由：F16「删除后读回 0 行」的验收
只覆盖数据库，内容一旦进日志就出现「验收通过但数据仍在」的洞。

本模块把 R-O 变成**可机检的构造约束**：

- 唯一写日志的口子是 :func:`log_action`；
- 除 ``action`` 外只允许 ``*_id`` 后缀的键与白名单里的**非内容**字段（``status`` / ``reason``）；
- 任何其它键名（典型如 ``question`` / ``answer`` / ``text``）**当场抛错** —— 内容进不了日志，
  而不是「靠 review 发现」；
- 落盘到 ``backend/data/logs/``（docs/02 §1.4 定死的目录），RotatingFileHandler 10MB × 5。

纯标准库。
"""
from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path
from typing import Any

AUDIT_LOGGER_NAME = "probcrew.audit"

#: R-O 允许的非 ID 字段（值都必须是**短枚举/代码**，不是内容）。
ALLOWED_NON_ID_FIELDS = frozenset({"status", "reason", "level"})

#: 单字段值长度上界（ID 最长 64，取裕量；防止把内容塞进 id 字段）。
_VALUE_MAX = 128


class LogContentViolation(ValueError):
    """试图把内容（非 ID / 非动作）写进日志时抛出 —— R-O 的机器可执行形式。"""


def setup_logging(
    log_dir: "str | Path",
    *,
    max_bytes: int = 10 * 1024 * 1024,
    backup_count: int = 5,
) -> logging.Logger:
    """初始化审计日志：写 ``<log_dir>/audit.log``，轮转 10MB × 5（docs/02 §1.4）。

    幂等：重复调用不会叠加 handler。
    """
    directory = Path(log_dir)
    directory.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(AUDIT_LOGGER_NAME)
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        handler = logging.handlers.RotatingFileHandler(
            directory / "audit.log",
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
        logger.propagate = False
    return logger


def log_action(action: str, **ids: "Any") -> str:
    """写一条「只含 ID 与动作」的审计日志（R-O 唯一入口）。

    - ``action``：动作标识（如 ``grade`` / ``hint`` / ``login``），非空且 ≤ 64 字；
    - 其余关键字参数：键名必须是 ``*_id`` 后缀，或在 :data:`ALLOWED_NON_ID_FIELDS` 内；
      值会被转成字符串并截断到 128 字（防内容经 id 字段渗入）。

    返回写入的一行（便于测试断言）。
    """
    if not isinstance(action, str) or not (1 <= len(action) <= 64):
        raise ValueError("action 必须是 1–64 字的字符串")

    parts: list[str] = [f"action={action}"]
    for key, value in sorted(ids.items()):
        if not (key.endswith("_id") or key in ALLOWED_NON_ID_FIELDS):
            raise LogContentViolation(
                f"日志字段 {key!r} 不是 *_id 也不是白名单字段 —— R-O 只记 ID 与动作，"
                f"内容（提问/答案/教材片段/凭据）不得进日志"
            )
        text = str(value)
        if len(text) > _VALUE_MAX:
            raise LogContentViolation(
                f"日志字段 {key!r} 的值超过 {_VALUE_MAX} 字 —— 疑似内容而非标识，拒绝写入"
            )
        parts.append(f"{key}={text}")

    line = " ".join(parts)
    logging.getLogger(AUDIT_LOGGER_NAME).info(line)
    return line
