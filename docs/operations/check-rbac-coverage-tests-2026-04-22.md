# `check_rbac_coverage.py` 회귀 테스트 하니스 — 2026-04-22

> **문서 번호**: OPS-020
>
> **목적**: RBAC 가드 정적 검사기(`scripts/check_rbac_coverage.py`) 에 21 개 회귀 테스트를 추가하여 규칙 drift 와 silent miss 를 구조적으로 차단한다. Phase 1 Path A 의 마지막 단계로, Stage 1 (`iter_python_files` SSOT, OPS-017) → Stage 2 (`check_bool_literals` AST 전환, OPS-019) 의 연장선이다.

---

## 1. 배경

`check_rbac_coverage.py` 는 9위 RBAC 작업 후속 방어선으로 2026 년 초에 도입되었다 (CLAUDE.md "인증 ≠ 인가 분리 원칙" §RBAC Wiring Rule). 구조적으로는 이미 AST 기반이었으나 **검사기 자체에 대한 테스트 하니스가 전무**했다. 이는 다음과 같은 위험을 남겨 두었다.

1. **규칙 drift 의 silent miss**: WHITELIST 에 항목을 추가하거나 `MUTATION_METHODS` 를 수정했을 때 의도와 실제 동작이 엇갈려도 알 길이 없다. 기존에는 "레포 전체에 대해 0 errors" 를 CI 에서 확인하는 것으로 대체했으나, 이 지표는 검사기가 *탐지를 놓쳤을 때* 와 *실제로 위반이 없을 때* 를 구분하지 못한다.
2. **false negative 확률의 누적**: 가드를 새로 정의하거나 이름을 바꾸면 `REQUIRE_NAMES` 와의 동기화가 깨질 수 있다. 테스트가 없으면 "라우트에는 가드를 달았지만 검사기는 인식 못함 → 레포는 통과하지만 실제로는 아무 라우트도 가드 검증이 안 됨" 이라는 시나리오가 성립 가능하다.
3. **refactor 장벽**: 검사기 자체를 리팩토링하려면 "실제 저장소에 돌렸을 때 결과가 같다" 수준만 확인 가능했다. 엣지 케이스별 동작 보장이 없어 내부 구조 변경이 리스크였다.

Stage 2 (`check_bool_literals`) 는 regex → AST 구조 자체를 바꾸는 이전이었고, Stage 3 은 **기존 AST 구현 위에 테스트 하니스만 추가**하여 이후의 모든 규칙 변경에 대한 회귀 방어선을 확보하는 작업이다.

### 1.1 Stage 2 와의 관계

Stage 2 (`test_check_bool_literals.py`) 는 27 개 테스트로 regex → AST 전환의 동치성을 증빙하면서, 동시에 AST 전환이 "기존 regex 가 놓치던 4 가지 결손" 을 자연스럽게 해소한다는 사실을 고정했다. Stage 3 은 같은 6 그룹 패턴(**하위 호환 / 위반 검출 / 오탐 방지 / 구문 오류 / 실제 레포 / main() 진입**) 을 `check_rbac_coverage.py` 에 적용한다. 두 검사기의 테스트 하니스가 같은 구조를 공유함으로써 이후 유지보수자가 한쪽을 이해하면 다른 쪽도 즉시 이해 가능한 mental model 을 갖는다.

---

## 2. 설계 목표

1. **정책 하위 호환의 실행 가능한 계약**: RBAC 정책 — "mutation 은 `require_operator|admin`, read 는 `require_viewer` 이상" — 을 자연어 문서가 아닌 실행 가능한 assertion 으로 고정한다. 파라미터 `Depends(...)` 와 데코레이터 `dependencies=[Depends(...)]` 두 wiring 경로 모두 커버한다.
2. **위반 검출의 민감도 검증**: 검사기가 *잡아야 할 것을 잡는지* 를 증명한다. 특히 `get_current_user` 만 있는 라우트가 여전히 위반으로 보고되는지(인증 ≠ 인가 원칙의 자동 집행) 는 본 검사기의 존재 이유 그 자체다.
3. **오탐(false positive) 차단**: `@app.get(...)` 처럼 `router` 이외의 객체 데코레이터, `@router.include_router(...)` 같은 non-HTTP-method 호출, `= 10` / `= None` 같은 상수 default, 일반 함수 등이 잘못 플래그되지 않음을 증빙한다. 오탐은 검사기 신뢰도를 떨어뜨려 "어차피 가짜 경보 많잖아" 라는 습관을 만든다.
4. **stale whitelist 방지**: WHITELIST 항목이 실제 파일/함수와 연결되어 있는지 검증한다. 파일명이나 함수명이 바뀌면 whitelist 도 같이 바뀌어야 하고, 안 바뀌면 *실제 누락이 whitelist 에 우연히 겹쳐 silent miss* 가 될 수 있다.
5. **main() 진입 경로 보장**: CI 가 호출하는 실제 진입점(`main()`) 의 exit code 와 출력을 검증한다. `check_file` 만 통과해도 `main()` 이 깨져 있으면 CI 에서 exit code 0 을 받지 못해 배포가 막힐 수 있다.

