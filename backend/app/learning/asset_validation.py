"""内容资产启动校验 —— 七条坏资产必须在监听端口前拒绝启动。

唯一实现同时供 `app.main` 与 `scripts/verify.sh` 调用。这里只用标准库，
不复制 `content.schema.json` 的完整 JSON Schema 校验；七条之外的结构/规模校验仍由门禁负责。
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterable, Mapping

ASSET_KEYS = ("dag", "formula", "mistakes", "problems", "types")
_REQUIRED_CORPUS_FIELDS = ("license", "source_url", "fetched_at")
_FRONT_MATTER = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n?", re.DOTALL)


class ContentAssetValidationError(RuntimeError):
    """七条启动校验失败；``violations`` 逐条保留可行动的规则名。"""

    def __init__(self, violations: Iterable[str]) -> None:
        self.violations = tuple(str(item) for item in violations)
        super().__init__("；".join(self.violations))


def _read_json(path: Path) -> Mapping[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _asset(content_dir: Path, key: str) -> Mapping[str, Any]:
    path = content_dir / f"{key}.json"
    if not path.exists():
        raise FileNotFoundError(f"content/{key}.json 不存在")
    return _read_json(path)


def _items(content_dir: Path, key: str) -> "list[Mapping[str, Any]]":
    items = _asset(content_dir, key).get("items")
    return list(items) if isinstance(items, list) else []


def _has_cycle(graph: "Mapping[str, list[str]]") -> "list[str]":
    visiting: set[str] = set()
    visited: set[str] = set()
    cycle_nodes: set[str] = set()

    def visit(node: str) -> None:
        if node in visiting:
            cycle_nodes.add(node)
            return
        if node in visited:
            return
        visiting.add(node)
        for dependency in graph.get(node, []):
            visit(dependency)
        visiting.remove(node)
        visited.add(node)

    for node in graph:
        visit(node)
    return sorted(cycle_nodes)


def _corpus_files(corpus_dir: Path) -> "list[Path]":
    return sorted(corpus_dir.rglob("*.md")) if corpus_dir.is_dir() else []


def validate_content_assets(content_dir: Path) -> None:
    """执行 `docs/02 §8-14` 的七条；失败抛 :class:`ContentAssetValidationError`。"""
    content_dir = Path(content_dir)
    violations: "list[str]" = []

    try:
        dag = _items(content_dir, "dag")
        problems = _items(content_dir, "problems")
        formula = _items(content_dir, "formula")
        mistakes = _items(content_dir, "mistakes")
        types = _items(content_dir, "types")
        manifest = _read_json(content_dir / "MANIFEST.json")
    except (FileNotFoundError, json.JSONDecodeError, OSError) as exc:
        raise ContentAssetValidationError([f"内容资产无法加载：{exc}"]) from exc

    graph: "dict[str, list[str]]" = {}
    for node in dag:
        node_id = str(node.get("kc_id") or "")
        graph[node_id] = [str(dep) for dep in node.get("depends_on", [])]
    cycle_nodes = _has_cycle(graph)
    if cycle_nodes:
        violations.append(f"DAG 有环：{', '.join(cycle_nodes[:8])}")

    for key, items in (("problems", problems), ("formula", formula), ("mistakes", mistakes), ("types", types)):
        missing = [str(item.get("prob_id") or item.get("fm_id") or item.get("mp_id") or item.get("pt_id") or "?") for item in items if not item.get("kc_ids")]
        if missing:
            violations.append(f"{key} 条目缺 kc_ids：{', '.join(missing[:8])}")

    missing_gradable = [str(item.get("prob_id") or "?") for item in problems if "gradable" not in item]
    if missing_gradable:
        violations.append(f"题目缺 gradable：{', '.join(missing_gradable[:8])}")
    missing_answer_kind = [str(item.get("prob_id") or "?") for item in problems if not item.get("answer_kind")]
    if missing_answer_kind:
        violations.append(f"题目缺 answer_kind：{', '.join(missing_answer_kind[:8])}")

    names = [str(item.get("name") or "") for item in formula]
    duplicates = sorted({name for name in names if name and names.count(name) > 1})
    if duplicates:
        violations.append(f"公式名称重复：{', '.join(duplicates[:8])}")

    declared = manifest.get("revs") if isinstance(manifest.get("revs"), Mapping) else {}
    for key in ASSET_KEYS:
        actual = _asset(content_dir, key).get("rev")
        if declared.get(key) != actual:
            violations.append(f"{key}.rev 与 MANIFEST 不一致：{actual!r} != {declared.get(key)!r}")

    corpus_dir = content_dir.parent / "backend" / "data" / "corpus"
    for path in _corpus_files(corpus_dir):
        text = path.read_text(encoding="utf-8")
        match = _FRONT_MATTER.match(text)
        fields: "dict[str, str]" = {}
        if match:
            for line in match.group(1).splitlines():
                key, separator, value = line.partition(":")
                if separator and value.strip():
                    fields[key.strip()] = value.strip()
        missing = [name for name in _REQUIRED_CORPUS_FIELDS if not fields.get(name)]
        if missing:
            violations.append(f"L1 语料缺 {'/'.join(missing)}：{path.name}")

    if violations:
        raise ContentAssetValidationError(violations)


__all__ = ["ASSET_KEYS", "ContentAssetValidationError", "validate_content_assets"]
