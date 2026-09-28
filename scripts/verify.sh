#!/usr/bin/env bash
# scripts/verify.sh —— 本地门禁（v0）· 卡 1 · W0a 第 ⑥ 步
#
# 本地门禁**就是** CI 入口：`.github/workflows/ci.yml` 的实质步骤之一直接调它
# （`docs/03 §8.2` 的"唯一门禁入口"）。用法：
#
#     bash scripts/verify.sh          # 在仓库根执行；全过退出码 0，任一失败非零
#
# ★ 范围是**写死的**，分两批（`docs/06 §12.3` 卡 1 · `docs/06 §12.7` 第三行 · `§11.2` 的批次 3）：
#     **v0（批次 1 · W0a）**：R-P1 / R-P2 / R-P3（文档自洽） + `docs/04 §7` 的契约校验 ①–⑤
#     **v1（批次 3 · W5 的第一批，2026-09-27 接上）**：
#       · `docs/04 §7` 校验⑦（部署无关化 + 零依赖）· ⑥ 登记为"对象未产出"（见下）
#       · R-A（字符串上界 · 禁嵌套量词）· R-B（写库显式 commit）· R-D（禁硬编码色值）
#       · R-E（禁 innerHTML 赋值）· R-H（演示类 ≤ 产品页）· R-K（唯一出网口）
#       · R-Q ①②（允许集合不得为 None · 成功标记不得硬编码）· R-J/R-O（日志写入点收敛）
#       · 依赖方向（`docs/03 §3.1`，前端一并扫）· `api/` 薄（`docs/03 §3.2` 第 1 条）
#       · R-F③ 对比度（**只对 `tokens.css` 里声明的色对**，N8 的载体）
#   ★ **仍不在本脚本**（`docs/06 §8` 的"落在哪"列就是这么写的）：R-C（人）· R-M⑥（review）·
#     R-L / R-N（**只有用例**）· R-F①② / R-G / R-I（**前端冒烟**，要真跑页面）；
#     运维演练与 `backup.py` / `load_test.py` / 启动校验七条属批次 3 的其余部分。
#
# ★ **三态输出（v1 新增的制度）**：`[PASS]` / `[FAIL]` / **`[SKIP]`**。
#   `[SKIP]` = **对象还没产出**（原因逐条打印，且**不计入通过数**，也不当成失败）。
#   ★ 为什么必须有这一态：**"对象不存在"与"检查通过"在输出里必须长得不一样** ——
#   `docs/06 §14.4` 的"别把门禁绿读成本轮的结构性纪律已合规"说的正是这件事；
#   若用"干脆不写这条检查"来实现，门禁就会看起来比实际更强（README 与 `docs/05 §7.2`
#   记过的那三次实测都是这个形态）。
#
# ★ 为什么全部**内联**在这一个文件里（不拆成 `scripts/*.py`）：
#   `docs/03 §8.2` 写明"新增脚本 = 先改本清单 + 加 `docs/05 §3` 一行"，两处表都要动；
#   而本卡只需要一条检查链 —— 内联可以避免"两张表都不承认的路径"（该节 2026-09-17 的补记）。
#
# ★ 本脚本**只读**：不写任何文件、不联网、不改仓库（离线可用，`docs/02 §1.5③` 的"恰好两个出网口"与之无关）。

set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT" || exit 1

if command -v python >/dev/null 2>&1; then
  PYTHON=python
elif command -v python3 >/dev/null 2>&1; then
  PYTHON=python3
else
  echo "[FAIL] 找不到 python / python3 —— 门禁需要它来做静态检查与契约校验。" >&2
  exit 1
fi

"$PYTHON" - "$ROOT" <<'PYTHON_VERIFY'
# -*- coding: utf-8 -*-
"""门禁 v0 的检查体（由 scripts/verify.sh 以 heredoc 内联执行）。

三类检查 + 五节契约校验，逐项打印 PASS/FAIL；任一失败 → 退出码 1。
"""

from __future__ import annotations

import glob
import hashlib
import json
import os
import re
import sys
from urllib.parse import urljoin
from collections import Counter

ROOT = sys.argv[1]
os.chdir(ROOT)

RESULTS: list[tuple[str, "bool | None", str]] = []


# --------------------------------------------------------------------------- #
# 通用工具
# --------------------------------------------------------------------------- #

def read(path: str) -> str:
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def section(text: str, start: str) -> str:
    """从 `start` 起到下一个 1–3 级标题为止（含首行）。"""
    index = text.index(start)
    match = re.search(r"^#{1,3} ", text[index + len(start):], re.M)
    end = index + len(start) + (match.start() if match else len(text))
    return text[index:end]


def table_rows(block: str) -> list[list[str]]:
    """取 markdown 表格行（含表头，去掉 `|---|` 分隔行）。"""
    out: list[list[str]] = []
    for line in block.splitlines():
        if not line.startswith("|"):
            continue
        if re.fullmatch(r"\|[\s:\-|]+\|", line):
            continue
        out.append([cell.strip() for cell in line.strip().strip("|").split("|")])
    return out


def has_section(path: str, number: str) -> bool:
    """该文件里有没有 `N.M` 小节（标题形如 `### 8.4 …` / `## 3.2 …`）。"""
    return re.search(r"^#{2,4}\s*" + re.escape(number) + r"[\.\s、]", read(path), re.M) is not None


def registered_target(adr_number: str, target_section: str):
    """在 `docs/07 §2.3` 的**登记表**里查"这个 ADR 的这个裸引用"的真目标。

    该表是仓库自己的做法：左边写"哪个 ADR 的正文第几行是裸 `§N`"，右边写它的真目标是哪一篇的哪一节
    （例如 `0005` 第 17 行的裸 `§6.3` ⇒ `docs/02 §6.3`）。命中了就按登记的目标判。
    """
    for row in DOC_MAP.splitlines():
        if adr_number in row and f"`§{target_section}`" in row:
            found = re.search(r"docs/(\d\d)\s*§\s*([\d.]+)", row)
            if found:
                return DOC_NUMBERED.get(found.group(1)), found.group(2)
    return None, None


class Skipped(Exception):
    """**对象未产出** —— 如实登记为未覆盖（不计入通过，也不是失败）。

    只允许在"要查的东西还不存在"时抛出，且**必须写清原因与它在 `docs/07 §2.2` 里的登记**。
    把"没检查"与"检查通过"分开，是本脚本 v1 唯一的制度变化（文件头的三态说明）。
    """


def check(name: str, fn) -> None:
    try:
        detail = fn() or ""
        RESULTS.append((name, True, detail))
    except Skipped as exc:
        RESULTS.append((name, None, str(exc)))  # None = 未覆盖（既不是 PASS，也不是 FAIL）
    except AssertionError as exc:
        RESULTS.append((name, False, str(exc)))
    except Exception as exc:  # 检查体自身出错的形态要看得见，不许当成"通过"
        RESULTS.append((name, False, f"{type(exc).__name__}: {exc}"))


DOC_NUMBERED = {os.path.basename(p)[:2]: p for p in sorted(glob.glob("docs/0*.md"))}
ADR_FILES = {os.path.basename(p)[:4]: p for p in sorted(glob.glob("docs/adr/*.md"))}
SCAN_FILES = ["README.md"] + sorted(glob.glob("docs/*.md")) + sorted(glob.glob("docs/adr/*.md"))
DOC_MAP = read("docs/07-文档地图.md")


# --------------------------------------------------------------------------- #
# R-P1 · 计数由表体导出（docs/06 §3 第 1 项）
# --------------------------------------------------------------------------- #

