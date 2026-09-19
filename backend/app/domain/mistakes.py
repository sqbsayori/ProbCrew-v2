"""错因匹配 —— W0b · 卡2 · 第②步（F5，docs/01 §6 · docs/02 §4.3-④）。

入口签名（全仓唯一写法，docs/06 §12.3 卡2 · docs/01 §6 · content.schema.json）::

    domain.match_mistake(normalize(answer), item)

★ **归一化必须在入口内**（docs/06 §12.8：否则测的是现实中不存在的路径）——
因此 :func:`match_mistake` 接收**学生原文**，第一步自己调 :func:`normalize`。

F5 命中定义（集合语义，content.schema.json answer_injection）：
**实际命中集合 ⊇ 期望集合，且不含未期望条目**。未识别 ⇒ **如实返回空列表**，不得编造
（负样本即红灯；``mistake_matched=false`` 是「如实未识别」的机器可读形式）。

W0 机制（实现定值，按 docs/06 §12.5 检查点 5 须回写 docs/02 §4.3）：
- 规则注册表 :data:`RULES` 是**显式**的「错因 ID → 检测规则」映射（非空且非通配，呼应 R-Q ① 的精神）；
- 覆盖 content.schema.json 注入集的四个 category（direction_reversed / missing_partition /
  unit_error / independence_confusion），每条规则都是纯函数、确定性、无 LLM；
- 全部正则为**平坦模式，无嵌套量词**（R-A：旧仓 100k 位数字 P0 事故）；
- ``content/mistakes.json``（≥ 40 条，模块四资产）在 W2 接入后，规则表由注入集校准扩充；
  本文件只提供**最小可测机制**，不预设题库内容。

规则边界（刻意保守，避免「编造错因」）：
- 规则只比较 ``item["expected"]`` 的归一化结果与答案的归一化结果（外加题干做独立性语境判定），
  不做任何语义推断；
- 一条规则判不出就返回 False，由外层如实聚合。
"""
from __future__ import annotations

import re
from fractions import Fraction
from typing import Any, Callable

from app.domain.normalize import normalize

#: 条件概率记号 p(a|b)（归一化后必为小写；平坦正则，无嵌套量词）。
_CONDITIONAL = re.compile(r"([a-z]+)\(([^()|]*)\|([^()|]*)\)")

#: 数字片段（平坦正则）。
_NUMBER = re.compile(r"-?\d+(?:\.\d+)?")

#: 独立重复试验的题干语境（平坦交替，无嵌套量词）。
_INDEPENDENCE_STEM = re.compile(r"独立|重复|抛掷|投掷|射击|有放回|n\s*=?\s*\d+")


def _as_fraction(text: str) -> "Fraction | None":
    """把归一化后的片段解析为精确有理数；支持 ``50%`` 与 ``1/4``；失败返回 None。"""
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


# ---------------------------------------------------------------------------
# 规则一：方向颠倒（direction_reversed）—— 契约示例 ai_01：
#   expected="P(A|B)"，answer="P(B|A)" ⇒ 命中 mp_direction_reversed
# ---------------------------------------------------------------------------
def _rule_direction_reversed(ans: str, exp: str, item: "dict[str, Any]") -> bool:
    if ans == exp or not _CONDITIONAL.search(ans):
        return False
    swapped = _CONDITIONAL.sub(lambda m: f"{m.group(1)}({m.group(3)}|{m.group(2)})", ans)
    return swapped == exp


# ---------------------------------------------------------------------------
# 规则二：单位错（unit_error）—— 数值相等但单位（非数字余部）不一致
#   expected="5米"，answer="5厘米"；expected="0.5"，answer="50%" 也归此类
# ---------------------------------------------------------------------------
def _rule_unit_error(ans: str, exp: str, item: "dict[str, Any]") -> bool:
    ans_nums = _NUMBER.findall(ans)
    exp_nums = _NUMBER.findall(exp)
    if len(ans_nums) != 1 or len(exp_nums) != 1:
        return False
    value_ans = _as_fraction(ans_nums[0])
    value_exp = _as_fraction(exp_nums[0])
    if value_ans is None or value_exp is None or value_ans != value_exp:
        return False
    remainder_ans = _NUMBER.sub("", ans)
    remainder_exp = _NUMBER.sub("", exp)
    return remainder_ans != remainder_exp


