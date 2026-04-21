# 정적 검사기 vendored 디렉토리 제외 통일 — 2026-04-21

> **문서 번호**: OPS-017
>
> **목적**: `scripts/check_*.py` 계열 정적 검사기가 `backend/.venv/` 등 vendored 디렉토리를 각자 다른 방식으로 처리하여 발생한 회귀(false positive + sandbox timeout) 를 단일 공통 유틸로 수렴시킨 작업 기록. Phase 1 Stage 1 범위이며, 후속 Stage 2 (`check_bool_literals` regex→AST 전환) / Stage 3 (`check_rbac_coverage` 테스트 하니스) 의 기반 인프라를 제공한다.

---

## 1. 관측된 증상

### 1.1 `check_bool_literals.py` — 226 false positives

2026-04-21 로컬 개발자 머신에서 `python scripts/check_bool_literals.py` 실행 시 226건의 ad-hoc bool 파싱 위반이 집계되었다. 모든 건이 `backend/.venv/lib/python3.11/site-packages/` 하위 서드파티 코드 (`sympy`, `torch`, `google-colab-utils` 등) 에서 발생했다. AQTS 프로젝트 코드(`backend/`, `scripts/`) 에서는 0건이었다.

원인은 `PYTHON_GLOBS` 가 `ROOT.glob("backend/**/*.py")` 과 `ROOT.glob("scripts/**/*.py")` 를 사용했고, `backend/**/*.py` 가 `backend/.venv/` 아래의 모든 `.py` 파일을 포함한 점이다. 인라인으로 `if "__pycache__" in path.parts` 만 제외했으므로 `.venv` / `site-packages` / `build` 등 다른 vendored 패턴은 전혀 걸러지지 않았다.

### 1.2 `check_loguru_style.py` — sandbox 15s 타임아웃

동일 머신의 `friendly-sleepy-wright` sandbox 에서 `python scripts/check_loguru_style.py` 실행 시 15 초 wrapper 타임아웃에 걸려 exit code 124 로 중단되었다. 원인은 `BACKEND.rglob("*.py")` 가 `backend/.venv/` 아래 수만 개 파일을 열거한 뒤 각 파일을 `ast.parse()` 로 파싱했기 때문이다. 본래 AST 파싱 자체가 regex 대비 비싸고, 서드파티 whl 은 보통 많은 모듈을 포함하므로 I/O + 파싱 비용이 폭증한다.

두 회귀 모두 개별 검사기가 **제외 경로 로직을 각자 구현** 한 데서 비롯되었다. 한 곳(`__pycache__`) 만 커버하고 나머지를 빠뜨리면 즉시 false positive 또는 성능 역행이 발생하는 구조였다.

---

## 2. 설계 목표

1. **단일 진실원천**: 모든 `check_*.py` 가 공유하는 경로 제외 로직은 한 곳에만 정의한다.
2. **성능 원천 차단**: vendored 디렉토리는 **열거 전 단계** 에서 재귀를 차단한다. `rglob` 후 필터는 대용량 `.venv` 안으로 내려가는 I/O 를 이미 지불한 뒤이므로 부적절하다.
3. **회귀 테스트 고정**: 검사기 추가/수정 시 제외 로직이 조용히 끊어지는 사례를 방지한다. 본 파일 1개가 12개 vendored 디렉토리 이름의 제외 동작을 전수 검증한다.
4. **호출부 단순화**: 검사기 스크립트가 `iter_python_files(root)` 한 줄로 "프로젝트 `.py` 파일만" 을 얻는다. 인라인 필터 코드가 제거된다.

---

## 3. 구현

### 3.1 신규 유틸: `scripts/_check_utils.py`

```python
VENDORED_DIR_NAMES: frozenset[str] = frozenset({
    ".venv", "venv", "__pycache__", ".tox",
    "build", "dist", ".pytest_cache", "htmlcov",
    ".mypy_cache", ".ruff_cache", "node_modules", "site-packages",
})

def iter_python_files(root: Path, *, extra_excludes: Iterable[str] = ()) -> Iterator[Path]:
    if not root.exists():
        return
    excludes = set(VENDORED_DIR_NAMES) | set(extra_excludes)
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in excludes]
        for fname in filenames:
            if fname.endswith(".py"):
                yield Path(dirpath) / fname
```

핵심은 `dirnames[:] = [...]` 슬라이스 대입이다. `os.walk` 는 `dirnames` 리스트를 호출부가 mutate 하면 해당 서브트리를 더 이상 재귀하지 않는다. 단순 재할당(`dirnames = [...]`) 은 지역 변수만 바꾸고 재귀에 영향을 주지 못하므로, 반드시 슬라이스 대입을 사용한다. 이 관용구는 Python 표준 라이브러리 문서의 공식 제외 패턴이다.

부가적으로 `is_vendored_path(path, extra_excludes)` 도 export 한다. 이미 경로가 주어진 상황(예: `check_bool_literals.py::PYTHON_EXEMPT` 비교) 에서 vendored 여부를 판정할 때 사용한다.

### 3.2 호출부 변경

