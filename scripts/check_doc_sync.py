#!/usr/bin/env python3
"""
AQTS Document-Code-Test Sync Checker (Stage 1)
===============================================
FEATURE_STATUS.md와 실제 코드/테스트 상태를 자동 비교하여
불일치를 감지합니다.

검증 항목:
  1. FEATURE_STATUS.md에 기재된 Code Path가 실제로 존재하는가
  2. 테스트 파일/수가 실제와 일치하는가
  3. README.md의 '구현 기능'이 FEATURE_STATUS.md에서 Tested 이상인가
  4. 구현률(Tested 이상 / 전체) 자동 산출

Usage:
  python scripts/check_doc_sync.py [--verbose] [--strict]
"""

import ast
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# ─── Configuration ───

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = PROJECT_ROOT / "backend"
DOCS_DIR = PROJECT_ROOT / "docs"
FEATURE_STATUS_PATH = DOCS_DIR / "FEATURE_STATUS.md"
README_PATH = PROJECT_ROOT / "README.md"

VALID_STATUSES = {
    "Not Started", "In Progress", "Implemented",
    "Tested", "Production-ready", "Blocked"
}
TESTED_OR_ABOVE = {"Tested", "Production-ready"}


# ─── Data Structures ───

@dataclass
class FeatureRow:
    module: str
    feature: str
    status: str
    code_path: str
    tests: str
    notes: str
    line_number: int = 0


@dataclass
class SyncIssue:
    severity: str  # ERROR, WARNING
    category: str  # CODE_PATH, TEST_COUNT, STATUS, README
    message: str
    file: str = ""
    line: int = 0


@dataclass
class SyncReport:
    issues: list = field(default_factory=list)
    total_features: int = 0
    tested_count: int = 0
    implemented_count: int = 0
    not_started_count: int = 0
    implementation_rate: float = 0.0


# ─── Parser ───

def parse_feature_status(path: Path) -> list[FeatureRow]:
    """FEATURE_STATUS.md에서 테이블 행을 파싱합니다."""
    rows = []
    if not path.exists():
        return rows

    content = path.read_text(encoding="utf-8")
    lines = content.split("\n")

    # 테이블 행 패턴: | col1 | col2 | ... |
    table_re = re.compile(
        r"^\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|$"
    )
    separator_re = re.compile(r"^\|[\s\-:]+\|")
    header_keywords = {"Module", "Feature", "Status", "Code Path", "Tests", "Notes",
                       "Item", "Contract", "Gate", "Component"}

    for i, line in enumerate(lines, 1):
        line = line.strip()
        if not line.startswith("|"):
            continue
        if separator_re.match(line):
            continue

        m = table_re.match(line)
        if not m:
            continue

        cols = [c.strip() for c in m.groups()]

        # 헤더 행 스킵
        if any(kw in cols[0] for kw in header_keywords) and any(kw in cols[2] for kw in {"Status"}):
            continue
        if cols[0] in header_keywords:
            continue

        status = cols[2]
        if status not in VALID_STATUSES:
            continue

        rows.append(FeatureRow(
            module=cols[0],
            feature=cols[1],
            status=status,
            code_path=cols[3],
            tests=cols[4],
            notes=cols[5],
            line_number=i
        ))

    return rows


# ─── Check 1: Code Path Existence ───

def check_code_paths(rows: list[FeatureRow], report: SyncReport):
    """Code Path가 실제로 존재하는지 확인합니다."""
    for row in rows:
        if row.code_path in ("(pending)", "(no file)", "N/A", "(none)"):
            if row.status != "Not Started":
                report.issues.append(SyncIssue(
                    severity="WARNING",
                    category="CODE_PATH",
                    message=f"[{row.module}] Status is '{row.status}' but Code Path is '{row.code_path}'",
                    file=str(FEATURE_STATUS_PATH),
                    line=row.line_number
                ))
            continue

        # 실제 파일 존재 확인
        full_path = BACKEND_DIR / row.code_path
        if not full_path.exists():
            # config/ 등 다른 위치 시도
            alt_path = PROJECT_ROOT / row.code_path
            if not alt_path.exists():
                report.issues.append(SyncIssue(
                    severity="ERROR",
                    category="CODE_PATH",
                    message=f"[{row.module}] Code path '{row.code_path}' does not exist",
                    file=str(FEATURE_STATUS_PATH),
                    line=row.line_number
                ))


# ─── Check 2: Test Count Validation ───

def count_test_functions(test_path: Path) -> int:
    """테스트 파일에서 test_ 함수 수를 카운트합니다."""
    if not test_path.exists():
        return 0
    try:
        content = test_path.read_text(encoding="utf-8")
        return len(re.findall(r"^\s*(?:async\s+)?def\s+test_", content, re.MULTILINE))
    except Exception:
        return 0


def check_test_counts(rows: list[FeatureRow], report: SyncReport):
    """테스트 파일과 수가 실제와 일치하는지 확인합니다."""
    test_re = re.compile(r"(test_\w+\.py)\s*\((\d+)\)")

    for row in rows:
        if row.tests in ("N/A", "(no tests)", "(none)", ""):
            if row.status in TESTED_OR_ABOVE:
                report.issues.append(SyncIssue(
                    severity="ERROR",
                    category="TEST_COUNT",
                    message=f"[{row.module}] Status is '{row.status}' but no tests specified",
                    file=str(FEATURE_STATUS_PATH),
                    line=row.line_number
                ))
            continue

        m = test_re.search(row.tests)
        if not m:
            continue

        test_file = m.group(1)
        doc_count = int(m.group(2))
        test_path = BACKEND_DIR / "tests" / test_file
        actual_count = count_test_functions(test_path)

        if not test_path.exists():
            report.issues.append(SyncIssue(
                severity="ERROR",
                category="TEST_COUNT",
                message=f"[{row.module}] Test file '{test_file}' does not exist",
                file=str(FEATURE_STATUS_PATH),
                line=row.line_number
            ))
        elif abs(actual_count - doc_count) > max(doc_count * 0.1, 2):
            report.issues.append(SyncIssue(
                severity="WARNING",
                category="TEST_COUNT",
                message=f"[{row.module}] Test count mismatch: doc says {doc_count}, actual is {actual_count} in {test_file}",
                file=str(FEATURE_STATUS_PATH),
                line=row.line_number
            ))


