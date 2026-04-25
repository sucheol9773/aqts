#!/usr/bin/env python3
"""소유권 경계 위반 정적 검사기.

정책: ``agent_docs/governance.md §2`` 팀 소유권 매트릭스.

검사 항목:
1. 현재 브랜치 이름에서 팀 번호를 추출 (``team{N}/`` prefix).
2. ``git diff --name-only origin/main...HEAD`` 로 변경 파일 목록을 산출.
3. 각 변경 파일이 해당 팀의 소유 경로에 포함되는지 판정.
4. 리드 전용 파일(governance.md §2.5)을 팀원이 수정한 경우 error.
5. 다른 팀 소유 파일을 수정한 경우 error.

Exit code: 0 = PASS, 1 = FAIL, 2 = SKIP (리드 브랜치 등 팀 판별 불가).
"""

from __future__ import annotations

import fnmatch
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# ── governance.md §2.5 리드 전용 파일 ──
LEAD_ONLY: list[str] = [
    "CLAUDE.md",
    "agent_docs/development-policies.md",
    "agent_docs/governance.md",
    "backend/config/settings.py",
    "backend/core/utils/env.py",
    "backend/core/utils/time.py",
    ".env.example",
    "docs/archive/**",
]

# ── governance.md §2.1–§2.4 팀 소유권 매핑 ──
# 각 패턴은 fnmatch glob 으로 평가된다.
OWNERSHIP: dict[int, list[str]] = {
    1: [
        "backend/core/strategy_ensemble/**",
        "backend/core/backtest_engine/**",
        "backend/core/oos/**",
        "backend/core/hyperopt/**",
        "backend/core/param_sensitivity/**",
        "backend/core/quant_engine/**",
        "backend/core/weight_optimizer.py",
        "backend/config/ensemble_config.yaml",
        "backend/config/ensemble_config_loader.py",
        "scripts/run_backtest.py",
        "scripts/run_hyperopt.py",
        "scripts/run_walk_forward.py",
    ],
    2: [
        "backend/scheduler_main.py",
        "backend/core/trading_scheduler.py",
        "backend/core/scheduler_handlers.py",
        "backend/core/scheduler_heartbeat.py",
        "backend/core/scheduler_idempotency.py",
        "backend/core/market_calendar.py",
        "backend/core/periodic_reporter.py",
        "backend/core/daily_reporter.py",
        "backend/core/reconciliation*.py",
        "backend/core/notification/**",
        "backend/core/monitoring/**",
        "backend/core/emergency_monitor.py",
        "backend/core/circuit_breaker.py",
        "backend/core/graceful_shutdown.py",
        "backend/core/health_checker.py",
        "docker-compose*.yml",
        "monitoring/prometheus/**",
        "monitoring/alertmanager/**",
        ".github/workflows/*.yml",
    ],
    3: [
        "backend/main.py",
        "backend/api/**",
        "backend/db/**",
        "backend/alembic/**",
        "backend/core/audit/**",
        "backend/core/compliance/**",
        "backend/core/order_executor/**",
        "backend/core/trading_guard.py",
        "backend/core/portfolio_manager/**",
        "backend/core/portfolio_ledger.py",
        "backend/core/idempotency/**",
        "backend/core/data_collector/**",
    ],
    4: [
        "backend/tests/**",
        "scripts/check_*.py",
        "scripts/post_deploy_smoke.sh",
        "scripts/pre_deploy_check.sh",
        "scripts/gen_status.py",
        "docs/FEATURE_STATUS.md",
        "docs/PRD.md",
        "docs/YAML_CONFIG_GUIDE.md",
        "docs/conventions/**",
        "docs/backtest/**",
        "docs/operations/**",
    ],
}

# ── 모든 팀이 수정 가능한 공유 경로 ──
SHARED_PATHS: list[str] = [
    "agent_docs/mailboxes/**",
    "*.md",  # 루트 레벨 README 등 (CLAUDE.md 는 리드 전용으로 별도 차단)
]