def rp1_requirements_matrix() -> str:
    """`docs/01 §1.1`：行数、S1–S4 计数、功能/非功能拆分，全部与正文自述比对。"""
    doc = read("docs/01-需求分析.md")
    block = section(doc, "### 1.1")
    data: list[tuple[list[str], str]] = []
    for row in table_rows(block)[1:]:
        # N3 行是**废弃标记**（写作 `~~N3 …~~`）：计入行数、不计入服务角色统计 ⇒ 先去掉删除线再取 ID。
        first = row[0].replace("~~", "")
        match_id = re.match(r"^\**\s*([FN]\d+)", first)
        if match_id:
            data.append((row, match_id.group(1)))
    ids = [rid for _, rid in data]

    match = re.search(r"汇总（(\d+) 条", block)
    assert match, "§1.1 正文里找不到「汇总（N 条…」"
    expected_rows = int(match.group(1))
    assert len(data) == expected_rows, f"表体 {len(data)} 行 ≠ 正文自述 {expected_rows} 行"

    counter = Counter()
    for row, rid in data:
        if rid == "N3":  # N3 行保留为废弃标记：计入行数，不计入服务角色统计（§1.1 表下说明）
            continue
        for role in re.findall(r"S[1-4]", row[1]):
            counter[role] += 1
    for role in ("S1", "S2", "S3", "S4"):
        stated = re.search(rf"\*\*{role} = (\d+) 条\*\*", block)
        assert stated, f"§1.1 正文里找不到 {role} 的汇总数字"
        assert counter[role] == int(stated.group(1)), (
            f"{role} 表体算出 {counter[role]} ≠ 正文自述 {stated.group(1)}"
        )

    functions = sum(1 for rid in ids if rid.startswith("F"))
    non_functions = sum(1 for rid in ids if rid.startswith("N"))
    split = re.search(r"(\d+) 功能 \+ \*\*(\d+) 非功能\*\* = \*\*(\d+) 行\*\*", block)
    assert split, "§1.1 正文里找不到「X 功能 + Y 非功能 = Z 行」"
    assert (functions, non_functions, functions + non_functions) == (
        int(split.group(1)), int(split.group(2)), int(split.group(3))
    ), (f"拆分算出 {functions} 功能 + {non_functions} 非功能 = {functions + non_functions} 行 ≠ "
        f"正文自述 {split.group(1)} + {split.group(2)} = {split.group(3)}")
    return (f"{len(data)} 行 · S1={counter['S1']} S2={counter['S2']} S3={counter['S3']} S4={counter['S4']}"
            f" · {functions} 功能 + {non_functions} 非功能")


def rp1_must_checklist() -> str:
    """`docs/01 附录 B.1`：分类行的 ID 个数 == 该行写的条数，且合计 == 标题的条数。"""
    doc = read("docs/01-需求分析.md")
    block = section(doc, "### B.1")
    heading = block.splitlines()[0]
    total = int(re.search(r"（(\d+) 条）", heading).group(1))
    counted = 0
    seen = 0
    for row in table_rows(block)[1:]:
        if row[0] not in ("功能", "非功能"):
            continue
        ids = re.findall(r"[FN]\d+", row[1])
        stated = int(row[2])
        assert len(ids) == stated, f"{row[0]} 行列了 {len(ids)} 个 ID，但第 3 列写的是 {stated}"
        counted += stated
        seen += len(ids)
    assert counted == total, f"分类条数相加 {counted} ≠ 标题自述 {total} 条"
    assert seen == total, f"清单里实际列出 {seen} 个 ID ≠ {total}"
    return f"{total} 条（分类合计与实际列出的 ID 数一致）"


def rp1_must_landing_count() -> str:
    """`docs/02 §10`：唯一 MUST 需求数 == 该节自述的条数。"""
    doc = read("docs/02-系统设计.md")
    block = section(doc, "## 10.")
    stated = int(re.search(r"\*\*(\d+) 条 MUST 全部有落点", block).group(1))
    found: set[str] = set()
    for row in table_rows(block)[1:]:
        if len(row) < 4:
            continue
        for index in (0, 2):
            cell = re.sub(r"~~.*?~~", "", row[index])  # 剔除已废弃的 N3 标记
            found.update(re.findall(r"\b([FN]\d+)\b", cell))
    assert len(found) == stated, f"§10 表体里唯一 MUST 有 {len(found)} 个 ≠ 正文自述 {stated} 条"
    return f"{stated} 条唯一 MUST"


# --------------------------------------------------------------------------- #
# R-P2 · 相对引用解析（docs/06 §3 第 2 项；扫描范围 = README + docs/** + docs/adr/**）
# --------------------------------------------------------------------------- #

REF_PATTERN = re.compile(r"(?:(docs/(\d\d)[^\s`)]*)|(ADR-(\d{4})))?\s*§\s*(\d+(?:\.\d+)*)")
LINK_PATTERN = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")


def rp2_references() -> str:
    dangling: list[str] = []
    exempted: list[str] = []
    notation_examples = 0
    registered: list[str] = []
    total = 0
    for path in SCAN_FILES:
        body = read(path)
        for lineno, line in enumerate(body.splitlines(), 1):
            for match in REF_PATTERN.finditer(line):
                prefix, doc_number, adr_prefix, adr_number, target_section = match.groups()
                total += 1
                if adr_number:
                    target = ADR_FILES.get(adr_number)
                    if target is None or not has_section(target, target_section):
                        dangling.append(f"{path}:{lineno} ADR-{adr_number} §{target_section}")
                elif doc_number:
                    target = DOC_NUMBERED.get(doc_number)
                    if target is None:
                        # 已知豁免：指向旧仓的引用（docs/07 的对照表里登记为"已豁免"）
                        if "旧仓" in line:
                            exempted.append(f"{path}:{lineno} docs/{doc_number} §{target_section}")
                        else:
                            dangling.append(f"{path}:{lineno} docs/{doc_number} §{target_section}")
                    elif not has_section(target, target_section):
                        dangling.append(f"{path}:{lineno} docs/{doc_number} §{target_section}")
                elif "§N-K" in line:
                    # ★ 已登记的豁免：`docs/02 §1.6` 里讲 R-P2 写法的**记号定义行**，
                    # 句中的 `§8-13g` · `§4.2-4` · `§9.3-1` · `§11.1-3` 是**写法示例**，不是引用。
                    notation_examples += 1
                else:
                    # 裸 `§N.M` 的归属，按人读文档的方式逐个试（docs/06 §3 第 2 项说"裸的一律按本篇判"，
                    # 而实际写法还有两种：同句里"最近的那个 `docs/0X`"（含链接写法 `](03-架构总览.md)`），
                    # 以及 `docs/07 §2.3` 那张**登记表**里"左边写裸引用、右边写它的真目标"的行）。
                    before = list(reversed(re.findall(r"(?:docs/|\]\()(\d\d)", line[: match.start()])))
                    after = re.findall(r"(?:docs/|\]\()(\d\d)", line[match.start():])
                    candidates: list[str] = []
                    old_repo = False
                    for number in before + after:
                        target = DOC_NUMBERED.get(number)
                        if target is None:
                            old_repo = old_repo or ("旧仓" in line)
                        elif target not in candidates:
                            candidates.append(target)
                    candidates.append(path)  # 最后才按"本篇"判
                    if not any(has_section(candidate, target_section) for candidate in candidates):
                        if old_repo:
                            exempted.append(f"{path}:{lineno} §{target_section}")
                        elif path.startswith("docs/adr/"):
                            # ADR 里的裸引用：按 `docs/07 §2.3` 登记表里写明的真目标解析
                            target, target_number = registered_target(os.path.basename(path)[:4], target_section)
                            if target is not None and has_section(target, target_number):
                                registered.append(f"{path}:{lineno} §{target_section} → docs/{target_number}")
                            else:
                                dangling.append(f"{path}:{lineno} §{target_section}（ADR 内未解析且登记表里没有目标）")
                        else:
                            where = "、".join(os.path.basename(c) for c in candidates)
                            dangling.append(f"{path}:{lineno} §{target_section}（本篇与 {where} 都没有）")

            for target in LINK_PATTERN.findall(line):
                if target.startswith(("http://", "https://", "mailto:", "#")):
                    continue
                cleaned = target.split("#")[0]
                if not cleaned:
                    continue
                resolved = os.path.normpath(os.path.join(os.path.dirname(path) or ".", cleaned))
                if not os.path.exists(resolved) and os.path.basename(cleaned) not in DOC_MAP:
                    dangling.append(f"{path}:{lineno} → {target}（未产出且未在 docs/07 登记）")

    assert not dangling, "悬空引用：\n      " + "\n      ".join(dangling[:8])
    notes = []
    if exempted:
        notes.append(f"豁免旧仓引用 {len(exempted)} 处")
    if registered:
        notes.append(f"按 docs/07 §2.3 登记表解析 {len(registered)} 处（ADR 裸引用）")
    if notation_examples:
        notes.append(f"跳过记号示例 {notation_examples} 处（docs/02 §1.6 的写法定义行）")
    suffix = f"（{'；'.join(notes)}）" if notes else ""
    return f"{total} 处 § 引用 + 相对链接全部命中{suffix}"


