---
paths:
  - "backend/tests/**/*.py"
  - "scripts/check_*.py"
  - "scripts/post_deploy_smoke.sh"
  - "scripts/pre_deploy_check.sh"
  - "scripts/gen_status.py"
---

# 테스트 / Doc-Sync / 정적 검사 영역 가드

**소유**: 팀메이트 4 (Tests / Doc-Sync / Static Checkers). 상세: `agent_docs/governance.md §2.4`.
**SSOT**: 본 경로의 규칙은 `CLAUDE.md` / `agent_docs/development-policies.md §1,§3,§8,§9` 요약입니다.

## 절대 규칙

- **테스트 기대값 수정 절대 금지** (development-policies.md §1). 기능이 실제로 기대하는 값만 통과해야 하며, 단순 테스트 통과를 위한 기대값 수정은 절대 허용되지 않습니다. 오류 발생 시 기대값이 아니라 입력값/로직을 조정합니다.
- **Silent miss 의심** (development-policies.md §8). 수정 전에는 실패하던 것이 수정 후 조용히 `None`/skip 경로로 빠져나간 것은 아닌지 반드시 점검합니다. 특히 테스트가 "데이터 부재 → skip" 경로만 커버하고 "데이터 존재 → 처리" 경로를 커버하지 않으면 silent miss 를 잡을 수 없습니다.
- **CI/CD 검증 결과 전수 처리** (development-policies.md §9). warning 이든 error 든 발견 시점이 수정 시점. "기존부터 있던 warning 이라 이번 범위가 아니다" 로 넘기지 않습니다.
- **런타임 환경 재현 의무**. multiprocessing, 외부 프로세스 실행, 직렬화(pickle) 등 런타임 환경에 의존하는 코드는 실제 실행 환경을 재현하는 테스트를 포함합니다 (예: `Pool.map()` 실제 호출).

## Wiring Rule — 정적 검사기 결손 패턴

`check_*.py` 를 추가/수정할 때는 regex 기반 휴리스틱이 아닌 **AST 기반**으로 구현합니다. regex 검사는 다음 패턴에서 결손이 발생했습니다:

- `check_loguru_style.py` — `logger.info("...%d...", n)` posarg 감지에 regex 가 실패 (2026-04-15 회귀)
- `check_bool_literals.py` — 환경변수 bool 파싱 ad-hoc 코드 탐지
- `check_rbac_coverage.py` — 라우터 mutation 데코레이터의 `require_*` 의존성 누락 탐지

정적 검사기를 수정할 때는 반드시 다음을 확인:
1. 새 검사 규칙이 기존 회귀 사례를 재현 테스트로 커버하는가
2. 검사기가 false negative (놓침) 아닌 false positive 로 기우는가
3. CI 에서 0 errors + 0 warnings 상태를 강제하는가

## 커밋 전 체크

```bash
cd backend && python -m ruff check . --config pyproject.toml
cd backend && python -m black --check . --config pyproject.toml
cd backend && python -m pytest tests/ -q --tb=short   # timeout ≥ 540s
python scripts/check_bool_literals.py
python scripts/check_doc_sync.py --verbose
python scripts/check_rbac_coverage.py                  # RBAC 관련 변경 시
```

**pytest 타임아웃 원칙**: 최근 관측 러너 시간의 1.5배 이상. 직전 기준선(2026-04-09) 3667 passed ≈ 349s → 최소 540s 권장 (development-policies.md §3).

## 문서-only 커밋 예외

코드 변경이 전혀 없고 `.md` / `.env.example` / 문서성 yaml 주석만 수정되는 경우 전체 `pytest tests/` 생략 가능. 판정 기준은 `git diff --stat` 에 `.py`/`.toml`/`.sh`/`Dockerfile*`/`.github/workflows/*.yml` 이 단 한 줄도 없을 것. 상세: development-policies.md §3.1.

## 소유권 경계

- **팀메이트 4 소유**: `backend/tests/**/*.py`, `scripts/check_*.py`, smoke/pre-deploy 스크립트, `scripts/gen_status.py`, `docs/FEATURE_STATUS.md`, `docs/PRD.md`
- **리드 전용**: 본 파일(`CLAUDE.md`) 및 `agent_docs/development-policies.md` 는 수정 금지. 규칙 변경 필요 시 메일박스로 리드에게 위임합니다.
- 다른 팀메이트의 코드(`backend/core/...`, `backend/api/...`)에 직접 테스트를 추가할 때도, 실제 코드 수정은 해당 팀에게 위임하거나 사전 협의합니다.
