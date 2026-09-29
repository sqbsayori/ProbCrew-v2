"""端点信任边界 —— W3 · `docs/02 §4.2.1` 六条（`ADR-0005 §4`）。

**为什么必须有它**：模型端点**由用户提供**（N14），因此服务端会向一个用户可控的 URL 发起请求
—— 这是一个真实的 SSRF 面。`docs/02 §4.2.1` 的六条规则里，能在**契约层**表达的写在
`contracts/chat.schema.json` 的 `definitions.endpoint.base_url` 上；**表达不了的部分**（私网段、
云元数据地址、禁跟重定向、超时与响应体上界）由本模块与 `tools/llm.py` 落地。

★ **`/api/chat` 与 `/api/model/ping` 必须共用本函数**（规则 6 的第一条）——
否则"用 ping 过校验、再直接调 chat"就是一条现成的旁路。**发布即冻结**（`docs/05 §7.2`）。

公开接口::

    validate_endpoint(endpoint, *, allow_loopback=False) -> EndpointConfig
    egress_of(config) -> "loopback" | "remote"

★ **本模块刻意不 import `urllib` / `socket`**：它们是 **R-K** 白名单里的出网库，**只允许出现在
`tools/llm.py`**（`backend/tests/test_tools.py` 有一条 AST 断言在守）。因此：

- `base_url` 的解析用**手写正则**，不用 `urllib.parse` —— 顺带避免"解析器与检查器不一致"这类旁路
  （规则 4「禁跟重定向」的同一个动机）；
- **DNS 解析后的私网段检查不在本模块**，而在 `tools/llm.py` 的连接路径上
  （`_assert_global_resolution`）—— 那里本来就要解析主机名，且它在 R-K 白名单内。

依赖方向（`docs/03 §3.1`）：`tools/` 只允许 import `core/` / `domain/`。
"""
from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from typing import Any, Mapping

#: 与 `contracts/chat.schema.json` 的 `definitions.providerId.enum` 逐项一致
#: （`backend/tests/test_tools.py` 有一条用例对着契约读一遍，防止两处漂移）。
PROVIDER_IDS: tuple[str, ...] = ("deepseek", "qwen", "glm", "custom")

#: 规则 5 的首次取值（`docs/02 §4.2.1`）：连接/读取超时与响应体上界**必须有上界**。
DEFAULT_TIMEOUT_S = 30.0
MAX_RESPONSE_BYTES = 1024 * 1024

#: 与 `docs/02 §6.1` 的 R-A 表一致（`model` 128 · `base_url` 2048 · `provider` 32 且为枚举）。
MODEL_MAX_LENGTH = 128
BASE_URL_MAX_LENGTH = 2048

#: 规则 2/3 的**结构可表达部分**：`https://<host>[:port][/path]` 或本机 loopback 的 `http://`。
#: ★ `[^/?#@\s:]` 里的 `@` 是**故意的**：带 userinfo 的 URL（`https://user:pw@host/`）不匹配
#:   ⇒ 直接被拒 —— 凭据不得出现在 URL 里（**R-M 第 ⑤ 面**）。
_BASE_URL_RE = re.compile(
    r"^(?P<scheme>https?)://"
    r"(?P<host>\[[0-9A-Fa-f:]+\]|[^/?#@\s:]+)"
    r"(?::(?P<port>[0-9]{1,5}))?"
    r"(?P<path>/[^\s]*)?$"
)

_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})

#: 云元数据地址的主机名形式（规则 3 的"等"）—— IP 形式（169.254.169.254）由地址检查覆盖。
_METADATA_HOSTS = frozenset({"metadata.google.internal", "metadata.goog"})

_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
_PATH_SEPARATORS = ("/", "\\")


class EndpointRejection(RuntimeError):
    """端点被信任边界拒绝（规则 6：**校验失败一律 `model_unreachable`**）。

    ★ `detail` 只给**日志与测试**看：规则 6 禁止把"被拒绝的地址"回显给用户，
    因此 :meth:`__str__` 里**只有规则名**，不会带 host / IP（`docs/02 §4.2.1` 规则 6）。
    """

    #: 对外合并后的错误码（`contracts/error.schema.json` 的枚举之一）。
    reason = "model_unreachable"

    def __init__(self, rule: str, detail: str) -> None:
        self.rule = rule
        self.detail = detail
        super().__init__(f"端点被信任边界拒绝（{rule}）")


@dataclass(frozen=True)
class EndpointConfig:
    """**通过校验**的端点（`contracts/chat.schema.json` 的 `definitions.endpoint` 三件套 + 导出字段）。"""

    provider: str
    model: str
    base_url: str
    scheme: str
    host: str
    port: int
    egress: str  # "loopback" | "remote" —— N15 / N16 的判据（`docs/02 §1.5①`）

    def redacted(self) -> dict[str, Any]:
        """给日志/事件用的**只含标识**的视图 —— 端点地址本身不是秘密，但也别进日志内容面。"""
        return {"provider": self.provider, "egress": self.egress}


