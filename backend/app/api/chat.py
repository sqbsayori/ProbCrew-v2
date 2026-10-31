"""答疑与模型端点 —— W3 的 HTTP 接线（`docs/06 §14.3` 卡 W3）。

| 端点 | 认证 | 成功 | 失败 |
|---|---|---|---|
| `POST /api/chat` | 是 | `text/event-stream`（14 类事件帧） | 503 `model_key_missing` · 503 `model_unreachable` |
| `POST /api/model/ping` | 是 | 200 `{ok, latency_ms, message?}` | 503（缺凭据 / 端点被拒） |
| `GET /api/model/providers` | 是 | 200 `{providers[]}` | 401 |

★ **凭据只有一个入口**：请求头 `X-Model-Key`；请求体里没有承载凭据的字段
  （R-M 第 ⑦ 面 · `contracts/chat.schema.json`）。
★ **端点校验只有一处**：本文件的 chat 与 ping 都走
  `app.tools.endpoint.validate_endpoint`（规则 6 第一条：不允许一条 ping 能过、
  chat 却另走一条更松的路）。
★ **先落 `qa_log` 再答**：`answer` 先 `NULL`，终帧后补；中途失败/断流也留下流水
  （契约 `qa-log.schema.json` 的口径）。
"""
from __future__ import annotations

import time
import uuid
from typing import Any

from fastapi import APIRouter, Depends, Header
from fastapi.responses import StreamingResponse

from app.api.deps import current_context, get_conn, password_gate
from app.api.schemas import ChatEndpoint, ChatRequest
from app.core import db
from app.core.config import deployment_allows_loopback
from app.core.errors import AppError
from app.graph.events import encode_frame
from app.graph.runner import stream_run
from app.identity.auth import AuthContext
from app.learning import store
from app.tools import llm
from app.tools.endpoint import EndpointRejection, validate_endpoint

__all__ = ["PROVIDERS", "router"]

router = APIRouter(tags=["答疑"])

#: 预置供应商（契约 `providers[].provider` 的四值）。`custom` 的默认地址为 null。
PROVIDERS: "list[dict[str, Any]]" = [
    {
        "provider": "deepseek",
        "label": "DeepSeek",
        "default_base_url": "https://api.deepseek.com/v1",
        "requires_key": True,
    },
    {
        "provider": "qwen",
        "label": "通义千问（DashScope 兼容模式）",
        "default_base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "requires_key": True,
    },
    {
        "provider": "glm",
        "label": "智谱 GLM",
        "default_base_url": "https://open.bigmodel.cn/api/paas/v4",
        "requires_key": True,
    },
    {
        "provider": "custom",
        "label": "自定义 OpenAI 兼容端点",
        "default_base_url": None,
        "requires_key": False,
    },
]

_PING_MESSAGES = {
    "model_key_missing": "没有提供模型凭据。",
    "model_key_invalid": "模型凭据被端点拒绝了。",
    "model_quota_exceeded": "模型额度不足。",
    "model_unreachable": "模型端点不可达或响应无效。",
}


@router.get("/api/model/providers")
def list_providers(
    context: AuthContext = Depends(current_context),
) -> "dict[str, Any]":
    """设置页的下拉框数据（不得在前端硬编码这份清单）。"""
    return {"providers": [dict(item) for item in PROVIDERS]}


@router.post("/api/model/ping")
def ping_model(
    payload: ChatEndpoint,
    context: AuthContext = Depends(password_gate("model")),
    model_key: "str | None" = Header(default=None, alias="X-Model-Key"),
) -> "dict[str, Any]":
    """凭据自检：与 `/api/chat` 共用同一个端点校验函数与同一条凭据规则。"""
    config = _checked_endpoint(payload)
    credential = _checked_credential(model_key, config.egress)
    started = time.perf_counter()
    result = llm.call_model(
        ({"role": "user", "content": "ping"},),
        endpoint=config,
        credential=credential,
    )
    latency = round((time.perf_counter() - started) * 1000, 1)
    if result.ok:
        return {"ok": True, "latency_ms": latency}
    return {
        "ok": False,
        "latency_ms": latency,
        "message": _PING_MESSAGES.get(str(result.reason), _PING_MESSAGES["model_unreachable"]),
    }


@router.post("/api/chat")
def chat(
    payload: ChatRequest,
    context: AuthContext = Depends(password_gate("chat")),
    model_key: "str | None" = Header(default=None, alias="X-Model-Key"),
    conn=Depends(get_conn),
) -> StreamingResponse:
    """跑一次答疑并逐帧返回（`POST + fetch 流`，不是 `EventSource`）。"""
    config = _checked_endpoint(payload.endpoint)
    credential = _checked_credential(model_key, config.egress)

    qa_id = f"qa_{uuid.uuid4().hex[:12]}"
    session_id = f"ses_{uuid.uuid4().hex[:12]}"
    store.record_qa_start(
        conn,
        qa_id=qa_id,
        user_id=context.user_id,
        query=payload.query,
        provider=config.provider,
        model=config.model,
        egress=config.egress,
        created_at=time.time(),
    )
    body = payload.model_dump()

    async def frames():
        answer: "str | None" = None
        try:
            async for frame in stream_run(
                run_id=qa_id,
                session_id=session_id,
                query=payload.query,
                context=body.get("context"),
                # ★ 传**已校验**的 EndpointConfig：它带着 `egress` / loopback 许可；
                #   再降回原始字典会让 `llm.call_model` 二次校验时丢掉单机例外。
                endpoint=config,
                credential=credential,
                owner_id=context.user_id,
            ):
                if frame.type == "done":
                    value = frame.payload.get("final_answer")
                    answer = value if isinstance(value, str) else None
                yield encode_frame(frame)
        finally:
            # 响应结束/断流时依赖连接可能已进入关闭流程 ⇒ 用独立连接补最后一笔。
            done = db.initialize()
            try:
                store.finish_qa(done, qa_id, answer)
            finally:
                done.close()

    return StreamingResponse(
        frames(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def _checked_endpoint(payload: ChatEndpoint):
    try:
        return validate_endpoint(
            payload.model_dump(), allow_loopback=deployment_allows_loopback()
        )
    except EndpointRejection as exc:
        # 规则 6：校验失败一律 `model_unreachable`，且不回显被拒地址。
        raise AppError("model_unreachable", "模型端点不可用：请检查地址与部署形态。") from exc


def _checked_credential(model_key: "str | None", egress: str) -> str:
    if model_key is not None and len(model_key) > 4096:
        raise AppError("invalid_argument", "模型凭据过长", {"field": "X-Model-Key"})
    if model_key is None:
        if egress == "remote":
            raise AppError("model_key_missing", "还没有配置模型凭据：请先在设置页填写 Key。")
        return ""
    if not model_key and egress == "remote":
        raise AppError("model_key_missing", "还没有配置模型凭据：请先在设置页填写 Key。")
    return model_key
