"""`app.identity` —— 账号、令牌、鉴权内核与审计（W1 · 最小身份闭环）。

写作用域：`docs/05 §3` 把 `backend/app/identity/**` 判给**后端与数据**（域2）。

★ **依赖方向（`docs/03 §3.1` + `scripts/verify.sh` 的 `import_direction()` 硬拦）**：
本包**只允许** import `app.core` · `app.identity`；**明确禁止** `fastapi` · `langgraph` ·
`app.graph` · `app.domain` · `app.api`。

⇒ 因此这里**没有一行** FastAPI 代码：不出现 `Depends` / `Header` / `HTTPException`。
请求头解析与依赖注入留在 `app/api/deps.py` —— 那让 `api/` 天然薄
（`docs/03 §3.2` 第 1 条），也让本包**可以在没有 HTTP 的情况下被直接测试**。
本包对外抛的是 `app.core.errors.AppError`（code 与 HTTP 状态由 `core/errors.py` 派生）。
"""
from __future__ import annotations

from app.identity import audit, auth, tokens, users

__all__ = ["audit", "auth", "tokens", "users"]
