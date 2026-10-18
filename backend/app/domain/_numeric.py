"""数值解析的共享入口 —— 判定（``judge``）与错因匹配（``mistakes``）共用。

★ **R-A 性能护栏**（旧仓 100k 位数字 P0 的同族问题）：``Fraction('1e1000000')`` 会计算
``10**1000000`` 的大整数幂，而字符串只有 9 个字 —— 单 worker + GIL 下等于整站阻塞。
因此本入口**拒绝指数记号**（``e`` / ``E``）与**超长数字串**（``> NUMERIC_TEXT_MAX``），
被拒时如实返回 ``None``（调用方退化为形状相等），而不是花十秒算一个学生根本不会填的数。

纯函数 + 纯计算：无 LLM、无网络、无落盘（``docs/02 §4.3`` 硬性设计 1、4）。
"""
from __future__ import annotations

from fractions import Fraction

#: 数值文本长度上界（防御性上界；R-A 的答案上界 200 字之外，错因匹配侧另有 ``MAX_MATCH_LENGTH=400``）。
NUMERIC_TEXT_MAX = 64


def as_fraction(text: str) -> "Fraction | None":
    """精确有理数解析（支持 ``50%`` / ``1/4`` / 小数）；失败或被护栏拒绝时返回 ``None``。"""
    if "e" in text or "E" in text or len(text) > NUMERIC_TEXT_MAX:
        return None
    candidate = text
    if candidate.endswith("%"):
        try:
            return Fraction(candidate[:-1]) / 100
        except (ValueError, ZeroDivisionError):
            return None
    try:
        return Fraction(candidate)
    except (ValueError, ZeroDivisionError):
        return None
