# `check_bool_literals.py` regex → AST 전환 — 2026-04-22

> **문서 번호**: OPS-019
>
> **목적**: 환경변수 bool 표기 표준화 정적 검사기(`scripts/check_bool_literals.py`) 의 Python 검사부를 regex 기반 구현에서 AST 기반 구현으로 이전한 작업 기록. `check_loguru_style.py` (2026-04-15) 와 동일한 결손을 체계적으로 제거하고, 23개 회귀 테스트 하니스로 이후 규칙 drift 를 고정한다. Phase 1 Path A Stage 2 범위이며, Stage 1 `iter_python_files` SSOT (OPS-017) 위에 축적한다.

---

## 1. 배경

2026-04-15 `check_loguru_style.py` 회고(`docs/operations/` 밖의 커밋 메시지로 기록) 에서 정적 검사기의 regex 기반 구현이 **4 가지 구조적 결손**을 가진다는 것이 확인되었다. `check_bool_literals.py` 는 동일한 regex 패턴군을 사용하고 있었으며, 회고 직후에도 즉각 전환되지 않고 남아 있었다. 본 문서의 작업은 그 전환을 완료하는 것이다.

### 1.1 regex 가 놓치는 4 가지 사례

1. **중첩 괄호 누락**: `os.environ.get("X", fallback()) == "true"` 와 같이 인자 리스트 안에 다시 괄호가 열리는 호출. regex 패턴 `os\.environ\.get\([^)]*\)` 는 첫 번째 `)` 에서 매칭을 끝내므로 실제 호출의 닫는 괄호를 잡지 못하고 전체 표현식이 누락된다.
2. **멀티라인 호출 누락**: 인자를 여러 줄에 걸쳐 쓰는 호출 (`os.environ.get(\n    "X",\n    default,\n) == "true"`). per-line regex 는 한 번에 한 줄만 보므로 본 줄(`os.environ.get(` 만 있는 줄) 외에는 매칭되지 않는다.
3. **문자열 내부 false positive**: 문서 문자열, 로그 메시지 등 문자열 리터럴 안에 `"os.environ.get(X) == 'true'"` 와 같은 코드 모양의 부분 문자열이 있으면 regex 가 이를 실제 코드로 매칭한다. AST 는 이 경우를 `Constant` 노드로 판정하므로 근본적으로 분리된다.
4. **비교 순서 역전 누락**: `"true" == os.environ.get(...)` 처럼 Yoda-style 비교로 쓰면 `os\.environ\.get\([^)]*\)\s*==\s*["\']` 패턴이 엇갈려 매칭되지 않는다.

### 1.2 왜 bool 검사기가 loguru 검사기와 동시 전환되지 않았는가

`check_loguru_style.py` 는 2026-04-15 에 단독으로 전환되었으며, 그 커밋의 스코프 규율(한 커밋 하나 원인) 로 bool 검사기는 같이 묶이지 않았다. CLAUDE.md §9 TODO 에 "AST 기반 정적 검사기 커버리지 확장" 항목이 남아 있었고, Stage 1 (OPS-017) 에서 `iter_python_files` SSOT 를 먼저 구축하고 나서 본 Stage 2 에서 규칙부를 전환하는 것이 사전 합의된 순서였다.

---

## 2. 설계 목표

1. **AST 기반 노드 판정**: `ast.parse()` + `ast.walk()` 로 전체 트리를 순회하고, 각 노드가 "환경변수 호출 결과를 bool 로 파싱하는가" 라는 단일 질문에 답하는 분류기(`_classify`) 를 만든다. regex 의 "문자열 맞춤" 과 달리 AST 는 호출 구조·비교 구조를 직접 판정하므로 위 4 가지 결손이 구조적으로 제거된다.
2. **기존 정책 불변**: 정책 자체(어떤 환경변수가 표준 표기를 강제받는가, 면제 파일은 무엇인가) 는 regex 구현과 동일하게 유지한다. `BOOL_ENV_KEYS` 화이트리스트, `PYTHON_EXEMPT` 집합, `.env`/`docker-compose*.yml`/`workflows/*.yml` KV-regex 검사는 그대로 간다.
3. **회귀 테스트 하니스**: 전환 이후 규칙이 조용히 끊어지는 사례를 방지한다. 특히 위 4 가지 결손 케이스 각각을 실행 가능한 테스트로 고정한다.
4. **설정 파일 검사부는 유지**: `.env*`, `docker-compose*.yml`, `.github/workflows/*.yml` 은 Python AST 파싱 대상이 아니므로 기존 KV-regex 경로(`_KV_RE`, `check_config_files`) 를 그대로 둔다. 불필요한 구조 변경을 스코프에 넣지 않는다.

