"""scripts/gen_status.py 동작 검증.

CLAUDE.md 규칙: 기대값을 코드에 맞춰 수정하지 않는다. 본 테스트는
gen_status 의 정책(함수 정의 카운트, 마커 치환, changelog 라인 보존)을
직접 호출 가능한 헬퍼 함수에 대해 검증한다.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
GEN_STATUS_PATH = REPO_ROOT / "scripts" / "gen_status.py"


def _load_gen_status():
    spec = importlib.util.spec_from_file_location("gen_status", GEN_STATUS_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["gen_status"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def gen_status():
    return _load_gen_status()


def test_count_test_functions_counts_def_and_async(gen_status, tmp_path):
    p = tmp_path / "test_sample.py"
    p.write_text(
        "def test_a():\n    pass\n"
        "def test_b():\n    pass\n"
        "async def test_c():\n    pass\n"
        "def helper():\n    pass\n",
        encoding="utf-8",
    )
    assert gen_status.count_test_functions(p) == 3


def test_count_test_functions_ignores_non_test(gen_status, tmp_path):
    p = tmp_path / "test_sample.py"
    p.write_text("def helper():\n    pass\n", encoding="utf-8")
    assert gen_status.count_test_functions(p) == 0


def test_rewrite_feature_test_counts_updates_known_file(gen_status):
    text = "| feat | desc | Tested | path | test_foo.py (5) | notes |"
    counts = {"test_foo.py": 12}
    out = gen_status._rewrite_feature_test_counts(text, counts)
    assert "test_foo.py (12)" in out
    assert "(5)" not in out


def test_rewrite_feature_test_counts_keeps_unknown_unchanged(gen_status):
    text = "test_unknown.py (7)"
    out = gen_status._rewrite_feature_test_counts(text, {"test_other.py": 99})
    assert out == text


def test_rewrite_feature_test_counts_supports_subdir_path(gen_status):
    text = "unit/test_inner.py (3)"
    counts = {"unit/test_inner.py": 8}
    out = gen_status._rewrite_feature_test_counts(text, counts)
    assert "unit/test_inner.py (8)" in out


def test_rewrite_total_tests_total_tests_label(gen_status):
    text = "Total Tests: 3,088 tests (413 smoke)"
    out = gen_status._rewrite_total_tests(text, 3187)
    assert "Total Tests: 3,187 tests" in out


def test_rewrite_total_tests_readme_pattern(gen_status):
    text = "# 전체 테스트 (3,088 tests)"
    out = gen_status._rewrite_total_tests(text, 3187)
    assert "전체 테스트 (3,187 tests)" in out


def test_rewrite_total_tests_readme_tree_comment(gen_status):
    text = "# 3,088 tests (전체 통과)"
    out = gen_status._rewrite_total_tests(text, 3187)
    assert "# 3,187 tests (전체 통과)" in out


def test_rewrite_total_tests_release_gate_pass_line(gen_status):
    text = "| pytest 0 failures | PASS (3,088건 통과) |"
    out = gen_status._rewrite_total_tests(text, 3187)
    assert "PASS (3,187건 통과)" in out


def test_rewrite_release_gate_a_summary(gen_status):
    text = "Gate A: PASS (스트레스 테스트 28건 추가, 3,088건 통과, 90% 커버리지)"
    out = gen_status._rewrite_release_gate_a_pass(text, 3187)
    assert "3,187건 통과" in out
    assert "스트레스 테스트 28건 추가" in out
    assert "90% 커버리지" in out


def test_rewrite_does_not_touch_changelog_history(gen_status):
    """changelog (v1.x) 라인은 절대 건드리지 않는다."""
    text = (
        "- v1.27 (2026-04-07): 환경변수 bool 표기 표준화 — ... 테스트 3,200건\n"
        "- v1.26 (2026-04-07): OpenTelemetry ... 테스트 3,166건\n"
    )
    out = gen_status._rewrite_total_tests(text, 9999)
    out = gen_status._rewrite_release_gate_a_pass(out, 9999)
    assert "3,200건" in out
    assert "3,166건" in out
    assert "9,999" not in out


def test_collect_per_file_counts_includes_self(gen_status):
    counts = gen_status.collect_per_file_counts()
    assert "test_gen_status.py" in counts
    assert counts["test_gen_status.py"] >= 12


def test_total_tests_matches_sum(gen_status):
    counts = gen_status.collect_per_file_counts()
    assert gen_status.total_tests(counts) == sum(counts.values())


def test_compute_diffs_idempotent_after_update(gen_status):
    """현재 상태에서 compute_diffs() 가 변경 없음을 보고해야 한다."""
    diffs = gen_status.compute_diffs()
    changed = [d for d in diffs if d.changed]
    assert changed == [], f"unexpected diffs: {[d.path.name for d in changed]}"
