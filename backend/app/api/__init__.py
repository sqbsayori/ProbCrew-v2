"""`app.api` —— HTTP 薄接线层（W1 · 最小身份闭环）。

★ **薄到什么程度**（`docs/03 §3.2` 第 1 条）：单函数 ≤ 40 行 · **文件里不出现一句 SQL**。
请求体 → 调 `identity/` / `learning/` 的存储层 → 返回契约形状。业务判定一律在下面那层，
本层只负责"把 HTTP 的输入翻译成调用、把结果翻译成响应"。

★ **越权的结构性落点（F15）**：除 ``login`` 外，**没有任何端点接受 ``user_id`` / ``username``
  参数** —— 用户身份一律来自 ``Authorization: Bearer``。因此"看别人的数据"不是一个
  "记得校验"的纪律问题，而是**没有可越权的入参**。语义面（`docs/02 §6.3` 收口）：
  读他人资源 → **404**（对外看起来"不存在"，日志里记 403）；``403 forbidden`` 只保留"角色不足"。

★ **本模块由 `main.py` 挂载**（`app.include_router(app.api.router)`）—— 那一行必须在那次
  ``mount("/")`` **之前**，否则静态托管的 ``Mount("/")`` 会先匹配掉一切（Starlette 按注册顺序匹配）。
"""
from __future__ import annotations

from fastapi import APIRouter

from app.api import admin as admin_routes
from app.api import auth as auth_routes
from app.api import chat as chat_routes
from app.api import me as me_routes
from app.api import practice as practice_routes
from app.api import runs as run_routes

__all__ = ["router"]

#: 全站 API 路由的聚合点：新增路由模块时**只在这里**加一行（`main.py` 不随端点增长而改）。
router = APIRouter()
router.include_router(auth_routes.router)
router.include_router(me_routes.router)
router.include_router(practice_routes.router)
router.include_router(admin_routes.router)
router.include_router(chat_routes.router)
router.include_router(run_routes.router)