---

## 3. 구현

### 3.1 AST 분류기 (`scripts/check_bool_literals.py`)

핵심 헬퍼 5 종과 분류기 `_classify` 로 구성한다.

- `_attr_chain(node)`: `a.b.c` 형태의 `Attribute`/`Name` 체인을 점으로 연결한 문자열로 반환. `os.environ.get` 같은 체인을 식별할 때 사용. 하위 노드가 이 두 타입이 아니면 `None`.
- `_is_env_call(node)`: 노드가 `ast.Call` 이고 그 func 의 체인이 `{"os.environ.get", "os.getenv"}` 중 하나이면 True. frozenset 상수(`_ENV_CALL_FUNCS`) 로 미리 고정.
- `_is_string_constant(node)`: `ast.Constant` + `isinstance(value, str)`. 주로 `compare_eq` 판정에 사용.
- `_is_bool_literal_constant(node)`: `ast.Constant` 이며 값이 `_BOOL_LITERAL_TOKENS = {"true", "false", "1", "0", "yes", "no", "on", "off"}` 중 하나(대소문자 무시). `env_bool()` (`backend/core/utils/env.py::_TRUE_VALUES|_FALSE_VALUES`) 의 허용 토큰과 정확히 동일해야 한다.
- `_is_string_container(node)`: `ast.Tuple | ast.List | ast.Set` 이며 원소 중 하나 이상이 **bool 리터럴 토큰 상수**. 임의 문자열 상수가 아니라 bool 토큰으로 제한하는 이유는 §3.4 에서 상술.

분류기 `_classify(node) -> str | None` 는 세 가지 패턴을 반환한다.

- `"compare_eq"`: `env_call() == "true"` 또는 `"true" == env_call()` (양방향). `ast.Eq` + `ast.NotEq` 모두 포함.
- `"lower_chain"`: `env_call().lower()` — `Attribute(attr="lower", value=env_call)` 구조.
- `"in_container"`: `env_call() in ("true", "1", ...)` / `not in (...)` — `ast.In` + `ast.NotIn`, 우변이 문자열 컨테이너.

`_scan_file(path)` 는 `ast.walk(tree)` 를 한 번만 돌면서 각 노드에 `_classify` 를 적용하고, 매칭된 노드의 `lineno` + 분류명 + 소스 라인 snippet(최대 200자) 을 수집한다. `check_python_files()` 는 `iter_python_files` (Stage 1 SSOT) 를 사용하여 `backend/` 와 `scripts/` 를 순회한다.

### 3.2 regex 결손이 AST 에서 자동 해소되는 이유

위 §1.1 의 4 가지 사례를 AST 가 어떻게 잡는지를 실제 트리로 설명하면:

1. **중첩 괄호**: `os.environ.get("X", fallback())` 은 `ast.Call(func=Attribute(...), args=[Constant, Call])` 이다. regex 가 보던 "괄호 문자" 가 아니라 "함수 호출 구조" 를 보므로, 인자에 몇 겹의 괄호가 있든 외부 호출 노드는 동일하게 `ast.Call` 이다.
2. **멀티라인**: `ast.parse` 는 표현식의 물리적 줄 위치를 `lineno` 로 보존하지만 트리 구조는 줄 수와 무관하다. 인자가 10줄에 걸쳐 있어도 `ast.Compare` 노드 하나다.
3. **문자열 리터럴 내부**: 문자열 안의 `"os.environ.get(X) == 'true'"` 는 `ast.Constant(value="os.environ.get(X) == 'true'")` 로만 파싱된다. 그 문자열 값은 Python 코드로 다시 해석되지 않으므로 `_classify` 의 탐색 경로에 들어오지 않는다.
4. **비교 순서 역전**: `_classify` 의 `compare_eq` 분기는 `left` 와 `right` 둘 다에 대해 "env_call vs 문자열 상수" 조합을 검사한다(`if _is_env_call(left) and _is_string_constant(right): return "compare_eq"` + `if _is_env_call(right) and _is_string_constant(left): return "compare_eq"`). 순서 역전이 사라지는 구조다.

