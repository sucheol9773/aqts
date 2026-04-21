# Dev 의존성 파일 분리 — 2026-04-21

> **문서 번호**: OPS-018
>
> **목적**: `.github/workflows/ci.yml` 에 하드코딩되어 있던 lint/format 도구 버전 pin (`ruff==0.5.0`, `black==24.4.2`) 을 `backend/requirements-dev.txt` 단일 진실원천(SSOT) 으로 이전한 작업 기록. CLAUDE.md §1 "하드코딩 금지" 원칙과 CI 워크플로의 버전 관리 현실 사이의 긴장을 해소한다.

---

## 1. 배경

### 1.1 문제점

`chore/python-version-align` 진행 중 venv 초기 셋업 과정에서 다음 세 가지 마찰이 관찰되었다.

1. **로컬 개발자 셋업 경로 부재**: `pip install -r backend/requirements.txt` 만으로는 `ruff`, `black` 이 설치되지 않는다. 로컬에서 커밋 전 검증(`python -m ruff check .`, `python -m black --check .`) 을 돌리려면 개발자가 별도로 어떤 버전을 설치해야 하는지 워크플로 yml 을 뒤져서 찾아야 했다.
2. **버전 업그레이드 번거로움**: `ruff` / `black` 을 bump 하려면 `.github/workflows/ci.yml` 라인 39 의 `pip install ruff==0.5.0 black==24.4.2` 문자열을 직접 수정해야 한다. Python 의존성 관리의 표준 위치인 `requirements*.txt` 밖에서 관리되므로 Dependabot 자동 PR 대상에서도 벗어난다.
3. **CLAUDE.md §1 규칙과의 긴장**: 프로젝트 규칙은 "하드코딩 절대 금지" 이지만 workflow yml 의 version pin 은 관례적으로 허용되어 왔다. 이 미묘한 예외가 신규 기여자에게 혼란을 준다.

### 1.2 관측된 CI.yml 하드코딩 지점

```
$ grep -rn 'ruff==\|black==' .github/workflows/
.github/workflows/ci.yml:39:        run: pip install ruff==0.5.0 black==24.4.2
```

`pip-audit==2.7.3` (라인 89) 도 같은 패턴이지만 용도가 "security scan" 이라 별도 이슈로 분리 (후속 TODO — CLAUDE.md §9 참조).

---

## 2. 설계 선택

### 2.1 옵션 A vs B

| 관점 | 옵션 A: `backend/requirements-dev.txt` | 옵션 B: `pyproject.toml [project.optional-dependencies].dev` |
|---|---|---|
| 변경 범위 | 신규 파일 1 + CI 1 스텝 + README 1 블록 | `[project]` 섹션 신설 필요 (name/version/requires-python + `[build-system]`) |
| 일관성 | 기존 `requirements.txt` 패턴과 동일 | 새로운 패러다임 (PEP 621) |
| installable package 필요 여부 | 불필요 | 필요 (`pip install -e ".[dev]"`) |
| `.pip-audit-ignore` 호환 | 즉시 | 구조 재검토 필요 |
| PEP 621 권장 | — | ✓ |

**결정: 옵션 A**. 근거:

1. 본 프로젝트는 installable package 가 아니라 **app 구조** — `backend/main.py`, `backend/scheduler_main.py` 를 직접 실행하며, `setup.py` 나 `[project]` 메타데이터가 정의된 적 없음.
2. 기존 `backend/requirements.txt` 패턴과 일관성을 유지하여 신규 기여자가 학습 비용 없이 이해 가능.
3. PEP 621 로 이전하려면 `[project]` 신설 + `[build-system]` 정의 + Dockerfile `COPY requirements.txt` 구문 재검토 등 파급이 넓어 **별도 ADR 감** — 본 커밋의 TODO 본래 의도를 초과.
4. 변경 범위 최소로 회귀 위험 축소.

### 2.2 `-r requirements.txt` 포함 여부

초안에서는 `backend/requirements-dev.txt` 상단에 `-r requirements.txt` 를 포함하여 한 파일만 설치하면 runtime + lint 가 모두 설치되도록 했으나, 다음 이유로 제외하기로 결정.

