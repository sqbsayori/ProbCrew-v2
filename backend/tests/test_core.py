"""`core/` 的用例（文件名与被测模块同名 —— `docs/05 §3`）。

覆盖三件事：配置读取（`docs/02 §8-15` 的 `WEB_CONCURRENCY`）· 统一错误体（`docs/02 §6.2` / `§6.3`）·
日志入口（R-O：只记 ID 与动作 + 目录与轮转参数）。
"""

from __future__ import annotations

import logging
import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:  # 本模块自足：不依赖卡1 的 conftest（见 _plan/W0b-阶段性计划.md）
    sys.path.insert(0, str(BACKEND))

from app.core import (  # noqa: E402
    ERROR_CODES,
    LOG_DIR,
    ApiError,
    ConfigError,
    error_body,
    get_logger,
    get_settings,
    log_action,
    setup_logging,
)
from app.core.logging import LOG_ROTATE_BACKUPS, LOG_ROTATE_MAX_BYTES  # noqa: E402


def test_web_concurrency_defaults_to_one() -> None:
    """缺省 1 —— 单 worker 约束的基线（`docs/02 §8-15`）。"""
    assert get_settings({}).web_concurrency == 1
    assert get_settings({"WEB_CONCURRENCY": "  "}).web_concurrency == 1


def test_web_concurrency_reads_env() -> None:
    assert get_settings({"WEB_CONCURRENCY": "2"}).web_concurrency == 2


@pytest.mark.parametrize("bad", ["0", "-1", "abc", "1.5", "２"])
def test_web_concurrency_rejects_invalid(bad: str) -> None:
    """非法值**抛错**而不是静默按 1 —— 配置写错要当场看得见。"""
    with pytest.raises(ConfigError):
        get_settings({"WEB_CONCURRENCY": bad})


def test_log_dir_is_backend_data_logs() -> None:
    """R-O 把日志目录定死在 `backend/data/logs/`。"""
    assert LOG_DIR.parts[-3:] == ("backend", "data", "logs")


def test_error_body_shape_without_detail() -> None:
    err = ApiError("unauthenticated", "未登录或登录已过期，请重新登录")
    assert error_body(err) == {
        "error": {"code": "unauthenticated", "message": "未登录或登录已过期，请重新登录"}
    }


def test_error_body_keeps_detail() -> None:
    err = ApiError("invalid_argument", "参数不合法", {"field": "answer", "reason": "too_long"})
    assert error_body(err)["error"]["detail"] == {"field": "answer", "reason": "too_long"}
    assert err.status == 400


def test_error_codes_match_docs_02_6_3() -> None:
    """易混淆的几条：凭据类错误**不得复用 401**（`docs/02 §6.3` 的"最容易踩的坑"）。"""
    assert ERROR_CODES["unauthenticated"] == 401
    assert ERROR_CODES["bad_credentials"] == 401
    assert ERROR_CODES["model_key_invalid"] == 400, "凭据无效是客户端可修的输入问题，不是身份问题"
    assert ERROR_CODES["model_key_missing"] == 503
    assert ERROR_CODES["model_unreachable"] == 503
    assert ERROR_CODES["model_quota_exceeded"] == 402
    assert ERROR_CODES["must_change_pw"] == 403
    assert ERROR_CODES["forbidden"] == 403
    assert ERROR_CODES["account_disabled"] == 403
    assert ERROR_CODES["locked"] == 423
    assert ERROR_CODES["not_found"] == 404
    assert ERROR_CODES["server_busy"] == 503
    assert len(ERROR_CODES) == 16


def test_api_error_rejects_unknown_code() -> None:
    with pytest.raises(ValueError):
        ApiError("no_such_code", "x")


@pytest.fixture()
def log_dir() -> Iterator[Path]:
    """临时日志目录。

    ★ 不用 pytest 的 `tmp_path`：它在 Windows 上要先 `os.scandir` 一个共享的
    `%TEMP%\\pytest-of-<user>` 目录，而受限环境下那一步会被拒（实测 `PermissionError: WinError 5`）。
    自己用 `tempfile` 建一个，退出前先 `logging.shutdown()` 放掉文件句柄（否则临时目录删不掉）。
    """
    with tempfile.TemporaryDirectory() as tmp:
        yield Path(tmp)
        logging.shutdown()


def test_log_action_rejects_free_text(log_dir: Path) -> None:
    """R-O 的机制：想记正文就写不进去。"""
    setup_logging(log_dir)
    logger = get_logger("test.core")
    with pytest.raises(ValueError):
        log_action(logger, "chat.done", text="你好，这是我的提问原文")
    with pytest.raises(ValueError):
        log_action(logger, "Chat Done", user_id="usr_1")
    with pytest.raises(ValueError):
        log_action(logger, "chat.done", user_id="含 空格 的正文")


def test_log_rotation_params_and_no_content_in_file(log_dir: Path) -> None:
    """轮转 10MB × 5；写一条后日志里只有动作与 ID。"""
    assert (LOG_ROTATE_MAX_BYTES, LOG_ROTATE_BACKUPS) == (10 * 1024 * 1024, 5)
    setup_logging(log_dir)
    logger = get_logger("test.core")
    log_action(logger, "attempt.graded", user_id="usr_abc", item_id="prob_bayes_0012")
    for handler in logging.getLogger("probcrew").handlers:
        handler.flush()
    text = (log_dir / "app.log").read_text(encoding="utf-8")
    assert "attempt.graded" in text
    assert "usr_abc" in text
    assert "prob_bayes_0012" in text
