"""配置读取 —— W0b · 卡2 · 第①步（docs/06 §12.3）。

W0 最小集只含两个路径配置：
- ``log_dir``     日志目录（R-O：默认 ``backend/data/logs/``，docs/02 §1.4 规则 1 / ADR-0005）
- ``content_dir`` 内容资产目录（默认 ``content/``，W2 判定链吃题库时使用）

★ **W1 增量（2026-10-16）**：加第三个路径 ``db_path`` —— L2 库文件（``docs/03 §2`` 只写了
``backend/data/*.sqlite``，**没定文件名**；首次取值见 :data:`DB_FILENAME`）。它放在本模块而不是
`core/db.py` 的原因：配置与**读配置的模块**不能互相 import（`core` 的允许依赖只有 `app.core`，
`db.py` 从本模块取，方向单向）。⇒ 库文件名这件"配置"与日志目录、内容目录同级。
★ **跨域记账（`docs/05 §3` 规则 1）**：`docs/03 §2` 的骨架行需补这个文件名，`docs/**`
的唯一写入域是**架构与集成** ⇒ 本模块只提供取值，落笔请域1 执行。

约定：
- 环境变量前缀 ``PROBCREW_``（``PROBCREW_LOG_DIR`` / ``PROBCREW_CONTENT_DIR`` / ``PROBCREW_DB_PATH``）；
- 纯标准库实现（不引入 pydantic-settings；依赖口径归模块一，docs/03 §8.4 三个待定问题）；
- 路径解析失败不在此处兜底 —— 目录由使用方按需创建（logging 模块创建日志目录，db 模块创建库目录）。
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ENV_PREFIX = "PROBCREW_"

#: 日志轮转参数（docs/02 §1.4 规则 1：10MB × 5）
LOG_MAX_BYTES = 10 * 1024 * 1024
LOG_BACKUP_COUNT = 5

#: ★ **首次取值**（W1，2026-10-16）：L2 库文件名。`docs/03 §2` 只写到 `backend/data/*.sqlite`。
#: `backend/data/**` 是 deny-by-default 忽略的运行时目录（`.gitignore`），所以这个文件名
#: **不需要入仓白名单**；但它是"库在哪"的唯一来源，散在各处就会出现"两个进程开两个库"。
DB_FILENAME = "probcrew.sqlite"


@dataclass(frozen=True)
class Settings:
    """进程级只读配置。"""

    log_dir: Path
    content_dir: Path
    #: ★ W1 增量：L2 库文件（`backend/data/<DB_FILENAME>`）。
    db_path: Path
    log_max_bytes: int = LOG_MAX_BYTES
    log_backup_count: int = LOG_BACKUP_COUNT


def _repo_root() -> Path:
    """仓库根 = ``backend/app/core/config.py`` 上溯 3 级。"""
    return Path(__file__).resolve().parents[3]


def load_settings(env: "dict[str, str] | None" = None) -> Settings:
    """从环境变量读取配置；未设置时取仓库内的约定默认值。

    显式接收 ``env`` 参数是为了可测试（纯函数风格，无隐式全局读取）。
    """
    source = os.environ if env is None else env
    root = _repo_root()
    return Settings(
        log_dir=Path(source.get(ENV_PREFIX + "LOG_DIR", str(root / "backend" / "data" / "logs"))),
        content_dir=Path(source.get(ENV_PREFIX + "CONTENT_DIR", str(root / "content"))),
        db_path=Path(source.get(ENV_PREFIX + "DB_PATH", str(root / "backend" / "data" / DB_FILENAME))),
    )
