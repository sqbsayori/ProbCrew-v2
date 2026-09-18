"""日志入口 —— `core/` 的第三件东西。

★ **R-O（`docs/02 §1.6`）：日志与审计只记 ID 与动作，不记内容** ——
不得出现提问原文、学生答案、教材片段、凭据；日志目录定死 `backend/data/logs/`，轮转 10MB × 5。

★ 这里**把 R-O 写成机制而不是纪律**：`log_action()` 只接受"动作名 + 若干 ID 值"，
值不符合 ID 形状就直接抛错 —— 想记正文就写不进去。理由见 R-O 的由来：
F16 靠"读回 0 行"验收，而**日志文件不在读回清单里**，内容一旦进日志，"删除"就只删了数据库那一份。
"""

from __future__ import annotations

import logging
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .config import LOG_DIR

__all__ = [
    "LOG_ROTATE_MAX_BYTES",
    "LOG_ROTATE_BACKUPS",
    "get_logger",
    "log_action",
    "setup_logging",
]

#: R-O 定死的轮转参数（`docs/02 §1.6`）
LOG_ROTATE_MAX_BYTES: int = 10 * 1024 * 1024
LOG_ROTATE_BACKUPS: int = 5

LOG_FILE_NAME: str = "app.log"
_LOGGER_ROOT: str = "probcrew"
_HANDLER_MARK: str = "_probcrew_file_handler"

_ACTION = re.compile(r"[a-z][a-z0-9_]*(?:\.[a-z0-9_]+)*")
_FIELD = re.compile(r"[a-z][a-z0-9_]*")
#: ID 类值：学号 / 题目 ID / 会话 ID / 动作 ID 一类，**不含空格与中文** —— 于是正文写不进来
_ID_VALUE = re.compile(r"[A-Za-z0-9_.:-]{1,64}")


def setup_logging(log_dir: Path | None = None, level: int = logging.INFO) -> logging.Logger:
    """装配进程级日志：`<log_dir>/app.log`，轮转 10MB × 5。

    幂等：重复调用不会叠加 handler。缺省目录 = `backend/data/logs/`（`core.config.LOG_DIR`）。
    """
    directory = Path(log_dir) if log_dir is not None else LOG_DIR
    directory.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger(_LOGGER_ROOT)
    root.setLevel(level)
    for handler in list(root.handlers):
        if getattr(handler, _HANDLER_MARK, False):
            root.removeHandler(handler)
            handler.close()

    handler = RotatingFileHandler(
        directory / LOG_FILE_NAME,
        maxBytes=LOG_ROTATE_MAX_BYTES,
        backupCount=LOG_ROTATE_BACKUPS,
        encoding="utf-8",
    )
    setattr(handler, _HANDLER_MARK, True)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    root.addHandler(handler)
    return root


def get_logger(name: str) -> logging.Logger:
    """取一个具名 logger（形如 `task.worker`）。"""
    if _FIELD.fullmatch(name.replace(".", "_")) is None:
        raise ValueError(f"logger 名不合法：{name!r}")
    return logging.getLogger(f"{_LOGGER_ROOT}.{name}")


def log_action(logger: logging.Logger, action: str, /, **ids: str) -> None:
    """记一条**只含 ID 与动作**的日志（R-O）。

    * `action` 形如 `attempt.graded` / `backup.failed`
    * `ids` 的键是字段名，值是 **ID 类字符串**；值不符合 ID 形状 ⇒ `ValueError`

    ★ 抛错是**故意**的：静默把正文写进日志，正是 R-O 要防的那件事。
    """
    if _ACTION.fullmatch(action) is None:
        raise ValueError(f"action 不合法（应为 attempt.graded 一类）：{action!r}")
    parts = [action]
    for key, value in ids.items():
        if _FIELD.fullmatch(key) is None:
            raise ValueError(f"字段名不合法：{key!r}")
        if not isinstance(value, str) or _ID_VALUE.fullmatch(value) is None:
            raise ValueError(
                f"字段 {key!r} 的值不是 ID 形状：{value!r}"
                "（R-O：日志只记 ID 与动作，不记内容 —— docs/02 §1.6）"
            )
        parts.append(f"{key}={value}")
    logger.info(" ".join(parts))
