"""domain/ 的测试用例（W0b · 卡2 · 第③步 ⇒ 边②）。

命名约定（docs/05 §3）：测试文件与被测模块同名 —— ``test_domain.py`` ↔ ``domain/``。
★ ``conftest.py`` / pytest 入口配置归架构与集成（模块一，docs/06 §12.3 卡1 第⑤步）；
  在模块一的 tests 入口就绪之前，本文件自带最小 sys.path 引导（见下），届时由模块一的
  conftest 接管，本引导可删。

覆盖（对应计划 3.2）：
1. 归一化：大小写锚点 / 空白 / 全半角 / 精度口径 / 边界
2. 判定：value 正确路径（含 0.08330 ≡ 0.0833、50% ≡ 0.5）
3. 判定：gradable=false 显式降级（契约 gradeResult 第二支逐字段）
4. 参数校验：R-A 上界（>200 字 ⇒ invalid_argument）
5. 错因匹配：四类正样本（契约注入集 category）+ 两类负样本（F5 不得编造）
6. 防 ReDoS：100k 位数字毫秒级完成（R-A：无嵌套量词）
7. 架构约束：domain/ import 白名单静态断言（docs/03 §3.1，可自动核对）
"""
from __future__ import annotations

import ast
import sys
import time
from pathlib import Path

# ── 最小 sys.path 引导（模块一 conftest 就绪后可删）──────────────────────────
BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import pytest

from app.core.errors import AppError
from app.domain import grade, match_mistake, normalize

# ---------------------------------------------------------------------------
# 题库条目（形状对齐 contracts/content.schema.json problemEntry 的判定相关字段；
# 数据参考 x-illustrative-examples 的 prob_bayes_0012 与样例集 s1–s3 同型）
# ---------------------------------------------------------------------------

BAYES_ITEM = {
    "prob_id": "prob_bayes_0012",
    "stem": "某病发病率 1%，检测灵敏度 90%、特异度 90%，检出阳性者患病的概率是多少？",
    "expected": "0.0833",
    "kc_ids": ["kc_bayes"],
    "pt_id": "pt_bayes_forward",
    "gradable": True,
    "answer_kind": "value",
    "difficulty": 3,
}

SWAP_ITEM = {
    "prob_id": "prob_cond_swap",
    "stem": "已知事件 B 发生时 A 发生的条件概率是多少？",
    "expected": "P(A|B)",
    "kc_ids": ["kc_conditional_prob"],
    "pt_id": "pt_conditional_read",
    "gradable": True,
    "answer_kind": "value",
    "difficulty": 2,
}

# ---------------------------------------------------------------------------
# 1. 归一化
# ---------------------------------------------------------------------------


def test_normalize_case_anchor() -> None:
    """契约实例锚点：P(B|A) → p(b|a)（attempt.schema.json examples）。"""
    assert normalize("P(B|A)") == "p(b|a)"


def test_normalize_strips_all_whitespace() -> None:
    assert normalize(" P(B | A)\n") == "p(b|a)"
    assert normalize("0.\t0833") == "0.0833"  # 中间空白也去（去空白 = 删全部空白）


def test_normalize_fullwidth_to_halfwidth() -> None:
    assert normalize("Ｐ（Ｂ｜Ａ）") == "p(b|a)"


def test_normalize_decimal_precision() -> None:
    """精度口径：小数去尾零。"""
    assert normalize("0.50") == "0.5"
    assert normalize("1.00") == "1"
    assert normalize("0.0833") == "0.0833"  # 非尾零不动


def test_normalize_symbol_unification() -> None:
    assert normalize("−5") == "-5"
    assert normalize("2×3÷4") == "2*3/4"


def test_normalize_empty() -> None:
    assert normalize("") == ""
    assert normalize("   ") == ""


# ---------------------------------------------------------------------------
# 2. 判定 —— value 正确路径
# ---------------------------------------------------------------------------


def test_grade_correct_value() -> None:
    result = grade("0.0833", BAYES_ITEM)
    assert result == {
        "graded": True,
        "correct": True,
        "score": 1.0,
        "mistake_id": None,
        "mistake_matched": False,
    }


