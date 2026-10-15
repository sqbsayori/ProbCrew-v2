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

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.config import load_settings
from app.core.errors import AppError, HTTP_STATUS_BY_CODE, error_envelope
from app.core.logging import setup_logging

__all__ = [
    "CONCURRENCY_ENV",
    "DEFAULT_CONCURRENCY",
    "DEPLOYMENT_ENV",
    "DEPLOYMENT_HOSTED",
    "DEPLOYMENT_SINGLE",
    "FRONTEND_DIR",
    "HOST",
    "LOOPBACK_HOSTS",
    "PORT",
    "app",
    "app_error_handler",
    "assert_deployment_consistent",
    "assert_single_worker",
    "describe_deployment",
    "http_exception_handler",
    "loopback_endpoint_allowed",
    "main",
    "resolve_concurrency",
    "resolve_deployment",
    "validation_error_handler",
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


# ---- 部署形态（N16 的「部署者开关」 · ★ 2026-09-27 补）------------------------

#: 部署形态信号变量。★ **首次取值** —— 此前它只有散文（`docs/02 §1.5①` 的 P1 例外
#: 「必须由部署者**显式勾选**才生效」，而「勾在哪一行代码上」**没有任何落点**）。
DEPLOYMENT_ENV = "PROBCREW_DEPLOYMENT"
#: 两档形态（`docs/01` N16）：远程托管（正式服务 · **loopback 端点一律拒绝**）·
#: 单机（答辩 / 教师自用 · 允许本机端点 P1）。★ 名字用 `hosted` / `single` 而不是
#: `remote` / `local` —— 避免与 `egress` 的枚举（`loopback` / `remote`）混淆：**形态与去向是两个维度**
#: （`docs/02 §1.5①` 的表就是四象限，单机形态下也可能 `egress=remote`）。
DEPLOYMENT_HOSTED = "hosted"
DEPLOYMENT_SINGLE = "single"
#: 回环监听地址 —— 单机形态的正当性前提是"**本机访问**"（`docs/02 §1.5①`）。
LOOPBACK_HOSTS = ("127.0.0.1", "::1", "localhost")


def resolve_deployment(env: Mapping[str, str] | None = None) -> str:
    """部署形态：未设置或空串 ⇒ **`hosted`（远程托管）**；坏值**拒绝启动**（不静默降级）。

    ★ **为什么缺省取最严的那一档**：`docs/02 §1.5①` 写着 loopback 例外「**默认关闭**」——
      缺省 `hosted` 就是"默认关闭"的机器可读形式；单机形态必须由部署者**显式**声明。
    ★ 坏值处理与 {@link resolve_concurrency} / {@link resolve_port} 同款：**说清原因再拒绝**。
    """
    raw = (os.environ if env is None else env).get(DEPLOYMENT_ENV, "").strip().lower()
    if raw == "":
        return DEPLOYMENT_HOSTED
    if raw not in (DEPLOYMENT_HOSTED, DEPLOYMENT_SINGLE):
        sys.stderr.write(
            f"[拒绝启动] {DEPLOYMENT_ENV}={raw!r} 不是合法形态 —— 只接受 "
            f"{DEPLOYMENT_SINGLE!r}（单机：答辩 / 教师自用）或 {DEPLOYMENT_HOSTED!r}（远程托管，缺省）。\n"
            "  原因：形态决定 loopback 端点与明文 http 是否允许（docs/02 §1.5① · §4.2.1 规则 2），"
            "猜一个等于把安全开关交给默认值。\n"
        )
        raise SystemExit(REFUSE_EXIT_CODE)
    return raw


def loopback_endpoint_allowed(deployment: str) -> bool:
    """P1（本机端点）在**服务端**这一半是否放开 —— **只在单机形态**（`docs/02 §1.5①` · §4.2.1 规则 2）。

    ★ 规则 2 的原话是「**仅在单机形态**，且必须由部署者**显式勾选**才生效」⇒ 它是**两个条件的合取**：
      本函数判服务端形态，客户端还要用户在设置页勾「本机端点」。**两者都成立**才真的放行。
    ★ **消费点**（本仓唯一来源就是这里）：端点校验函数（`/api/model/ping` 与 `/api/chat`
      **必须共用同一个** —— `docs/02 §4.2.1`）+ N18 的"单机允许 http"那一半。
      ⇒ ★ **跨域事项（`docs/05 §3` 规则 1）**：`backend/app/api/**` 与 `core/**` 属**后端与数据**；
      若该域希望从 `core/config.py` 的 `Settings` 读这个开关，请按规则 1 记账后再搬 —— **唯一来源不能有两处**。
    """
    return deployment == DEPLOYMENT_SINGLE


def assert_deployment_consistent(deployment: str, host: str = HOST) -> None:
    """启动校验：**单机形态不得对外监听** —— 否则「允许 loopback 端点」的正当性不成立。

    ★ 为什么这条是真检查而不是形式：`docs/02 §4.2.1` 规则 2 的理由原文是「远程托管下学生填的
      loopback 是**服务器**的 loopback：他既无正当性使用它，**它又是一条通往服务器内部服务的路**」
      ⇒ 一个**对外可达**的服务若同时允许 loopback 端点，就正好把那句话里的场景构造出来了。
    ★ 今天 `HOST` 是常量（回环）—— `docs/03 §8.1` 的拓扑要求**反代与服务同机**，因此本函数
      **是守卫**：它挡住的是「把 HOST 改成 `0.0.0.0` 又声明成 `single`」这个组合。
    """
    if deployment == DEPLOYMENT_SINGLE and host not in LOOPBACK_HOSTS:
        sys.stderr.write(
            f"[拒绝启动] {DEPLOYMENT_ENV}={DEPLOYMENT_SINGLE} 却监听 {host!r} —— 二者矛盾。\n"
            "  原因：单机形态的前提是「**本机访问**」（docs/02 §1.5①）；对外监听 + 允许 loopback 端点\n"
            "        正是 docs/02 §4.2.1 规则 2 要挡的那个场景（学生填 127.0.0.1 就能打服务器内部）。\n"
            "  处理：远程托管请用缺省（PROBCREW_DEPLOYMENT 不设或设为 hosted）并由反代对外（docs/03 §8.5）。\n"
        )
        raise SystemExit(REFUSE_EXIT_CODE)


def describe_deployment(deployment: str) -> str:
    """启动时打印一行 —— 让「当前是哪一档形态、端点例外开不开」在**日志与终端里可见**。"""
    if deployment == DEPLOYMENT_SINGLE:
        return (
            "部署形态：单机（答辩 / 教师自用）—— ★ 允许本机端点（P1）与明文 http；"
            "**不要把它暴露到局域网 / 公网**（docs/02 §1.5①）"
        )
    return (
        "部署形态：远程托管 —— ★ loopback 端点与明文 http **一律拒绝**"
        "（docs/02 §4.2.1 规则 2 · N18）"
    )


# ---- 应用对象 ---------------------------------------------------------------

app = FastAPI(
    title="ProbCrew · 概率论伴学助手",
    description="W0a 骨架：只有同源静态托管与单 worker 启动检测，本轮没有任何业务端点。",
)


# ---- 统一错误体（contracts/error.schema.json）--------------------------------
# ★ 全仓只有这一处把「异常 → ErrorEnvelope」接上（docs/02 §6.3 · docs/04 承诺 12 的传输面）：
#   FastAPI 缺省返回 `{"detail": ...}`，不符合契约，前端 `core/api.js` 只能兜底成 `internal_error`。
#   这里收三类：业务 `AppError`（W1+ 各端点直接抛它）· 路由 404/405 等 `HTTPException` ·
#   请求体校验 `RequestValidationError`。业务端点不再各自手写响应体。
_STATUS_TO_CODE: dict[int, str] = {}
for _code, _status in HTTP_STATUS_BY_CODE.items():
    _STATUS_TO_CODE.setdefault(_status, _code)
# ★ 405 在契约 16 个 code 里没有对应项（没有「方法不允许」）—— 它是**客户端错**，
#   不是内部错误，故显式归 invalid_argument，而不是落到兜底的 internal_error。
_STATUS_TO_CODE[405] = "invalid_argument"

_STATUS_MESSAGES = {
    404: "资源不存在",
    405: "请求方法不允许",
    500: "服务内部错误",
}


async def app_error_handler(_: Request, exc: AppError) -> JSONResponse:
    """业务错误 → 契约信封（状态码由 code 派生，`core/errors.py` 的 `http_status`）。"""
    return JSONResponse(status_code=exc.http_status, content=exc.to_envelope())


async def http_exception_handler(_: Request, exc: StarletteHTTPException) -> JSONResponse:
    """路由 404/405 等 → 契约信封（状态码 ↔ code 的反查只取 `core/errors.py` 的映射）。"""
    status = exc.status_code
    code = _STATUS_TO_CODE.get(status, "internal_error")
    message = _STATUS_MESSAGES.get(status, f"请求失败（HTTP {status}）")
    return JSONResponse(status_code=status, content=error_envelope(code, message))


async def validation_error_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    """请求体校验失败 → `invalid_argument`（不回声原始输入，凭据不得进错误信息，R-M③）。"""
    return JSONResponse(status_code=400, content=error_envelope("invalid_argument", "请求参数不合法"))


app.add_exception_handler(AppError, app_error_handler)
app.add_exception_handler(StarletteHTTPException, http_exception_handler)
app.add_exception_handler(RequestValidationError, validation_error_handler)


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
    # ★ 部署形态：坏值在函数内拒绝；一致性检查见 `docs/02 §4.2.1` 规则 2 的落点说明。
    deployment = resolve_deployment()
    assert_deployment_consistent(deployment)
    print(describe_deployment(deployment), flush=True)
    # ★ 审计日志（R-O）：跑业务前先挂上 handler —— 否则 `core/logging.log_action` 会当场拒绝写入
    #   （不接受「静默丢日志」，docs/02 §1.4 规则 1 · ADR-0005）。
    setup_logging(load_settings().log_dir)
    import uvicorn

    uvicorn.run(app, host=HOST, port=port, log_level="info")


if __name__ == "__main__":
    main()