# --------------------------------------------------------------------------- #
# R-P3 · 落点反向闭合（docs/06 §3 第 3 项；含 N15 那条历史坑）
# --------------------------------------------------------------------------- #

def rp3_landing_closure() -> str:
    coverage_text = read("docs/04-契约层说明.md")  # 端点 path 的权威登记处（docs/04 §3 的覆盖表）
    problems: list[str] = []
    rows = table_rows(section(read("docs/02-系统设计.md"), "## 10."))[1:]
    for row in rows:
        if len(row) < 4:
            continue
        for index in (1, 3):
            cell = row[index]
            refs = re.findall(r"docs/(\d\d)[^\s`)]*?\s*§\s*(\d+(?:\.\d+)*)", cell)
            for number, target_section in refs:
                target = DOC_NUMBERED.get(number)
                if target is None or not has_section(target, target_section):
                    problems.append(f"落点指向的 docs/{number} §{target_section} 不存在")
            # 端点 path ⇒ 必须能在 contracts/ 里找到
            for token in re.findall(r"`([^`]+)`", cell):
                endpoint = re.fullmatch(r"[A-Z]{3,7}(?:/[A-Z]{3,7})*\s+(/api/\S+)", token)
                if endpoint and endpoint.group(1) not in coverage_text:
                    problems.append(f"落点里的 {endpoint.group(1)} 不在 docs/04 §3 的覆盖表里")
            # 关键词闭合：这一格引了 §N.M，则至少一个反引号标识符要出现在被指小节里
            if refs:
                target = DOC_NUMBERED.get(refs[0][0])
                if target:
                    target_text = read(target)
                    identifiers = [t for t in re.findall(r"`([^`]+)`", cell)
                                   if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]*", t)]
                    if identifiers and not any(t in target_text for t in identifiers):
                        problems.append(f"落点关键词 {identifiers} 在被指小节 docs/{refs[0][0]} §{refs[0][1]} 里一个都找不到")
    assert not problems, "；".join(sorted(set(problems))[:5])
    return f"{len(rows)} 行的落点逐格闭合"


# --------------------------------------------------------------------------- #
# 契约校验 ①–⑤（docs/04 §7）
# --------------------------------------------------------------------------- #

def load_schemas() -> dict[str, dict]:
    schemas: dict[str, dict] = {}
    for path in sorted(glob.glob("contracts/*.schema.json")):
        document = json.loads(read(path))
        schemas[document["$id"]] = document
    return schemas


def resolve_pointer(document, pointer: str):
    node = document
    for raw in pointer.strip("/").split("/"):
        token = raw.replace("~1", "/").replace("~0", "~")
        node = node[int(token)] if isinstance(node, list) else node[token]
    return node


def collect_refs(node, acc: list[str]) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "$ref" and isinstance(value, str):
                acc.append(value)
            else:
                collect_refs(value, acc)
    elif isinstance(node, list):
        for item in node:
            collect_refs(item, acc)


def check_01_refs(schemas: dict[str, dict]) -> str:
    """① schema 自洽：JSON 合法 + 所有 `$ref` 能解析（**离线**，按 `$id` 自建 registry）。"""
    broken: list[str] = []
    count = 0
    for schema_id, document in schemas.items():
        refs: list[str] = []
        collect_refs(document, refs)
        for ref in refs:
            count += 1
            url, _, fragment = ref.partition("#")
            resolved = urljoin(schema_id, url) if url else schema_id
            target = schemas.get(resolved)
            if target is None:
                broken.append(f"{schema_id} → {ref}（目标 schema 未登记）")
                continue
            if fragment:
                try:
                    resolve_pointer(target, fragment)
                except Exception as exc:
                    broken.append(f"{schema_id} → {ref}（{type(exc).__name__}: {exc}）")
    assert not broken, "；".join(broken[:5])
    return f"{len(schemas)} 份 schema · {count} 处 $ref 全部解析（离线 registry）"


def check_02_examples(schemas: dict[str, dict]) -> str:
    """② `examples` 自校验：每份 schema 的顶层 examples 必须通过它自己。"""
    try:
        from jsonschema import Draft7Validator
        from referencing import Registry, Resource
        from referencing.jsonschema import DRAFT7
    except ImportError as exc:  # jsonschema 属 requirements-dev.txt
        raise AssertionError(f"缺少 jsonschema / referencing 依赖（requirements-dev.txt）：{exc}")

    registry = Registry().with_resources(
        [(sid, Resource.from_contents(doc, default_specification=DRAFT7)) for sid, doc in schemas.items()]
    )
    failures: list[str] = []
    validated = 0
    for schema_id, document in schemas.items():
        validator = Draft7Validator(document, registry=registry)
        for index, example in enumerate(document.get("examples", [])):
            validated += 1
            errors = sorted(validator.iter_errors(example), key=lambda error: list(error.path))
            if errors:
                first = errors[0]
                location = "/".join(str(part) for part in first.path) or "(根)"
                failures.append(f"{schema_id} examples[{index}] @{location}: {first.message}")
    assert not failures, "；".join(failures[:5])
    return f"{validated} 条 examples 全部自校验通过"


def check_03_error_codes() -> str:
    """③ `code` 枚举 == `x-http-status-by-code` == `docs/02 §6.3` 表体（状态码逐项一致）。"""
    document = json.loads(read("contracts/error.schema.json"))
    enum = set(document["definitions"]["errorCode"]["enum"])
    mapping = {key: value for key, value in document["x-http-status-by-code"].items() if isinstance(value, int)}

    table: dict[str, int] = {}
    for row in table_rows(section(read("docs/02-系统设计.md"), "### 6.3"))[1:]:
        if len(row) < 2:
            continue
        status = re.search(r"(\d{3})", row[0])
        code = re.search(r"`([a-z_]+)`", row[1])  # 可能是 **`code`**（加粗，如 must_change_pw）
        if status and code:
            table[code.group(1)] = int(status.group(1))

    assert enum == set(mapping), f"枚举与 x-http-status-by-code 不一致：{sorted(enum ^ set(mapping))}"
    assert enum == set(table), f"枚举与 docs/02 §6.3 表体不一致：{sorted(enum ^ set(table))}"
    mismatch = [code for code in sorted(enum) if mapping[code] != table[code]]
    assert not mismatch, f"HTTP 状态码不一致：{[(c, mapping[c], table[c]) for c in mismatch]}"
    return f"{len(enum)} 个 code 三处逐项一致"


def check_04_paths() -> str:
    """④ 25 条 path 覆盖完整：三处的**唯一 path 集合**两两相等且各为 25。"""
    def paths_of(block: str) -> set[str]:
        found: set[str] = set()
        for row in table_rows(block):
            for cell in row:
                for token in re.findall(r"[A-Z]{3,7}(?:/[A-Z]{3,7})*\s+(/api/[A-Za-z0-9_{}/\-]+)", cell):
                    found.add(token)
        return found

    sources = {
        "docs/02 §6.1": paths_of(section(read("docs/02-系统设计.md"), "### 6.1")),
        "docs/03 §6.1": paths_of(section(read("docs/03-架构总览.md"), "### 6.1")),
        "docs/04 §3": paths_of(section(read("docs/04-契约层说明.md"), "## 3.")),
    }
    for name, found in sources.items():
        assert len(found) == 25, f"{name} 抽出 {len(found)} 条唯一 path ≠ 25"
    names = list(sources)
    for index in range(1, len(names)):
        left, right = sources[names[0]], sources[names[index]]
        assert left == right, f"{names[0]} 与 {names[index]} 差集：{sorted(left ^ right)}"
    return "三处各 25 条唯一 path，双向 diff 为空"


