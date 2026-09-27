"""模型调用工具 —— W3 · 卡 W3（`docs/06 §14.3` 的 W3 行：答疑链的**唯一出网口**）。

★ **R-K**：`httpx` / `requests` / `urllib` / `socket` 只允许出现在本文件
（`backend/tests/test_tools.py` 有一条 AST 断言在守）。预抓脚本 `scripts/fetch_corpus.py`
是**唯一被允许的离线出网口**，且不在服务运行期。

**两层拆分**（这一步是为了让 **R-L** 的"假 provider 抓载荷"真的可做）::

    build_request(messages, *, endpoint, credential) -> ModelRequest   # 纯函数，不触网
    send(request, *, timeout_s, max_bytes) -> ToolResult               # 唯一触网点
    ENTRY = call_model(messages, *, endpoint, credential, timeout_s)   # 工具入口（ToolBox 按名取）

**三条纪律的落点**：

1. **R-L 载荷白名单**在 :func:`build_request` 里**构造时强制**：出网载荷只允许
   `messages`（系统提示 / 提问原文 / 上下文片段）与模型名；**反向禁止**的字段名
   （`user_id` · `username` · 令牌 · `attempt` · `mastery` · `qa_log` · 审计 · 凭据）一出现即拒。
2. **R-M 七面**：凭据只进 :class:`ModelRequest` 的 `headers`（`Authorization: Bearer …`），
   **不进 body / query**；`ModelRequest.__repr__` **不打印 headers** —— 否则"顺手 print 一下"就泄露。
3. **四类 `code`** 取自 `app.core.errors` 的枚举（不自造字符串），映射见 :func:`_code_for_status`；
   **校验失败一律 `model_unreachable`**（`docs/02 §4.2.1` 规则 6）。

**本轮不做**（W0c 卡3 的"不做什么"在 W3 里只放开到"接真实调用"）：
不做上游流式（`stream: false`）· 不做重试（重试由域2 的 `api/` 决定）· 不做多供应商适配层
（N14 已把"多供应商"收成用户端点配置）。

依赖方向（`docs/03 §3.1`）：`tools/` 只允许 import `core/` / `domain/`。
"""
from __future__ import annotations

import ipaddress
import json
import socket
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from app.tools import (
    INVALID_REQUEST,
    INVALID_RESPONSE,
    MODEL_KEY_INVALID,
    MODEL_KEY_MISSING,
    MODEL_QUOTA_EXCEEDED,
    MODEL_UNREACHABLE,
    PAYLOAD_REJECTED,
    ToolResult,
)
from app.tools.endpoint import (
    DEFAULT_TIMEOUT_S,
    MAX_RESPONSE_BYTES,
    EndpointConfig,
    EndpointRejection,
    validate_endpoint,
)

#: 上游 JSON 里"回答正文"的位置（OpenAI 兼容：`choices[0].message.content`）。
_CHOICES_PATH = ("choices", 0, "message", "content")

#: R-L 反向禁止：字段名（小写子串）命中即拒。★ 这些都是**标识与凭据**，不是内容。
_FORBIDDEN_PAYLOAD_KEYS: tuple[str, ...] = (
    "user_id",
    "username",
    "token",
    "password",
    "secret",
    "credential",
    "authorization",
    "api_key",
    "apikey",
    "attempt",
    "mastery",
    "qa_log",
    "qalog",
    "audit",
)

_ALLOWED_ROLES = frozenset({"system", "user", "assistant"})
_MESSAGE_CONTENT_MAX = 8000  # 与 `qa-log.schema.json` 的 answer 上界同量级


class PayloadRejected(RuntimeError):
    """出网载荷命中 R-L 的反向禁止字段（构造时即拒，不靠运行期抽查）。"""


class EgressRejected(RuntimeError):
    """连接路径上的拒绝（DNS 解析到内网 / 响应体超界 / 传输异常）—— 对外合并为 `model_unreachable`。"""


@dataclass(frozen=True)
class ModelRequest:
    """一次上游请求：URL + 头 + body。★ **唯一承载凭据的地方是 `headers`**（R-M 第 ⑦ 面）。"""

    url: str
    headers: Mapping[str, str] = field(default_factory=dict)
    body: bytes = b""
    method: str = "POST"
    loopback_ok: bool = False

    def __repr__(self) -> str:
        # ★ 刻意不打印 headers：它含 Authorization（R-M 第 ③/⑥ 面）。
        return f"ModelRequest(method={self.method!r}, url={self.url!r}, bytes={len(self.body)})"

    __str__ = __repr__


@dataclass(frozen=True)
class TransportResponse:
    """传输层结果（**测试里的假 provider 也返回这个形状**）。"""

    status: int
    body: bytes


#: ★ 唯一的触网点实现 —— **测试通过替换它注入假 provider**（产品路径不含任何 mock，N2）。
_TRANSPORT: "Any" = None  # 见模块末尾：默认指向 _urllib_transport