---

## 3. 구현

### 3.1 테스트 파일 구조 (`backend/tests/test_check_rbac_coverage.py`)

21 개 테스트를 6 그룹으로 분할한다. `test_check_bool_literals.py` 의 그룹 수와 정확히 일치하도록 의도적으로 맞췄다.

1. **정책 하위 호환 (5 tests)** — mutation × `require_operator|admin`, decorator-level dependencies, read × `require_viewer`, read × 더 엄격한 가드(`require_admin`). 통과 경로의 스펙 고정.
2. **위반 검출 (4 tests)** — mutation 에 `require_viewer` 만, mutation 에 가드 전혀 없음, read 에 가드 전혀 없음, sync `def` 핸들러도 검사 대상. 검사기가 정확히 어떤 오류 메시지를 내는지(`"의존성 누락"` / `"require_operator 또는 require_admin"`) 까지 assert.
3. **오탐 방지 (7 tests)** — `(auth.py, login)` / `(auth.py, get_me)` WHITELIST 통과, `@app.get(...)` 무시, `@router.include_router(...)` 무시, 상수 default 에서 crash 없음, 데코레이터 없는 함수 무시, **`get_current_user` 만 있는 라우트는 여전히 위반**.
4. **구문 오류 처리 (1 test)** — SyntaxError 파일은 `<file>: parse error: ...` 단일 메시지로 보고. 예외로 스캔을 중단시키지 않는 설계 결정의 회귀 고정.
5. **실제 저장소 회귀 고정 (2 tests)** — 현재 `backend/api/routes/` 가 0 errors, WHITELIST 의 모든 `(file, function)` 쌍이 AST 로 검증되어 실재.
6. **main() 진입 경로 (2 tests)** — `ROUTES_DIR` 누락 시 exit 1 + stderr 메시지, 정상 시 exit 0 + `[PASS]` stdout.

### 3.2 테스트 하니스 패턴

Stage 2 와 동일한 기법을 사용한다.

- `importlib.util.spec_from_file_location("check_rbac_coverage", CHECKER_PATH)` 로 검사기 모듈을 임시 로드. 검사기는 `scripts/` 아래 있어 정규 import path 밖이므로 명시 로딩이 필수.
- `_check(source, tmp_path, filename="sample.py")` 헬퍼로 임시 파일을 만들고 `check_file(path)` 를 호출하여 에러 리스트를 반환.
- WHITELIST 테스트 (`test_whitelisted_auth_login_without_guard_is_allowed`) 는 `filename="auth.py"` 로 호출하여 WHITELIST 매칭이 파일명 기준임을 활용.
- `main()` 테스트는 `monkeypatch.setattr(CHECKER, "ROUTES_DIR", missing)` 로 ROUTES_DIR 를 임시 디렉토리로 치환.

### 3.3 특히 중요한 회귀 고정 3 건

1. **`test_get_current_user_only_is_still_flagged`**: 9위 RBAC 작업 회고에서 "`users.py` 외 9 개 라우터에 가드가 누락된 채 머지됐다. 원인: 기존 라우터는 `get_current_user` 를 쓰니 인증된다는 가정(인증/인가 미분리)" 이라고 명시된 근본 원인에 대한 자동 집행. 이 테스트가 fail 하면 *검사기가 CLAUDE.md 원칙을 위반하는 쪽으로 drift* 했다는 의미다.

