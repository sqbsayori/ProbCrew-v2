#!/usr/bin/env python3
"""`scripts/manage_users.py` —— 用户管理 CLI（**本仓"第一个管理员账号"的唯一入口** · F15）。

唯一权威（本脚本不重述口径，只落实现）：

- **接口不可创建管理员** → `docs/01` F15 · `docs/03 §8.4` 第 7 步 · `docs/02 §6.1` 的管理行；
  `role` 因此**只能**在这里给（`api/schemas.py` 的 `extra="forbid"` 与
  `api/admin.py` 对 `PATCH role=admin` 的拒绝，是另外两处结构）；
- **只存哈希 / 凭据不落日志** → N6 · R-J / R-M（明文密码只经 stdin 或交互式输入，永不回显、
  永不进 `student_audit.detail`）；
- **账号状态机** → `docs/02 §5.3`（`active` ⇄ `disabled` · `deleted` 是终点）；
- **重置密码的三件事** → 契约 `adminPasswordResetResult`（强制改密 + 令牌全撤 + 写审计）。

用法：

    python scripts/manage_users.py create --username teach01 --role admin
    python scripts/manage_users.py create --username s01 --password-stdin < pw.txt
    python scripts/manage_users.py list --role student
    python scripts/manage_users.py disable --username s01
    python scripts/manage_users.py enable  --username s01
    python scripts/manage_users.py reset-password --username s01 --generate
    python scripts/manage_users.py delete --username s01 --yes

★ **密码四种给法**：交互式（缺省，不回显）· `--password-stdin`（管道，**推荐**）·
  `--password`（会进 shell 历史，仅供脚本与用例）· `--generate`（随机生成，**只打印一次**）。
★ **库路径**走 `PROBCREW_DB_PATH`（与 `core/config.py` 同一个旋钮）—— 用例与运维演练靠它换库。
★ **审计**：`reset-password` / `delete` 各写一条（`reset_password` / `delete_user`，
  `actor_id` 写 **`cli`**、`actor_role` 写 `admin`）—— 审计要能回答"这次是谁做的"，
  而命令行没有登录账号，`cli` 是**如实**的回答（不是编一个不存在的用户 ID）。
  `create` **不写审计**：契约 `auditRow.action` 的 8 个取值里没有 `create_user`，
  按 `docs/04 §6` 的口径**不自行扩张枚举**。
"""
from __future__ import annotations

import argparse
import getpass
import secrets
import sys
from pathlib import Path

#: 与 `backend/tests/conftest.py`、`scripts/backup.py` 同一手法：把 `backend/` 放进 `sys.path`。
REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.core import db  # noqa: E402
from app.core.errors import AppError  # noqa: E402
from app.identity import audit, auth, tokens, users  # noqa: E402
from app.learning import store  # noqa: E402

#: 命令行操作者在审计里的 `actor_id`（**不是**一个账号 —— 如实写"命令行"）。
CLI_ACTOR = "cli"
#: 随机密码的字符集与长度（去掉易混的 `0/O/1/l/I`）。
_PASSWORD_ALPHABET = "abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_GENERATED_LENGTH = 16


def _read_password(args: argparse.Namespace) -> str:
    """按优先级取新密码：`--generate` > `--password-stdin` > `--password` > 交互式。"""
    if getattr(args, "generate", False):
        return "".join(secrets.choice(_PASSWORD_ALPHABET) for _ in range(_GENERATED_LENGTH))
    if getattr(args, "password_stdin", False):
        return sys.stdin.readline().rstrip("\n")
    if getattr(args, "password", None):
        return args.password
    first = getpass.getpass("新密码（不回显）：")
    if first != getpass.getpass("再输一次："):
        raise SystemExit("[中止] 两次输入不一致")
    return first


def _resolve_user_id(conn, args: argparse.Namespace) -> str:
    """`--user-id` 或 `--username` 定位账号；找不到 ⇒ 退出码 1（不静默成功）。"""
    if args.user_id:
        return args.user_id
    found = users.find_by_username(conn, args.username)
    if found is None:
        raise SystemExit(f"[未找到] 用户名 {args.username!r} 不存在")
    return found["user_id"]



def _cmd_create(conn, args: argparse.Namespace) -> int:
    password = _read_password(args)
    owner = users.create_user(
        conn,
        username=args.username,
        password=password,
        display_name=args.display_name,
        role=args.role,
        created_by=args.created_by or CLI_ACTOR,
        must_change_pw=args.must_change_pw,
    )
    if args.generate:
        # ★ 明文只在这里出现一次（N6）：之后库里与任何日志里都不可能再取回它。
        print(f"[生成密码] {password}")
    print(f"[已创建] {owner['user_id']} · {owner['username']} · role={owner['role']} · status={owner['status']}")
    return 0


def _cmd_list(conn, args: argparse.Namespace) -> int:
    rows, _ = users.list_users_page(
        conn, role=args.role, status=args.status, limit=args.limit, cursor=None
    )
    if not rows:
        print("[空] 没有符合条件的账号")
        return 0
    print(f"{'user_id':20} {'username':24} {'role':8} {'status':9} {'fail':>4} {'tokens':>6}  locked")
    for row in rows:
        active = tokens.active_token_count(conn, row["user_id"])
        locked = "yes" if row.get("locked_until") else ""
        print(
            f"{row['user_id']:20} {row['username']:24} {row['role']:8} {row['status']:9}"
            f" {int(row.get('failed_count') or 0):>4} {active:>6}  {locked}"
        )
    return 0