def check_05_events() -> str:
    """⑤ 事件枚举与实现一致：枚举个数 == `x-type-count.value`。"""
    document = json.loads(read("contracts/events.schema.json"))
    enum = document["properties"]["type"]["enum"]
    stated = document["x-type-count"]["value"]
    assert len(enum) == stated, f"事件枚举 {len(enum)} 类 ≠ x-type-count 的 {stated}"
    assert len(set(enum)) == len(enum), "事件枚举里有重复项"
    return f"{stated} 类事件（枚举与 x-type-count 一致）"


# --------------------------------------------------------------------------- #
# 通用扫描工具（v1）
# --------------------------------------------------------------------------- #

BACKEND = "backend/app"

#: 嵌套量词：组里已经有量词，组外又跟一个量词（`(a+)+` 形态的灾难性回溯）。
NESTED_QUANTIFIER = re.compile(r"\([^()]*[+*}][^()]*\)\s*(?:[+*]|\{\d*,?\d*\})")

#: R-K 的出网模块名（顶层模块名）。
NETWORK_MODULES = ("httpx", "requests", "urllib", "socket", "aiohttp", "urllib3", "http")


def walk_files(root: str, suffixes: tuple[str, ...]) -> list[str]:
    """递归取文件（跳过 `__pycache__`），**排序输出** —— 顺序稳定才可 diff、才可复现。"""
    found: list[str] = []
    for base, dirs, names in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d != "__pycache__")
        found.extend(os.path.join(base, name) for name in sorted(names) if name.endswith(suffixes))
    return sorted(found)


def code_only(text: str) -> str:
    """去掉注释与三引号块 —— 静态扫描只该看**代码**（散文里提到一个词不算实现）。"""
    without_blocks = re.sub(r"\"\"\".*?\"\"\"|'''.*?'''", "", text, flags=re.S)
    return "\n".join(line.split("#", 1)[0] for line in without_blocks.splitlines())


def backend_files() -> list[str]:
    return walk_files(BACKEND, (".py",))


def frontend_files() -> list[str]:
    return walk_files("frontend", (".js", ".css", ".html"))


def imported_modules(path: str) -> set[str]:
    """一个 Python 文件 import 的顶层模块名（`from app.core.errors import x` → `app`）。"""
    found: set[str] = set()
    for match in re.finditer(r"^\s*(?:from\s+([\w.]+)|import\s+([\w.,\s]+))", read(path), re.M):
        first, second = match.groups()
        if first:
            found.add(first.split(".")[0])
        else:
            found.update(part.strip().split(" as ")[0].split(".")[0] for part in second.split(","))
    return {name for name in found if name}


def app_modules(path: str) -> set[str]:
    """同一个文件里的**仓内** `from app.x…` import，收敛到两级（`app.core.errors` → `app.core`）。"""
    return {
        ".".join(match.group(1).split(".")[:2])
        for match in re.finditer(r"^\s*from\s+(app(?:\.\w+)+)", read(path), re.M)
    }


# --------------------------------------------------------------------------- #
# R-A…R-Q 静态扫描（规则内容 = `docs/02 §1.6`；落在哪 = `docs/06 §8`）
# --------------------------------------------------------------------------- #

def ra_string_bounds() -> str:
    """R-A①：请求体的**字符串字段必须有上界**（Pydantic 字段声明须带 `max_length`）。"""
    models = fields = 0
    offenders: list[str] = []
    for path in backend_files():
        body = read(path)
        for block in re.finditer(r"^class\s+\w+\([^)]*BaseModel[^)]*\):\n((?:[ \t].*\n|\n)*)", body, re.M):
            models += 1
            for line in block.group(1).splitlines():
                hit = re.match(r"\s*(\w+)\s*:\s*str\b(.*)$", line)
                if not hit:
                    continue
                fields += 1
                if "max_length" not in hit.group(2):
                    offenders.append(f"{path}:{hit.group(1)}")
    assert not offenders, "字符串字段缺 max_length：" + "；".join(offenders[:5])
    if models == 0:
        raise Skipped("0 个请求体模型（`backend/app/api/**` 未产出 —— `docs/07 §2.2` 的 W1–W4）")
    return f"{models} 个 BaseModel / {fields} 个 str 字段，全部带 max_length"


def ra_regex_quantifiers() -> str:
    """R-A②：判定用的正则**禁嵌套量词**（`docs/02 §8-6` 的灾难性回溯）。"""
    compiled = 0
    offenders: list[str] = []
    for path in walk_files(f"{BACKEND}/domain", (".py",)) + walk_files(f"{BACKEND}/graph", (".py",)):
        for match in re.finditer(r"re\.compile\(\s*r?([\"'])(.*?)\1", read(path), re.S):
            compiled += 1
            if NESTED_QUANTIFIER.search(match.group(2)):
                offenders.append(f"{path}: {match.group(2)[:48]}")
    assert not offenders, "出现嵌套量词（回溯风险）：" + "；".join(offenders)
    if compiled == 0:
        raise Skipped("0 条判定正则（`backend/app/domain/**` 未产出）")
    return f"{compiled} 条 re.compile，无嵌套量词"


def rb_write_commit() -> str:
    """R-B：写库必须**显式 `commit()`**（`docs/02 §8-8` 的"写库静默回滚"）。"""
    writes = 0
    offenders: list[str] = []
    for path in backend_files():
        body = read(path)
        count = len(re.findall(r"\.(?:execute|executemany)\(", body))
        if not count:
            continue
        writes += count
        if ".commit()" not in body:
            offenders.append(path)
    assert not offenders, "有 execute() 但整个文件没有 commit()：" + "；".join(offenders)
    if writes == 0:
        raise Skipped("0 个写库调用点（`backend/app/learning/**` · `identity/**` 未产出 —— W1）")
    return f"{writes} 处 execute()，所在文件都有显式 commit()"


def rd_hardcoded_colors() -> str:
    """R-D：样式**禁硬编码色值** —— 两个豁免：`frontend/styles/tokens.css` 与 `frontend/vendor/**`。

    ★ **为什么多一个豁免**（2026-09-27 · katex 本地化时定）：`vendor/` 是**第三方原件**
      （`docs/03 §5.1` 判给架构与集成、`.gitattributes` 把它标成 binary）—— 它的色值
      **不是我们的令牌面**，改它等于改第三方产物。⇒ 它由**许可 + 来源 + vendor 完整性一节**守，
      不由 R-D 守。★ 豁免**逐件打印**（`vendor/` 几件），不静默跳过。
    """
    pattern = re.compile(r"#[0-9a-fA-F]{3,8}\b|\b(?:rgb|rgba|hsl|hsla|oklch|oklab|color-mix)\(", re.I)
    scanned = 0
    vendor_files = 0
    offenders: list[str] = []
    for path in frontend_files():
        if not path.endswith(".css") or path.endswith("styles/tokens.css"):
            continue
        if path.startswith("frontend/vendor/"):
            vendor_files += 1
            continue
        scanned += 1
        for lineno, line in enumerate(read(path).splitlines(), 1):
            if pattern.search(line):
                offenders.append(f"{path}:{lineno}")
    assert not offenders, "硬编码色值：" + "；".join(offenders[:5])
    return f"{scanned} 个 CSS 文件（豁免：`tokens.css` · `vendor/` 第三方原件 {vendor_files} 件）"


