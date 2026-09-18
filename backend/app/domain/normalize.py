"""归一化 —— `domain/` 的入口之一（`docs/02 §4.3-③`）。

★ 口径**继承旧仓 ProbCrew 的实测结论**（v2 文档只写了"去空白 / 统一符号 / 精度口径"六个字，
没有展开；旧仓那条链有测试钉死边界，这里照搬，不自行发明）：

* **解析顺序有讲究**：① 百分数先（否则 `2.6%` 会先被当成数字 `2.6`）→ ② 整表达式
  （否则 `45*0.05**2*0.95**8` 会被"找第一个数字"截成 `45`）→ ③ 单数字 / 分数 → ④ 取最后一个数字
  （`P(D)=0.026` 这类等式的右侧）
* **不做数值重写**：`0.026` 归一化后仍是 `0.026` —— 因为判定要**从文本读有效位**
  （`grade._precision`），重新格式化会把精度信息抹掉。唯一例外是百分数（`2.6%` → `0.026`，旧仓同此）。
"""

from __future__ import annotations

import re
import unicodedata
from decimal import Decimal

__all__ = ["ANSWER_KINDS", "MAX_NORMALIZED_LENGTH", "normalize_answer"]

#: `answer_kind` 的取值（`contracts/content.schema.json` 的 `problemEntry`）
ANSWER_KINDS: tuple[str, ...] = ("value", "text")

#: 归一化结果的上界 —— 与契约 `answer_normalized` / `expected` 的 `maxLength` 一致
MAX_NORMALIZED_LENGTH: int = 400

_LATEX_WRAP = re.compile(r"\$+([^$]+)\$+")
_LEAD_JUNK = re.compile(r"(?i)^(答案是|答案|结果|约等于|约为|等于|≈|=|：|:|\s)+")
_TRAIL_JUNK = "。.；;，, "
_SYMBOL_MAP = {
    "×": "*",
    "·": "*",
    "∗": "*",
    "÷": "/",
    "−": "-",
    "－": "-",
    "＝": "=",
    "％": "%",
    "^": "**",
}
_PUNCT_MAP = {
    "，": ",",
    "。": ".",
    "；": ";",
    "：": ":",
    "！": "!",
    "？": "?",
    "、": ",",
    "“": '"',
    "”": '"',
    "‘": "'",
    "’": "'",
}
_PERCENT = re.compile(r"(\d+(?:\.\d+)?)\s*%")
_NUMBER = re.compile(r"[+-]?\d*\.?\d+")
_FRACTION = re.compile(r"([+-]?\d+(?:\.\d+)?)\s*/\s*(\d+(?:\.\d+)?)")
_FRACTION_ONLY = re.compile(r"\s*[+-]?\d*\.?\d+\s*/\s*\d*\.?\d+\s*")
_EXPR_CHARS = re.compile(r"[0-9+\-*/^().\s]+")
_ANY_NUMBER = re.compile(r"[+-]?\d+(?:\.\d+)?(?:/\d+(?:\.\d+)?)?")
_WHITESPACE = re.compile(r"\s+")
_EDGE_PUNCT = re.compile(r"^[\W_]+|[\W_]+$", re.UNICODE)


def _clean(raw: str) -> str:
    """NFKC + 剥 `$…$` + 去前后缀噪声 + 符号统一。"""
    text = unicodedata.normalize("NFKC", raw).strip()
    text = _LATEX_WRAP.sub(r"\1", text)
    text = _LEAD_JUNK.sub("", text)
    text = text.strip(_TRAIL_JUNK)
    for src, dst in _SYMBOL_MAP.items():
        text = text.replace(src, dst)
    return text.strip()


def _normalize_text(raw: str) -> str:
    """文本类答案：全角转半角 → 标点归一 → 去所有空白 → 拉丁字母小写 → 去首尾标点。"""
    text = unicodedata.normalize("NFKC", raw)
    for src, dst in _PUNCT_MAP.items():
        text = text.replace(src, dst)
    text = _WHITESPACE.sub("", text).lower()
    return _EDGE_PUNCT.sub("", text)