2. **`test_whitelist_entries_refer_to_existing_files_and_functions`**: WHITELIST 의 stale entry 방지. 예를 들어 `auth.py` 의 `mfa_setup` 함수가 `mfa_enroll` 로 리네임되면 whitelist 도 같이 바꿔야 한다. 안 바꾸면 향후 누군가 `mfa_setup` 라는 이름의 *다른* 함수를 mutation 으로 추가할 때 가드 없이도 통과하는 시나리오가 가능.

3. **`test_current_repo_routes_have_zero_violations`**: 매 pytest 실행마다 `backend/api/routes/*.py` 를 전수 스캔하여 0 errors 를 assert. `scripts/check_rbac_coverage.py` 를 별도로 실행하는 CI 단계가 (한시적으로) 빠지더라도 pytest 만 통과하면 RBAC 가드 누락이 잡힌다.

---

## 4. 검증

### 4.1 테스트 실행

```bash
cd backend && python -m pytest tests/test_check_rbac_coverage.py -q --tb=short
# 21 passed in 0.20s
```

### 4.2 최소 게이트

```bash
cd backend && python -m ruff check . --config pyproject.toml         # All checks passed!
cd backend && python -m black --check . --config pyproject.toml      # 382 files unchanged
python scripts/check_bool_literals.py                                # ✓ BOOL LITERAL CHECK PASSED
python scripts/check_rbac_coverage.py                                # [PASS] RBAC coverage check
python scripts/check_loguru_style.py                                 # 0 violations
python scripts/check_doc_sync.py --verbose                           # ✓ SYNC CHECK PASSED
```

### 4.3 SSOT cascade

`gen_status.py --update` 실행으로 `README.md` · `docs/FEATURE_STATUS.md` · `docs/operations/release-gates.md` 의 총 테스트 수가 4,080 → 4,101 로 +21 갱신. 이는 Stage 3 테스트 21 건과 정확히 일치.

### 4.4 black 버전 주의

로컬 black 은 repo pin (`24.4.2`) 과 맞춰져 있어야 한다. CI 와 동일 버전을 쓰지 않으면 "로컬에서 black 완료 → CI 에서 drift" 회귀(2026-04-21, OPS-018) 가 재발한다. 커밋 전에 `pip install -r backend/requirements-dev.txt` 로 버전 고정 필수.

---

## 5. 후속 작업

1. **CLAUDE.md §9 TODO 완결**: "AST 기반 정적 검사기 커버리지 확장" 은 `check_loguru_style.py`(2026-04-15) → `check_bool_literals.py`(2026-04-22 Stage 2) → `check_rbac_coverage.py`(2026-04-22 Stage 3) 로 세 검사기 모두 테스트 하니스 보유. 본 커밋으로 완결 표시.
2. **Phase 1 Path A 종결 → Phase 2 ADR 진입**: Stage 3 완료로 Phase 1 Path A(순차 마이그레이션) 의 정적 검사기 강화 블록이 끝난다. 다음 단계는 Phase 2 ADR 작성 — Agent Teams 공식 진입 gate 와 외부 참고(StyleSeed, Graphify, agent-skills) 심사. **(2026-04-22 갱신)**: [ADR-001](../architecture/adr-001-phase2-entry-gate.md) 으로 Phase 2 진입 gate 확립. 본 문서의 "Phase 2 ADR 작성" 후속 작업은 그 ADR 로 연결되며, 개별 도구 심사는 ADR-002 이후로 위임.
3. **신규 정적 검사기의 기본 템플릿**: 향후 `check_*.py` 를 추가할 때는 최소 본 문서의 6 그룹 구조에 해당하는 테스트를 함께 작성하는 것이 내부 표준. 규칙 변경을 테스트 없이 머지하는 관행이 있던 기존 practice 와 단절.

---

## 6. 관련 문서

- `docs/security/rbac-policy.md` — RBAC 권한 매트릭스 SSOT.
- `docs/operations/check-bool-literals-ast-2026-04-22.md` (OPS-019) — Stage 2 동일 패턴 선행 작업. 본 커밋은 그 테스트 구조를 재사용.
- `docs/operations/static-checker-venv-audit-2026-04-21.md` (OPS-017) — Stage 1 `iter_python_files` SSOT.
- `agent_docs/development-policies.md` — RBAC Wiring Rule.
- `CLAUDE.md` — "인증(authn) ≠ 인가(authz) 분리 원칙" / §9 Phase 1 잔여 TODO.