def egress_of(config: EndpointConfig) -> str:
    """去向（N16 的验收对象）：`loopback` = 本机端点、`remote` = 会外传。"""
    return config.egress


def validate_endpoint(
    endpoint: "EndpointConfig | Mapping[str, Any]",
    *,
    allow_loopback: bool = False,
) -> EndpointConfig:
    """按 `docs/02 §4.2.1` 的六条校验端点；**坏端点一律抛 :class:`EndpointRejection`**。

    `allow_loopback` 由**调用方**给出（规则 2：loopback 例外**仅在单机形态**且**显式勾选**才生效，
    与 N18 的"单机允许 http"**共用同一开关**）。★ 配置读取的唯一权威是 `core/config.py`（域2）
    —— 本模块**不读配置**，只接受参数，这样它也就能被纯函数级用例覆盖。

    已实现 = 规则 1 / 2 / 3（字面量部分）/ 4（结构部分）/ 5（上界取值）/ 6；
    DNS 解析后的私网段检查在 `tools/llm.py` 的连接路径上（见模块头）。
    """
    if isinstance(endpoint, EndpointConfig):
        return endpoint
    if not isinstance(endpoint, Mapping):
        raise EndpointRejection("结构", "endpoint 必须是对象（provider / model / base_url 三件套）")

    unknown = sorted(set(endpoint) - {"provider", "model", "base_url"})
    if unknown:
        raise EndpointRejection("结构", f"endpoint 出现契约之外的字段：{unknown}")

    provider = endpoint.get("provider")
    if provider not in PROVIDER_IDS:
        raise EndpointRejection("规则1-供应商白名单", f"provider 必须是 {list(PROVIDER_IDS)} 之一")

    model = endpoint.get("model")
    if not isinstance(model, str) or not 1 <= len(model) <= MODEL_MAX_LENGTH:
        raise EndpointRejection("规则5-model 上界", f"model 必须是 1–{MODEL_MAX_LENGTH} 字的字符串")
    if _CONTROL_CHARS.search(model) or any(sep in model for sep in _PATH_SEPARATORS):
        raise EndpointRejection("规则3-model 字符集", "model 不得含控制字符或路径分隔符")

    base_url = endpoint.get("base_url")
    if not isinstance(base_url, str) or not base_url:
        raise EndpointRejection("结构", "base_url 必须是非空字符串")
    if len(base_url) > BASE_URL_MAX_LENGTH:
        raise EndpointRejection("规则5-base_url 上界", f"base_url 不得超过 {BASE_URL_MAX_LENGTH} 字")

    matched = _BASE_URL_RE.match(base_url)
    if matched is None:
        raise EndpointRejection(
            "规则2/3-形状",
            "base_url 必须是 https://… 或本机形态的 http://127.0.0.1 | http://localhost[:port]/…"
            "（不得带 userinfo）",
        )

    scheme = matched.group("scheme")
    host = matched.group("host").strip("[]").lower()
    raw_port = matched.group("port")

    is_loopback = _is_loopback_host(host)
    if is_loopback and not allow_loopback:
        raise EndpointRejection(
            "规则2-loopback 例外",
            "本机端点例外仅单机形态、由部署者显式勾选后生效（与 N18 的『单机允许 http』共用同一开关）",
        )
    if scheme == "http" and not is_loopback:
        raise EndpointRejection("规则3-强制 https", "非本机端点必须用 https")

    if host in _METADATA_HOSTS:
        raise EndpointRejection("规则3-云元数据地址", "云元数据地址一律拒绝")
    if not is_loopback:
        _reject_non_global_literal(host)

    if raw_port is None:
        port = 443 if scheme == "https" else 80
    else:
        port = int(raw_port)
        if not 1 <= port <= 65535:
            raise EndpointRejection("规则5-端口", "端口必须在 1–65535")

    return EndpointConfig(
        provider=provider,
        model=model,
        base_url=base_url,
        scheme=scheme,
        host=host,
        port=port,
        egress="loopback" if is_loopback else "remote",
    )


def _is_loopback_host(host: str) -> bool:
    if host in _LOOPBACK_HOSTS:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _reject_non_global_literal(host: str) -> None:
    """IP 字面量的私网 / 保留 / 元数据检查（规则 3）。

    ★ **主机名在这里不查**：解析它需要 `socket`，而 R-K 只允许出网库出现在 `tools/llm.py`
    ⇒ 解析后的检查放在连接路径上（`tools/llm.py` 的 `_assert_global_resolution`）。
    """
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return  # 主机名：交给连接路径的解析后检查
    if not address.is_global:
        raise EndpointRejection(
            "规则3-内网与保留地址",
            "RFC1918 / 链路本地 / 云元数据 / 保留地址一律拒绝（只挡 loopback 不够）",
        )