- CI 의 `lint` 잡은 ruff/black check 만 수행하고 Python import 를 실행하지 않는다. FastAPI/SQLAlchemy/Motor 등 runtime 의존성 수십 MB 를 매번 설치하는 것은 불필요한 오버헤드.
- 로컬 개발자 편의는 README 의 두 줄 가이드로 충분히 대체된다 (`pip install -r backend/requirements.txt -r backend/requirements-dev.txt`).
- CI 성능 (lint 잡 캐시 미스 시 설치 시간) vs 로컬 편의 (한 명령 설치) 사이의 절충에서 **CI 성능 우선** — 로컬 개발자는 1회성 셋업, CI 는 모든 push/PR 마다 실행.

결과적으로 `requirements-dev.txt` 는 "lint 도구 only" 로 구성되며, runtime 의존성은 기존 `requirements.txt` 로 분리 유지된다.

### 2.3 pytest/pytest-asyncio 는 왜 dev 로 이동하지 않는가

`requirements.txt` 에 이미 `pytest==9.0.3`, `pytest-asyncio==1.3.0` 등이 고정되어 있고, CI 의 `smoke` / `test` 잡과 `Dockerfile` 의 `RUN pip install -r requirements.txt` 가 이를 전제로 한다. pytest 계열을 `requirements-dev.txt` 로 이동하면 Dockerfile 의 `COPY requirements.txt` + `pip install -r requirements.txt` 구조를 건드려야 하고, 이미지가 정상 빌드되는지 별도 검증이 필요하므로 **범위 초과**. 본 커밋에서는 의도적으로 배제한다.

---

## 3. 구현

### 3.1 신규 파일: `backend/requirements-dev.txt`

```
# ── Linters & Formatters ──
ruff==0.5.0
black==24.4.2
```

파일 상단 주석에 다음 정보를 명시한다 — 목적, 로컬 개발자 셋업 명령(2 가지 시나리오), `-r requirements.txt` 를 포함하지 않는 설계 결정 근거, 버전 업그레이드 절차, 의도적 제외 대상(pytest, pip-audit).

### 3.2 `.github/workflows/ci.yml` 수정

#### 3.2.1 `cache-dependency-path` 변경

```diff
       - uses: actions/setup-python@v6
         with:
           python-version: ${{ env.PYTHON_VERSION }}
           cache: pip
-          cache-dependency-path: backend/requirements.txt
+          cache-dependency-path: backend/requirements-dev.txt
```

lint 잡은 runtime 의존성을 설치하지 않으므로 pip cache 무효화 키를 dev 파일로 지정해야 정확한 동작. 버전 bump 시에만 cache 가 재생성된다.

#### 3.2.2 `Install linters` 스텝 변경

```diff
       - name: Install linters
-        run: pip install ruff==0.5.0 black==24.4.2
+        run: pip install -r backend/requirements-dev.txt
```

버전 pin 은 이제 `backend/requirements-dev.txt` 에만 존재. Dependabot 이 자동으로 PR 을 생성할 수 있게 된다.

### 3.3 README.md 갱신

- "프로젝트 구조" 트리 라인 68 아래에 `requirements-dev.txt` 엔트리 추가.
- "시작하기" 섹션에 §2.4 "로컬 개발자 lint/format 도구 설치" 블록 신설. 두 시나리오 제시:
  1. `pip install -r backend/requirements-dev.txt` — lint 도구만 필요 (editor LSP 등).
  2. `pip install -r backend/requirements.txt -r backend/requirements-dev.txt` — 런타임 + lint 도구 모두 (테스트까지 로컬에서 돌릴 때).

---

## 4. 효과 측정

| 지표 | 수정 전 | 수정 후 |
|------|---------|---------|
| `ruff`/`black` 버전 pin 위치 | `.github/workflows/ci.yml:39` (yml 내부) | `backend/requirements-dev.txt` (SSOT) |
| 로컬 lint 도구 설치 명령 | 워크플로 yml 읽고 수동으로 `pip install ruff==0.5.0 black==24.4.2` 타이핑 | `pip install -r backend/requirements-dev.txt` |
| Dependabot 자동 PR 대상 여부 | 불가능 (yml 내부 version 문자열 파싱 안함) | 가능 (`requirements-dev.txt` 파싱 지원) |
| CI lint 잡 성능 | ruff/black 만 설치 (~5s) | 동일 (~5s, `-r requirements.txt` 미포함) |
| CLAUDE.md §1 "하드코딩 금지" 긴장 | 존재 | 해소 |

