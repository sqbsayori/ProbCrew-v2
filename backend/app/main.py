"""FastAPI 应用入口（骨架）—— 卡 1 · W0a 第 ②③ 步。

★ 本轮（W0a）**只做三件事**，别的一行不写（`docs/06 §12.6` 规则 1：本轮不写任何业务端点）：

1. **单 worker 启动检测** —— 判据②：并发度 > 1 时**拒绝启动并说明原因**（`docs/02 §8-15`）。
   信号变量 `WEB_CONCURRENCY`（缺省 1）是 `docs/02 §8-15` 的**首次取值**；
   理由是 uvicorn / gunicorn 生态的标准变量（`uvicorn --workers` 读的就是它）。
   ★ 演示方式：`WEB_CONCURRENCY=2 python -m app.main` 必须拒绝启动。
2. **同源静态托管 `frontend/`** —— 服务根（`/`）就是 `frontend/index.html`；
   **不另开静态服务器、不配 CORS**，前端一律走相对 `/api/*`（`docs/03 §8.4`）。
3. 给出可被 uvicorn 直接使用的应用对象 `app`。

★ **`/api/*` 为零**：本轮一个业务端点都没有。后续工作包（W1–W4）在 `api/` 里加路由，
   由本文件按 `include_router` 挂载 —— ★ **必须挂在文件末尾那次 `mount("/")` 之前**，
   否则 `Mount("/")` 会先匹配掉一切（Starlette 按注册顺序匹配）。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Mapping

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

__all__ = [
    "CONCURRENCY_ENV",
    "DEFAULT_CONCURRENCY",
    "FRONTEND_DIR",
    "HOST",
    "PORT",
    "app",
    "assert_single_worker",
    "main",
    "resolve_concurrency",
]

# ---- 路径 -------------------------------------------------------------------

#: 仓库根（`backend/app/main.py` → 上三级）。
REPO_ROOT = Path(__file__).resolve().parents[2]
#: 同源托管的服务根（`docs/03 §2`：`frontend/index.html` 是唯一 HTML）。
FRONTEND_DIR = REPO_ROOT / "frontend"

# ---- 单 worker 检测 ---------------------------------------------------------

#: 并发度信号变量（`docs/02 §8-15` 首次取值，待负责人确认）。
CONCURRENCY_ENV = "WEB_CONCURRENCY"
#: 缺省并发度：**1**。为 1 而不是"未设置" —— 图检查点是内存态（`docs/02 §5.1`），
#: 多 worker 会表现为"随机丢 run / 事件串台"，而症状看起来像前端 bug。
DEFAULT_CONCURRENCY = 1
#: 拒绝启动时使用的退出码（与"启动失败"同一类，不是 0）。
REFUSE_EXIT_CODE = 2

# ---- 监听地址 ---------------------------------------------------------------

#: 监听地址与端口（★ 首次取值：全仓文档没有指定过 host/port）。
#: 默认只监听本机回环；`PORT` 可覆盖（同 `WEB_CONCURRENCY` 一样取生态里的通用变量）。
HOST = "127.0.0.1"
DEFAULT_PORT = 8000
PORT_ENV = "PORT"


def resolve_concurrency(env: Mapping[str, str] | None = None) -> int:
    """把并发度信号解析成正整数。

    - **未设置或空串** ⇒ {@link DEFAULT_CONCURRENCY}（空串在 shell 里与未设置等价）。
    - **不是正整数** ⇒ 抛 `ValueError` —— 坏输入**不静默降级**（本仓不接受"没报错就算过"）。
    """
    raw = (os.environ if env is None else env).get(CONCURRENCY_ENV, "")
    if raw.strip() == "":
        return DEFAULT_CONCURRENCY
    try:
        value = int(raw.strip())
    except ValueError as exc:
        raise ValueError(
            f"{CONCURRENCY_ENV}={raw!r} 不是整数 —— 无法判断 worker 数，拒绝启动。"
        ) from exc
    if value < 1:
        raise ValueError(f"{CONCURRENCY_ENV}={raw!r} 必须 ≥ 1 —— 拒绝启动。")
    return value


def assert_single_worker(concurrency: int) -> None:
    """并发度必须恰好为 1，否则**打印原因并以非零码退出**（`docs/02 §8-15` 判据②）。"""
    if concurrency == 1:
        return
    sys.stderr.write(
        f"[拒绝启动] 检测到 {CONCURRENCY_ENV}={concurrency}，而本服务**只允许单 worker**。\n"
        "  原因：图检查点是**内存态**（docs/02 §5.1）—— 多 worker 会出现"
        "\"随机丢 run / 事件串台\"，而症状看起来像前端 bug（docs/02 §8-15）。\n"
        f"  处理：去掉 {CONCURRENCY_ENV} 或设为 1（本服务是单进程入口，没有 --workers 的位置）。\n"
    )
    raise SystemExit(REFUSE_EXIT_CODE)


# ---- 应用对象 ---------------------------------------------------------------

app = FastAPI(
    title="ProbCrew · 概率论伴学助手",
    description="W0a 骨架：只有同源静态托管与单 worker 启动检测，本轮没有任何业务端点。",
)

# ⚠️ 后续工作包的 `app.include_router(...)` 一律加在**本行之上**。
# 同源静态托管：`/` 就是 `frontend/`，`html=True` 让目录请求回落到 `index.html`。
# ★ 不配 CORS —— 前后端同源（`docs/03 §8.4`），没有跨域这回事。
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")


def resolve_port(env: Mapping[str, str] | None = None) -> int:
    """监听端口：未设置用 {@link DEFAULT_PORT}；坏值当场报错（不静默降级）。"""
    raw = (os.environ if env is None else env).get(PORT_ENV, "")
    if raw.strip() == "":
        return DEFAULT_PORT
    try:
        value = int(raw.strip())
    except ValueError as exc:
        raise ValueError(f"{PORT_ENV}={raw!r} 不是整数 —— 拒绝启动。") from exc
    if not (1 <= value <= 65535):
        raise ValueError(f"{PORT_ENV}={raw!r} 不在 1–65535 —— 拒绝启动。")
    return value


def main() -> None:
    """`docs/03 §8.4` 第 2 步的入口：`cd backend && python -m app.main`。"""
    try:
        concurrency = resolve_concurrency()
        port = resolve_port()
    except ValueError as exc:
        # 坏输入**不静默降级**（本仓不接受"没报错就算过"）：说清原因再拒绝启动。
        sys.stderr.write(f"[拒绝启动] {exc}\n")
        raise SystemExit(REFUSE_EXIT_CODE) from exc

    assert_single_worker(concurrency)
    import uvicorn

    uvicorn.run(app, host=HOST, port=port, log_level="info")


if __name__ == "__main__":
    main()
