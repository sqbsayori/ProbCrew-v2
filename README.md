# ProbCrew v2 · 模块二实现说明（W0b · 卡2 —— 领域与基础设施）

> **本文档只记录模块二的修改内容。**
> 项目总览、文档地图、契约索引、门禁说明等原有 README 内容**已保存在 `main` 分支**的 `README.md`，不在本文重复。

| | |
|---|---|
| 分支 | `gy`（本项目一人一分支，个人分支名即 `gy`） |
| 提交 | `a32ec03` 代码与测试 · `412ba77` README 更新 · `db245dd` 分支指针修正 |
| 基点 | `main` @ `ac1a55c` |
| 规模 | 10 个新增文件 · 约 1000 行 |
| 测试 | `python -m pytest backend/tests` → **28 passed** |

---

## 一、模块二要解决的问题

模块二对应第一轮分工里的**工作包 W0b · 卡2**，职责是给整个后端打地基。它由两条**交付边**定义——这两条边是卡与卡之间的依赖契约，不是比喻：

```
   边① ──▶ core/ 最小公共形状（配置 / 错误体 / 日志入口）
              └──▶ 解锁「模块三 · 编排与工具形状」（它要 import core 才能开工）

   边② ──▶ tests/ 第一条真实用例（让 pytest 真正收集到用例）
              └──▶ 与「模块一 · 骨架与外壳」合起来，让 CI 的 pytest 步骤不再是 skipped
```

**为什么模块二先做**：`domain/`（判定领域逻辑）是整个产品的核心资产——判定链必须**确定性、零模型调用、< 1s**；而它又只依赖极小的公共设施（配置、错误体、日志）。把这块先冻结成纯函数，后面 API 层、编排层、前端都能围着它写测试，不会重演旧仓"**引擎有、通路无**"的问题（判定引擎 521 行、16 项测试通过，而全仓 21 个路由里没有一个批改端点）。

**架构硬约束**（写在 `docs/03` §3.1，本模块严格遵守）：

```
core/  ←──  domain/        domain 只允许依赖 core
  ↑                          不得 import：数据库 / FastAPI / LangGraph / 网络 / tools/ / learning/
  └──  api/ · graph/ · tools/ · learning/     （后续工作包）
```

一句话：**依赖方向单向朝内，domain 是一块能被单独拿出来算的纯逻辑。**

---

## 二、本次修改的文件清单

| 文件 | 步骤 | 作用一句话 |
|---|---|---|
| `backend/app/__init__.py` | ① | 包标记，声明包用途 |
| `backend/app/core/__init__.py` | ① | 包标记 + 公开入口（`Settings` / `load_settings` / `AppError` / `log_action`） |
| `backend/app/core/config.py` | ① | 配置读取：路径配置 + 日志轮转参数 |
| `backend/app/core/errors.py` | ① | 统一错误体构造，把契约纪律变成构造时校验 |
| `backend/app/core/logging.py` | ① | 审计日志唯一入口，只记 ID 与动作 |
| `backend/app/domain/__init__.py` | ② | 包标记 + 领域公开入口（`normalize` / `match_mistake` / `grade`） |
| `backend/app/domain/normalize.py` | ② | 答案归一化（去空白 / 统一符号 / 精度口径），全程线性 |
| `backend/app/domain/mistakes.py` | ② | 错因匹配 `match_mistake`，集合语义，四类规则注册表 |
| `backend/app/domain/judge.py` | ② | 判定确定性入口 `grade`，零 LLM |
| `backend/tests/test_domain.py` | ③ | 28 条用例，含性能回归与静态架构断言 |

---

## 三、本模块落地的规则索引

规则编号来自 `docs/02 §1.4 / §1.6`。本模块的工作就是把其中几条从"文档里的字"变成"代码里的约束"：

