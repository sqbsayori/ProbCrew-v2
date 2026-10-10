"""本地语料检索工具 —— W4 · T1（``docs/06 §11`` 的 W4 行 · F10 的检索侧）。

**只做词法检索**（决策 1A —— ``docs/02 §1.3`` / §3.3：不用嵌入、不用重排权重、不下载任何模型权重）：
BM25 + 中文 bigram。**不承诺召回率** —— F10 是 SHOULD，验收只要求"命中带出处 / 无命中如实说明"
（``docs/01 §4.1`` 的 F10 行）。

形状（计划 §4.7① 写死）：

- ``ENTRY = search(query, *, corpus_dir="", limit=5)`` —— **签名不变**（``graph/registry.py`` 声明它、
  ``graph/nodes/solve.py`` 调它，``solve.first_cite`` 读 ``value["hits"][0]["cite_id"]``）；
- ``corpus_dir=""`` ⇒ 默认 ``<repo>/backend/data/corpus``，**由 ``__file__`` 推导**（不依赖 cwd，
  换机器 / 换工作目录都取同一个目录）；
- 返回 ``value = {"hits": [{"cite_id", "title", "chapter", "snippet"}]}``；``snippet`` 取自**正文**
  （不含 front matter）；
- ★ **降级只有一条码**：目录缺失 / 目录内无可用语料（缺必填字段、或 ``license`` 不在白名单）/
  无命中 ⇒ ``ok=False, reason="cite_unavailable"`` —— 如实说"出处暂不可用"，**不得凭记忆编造章节号**
  （F10 · N2）。三类的差异写进**审计日志**（``core.logging.log_action``，R-O：只记 ID 与动作），
  **不新增 ``value`` 字段**（``tool.result.data`` 的契约不变）。
- 入参：``query`` 非字符串或 strip 后为空 ⇒ ``ok=False, reason="parse_error"``（"未执行，不是不通过"，
  与 ``tools/expression.py`` 同口径）；``limit`` 夹在 1–20（越界夹取，不报错）。

**索引确定性**（同一份语料每次同一顺序）：只扫 ``corpus/**/*.md``；中文按 **2-gram**、英文/数字按
**小写词**切分；BM25 ``k1=1.2`` / ``b=0.75``；得分并列时按 ``cite_id`` **字典序升序**。

**不出网**：本文件不得 import ``urllib`` / ``socket`` / ``http``（R-K；机器可执行面在
``backend/tests/test_tools.py`` 的 AST 断言）。语料由 ``scripts/fetch_corpus.py`` **离线预抓**，
运行期只读缓存（``docs/02 §1.3``："预抓 + 运行期只读"）。

★ front matter 用**最小子集**自己解析（``key: value`` 单行标量）：``requirements.txt`` 不含 PyYAML，
而写入方是我们自己的 ``scripts/fetch_corpus.py`` ⇒ 形状可控、不引依赖。
"""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

from app.core.logging import log_action
from app.tools import CITE_UNAVAILABLE, PARSE_ERROR, ToolResult

#: 默认语料根 —— ``<repo>/backend/data/corpus``（``__file__`` 推导，不依赖 cwd）。
DEFAULT_CORPUS_DIR = Path(__file__).resolve().parents[2] / "data" / "corpus"

#: ``limit`` 的夹取区间（计划 §4.7①）。
LIMIT_MIN = 1
LIMIT_MAX = 20

#: BM25 的两个参数（计划 §4.7① 写死 —— 不调参、不承诺召回率）。
BM25_K1 = 1.2
BM25_B = 0.75

#: ``snippet`` 的长度上界（正文片段，不是整节）。
SNIPPET_MAX = 120

#: 许可白名单枚举（``docs/02 §1.3`` 规则 1）—— ★ **不在枚举内的语料不可用**（不是"警告后照用"）。
LICENSE_WHITELIST = frozenset({"CC0-1.0", "CC-BY-4.0", "CC-BY-SA-4.0", "public-domain"})

#: front matter 六件（形状权威 ``docs/02 §1.3``）；缺任一件 ⇒ 该文件不可用。
FRONT_MATTER_FIELDS = ("cite_id", "title", "chapter", "license", "source_url", "fetched_at")

_ASCII_WORD = re.compile(r"[a-z0-9]+")
_CJK_RUN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]+")
_FRONT_MATTER = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n?", re.DOTALL)


@dataclass(frozen=True)
class Document:
    """一份可用的语料小节（front matter 已过校验、正文已切词）。"""

    cite_id: str
    title: str
    chapter: int
    body: str
    tokens: "tuple[str, ...]"


def tokenize(text: str) -> "list[str]":
    """中文按 **2-gram**、英文/数字按**小写词**切分（决策 1A 的口径）。

    ★ 由 2-gram 的定义直接得到一条边界：**单个汉字的查询词元为空** ⇒ 检索走"无命中 ⇒
    `cite_unavailable`"（如实降级，不猜）。
    """
    lowered = text.lower()
    tokens = _ASCII_WORD.findall(lowered)
    for run in _CJK_RUN.findall(lowered):
        tokens.extend(run[index : index + 2] for index in range(len(run) - 1))
    return tokens


def parse_front_matter(text: str) -> "tuple[dict[str, str], str] | None":
    """解析 ``---`` 包起来的 front matter（最小子集）；**没有就返回 ``None``**。"""
    match = _FRONT_MATTER.match(text)
    if match is None:
        return None
    fields: "dict[str, str]" = {}
    for line in match.group(1).splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or ":" not in stripped:
            continue
        key, _, value = stripped.partition(":")
        fields[key.strip()] = value.strip().strip('"').strip("'")
    return fields, text[match.end() :]