def _cmd_set_status(conn, args: argparse.Namespace, status: str) -> int:
    user_id = _resolve_user_id(conn, args)
    users.update_user_fields(conn, user_id, status=status)
    print(f"[已{'启用' if status == 'active' else '停用'}] {user_id}")
    return 0


def _cmd_reset_password(conn, args: argparse.Namespace) -> int:
    user_id = _resolve_user_id(conn, args)
    password = _read_password(args)
    revoked = auth.admin_reset_password(conn, user_id, password)
    audit.write_audit(
        conn,
        actor_id=CLI_ACTOR,
        actor_role="admin",
        action="reset_password",
        target_id=user_id,
    )
    if args.generate:
        print(f"[生成密码] {password}")
    print(f"[已重置] {user_id} · 强制下次登录改密 · 撤销令牌 {revoked} 条")
    return 0


def _cmd_delete(conn, args: argparse.Namespace) -> int:
    if not args.yes:
        raise SystemExit("[中止] 删除不可恢复（数据硬删 + 账号脱敏）—— 确认请加 --yes")
    user_id = _resolve_user_id(conn, args)
    owner = users.find_by_id(conn, user_id)
    if owner is None or owner["status"] == "deleted":
        raise SystemExit(f"[未找到] {user_id} 不存在或已删除")
    before = sum(store.count_user_rows(conn, user_id).values())
    store.delete_user_data(conn, user_id)
    revoked = users.anonymize_user(conn, user_id)
    audit.write_audit(
        conn,
        actor_id=CLI_ACTOR,
        actor_role="admin",
        action="delete_user",
        target_id=user_id,
        detail={"deleted_rows": before},
    )
    print(f"[已删除] {user_id} · 数据行 {before} · 撤销令牌 {revoked} 条（审计保留）")
    return 0


def _add_target_arguments(sub: argparse.ArgumentParser) -> None:
    """三个"对已有账号动刀"的子命令共用同一组定位参数（**互斥**，不许同时给）。"""
    target = sub.add_mutually_exclusive_group(required=True)
    target.add_argument("--username")
    target.add_argument("--user-id")


def build_parser() -> argparse.ArgumentParser:
    """子命令 = "能做的六件事" —— 与 `docs/03 §8.4` 第 7 步 / `docs/07 §2.2` 的登记一致。"""
    parser = argparse.ArgumentParser(
        prog="manage_users.py",
        description="ProbCrew 用户管理 CLI（F15：接口不可创建管理员，管理员只能由本脚本创建）",
    )
    subs = parser.add_subparsers(dest="command", required=True)

    create = subs.add_parser("create", help="建账号（**唯一能建管理员的地方**）")
    create.add_argument("--username", required=True)
    create.add_argument("--display-name", default="")
    create.add_argument("--role", choices=("student", "admin"), default="student")
    create.add_argument("--password")
    create.add_argument("--password-stdin", action="store_true")
    create.add_argument("--generate", action="store_true")
    create.add_argument("--must-change-pw", action="store_true")
    create.add_argument("--created-by", default=CLI_ACTOR)

    listing = subs.add_parser("list", help="列出账号（**不含凭据**）")
    listing.add_argument("--role", choices=("student", "admin"))
    listing.add_argument("--status", choices=("active", "disabled", "deleted"))
    listing.add_argument("--limit", type=int, default=50)

    for name, help_text in (("disable", "停用账号（下次请求即 403）"), ("enable", "启用账号")):
        _add_target_arguments(subs.add_parser(name, help=help_text))

    reset = subs.add_parser("reset-password", help="重置密码（强制改密 + 撤销全部令牌）")
    _add_target_arguments(reset)
    reset.add_argument("--password")
    reset.add_argument("--password-stdin", action="store_true")
    reset.add_argument("--generate", action="store_true")

    delete = subs.add_parser("delete", help="删除账号（数据硬删 + 账号脱敏 + 审计保留）")
    _add_target_arguments(delete)
    delete.add_argument("--yes", action="store_true")
    return parser


def main(argv: "list[str] | None" = None) -> int:
    """入口：解析 → 开库（**唯一建库入口** `db.initialize`）→ 分发 → 关库。"""
    args = build_parser().parse_args(argv)
    conn = db.initialize()
    try:
        if args.command == "create":
            return _cmd_create(conn, args)
        if args.command == "list":
            return _cmd_list(conn, args)
        if args.command == "disable":
            return _cmd_set_status(conn, args, "disabled")
        if args.command == "enable":
            return _cmd_set_status(conn, args, "active")
        if args.command == "reset-password":
            return _cmd_reset_password(conn, args)
        return _cmd_delete(conn, args)
    except AppError as exc:
        # ★ 与 HTTP 面同一条口径：失败**如实报 code**，且不回显任何输入（凭据不进输出）。
        sys.stderr.write(f"[{exc.code}] {exc.message}\n")
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())