| 规则 | 含义 | 在本模块的落地位置 |
|---|---|---|
| **R-A** | 平坦正则（无嵌套量词）+ 输入长度上界，防灾难性回溯 | `normalize._trim_decimals` 手写线性扫描；`judge.ANSWER_MAX_LENGTH = 200`；`mistakes.MAX_MATCH_LENGTH = 400` |
| **R-M** | 凭据七面不得外泄（含**错误信息**面） | `errors.AppError` 构造时对 `detail` 键名做敏感模式匹配，命中即抛错 |
| **R-O** | 日志与审计**只记 ID 与动作**，不记内容 | `logging.log_action` 是唯一入口；非 `*_id` 字段当场抛错 |
| **F4** | 能算的不要问模型 —— 判定链**零 LLM 调用** | `judge.grade` 全程纯计算；测试里有静态断言扫描源码禁止 LLM/SDK 字样 |
| **F5** | 错因命中是**集合语义**；未识别须如实返回空，不得编造 | `mistakes.match_mistake` 返回命中 ID 列表；负样本用例是红灯 |
| **R-Q ①** | 允许集合非空且非通配（不得写"什么都算"的规则） | `mistakes.RULES` 是四条显式具名规则，无通配分支 |

---

## 四、逐文件详解

### 4.1 `backend/app/core/config.py` —— 配置读取

W0 最小集只含两个路径配置，刻意不做成完整配置中心：

| 字段 | 默认值 | 用途 |
|---|---|---|
| `log_dir` | `backend/data/logs/` | 审计日志落盘目录（`docs/02 §1.4` 规则 1 / ADR-0005 定死） |
| `content_dir` | `content/` | 内容资产目录（W2 判定链吃题库时使用） |
| `log_max_bytes` | `10485760`（10MB） | 日志轮转阈值 |
| `log_backup_count` | `5` | 保留份数 |

设计取舍：

- **环境变量前缀 `PROBCREW_`**（`PROBCREW_LOG_DIR` / `PROBCREW_CONTENT_DIR`），避免与其他进程的配置串味。
- **纯标准库**（`dataclasses` + `pathlib`），不引 `pydantic-settings`——依赖口径归模块一统一决定（`docs/03 §8.4` 三个待定问题），W0 不抢跑。
- `Settings` 是 `frozen=True` 的 dataclass：**进程级只读**，改配置只能重构，不能就地改。
- `load_settings(env=None)` **显式接收环境字典**，默认才读 `os.environ`——这样测试可以传假环境，不必 monkeypatch 全局状态（纯函数风格，无隐式全局读取）。
- **路径解析失败不在这里兜底**：目录由使用方按需创建（`logging.setup_logging` 建日志目录），保持单一职责。
- `_repo_root()` 从 `config.py` 上溯 3 级（`core → app → backend → 仓库根`），让默认路径与**仓库布局**绑定，而不是与"当前工作目录"绑定。

### 4.2 `backend/app/core/errors.py` —— 统一错误体

形状权威是 `contracts/error.schema.json`。本模块把它的三条纪律**前移到构造时**：

1. **`code` 只允许 16 个枚举值**（`ERROR_CODES`），与契约 `definitions.errorCode.enum` 逐项一致；口径是"只增不改"（机器读的稳定标识）。
2. **`message`** 是给人看的中文，1–200 字，构造时校验长度。
3. **`detail`** 的键名命中敏感模式（`key` / `token` / `secret` / `password` / `credential` / `authorization` / `api_key`）时**直接拒绝构造**——这是 R-M"凭据不得进错误信息"的机器可执行形式，对应契约里的 `propertyNames` not-pattern。

```python
AppError(code="model_key_invalid", message="模型密钥无效，请检查后重试")
# → .http_status == 400（由 HTTP_STATUS_BY_CODE 派生，调用方不必手写状态码）

AppError(code="internal_error", message="...", detail={"api_key": "sk-..."})
# → ValueError: detail 键名 'api_key' 命中敏感模式，凭据不得进错误信息（R-M）
```

两个公开入口：

