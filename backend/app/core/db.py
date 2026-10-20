"""L2 数据层入口（连接 + **唯一建表入口**）—— W1 · 首发建表项（docs/06 §14.2）。

★ **为什么这个文件在 `core/` 而不是 `identity/` 或 `learning/`** —— 这是推论，不是偏好：

1. `docs/02 §3.6` 要求「**只有一个建表入口**，幂等」（分两处建表必出"某个入口漏建了表"，
   症状是运行时的 ``no such table``）；
2. 七张表**横跨两域**：``student_user`` / ``student_token`` / ``student_audit`` 归 `identity/`，
   ``student_qa_log`` / ``student_hint_log`` / ``student_attempt`` / ``student_mastery`` 归 `learning/`
   （`docs/02 §3.1` 的"谁写"列）；
3. 而 `docs/03 §3.1` 的允许依赖是：`identity/` → `app.core` · `learning/` → `app.core` `app.domain`
   —— **`learning/` 不得 import `identity/`**（`scripts/verify.sh` 的 `import_direction()` 用白名单硬拦）；
4. ⇒ **唯一能同时被两者 import 的层只有 `core/`**。"一个入口"于是是**结构上成立**的，
   而不是靠"记得两边都建表"的自觉。

**代价（记账，`docs/05 §3` 规则 1）**：`docs/03 §2` 把 `core/` 描述为「配置 / 统一错误体 / 日志」，
本文件是**骨架增量**；`§3.1` 的 `core` 行同步说明。两处回写**归架构与集成**（`docs/**` 的唯一写入域），
本域只提供内容。

纪律（`docs/02 §3.6`，逐条落在这里）：
- 并发写：``WAL`` + ``busy_timeout=5000``（:func:`connect`）；
- 建表：**唯一入口 · 幂等**（:func:`ensure_schema` 全部 ``IF NOT EXISTS``，全部 < 5 条语句清单）；
- 写事务：**显式 ``commit()``**（:func:`ensure_schema` 末尾），连接生命周期由调用方管
  （HTTP 面是 `api/deps.py` 的依赖 ``yield``；CLI 与用例用 ``with closing(...)``）；
- 备份**不得文件级 ``cp``**：开了 WAL 之后必须 ``Connection.backup()``（属 N19 / `scripts/backup.py`，
  W5 · 域1；本文件只保证 WAL 真的开着，用例断言 ``PRAGMA journal_mode == 'wal'``）。

纯标准库（`sqlite3`，无 ORM —— `docs/02 §1.2`）。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from app.core.config import DB_FILENAME, Settings, load_settings

__all__ = [
    "BUSY_TIMEOUT_MS",
    "DB_FILENAME",
    "SCHEMA_SQL",
    "TABLE_NAMES",
    "connect",
    "db_path",
    "ensure_schema",
    "initialize",
]

#: 并发争用的自动重试窗口（`docs/02 §3.6` 首次取值：5000ms）。
BUSY_TIMEOUT_MS = 5000

#: 七张表（`docs/02 §3.1`）。用例逐条断言它们都在 `sqlite_master` 里。
TABLE_NAMES: tuple[str, ...] = (
    "student_user",
    "student_token",
    "student_audit",
    "student_qa_log",
    "student_hint_log",
    "student_attempt",
    "student_mastery",
)

#: 建表语句清单（幂等）。**只增不改**：已发布后改列须走迁移，而 v2 是新库、无迁移负担（ADR-0003）。
SCHEMA_SQL: tuple[str, ...] = (
    # ---- identity/ 三张 -------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS student_user (
      user_id        TEXT PRIMARY KEY,
      username       TEXT NOT NULL COLLATE NOCASE UNIQUE,
      password_hash  TEXT NOT NULL,
      display_name   TEXT NOT NULL DEFAULT '',
      role           TEXT NOT NULL CHECK (role IN ('student','admin')),
      status         TEXT NOT NULL CHECK (status IN ('active','disabled','deleted')),
      created_at     REAL NOT NULL,
      created_by     TEXT,
      last_login_at  REAL,
      failed_count   INTEGER NOT NULL DEFAULT 0,
      locked_until   REAL,
      must_change_pw INTEGER NOT NULL DEFAULT 0 CHECK (must_change_pw IN (0,1)),
      notice_ack_at  REAL
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_user_role_status ON student_user(role, status)",
    """
    CREATE TABLE IF NOT EXISTS student_token (
      token_hash TEXT PRIMARY KEY,
      user_id    TEXT NOT NULL REFERENCES student_user(user_id) ON DELETE CASCADE,
      issued_at  REAL NOT NULL,
      expires_at REAL NOT NULL,
      revoked    INTEGER NOT NULL DEFAULT 0 CHECK (revoked IN (0,1))
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_token_user_revoked ON student_token(user_id, revoked)",
    """
    CREATE TABLE IF NOT EXISTS student_audit (
      audit_id   TEXT PRIMARY KEY,
      actor_id   TEXT NOT NULL,
      actor_role TEXT NOT NULL CHECK (actor_role IN ('student','admin')),
      target_id  TEXT,
      action     TEXT NOT NULL,
      detail     TEXT NOT NULL DEFAULT '{}',
      created_at REAL NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_audit_actor ON student_audit(actor_id, created_at)",
    "CREATE INDEX IF NOT EXISTS ix_audit_target ON student_audit(target_id, created_at)",
    # ---- learning/ 四张 -------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS student_qa_log (
      qa_id      TEXT PRIMARY KEY,
      user_id    TEXT NOT NULL REFERENCES student_user(user_id) ON DELETE CASCADE,
      query      TEXT NOT NULL,
      answer     TEXT NOT NULL,
      provider   TEXT,
      model      TEXT,
      egress     TEXT NOT NULL CHECK (egress IN ('loopback','remote')),
      created_at REAL NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_qa_user_created ON student_qa_log(user_id, created_at)",
    """
    CREATE TABLE IF NOT EXISTS student_hint_log (
      hint_id    TEXT PRIMARY KEY,
      user_id    TEXT NOT NULL REFERENCES student_user(user_id) ON DELETE CASCADE,
      item_id    TEXT NOT NULL,
      session_id TEXT NOT NULL,
      level      INTEGER NOT NULL CHECK (level BETWEEN 1 AND 3),
      served_at  REAL NOT NULL,
      attempt_id TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_hint_user_item_session ON student_hint_log(user_id, item_id, session_id)",
    "CREATE INDEX IF NOT EXISTS ix_hint_user_item ON student_hint_log(user_id, item_id)",
    """
    CREATE TABLE IF NOT EXISTS student_attempt (
      attempt_id        TEXT PRIMARY KEY,
      user_id           TEXT NOT NULL REFERENCES student_user(user_id) ON DELETE CASCADE,
      item_id           TEXT NOT NULL,
      session_id        TEXT NOT NULL,
      kc_ids            TEXT NOT NULL CHECK (kc_ids <> '[]'),
      correct           INTEGER,
      score             REAL,
      answer_raw        TEXT NOT NULL,
      answer_normalized TEXT,
      expected          TEXT NOT NULL,
      hint_used         INTEGER NOT NULL DEFAULT 0,
      duration_ms       INTEGER,
      attempt_no        INTEGER NOT NULL,
      source            TEXT NOT NULL,
      grader            TEXT NOT NULL,
      created_at        REAL NOT NULL,
      asset_version     TEXT NOT NULL,
      mistake_id        TEXT,
      mistake_matched   INTEGER NOT NULL DEFAULT 0 CHECK (mistake_matched IN (0,1)),
      UNIQUE (user_id, item_id, attempt_no)
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_attempt_user_created ON student_attempt(user_id, created_at DESC, attempt_id)",
    "CREATE INDEX IF NOT EXISTS ix_attempt_user_item ON student_attempt(user_id, item_id)",
    """
    CREATE TABLE IF NOT EXISTS student_mastery (
      user_id    TEXT NOT NULL REFERENCES student_user(user_id) ON DELETE CASCADE,
      topic      TEXT NOT NULL,
      attempts   INTEGER NOT NULL DEFAULT 0,
      correct    INTEGER NOT NULL DEFAULT 0,
      updated_at REAL NOT NULL,
      PRIMARY KEY (user_id, topic)
    )
    """,
)

#: 已建表的库文件（进程内缓存）—— 让"每个请求都建一次表"不必发生，而入口**仍然只有一个**。
_initialized: set[str] = set()


def db_path(settings: "Settings | None" = None) -> Path:
    """L2 库文件路径：默认 ``backend/data/<DB_FILENAME>``，``PROBCREW_DB_PATH`` 可覆盖。"""
    resolved = load_settings() if settings is None else settings
    return Path(resolved.db_path)


def connect(path: "str | Path | None" = None) -> sqlite3.Connection:
    """开一个连接，并把三条 PRAGMA 摆好（`docs/02 §3.6`）。

    - ``journal_mode=WAL`` —— 读写不互相阻塞（单 worker 下前端轮询不会卡住作答）；
    - ``busy_timeout=5000`` —— 短时争用自动重试而不是立刻 ``database is locked``；
    - ``foreign_keys=ON`` —— **必须显式开**（SQLite 默认关）：关掉时
      ``student_token`` / 四张学习表的 ``ON DELETE CASCADE`` 会**静默不生效**，
      于是 F16 的"删除读回 0 行"会出现"删了账号、行还在"。

    ★ 连接是**每次调用一个新对象**（sqlite3 的连接不可跨线程共享，FastAPI 的依赖会在线程池里跑）。
    父目录不存在时按需创建 —— `backend/data/`（运行时目录）不在版本库里，首次运行必须能自建。

    ★ **`check_same_thread=False`（2026-10-20 补）**：连接是**请求级**的（`api/deps.py` 的
      ``yield`` 用完即关），但 FastAPI 可能**在线程池里创建依赖、在事件循环里执行 async 端点**
      —— 两者不是同一个线程。sqlite3 默认禁止跨线程使用，症状是 W2 的 `POST /api/practice/grade`
      第一次被调用就 ``ProgrammingError``。本仓的连接**不跨请求共享**，因此关掉这条检查是安全的；
      它也是 W3 的异步答疑端点在同一个请求里写库的前提。
    """
    target = db_path() if path is None else Path(path)
    parent = target.parent
    if str(parent) not in ("", "."):
        parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(target), timeout=BUSY_TIMEOUT_MS / 1000, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def ensure_schema(conn: sqlite3.Connection) -> None:
    """建七张表与九处索引（+ 一处唯一约束）——**幂等**：连跑两次不报错、表数不变。

    ★ 全部语句都是 ``IF NOT EXISTS``：这是"唯一入口必须幂等"的实现形式 ——
      同一个入口既被"首次启动"调用、也被"每次请求"调用（:func:`initialize` 会缓存），
      若不幂等，第二次调用就成了"启动即崩"的 bug。
    """
    for statement in SCHEMA_SQL:
        conn.execute(statement)
    conn.commit()


def initialize(path: "str | Path | None" = None) -> sqlite3.Connection:
    """**全仓唯一的建库入口**：``connect()`` + （每个库文件首次）``ensure_schema()``。

    三处共用它：`api/deps.py` 的请求依赖 · CLI（`scripts/manage_users.py`，W1 后续）· 用例。
    """
    target = db_path() if path is None else Path(path)
    conn = connect(target)
    key = str(target)
    if key not in _initialized:
        ensure_schema(conn)
        _initialized.add(key)
    return conn
