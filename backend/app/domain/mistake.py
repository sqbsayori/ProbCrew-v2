"""错因匹配入口 —— `domain/` 的入口之三（`docs/02 §4.3-④` · F5）。

★ **F5 写死了两件事**，本模块逐条兑现：

1. **归一化必须在入口内** —— 所以 `match_mistake()` 收的是学生原文，自己调 `normalize_answer`；
   否则测的是现实中不存在的路径。
2. **未命中就如实说"未识别错因"，不得编造** —— 所以返回的是**集合**：
   空集合 = 未识别；且**只有确实存在于当前错因库里的 `mp_id` 才可能被返回**。

★ 检测器注册表（`DETECTORS`）：每条检测器是一个**纯谓词** —— 吃（归一化后的学生答案 · 题条目 ·
归一化后的标准答案），输出"是不是这个错因"。这填的是 v2 契约里的一个客观空白：
`mistakeEntry` 只有 `mp_id / name / category / kc_ids / explanation`，**没有任何"可机器匹配的钩子"字段**，
而本轮**契约不许改**（`docs/06 §12.6` 规则 2）—— 于是匹配规则只能落在本域自己的代码里。

⚠️ **已知返工点（如实记账）**：本轮 4 个 `mp_id` / `category` 是**临时命名**，
等 W2 的 `content/mistakes.json`（错因库，本域主责）定稿后要在本文件内对齐一次 ——
成本低：注册表是本域自己的文件，且契约零改动。

★ **同一"证据族"最多命中一条**（`MistakeDetector.family`）：数值类偏差彼此高度重叠 ——
`2.6` 与标准答案 `0.026` 同时满足"百分比未换算"与"小数点位移两位"。
两条都返回会让 F5 的"**不含未期望条目**"当场不成立，所以按注册表顺序取**第一条命中**。
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import sympy as sp

from .normalize import normalize_answer

__all__ = ["DETECTORS", "MISTAKES_PATH", "MistakeDetector", "load_mistakes", "match_mistake"]

# 错因库的落点（docs/03 §2：content/mistakes.json）
MISTAKES_PATH: Path = Path(__file__).resolve().parents[3] / "content" / "mistakes.json"

# 条件概率写法 P(A|B)，方向颠倒检测用
_CONDITIONAL = re.compile(r"p\s*\(\s*([a-z0-9_]+)\s*\|\s*([a-z0-9_]+)\s*\)", re.IGNORECASE)

_REL_EPS = 1e-6


@dataclass(frozen=True)
class MistakeDetector:
    """一条错因检测器：`mp_id` + 一级分类 + 证据族 + 纯谓词。

    `family` 是"这条检测器读的是哪一类证据"（`symbol` = 符号结构，`value` = 数值大小）；
    **同一族在一次匹配里最多命中一条**，见模块 docstring。
    """

    mp_id: str
    category: str
    family: str
    description: str
    predicate: Callable[[str, Mapping[str, Any], str], bool]


def _as_float(expr: str) -> float | None:
    """把归一化后的表达式求成实数；求不出（含未赋值符号 / 复数）就返回 None。"""
    if not expr:
        return None
    try:
        value = sp.N(sp.sympify(expr))
    except Exception:  # noqa: BLE001 —— 解析不了不是异常路径，是"匹配不上"
        return None
    if getattr(value, "is_real", None) is False:
        return None
    try:
        return float(value)
    except Exception:  # noqa: BLE001
        return None


def _close(left: float, right: float) -> bool:
    return abs(left - right) <= _REL_EPS * max(1.0, abs(left), abs(right))


def _is_direction_reversed(student: str, item: Mapping[str, Any], expected: str) -> bool:
    """条件方向颠倒：把 `P(A|B)` 的下标对调后与标准答案一致。"""
    got = _CONDITIONAL.search(student)
    want = _CONDITIONAL.search(expected)
    if got is None or want is None:
        return False
    return got.groups() != want.groups() and (got.group(1), got.group(2)) == (want.group(2), want.group(1))


def _is_percent_not_converted(student: str, item: Mapping[str, Any], expected: str) -> bool:
    """百分比未换算：学生写 `2.6`，标准答案是 `0.026`（或反过来）。"""
    got, want = _as_float(student), _as_float(expected)
    if got is None or want is None or want == 0:
        return False
    return _close(got, want * 100.0) or _close(got, want / 100.0)


def _is_sign_error(student: str, item: Mapping[str, Any], expected: str) -> bool:
    """符号错：数值大小一样，但符号写反了。"""
    got, want = _as_float(student), _as_float(expected)
    if got is None or want is None or want == 0:
        return False
    return (not _close(got, want)) and _close(got, -want)


def _is_decimal_shift(student: str, item: Mapping[str, Any], expected: str) -> bool:
    """小数点位移：数值差 10 倍或 100 倍。"""
    got, want = _as_float(student), _as_float(expected)
    if got is None or want is None or want == 0:
        return False
    return any(_close(got, want * (10.0**k)) for k in (-2, -1, 1, 2))


# 本轮内置的 4 条检测器，覆盖 3 类一级分类（命名待 W2 错因库对齐，见模块 docstring）
# 顺序 = 优先级：同一证据族里先命中的那条胜出
DETECTORS: tuple[MistakeDetector, ...] = (
    MistakeDetector(
        mp_id="mp_direction_reversed",
        category="条件方向",
        family="symbol",
        description="条件概率方向写反了（P(A|B) 与 P(B|A) 互换）",
        predicate=_is_direction_reversed,
    ),
    MistakeDetector(
        mp_id="mp_percent_not_converted",
        category="单位与量纲",
        family="value",
        description="百分数没有换算成小数（或多乘了 100）",
        predicate=_is_percent_not_converted,
    ),
    MistakeDetector(
        mp_id="mp_sign_error",
        category="符号与方向",
        family="value",
        description="数值大小对，符号写反了",
        predicate=_is_sign_error,
    ),
    MistakeDetector(
        mp_id="mp_decimal_shift",
        category="数值精度",
        family="value",
        description="小数点位置错（差 10 倍 / 100 倍）",
        predicate=_is_decimal_shift,
    ),
)


def load_mistakes(path: Path | None = None) -> tuple[Mapping[str, Any], ...]:
    """读错因库（`content/mistakes.json`）。

    ★ 文件不存在 ⇒ **空元组**：本轮（W0b）错因库尚未产出（产出在 W2），
    空库的语义是"没有任何已知错因可匹配" ⇒ `match_mistake` 返回空集合 ⇒ 如实说"未识别错因"。
    """
    target = MISTAKES_PATH if path is None else Path(path)
    if not target.exists():
        return ()
    raw = json.loads(target.read_text(encoding="utf-8"))
    items = raw.get("items") if isinstance(raw, Mapping) else None
    if not isinstance(items, list):
        return ()
    return tuple(entry for entry in items if isinstance(entry, Mapping))


def match_mistake(
    raw_answer: str,
    item: Mapping[str, Any],
    *,
    mistakes: Sequence[Mapping[str, Any]] | None = None,
) -> frozenset[str]:
    """匹配错因 —— F5 的函数级入口。

    * 归一化在**入口内**完成（F5 写死）
    * 返回值只包含**确实存在于当前错因库**的 `mp_id`
    * 未命中 / 库为空 / 命中的 id 不在库里 ⇒ 返回**空集合**（"未识别错因"，不得编造）
    * **同一证据族最多一条** —— 否则"百分比未换算"与"小数点位移"会同时命中同一个答案，
      而 F5 的断言是"实际命中集合 **不含未期望条目**"

    `mistakes` 参数供 W2 之前的用例注入快照；缺省读 `content/mistakes.json`。
    """
    answer_kind = item.get("answer_kind", "value")
    expected_raw = item.get("expected", "")
    if not isinstance(expected_raw, str):
        return frozenset()

    normalized = normalize_answer(raw_answer, answer_kind)
    expected = normalize_answer(expected_raw, answer_kind)

    entries = load_mistakes() if mistakes is None else tuple(mistakes)
    known = {str(entry["mp_id"]) for entry in entries if "mp_id" in entry}
    if not known:
        return frozenset()

    hits: set[str] = set()
    claimed: set[str] = set()
    for detector in DETECTORS:
        if detector.mp_id not in known or detector.family in claimed:
            continue
        if detector.predicate(normalized, item, expected):
            claimed.add(detector.family)
            hits.add(detector.mp_id)
    return frozenset(hits)