# ─── Check 3: README Consistency ───

def parse_readme_features(path: Path) -> list[str]:
    """README.md에서 구현 기능 목록을 추출합니다."""
    features = []
    if not path.exists():
        return features

    content = path.read_text(encoding="utf-8")

    # Phase 테이블에서 기능명 추출
    phase_re = re.compile(r"\|\s*(.+?\.py)\s*\|")
    for m in phase_re.finditer(content):
        features.append(m.group(1).strip())

    # 프로젝트 구조에서 .py 파일 추출
    py_re = re.compile(r"[├└│─\s]+(\w+\.py)")
    for m in py_re.finditer(content):
        features.append(m.group(1).strip())

    return list(set(features))


def check_readme_consistency(rows: list[FeatureRow], report: SyncReport):
    """README에서 언급된 기능이 FEATURE_STATUS.md에서 Tested 이상인지 확인합니다."""
    readme_features = parse_readme_features(README_PATH)

    # FEATURE_STATUS의 code path에서 파일명 추출
    status_map = {}
    for row in rows:
        if row.code_path not in ("(pending)", "(no file)", "N/A", "(none)"):
            filename = Path(row.code_path).name
            status_map[filename] = row.status

    for feat in readme_features:
        if feat in status_map:
            status = status_map[feat]
            if status not in TESTED_OR_ABOVE and status != "Implemented":
                report.issues.append(SyncIssue(
                    severity="WARNING",
                    category="README",
                    message=f"README mentions '{feat}' but FEATURE_STATUS status is '{status}'",
                    file=str(README_PATH)
                ))


# ─── Check 4: Status Validity ───

def check_status_validity(rows: list[FeatureRow], report: SyncReport):
    """Status 값이 유효한지 확인합니다."""
    for row in rows:
        if row.status not in VALID_STATUSES:
            report.issues.append(SyncIssue(
                severity="ERROR",
                category="STATUS",
                message=f"[{row.module}] Invalid status '{row.status}'",
                file=str(FEATURE_STATUS_PATH),
                line=row.line_number
            ))


# ─── Report ───

def compute_stats(rows: list[FeatureRow], report: SyncReport):
    """구현률 통계를 산출합니다."""
    report.total_features = len(rows)
    report.tested_count = sum(1 for r in rows if r.status in TESTED_OR_ABOVE)
    report.implemented_count = sum(1 for r in rows if r.status == "Implemented")
    report.not_started_count = sum(1 for r in rows if r.status == "Not Started")

    total_implemented = report.tested_count + report.implemented_count
    if report.total_features > 0:
        report.implementation_rate = total_implemented / report.total_features * 100


def print_report(report: SyncReport, verbose: bool = False):
    """검증 결과를 출력합니다."""
    print("=" * 60)
    print("  AQTS Document-Code-Test Sync Report")
    print("=" * 60)
    print()

    # Stats
    print(f"  Total features:      {report.total_features}")
    print(f"  Tested or above:     {report.tested_count}")
    print(f"  Implemented:         {report.implemented_count}")
    print(f"  Not Started:         {report.not_started_count}")
    print(f"  Implementation rate: {report.implementation_rate:.1f}%")
    print()

    errors = [i for i in report.issues if i.severity == "ERROR"]
    warnings = [i for i in report.issues if i.severity == "WARNING"]

    print(f"  Errors:   {len(errors)}")
    print(f"  Warnings: {len(warnings)}")
    print()

    if errors:
        print("─── ERRORS ───")
        for issue in errors:
            loc = f" (line {issue.line})" if issue.line else ""
            print(f"  [{issue.category}] {issue.message}{loc}")
        print()

    if warnings and verbose:
        print("─── WARNINGS ───")
        for issue in warnings:
            loc = f" (line {issue.line})" if issue.line else ""
            print(f"  [{issue.category}] {issue.message}{loc}")
        print()

    # Final verdict
    if not errors:
        print("✓ SYNC CHECK PASSED")
        if warnings:
            print(f"  ({len(warnings)} warnings — run with --verbose to see)")
    else:
        print(f"✗ SYNC CHECK FAILED ({len(errors)} errors)")

    return len(errors) == 0


# ─── Main ───

def main():
    verbose = "--verbose" in sys.argv or "-v" in sys.argv
    strict = "--strict" in sys.argv

    if not FEATURE_STATUS_PATH.exists():
        print(f"ERROR: {FEATURE_STATUS_PATH} not found")
        print("Run Stage 1 to create FEATURE_STATUS.md first.")
        sys.exit(1)

    rows = parse_feature_status(FEATURE_STATUS_PATH)
    if not rows:
        print("ERROR: No feature rows found in FEATURE_STATUS.md")
        sys.exit(1)

    report = SyncReport()

    # Run all checks
    check_status_validity(rows, report)
    check_code_paths(rows, report)
    check_test_counts(rows, report)
    check_readme_consistency(rows, report)
    compute_stats(rows, report)

    passed = print_report(report, verbose)

    if strict and report.issues:
        sys.exit(1)
    elif not passed:
        sys.exit(1)
    else:
        sys.exit(0)


if __name__ == "__main__":
    main()
