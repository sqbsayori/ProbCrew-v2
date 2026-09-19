r"""归一化 —— W0b · 卡2 · 第②步（docs/02 §4.3-③）。

文档规格只有九个字：「去空白 / 统一符号 / 精度口径」，外加契约实例锚点
``P(B|A) → p(b|a)``（attempt.schema.json examples，大小写归一）。
分数 / 百分数 / 单位的逐项处理文档**没有明文** —— 本文件取 W0 最小集（见下表），
按 docs/06 §12.5 检查点 5，**实现定值后须回写 docs/02 §4.3**（由本域的人改 docs）。

W0 最小集（每条都可被用例单独复算）：

| 类 | 规则 | 例 |
|----|------|----|
| 去空白 | 删除全部空白字符（含全角空格 / 制表 / 换行） | ``"P(B | A)"`` → ``"p(b|a)"`` |
| 统一符号 | 全角 → 半角（NFKC）；统一小写；``−``→``-``；``×``→``*``；``÷``→``/`` | ``Ｐ（Ｂ｜Ａ）`` → ``p(b|a)`` |
| 精度口径 | 小数去尾零（``0.50``→``0.5``，``1.00``→``1``） | 数值等价交由判定入口按精确有理数复算 |

不做的事（W0 刻意不做，避免发明没人约定的口径）：
- 不解析 LaTeX / 不展开分数约分（``1/4`` 与 ``0.25`` 的等价在**判定入口**用精确有理数比较处理，
  归一化保持原文形状，保证 ``answer_normalized`` 可复算）；
- 不处理单位换算（单位差异是**错因** mp_unit_error 的检测素材，归一化阶段不能抹掉）。

性能纪律（R-A）：**全程线性**。刻意不用正则做小数处理 —— ``\d+\.\d+`` 这类模式在
无匹配的纯数字长串上会逐位置重启贪心扫描，实测 100k 位数字耗时 31s（O(n²)），
线性手动扫描器 < 10ms。这也是旧仓 100k 位数字 P0 事故的同族问题。
"""
from __future__ import annotations

import unicodedata


def _trim_decimals(text: str) -> str:
    """线性扫描去小数尾零（``0.50``→``0.5``，``1.00``→``1``）。

    刻意不用 ``re.sub(r"\\d+\\.\\d+", ...)``：无匹配的长数字串会让正则引擎
    在每个位置重启 ``\\d+`` 的贪心扫描，退化为 O(n²)（实测 100k 位 31s）。
    本扫描器对每个字符只访问一次，O(n)。
    """
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if not ch.isdigit():
            out.append(ch)
            i += 1
            continue
        # 整数段 [i, j)
        j = i
        while j < n and text[j].isdigit():
            j += 1
        # 小数段 j.digits（须有至少一位小数才算小数）
        if j < n and text[j] == "." and j + 1 < n and text[j + 1].isdigit():
            k = j + 1
            while k < n and text[k].isdigit():
                k += 1
            fraction = text[j + 1 : k].rstrip("0")
            out.append(text[i:j])
            if fraction:
                out.append(".")
                out.append(fraction)
            i = k
        else:
            out.append(text[i:j])
            i = j
    return "".join(out)


def normalize(answer: str) -> str:
    """把学生答案 / 标准答案归一化为可比较、可复算的规范形式。

    纯函数：不读环境、不落盘、无副作用。空串进空串出。
    """
    if not isinstance(answer, str):
        raise TypeError("answer 必须是字符串")

    # ① 全角 → 半角（Ｐ（Ｂ｜Ａ） → P(B|A)；全角空格 → 空格）
    text = unicodedata.normalize("NFKC", answer)

    # ② 统一符号（在去空白之前做替换，避免依赖空白位置）
    table = str.maketrans({
        "−": "-",  # U+2212 MINUS SIGN（NFKC 不转换）
        "×": "*",
        "÷": "/",
    })
    text = text.translate(table)

    # ③ 去空白：删除全部空白字符（含全半角空格 / 制表 / 换行）
    text = "".join(ch for ch in text if not ch.isspace())

    # ④ 大小写归一（契约实例锚点：P(B|A) → p(b|a)）
    text = text.lower()

    # ⑤ 精度口径：小数去尾零（0.50 → 0.5；1.00 → 1）
    text = _trim_decimals(text)

    return text