- `AppError(...)` —— 异常载体。API 层 try/except 后取 `.http_status` 设响应码、`to_envelope()` 序列化成契约形状。
- `error_envelope(code, message, detail)` —— 不抛异常的场景（如通用 500 处理器）一次性造出响应体。

**为什么放在 core**：错误体是全仓唯一形状，任何一层都可能抛错，所以它属于"最小公共形状"。`HTTP_STATUS_BY_CODE` 与契约的 `x-http-status-by-code` 逐项一致，将来由 `scripts/verify.sh` 门禁对账（`docs/04 §7` 校验③）。

### 4.3 `backend/app/core/logging.py` —— 审计日志（只记 ID 与动作）

**R-O 的理由**：验收口径是"删除后逐表读回 0 行"，但它**只覆盖数据库**。内容一旦进日志，就会出现"**验收通过但数据仍在**"的洞——学生答案被写进日志文件，删除账号后那句话还留在磁盘上。

所以本模块不给"靠 review 发现"的机会：

- **唯一写日志口子** `log_action(action, **ids)`。除 `action` 外，键名**必须**以 `_id` 结尾，或在白名单 `{"status", "reason", "level"}` 内（这三个是短枚举/代码，不是内容）。
- 键名不合规 → 抛 `LogContentViolation`（`ValueError` 子类）。例如 `log_action("grade", question="...")` 直接失败。
- **值上界 128 字**：防止把内容塞进 `_id` 字段（正常 ID 最长 64，取 2× 裕量，超长同样抛错）。
- `action` 自身 1–64 字。
- **返回写入的那一行文本**，便于测试直接断言，不必去读日志文件。
- `setup_logging(log_dir, max_bytes, backup_count)`：写 `<log_dir>/audit.log`，`RotatingFileHandler` 10MB × 5，`utf-8`，**幂等**（重复调用不叠加 handler，避免进程内多处初始化导致重复吐日志）。
- `logger.propagate = False`：审计日志不向 root logger 冒泡，避免被别处的 handler 二次处理。

### 4.4 `backend/app/domain/normalize.py` —— 归一化

文档对归一化的规格只有九个字：「**去空白 / 统一符号 / 精度口径**」，外加契约实例给的锚点 `P(B|A) → p(b|a)`。本文件把这九个字实现为五步流水线：

| 步 | 做什么 | 例 |
|---|---|---|
| ① 全角→半角 | `unicodedata.normalize("NFKC", ...)` | `Ｐ（Ｂ｜Ａ）` → `P(B\|A)` |
| ② 统一符号 | `−`(U+2212)→`-`；`×`→`*`；`÷`→`/` | `5×3` → `5*3` |
| ③ 去空白 | 删除**全部**空白字符（含全角空格、制表、换行） | `"P(B \| A)"` → `"p(b\|a)"` |
| ④ 大小写归一 | `.lower()` | `P(B\|A)` → `p(b\|a)`（契约锚点） |
| ⑤ 精度口径 | 小数去尾零 | `0.50`→`0.5`；`1.00`→`1` |

**刻意不做的事**（避免发明没人约定过的口径）：

- **不解析 LaTeX、不展开分数约分**。`1/4` 与 `0.25` 的等价不在这里做，而是**判定入口用精确有理数比较**处理（见 `judge._value_equal`）。这样 `answer_normalized` 保持原文形状、**可复算**——归一化结果是能给人看的中间产物。
- **不做单位换算**。单位差异是错因 `mp_unit_error` 的**检测素材**，归一化阶段一旦抹掉，错因就永远匹配不出来。

**性能纪律（R-A）——本模块最硬的一处取舍**：

去小数尾零**刻意不用** `re.sub(r"\d+\.\d+", ...)`。原因不是风格偏好：这类模式在**无匹配的纯数字长串**上，正则引擎会在每个位置重启 `\d+` 的贪心扫描，退化为 **O(n²)**。实测 100k 位数字耗时 **31 秒**——正是旧仓"100k 位数字 P0 事故"的同族问题。

