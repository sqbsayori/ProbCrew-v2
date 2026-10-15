#!/usr/bin/env python3
"""`scripts/backup.py` —— 备份与恢复（N19）· 架构与集成 · 阶段门 B 第 9 项演练 ②③。

唯一权威（本脚本不重述口径，只落实现）：
- 位置 / 内容 / 取法 / 恢复读回 → `docs/03 §8.3`
- 演练节奏（首次恢复 · 每两周 · schema/`content_rev` 变更后追加）→ `docs/06 §6` 演练②③
- 验收四条（落本地 / L2 + rev 快照 / 恢复读回 / 失败可观测）→ `docs/01 §6` N19
- 新鲜度字段形状 → `contracts/registry.schema.json` v1.2 的 `backup` 分支

用法：:

    python scripts/backup.py backup  [--data-dir backend/data] [--content-dir content]
    python scripts/backup.py restore [--data-dir backend/data]

★ 三条纪律：
1. **WAL 下不得文件级 `cp`** —— 一律 `sqlite3.Connection.backup()` 取一致快照（N19 验收②）。
2. **失败可观测** —— 每次运行写 `backend/data/backups/last_backup.json` 并落一条只含 ID 与动作
   的审计日志（R-O，经 `app.core.logging.log_action`；失败也记，绝不静默）。
3. **不伪造成功**（N2）—— 找不到 `*.sqlite` 时如实记 `failed`，而不是假装备份了空库。
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

#: 让 `from app.core.logging import …` 成立（与 backend/tests/conftest.py 同一手法）。
REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.core.logging import log_action, setup_logging  # noqa: E402

#: N19 验收③ 要逐表读回的四个学生表（`docs/03 §8.3` · `docs/02 §3.x`）。
STUDENT_TABLES = ("student_user", "student_attempt", "student_mastery", "student_hint_log")

#: `last_backup.json` 的时间戳格式（契约 `backup.last_success_at` 的 pattern 是 ``…Z``）。
_TS_FMT = "%Y-%m-%dT%H:%M:%SZ"
#: 备份文件名后缀（可字典序排序，便于 restore 找最新一批）。
_FILE_TS_FMT = "%Y%m%dT%H%M%SZ"


def _now() -> str:
    return datetime.now(timezone.utc).strftime(_TS_FMT)


def _read_last(backups_dir: Path) -> dict:
    """读 `last_backup.json`；不存在 = 还没排过（`never`）。"""
    path = backups_dir / "last_backup.json"
    if not path.exists():
        return {"last_success_at": None, "last_result": "never", "last_failure_at": None}
    return json.loads(path.read_text(encoding="utf-8"))


def _write_last(backups_dir: Path, *, success: bool) -> None:
    """写新鲜度状态：成功只动 `last_success_at`，失败只动 `last_failure_at`（`failed` ≠ `never`）。"""
    backups_dir.mkdir(parents=True, exist_ok=True)
    previous = _read_last(backups_dir)
    now = _now()
    record = {
        "last_success_at": now if success else previous.get("last_success_at"),
        "last_result": "ok" if success else "failed",
        "last_failure_at": None if success else now,
    }
    (backups_dir / "last_backup.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def _snapshot_sqlite(src: Path, dest: Path) -> None:
    """WAL 下的一致快照：`Connection.backup()`，不是文件级 `cp`（`docs/03 §8.3`）。"""
    source = sqlite3.connect(str(src))
    try:
        target = sqlite3.connect(str(dest))
        try:
            source.backup(target)
        finally:
            target.close()
    finally:
        source.close()


def _table_rows(conn: sqlite3.Connection, table: str) -> int:
    """逐表读回行数；表不存在返回 -1（「没读」与「读到 0 行」必须长得不一样）。"""
    try:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    except sqlite3.OperationalError:
        return -1


def run_backup(data_dir: Path, content_dir: Path) -> int:
    setup_logging(data_dir / "logs")
    backups_dir = data_dir / "backups"
    backups_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime(_FILE_TS_FMT)

    db_files = sorted(data_dir.glob("*.sqlite"))
    if not db_files:
        _write_last(backups_dir, success=False)
        log_action("backup", status="failed", reason="no_db")
        print("[失败] 没有可备份的 *.sqlite（L2 学生库尚未产出，属 W1–W4）—— 如实记 failed，不伪造成功")
        return 1

    for db in db_files:
        _snapshot_sqlite(db, backups_dir / f"{db.stem}-{stamp}.sqlite")

    rev = None
    manifest = content_dir / "MANIFEST.json"
    if manifest.exists():
        rev = json.loads(manifest.read_text(encoding="utf-8")).get("content_rev")
    if rev is not None:
        (backups_dir / f"manifest-rev-{stamp}.json").write_text(
            json.dumps({"content_rev": rev}, ensure_ascii=False) + "\n", encoding="utf-8"
        )

    _write_last(backups_dir, success=True)
    log_action("backup", status="ok")
    print(f"[成功] 备份 {len(db_files)} 个库到 {backups_dir}（content_rev={rev}）")
    return 0


def run_restore(data_dir: Path) -> int:
    setup_logging(data_dir / "logs")
    backups_dir = data_dir / "backups"
    backups = sorted(backups_dir.glob("*-*.sqlite")) if backups_dir.exists() else []
    if not backups:
        log_action("restore", status="failed", reason="no_backup")
        print("[失败] 没有可恢复的备份 —— 先跑一次 `backup`")
        return 1

    # 取文件名时间戳最新的一批（同一时刻的多个库是一组）。
    def _stamp(path: Path) -> str:
        return path.stem.rsplit("-", 1)[-1]

    latest = max(_stamp(p) for p in backups)
    batch = [p for p in backups if _stamp(p) == latest]

    for backup_file in batch:
        live = data_dir / f"{backup_file.stem.rsplit('-', 1)[0]}.sqlite"
        _snapshot_sqlite(backup_file, live)

    counts: dict[str, int] = {}
    for db in sorted(data_dir.glob("*.sqlite")):
        conn = sqlite3.connect(str(db))
        try:
            for table in STUDENT_TABLES:
                counts[table] = _table_rows(conn, table)
        finally:
            conn.close()

    log_action("restore", status="ok")
    print(f"[成功] 恢复 {len(batch)} 个库；逐表读回（-1 = 表不存在）：{counts}")
    return 0


def main(argv: "list[str] | None" = None) -> int:
    parser = argparse.ArgumentParser(
        prog="scripts/backup.py",
        description="N19 备份与恢复（sqlite3 一致快照 · 新鲜度 · R-O 日志）",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    backup = sub.add_parser("backup", help="备份 L2 的 *.sqlite + MANIFEST rev 快照")
    backup.add_argument("--data-dir", default=str(REPO_ROOT / "backend" / "data"))
    backup.add_argument("--content-dir", default=str(REPO_ROOT / "content"))

    restore = sub.add_parser("restore", help="从最近一批备份恢复，并逐表读回四张学生表")
    restore.add_argument("--data-dir", default=str(REPO_ROOT / "backend" / "data"))

    args = parser.parse_args(argv)
    if args.command == "backup":
        return run_backup(Path(args.data_dir), Path(args.content_dir))
    return run_restore(Path(args.data_dir))


if __name__ == "__main__":
    sys.exit(main())
