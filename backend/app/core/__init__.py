"""`core/` —— 基础设施：配置 / 统一错误体 / 日志。

★ 本包是 W0b（模块二）的边①：`graph/` 与 `tools/` 都 import 它，因此**这里的形状是冻结接口**
（改动走 `docs/05 §3` 规则 1 记账）。判据出处：`docs/06 §12.3` 卡2 · `docs/03 §2` · `docs/02 §1.6`（R-O）。

只放三样东西，不预设任何别的东西：配置读取 · 统一错误体构造 · 日志入口。
"""

from .config import DATA_DIR, LOG_DIR, ConfigError, Settings, get_settings
from .errors import ERROR_CODES, ApiError, error_body
from .logging import LOG_ROTATE_BACKUPS, LOG_ROTATE_MAX_BYTES, get_logger, log_action, setup_logging

__all__ = [
    "DATA_DIR",
    "LOG_DIR",
    "ConfigError",
    "Settings",
    "get_settings",
    "ERROR_CODES",
    "ApiError",
    "error_body",
    "LOG_ROTATE_MAX_BYTES",
    "LOG_ROTATE_BACKUPS",
    "get_logger",
    "log_action",
    "setup_logging",
]