### 3.3 테스트 하니스 (`backend/tests/test_check_bool_literals.py`)

27개 테스트로 구성하며 6 그룹으로 분할한다.

1. **Regex backward compat (5)**: regex 구현이 이미 잡던 5 패턴 — `os.environ.get(...) == "true"`, `os.getenv(...) == "true"`, `.lower()` on `os.environ.get`, `.lower()` on `os.getenv`, tuple `in` 멤버십 — 이 AST 구현에서도 그대로 잡힌다.
2. **AST 신규 커버리지 (7)**: 위 §1.1 의 4 결손 각각 + `NotEq`/`NotIn` + `list`/`set` 컨테이너. 특히 중첩 괄호·멀티라인·Yoda-style 역전은 regex 로는 잡지 못하던 케이스이므로 **전환 가치의 직접 증빙** 이다.
3. **False positive 방지 (9)**: `env_bool(...)` 헬퍼 호출은 허용, 문자열 리터럴 안의 코드 모양은 무시, 주석 줄 무시, `other_func(...) == "true"` 같은 무관한 호출 무시, `os.getenv(...) == 42` 같은 문자열 아닌 비교 무시, enum-style `in/not in` 멤버십(`("prod", "staging")`) 무시, bool 토큰이 섞인 혼합 컨테이너는 여전히 감지, 대소문자 다른 bool 토큰(`"TRUE"`/`"False"`) 도 감지.
4. **Syntax error 회피 (1)**: 파싱 실패 파일은 조용히 skip.
5. **면제 파일 (2)**: `backend/core/utils/env.py` 와 `scripts/check_bool_literals.py` 는 자기 자신이므로 스캔 제외. 현재 레포 전체에 대해 `check_python_files` 가 0 errors 를 반환하는 회귀 고정.
6. **설정 파일 KV-regex (3)**: 비표준 값 감지, 표준 값 통과, 화이트리스트 밖 키 무시.

테스트 패턴은 `test_check_loguru_style.py` 와 동일하게 `importlib.util.spec_from_file_location` 으로 체커 모듈을 임시 로드하고, `tmp_path` 에 샘플 `.py` 를 작성한 뒤 `_scan_file(path)` 를 직접 호출한다. `check_python_files` / `check_config_files` 는 ROOT 상수에 의존하므로 monkeypatch 로 ROOT 를 임시 디렉토리로 치환한다.

### 3.4 Codex P2 회귀 방어 — `in_container` 판정 범위 축소

초기 AST 구현(`ac9818f`) 의 `_is_string_container` 는 "하나 이상의 문자열 상수를 원소로 갖는 Tuple/List/Set" 을 모두 매칭했다. Codex 리뷰 봇이 이를 P2 이슈로 지적 — `os.getenv("APP_ENV") in ("prod", "staging")` 같은 enum-style 멤버십 검사가 **ad-hoc bool 파싱이 아님에도** `in_container` 로 잡혀 CI 에서 정당한 enum 비교를 차단할 수 있었다.

이는 regex 구현과의 **동치성도 깨는** 회귀다. 기존 regex `r'os\.environ\.get\([^)]*\)\s*in\s*\([^)]*["\']true'` 는 컨테이너에 `"true"` 가 포함된 경우에만 매칭했으므로, enum 비교는 regex 시절부터 통과 대상이었다.

해소: `_BOOL_LITERAL_TOKENS` frozenset 과 `_is_bool_literal_constant` 헬퍼를 도입하여, 컨테이너 원소 중 하나라도 bool 리터럴 토큰(대소문자 무시) 이어야 `_is_string_container` 가 True 를 반환하도록 제한. 토큰 집합은 `env_bool()` 의 허용 토큰(`_TRUE_VALUES | _FALSE_VALUES` = `{true, false, 1, 0, yes, no, on, off}`) 과 정확히 동일하게 유지한다 — 어느 한쪽이 넓거나 좁으면 "env_bool 이 허용하는데 검사기는 차단" 혹은 반대의 드리프트가 생긴다.

