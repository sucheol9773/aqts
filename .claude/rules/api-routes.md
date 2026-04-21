---
paths:
  - "backend/api/**/*.py"
  - "backend/db/models/**/*.py"
  - "backend/db/repositories/**/*.py"
  - "backend/alembic/**/*.py"
  - "backend/core/audit/**/*.py"
  - "backend/core/compliance/**/*.py"
  - "backend/core/order_executor/**/*.py"
  - "backend/core/portfolio_manager/**/*.py"
  - "backend/core/idempotency/**/*.py"
  - "backend/core/trading_guard.py"
  - "backend/core/portfolio_ledger.py"
  - "backend/core/data_collector/kis_*.py"
---

# API / RBAC / Security 영역 가드

**소유**: 팀메이트 3 (API / RBAC / Security). 상세: `agent_docs/governance.md §2.3`.
**SSOT**:
- API 계약·RBAC 매트릭스: `agent_docs/api_contracts.md`
- DB 스키마: `agent_docs/database_schema.md`
- 보안 정책: `docs/security/` (특히 `rbac-policy.md`, `supply-chain-policy.md`)

## RBAC Wiring Rule (authn ≠ authz)

- 모든 **mutation 라우트** (`@router.post|put|patch|delete`) 에 `require_operator` 또는 `require_admin` 의존성 명시.
- 모든 **read 라우트** (`@router.get`) 에 `require_viewer` 또는 더 엄격한 가드 명시.
- `Depends(get_current_user)` 직접 사용은 `auth.py` 의 `/me`, `/refresh`, `/logout`, `/mfa/*` 자기 세션 관리 엔드포인트에 한정.
- **RBAC 헬퍼 정의 ≠ 적용**. 정의했다고 적용된 것이 아님. 반드시 실제 라우트에 의존성을 붙여야 함.
- 신규 라우트 PR 시 `docs/security/rbac-policy.md` 권한 매트릭스 동시 갱신.

**강제 검사 절차**:

```bash
python scripts/check_rbac_coverage.py                  # 정적 AST 검사, 0 errors 강제
cd backend && python -m pytest tests/test_rbac_routes.py -q
# 수동: viewer 토큰으로 mutation 라우트 호출 → 403 확인
```

## 공급망 서명 흐름 (읽기 전용 주의)

CI 에서 `pip-audit` + `grype high+` + `syft` SBOM + `cosign sign` keyless (Fulcio/Rekor) + `cosign attest` 수행. CD 는 `cosign verify` 실패 시 즉시 중단. 상세: `agent_docs/development-policies.md §13`.

- `backend/.pip-audit-ignore` 는 만료일 + 사유 필수. 만료된 entry 는 제거 (`docs/security/supply-chain-policy.md`).
- 서명 흐름에 영향을 주는 워크플로 (`.github/workflows/ci.yml`, `cd.yml`) 수정은 팀메이트 2 영역 — `[Ask]` 메일박스로 위임.

## 스키마-코드 동기 (alembic)

- 새 모델/컬럼 추가 시 alembic revision 파일 + `backend/db/models/` 모델 class + 관련 repository 메서드 세 곳이 항상 동기.
- alembic 006 회귀 사례 (schema-code drift) 를 반복하지 않도록, revision 파일은 `backend/db/models/` 변경과 **같은 커밋** 에 포함.
- `agent_docs/database_schema.md` 의 테이블 정의와 revision 파일 간 diff 가 커밋에 같이 있어야 함.

## 절대 규칙

1. **테스트 기대값 수정 금지** (`agent_docs/development-policies.md §1`). RBAC 403/200 기대값은 실제 권한 매트릭스에 맞춰 입력(토큰 role) 을 조정.
2. **하드코딩 금지**. 임계값·키 이름은 `backend/config/` 또는 `core/utils/` 유틸 사용.
3. **Silent miss 의심**: 광범위 `except Exception` 이 새로운 예외를 삼키지 않는지 확인 (`§8`).

## 커밋 전 체크

```bash
cd backend && python -m ruff check . --config pyproject.toml
cd backend && python -m black --check . --config pyproject.toml
python scripts/check_rbac_coverage.py                  # 0 errors 강제
python scripts/check_bool_literals.py
python scripts/check_doc_sync.py --verbose
cd backend && python -m pytest tests/ -q --tb=short   # 540s timeout 권장
```

## 소유권 경계

- `backend/core/utils/`, `backend/config/settings.py`, `.env.example` 은 리드 전용 — `[Lead-Approval]` 메일박스.
- `backend/main.py` lifespan 수정은 팀메이트 2 와 공동 — `[Ask]`.
- `backend/core/notification/`, `monitoring/`, `scheduler*` 는 팀메이트 2 영역.
- `backend/core/strategy_ensemble/`, `backtest_engine/` 등은 팀메이트 1 영역.
- `backend/tests/` 는 팀메이트 4 영역. 단, 본 영역 변경과 **같은 커밋에 관련 테스트 추가/갱신** 은 필수 (팀 4 와 사전 협의 불요).