def frontend_smoke_static() -> str:
    """前端冒烟的**静态面**（ADR-0006）：挂载点 · import 图闭合 · 路由监听 · 尺寸令牌被用。

    ★ **渲染面不在这里** —— 真实点击尺寸（R-F①）· 键盘可达与语义标签（R-F②）· 哈希跳转**是否
      真的发生**（R-G）· `must_render` 是否**真的上屏**（R-I）都要真跑 DOM，而 ADR-0006 决定
      **不把无头浏览器带进门禁**（ADR-0001 的零安装前提）。那几条由 `docs/06 §14.5` 检查点
      第 2 条"端到端走一遍并刷新页面"的人工验收覆盖，并在覆盖台账里逐条打印。
    """
    index = "frontend/index.html"
    assert os.path.exists(index), "缺 `frontend/index.html`（同源托管的唯一入口）"
    assert 'id="app-main"' in read(index), "`index.html` 里没有 DOM 挂载点 `#app-main`（`docs/03 §5.1`）"

    seen: set[str] = {index}
    queue = [index]
    edges = 0
    while queue:
        current = queue.pop()
        base = os.path.dirname(current)
        for raw in re.findall(r"""(?:import|from)\s*\(?\s*["']([^"']+)["']""", read(current)):
            if not raw.startswith("."):
                continue
            edges += 1
            target = os.path.normpath(os.path.join(base, raw))
            assert os.path.exists(target), (
                f"{current} 引用了不存在的模块：{raw} —— 同源托管下它就是 404（白屏级故障）"
            )
            if target not in seen:
                seen.add(target)
                queue.append(target)

    router = read("frontend/core/router.js")
    for event in ("hashchange", "popstate"):
        # ★ 认**注册**那一句，不认"词出现过" —— 第一版写成 `event in router` 时，"监听被删掉、
        #   只剩注释与 removeEventListener" 照样通过（反向演示当场抓到）。
        assert re.search(rf"addEventListener\(\s*[\"']{event}[\"']", router), (
            f"`core/router.js` 里没有 `addEventListener('{event}', …)`（`docs/01` N11 的可寻址面）"
        )

    targets = [path for path in frontend_files() if path.endswith(".css") and "var(--target-min)" in read(path)]
    shell = [path for path in targets if path.startswith(("frontend/styles/", "frontend/features/"))]
    assert shell, "外壳与页面样式里没有一处使用 `var(--target-min)` —— N8① 的声明面没接上"
    names = " · ".join(os.path.basename(os.path.dirname(path)) + "/" + os.path.basename(path)
                       for path in targets)
    return f"import 图 {len(seen)} 文件 / {edges} 条边闭合 · 挂载点 + hashchange/popstate · --target-min {len(targets)} 处（{names}）"


def vendor_integrity() -> str:
    """`frontend/vendor/**` 的**本地化完整性**（N4 的实物）：许可 · 来源 · CSS 引用全部命中。

    ★ 这一节**替代不了**对第三方代码的审读；它守的是"**本地化这件事真的做完了**" ——
      少一个字体文件，公式会回退到错误字形，而这件事在 CI 里**没有任何症状**。
    """
    packages = sorted(glob.glob("frontend/vendor/*"))
    if not packages:
        raise Skipped("`frontend/vendor/**` 未产出（N4 的实物 —— `docs/07 §2.2`）")
    notes: list[str] = []
    for folder in packages:
        for required in ("LICENSE", "PROVENANCE.md"):
            assert os.path.exists(os.path.join(folder, required)), (
                f"{folder} 缺 `{required}` —— 开放许可素材须按许可署名、本地化须写明来源"
            )
        refs = 0
        problems: list[str] = []
        for css in walk_files(folder, (".css",)):
            for raw in re.findall(r"url\(([^)]+)\)", read(css)):
                target = raw.strip().strip("'\"")
                if target.startswith(("data:", "http://", "https://", "//")):
                    problems.append(f"{css}: 外部地址（N4 要本地化）{target[:40]}")
                    continue
                refs += 1
                candidate = os.path.join(os.path.dirname(css), target.split("?")[0].split("#")[0])
                if not os.path.exists(candidate):
                    problems.append(f"{css}: 引用不存在 {target}")
        assert not problems, "；".join(problems[:4])
        notes.append(f"{os.path.basename(folder)}：LICENSE + 来源 + {refs} 个 CSS 引用全部命中")
    return " · ".join(notes)


def re_no_innerhtml() -> str:
    """R-E：前端**不得用 `innerHTML` 拼内容**（XSS 的唯一防线，`docs/02 §7.4`）。"""
    pattern = re.compile(r"innerHTML\s*(?:\+=|=)")
    scanned = 0
    offenders: list[str] = []
    for path in frontend_files():
        if not path.endswith((".js", ".html")):
            continue
        scanned += 1
        for lineno, line in enumerate(read(path).splitlines(), 1):
            if pattern.search(line):
                offenders.append(f"{path}:{lineno}")
    assert not offenders, "innerHTML 赋值：" + "；".join(offenders)
    return f"{scanned} 个前端文件，0 处 innerHTML 赋值"


def rh_page_budget() -> str:
    """R-H：演示类页面总行数 ≤ 产品功能页总行数（分组键 = `feature.json` 的 `class`）。"""
    groups: dict[str, int] = {}
    features = 0
    for meta in sorted(glob.glob("frontend/features/*/feature.json")):
        data = json.loads(read(meta))
        kind = data.get("class")
        assert kind in ("product", "demo"), (
            f"{meta}: class={kind!r} 不在 product / demo 里（`docs/03 §5.2` 是它的唯一权威）"
        )
        folder = os.path.dirname(meta)
        groups[kind] = groups.get(kind, 0) + sum(
            len(read(os.path.join(folder, name)).splitlines())
            for name in ("index.js", "styles.css", "feature.json")
            if os.path.exists(os.path.join(folder, name))
        )
        features += 1
    if features == 0:
        raise Skipped("0 个 feature（`frontend/features/**` 未产出）")
    demo, product = groups.get("demo", 0), groups.get("product", 0)
    assert demo <= product, f"演示类 {demo} 行 > 产品功能页 {product} 行（`docs/01` 承诺 10 · R-H）"
    return f"{features} 个 feature：product {product} 行 · demo {demo} 行"


def rk_single_egress() -> str:
    """R-K：**全仓只有一个运行期出网口** `backend/app/tools/llm.py`。"""
    hits = 0
    offenders: list[str] = []
    for path in backend_files():
        for match in re.finditer(r"^\s*(?:from|import)\s+([\w.]+)", read(path), re.M):
            if match.group(1).split(".")[0] not in NETWORK_MODULES:
                continue
            hits += 1
            if not path.endswith("tools/llm.py"):
                offenders.append(f"{path}: {match.group(1)}")
    assert not offenders, "出网模块出现在 `tools/llm.py` 之外：" + "；".join(offenders)
    if hits == 0:
        raise Skipped("0 处出网 import（`tools/llm.py` 尚未发真实请求 —— W3）")
    return f"{hits} 处出网 import，全部在 `tools/llm.py`"


def rq_tool_boundary() -> str:
    """R-Q ①②：**允许集合不得为通配 / `None`**；**成功标记不得硬编码**（`docs/02 §1.6`）。"""
    wildcard: list[str] = []
    hardcoded: list[str] = []
    for path in backend_files():
        for lineno, line in enumerate(read(path).splitlines(), 1):
            if re.search(r"(?<![\w_])allowed\s*[:=][^\n]*\bNone\b", line):
                wildcard.append(f"{path}:{lineno}")
            if re.search(r"(?<![\w_])ok\s*[:=]\s*True\b", line):
                hardcoded.append(f"{path}:{lineno}")
    assert not wildcard, "允许集合写成 None（R-Q ①）：" + "；".join(wildcard)
    assert not hardcoded, "硬编码成功标记（R-Q ②）：" + "；".join(hardcoded)
    return "0 处 allowed=None · 0 处硬编码 ok=True"


def rj_ro_log_gate() -> str:
    """R-J / R-O：日志写入点**收敛在 `core/logging.py` 的唯一口子**上，且无游离 `print`。

    ★ 这是 v1 能做到的最强形态：`core/logging.py` 已经把 R-O 变成**构造约束**
    （非 `*_id` / 非白名单键当场抛错）⇒ 静态面只需守住"**没有人绕过它**"。
    """
    log_writers: list[str] = []
    prints: list[str] = []
    for path in backend_files():
        body = read(path)
        is_gate = path.endswith("core/logging.py")
        has_cli = "__main__" in body
        for lineno, line in enumerate(code_only(body).splitlines(), 1):
            if not is_gate and re.search(
                r"(?<![\w.])(?:getLogger|logging\.\w+)\s*\(|(?<![\w.])logger\s*\.\w+\s*\(", line
            ):
                log_writers.append(f"{path}:{lineno}")
            if not has_cli and re.search(r"(?<![\w.])print\(", line):
                prints.append(f"{path}:{lineno}")
    assert not log_writers, "绕过 `core/logging.py` 直接写日志：" + "；".join(log_writers)
    assert not prints, "非 CLI 文件里的 print（R-J 的隐患面）：" + "；".join(prints)
    return "日志写入点只在 `core/logging.py` · 0 处游离 print"


