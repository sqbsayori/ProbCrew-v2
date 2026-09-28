"""``main.py`` 的用例 —— 与被测模块同名（``docs/05 §3``，入口与配置归架构与集成）。

判据对照：

- **单 worker**：并发度 > 1 ⇒ 拒绝启动并说明原因（``docs/02 §8-15`` · N20 的"不做"）；
- ★ **部署形态开关（N16 的"部署者开关"，2026-09-27 补）**：``docs/02 §1.5①`` 写着 loopback 例外
  「**仅在单机形态**，且必须由部署者**显式勾选**才生效；**默认关闭**」，而此前"勾在哪一行"没有落点。
  本文件把那一句话变成可断言的形状：**缺省最严** · **坏值拒绝启动** · **单机不得对外监听**。
- **端口**：坏值不静默降级（与上面同款）。
"""
from __future__ import annotations

import pytest

from app.main import (
    DEPLOYMENT_ENV,
    DEPLOYMENT_HOSTED,
    DEPLOYMENT_SINGLE,
    REFUSE_EXIT_CODE,
    assert_deployment_consistent,
    describe_deployment,
    loopback_endpoint_allowed,
    main,
    resolve_deployment,
    resolve_port,
)


# ---- 部署形态（N16 的部署者开关） ------------------------------------------- #

def test_deployment_defaults_to_the_strictest_form() -> None:
    """未设置 / 空串 / 纯空白 ⇒ **远程托管**（= loopback 例外"默认关闭"的机器可读形式）。"""
    assert resolve_deployment({}) == DEPLOYMENT_HOSTED
    assert resolve_deployment({DEPLOYMENT_ENV: ""}) == DEPLOYMENT_HOSTED
    assert resolve_deployment({DEPLOYMENT_ENV: "   "}) == DEPLOYMENT_HOSTED


def test_deployment_single_must_be_explicit() -> None:
    """单机形态只能由部署者**显式**声明（大小写不敏感）。"""
    assert resolve_deployment({DEPLOYMENT_ENV: "single"}) == DEPLOYMENT_SINGLE
    assert resolve_deployment({DEPLOYMENT_ENV: "SINGLE"}) == DEPLOYMENT_SINGLE


def test_deployment_bad_value_refuses_to_start() -> None:
    """坏值**不猜**：猜一个等于把安全开关交给默认值。"""
    with pytest.raises(SystemExit) as exc:
        resolve_deployment({DEPLOYMENT_ENV: "prod"})
    assert exc.value.code == REFUSE_EXIT_CODE


def test_loopback_endpoint_only_allowed_in_single() -> None:
    """``docs/02 §4.2.1`` 规则 2：loopback 例外**仅在单机形态**（服务端这一半）。"""
    assert loopback_endpoint_allowed(DEPLOYMENT_SINGLE) is True
    assert loopback_endpoint_allowed(DEPLOYMENT_HOSTED) is False


def test_single_must_not_listen_publicly() -> None:
    """单机形态 + 对外监听 = 规则 2 明确要挡的那个场景（学生填 ``127.0.0.1`` 打服务器内部）。"""
    assert_deployment_consistent(DEPLOYMENT_SINGLE, "127.0.0.1")   # 不抛
    assert_deployment_consistent(DEPLOYMENT_HOSTED, "0.0.0.0")     # 远程形态对外监听不归这条管
    with pytest.raises(SystemExit) as exc:
        assert_deployment_consistent(DEPLOYMENT_SINGLE, "0.0.0.0")
    assert exc.value.code == REFUSE_EXIT_CODE


def test_describe_states_the_switch_outcome() -> None:
    """启动时那一行必须说清"例外开不开" —— 否则运维看不到自己跑在哪一档。"""
    assert "单机" in describe_deployment(DEPLOYMENT_SINGLE)
    assert "一律拒绝" in describe_deployment(DEPLOYMENT_HOSTED)


def test_main_refuses_on_bad_deployment(monkeypatch: pytest.MonkeyPatch) -> None:
    """端到端一条：坏形态 ⇒ 在**起 uvicorn 之前**就拒绝（因此本用例不会开端口）。"""
    monkeypatch.delenv("WEB_CONCURRENCY", raising=False)
    monkeypatch.setenv(DEPLOYMENT_ENV, "nope")
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == REFUSE_EXIT_CODE


# ---- 端口 ------------------------------------------------------------------- #

def test_port_default_and_bounds() -> None:
    assert resolve_port({}) == 8000
    assert resolve_port({"PORT": "8899"}) == 8899
    with pytest.raises(ValueError):
        resolve_port({"PORT": "70000"})
