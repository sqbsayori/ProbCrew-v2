#!/usr/bin/env python3
"""`scripts/load_test.py` —— 并发压测（N20）· 架构与集成 · 阶段门 B 第 9 项演练 ④。

判据（`docs/01 §6` N20 · `docs/06 §6` 演练④ · `docs/02 §8-13g`）：
1. 10 路并发答疑下 `GET /api/health` 仍 < 100ms；
2. 第 11 路起即超限 ⇒ 如实 503；
3. 5 路中途断流 ⇒ 图取消、无残留；
4. 10 答疑 + 50 判定复合压测无 5xx、无写库冲突；
5. 以上均在 `worker=1`。

★ 诚实（N2）：目标端点（`/api/health` · `/api/chat` · `/api/practice/grade`）属 W1–W4，尚未产出。
本脚本只对端点做**真实探测**；端点返回 404 `not_found`（契约信封）时如实报「端点未就绪」，
**不伪造任何延迟 / 吞吐数字**，并以非零码退出 —— 演练④ 的真正执行要等这些端点落地。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

#: 让 `from app.core.errors import …` 可复用（复用错误码词汇表）。
REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.core.errors import ERROR_CODES  # noqa: E402

#: N20 的并发口径（`docs/01 §6` N20 格子）。
CHAT_CONCURRENCY = 10
GRADE_CONCURRENCY = 50
SAMPLES_PER_WORKER = 20
HEALTH_BUDGET_MS = 100


def _probe(base_url: str, path: str, timeout_s: float = 3.0) -> "tuple[int, float, str | None]":
    """打一个端点，返回 (HTTP 状态, 延迟毫秒, code)。失败也如实返回状态 0，不猜。"""
    start = time.perf_counter()
    try:
        with urllib.request.urlopen(base_url + path, timeout=timeout_s) as resp:
            status = resp.status
            body = resp.read().decode("utf-8", "replace")
            code = None
            if body.strip().startswith("{"):
                try:
                    code = json.loads(body).get("error", {}).get("code")
                except json.JSONDecodeError:
                    code = None
            return status, (time.perf_counter() - start) * 1000, code
    except urllib.error.HTTPError as exc:
        elapsed = (time.perf_counter() - start) * 1000
        body = exc.read().decode("utf-8", "replace")
        code = None
        try:
            code = json.loads(body).get("error", {}).get("code")
        except json.JSONDecodeError:
            code = None
        return exc.code, elapsed, code
    except Exception as exc:  # noqa: BLE001 —— 连接失败要如实报，不静默
        return 0, (time.perf_counter() - start) * 1000, type(exc).__name__


def _assert_ready(base_url: str) -> "str | None":
    """探测 `/api/health`；未就绪返回一段说明（供 main 打印后退出）。"""
    status, _, code = _probe(base_url, "/api/health")
    if status == 404 and code in (None, "not_found"):
        return (
            "端点未就绪：GET /api/health 返回 404 not_found —— 它属 W1–W4 的 api/ 接线，"
            "尚未产出。压测判据要等 /api/health · /api/chat · /api/practice/grade 落地后再跑。"
        )
    if status == 0:
        return "连不上服务：先按 docs/03 §8.4 第 2 步起服务（cd backend && python -m app.main）。"
    return None


def _health_latency(base_url: str) -> None:
    """N20 判据①：10 路并发下 `/api/health` < 100ms。"""
    def one(_: int) -> float:
        _, elapsed, _code = _probe(base_url, "/api/health")
        return elapsed

    with ThreadPoolExecutor(max_workers=CHAT_CONCURRENCY) as pool:
        latencies = list(pool.map(one, range(CHAT_CONCURRENCY)))

    worst = max(latencies)
    verdict = "PASS" if worst < HEALTH_BUDGET_MS else "FAIL"
    print(f"[{verdict}] 10 路并发 /api/health：最大延迟 {worst:.1f}ms（预算 < {HEALTH_BUDGET_MS}ms）")


def main(argv: "list[str] | None" = None) -> int:
    parser = argparse.ArgumentParser(
        prog="scripts/load_test.py",
        description="N20 并发压测（worker=1）—— 端点未就绪时如实报并退出",
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    args = parser.parse_args(argv)

    not_ready = _assert_ready(args.base_url)
    if not_ready:
        print(f"[SKIP] {not_ready}")
        return 1

    _health_latency(args.base_url)
    # ★ N20 判据②③④（第 11 路 503 · 5 路断流无残留 · 10+50 复合压测）都要 /api/chat 与
    #   /api/practice/grade —— 它们属 W3 / W2，端点落地后在此接上（docs/06 §6 演练④）。
    print("[SKIP] 判据②③④ 待 /api/chat 与 /api/practice/grade 落地（W2 / W3）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
