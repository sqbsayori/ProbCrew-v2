"""``tools/`` 的用例 —— 与被测模块同名（``docs/05 §3``）。

判据对照：

- **R-K**：全仓唯一的运行期出网口是 ``tools/llm.py``（本文件末的 AST 断言）；
- **R-L**：出网载荷白名单 —— 断言方式是**看构造出来的 body**（构造即白名单）；
- **R-Q ②③**：``ok`` 来自真实执行结果（禁止硬编码 ``true``）；工具失败必须进事件流；
- **``docs/02 §4.2.1`` 六条**：端点信任边界的**逐条负样本**（规则 1–6）；
- **``docs/02 §6.3``**：上游状态 → 四类 ``code`` 的映射（401/403 **绝不复用** 401 ``unauthenticated``）。
- **S2（``docs/06 §4`` 第 11 项）**：两个验证手段 —— ``check`` 形状**过契约**、
  解析失败 ⇒ 未执行（**不是**不通过）、不可信表达式**不入 ``eval``**、``sympy`` 不得进 ``graph/``。

★ 假 provider 的形态是**替换 ``llm._TRANSPORT``**（R-L 的"假 provider 抓载荷"要有落点）；
产品路径里**没有任何 mock**（N2 · ``docs/02 §4.2.1``）。
"""
from __future__ import annotations

import ast
import json
import time
from pathlib import Path
from typing import Any

import jsonschema
import pytest

from app.core.errors import ERROR_CODES
from app.graph.nodes import solve
from app.graph.state import NodeContext, RecordingEventSink
from app.tools import (
    INVALID_REQUEST,
    INVALID_RESPONSE,
    MODEL_CODES,
    MODEL_KEY_INVALID,
    MODEL_KEY_MISSING,
    MODEL_QUOTA_EXCEEDED,
    MODEL_UNREACHABLE,
    NOT_IMPLEMENTED,
    PARSE_ERROR,
    TIMEOUT,
    llm,
    retrieval,
    symbolic_equivalence,
    sympy_recompute,
)
from app.tools import expression as expression_guard
from app.tools.endpoint import (
    PROVIDER_IDS,
    EndpointRejection,
    egress_of,
    validate_endpoint,
)

BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_DIR.parent
APP_DIR = BACKEND_DIR / "app"

#: 出网 / 套接字类依赖 —— 只允许出现在 ``tools/llm.py``（R-K）。
EGRESS_LIBS = {"httpx", "requests", "urllib", "socket", "aiohttp", "http"}
ALLOWED_EGRESS_FILE = APP_DIR / "tools" / "llm.py"