# ---------------------------------------------------------------------------
# 规则三：漏完备组（missing_partition）—— 全概率公式漏项：
#   expected 是「+」连接的多项式，答案恰好是其中的真子集
# ---------------------------------------------------------------------------
def _rule_missing_partition(ans: str, exp: str, item: "dict[str, Any]") -> bool:
    exp_terms = [term for term in exp.split("+") if term]
    ans_terms = [term for term in ans.split("+") if term]
    if len(exp_terms) < 2 or not ans_terms:
        return False
    exp_set = set(exp_terms)
    ans_set = set(ans_terms)
    return ans_set < exp_set


# ---------------------------------------------------------------------------
# 规则四：n 乘错 / 独立性混淆（independence_confusion）——
#   答案 = 期望值 × n 或 ÷ n（n 为 ≥2 的整数），且题干含独立重复语境
#   （刻意要求题干语境，避免把普通数值错判成 n 乘错 —— 保守优先）
# ---------------------------------------------------------------------------
def _rule_independence_confusion(ans: str, exp: str, item: "dict[str, Any]") -> bool:
    value_ans = _as_fraction(ans)
    value_exp = _as_fraction(exp)
    if value_ans is None or value_exp is None or value_exp == 0:
        return False
    ratio = value_ans / value_exp
    if ratio.denominator == 1:
        n = ratio.numerator
    else:
        inverse = Fraction(1) / ratio
        if inverse.denominator != 1:
            return False
        n = inverse.numerator
    if n < 2:
        return False
    stem = normalize(str(item.get("stem", "")))
    return bool(_INDEPENDENCE_STEM.search(stem))


#: 显式规则注册表：错因 ID → 检测规则（非空且非通配；顺序即 mistake_id 取单值时的优先序）。
RULES: "list[tuple[str, Callable[[str, str, dict[str, Any]], bool]]]" = [
    ("mp_direction_reversed", _rule_direction_reversed),
    ("mp_unit_error", _rule_unit_error),
    ("mp_missing_partition", _rule_missing_partition),
    ("mp_independence_confusion", _rule_independence_confusion),
]


#: 错因匹配的输入长度护栏：契约注入集的 answer 上界是 200 字（answer_injection maxLength），
#: 取 2× 裕量。超长输入不属于任何现实路径（判定入口的 R-A 上界本来就会拦截），
#: 如实返回「未识别」—— 同时避免大整数 Fraction 运算的平方级开销（R-A 性能纪律）。
MAX_MATCH_LENGTH = 400


def match_mistake(answer: str, item: "dict[str, Any]") -> "list[str]":
    """错因匹配入口（F5）。接收**学生原文**，归一化在入口内完成。

    返回命中的错因 ID 列表（集合语义：调用方按集合断言 ⊇ 期望且不含未期望）；
    未识别任何错因时返回 ``[]`` —— 如实说「未识别」，不得编造。
    """
    if not isinstance(answer, str):
        raise TypeError("answer 必须是字符串")
    if not isinstance(item, dict):
        raise TypeError("item 必须是题库条目（dict）")

    normalized_answer = normalize(answer)
    normalized_expected = normalize(str(item.get("expected", "")))

    if not normalized_answer or not normalized_expected:
        return []
    if len(normalized_answer) > MAX_MATCH_LENGTH or len(normalized_expected) > MAX_MATCH_LENGTH:
        # 病理输入（如 100k 位数字）：不在契约输入面内，如实「未识别」，不做昂贵的数值规则运算
        return []

    hits: list[str] = []
    for mistake_id, rule in RULES:
        if rule(normalized_answer, normalized_expected, item):
            hits.append(mistake_id)
    return hits
