"""七条内容资产启动校验的反向演示。"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from app.learning.asset_validation import ContentAssetValidationError, validate_content_assets

REPO_ROOT = Path(__file__).resolve().parents[2]


def _workspace(tmp_path: Path) -> "tuple[Path, Path]":
    content = tmp_path / "content"
    corpus = tmp_path / "backend" / "data" / "corpus"
    shutil.copytree(REPO_ROOT / "content", content)
    corpus.mkdir(parents=True)
    (corpus / "sec01.md").write_text(
        "---\n"
        "cite_id: test-01-01\n"
        "title: 测试语料\n"
        "chapter: 1\n"
        "license: CC0-1.0\n"
        "source_url: https://example.com/test\n"
        "fetched_at: 2026-11-19\n"
        "---\n\n"
        "测试语料正文。\n",
        encoding="utf-8",
    )
    return content, corpus


def _read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _assert_rejected(content: Path, needle: str) -> None:
    with pytest.raises(ContentAssetValidationError) as raised:
        validate_content_assets(content)
    assert needle in str(raised.value)


def test_valid_assets_pass(tmp_path: Path) -> None:
    content, _ = _workspace(tmp_path)
    validate_content_assets(content)


def test_dag_cycle_is_rejected(tmp_path: Path) -> None:
    content, _ = _workspace(tmp_path)
    path = content / "dag.json"
    doc = _read(path)
    first, last = doc["items"][0], doc["items"][-1]
    first["depends_on"] = [last["kc_id"]]
    last["depends_on"] = [first["kc_id"]]
    _write(path, doc)
    _assert_rejected(content, "DAG 有环")


def test_missing_kc_ids_is_rejected(tmp_path: Path) -> None:
    content, _ = _workspace(tmp_path)
    path = content / "problems.json"
    doc = _read(path)
    doc["items"][0].pop("kc_ids")
    _write(path, doc)
    _assert_rejected(content, "缺 kc_ids")


def test_missing_gradable_is_rejected(tmp_path: Path) -> None:
    content, _ = _workspace(tmp_path)
    path = content / "problems.json"
    doc = _read(path)
    doc["items"][0].pop("gradable")
    _write(path, doc)
    _assert_rejected(content, "缺 gradable")


def test_missing_answer_kind_is_rejected(tmp_path: Path) -> None:
    content, _ = _workspace(tmp_path)
    path = content / "problems.json"
    doc = _read(path)
    doc["items"][0].pop("answer_kind")
    _write(path, doc)
    _assert_rejected(content, "缺 answer_kind")


def test_duplicate_formula_name_is_rejected(tmp_path: Path) -> None:
    content, _ = _workspace(tmp_path)
    path = content / "formula.json"
    doc = _read(path)
    doc["items"][1]["name"] = doc["items"][0]["name"]
    _write(path, doc)
    _assert_rejected(content, "公式名称重复")


def test_rev_manifest_mismatch_is_rejected(tmp_path: Path) -> None:
    content, _ = _workspace(tmp_path)
    path = content / "dag.json"
    doc = _read(path)
    doc["rev"] = "dag-2099.01.01.1"
    _write(path, doc)
    _assert_rejected(content, "dag.rev 与 MANIFEST 不一致")


def test_explicit_corpus_dir_is_used_independently_of_content_dir(tmp_path: Path) -> None:
    content, corpus = _workspace(tmp_path)
    alternate = tmp_path / "alternate-corpus"
    alternate.mkdir()
    (alternate / "sec.md").write_text("没有许可字段的正文", encoding="utf-8")
    with pytest.raises(ContentAssetValidationError) as raised:
        validate_content_assets(content, alternate)
    assert "L1 语料缺 license" in str(raised.value)
    validate_content_assets(content, corpus)


def test_corpus_missing_license_is_rejected(tmp_path: Path) -> None:
    content, corpus = _workspace(tmp_path)
    target = sorted(corpus.rglob("*.md"))[0]
    text = target.read_text(encoding="utf-8").replace("license: CC0-1.0\n", "")
    target.write_text(text, encoding="utf-8")
    _assert_rejected(content, "L1 语料缺 license")