def load_document(path: Path) -> "Document | None":
    """读一份语料；**不可用就返回 ``None``**（缺字段 / 许可不在白名单 / 读不了 / 正文为空）。"""
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    parsed = parse_front_matter(raw)
    if parsed is None:
        return None
    fields, body = parsed
    if any(not fields.get(name) for name in FRONT_MATTER_FIELDS):
        return None
    if fields["license"] not in LICENSE_WHITELIST:
        return None
    body = body.strip()
    if not body:
        return None
    try:
        chapter = int(fields["chapter"])
    except ValueError:
        return None
    return Document(
        cite_id=fields["cite_id"],
        title=fields["title"],
        chapter=chapter,
        body=body,
        tokens=tuple(tokenize(f"{fields['title']}\n{body}")),
    )


def load_corpus(corpus_dir: Path) -> "list[Document]":
    """扫描 ``corpus_dir/**/*.md``，返回可用文档（按 ``cite_id`` 升序 ⇒ 确定性）。"""
    if not corpus_dir.is_dir():
        return []
    documents = [document for path in sorted(corpus_dir.rglob("*.md")) if (document := load_document(path))]
    documents.sort(key=lambda document: document.cite_id)
    return documents


def rank(
    query: str, documents: Sequence[Document], limit: int
) -> "list[tuple[float, Document]]":
    """BM25 排序；**并列按 `cite_id` 升序**（同一份语料每次同一顺序）。"""
    terms = tokenize(query)
    if not terms or not documents:
        return []
    lengths = [len(document.tokens) for document in documents]
    average = sum(lengths) / len(lengths)
    document_frequencies: Counter[str] = Counter()
    for document in documents:
        document_frequencies.update(set(document.tokens))

    scored: "list[tuple[float, Document]]" = []
    for document, length in zip(documents, lengths):
        counts = Counter(document.tokens)
        score = 0.0
        for term in set(terms):
            frequency = counts.get(term, 0)
            if not frequency:
                continue
            seen = document_frequencies[term]
            inverse = math.log(1 + (len(documents) - seen + 0.5) / (seen + 0.5))
            denominator = frequency + BM25_K1 * (1 - BM25_B + BM25_B * length / average)
            score += inverse * frequency * (BM25_K1 + 1) / denominator
        if score > 0:
            scored.append((score, document))
    scored.sort(key=lambda item: (-item[0], item[1].cite_id))
    return scored[:limit]


def snippet_of(document: Document, terms: Iterable[str]) -> str:
    """取**第一条含命中词元的行**（压缩空白后截断）；没有命中行就取正文首个非空行。

    ★ 只见正文 —— front matter（含 `cite_id` / `license` / `source_url`）不进 ``snippet``。
    """
    wanted = set(terms)
    fallback = ""
    for line in document.body.splitlines():
        collapsed = " ".join(line.split())
        if not collapsed:
            continue
        if not fallback:
            fallback = collapsed
        if wanted & set(tokenize(collapsed)):
            return collapsed[:SNIPPET_MAX]
    return fallback[:SNIPPET_MAX]


def search(query: str, *, corpus_dir: "str | Path" = "", limit: int = 5) -> ToolResult:
    """检索本地教材语料（W4 · T1 的真实现）。

    命中 ⇒ 返回成功结果，且每条 ``hit`` 都带 ``cite_id``（F10 的验收对象）；否则**如实降级**。
    """
    if not isinstance(query, str) or not query.strip():
        return ToolResult(ok=False, reason=PARSE_ERROR)  # 未执行，不是不通过
    try:
        wanted = int(limit)
    except (TypeError, ValueError):
        wanted = LIMIT_MIN
    wanted = max(LIMIT_MIN, min(LIMIT_MAX, wanted))

    directory = Path(corpus_dir) if str(corpus_dir) else DEFAULT_CORPUS_DIR
    if not directory.is_dir():
        return _degrade("corpus_missing")
    documents = load_corpus(directory)
    if not documents:
        return _degrade("corpus_unusable")
    hits = rank(query, documents, wanted)
    if not hits:
        return _degrade("no_hit")
    terms = tokenize(query)
    rows = [
        {
            "cite_id": document.cite_id,
            "title": document.title,
            "chapter": document.chapter,
            "snippet": snippet_of(document, terms),
        }
        for _, document in hits
    ]
    # ★ `ok` 派生自"真的产出了命中行"（R-Q ②：成功标记不得是字面量）
    return ToolResult(ok=bool(rows), value={"hits": rows})


def _degrade(status: str) -> ToolResult:
    """如实降级：**一条对外码 + 一条 R-O 日志**（三类的差异只在日志里，不进 ``value``）。"""
    log_action("retrieval.degrade", reason=CITE_UNAVAILABLE, status=status)
    return ToolResult(ok=False, reason=CITE_UNAVAILABLE)


#: 工具入口 —— ``graph.state.ToolBox`` 按这个名字取（``app/tools/__init__.py`` 的约定）。
ENTRY = search

__all__ = [
    "BM25_B",
    "BM25_K1",
    "DEFAULT_CORPUS_DIR",
    "Document",
    "ENTRY",
    "FRONT_MATTER_FIELDS",
    "LICENSE_WHITELIST",
    "LIMIT_MAX",
    "LIMIT_MIN",
    "SNIPPET_MAX",
    "load_corpus",
    "load_document",
    "parse_front_matter",
    "rank",
    "search",
    "snippet_of",
    "tokenize",
]
