"""``core/logging.py`` 的用例 —— 与被测模块同名（``docs/05 §3``）。

判据对照（``docs/02 §1.6`` 的 R-O · R-J）：

- R-O 只记 ID 与动作：非 ``*_id`` / 非白名单字段当场拒绝；
- 值超长**拒绝写入**（不静默截断）；
- ★ handler 未挂时**当场报错**（不接受「静默丢日志」）。
"""
from __future__ import annotations

import logging

import pytest

from app.core.logging import AUDIT_LOGGER_NAME, LogContentViolation, log_action, setup_logging


def _clear_audit() -> None:
    logger = logging.getLogger(AUDIT_LOGGER_NAME)
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
    logger.handlers.clear()


def test_log_action_rejects_non_id_field() -> None:
    with pytest.raises(LogContentViolation):
        log_action("grade", question="贝叶斯公式是啥")


def test_log_action_rejects_overlong_value() -> None:
    with pytest.raises(LogContentViolation):
        log_action("grade", attempt_id="x" * 129)


def test_log_action_fails_loud_without_setup() -> None:
    _clear_audit()
    import app.core.logging as logging_module

    logging_module._configured = False
    with pytest.raises(RuntimeError, match="setup_logging"):
        log_action("grade", attempt_id="a1")


def test_setup_logging_then_log_action_writes(tmp_path) -> None:
    setup_logging(tmp_path)
    line = log_action("grade", attempt_id="a1", status="ok")
    log = (tmp_path / "audit.log").read_text(encoding="utf-8")
    assert "action=grade" in log
    assert "attempt_id=a1" in log
    assert "status=ok" in log
    assert "action=grade attempt_id=a1 status=ok" == line