`_trim_decimals` 改为手写单遍扫描器：每个字符只访问一次（整数段 → 可选小数段 → 去尾零），**O(n)**，同样的 100k 位输入 **< 10ms**。

### 4.5 `backend/app/domain/mistakes.py` —— 错因匹配

**入口签名是全仓唯一写法**（`docs/01 §6` · `docs/06 §12.3` · `content.schema.json`）：

```python
domain.match_mistake(normalize(answer), item)
```

★ **归一化必须在入口内**（`docs/06 §12.8`）——否则测试测的是"现实中不存在的路径"（调用方已归一化过的输入）。因此 `match_mistake` **接收学生原文**，第一步自己调 `normalize`。

**F5 命中定义（集合语义）**：实际命中集合 ⊇ 期望集合，且不含未期望条目。**未识别 ⇒ 如实返回 `[]`**，绝不编造；负样本用例就是红灯。返回值是错因 ID 列表。

覆盖 `content.schema.json` 注入集里的四个 category（显式注册表 `RULES`，顺序即取单值时的优先序）：

| 错因 ID | 教学含义 | 判定规则 | 例 |
|---|---|---|---|
| `mp_direction_reversed` | 条件概率**方向颠倒** | 答案里的 `p(x\|y)` 把两侧互换后恰好等于期望 | expected `p(a\|b)`，answer `p(b\|a)` |
| `mp_unit_error` | 数值对但**单位/尺度错** | 两边各恰有一个数，数值（精确有理数）相等，但**非数字余部不同** | expected `5米`，answer `5厘米`；expected `0.5`，answer `50%` |
| `mp_missing_partition` | **全概率漏完备组** | expected 是 `+` 连接的多项式，答案为**真子集** | expected `a+b`，answer `a` |
| `mp_independence_confusion` | **独立重复 n 乘错** | 答案 = 期望 × n 或 ÷ n（n≥2 整数），**且题干含独立重复语境** | 题干含"独立/抛掷/有放回"，expected `0.5`，answer `0.0625` |

**保守优先的边界**：规则只做 `expected` 归一化结果与答案归一化结果之间的**数值/形状比较**（`independence_confusion` 额外要求题干语境），**不做任何语义推断**；一条规则判不出来就返回 `False`，由外层如实聚合。宁可漏报，不可编造。

**长度护栏**：契约注入集的 `answer` 上界是 200 字（`answer_injection.maxLength`），本模块取 2× 裕量设 `MAX_MATCH_LENGTH = 400`，超长直接返回"未识别"。这不只是防御性写法——实测发现超长纯数字串会让 `Fraction` 的约分（大整数欧几里得算法）进入平方级开销，单次匹配可拖到分钟级。超长输入本就不在任何现实路径内（判定入口的 R-A 上界先会拦），如实返回空既正确、又避开昂贵运算。

### 4.6 `backend/app/domain/judge.py` —— 判定确定性入口

`grade(answer, item) -> dict` 是判定链的 domain 段（`docs/02 §4.3` ①–④）：

```
① 参数校验（answer ≤ 200 字，R-A）
② 读 gradable —— false ⇒ 显式降级为「需人工判定」
③ 归一化（★ 在入口内）
④ 判定（确定性，零 LLM）—— 不等价时接错因匹配（F5）
```

**输出形状对齐 `contracts/attempt.schema.json` 的 `gradeResult`（oneOf 两支）**，但**不含服务端派生字段** `hint_used` / `attempt_no`——它们由服务端按流水计数派生（`docs/02 §4.3-⑥`），domain 层无权知道：

```python
# 可判定支
{"graded": True, "correct": True,  "score": 1.0, "mistake_id": None, "mistake_matched": False}
{"graded": True, "correct": False, "score": 0.0, "mistake_id": "mp_direction_reversed", "mistake_matched": True}

# 不可判定支（与契约逐字一致）
{"graded": False, "reason": "not_gradable", "message": "该题需人工判定",
 "correct": None, "grader": "not_gradable", "solution": None}
```

