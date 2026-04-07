#!/usr/bin/env python3
"""문서 SSOT 자동화 — 테스트 수/기능 수 자동 동기화.

FEATURE_STATUS.md, README.md, release-gates.md 의 "현재" 테스트 수치를
실제 코드에서 산출한 값으로 자동 갱신한다. 변경 이력(changelog) 라인은
건드리지 않는다.

산출 규칙
---------
- 단일 기능 테스트 수 (`test_xxx.py (N)`): backend/tests/test_xxx.py 안의
  ``def test_`` / ``async def test_`` 함수 수. parametrize 확장은 무시
  (doc-sync 와 동일 의미).
- 총 테스트 수 (Total Tests): backend/tests/**/test_*.py 모든 파일의
  함수 수 합계.
- 자동 갱신 대상 (마커 또는 알려진 라인 패턴):
  · FEATURE_STATUS.md  : 표 안의 ``test_xxx.py (N)``, "Total Tests: N tests"
  · README.md          : "tests/" 트리 코멘트와 "전체 테스트 (N tests)"
  · release-gates.md   : "Gate A 단위 테스트 전체 통과" 라인의 (N건),
                         "Gate A: PASS (... N건 통과 ...)" 라인.
                         changelog (v1.x) 라인은 절대 건드리지 않음.

Usage
-----
    python scripts/gen_status.py            # --check (기본): 변경 필요 시 exit 1
    python scripts/gen_status.py --update   # 실제로 파일을 수정
    python scripts/gen_status.py --print    # 산출값만 출력
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKEND_TESTS = ROOT / "backend" / "tests"
FEATURE_STATUS = ROOT / "docs" / "FEATURE_STATUS.md"
README = ROOT / "README.md"
RELEASE_GATES = ROOT / "docs" / "operations" / "release-gates.md"


# ── 산출 ────────────────────────────────────────────────


_TEST_DEF_RE = re.compile(r"^\s*(?:async\s+)?def\s+test_", re.MULTILINE)


def count_test_functions(path: Path) -> int:
    try:
        return len(_TEST_DEF_RE.findall(path.read_text(encoding="utf-8")))
    except Exception:
        return 0


def collect_per_file_counts() -> dict[str, int]:
    counts: dict[str, int] = {}
    for path in BACKEND_TESTS.rglob("test_*.py"):
        rel = path.relative_to(BACKEND_TESTS).as_posix()
        counts[rel] = count_test_functions(path)
    return counts


def total_tests(counts: dict[str, int]) -> int:
    return sum(counts.values())


# ── 갱신 ────────────────────────────────────────────────


@dataclass
class Diff:
    path: Path
    before: str
    after: str

    @property
    def changed(self) -> bool:
        return self.before != self.after


def _format_total(n: int) -> str:
    return f"{n:,}"


_FEATURE_TEST_RE = re.compile(r"((?:[\w/]+/)?test_\w+\.py)\s*\((\d+)\)")


def _rewrite_feature_test_counts(text: str, counts: dict[str, int]) -> str:
    def repl(m: re.Match[str]) -> str:
        fname = m.group(1)
        # FEATURE_STATUS 내 경로 형태(예: test_api.py / unit/test_x.py)와
        # collect_per_file_counts 의 키(상대 경로) 둘 다 처리.
        actual = counts.get(fname)
        if actual is None:
            # 마지막 파일명만 일치하는 경우 fallback
            for k, v in counts.items():
                if k.endswith(fname):
                    actual = v
                    break
        if actual is None:
            return m.group(0)
        return f"{fname} ({actual})"

    return _FEATURE_TEST_RE.sub(repl, text)


def _rewrite_total_tests(text: str, total: int) -> str:
    formatted = _format_total(total)
    # FEATURE_STATUS.md / release-gates.md / README 공통 패턴
    patterns = [
        (re.compile(r"Total Tests:\s*[\d,]+\s*tests"), f"Total Tests: {formatted} tests"),
        (re.compile(r"전체 테스트\s*\(\s*[\d,]+\s*tests\s*\)"), f"전체 테스트 ({formatted} tests)"),
        (re.compile(r"#\s*[\d,]+\s+tests\s*\(전체 통과\)"), f"# {formatted} tests (전체 통과)"),
        (
            re.compile(r"pytest 0 failures\s*\|\s*PASS\s*\(\s*[\d,]+건 통과\s*\)"),
            f"pytest 0 failures | PASS ({formatted}건 통과)",
        ),
    ]
    out = text
    for pat, replacement in patterns:
        out = pat.sub(replacement, out)
    return out


def _rewrite_release_gate_a_pass(text: str, total: int) -> str:
    """release-gates.md 의 'Gate A: PASS (...)' 요약 라인만 갱신."""

    formatted = _format_total(total)
    pat = re.compile(r"(Gate A: PASS \([^)]*?)([\d,]+)건 통과([^)]*\))")

    def repl(m: re.Match[str]) -> str:
        return f"{m.group(1)}{formatted}건 통과{m.group(3)}"

    return pat.sub(repl, text)


def compute_diffs() -> list[Diff]:
    counts = collect_per_file_counts()
    total = total_tests(counts)

    diffs: list[Diff] = []

    fs_text = FEATURE_STATUS.read_text(encoding="utf-8")
    fs_new = _rewrite_feature_test_counts(fs_text, counts)
    fs_new = _rewrite_total_tests(fs_new, total)
    diffs.append(Diff(FEATURE_STATUS, fs_text, fs_new))

    readme_text = README.read_text(encoding="utf-8")
    readme_new = _rewrite_total_tests(readme_text, total)
    diffs.append(Diff(README, readme_text, readme_new))

    rg_text = RELEASE_GATES.read_text(encoding="utf-8")
    rg_new = _rewrite_total_tests(rg_text, total)
    rg_new = _rewrite_release_gate_a_pass(rg_new, total)
    diffs.append(Diff(RELEASE_GATES, rg_text, rg_new))

    return diffs


# ── CLI ─────────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--update", action="store_true", help="실제로 파일 수정")
    parser.add_argument("--print", dest="print_only", action="store_true", help="산출값만 출력")
    args = parser.parse_args()

    counts = collect_per_file_counts()
    total = total_tests(counts)

    if args.print_only:
        print(f"total_tests = {total}")
        print(f"test_files  = {len(counts)}")
        return 0

    diffs = compute_diffs()
    changed = [d for d in diffs if d.changed]

    if args.update:
        for d in changed:
            d.path.write_text(d.after, encoding="utf-8")
            print(f"updated {d.path.relative_to(ROOT)}")
        if not changed:
            print("no changes (already in sync)")
        print(f"total_tests = {total}")
        return 0

    if changed:
        print("✗ STATUS DOC OUT OF SYNC")
        for d in changed:
            print(f"  - {d.path.relative_to(ROOT)} would be updated")
        print(f"\n  total_tests = {total}")
        print("  Run: python scripts/gen_status.py --update")
        return 1

    print(f"✓ STATUS DOCS IN SYNC (total_tests = {total})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