def _normalize_symbol(raw: str) -> str:
    """值类兜底：NFKC + 去空白。

    ★ **不剥括号、不改大小写**（2026-09-19 实测修正）：最初这一步复用了文本归一化，
    而它会把 `P(B|A)` 末尾的 `)` 当"首尾标点"剥掉 —— 于是条件概率方向检测永远匹配不上。
    另外 `Phi(1.96)` 这类写法依赖大小写与 `grade._SAFE_LOCALS` 的键名，改小写同样会废掉它。
    """
    return _WHITESPACE.sub("", unicodedata.normalize("NFKC", raw))


def _percent_to_decimal(text: str) -> str:
    """百分数 → 小数（**用 Decimal，不用浮点**）。

    ★ `float("2.6") / 100` 得到的是 `0.026000000000000002` —— 这个尾差会原样写进
    `attempt.answer_normalized`（契约字段，F4 要求"判定可复算"）。用 Decimal 做十进制移位，
    `2.6%` → `0.026`，且保留学生原本写出的位数。
    """
    return format(Decimal(text) / Decimal(100), "f")


def _normalize_value(raw: str) -> str:
    """值类答案：按旧仓的解析顺序取出可比较的表达式文本。"""
    text = _clean(raw)
    if not text:
        return ""

    # ① 百分数先 —— 否则 2.6% 会被当成 2.6
    percent = _PERCENT.fullmatch(text)
    if percent is None and text.endswith("%"):
        percent = _PERCENT.search(text)
    if percent is not None:
        return _percent_to_decimal(percent.group(1))

    # ② 整个输入就是一个算式（含运算符，且不是纯分数）
    if (
        _FRACTION_ONLY.fullmatch(text) is None
        and _EXPR_CHARS.fullmatch(text) is not None
        and re.search(r"[+\-*]|/\s*[a-zA-Z(]", text) is not None
    ):
        return _WHITESPACE.sub("", text)

    # ③ 单个数字 / 分数
    if _NUMBER.fullmatch(text) is not None:
        return text
    fraction = _FRACTION.fullmatch(text)
    if fraction is not None:
        return f"({fraction.group(1)})/({fraction.group(2)})"

    # ④ 等式右侧或文字里最后一个数（P(D)=0.026 / 答案是 0.026）
    numbers = _ANY_NUMBER.findall(text)
    if numbers:
        return numbers[-1]

    # 解析不出数值形态：退回符号归一化（不返回空串，否则会把"写成了式子"读成"没作答"）
    return _normalize_symbol(text)


def normalize_answer(raw: str, answer_kind: str = "value") -> str:
    """把学生答案（或标准答案）归一化成可比较的形式。

    * `answer_kind="value"` —— 数值 / 闭式表达式
    * `answer_kind="text"` —— 含单位或文字结论的答案

    ★ 越界（> `MAX_NORMALIZED_LENGTH`）**抛 `ValueError`**：契约 `answer_normalized` 的上界是 400，
    截断会静默改变判定对象。

    ★★ **长度上界必须在任何正则之前判**（2026-09-19 实测加固）。理由不是洁癖，是 R-A 那条：
    本模块最初把长度检查放在解析之后，而 `%` 那个正则写的是 `(\\d+(?:\\.\\d+)?)\\s*%` ——
    对一个 10 万位的纯数字串，引擎会为 `\\d+` 枚举所有切分点再回溯，**实测直接把进程挂住**
    （旧仓的 P0 事故同型：100k 位数字耗时约 2 分钟）。现在先卡长度，后再跑正则。
    """
    if answer_kind not in ANSWER_KINDS:
        raise ValueError(f"answer_kind 必须是 {ANSWER_KINDS} 之一，实际为 {answer_kind!r}")
    if not isinstance(raw, str):
        raise ValueError("raw 必须是字符串")
    if len(raw) > MAX_NORMALIZED_LENGTH:
        raise ValueError(
            f"输入超长（{len(raw)} > {MAX_NORMALIZED_LENGTH}）—— "
            "先卡长度再跑正则，避免长数字串上的灾难性回溯（docs/02 R-A）"
        )

    normalized = _normalize_text(raw) if answer_kind == "text" else _normalize_value(raw)
    if len(normalized) > MAX_NORMALIZED_LENGTH:
        raise ValueError(
            f"归一化结果超长（{len(normalized)} > {MAX_NORMALIZED_LENGTH}）"
            "，上界见 contracts/attempt.schema.json"
        )
    return normalized