def test_grade_correct_value_precision_equivalence() -> None:
    """精度口径：尾零等价（0.08330 ≡ 0.0833）。"""
    assert grade("0.08330", BAYES_ITEM)["correct"] is True


def test_grade_value_percent_equivalence() -> None:
    """统一符号：50% ≡ 0.5（精确有理数比较）。"""
    item = {**BAYES_ITEM, "expected": "0.5"}
    assert grade("50%", item)["correct"] is True


def test_grade_fraction_equivalence() -> None:
    """1/4 ≡ 0.25（精确有理数比较，归一化不改写分数形状）。"""
    item = {**BAYES_ITEM, "expected": "0.25"}
    assert grade("1/4", item)["correct"] is True


def test_grade_wrong_value() -> None:
    result = grade("0.5", BAYES_ITEM)
    assert result["graded"] is True
    assert result["correct"] is False
    assert result["score"] == 0.0


# ---------------------------------------------------------------------------
# 3. 判定 —— gradable=false 显式降级（契约 gradeResult 第二支逐字段）
# ---------------------------------------------------------------------------


def test_grade_not_gradable_downgrade() -> None:
    item = {**BAYES_ITEM, "gradable": False}
    result = grade("随便答的", item)
    assert result == {
        "graded": False,
        "reason": "not_gradable",
        "message": "该题需人工判定",
        "correct": None,
        "grader": "not_gradable",
        "solution": None,
    }


# ---------------------------------------------------------------------------
# 4. 参数校验（R-A）
# ---------------------------------------------------------------------------


def test_grade_answer_over_max_length_rejected() -> None:
    with pytest.raises(AppError) as excinfo:
        grade("9" * 201, BAYES_ITEM)
    assert excinfo.value.code == "invalid_argument"
    assert excinfo.value.http_status == 400


def test_grade_rejects_non_string() -> None:
    with pytest.raises(AppError):
        grade(123, BAYES_ITEM)  # type: ignore[arg-type]


def test_grade_missing_gradable_is_asset_error() -> None:
    bad = {k: v for k, v in BAYES_ITEM.items() if k != "gradable"}
    with pytest.raises(AppError):
        grade("0.0833", bad)


# ---------------------------------------------------------------------------
# 5. 错因匹配 —— 四类正样本 + 负样本（F5 集合语义，不得编造）
# ---------------------------------------------------------------------------


def test_match_direction_reversed() -> None:
    """契约注入集示例 ai_01：P(B|A) vs P(A|B) ⇒ mp_direction_reversed。"""
    assert match_mistake("P(B|A)", SWAP_ITEM) == ["mp_direction_reversed"]


def test_match_direction_reversed_ignores_case_and_spaces() -> None:
    """归一化在入口内：大小写与空白不阻碍命中。"""
    assert match_mistake(" p( b | a ) ", SWAP_ITEM) == ["mp_direction_reversed"]


def test_match_missing_partition() -> None:
    item = {
        **SWAP_ITEM,
        "expected": "P(A1|B)+P(A2|B)",
    }
    assert match_mistake("P(A1|B)", item) == ["mp_missing_partition"]


def test_match_unit_error() -> None:
    item = {
        "prob_id": "prob_unit_0001",
        "stem": "求半径为 5 米的圆的直径。",
        "expected": "10米",
        "kc_ids": ["kc_unit"],
        "pt_id": "pt_unit",
        "gradable": True,
        "answer_kind": "text",
        "difficulty": 1,
    }
    assert match_mistake("10厘米", item) == ["mp_unit_error"]


def test_match_independence_confusion() -> None:
    item = {
        "prob_id": "prob_binom_0007",
        "stem": "将一枚硬币独立重复抛掷 10 次，求某事件概率。",
        "expected": "0.1",
        "kc_ids": ["kc_binomial"],
        "pt_id": "pt_binomial",
        "gradable": True,
        "answer_kind": "value",
        "difficulty": 3,
    }
    assert match_mistake("1", item) == ["mp_independence_confusion"]