# --------------------------------------------------------------------------- #
# 依赖方向与结构纪律（`docs/03 §3.1` 的模块依赖表 · §3.2 的三条纪律）
# --------------------------------------------------------------------------- #

def import_direction() -> str:
    """模块依赖方向：`docs/03 §3.1` 的**允许依赖**白名单 + **明确禁止**里可静态判的部分。"""
    allowed = {
        "core": {"app.core"},
        "domain": {"app.core", "app.domain"},
        "tools": {"app.core", "app.domain", "app.tools"},
        "learning": {"app.core", "app.domain", "app.learning"},
        "identity": {"app.core", "app.identity"},
        "graph": {"app.core", "app.domain", "app.tools", "app.learning", "app.identity", "app.graph"},
        "api": {"app.core", "app.domain", "app.tools", "app.learning", "app.identity", "app.graph", "app.api"},
    }
    forbidden = {
        "domain": {"fastapi", "langgraph", "sqlite3", "sqlalchemy", "httpx", "requests", "urllib",
                   "socket", "app.tools", "app.learning", "app.graph", "app.identity", "app.api"},
        "identity": {"app.graph", "app.domain", "app.api", "fastapi", "langgraph"},
        "tools": {"app.graph", "app.api"},
        "learning": {"app.graph", "app.api"},
        "graph": {"app.api"},
    }
    scanned = 0
    entry_skipped = 0
    problems: list[str] = []
    for path in backend_files():
        relative = os.path.relpath(path, BACKEND)
        parts = relative.split(os.sep)
        if len(parts) == 1:  # `backend/app/main.py` · `backend/app/__init__.py` = 入口层，不设白名单
            entry_skipped += 1
            continue
        module = parts[0]
        if module not in allowed:
            continue
        scanned += 1
        for name in sorted(app_modules(path) - allowed[module]):
            problems.append(f"{relative}: import {name}")
        for name in sorted(imported_modules(path) & forbidden.get(module, set())):
            problems.append(f"{relative}: import {name}（`docs/03 §3.1` 明确禁止）")
    if scanned == 0:
        raise Skipped("0 个业务模块（`backend/app/**` 未产出）")
    assert not problems, "；".join(problems[:6])
    return f"{scanned} 个模块文件，仓内 import 全在白名单内（入口层 {entry_skipped} 个文件不设白名单）"


def frontend_import_direction() -> str:
    """前端依赖（`docs/03 §3.1`）：`features/*` **互不 import** · `components/` 不 import `features/`。"""
    scanned = 0
    problems: list[str] = []
    for path in walk_files("frontend", (".js",)):
        scanned += 1
        body = read(path)
        if "/components/" in path and re.search(r"from\s+[\"'][^\"']*features/", body):
            problems.append(f"{path}: `components/` 里 import `features/`")
        if "/features/" in path:
            owner = path.split("/features/")[1].split("/")[0]
            for match in re.finditer(r"from\s+[\"'][^\"']*features/(\w+)", body):
                if match.group(1) != owner:
                    problems.append(f"{path}: features/{owner} import features/{match.group(1)}")
    assert not problems, "；".join(problems)
    if scanned == 0:
        raise Skipped("0 个前端 JS 文件")
    return f"{scanned} 个前端 JS 文件，依赖方向互斥"


def api_is_thin() -> str:
    """`api/` 必须薄（`docs/03 §3.2` 第 1 条）：**单路由函数 ≤ 40 行** · **不写 SQL**。"""
    files = walk_files(f"{BACKEND}/api", (".py",))
    if not files:
        raise Skipped("`backend/app/api/**` 未产出（W1–W4 的薄接线 —— `docs/07 §2.2`）")
    functions = 0
    problems: list[str] = []
    for path in files:
        body = read(path)
        lines = body.splitlines()
        for index, line in enumerate(lines):
            hit = re.match(r"\s*(?:async\s+)?def\s+(\w+)\s*\(", line)
            if not hit:
                continue
            indent = len(line) - len(line.lstrip())
            length = 0
            for follow in lines[index + 1:]:
                if follow.strip() and (len(follow) - len(follow.lstrip())) <= indent:
                    break
                length += 1
            functions += 1
            if length > 40:
                problems.append(f"{path}: {hit.group(1)}() ≈ {length} 行 > 40")
        for keyword in ("SELECT ", "INSERT INTO", "UPDATE ", "DELETE FROM", "CREATE TABLE"):
            if keyword in body:
                problems.append(f"{path}: 出现 SQL（{keyword.strip()}）")
    assert not problems, "；".join(problems[:5])
    return f"{len(files)} 个 api 文件 / {functions} 个函数（单函数 ≤ 40 行 · 无 SQL）"


def content_rev(revs: dict) -> str:
    """`docs/02 §3.3` 规则 2 的算法（机器可读形式 = `content.schema.json#/x-content-rev`）。"""
    payload = ";".join(f"{key}={revs[key]}" for key in sorted(revs))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def check_06_manifest() -> str:
    """校验⑥（`docs/04 §7`）：`content_rev` **可复算** + `MANIFEST.json` 与五类 `rev` 一致。

    ★ 前半段（算法）**每次都对契约自带的示例验证** —— 它不需要任何资产，因此**永不 SKIP**：
    这正是"示例必须通过它自己的校验"（`docs/04 §4`）在门禁里的用法。
    """
    document = json.loads(read("contracts/content.schema.json"))
    example = next(item for item in document["examples"] if "content_rev" in item)
    assert set(example["revs"]) == set(document["x-content-rev"]["key_order"]), (
        "契约示例的键名与 x-content-rev 的 key_order 不一致"
    )
    computed = content_rev(example["revs"])
    assert computed == example["content_rev"], (
        f"算法与契约示例不符：算出 {computed} ≠ 契约里的 {example['content_rev']}（x-content-rev）"
    )
    if not os.path.exists("content/MANIFEST.json"):
        raise Skipped(
            f"算法已对契约示例验证（{computed}）；但 `content/MANIFEST.json` 未产出"
            f"（五类齐备后由域1 派生 —— `docs/07 §2.2`）"
        )
    manifest = json.loads(read("content/MANIFEST.json"))
    assert manifest["content_rev"] == content_rev(manifest["revs"]), (
        f"MANIFEST.content_rev={manifest['content_rev']} ≠ 重算的 {content_rev(manifest['revs'])}"
    )
    for key in document["x-content-rev"]["key_order"]:
        path = f"content/{key}.json"
        if os.path.exists(path):
            declared = json.loads(read(path))["rev"]
            assert declared == manifest["revs"][key], (
                f"{path} 的 rev={declared} ≠ MANIFEST.revs[{key}]={manifest['revs'][key]}"
            )
    return f"content_rev={manifest['content_rev']}（算法与契约示例一致 · 五类 rev 逐项对齐）"


def check_07_deployment_agnostic() -> str:
    """校验⑦（`docs/04 §7`）：**部署无关化 + 零依赖**（与 N18 验收②同一条断言）。"""
    scanned = 0
    absolute: list[str] = []
    external = 0
    for path in frontend_files():
        scanned += 1
        body = read(path)
        for lineno, line in enumerate(body.splitlines(), 1):
            if re.search(r"https?://", line):
                absolute.append(f"{path}:{lineno}")
        external += len(re.findall(
            r"""(?:src|href)\s*=\s*["']https?://|from\s+["']https?://|import\s*\(\s*["']https?://""", body))
    assert not absolute, "前端出现绝对地址（写死后端 / 外部资源）：" + "；".join(absolute[:5])
    assert external == 0, f"{external} 处外部 CDN / http import（N4 零依赖）"
    for junk in ("package.json", "package-lock.json", "yarn.lock", "node_modules"):
        assert not os.path.exists(junk), f"仓库里出现 npm 产物：{junk}（ADR-0001 免构建）"
    return f"{scanned} 个前端文件：0 处绝对地址 · 0 处外部依赖 · 无 npm 产物"