부가 회귀 방어 테스트 4 건 추가 (§3.3 그룹 3 의 증가분):

- `test_enum_style_in_membership_is_not_flagged` — `os.getenv("APP_ENV") in ("prod", "staging")` 통과.
- `test_enum_style_not_in_membership_is_not_flagged` — `not in` 형태도 통과.
- `test_mixed_container_with_bool_literal_is_still_flagged` — `("prod", "true")` 처럼 bool 토큰이 하나라도 섞이면 의심스러우므로 여전히 감지.
- `test_case_insensitive_bool_literal_in_container_is_detected` — `("TRUE", "False")` 도 감지 (`env_bool()` 이 대소문자 무시이므로 동치성 유지).

---

## 4. 검증

### 4.1 테스트 실행

```bash
cd backend && python -m pytest tests/test_check_bool_literals.py -v --tb=short
# 27 passed in 1.14s
```

전 테스트 통과. 특히 §3.3 그룹 2 의 regex 결손 3종 (nested_call, multiline, reversed_compare) 이 AST 구현에서 통과하는 것이 본 전환의 핵심 증빙이다. 그룹 3 의 enum-style 멤버십 4 건은 §3.4 Codex P2 회귀 방어의 회귀 고정.

### 4.2 기존 코드베이스 회귀 없음

```bash
python scripts/check_bool_literals.py
# ✓ BOOL LITERAL CHECK PASSED
```

전환 전후 모두 0 violations. 기존 코드에 regex 가 놓쳤던 숨은 위반이 있었는지 확인하는 의미가 있으며, 없었다는 것이 AST 전환의 안전성을 보조한다 (현재 코드가 그동안 운 좋게 regex 결손 사례를 피해 왔다는 뜻).

### 4.3 최소 게이트

```bash
cd backend && python -m ruff check . --config pyproject.toml        # All checks passed!
cd backend && python -m black --check . --config pyproject.toml     # 381 files unchanged
python scripts/check_doc_sync.py --verbose                          # ✓ SYNC CHECK PASSED
python scripts/gen_status.py --update                               # total_tests = 4076
```

`gen_status.py` 업데이트로 `README.md` · `docs/FEATURE_STATUS.md` · `docs/operations/release-gates.md` 의 "총 테스트 수" 표기가 4,053 → 4,080 으로 자동 갱신(+27, AST 전환 23 건 + Codex P2 회귀 방어 4 건). SSOT cascade 정상 동작.

---

## 5. 후속 작업

1. **Stage 3 — `check_rbac_coverage.py` 테스트 하니스**: 현재 이 검사기는 AST 기반이지만 테스트가 없다. 본 커밋과 동일한 하니스 패턴(6 그룹 구조 + `importlib` + `tmp_path`) 으로 하니스를 추가한다. CLAUDE.md §9 "AST 기반 정적 검사기 커버리지 확장" TODO 의 마지막 항목.
2. **AQTS_STRICT_BOOL=true 승격**: Phase 2 gate 는 정적 검사 0 errors + CI/운영 비표준 0건 + 14일 관측이다. 본 커밋으로 정적 검사의 신뢰도가 올라갔으므로 나머지 두 조건만 충족하면 승격 절차를 시작할 수 있다.

---

## 6. 관련 문서

- `docs/conventions/boolean-config.md` — 정책 SSOT (검사 규칙의 근거).
- `docs/operations/static-checker-venv-audit-2026-04-21.md` (OPS-017) — Stage 1 공통 util 구축. 본 커밋은 그 위의 규칙부 교체.
- `agent_docs/development-policies.md §6` — 환경변수 bool 표기 표준 규칙.
- `CLAUDE.md §9` — Phase 1 잔여 TODO. 본 커밋 반영 후 "AST 기반 정적 검사기 커버리지 확장" 항목의 `check_bool_literals.py` 부분은 해소 표시.
