"""`domain/` —— 纯函数层（`docs/03 §4`）。

★ **只依赖 `core/`**（`docs/03 §3.1` 的"整张表的地基"）：不碰数据库、不碰 FastAPI、
不碰 LangGraph、不发网络请求、不 import `tools/` 与 `learning/`、**不含任何 LLM 调用**（F4）。

三个入口 —— 归一化 · 判定的确定性入口 · 错因匹配入口 —— 就是 `docs/06 §12.3` 卡2 第 ② 步的全部内容；
★ **归一化在每个入口内完成**（F5 写死：否则测的是现实中不存在的路径）。
"""

from .grade import GradeOutcome, grade
from .mistake import DETECTORS, MistakeDetector, match_mistake
from .normalize import ANSWER_KINDS, MAX_NORMALIZED_LENGTH, normalize_answer

__all__ = [
    "ANSWER_KINDS",
    "MAX_NORMALIZED_LENGTH",
    "normalize_answer",
    "GradeOutcome",
    "grade",
    "DETECTORS",
    "MistakeDetector",
    "match_mistake",
]
