#!/usr/bin/env bash
# scripts/verify.sh —— 本地门禁（v0）· 卡 1 · W0a 第 ⑥ 步
#
# 本地门禁**就是** CI 入口：`.github/workflows/ci.yml` 的实质步骤之一直接调它
# （`docs/03 §8.2` 的"唯一门禁入口"）。用法：
#
#     bash scripts/verify.sh          # 在仓库根执行；全过退出码 0，任一失败非零
#
# ★ v0 的范围是**写死的**（`docs/06 §12.3` 卡 1、`docs/06 §12.7` 第三行）：
#     R-P1 / R-P2 / R-P3（文档自洽）  +  `docs/04 §7` 的契约校验 ①–⑤
#   ★ **⑥⑦ 不在 v0**：⑥ 要 `content/MANIFEST.json`（W4）、⑦ 要前端外壳（W5 的全量门禁）。
#   ★ R-A / R-B / R-D / R-E / R-H / R-I / 依赖方向扫描等同样**不在 v0**（属 W5）。
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
import json
import os
import re
import sys
from urllib.parse import urljoin
from collections import Counter

ROOT = sys.argv[1]
os.chdir(ROOT)

RESULTS: list[tuple[str, bool, str]] = []


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


def check(name: str, fn) -> None:
    try:
        detail = fn() or ""
        RESULTS.append((name, True, detail))
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
# 主流程
# --------------------------------------------------------------------------- #

def main() -> int:
    print("门禁 v0 —— R-P1/P2/P3 + 契约校验①–⑤（范围见 docs/06 §12.3 卡 1）")
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
    ]

    for title, items in groups:
        print(f"== {title} ==")
        for name, fn in items:
            check(name, fn)
            label, ok, detail = RESULTS[-1]
            print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
            if detail:
                print(f"         {detail}" if ok else f"         ↳ {detail}")
        print()

    passed = sum(1 for _, ok, _ in RESULTS if ok)
    failed = [(name, detail) for name, ok, detail in RESULTS if not ok]
    print(f"== 汇总：通过 {passed} / 失败 {len(failed)}（共 {len(RESULTS)} 项）==")
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
