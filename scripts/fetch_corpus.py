"""离线预抓语料 —— W4 · T1（计划 §4.7② 里**唯一被允许的离线出网口**）。

★ **运行期不出网**：服务进程唯一的出网口仍是 ``backend/app/tools/llm.py``（R-K，``docs/02 §1.6``）。
本脚本**离线手跑**，产物落 ``backend/data/corpus/``（L1 —— 教材原文与抓取到的开放许可资源
**不入仓**，`.gitignore` 已排除 ``backend/data/**``，见 ``docs/02 §1.3`` / §3.3 的"存放路径"）。

用法::

    # ① 抓一份（许可必须在白名单枚举内，否则拒绝执行并非零退出）
    python scripts/fetch_corpus.py --url <URL> --title <章节标题> --license CC-BY-4.0 \
        --cite-prefix <教材>-<nn> [--out-dir <路径>] [--proxy <URL>]

    # ② 降级语料（**不联网**）：试抓的任一条件不成立时，按计划 §4.7② 落自撰演示语料
    python scripts/fetch_corpus.py --demo [--out-dir <路径>]

形状（写死，``docs/02 §1.3`` 是权威）：落 ``<教材>/ch<nn>/sec<nn>.md`` + front matter 六件
（``cite_id`` / ``title`` / ``chapter`` / ``license`` / ``source_url`` / ``fetched_at``），
其中 ``cite_id`` 的 ``<教材>-<nn>-<nn>`` 就是 F10 的验收对象。

★ **试抓的实测结论（2026-10-10，细节见 `_plan/data.md`）**：白名单内**可达**的源里没有中文概率论教材 ——
Gutenberg（`public-domain`，直连 200）只有英文的 Venn / Boole / Poincaré；Wikibooks（`CC-BY-SA-4.0`）
直连超时；OpenStax（英文）的许可表述**自相矛盾**（正文 CC BY 4.0，紧邻文字却写 noncommercial + share alike）。
而检索是**纯词法**（BM25 + 中文 bigram，决策 1A）⇒ 抓到英文语料对中文查询**恒不命中**，
只会制造"有语料却检索不到"的假故障。故本轮**降级**为 ``--demo``，如实记「F10 部分达成（降级）」。

★ 与计划 §4.7② 的差异（新增的两个开关，原因写在提交信息里）：``--proxy`` —— 计划要求
"站点可达（走 ``socks5h://127.0.0.1:10808``）"，而这个口只有 http(s) 代理能用标准库走；
``--demo`` —— 降级语料必须**可再生成**（它不入仓），否则"有语料"这条路径永远没有样本。
"""
from __future__ import annotations

import argparse
import re
import sys
import urllib.request
from datetime import date
from pathlib import Path
from typing import Any

#: ``scripts/`` 的兄弟目录 ``backend/`` —— ``app`` 包的父目录（脚本不在包里，自己引导）。
BACKEND_DIR = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.tools.retrieval import (  # noqa: E402  （必须在 sys.path 引导之后）
    DEFAULT_CORPUS_DIR,
    LICENSE_WHITELIST,
)

#: 自撰语料的 ``source_url`` —— 指向本仓（它没有外部来源，**不编一个假的**）。
REPO_URL = "https://github.com/sqbsayori/ProbCrew-v2"

#: ``--cite-prefix`` 的形状：``<教材>-<两位章节号>``（对应 ``cite_id`` 的前两段）。
CITE_PREFIX_PATTERN = re.compile(r"^[a-z0-9_]+-\d{2}$")

DEMO_ASSET = "demo"
DEMO_TITLE = "演示语料（自撰，非教材）"
DEMO_LICENSE = "CC0-1.0"

#: 降级语料的两节 —— 覆盖本项目最常用的几个知识点词（检索要有可命中的样本）。
DEMO_SECTIONS: "tuple[tuple[str, str], ...]" = (
    (
        "样本空间与古典概型",
        "本节是**自撰的演示语料**，不是任何教材的原文；用途只是让本地检索链路（BM25 + 中文 bigram）"
        "有一次可命中的样本，并验证「命中带 cite_id / 无命中如实降级」两条路径。\n\n"
        "样本空间是随机试验全部可能结果的集合。当样本空间有限、且每个基本事件等可能时，"
        "事件 A 的概率等于 A 包含的基本事件数除以样本空间的基本事件数 —— 这就是古典概型。",
    ),
    (
        "条件概率与贝叶斯公式",
        "条件概率 P(A|B) 刻画「已知 B 发生」时 A 发生的可能性，由它可直接得到乘法公式；"
        "再把全概率公式代进去，就得到贝叶斯公式 —— 它把「先验」与「证据」接成一条链。",
    ),
)


def front_matter(
    *,
    cite_id: str,
    title: str,
    chapter: int,
    license_name: str,
    source_url: str,
    fetched_at: str,
) -> str:
    """front matter 六件（``docs/02 §1.3`` 的形状；``chapter`` 与 ``cite_id`` 第 2 段同值）。"""
    return (
        "---\n"
        f"cite_id: {cite_id}\n"
        f"title: {title}\n"
        f"chapter: {chapter}\n"
        f"license: {license_name}\n"
        f"source_url: {source_url}\n"
        f"fetched_at: {fetched_at}\n"
        "---\n\n"
    )


def write_section(
    out_dir: Path,
    *,
    slug: str,
    chapter: int,
    section: int,
    cite_id: str,
    title: str,
    license_name: str,
    source_url: str,
    body: str,
) -> Path:
    """写一节：``<out>/<教材>/ch<nn>/sec<nn>.md``（路径即章节位置）。"""
    path = out_dir / slug / f"ch{chapter:02d}" / f"sec{section:02d}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        front_matter(
            cite_id=cite_id,
            title=title,
            chapter=chapter,
            license_name=license_name,
            source_url=source_url,
            fetched_at=date.today().isoformat(),
        )
        + body.strip()
        + "\n",
        encoding="utf-8",
    )
    return path


