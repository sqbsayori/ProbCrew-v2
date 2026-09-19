"""ProbCrew-v2 后端应用包。

包结构（docs/03 §2 目录骨架的唯一权威）：
    app.core     基础设施（配置 / 统一错误体 / 日志入口）
    app.domain   领域纯函数（归一化 / 判定 / 错因匹配）
    app.graph    编排（W0c）
    app.tools    工具（W0c）
    app.learning 学习数据（W2）
    app.api      HTTP 路由（W1–W4）
"""