def contrast_ratio(foreground: str, background: str) -> float:
    """WCAG 2.1 相对亮度对比度（纯 Python · 离线 · 无依赖）。"""

    def luminance(color: str) -> float:
        channels = [int(color[index:index + 2], 16) / 255 for index in (1, 3, 5)]
        linear = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
        return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]

    high, low = sorted((luminance(foreground), luminance(background)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def rf_contrast_pairs() -> str:
    """R-F③ + N8①：对比度 ≥ 4.5:1 —— **只对 `tokens.css` 里声明的色对**（N8 的载体）。

    ★ 渲染结果的对比度**不在**本检查内（要无头浏览器，与"零安装"冲突 —— `docs/01` N8 已记账）。
    """
    body = read("frontend/styles/tokens.css")
    tokens = dict(re.findall(r"(--[\w-]+)\s*:\s*(#[0-9a-fA-F]{6})\s*;", body))
    pairs = re.findall(r"@contrast-pair:\s*(--[\w-]+)\s+on\s+(--[\w-]+)(?:\s*,\s*min\s*([\d.]+))?", body)
    assert pairs, "`tokens.css` 里没有 `@contrast-pair` 声明 —— 色对清单是 R-F③ 的唯一输入（N8）"
    worst = 99.0
    failures: list[str] = []
    for foreground, background, minimum in pairs:
        for name in (foreground, background):
            assert name in tokens, f"色对引用了 `tokens.css` 里没有的十六色：{name}"
        threshold = float(minimum or 4.5)
        ratio = contrast_ratio(tokens[foreground], tokens[background])
        worst = min(worst, ratio)
        if ratio < threshold:
            failures.append(f"{foreground} on {background}: {ratio:.2f} < {threshold}")
    assert not failures, "；".join(failures)
    target = re.search(r"--target-min:\s*(\d+)px", body)
    assert target, "`tokens.css` 里没有 `--target-min`（N8① 的声明面）"
    assert int(target.group(1)) >= 32, f"--target-min = {target.group(1)}px < 32px（N8① 的点击目标）"
    return f"{len(pairs)} 组色对达标（最低 {worst:.2f}:1）· --target-min = {target.group(1)}px"


def content_branch_title(document: dict) -> str:
    """按文档自己的键，指出它**自称**是哪一支（`cases` / `revs` / `asset`）—— 报错时只说那一支。"""
    if "cases" in document:
        return "样例集"
    if "revs" in document:
        return "MANIFEST.json"
    return str(document.get("asset", ""))


def brief_schema_error(errors: list) -> str:
    """把校验错误压成**字段级一行**。

    ★ 为什么必须压：`oneOf` 失败时 jsonschema 的默认消息会把**整个实例**（一个 15 万字符的题库）
      打进输出 —— 红灯会淹掉自己。这里改为只报"**它自称的那一支**"里的前几条字段级错误；
      `minItems` 一类**把实例内嵌进消息**的关键字，则只报校验关键字名。
    """
    first = errors[0]
    branches = getattr(first, "context", None) or []
    if not branches:
        where = "/".join(str(part) for part in first.path) or "(根)"
        return f"@{where}: {first.message[:160]}"
    tips: list[str] = []
    for branch in branches[:3]:
        message = (branch.message or "").strip()
        where = "/".join(str(part) for part in branch.path) or "(根)"
        if message.startswith(("[", "{")):  # 例：`minItems` 的消息形如 "<整个数组> is too short"
            message = f"{branch.validator} 不成立"
        tips.append(f"@{where} {message[:80]}")
    return " / ".join(tips)


def validator_for(document: dict):
    """按 `$id` 自建**离线** registry 的校验器（`jsonschema` / `referencing` 属 `requirements-dev.txt`）。

    ★ 校验② 里那段 import 是内联的（2026-09-20 写的）；本函数是它的**可复用形式**，供内容资产
    校验使用 —— 不回头改校验②，避免把已经跑过一轮的检查动到。
    """
    try:
        from jsonschema import Draft7Validator
        from referencing import Registry, Resource
        from referencing.jsonschema import DRAFT7
    except ImportError as exc:
        raise AssertionError(f"缺少 jsonschema / referencing 依赖（requirements-dev.txt）：{exc}")
    schemas = load_schemas()
    registry = Registry().with_resources(
        [(sid, Resource.from_contents(doc, default_specification=DRAFT7)) for sid, doc in schemas.items()]
    )
    return Draft7Validator(document, registry=registry)


def content_assets() -> str:
    """内容资产与样例集：**格式**（对 `content.schema.json` 的 `oneOf` 每一支）+ 两道**交叉**检查。

    ★ 这是 `docs/06 §11` 的 W5 行里那条「**内容资产对 `content.schema.json` 的格式校验**
    （卡 4 判据① 的机器化）」的落地；在此之前，内容资产只有 W0d 的**一次手跑**（§12.5 的记录）。
    ★ **为什么值得机器做**：`examples` 已被校验② 守着，而**真正入仓的 `content/*.json` 谁也没校**
       —— 手改一处字段名（如 `kc_ids` → `kc_id`）不会有任何红灯，直到某个页面在运行时才发现。
    ★ **交叉检查**（draft-07 表达不了的那些，`x-invariants` 已逐条写成断言说明）：
      ① 样例集的 `item_id` **必须在题库里存在**（引用闭包）；② 若错因库已产出，则
      `expected_conclusion` 里出现的 `mp_*` **也必须在错因库里**（未产出时**如实打印**为未机检）。
    """
    schemas = load_schemas()
    content_id = next(sid for sid in schemas if sid.endswith("/content.schema.json"))
    validator = validator_for(schemas[content_id])
    files = sorted(glob.glob("content/*.json")) + sorted(glob.glob("content/eval/sample/*.json"))
    if not files:
        raise Skipped("0 个内容资产文件（`content/*.json` 未产出 —— 五类资产属 W2 / W4）")

    checked = len(files)
    notes: list[str] = []
    items_of = lambda key: json.loads(read(f"content/{key}.json"))["items"]  # noqa: E731
    produced = {key: len(items_of(key)) for key in ("problems", "types", "mistakes", "dag", "formula")
                if os.path.exists(f"content/{key}.json")}

    # ★ 下界先行：schema 的 `minItems` 也能拦住它，但那时的消息既不提下界的出处、也不说
    #   "现在有几条"；本断言给出的是可行动的句子（`x-floors` 是它的机器可读来源）。
    floors = schemas[content_id]["x-floors"]
    for key, count in produced.items():
        floor = floors.get(key, {}).get("minItems")
        if floor is not None:
            assert count >= floor, f"content/{key}.json 有 {count} 条 < 下界 {floor}（`docs/06 §7` · `x-floors`）"
    notes.append("下界：" + " · ".join(f"{k} {v}" for k, v in produced.items()))

    failures: list[str] = []
    for path in files:
        document = json.loads(read(path))
        errors = sorted(validator.iter_errors(document), key=lambda e: list(e.path))
        if not errors:
            continue
        branches = getattr(errors[0], "context", None) or []
        wanted = content_branch_title(document)
        picked = [branch for branch in branches if wanted and wanted in str(branch.schema.get("title", ""))]
        failures.append(f"{path}: {brief_schema_error(picked or errors)}")
    assert not failures, "；".join(failures[:4])

    sample = sorted(glob.glob("content/eval/sample/*.json"))
    if sample:
        cases = [case for path in sample for case in json.loads(read(path))["cases"]]
        assert 3 <= len(cases) <= 5, f"样例集 {len(cases)} 道 —— `docs/02 §9` 写的是首版 3–5 道"
        assert len({case["case_id"] for case in cases}) == len(cases), "样例集里 `case_id` 有重复"
        if os.path.exists("content/problems.json"):
            known = {item["prob_id"] for item in items_of("problems")}
            missing = [case["item_id"] for case in cases if case["item_id"] not in known]
            assert not missing, f"样例集引用了题库里不存在的 item_id：{missing}"
            notes.append(f"样例集 {len(cases)} 道的 item_id 闭包成立")
        if os.path.exists("content/mistakes.json"):
            known_mp = {item["mp_id"] for item in items_of("mistakes")}
            cited = {mid for case in cases for mid in re.findall(r"mp_[a-z0-9_]+", case["expected_conclusion"])}
            unknown = sorted(cited - known_mp)
            assert not unknown, f"样例集的 expected_conclusion 引用了错因库里没有的 mp_id：{unknown}"
            notes.append(f"{len(cited)} 个 mp_id 引用闭包成立")
        else:
            notes.append("错因库未产出 ⇒ expected_conclusion 的 mp_id 闭包本轮机检跳过（`docs/07 §2.2`）")
        return f"{checked} 个文件过 `content.schema.json` 校验 · " + " · ".join(notes)
    notes.append("样例集未产出（`content/eval/sample/**` —— 属 W5 第二批）")
    return f"{checked} 个文件过 `content.schema.json` 校验 · " + " · ".join(notes)


def coverage_ledger() -> str:
    """覆盖台账：**本门禁不覆盖什么**（`docs/06 §8` 的"落在哪"列就是这么划的）。

    ★ 它的作用不是"多一条检查"，而是让"**没查**"这件事**打印出来**：每一条都能指到权威处
    （下面顺手断言那三个小节还在，避免指针烂掉）。
    """
    for path, number in (("docs/06-开发计划与阶段门.md", "8"), ("docs/02-系统设计.md", "1.6"),
                         ("docs/04-契约层说明.md", "7")):
        assert has_section(path, number), f"覆盖台账引用的 `{path} §{number}` 不存在（指针烂了）"
    outside = ("R-C（责任人，`docs/05 §2.4`）· R-M⑥（无埋点 / 无导出路径，review）"
               "· R-L · R-N（**只有用例**；`docs/06 §8` 的\"落在哪\"列就是这么写的）"
               "· ★ **前端冒烟的渲染面**：R-F① 真实点击尺寸 · R-F② 键盘可达与语义标签 · "
               "R-G 哈希跳转**是否真的发生** · R-I `must_render` **是否真的上屏**（静态面已在下一节，"
               "渲染面按 ADR-0006 留人工 —— 要真跑 DOM，与零安装冲突）；"
               "★ **实跑方式**：`scripts/manual_check.py`（本机手跑，零新依赖，不进 CI）")
    later = ("`api/` 薄（对象未产出）· 启动校验七条（`docs/06 §4` 第 8 项，输入资产未齐）"
             "· 运维演练 ②③④ 与 `backup.py` / `load_test.py`（第 9 项）"
             "· ★ 样例集的**端到端执行**（格式与引用闭包已接）与 `scripts/eval_verify.py`（阶段 C）"
             "· 冻结正式集与期望判据表（**不入主仓**，人工门禁）"
             "· ★ vendor 里第三方代码的**审读**（本地化完整性已接，审读不是脚本的事）")
    return f"不在本脚本：{outside} ⇒ 批次 3 其余：{later}"


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #

def main() -> int:
    print("门禁 v1 —— v0（R-P1/P2/P3 + 契约校验①–⑤）+ W5 的第一批")
    print("  范围与三态（PASS / FAIL / SKIP）见本脚本头部；★ SKIP = 对象未产出，不计入通过。")
    print()

    groups = [
        ("R-P1 · 计数由表体导出", [
            ("docs/01 §1.1 覆盖矩阵（行数 · S1–S4 · 功能/非功能）", rp1_requirements_matrix),
            ("docs/01 附录 B.1 MUST 清单（分类合计 == 标题条数）", rp1_must_checklist),
            ("docs/02 §10 MUST 落点条数", rp1_must_landing_count),
        ]),
        ("R-P2 · 相对引用解析（README + docs/** + docs/adr/**）", [
            ("§ 引用与相对链接全部命中（未产出目标须在 docs/07 登记）", rp2_references),
        ]),
        ("R-P3 · 落点反向闭合", [
            ("docs/02 §10 每格落点闭合", rp3_landing_closure),
        ]),
        ("契约校验①–⑤（docs/04 §7）", [
            ("① schema 自洽（$ref 全部解析）", lambda: check_01_refs(load_schemas())),
            ("② examples 自校验", lambda: check_02_examples(load_schemas())),
            ("③ code 枚举一致（契约 ↔ §6.3 表体）", check_03_error_codes),
            ("④ 25 条 path 覆盖完整", check_04_paths),
            ("⑤ 事件枚举与 x-type-count 一致", check_05_events),
        ]),
        ("契约校验⑥⑦（docs/04 §7）· N4 / N8 的可自动面", [
            ("⑥ content_rev 可复算 + MANIFEST 一致", check_06_manifest),
            ("⑦ 部署无关化 + 零依赖", check_07_deployment_agnostic),
            ("`frontend/vendor/**` 本地化完整性（许可 · 来源 · 引用）", vendor_integrity),
            ("R-F③ 色对对比度 + N8① 点击目标（声明面）", rf_contrast_pairs),
        ]),
        ("内容资产与样例集（`docs/06 §7` · §11 的 W5 行）", [
            ("格式（`content.schema.json` 的 `oneOf`）+ 引用闭包 + 下界", content_assets),
        ]),
        ("R-A…R-Q 的静态面（规则 = docs/02 §1.6）", [
            ("R-A① 请求体字符串字段上界", ra_string_bounds),
            ("R-A② 判定正则禁嵌套量词", ra_regex_quantifiers),
            ("R-B 写库显式 commit()", rb_write_commit),
            ("R-D 样式禁硬编码色值", rd_hardcoded_colors),
            ("R-E 禁 innerHTML 赋值", re_no_innerhtml),
            ("R-H 演示类页面 ≤ 产品功能页", rh_page_budget),
            ("R-K 唯一运行期出网口", rk_single_egress),
            ("R-Q ①② 允许集合 / 成功标记", rq_tool_boundary),
            ("R-J / R-O 日志写入点收敛", rj_ro_log_gate),
        ]),
        ("依赖方向与结构纪律（docs/03 §3.1 · §3.2）", [
            ("后端模块依赖白名单", import_direction),
            ("前端 features / components 依赖互斥", frontend_import_direction),
            ("`api/` 薄（单函数 ≤ 40 行 · 无 SQL）", api_is_thin),
        ]),
        ("前端冒烟（ADR-0006：静态面在门禁 · 渲染面留人工）", [
            ("静态面（挂载点 · import 图闭合 · 路由监听 · 尺寸令牌）", frontend_smoke_static),
        ]),
        ("覆盖台账（如实登记\"没查什么\"）", [
            ("不在本脚本 / 批次 3 其余部分", coverage_ledger),
        ]),
    ]

    for title, items in groups:
        print(f"== {title} ==")
        for name, fn in items:
            check(name, fn)
            label, ok, detail = RESULTS[-1]
            mark = "PASS" if ok is True else ("SKIP" if ok is None else "FAIL")
            print(f"  [{mark}] {label}")
            if detail:
                arrow = "↳" if ok is False else "·"
                print(f"         {arrow} {detail}")
        print()

    passed = sum(1 for _, ok, _ in RESULTS if ok is True)
    skipped = [(name, detail) for name, ok, detail in RESULTS if ok is None]
    failed = [(name, detail) for name, ok, detail in RESULTS if ok is False]
    print(f"== 汇总：通过 {passed} / 失败 {len(failed)} · 未覆盖 {len(skipped)}（共 {len(RESULTS)} 项）==")
    if skipped:
        print()
        print("未覆盖项（**对象未产出** —— 不计入通过，也不是失败）：")
        for name, detail in skipped:
            print(f"  - {name}")
            print(f"      {detail}")
    if failed:
        print()
        print("失败项：")
        for name, detail in failed:
            print(f"  - {name}")
            print(f"      {detail}")
        return 1
    return 0


sys.exit(main())
PYTHON_VERIFY

exit $?