CI lint 잡 성능은 변화 없음 — `requirements-dev.txt` 가 lint 도구 only 이기 때문.

---

## 5. 검증

### 5.1 최소 게이트 (로컬)

```bash
python3 scripts/check_bool_literals.py
python3 scripts/check_doc_sync.py --verbose
```

두 검사기 모두 0 errors / 0 warnings 통과.

### 5.2 정적 분석 (로컬)

```bash
cd backend && python -m ruff check . --config pyproject.toml
cd backend && python -m black --check . --config pyproject.toml
```

코드 변경 zero (`.py`, `.toml` 에 단 한 글자도 손대지 않음) — 사전 fixture 상태 유지.

### 5.3 전체 pytest (사용자 로컬 dev 환경)

`.github/workflows/ci.yml` 이 변경되었으므로 CLAUDE.md §3.1 규칙에 따라 전체 pytest 실행 필요. sandbox 의 cryptography 바인딩 이슈를 우회해 사용자 로컬 `dev` 환경에서 수행.

### 5.4 CI 실제 동작 확인

PR 머지 후 `Lint & Format Check` 잡이 녹색인지 확인. 녹색 = `pip install -r backend/requirements-dev.txt` → `ruff check` → `black --check` 전 체인 정상 동작 증거.

---

## 6. 후속 작업

- **pip-audit 하드코딩 해소**: `ci.yml:89` 의 `pip install pip-audit==2.7.3` 도 동일 패턴이나 용도가 "security scan" 이라 별도 이슈로 분리. CLAUDE.md §9 TODO 에 등록. `backend/requirements-security.txt` 신설 vs `requirements-dev.txt` 에 `## ── Security Scanning ──` 섹션 병합 중 후속 커밋에서 결정.
- **Dependabot 설정 확인**: `.github/dependabot.yml` 이 존재한다면 `backend/requirements-dev.txt` 도 monitoring 대상에 포함되는지 확인 필요 (존재하지 않으면 현 상태 유지 — 별도 ADR).
- **PEP 621 이전 (장기)**: 프로젝트가 installable package 로 전환되는 시점 (예: Phase 2 Agent Teams 에서 팀별 reusable 유틸 패키지화) 에 `[project]` 섹션 신설 + dev 이동 일괄 작업으로 처리.

---

## 7. Wiring Rule 적용 확인

"`requirements-dev.txt` 를 정의했다 ≠ CI 가 실제로 이를 통해 설치한다" 는 본 도메인의 Wiring Rule 이다. 본 커밋에서는:

1. `ci.yml` 의 `Install linters` 스텝이 `pip install -r backend/requirements-dev.txt` 를 직접 호출하도록 변경 → **배선 완료**.
2. `cache-dependency-path` 도 dev 파일로 변경 → pip cache 가 dev 파일 변경 시 invalidated 되는 경로까지 배선.
3. PR 머지 후 `Lint & Format Check` 잡의 실제 실행 로그에서 `Installing collected packages: ruff, black` 라인이 출력되는지 확인하는 것이 런타임 증거.

단위 테스트로는 Wiring 검증이 불가능하며 (yml 스텝은 GitHub Actions 런타임에서만 실행), PR 의 CI 녹색 = 배선 성공이라는 등가가 성립한다.

---

## 8. 관련 문서

- `CLAUDE.md §9` — 본 TODO 의 원본 발견 기록 및 체크박스 갱신.
- `backend/requirements.txt` — runtime 의존성 SSOT (본 커밋에서 변경 없음).
- `README.md §2.4` — 로컬 개발자 lint 도구 설치 가이드 (본 커밋에서 신설).
- `agent_docs/development-policies.md §3.1` — 문서-only 커밋 예외 기준 (본 커밋은 `.github/workflows/ci.yml` 변경이 포함되어 전체 pytest 필요).
- `docs/operations/static-checker-venv-audit-2026-04-21.md` — 같은 날의 선행 작업 (OPS-017). 검사기 인프라 통일 → 본 커밋 (dev 도구 배포 통일) 순서로 "정적 검사 파이프라인 SSOT" 기반이 완성된다.
