"""`app.learning` —— 学习留痕的存储层（W1 · 首发建表项的第二半）。

写作用域：`docs/05 §3` 把 `backend/app/learning/**` 判给**后端与数据**（域2）。
依赖方向（`docs/03 §3.1`）：本包**只允许** import `app.core` · `app.domain` · `app.learning`
—— **不得 import `app.identity`**（`learning/` 与 `identity/` 之间没有边；建表入口因此在 `core/`，
见 `app/core/db.py` 的模块说明）。

★ **本批（P1）只交"计数与删除"** —— 它们是 F16「删除后逐表读回 0 行」与 N19 恢复演练
要用的**读回面**。W2 的写入函数（写 attempt + 掌握度累加 + 提示流水）属判定链，
按 `docs/06 §14.3 卡 W2` 排在 W2，**不在本批**。
"""
from __future__ import annotations