def imported_roots(path: Path) -> set[str]:
    """静态取出一个文件 import 的模块根名（AST，不执行）。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return {name.split(".")[0] for name in modules}


def remote_endpoint(**overrides: Any) -> dict[str, Any]:
    """一个合法的**远程**端点（规则 1–3 都过）。"""
    endpoint = {
        "provider": "deepseek",
        "model": "deepseek-chat",
        "base_url": "https://api.deepseek.com/v1",
    }
    endpoint.update(overrides)
    return endpoint


def loopback_endpoint() -> dict[str, Any]:
    return {
        "provider": "custom",
        "model": "local-qwen",
        "base_url": "http://127.0.0.1:11434/v1",
    }


class FakeTransport:
    """假 provider：记下请求、返回预置响应（R-L 的抓载荷落点）。"""

    def __init__(self, status: int = 200, body: "bytes | None" = None) -> None:
        self.status = status
        self.body = body if body is not None else json.dumps(
            {"model": "deepseek-chat", "choices": [{"message": {"content": "答案是 0.0833"}}]}
        ).encode("utf-8")
        self.requests: list[llm.ModelRequest] = []

    def __call__(self, request: llm.ModelRequest, *, timeout_s: float, max_bytes: int):
        self.requests.append(request)
        return llm.TransportResponse(status=self.status, body=self.body)


# --------------------------------------------------------------------------- #
# 与契约对齐（防两处漂移）
# --------------------------------------------------------------------------- #
def test_provider_ids_match_the_chat_contract() -> None:
    contract = json.loads((REPO_ROOT / "contracts" / "chat.schema.json").read_text(encoding="utf-8"))
    assert list(PROVIDER_IDS) == contract["definitions"]["providerId"]["enum"]


def test_model_codes_are_error_codes_defined_in_core() -> None:
    # 四类 code 必须取自 `app.core.errors` 的枚举 —— 不自造字符串（`docs/02 §6.3`）。
    assert set(MODEL_CODES) <= set(ERROR_CODES)


# --------------------------------------------------------------------------- #
# docs/02 §4.2.1 六条 —— 负样本逐条
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "endpoint, rule_fragment",
    [
        # 规则 1：预置供应商白名单
        (remote_endpoint(provider="openai"), "规则1"),
        # 结构：契约之外的多余字段（additionalProperties: false）
        ({**remote_endpoint(), "extra": 1}, "结构"),
        # 规则 5：R-A 的上界（base_url 2048 · model 128）
        (remote_endpoint(base_url="https://api.deepseek.com/" + "a" * 2100), "规则5"),
        (remote_endpoint(model="m" * 200), "规则5"),
        # 规则 3 的字符集：model 不得含控制字符 / 路径分隔符
        (remote_endpoint(model="deepseek/chat"), "规则3"),
        (remote_endpoint(model="deep\x00seek"), "规则3"),
        # 规则 3：强制 https（非本机端点）
        (remote_endpoint(base_url="http://api.deepseek.com/v1"), "规则3"),
        # 规则 3：内网 / 云元数据地址（只挡 loopback 不够）
        (remote_endpoint(base_url="https://10.0.0.5/v1"), "规则3"),
        (remote_endpoint(base_url="https://192.168.1.10/v1"), "规则3"),
        (remote_endpoint(base_url="https://172.16.5.5/v1"), "规则3"),
        (remote_endpoint(base_url="https://169.254.169.254/latest/meta-data"), "规则3"),
        (remote_endpoint(base_url="https://metadata.google.internal/v1"), "规则3"),
        (remote_endpoint(base_url="https://0.0.0.0/v1"), "规则3"),
        # 规则 2：loopback 例外只在显式勾选时生效
        (loopback_endpoint(), "规则2"),
        # R-M 第 ⑤ 面：不得带 userinfo（凭据不进 URL）
        (remote_endpoint(base_url="https://user:pw@api.deepseek.com/v1"), "规则2/3"),
    ],
)
def test_endpoint_rejections(endpoint: dict[str, Any], rule_fragment: str) -> None:
    with pytest.raises(EndpointRejection) as excinfo:
        validate_endpoint(endpoint)
    assert rule_fragment in excinfo.value.rule
    # 规则 6：对外合并成 model_unreachable，且 __str__ 不带 host / IP
    assert excinfo.value.reason == MODEL_UNREACHABLE
    assert "api.deepseek.com" not in str(excinfo.value)
    assert "169.254.169.254" not in str(excinfo.value)


def test_remote_endpoint_is_accepted_and_marked_remote() -> None:
    config = validate_endpoint(remote_endpoint())
    assert config.egress == "remote"
    assert egress_of(config) == "remote"
    assert (config.host, config.port, config.scheme) == ("api.deepseek.com", 443, "https")


def test_loopback_endpoint_needs_the_explicit_switch() -> None:
    config = validate_endpoint(loopback_endpoint(), allow_loopback=True)
    assert config.egress == "loopback"
    assert (config.host, config.port, config.scheme) == ("127.0.0.1", 11434, "http")
    # ★ 规则 2 的开关是**显式参数**：默认关闭 ⇒ 同一个端点必须被拒（上面已断言）。


# --------------------------------------------------------------------------- #
# 工具入口：失败原因（R-Q ②③）
# --------------------------------------------------------------------------- #
def test_llm_without_credentials_is_not_ok() -> None:
    result = llm.call_model([{"role": "user", "content": "1+1=?"}])
    assert result.ok is False
    assert result.reason == MODEL_KEY_MISSING


def test_remote_endpoint_with_empty_credential_is_missing_key() -> None:
    # P1 的空串**只在 loopback 端点**合法；远程端点给空串 ⇒ 仍按"未配置凭据"处理。
    result = llm.call_model(
        [{"role": "user", "content": "1+1=?"}], endpoint=remote_endpoint(), credential=""
    )
    assert (result.ok, result.reason) == (False, MODEL_KEY_MISSING)


def test_bad_endpoint_is_unreachable_not_a_crash() -> None:
    result = llm.call_model(
        [{"role": "user", "content": "1+1=?"}], endpoint=remote_endpoint(provider="openai"), credential="k"
    )
    assert (result.ok, result.reason) == (False, MODEL_UNREACHABLE)


def test_malformed_messages_are_invalid_request() -> None:
    result = llm.call_model([{"role": "tool", "content": "x"}], endpoint=remote_endpoint(), credential="k")
    assert (result.ok, result.reason) == (False, INVALID_REQUEST)


# --------------------------------------------------------------------------- #
# 纯函数层：载荷白名单（R-L）与凭据不出现在 body / repr（R-M）
# --------------------------------------------------------------------------- #
def test_build_request_body_carries_only_the_whitelisted_fields() -> None:
    config = validate_endpoint(remote_endpoint())
    request = llm.build_request(
        [{"role": "user", "content": "这道贝叶斯题怎么算？"}], endpoint=config, credential="sk-test"
    )
    payload = json.loads(request.body.decode("utf-8"))
    assert set(payload) == {"model", "messages", "stream"}  # ★ 构造即白名单
    assert [set(item) for item in payload["messages"]] == [{"role", "content"}]
    assert request.url.endswith("/v1/chat/completions")


def test_credential_goes_to_the_header_only_and_never_to_repr() -> None:
    config = validate_endpoint(remote_endpoint())
    request = llm.build_request([{"role": "user", "content": "hi"}], endpoint=config, credential="sk-secret-xyz")
    assert request.headers["Authorization"] == "Bearer sk-secret-xyz"
    assert b"sk-secret-xyz" not in request.body
    assert "sk-secret-xyz" not in repr(request)
    assert "sk-secret-xyz" not in str(request)


def test_payload_guard_rejects_forbidden_keys() -> None:
    # 主防线是"构造即白名单"；这条守的是**兜底断言**本身（R-L 的反向禁止清单）。
    with pytest.raises(llm.PayloadRejected):
        llm._assert_payload_allowed({"messages": [{"user_id": "u_1"}]})  # noqa: SLF001
    with pytest.raises(llm.PayloadRejected):
        llm._assert_payload_allowed({"context": {"text": "x"}, "mastery": {"kc": 1}})  # noqa: SLF001


# --------------------------------------------------------------------------- #
# 传输层：四码映射 · 解析后检查 · 响应体上界
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "status, expected",
    [
        (401, MODEL_KEY_INVALID),
        (403, MODEL_KEY_INVALID),
        (402, MODEL_QUOTA_EXCEEDED),
        (429, MODEL_QUOTA_EXCEEDED),
        (500, MODEL_UNREACHABLE),
        (302, MODEL_UNREACHABLE),  # 规则 4：不跟重定向 ⇒ 3xx 也是"不可达"
    ],
)
def test_upstream_status_maps_to_codes(status: int, expected: str, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeTransport(status=status, body=b"{}")
    monkeypatch.setattr(llm, "_TRANSPORT", fake)
    result = llm.call_model(
        [{"role": "user", "content": "hi"}], endpoint=remote_endpoint(), credential="sk-test"
    )
    assert (result.ok, result.reason) == (False, expected)
    assert len(fake.requests) == 1  # 真的发了一次（不是提前返回）


def test_successful_call_returns_text_via_the_fake_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeTransport()
    monkeypatch.setattr(llm, "_TRANSPORT", fake)
    result = llm.call_model(
        [{"role": "user", "content": "这道贝叶斯题怎么算？"}], endpoint=remote_endpoint(), credential="sk-test"
    )
    assert (result.ok, result.value["text"]) == (True, "答案是 0.0833")
    # ★ 抓载荷：提问原文在 body 里、凭据只在头里（R-L · R-M 第 ⑦ 面）
    body = fake.requests[0].body.decode("utf-8")
    assert "贝叶斯" in body
    assert "sk-test" not in body


def test_2xx_without_choices_is_invalid_response(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llm, "_TRANSPORT", FakeTransport(body=b'{"model": "x"}'))
    result = llm.call_model(
        [{"role": "user", "content": "hi"}], endpoint=remote_endpoint(), credential="sk-test"
    )
    assert (result.ok, result.reason) == (False, INVALID_RESPONSE)


def test_resolved_private_address_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    # 规则 3 的**解析后**一半：主机名本身合法（是 https 域名），但解析到 10/8 ⇒ 拒。
    fake_infos = [(2, 1, 6, "", ("10.0.0.5", 443))]
    monkeypatch.setattr(llm.socket, "getaddrinfo", lambda *args, **kwargs: fake_infos)
    request = llm.build_request(
        [{"role": "user", "content": "hi"}],
        endpoint=validate_endpoint(remote_endpoint()),
        credential="sk-test",
    )
    assert llm.send(request).reason == MODEL_UNREACHABLE


def test_resolved_loopback_is_allowed_only_for_loopback_endpoints(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_infos = [(2, 1, 6, "", ("127.0.0.1", 11434))]
    monkeypatch.setattr(llm.socket, "getaddrinfo", lambda *args, **kwargs: fake_infos)
    # P1（本机端点）：loopback 是**预期**结果 ⇒ 不拒
    llm._assert_resolution_allowed("127.0.0.1", 11434, loopback_ok=True)  # noqa: SLF001
    # 远程端点解析到 loopback ⇒ 拒（规则 2/3：那是服务器自己的 loopback）
    with pytest.raises(llm.EgressRejected):
        llm._assert_resolution_allowed("api.deepseek.com", 443, loopback_ok=False)  # noqa: SLF001


class _FakeResponse:
    """最小可用的 `urlopen` 响应替身（只用到 read / 上下文管理）。"""

    status = 200

    def __init__(self, body: bytes) -> None:
        self._body = body

    def read(self, size: int = -1) -> bytes:
        return self._body if size is None or size < 0 else self._body[:size]

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False


class _FakeOpener:
    def __init__(self, response: _FakeResponse) -> None:
        self._response = response

    def open(self, request: Any, timeout: "float | None" = None) -> _FakeResponse:
        return self._response


def test_response_over_the_byte_cap_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    # 规则 5 的"响应体上界"在 `_urllib_transport` 里：读 max_bytes+1，超了就拒。
    monkeypatch.setattr(llm.socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 443))])
    monkeypatch.setattr(llm.urllib.request, "build_opener", lambda *a, **k: _FakeOpener(_FakeResponse(b"x" * 10)))
    request = llm.build_request(
        [{"role": "user", "content": "hi"}],
        endpoint=validate_endpoint(remote_endpoint()),
        credential="sk-test",
    )
    with pytest.raises(llm.EgressRejected):
        llm._urllib_transport(request, timeout_s=1.0, max_bytes=4)  # noqa: SLF001
    assert llm.send(request, max_bytes=4).reason == MODEL_UNREACHABLE  # 经 send 则合并成不可达


# --------------------------------------------------------------------------- #
# 其余工具与依赖方向
# --------------------------------------------------------------------------- #
def test_retrieval_is_shape_only() -> None:
    result = retrieval.search("条件概率")
    assert result.ok is False
    assert result.reason == NOT_IMPLEMENTED


def test_tool_failures_enter_the_event_stream() -> None:
    sink = RecordingEventSink()
    ctx = NodeContext.for_node("solve", events=sink)

    solve.run({"run_id": "run_1", "session_id": "sess_1", "query": "求 2+2"}, ctx)

    failed = [
        record
        for record in sink.records
        if record.event_type == "tool.result" and record.fields["status"] == "failed"
    ]
    assert len(failed) == 2  # retrieval（W4 才实现）与 llm（缺凭据）两条失败都可见
    assert all(record.fields.get("reason") for record in failed)


def test_egress_libraries_only_appear_in_the_llm_module() -> None:
    offenders: dict[str, list[str]] = {}
    for path in APP_DIR.rglob("*.py"):
        if path == ALLOWED_EGRESS_FILE:
            continue
        bad = sorted(imported_roots(path) & EGRESS_LIBS)
        if bad:
            offenders[str(path.relative_to(BACKEND_DIR))] = bad
    assert offenders == {}


# --------------------------------------------------------------------------- #
# S2 · 两个验证手段（docs/06 §4 第 11 项：A/B 级可达）
# --------------------------------------------------------------------------- #
VERIFICATION_CONTRACT = json.loads(
    (REPO_ROOT / "contracts" / "verification.schema.json").read_text(encoding="utf-8")
)
CHECK_SCHEMA = VERIFICATION_CONTRACT["definitions"]["check"]


def assert_check_is_contract_shaped(check: "dict[str, Any]") -> None:
    """★ 用**真 jsonschema** 校验 ``definitions.check`` —— 与 `docs/04 §7` 校验① 同一口径。

    为什么不让它"看起来像"契约：形状是**装配方的输入**（`graph/nodes/verify.py` 只搬运），
    这里漂一个键名，坏的是 `verification.report` 整条链（F3 的 `must_render` 也跟着空）。
    """
    jsonschema.validate(instance=check, schema=CHECK_SCHEMA)


def test_s2_methods_come_from_the_verification_contract() -> None:
    # 手段名与可达条件是**契约里的枚举**，本仓不自造（`docs/04 §7` 校验⑤ 的同口径）。
    enum = CHECK_SCHEMA["properties"]["method"]["enum"]
    reachability = VERIFICATION_CONTRACT["x-level-reachability"]
    assert sympy_recompute.METHOD in enum
    assert symbolic_equivalence.METHOD in enum
    assert reachability["A"]["requires_any"] == [sympy_recompute.METHOD]
    assert reachability["B"]["requires_any"] == [symbolic_equivalence.METHOD]


def test_s2_tools_expose_entry_points() -> None:
    # `app/tools/__init__.py` 的约定：一个工具一个模块，模块必须暴露 `ENTRY`。
    assert sympy_recompute.ENTRY is sympy_recompute.recompute
    assert symbolic_equivalence.ENTRY is symbolic_equivalence.equivalent


def test_recompute_accepts_an_exact_answer() -> None:
    result = sympy_recompute.recompute("1/6 + 1/12", expected="0.25")
    assert result.ok is True
    assert result.reason is None
    assert result.value["passed"] is True
    assert result.value["method"] == "sympy_recompute"
    assert_check_is_contract_shaped(result.value)


def test_recompute_accepts_a_decimal_answer_within_tolerance() -> None:
    # 模型给 12 位小数、SymPy 给精确值 ⇒ 不可能逐位相等，靠相对容差收口。
    result = sympy_recompute.recompute("1/3", expected="0.333333333333")
    assert result.value["passed"] is True
    assert "容差" in result.value["detail"]


def test_recompute_flags_an_inconsistent_answer() -> None:
    # ★ 独立重算出 1/4，解答写 0.58 ⇒ 结论是"不通过"。注意 **`ok` 仍为真**：
    #   "结论是不通过"不是工具失败 —— 写成失败会让 §4.4③ 的重算流程永远触发不了。
    result = sympy_recompute.recompute("1/6 + 1/12", expected="0.58")
    assert result.ok is True
    assert result.value["passed"] is False
    assert result.value["evidence"] == {"expected": "0.580000000000000", "actual": "1/4"}
    assert_check_is_contract_shaped(result.value)


@pytest.mark.parametrize("bad", ["sin(", "(1", "1,2", "log"])
def test_recompute_treats_unparsable_input_as_not_executed(bad: str) -> None:
    # ★ 未执行 ⇒ `ok` 为假 + `parse_error`；按 `docs/02 §4.4②` **不算通过、也不算不通过**。
    result = sympy_recompute.recompute(bad, expected="0")
    assert (result.ok, result.reason) == (False, PARSE_ERROR)


@pytest.mark.parametrize(
    "hostile",
    [
        "__import__('os').system('echo pwned')",
        "open('x')",
        "a.b",
        "x[0]",
        "globals()",
        "1;2",
    ],
)
def test_verification_tools_reject_untrusted_expressions(hostile: str) -> None:
    # 表达式来自**模型输出**，而 `parse_expr` 底层是 `eval` ⇒ 白名单守卫是第一道防线。
    assert sympy_recompute.recompute(hostile, expected="0").reason == INVALID_REQUEST
    assert symbolic_equivalence.equivalent(hostile, "0").reason == INVALID_REQUEST


def test_guard_rejects_before_the_string_reaches_eval(monkeypatch: pytest.MonkeyPatch) -> None:
    """★ 不只看返回码：**证明那条载荷根本没被送进 `parse_expr`**（它底层就是 `eval`）。

    换成"写一个标记文件、再断言文件不存在"也能说明问题，但那要多一个可写目录；这条用例把
    `parse_expr` 整个换成"被调用即失败"，覆盖的是同一件事，而且不依赖文件系统。
    """

    def explode(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("守卫没拦住 —— 不可信字符串被送进了 parse_expr（底层是 eval）")

    monkeypatch.setattr(expression_guard, "parse_expr", explode)
    for hostile in ("__import__('os').system('echo pwned')", "open('x')", "a.b", "x[0]"):
        with pytest.raises(expression_guard.ExpressionRejected) as excinfo:
            expression_guard.parse(hostile)
        assert excinfo.value.kind == INVALID_REQUEST


def test_expression_namespace_keeps_builtins_out() -> None:
    """第 3 层防线（给"前两层被绕过"留的）：命名空间里**拿不到任何内置函数**。

    直接用 `eval` 是为了对准真实机制 —— `parse_expr` 的最后一跳就是 `eval(code, global_dict, …)`，
    而 `global_dict` 里的 `__builtins__` 一旦缺失，Python 会**自动补上真内置**。
    """
    namespace = dict(expression_guard._GLOBAL_DICT)  # noqa: SLF001 - 校验的就是这个命名空间
    with pytest.raises(NameError):
        eval("__import__('os')", namespace)  # noqa: S307 - 本用例要验证的正是这一跳


def test_over_long_expression_is_invalid_request() -> None:
    # R-A 的同精神：不可信字符串必须有上界（否则一次答疑能被一个超长式子拖住）。
    over_long = "1" * (expression_guard.MAX_EXPRESSION_LENGTH + 1)
    result = sympy_recompute.recompute(over_long, expected="0")
    assert (result.ok, result.reason) == (False, INVALID_REQUEST)


def test_equivalent_accepts_symbolically_equal_forms() -> None:
    result = symbolic_equivalence.equivalent("sin(x)^2 + cos(x)^2", "1")
    assert result.ok is True
    assert result.value["passed"] is True
    assert result.value["method"] == "symbolic_equivalence"
    assert_check_is_contract_shaped(result.value)


def test_equivalent_calls_a_nonzero_constant_difference_unequal() -> None:
    # 差化简成非零常数（-1）⇒ 这一支本身就是结论，不需要反例。
    result = symbolic_equivalence.equivalent("x^2 + 1", "x^2 + 2")
    assert result.value["passed"] is False
    assert "非零常数" in result.value["detail"]
    assert "evidence" not in result.value


def test_equivalent_gives_a_counterexample_when_it_cannot_decide() -> None:
    # 差是 `-x`：化简不动、但也没证明不等价 ⇒ 保守判"未判定"，并给数值反例。
    result = symbolic_equivalence.equivalent("x^2 + 1", "x^2 + x + 1")
    assert result.value["passed"] is False
    assert "未能判定" in result.value["detail"]
    assert result.value["evidence"]["counterexample"].startswith("x=")
    assert_check_is_contract_shaped(result.value)


def test_equivalent_reports_parse_error() -> None:
    result = symbolic_equivalence.equivalent("(1", "0")
    assert (result.ok, result.reason) == (False, PARSE_ERROR)


def test_run_bounded_gives_up_at_the_deadline() -> None:
    # 截止时间的语义是"我不再等它"（到点抛 TIMEOUT ⇒ 未执行，不是不通过）。
    with pytest.raises(expression_guard.ExpressionRejected) as excinfo:
        expression_guard.run_bounded(lambda: time.sleep(0.3), 1)  # noqa: SLF001
    assert excinfo.value.kind == TIMEOUT


def test_timeout_is_reported_as_not_executed(monkeypatch: pytest.MonkeyPatch) -> None:
    def give_up(call: Any, timeout_ms: int) -> Any:
        raise expression_guard.ExpressionRejected(TIMEOUT, "测试注入：到点不再等")

    monkeypatch.setattr(sympy_recompute, "run_bounded", give_up)
    result = sympy_recompute.recompute("1/2", expected="0.5")
    assert (result.ok, result.reason) == (False, TIMEOUT)


def test_sympy_stays_out_of_the_graph_layer() -> None:
    # `docs/03 §3.2` 第 2 条：业务逻辑不得写进图节点 —— 计算库出现在节点里即红灯。
    # ★ 判据只覆盖 `graph/`：`docs/03 §4` 明写"分布计算与性质推导（SymPy）"**属于 `domain`**。
    offenders = [
        str(path.relative_to(BACKEND_DIR))
        for path in (APP_DIR / "graph").rglob("*.py")
        if "sympy" in imported_roots(path)
    ]
    assert offenders == []