def test_match_negative_free_text_is_honest() -> None:
    """负样本（契约 ai_21 同型）：「这道题我不会」⇒ 如实 []，不得编造。"""
    assert match_mistake("这道题我不会", SWAP_ITEM) == []


def test_match_negative_plausible_number_is_honest() -> None:
    """负样本：与期望无规则关系的普通错误数值 ⇒ []（不编造 n 乘错 / 单位错）。"""
    assert match_mistake("0.7", BAYES_ITEM) == []


def test_match_correct_answer_hits_nothing() -> None:
    assert match_mistake("P(A|B)", SWAP_ITEM) == []


def test_match_empty_answer_is_honest() -> None:
    assert match_mistake("", SWAP_ITEM) == []


def test_match_requires_gradable_context_but_is_pure() -> None:
    """match_mistake 不读 gradable（那是判定入口的职责）；缺 stem 也能确定性判定。"""
    minimal = {"expected": "P(A|B)"}
    assert match_mistake("P(B|A)", minimal) == ["mp_direction_reversed"]


# ---------------------------------------------------------------------------
# 6. 防 ReDoS（R-A）：100k 位数字必须毫秒级
# ---------------------------------------------------------------------------


def test_normalize_and_match_long_digit_string_fast() -> None:
    """R-A：100k 位数字必须毫秒级 —— 旧仓 P0 事故的回归用例。

    判定入口的 200 字上界（R-A）本来就会拦截超长答案（见上一个用例），
    因此 ReDoS 防护的正确测试面是**归一化与错因匹配的纯函数层**：
    即便上游漏了上界，正则与数值解析也必须线性、无灾难性回溯。
    """
    monster = "9" * 100_000
    start = time.perf_counter()
    normalized = normalize(monster)
    assert normalized == monster
    hits = match_mistake(monster, BAYES_ITEM)
    elapsed = time.perf_counter() - start
    assert hits == []  # 巨大数值 ≠ 期望，也无任何规则可命中
    assert elapsed < 1.0, f"长数字串处理耗时 {elapsed:.3f}s —— 疑似灾难性回溯（R-A）"


# ---------------------------------------------------------------------------
# 7. 架构约束：domain/ 只依赖 core/ + 标准库（docs/03 §3.1，静态可核对）
# ---------------------------------------------------------------------------

_ALLOWED_ROOT_PACKAGES = frozenset({"app", "__future__"})  # app 下只许 app.core / app.domain 自身


def test_domain_import_whitelist() -> None:
    domain_dir = BACKEND_DIR / "app" / "domain"
    assert domain_dir.is_dir(), "domain/ 目录不存在"
    for source in sorted(domain_dir.glob("*.py")):
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                names = [module] if node.level == 0 else [f".{module}"]  # 相对导入 = 包内
            else:
                continue
            for name in names:
                root = name.split(".")[0]
                if root == "app":
                    # 只允许 app.core / app.domain（禁止 tools/learning/graph/api 等兄弟包）
                    assert name.startswith(("app.core", "app.domain")), (
                        f"{source.name}: 非法 import {name!r} —— domain/ 只依赖 core/（docs/03 §3.1）"
                    )
                elif root == ".":
                    assert name.startswith(".app.domain") or name == ".", (
                        f"{source.name}: 非法相对 import {name!r}"
                    )
                else:
                    assert root in sys.stdlib_module_names, (
                        f"{source.name}: 非法 import {name!r} —— domain/ 只依赖标准库与 core/"
                    )


def test_domain_has_no_llm_references() -> None:
    """F4：判定链无 LLM/网络依赖 —— 基于 AST 检查**真实 import**（文档字符串提及不算违规）。"""
    forbidden = ("openai", "anthropic", "deepseek", "langchain", "langgraph", "requests", "httpx", "urllib")
    for source in (BACKEND_DIR / "app" / "domain").glob("*.py"):
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root = alias.name.split(".")[0]
                    assert root not in forbidden, f"{source.name}: import {alias.name!r} —— 判定链不得触碰 LLM/网络（F4）"
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                root = node.module.split(".")[0]
                assert root not in forbidden, f"{source.name}: from {node.module!r} —— 判定链不得触碰 LLM/网络（F4）"
