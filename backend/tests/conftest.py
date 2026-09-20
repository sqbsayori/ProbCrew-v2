"""`backend/tests/` 的**入口与命名约定** —— 卡 1 · W0a 第 ⑤ 步（依赖边 ②）。

归属：`docs/05 §3` 明确"测试入口与配置（`conftest.py` / pytest 配置一类）**不属于任何被测模块
⇒ 归架构与集成**"；其余测试文件按被测模块归属，**文件名与被测模块同名**
（`test_domain.py` ↔ `app/domain/`）。

本文件只做一件事：**把 `backend/` 放进 `sys.path`**，让 `from app… import …` 在
**任何工作目录**下都能成立 —— `docs/03 §8.4` 第 5 步的命令是
`python -m pytest -q backend/tests`（**仓库根**执行），而 CI 也是从仓库根跑的。

★ 历史：`backend/tests/test_domain.py` 里那段临时 `sys.path` 引导（模块二作者注明的"待卡1 的
conftest 就绪后删除"）在本文件落地后**已可删除**；删不删由该文件的归属域（后端与数据）决定，
本文件不代改别人的文件 —— 留着也不冲突（重复 insert 同一路径是无害的）。
"""

from __future__ import annotations

import sys
from pathlib import Path

#: `backend/` —— `app` 包的父目录。
BACKEND_DIR = Path(__file__).resolve().parents[1]

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