def write_demo(out_dir: Path) -> "list[Path]":
    """降级语料（**不联网**）：``demo/ch01/secNN.md`` —— ★ ``title`` 如实标注"自撰，非教材"。"""
    written: "list[Path]" = []
    for index, (heading, body) in enumerate(DEMO_SECTIONS, start=1):
        written.append(
            write_section(
                out_dir,
                slug=DEMO_ASSET,
                chapter=1,
                section=index,
                cite_id=f"{DEMO_ASSET}-01-{index:02d}",
                title=DEMO_TITLE,
                license_name=DEMO_LICENSE,
                source_url=REPO_URL,
                body=f"## {heading}\n\n{body}",
            )
        )
    return written


def split_sections(text: str) -> "list[tuple[str, str]]":
    """按 markdown 二级标题（``## ``）切分；**一份都没有就整份当一节**。"""
    sections: "list[tuple[str, str]]" = []
    current_title: "str | None" = None
    current: "list[str]" = []
    for line in text.splitlines():
        if line.startswith("## "):
            if current_title is not None:
                sections.append((current_title, "\n".join(current).strip()))
            current_title = line[3:].strip()
            current = []
        elif current_title is not None:
            current.append(line)
    if current_title is not None:
        sections.append((current_title, "\n".join(current).strip()))
    if not sections:
        body = text.strip()
        if body:
            first = next((line[2:].strip() for line in text.splitlines() if line.startswith("# ")), "")
            sections = [(first or "（未命名小节）", body)]
    return [(name, body) for name, body in sections if body]


def fetch(url: str, proxy: str) -> "tuple[str, str]":
    """抓一次，返回 ``(文本, content-type)``。★ 只有本函数出网，且只在**离线手跑**时执行。"""
    handlers: "list[Any]" = []
    if proxy:
        handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    opener = urllib.request.build_opener(*handlers)
    request = urllib.request.Request(url, headers={"User-Agent": "ProbCrew-corpus-fetch/1.0"})
    with opener.open(request, timeout=30) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        content_type = response.headers.get_content_type() or ""
        return response.read().decode(charset, errors="replace"), content_type


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="离线预抓语料（W4 · T1；运行期不出网）")
    parser.add_argument("--url", help="资料地址（markdown / 纯文本）")
    parser.add_argument("--title", help="章节标题 —— 写进 front matter 的 title")
    parser.add_argument("--license", dest="license_name", help="许可（必须在白名单枚举内）")
    parser.add_argument("--cite-prefix", dest="cite_prefix", help="<教材>-<两位章节号>，如 probstats-03")
    parser.add_argument("--out-dir", dest="out_dir", default="", help="缺省 = backend/data/corpus")
    parser.add_argument("--proxy", default="", help="http(s) 代理（socks5 不支持）")
    parser.add_argument("--demo", action="store_true", help="写降级语料（不联网）")
    return parser


def main(argv: "list[str] | None" = None) -> int:
    """返回进程退出码：0 成功 · 2 用法或许可被拒 · 3 抓取或形状不成立。"""
    args = build_parser().parse_args(argv)
    out_dir = Path(args.out_dir) if args.out_dir else DEFAULT_CORPUS_DIR

    if args.demo:
        written = write_demo(out_dir)
        print(f"[OK] 降级语料 {len(written)} 节 → {out_dir}（title 如实标注「自撰，非教材」）")
        return 0

    missing = [name for name in ("url", "title", "license_name", "cite_prefix") if not getattr(args, name)]
    if missing:
        print("[拒绝] 缺少参数：" + " · ".join(missing) + "（或用 --demo 写降级语料）", file=sys.stderr)
        return 2
    if args.license_name not in LICENSE_WHITELIST:
        print(
            f"[拒绝] 许可 {args.license_name!r} 不在白名单枚举内（`docs/02 §1.3` 规则 1）："
            + " · ".join(sorted(LICENSE_WHITELIST)),
            file=sys.stderr,
        )
        return 2
    if not CITE_PREFIX_PATTERN.match(args.cite_prefix):
        print(
            f"[拒绝] --cite-prefix {args.cite_prefix!r} 形状不对（应为「教材-两位章节号」，如 probstats-03）",
            file=sys.stderr,
        )
        return 2

    slug, _, chapter_text = args.cite_prefix.rpartition("-")
    chapter = int(chapter_text)
    try:
        text, content_type = fetch(args.url, args.proxy)
    except Exception as error:  # 抓取失败如实报出，**不静默降级**（降级由人按计划 §4.7② 决定）
        print(f"[失败] 抓取 {args.url} 出错：{type(error).__name__}: {error}", file=sys.stderr)
        return 3
    if "html" in content_type.lower() or text.lstrip()[:1] == "<":
        print("[拒绝] 抓到的内容像 HTML；本工具只吃 markdown / 纯文本（HTML 请先自行转换）", file=sys.stderr)
        return 3

    sections = split_sections(text)
    if not sections:
        print("[失败] 正文为空，没写出任何一节", file=sys.stderr)
        return 3
    for index, (heading, body) in enumerate(sections, start=1):
        write_section(
            out_dir,
            slug=slug,
            chapter=chapter,
            section=index,
            cite_id=f"{slug}-{chapter:02d}-{index:02d}",
            title=args.title,
            license_name=args.license_name,
            source_url=args.url,
            body=f"## {heading}\n\n{body}",
        )
    print(f"[OK] {len(sections)} 节 → {out_dir / slug / f'ch{chapter:02d}'}（license={args.license_name}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