# ── 리드 전용이지만 SHARED_PATHS 에 잡히는 것을 방지 ──
# LEAD_ONLY 가 SHARED_PATHS 보다 우선한다.


def get_current_team() -> int | None:
    """현재 브랜치에서 팀 번호를 추출. team{N}/ prefix 가 없으면 None."""
    try:
        branch = subprocess.check_output(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=ROOT,
            text=True,
        ).strip()
    except subprocess.CalledProcessError:
        return None

    m = re.match(r"team(\d+)/", branch)
    if m:
        return int(m.group(1))
    return None


def get_changed_files(base: str = "origin/main") -> list[str]:
    """base 브랜치 대비 변경된 파일 목록."""
    try:
        output = subprocess.check_output(
            ["git", "diff", "--name-only", f"{base}...HEAD"],
            cwd=ROOT,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except subprocess.CalledProcessError:
        return []
    if not output:
        return []
    return output.splitlines()


def matches_any(filepath: str, patterns: list[str]) -> bool:
    """파일 경로가 패턴 목록 중 하나에 매치되는지 판정."""
    for pattern in patterns:
        if fnmatch.fnmatch(filepath, pattern):
            return True
        # ** 패턴 지원: fnmatch 는 / 를 넘지 못하므로 수동 처리
        if "**" in pattern:
            prefix = pattern.split("**")[0]
            if filepath.startswith(prefix):
                return True
    return False


def check_ownership(team: int, files: list[str]) -> list[str]:
    """소유권 위반 파일 목록 반환."""
    errors: list[str] = []
    own_patterns = OWNERSHIP.get(team, [])

    for f in files:
        # 1) 리드 전용 파일 체크 (최우선)
        if matches_any(f, LEAD_ONLY):
            errors.append(f"LEAD-ONLY: {f} (governance.md §2.5 — 리드만 수정 가능)")
            continue

        # 2) 자기 팀 소유 경로에 해당하면 OK
        if matches_any(f, own_patterns):
            continue

        # 3) 공유 경로 체크
        if matches_any(f, SHARED_PATHS):
            continue

        # 4) 다른 팀 소유인지 확인
        owner_team = None
        for t, patterns in OWNERSHIP.items():
            if t != team and matches_any(f, patterns):
                owner_team = t
                break

        if owner_team is not None:
            errors.append(f"BOUNDARY: {f} (팀 {owner_team} 소유 — 메일박스로 위임 필요)")
        # 소유자 미지정 파일은 경고하지 않음 (새 파일 등)

    return errors


def main(argv: list[str] | None = None) -> int:
    args = argv or sys.argv[1:]

    # --allow-cross: 교차 편집 허용 (메일박스 승인 후 사용)
    allow_cross = "--allow-cross" in args

    team = get_current_team()
    if team is None:
        print("SKIP: 팀 브랜치가 아닙니다 (team{N}/ prefix 없음). 소유권 검사 생략.")
        return 2

    if team not in OWNERSHIP:
        print(f"SKIP: 알 수 없는 팀 번호 {team}. 소유권 검사 생략.")
        return 2

    files = get_changed_files()
    if not files:
        print(f"팀 {team}: 변경 파일 없음. PASS.")
        return 0

    errors = check_ownership(team, files)

    if allow_cross:
        # --allow-cross 시 LEAD-ONLY 위반만 유지, BOUNDARY 위반은 경고로 전환
        real_errors = [e for e in errors if e.startswith("LEAD-ONLY:")]
        warnings = [e for e in errors if e.startswith("BOUNDARY:")]
        for w in warnings:
            print(f"  WARNING (--allow-cross): {w}")
        errors = real_errors

    if errors:
        print(f"팀 {team}: 소유권 경계 위반 {len(errors)}건:\n")
        for e in errors:
            print(f"  ERROR: {e}")
        print(
            f"\n✗ OWNERSHIP CHECK FAILED — {len(errors)} violation(s). "
            "교차 편집이 필요하면 메일박스로 담당 팀에 위임하세요."
        )
        return 1

    print(f"✓ OWNERSHIP CHECK PASSED — 팀 {team}, {len(files)} file(s) checked.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