# --------------------------------------------------------------------------- #
# 纯函数层：构造请求（不触网 ⇒ 可被抓载荷断言）
# --------------------------------------------------------------------------- #
def build_request(
    messages: "Sequence[Mapping[str, Any]]",
    *,
    endpoint: "EndpointConfig | Mapping[str, Any]",
    credential: str,
) -> ModelRequest:
    """拼一次上游请求（**纯函数**：只读入参、不触网、不改全局）。

    坏输入一律抛异常，由 :func:`call_model` 转成 `ToolResult(ok=False, reason=…)`：
    - 端点不合法 ⇒ :class:`~app.tools.endpoint.EndpointRejection`；
    - 载荷命中 R-L 反向禁止 ⇒ :class:`PayloadRejected`；
    - `messages` 形状不对 ⇒ :class:`ValueError`。
    """
    config = endpoint if isinstance(endpoint, EndpointConfig) else validate_endpoint(endpoint)
    checked = _check_messages(messages)
    payload: dict[str, Any] = {"model": config.model, "messages": checked, "stream": False}
    _assert_payload_allowed(payload)

    headers: dict[str, str] = {"Content-Type": "application/json"}
    if credential:
        # ★ 凭据只走请求头；空串（P1 本机端点）**不发这个头**。
        headers["Authorization"] = f"Bearer {credential}"

    url = config.base_url.rstrip("/") + "/chat/completions"
    return ModelRequest(
        url=url,
        headers=headers,
        body=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        loopback_ok=config.egress == "loopback",
    )


def _check_messages(messages: "Sequence[Mapping[str, Any]]") -> list[dict[str, str]]:
    if isinstance(messages, (str, bytes)) or not isinstance(messages, Sequence) or not messages:
        raise ValueError("messages 必须是非空的消息序列")
    checked: list[dict[str, str]] = []
    for item in messages:
        if not isinstance(item, Mapping):
            raise ValueError("messages 的每一项都必须是对象")
        role = item.get("role")
        content = item.get("content")
        if role not in _ALLOWED_ROLES:
            raise ValueError(f"role 必须是 {sorted(_ALLOWED_ROLES)} 之一")
        if not isinstance(content, str) or not 1 <= len(content) <= _MESSAGE_CONTENT_MAX:
            raise ValueError(f"content 必须是 1–{_MESSAGE_CONTENT_MAX} 字的字符串")
        checked.append({"role": str(role), "content": content})
    return checked


def _assert_payload_allowed(node: Any, *, path: str = "$") -> None:
    """R-L 的机器可执行形式：**递归**扫字段名，命中反向禁止清单即拒。"""
    if isinstance(node, Mapping):
        for key, value in node.items():
            lowered = str(key).lower()
            if any(token in lowered for token in _FORBIDDEN_PAYLOAD_KEYS):
                raise PayloadRejected(f"载荷字段 {path}.{key} 命中 R-L 反向禁止清单")
            _assert_payload_allowed(value, path=f"{path}.{key}")
    elif isinstance(node, (list, tuple)):
        for index, value in enumerate(node):
            _assert_payload_allowed(value, path=f"{path}[{index}]")