**等价判定按 `answer_kind` 分流**（题库资产的唯一分类轴）：

- `value`（数值 / 闭式）：先用 **`Fraction` 精确有理数**比较——`0.5 ≡ 50% ≡ 1/2`，没有浮点误差；数值不可解析时退化为形状相等。精确有理数是必须的：判定要给分，浮点误差会造出"差一点算对"的争议。
- `text`（含单位 / 文字结论）：归一化后**形状相等**才算对。单位差异**故意不在这里通融**——它交给错因匹配去解释（答 `5厘米` 判错并命中 `mp_unit_error`，这是教学上有价值的反馈）。
- 其它取值 → 抛 `invalid_argument`（资产错误，应在加载期拦截）。

**资产错误显式失败**：`item` 缺 `gradable`、或可判定条目缺 `expected` / `answer_kind`，都抛 `AppError("invalid_argument", ...)` 并指明字段名——不静默按"判错"处理。

**两个口径要特别记住**：

1. **`mistake_id` 落库是单值**（`docs/02 §3.2` `attempt.mistake_id`），而错因匹配是**集合语义**。本入口按 `RULES` 的显式顺序取**第一个命中**落库；完整命中集合只存在于"响应之外"的评测运行器复算里。
2. **`mistake_matched`** 是"如实未识别"的机器可读形式：`correct=false` 且 `mistake_matched=false` 表示"判错，但现有规则认不出是什么错因"——这是诚实的状态，不是失败。

**调用方式约束**：`grade` 是**同步 CPU 工作**，上层（API / graph）必须丢线程池执行（`to_thread`，`docs/03 §3.2`），否则会阻塞单 worker 的事件循环。

纯函数 + 纯计算：无 LLM、无网络、无落盘。文件顶部 docstring 已声明，测试里有静态断言守卫。

### 4.7 `backend/tests/test_domain.py` —— 首条真实用例

28 条用例，按意图分组：

| 组 | 覆盖 |
|---|---|
| 归一化 | 各步规则单独可复算；契约锚点 `P(B\|A)→p(b\|a)`；空串进空串出 |
| 正确路径 | `value` 型数值等价（`0.5` ≡ `50%` ≡ `1/2`）；`text` 型形状相等 |
| 降级 | `gradable=false` → 不可判定支逐字段比对 |
| 参数校验 | 超 200 字抛 `invalid_argument`；缺 `gradable` / `expected` 的资产错误 |
| 错因正样本 | 四类错因各一条命中用例 |
| 错因负样本 | 相似但不该命中的输入 → 必须返回 `[]`（F5 的"不得编造"） |
| 性能回归 | 100k 位数字串归一化 < 1s（实际 < 10ms）—— 防 O(n²) 回潮 |
| 静态架构断言 | domain 源码 import 白名单（不得出现数据库 / FastAPI / LangGraph / 网络）；源码零 LLM 引用 |

**最后两组是"结构性用例"**：它们不测某个函数的行为，而测**架构约束是否被破坏**。这类断言把 `docs/03 §3.1` 的依赖方向规则变成 CI 可执行的形式——比人眼 review 可靠。

> 说明：测试文件目前自带临时 `sys.path` 引导。conftest / pytest 入口的规范化归属模块一（卡1），卡1 就绪后可删除这段引导。

---

## 五、怎么跑

```bash
# 仓库根目录执行；纯标准库实现，无第三方依赖（pytest 除外）
python -m pytest backend/tests

# 实测输出
# ............................                                             [100%]
# 28 passed in 0.22s
```

本机验证用的隔离环境（Python 3.13）：

```bash
"C:/Users/zedbj/.workbuddy/binaries/python/envs/default/Scripts/python.exe" -m pytest -q backend/tests
```

---

## 六、实现过程中发现并修掉的两个真实缺陷

两个都是**测试先发现的**，不是 review 发现的——这是"先写可复算用例"的收益。

### 缺陷一：归一化在长数字串上退化为 O(n²)（31 秒 → < 10ms）

