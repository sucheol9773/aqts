---
name: aqts-rbac-route-checker
description: Verify RBAC wiring on AQTS API routes after adding or modifying any `@router.{get,post,put,patch,delete}` decorator in `backend/api/`. Use whenever a PR or commit touches route handlers, to ensure `require_viewer` (read) / `require_operator` or `require_admin` (mutation) is attached and that authn != authz is preserved.
license: Apache-2.0
compatibility: Requires Python 3.11+ executed from the AQTS repository root. Depends on the existing `scripts/check_rbac_coverage.py` AST checker (OPS-020) and `tests/test_rbac_routes.py` integration suite.
---

## When to use

Whenever a PR or commit touches `backend/api/**/*.py` route decorators:
- 신규 라우트 추가
- 기존 라우트의 method 변경 (GET → POST 등)
- `Depends(require_*)` 또는 `dependencies=[Depends(...)]` wiring 수정
- WHITELIST 항목 추가/삭제 (`scripts/check_rbac_coverage.py` 의 `WHITELIST` set)

원칙: **인증(authn) ≠ 인가(authz) 분리** — `Depends(get_current_user)` 단독 사용은 인가가 아님 (development-policies.md §14).

## Steps

1. `python scripts/check_rbac_coverage.py` — AST 정적 검사. 0 errors 강제.
2. `cd backend && python -m pytest tests/test_rbac_routes.py -q` — viewer/operator/admin 토큰 통합 테스트 통과.
3. **수동 verification**: viewer JWT 를 직접 생성하여 신규 mutation 라우트(예: `curl -H "Authorization: Bearer <viewer_jwt>" -X POST <new_route>`) 를 호출하고 **403** 응답을 확인.
4. `docs/security/rbac-policy.md` 의 권한 매트릭스에 신규 라우트가 추가되었는지 확인. 누락 시 같은 커밋에 갱신.

## Gotchas (AQTS)

- **G1 한글 기술 서술**: 본 스킬이 PR 설명·커밋 메시지·런북 갱신 시 한글이 SSOT. RBAC 정책 변경은 `docs/security/rbac-policy.md` 에 한글로 기록.
- **G2 테스트 기대값 수정 금지**: step 2 의 `test_rbac_routes.py` 가 viewer 토큰에 대해 403 을 expect 하는데 실제로 200 이 나온다면, **테스트의 expected status 를 200 으로 바꾸지 말 것**. 라우트의 `require_*` 의존성을 추가하여 403 을 복원 (development-policies.md §1).
- **G3 하드코딩 금지**: 수동 verification 에 사용하는 viewer JWT 는 `.env.example` 의 `JWT_SECRET` 키 이름만 인용하여 로컬 helper 로 생성. 실 secret 을 PR 본문/커밋 메시지/스킬 진단 메시지에 절대 포함 금지 (development-policies.md §4).
- **G4 절대 규칙**: step 1~3 중 하나라도 fail 시 머지 금지. 특히 step 3 의 수동 403 확인은 자동화되지 않으므로 **반드시 사람이 직접 호출** 했음을 PR 설명에 명시 (CLAUDE.md §4 RBAC 변경 시 추가 게이트).
- **G5 grep 금지**: 신규 라우트의 의존성 위치 파악 시 `grep -rn "Depends(require_" backend/api/` 대신 Grep tool 사용.
- **G6 RBAC Wiring Rule**: 본 스킬의 핵심 검증 대상. `Depends(get_current_user)` 만 있는 라우트는 `WHITELIST` (자기 세션 관리 / 공개 엔드포인트) 가 아닌 한 위반. mutation 라우트(`POST`/`PUT`/`PATCH`/`DELETE`) 에는 `require_operator` 또는 `require_admin` 필수, read 라우트(`GET`) 에는 `require_viewer` 이상 필수 (development-policies.md §14).
- **G7 KST 통일**: 본 스킬이 audit log 또는 PR 응답 timestamp 를 다룰 때 `today_kst_str()` / `Asia/Seoul` ZoneInfo 사용. RBAC 변경 audit 의 KST 통일은 2026-04-15 회귀(`utcnow().strftime` silent miss) 의 후속 방어선 (CLAUDE.md §5).

## Exit codes

- 0: all RBAC gates passed (정적 + 통합 + 수동 + 정책 문서 동기화).
- 1: 정적 검사 또는 통합 테스트 실패.
- 2: invalid invocation 또는 워크트리 루트 외부 호출.
- 3: 수동 verification 미수행 (PR 설명에 manual 403 confirm 누락) — 본 스킬 자체가 강제할 수 없으므로, 사용자에게 명시적 확인을 요청한 뒤 응답 없으면 exit 3.

## Wiring 검증 — `WHITELIST` 와 `REQUIRE_NAMES` 의 SSOT 일치

`scripts/check_rbac_coverage.py` 의 `WHITELIST: set[tuple[str, str]]` 와 `REQUIRE_NAMES: set[str]` 가 본 스킬의 진단 메시지와 일치하는지 호출 시 한 번 더 확인. 두 변수 변경 시 본 스킬도 동일하게 갱신 (silent drift 방어). 마지막 동기화 검증: **2026-04-25 (W1 mid-week)**.

## SSOT 출처

- `agent_docs/development-policies.md §14` (RBAC Wiring Rule)
- `CLAUDE.md §4` (RBAC 변경 시 추가 게이트)
- `docs/security/rbac-policy.md` (권한 매트릭스)
- `scripts/check_rbac_coverage.py` (AST 정적 검사 SSOT)
- `backend/tests/test_rbac_routes.py` (통합 테스트 SSOT)