# --------------------------------------------------------------------------- #
# 传输层：唯一触网点
# --------------------------------------------------------------------------- #
def send(
    request: ModelRequest,
    *,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    max_bytes: int = MAX_RESPONSE_BYTES,
) -> ToolResult:
    """发请求并归一化结果 —— **失败也返回 `ToolResult`**（R-Q ③：失败要进事件流）。"""
    transport = _TRANSPORT or _urllib_transport
    try:
        response = transport(request, timeout_s=timeout_s, max_bytes=max_bytes)
    except EgressRejected:
        return ToolResult(ok=False, reason=MODEL_UNREACHABLE)
    except (OSError, TimeoutError, urllib.error.URLError, urllib.error.HTTPError):
        return ToolResult(ok=False, reason=MODEL_UNREACHABLE)

    code = _code_for_status(int(response.status))
    if code is not None:
        return ToolResult(ok=False, reason=code)

    try:
        data = json.loads(response.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return ToolResult(ok=False, reason=INVALID_RESPONSE)

    text = _dig(data, _CHOICES_PATH)
    if not isinstance(text, str) or not text.strip():
        return ToolResult(ok=False, reason=INVALID_RESPONSE)
    model = data.get("model") if isinstance(data, dict) else None
    return ToolResult(ok=True, value={"text": text, "model": model if isinstance(model, str) else None})


def _code_for_status(status: int) -> "str | None":
    """HTTP 状态 → 四类 `code`（`docs/02 §6.3`）。★ 401/403 绝不复用 401 `unauthenticated`。"""
    if 200 <= status < 300:
        return None
    if status in (401, 403):
        return MODEL_KEY_INVALID
    if status in (402, 429):
        return MODEL_QUOTA_EXCEEDED
    return MODEL_UNREACHABLE


def _dig(node: Any, path: "Sequence[Any]") -> Any:
    for step in path:
        if isinstance(step, int):
            if not isinstance(node, list) or len(node) <= step:
                return None
            node = node[step]
        else:
            if not isinstance(node, dict):
                return None
            node = node.get(step)
    return node


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """规则 4：**禁跟重定向** —— 返回 `None` 让 urllib 抛 `HTTPError`（302 不会被跟随）。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102 - 继承协议
        return None


def _urllib_transport(
    request: ModelRequest,
    *,
    timeout_s: float,
    max_bytes: int,
) -> TransportResponse:
    """默认传输实现（**全仓唯一真正的 socket 调用点**）。

    ★ 与 `tools/endpoint.py` 的分工：那里查**字面量**，这里查**解析结果**（`_assert_resolution_allowed`）
    —— 因为解析主机名需要 `socket`，而 R-K 只允许出网库出现在本文件。
    """
    parts = urllib.parse.urlsplit(request.url)
    host = parts.hostname or ""
    port = parts.port or (443 if parts.scheme == "https" else 80)
    _assert_resolution_allowed(host, port, loopback_ok=request.loopback_ok)

    http_request = urllib.request.Request(
        request.url, data=request.body, headers=dict(request.headers), method=request.method
    )
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(http_request, timeout=timeout_s) as response:
            body = response.read(max_bytes + 1)
            status = int(getattr(response, "status", 200))
    except urllib.error.HTTPError as exc:
        body = exc.read(max_bytes + 1)
        status = int(exc.code)
    if len(body) > max_bytes:
        raise EgressRejected(f"响应体超过上界 {max_bytes} 字节")
    return TransportResponse(status=status, body=body)


def _assert_resolution_allowed(host: str, port: int, *, loopback_ok: bool) -> None:
    """规则 3 的**解析后**一半：解析到内网 / 保留地址即拒（loopback 例外见规则 2）。

    ★ 已知边界（诚实登记）：这只能挡住"解析结果"这一层，挡不住 TOCTOU 式的 DNS rebinding
    —— 那需要把连接固定到已校验的 IP，属 W5 的运维/安全批次。
    """
    if not host:
        raise EgressRejected("URL 没有主机名")
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except OSError as exc:
        raise EgressRejected("主机名无法解析") from exc
    for info in infos:
        raw = info[4][0]
        try:
            address = ipaddress.ip_address(raw)
        except ValueError:
            raise EgressRejected("解析结果不是 IP 地址") from None
        if loopback_ok:
            if not address.is_loopback:
                raise EgressRejected("本机端点解析到了非 loopback 地址")
        elif not address.is_global:
            raise EgressRejected("解析到内网 / 保留地址（RFC1918 · 链路本地 · 云元数据）")


# --------------------------------------------------------------------------- #
# 工具入口
# --------------------------------------------------------------------------- #
def call_model(
    messages: "Sequence[Mapping[str, Any]]",
    *,
    endpoint: "EndpointConfig | Mapping[str, Any] | None" = None,
    credential: "str | None" = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> ToolResult:
    """工具入口（`app/tools/__init__.py` 的 `ENTRY` 约定）。

    失败一律**如实返回** `ToolResult(ok=False, reason=…)`，不静默、不假装成功（R-Q ②③）：

    - 缺凭据（`credential is None`，或**远程**端点却只给了空串）⇒ `model_key_missing`；
    - 端点不合法 / 被信任边界拒绝 ⇒ `model_unreachable`；
    - 载荷命中 R-L 反向禁止 ⇒ `payload_rejected`；`messages` 形状不对 ⇒ `invalid_request`；
    - 上游 401/403 ⇒ `model_key_invalid` · 402/429 ⇒ `model_quota_exceeded` · 其余 ⇒ `model_unreachable`。

    ★ **P1（本机端点）的空串是在 `endpoint.egress == "loopback"` 时才合法**的：
    因此调用方要先 `validate_endpoint(..., allow_loopback=True)` 得到 `EndpointConfig`
    （`allow_loopback` 的来源是部署配置，属域2 的 `core/config.py`），再传进来。
    """
    if credential is None or not isinstance(credential, str):
        return ToolResult(ok=False, reason=MODEL_KEY_MISSING)

    try:
        config = endpoint if isinstance(endpoint, EndpointConfig) else validate_endpoint(endpoint)
    except EndpointRejection as exc:
        return ToolResult(ok=False, reason=exc.reason)

    if not credential and config.egress != "loopback":
        return ToolResult(ok=False, reason=MODEL_KEY_MISSING)

    try:
        request = build_request(messages, endpoint=config, credential=credential)
    except EndpointRejection as exc:
        return ToolResult(ok=False, reason=exc.reason)
    except PayloadRejected:
        return ToolResult(ok=False, reason=PAYLOAD_REJECTED)
    except ValueError:
        return ToolResult(ok=False, reason=INVALID_REQUEST)

    return send(request, timeout_s=timeout_s)


_TRANSPORT = _urllib_transport

#: 工具入口 —— `graph.state.ToolBox` 按这个名字取（`app/tools/__init__.py` 的约定）。
ENTRY = call_model