- **症状**：`test_grade_long_digit_string_fast` 单条用例跑了 67 秒。
- **定位过程**：先怀疑错因规则的 `Fraction` 运算，加了入口长度护栏后仍是 71 秒 → 说明瓶颈在护栏之前。逐段计时 NFKC / translate / join / lower / `re.sub`，锁定 `re.sub(r"\d+\.\d+", ...)` 独占 31 秒。
- **根因**：**无匹配**的长数字串让正则引擎在每个起点重启 `\d+` 的贪心扫描（一直扫到串尾才发现后面没有 `.`），总代价 O(n²)。
- **修复**：`_trim_decimals` 改为手写单遍线性扫描器，`normalize` 从此**完全不含正则**。
- **残留风险处理**：`mistakes._as_fraction` 处理超长串时同样有大整数 `Fraction` 的平方级开销，故在 `match_mistake` 入口加 400 字护栏。
- **回归保护**：保留性能用例，锁死这条路径不许再退化。

### 缺陷二：两处测试自身的错误假设（实现无 bug）

- ReDoS 用例最初直接打 `grade()`，但 `grade` 的 200 字上界**先**拦住了长串——**这恰恰是 R-A 的正确行为**。改为针对"归一化 / 匹配层"，即长度护栏真正生效的位置。
- "零 LLM 引用"静态断言最初误扫了 `judge.py` 的 docstring（正文里自然提到"无 LLM 调用"等字样）。改为只检查代码正文里的库名 / 模块名。

---

## 七、边界：本模块**没有**做的事

按 `docs/06 §12.3` 卡2 的写作用域，以下都在本卡范围之外，本次**一行未动**：

- 不建表、不写数据库迁移（数据层属其它工作包）
- 不写 `api/` 业务路由、不写 `main.py`、不写 `graph/` / `tools/` / `learning/`
- 不碰 `docs/` 与其它能力域的文件
- 不引入任何第三方运行期依赖（含 pydantic 与各类框架）

---

## 八、遗留待办

| # | 事项 | 归属 |
|---|---|---|
| 1 | **PR：`gy` → `main`**（合并前门禁须绿） | 本卡收口 |
| 2 | **回写 `docs/02 §4.3`**：normalize 的分数 / 百分数 / 单位规则目前是"实现定值"（W0 最小集），按 `docs/06 §12.5` 检查点 5 需回写文档 | 后端与数据域 |
| 3 | `content/mistakes.json`（≥ 40 条，模块四资产）在 W2 接入后，用注入集**校准扩充** `mistakes.RULES`；本模块只提供最小可测机制，不预设题库内容 | 模块四 + 本域 |
| 4 | 测试文件中的临时 `sys.path` 引导，待卡1 的 conftest / pytest 入口就绪后删除 | 卡1 |

---

## 附：一页速查

| 我想知道… | 看这里 |
|---|---|
| 错误码有哪些、各自什么 HTTP 状态 | `backend/app/core/errors.py` 的 `ERROR_CODES` / `HTTP_STATUS_BY_CODE` |
| 为什么这句日志写不进去 | `backend/app/core/logging.py` 的 `ALLOWED_NON_ID_FIELDS` + 字段名规则 |
| 为什么"0.50 和 0.5 算同一个答案" | `normalize.py` 第 ⑤ 步 `_trim_decimals` |
| 为什么"1/4 和 0.25 算同一个答案" | `judge.py` 的 `_value_equal`（`Fraction` 精确比较），**不是** normalize |
| 判错却没给出错因是什么意思 | `judge.grade` 的 `mistake_matched=false`（如实未识别） |
| 错因规则在哪、怎么加一条 | `mistakes.py` 的 `RULES` 注册表（显式、无通配） |
| 性能和长输入的防线在哪 | `judge.ANSWER_MAX_LENGTH`(200) · `mistakes.MAX_MATCH_LENGTH`(400) · `normalize` 手写扫描器 |