**`scripts/check_bool_literals.py`**:
- `PYTHON_GLOBS = ["backend/**/*.py", "scripts/**/*.py"]` → `PYTHON_ROOTS = [ROOT / "backend", ROOT / "scripts"]`
- `ROOT.glob(pattern)` 루프 + 인라인 `__pycache__` 필터 → `iter_python_files(py_root)` 단일 호출
- `sys.path.insert(0, ...)` + `from _check_utils import iter_python_files  # noqa: E402` 추가
  (`scripts/` 는 패키지가 아니므로 sibling import 를 위해 sys.path 조작이 필요하고, 이미 `pyproject.toml [tool.ruff.lint] ignore = ["E402"]` 가 설정되어 있어 허용됨)

**`scripts/check_loguru_style.py`**:
- `sorted(BACKEND.rglob("*.py"))` → `sorted(iter_python_files(BACKEND))`
- 동일한 sys.path + import 헤더 추가
- 기존 docstring 에 "venv / __pycache__ 를 사전 제외" 설명 보강

### 3.3 회귀 테스트: `backend/tests/test_check_utils.py`

총 16개 테스트로 다음 불변을 고정한다:

1. `is_vendored_path` — 직접 부모 / 깊은 조상 / 프로젝트 파일 False / extra_excludes / `VENDORED_DIR_NAMES` 전수 매칭 (5)
2. `iter_python_files` — `.venv` / `venv` / `__pycache__` / build·dist·htmlcov / .pytest·.mypy·.ruff·.tox / 프로젝트 통합 시뮬레이션 / `.py` 외 확장자 제외 / `extra_excludes` 경유 / 존재하지 않는 루트 / 빈 디렉토리 (10)
3. **깊이 회귀 방지**: `.venv/level_0/.../level_19/deep.py` 20단계 구조를 생성하고 `iter_python_files` 가 **단 하나도** yield 하지 않는지 확인한다. `os.walk` 의 `dirnames[:]` mutation 이 깨지면 본 테스트가 실패한다 (1)

---

## 4. 효과 측정

| 지표 | 수정 전 | 수정 후 |
|------|---------|---------|
| `check_bool_literals.py` 위반 건수 | 226 (all FP from `.venv`) | 0 (참 위반만) |
| `check_loguru_style.py` 실행 시간 (sandbox) | 15s timeout (124) | ~0.8s 정상 종료 |
| 검사기별 중복 제외 로직 | 2곳 (개별 regex, `__pycache__` 인라인) | 0곳 (`iter_python_files` 단일 경유) |

로컬 개발자 머신(379 파일 backend + scripts)에서도 loguru 검사기가 약 1초 내 완료되어 사용 체감상 즉시.

---

## 5. 후속 작업 (Stage 2 / Stage 3)

본 작업은 **경로 제외 인프라 통일** 만 다룬다. 다음은 동일 파일군에 대한 별도 커밋/PR 로 분리한다.

- **Stage 2**: `check_bool_literals.py` 의 regex 패턴 5 종을 AST `Compare` / `Call` 노드 판정으로 이전. 현재 regex 는 문자열 내부 괄호/인코딩 이스케이프에서 누락 가능성이 있다 (`check_loguru_style.py` 가 이미 2026-04-15 에 regex→AST 전환을 완료한 회고 `phase1-demo-verification-2026-04-11.md §10.16` 참조).
- **Stage 3**: `check_rbac_coverage.py` 의 AST 방어선이 현재 정상 동작 중이지만, 검사기 자체를 검증하는 테스트 하니스 (`backend/tests/test_check_rbac_coverage.py`) 가 없다. `test_check_loguru_style.py` / `test_check_cd_stdin_guard.py` 패턴을 따라 추가.

---

## 6. Wiring Rule 적용 확인

"정적 검사기 공통 유틸 정의 ≠ 모든 검사기가 실제로 사용" 은 본 도메인의 Wiring Rule 이다. 본 작업에서는 두 호출부가 즉시 import 했고, `.github/workflows/doc-sync-check.yml` 이 두 검사기를 모두 실행하므로 CI 녹색 = 유틸 통합이라는 등가가 성립한다. 향후 신규 `check_*.py` 가 추가될 때는:

1. `from _check_utils import iter_python_files` 를 표준으로 사용
2. 인라인 `rglob` + 필터 패턴을 금지 (code review gate)
3. 신규 vendored 디렉토리 이름이 등장하면 `VENDORED_DIR_NAMES` 에만 추가 (SSOT)

---

## 7. 검증 결과

- `python -m ruff check backend/ scripts/_check_utils.py scripts/check_bool_literals.py scripts/check_loguru_style.py --config backend/pyproject.toml` → All checks passed
- `python -m black --check backend/ --config backend/pyproject.toml` → 380 files unchanged
- `python scripts/check_bool_literals.py` → PASSED (0 위반)
- `python scripts/check_loguru_style.py` → PASSED (0.83s)
- `python scripts/check_doc_sync.py --verbose` → 0 errors / 0 warnings
- `python scripts/check_cd_stdin_guard.py` → PASSED (18 files scanned)
- `python scripts/check_rbac_coverage.py` → PASSED (15 files scanned)
- `pytest backend/tests/test_check_utils.py tests/test_check_loguru_style.py tests/test_check_cd_stdin_guard.py` → 54 passed
