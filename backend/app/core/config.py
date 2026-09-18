"""配置读取 —— `core/` 的第一件东西。

★ **本模块只承载"已有出处的配置项"**，不发明第二处口径（`docs/01` 承诺 11：一处记载）。
当前全仓有出处的只有两条：

* **`WEB_CONCURRENCY`** —— 单 worker 约束的检测信号，缺省 1（`docs/02 §8-15`，
  原文标注"首次取值（待负责人确认）"）。★ **本模块只提供这个值**：
  "`>1` 拒绝启动并说明原因"的实现点在 `backend/app/main.py`，属架构与集成域（`docs/06 §12.3` 卡1 步骤②）。
* **`backend/data/` 下的目录** —— 日志目录由 R-O 定死为 `backend/data/logs/`
  （`docs/02 §1.6` R-O · `docs/03 §2`）。

`.env` 的键名在 v2 全仓**没有权威定义**（只在 `.gitignore` 的忽略规则里出现过），
因此这里不预设任何键 —— 等 W1/W2 有出处时再扩，扩的时候只改本文件。
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from os import environ

__all__ = ["DATA_DIR", "LOG_DIR", "ConfigError", "Settings", "get_settings"]


class ConfigError(ValueError):
    """静态配置错误。

    ★ **不静默兜底**：配置写错时按错的值继续跑，会把"配错了"伪装成"运行行为怪"
    （与 `docs/02 §8-14` 的"资产坏了宁可起不来"同一条理由）。
    """


#: 仓库内的运行时目录（`docs/03 §2`）：`<repo>/backend/data`
DATA_DIR: Path = Path(__file__).resolve().parents[2] / "data"

#: 日志目录 —— R-O 定死（`docs/02 §1.6`），轮转参数见 `core/logging.py`
LOG_DIR: Path = DATA_DIR / "logs"

#: 单 worker 检测的环境变量名与缺省值（`docs/02 §8-15`）
WEB_CONCURRENCY_ENV: str = "WEB_CONCURRENCY"
DEFAULT_WEB_CONCURRENCY: int = 1

_POSITIVE_INT = re.compile(r"[1-9][0-9]*")


@dataclass(frozen=True)
class Settings:
    """运行期配置的**最小公共形状**。

    ★ 字段只有三个，且都各有出处；**不要在这里加"以后可能用得上"的字段** ——
    每加一个都要先有权威出处，否则它就是第二处口径。
    """

    data_dir: Path = DATA_DIR
    log_dir: Path = LOG_DIR
    web_concurrency: int = DEFAULT_WEB_CONCURRENCY


def _read_web_concurrency(source: Mapping[str, str]) -> int:
    raw = source.get(WEB_CONCURRENCY_ENV)
    if raw is None or raw.strip() == "":
        return DEFAULT_WEB_CONCURRENCY
    text = raw.strip()
    if _POSITIVE_INT.fullmatch(text) is None:
        raise ConfigError(
            f"{WEB_CONCURRENCY_ENV} 必须是正整数，实际为 {raw!r}"
            "（缺省 1；单 worker 约束见 docs/02 §8-15）"
        )
    return int(text)


def get_settings(env: Mapping[str, str] | None = None) -> Settings:
    """读一次配置。

    `env` 只为测试注入用（`None` ⇒ 读进程环境），这样用例不必改全局状态。
    """
    source = environ if env is None else env
    return Settings(web_concurrency=_read_web_concurrency(source))
