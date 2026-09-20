"""app.domain —— 领域纯函数（W0b · 卡2 · 第②步）。

依赖方向铁律（docs/03 §3.1，"整张表的地基"）：**本包只允许 import ``app.core`` 与标准库**，
禁止 import 数据库 / FastAPI / LangGraph / 网络 / ``tools/`` / ``learning/``。
可自动核对方式：静态扫描 import 白名单（见 ``backend/tests/test_domain.py``）。

判定入口**不含任何 LLM 调用**（F4「能算的不要问模型」，docs/01 §4.1 · docs/02 §4.3-④）。

公开入口（docs/06 §12.8 接口清单）：
- :func:`normalize`      归一化（去空白 / 统一符号 / 精度口径）
- :func:`match_mistake`  错因匹配入口 ``domain.match_mistake(normalize(answer), item)`` —— ★ 归一化在入口内
- :func:`grade`          判定的确定性入口
"""
from app.domain.judge import grade
from app.domain.mistakes import match_mistake
from app.domain.normalize import normalize

__all__ = ["grade", "match_mistake", "normalize"]
