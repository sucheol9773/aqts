# Security 스캐너 의존성 분리 — `backend/requirements-security.txt` 신설 (OPS-025)

## 배경

`CLAUDE.md §9` 의 미해결 TODO **"pip-audit 하드코딩 해소 (발견 2026-04-21)"** 를
해소하기 위한 작업 기록. 2026-04-21 OPS-018
(`docs/operations/dev-deps-split-2026-04-21.md`) 에서 lint 도구(ruff/black) 를
`backend/requirements-dev.txt` 로 분리하면서, pip-audit 는 *"용도가 security scan
이라 별도 이슈로 분리 예정"* 이라는 코멘트(`requirements-dev.txt:32-33`) 와
함께 의도적으로 제외되어 있었다.

`.github/workflows/ci.yml` 의 `Install pip-audit` 스텝(구 라인 94-95) 에
`pip install pip-audit==2.7.3` 가 직접 박혀 있어, 향후 버전 bump 마다 워크플로
yml 을 수정해야 했다. 이는 `CLAUDE.md §1` 의 "하드코딩 금지" 규칙 위반이며
OPS-018 의 후속 작업으로 분명히 명시되어 있던 과제이다.

## 결정

**옵션 A — 별도 파일 (`backend/requirements-security.txt`) 신설** 채택.

옵션 B (`requirements-dev.txt` 에 `## ── Security Scanning ──` 섹션 추가, 단일
파일 병합) 는 다음 이유로 미채택:
- `requirements-dev.txt:17-21` 의 설계 결정 코멘트가 "lint 잡이 runtime 패키지를
  매번 설치하지 않도록" 의 의도를 명시하는데, 이는 lint 와 security 가 다른
  용도라는 점을 이미 전제로 한다. 같은 파일에 합치면 그 의도가 흐려진다.
- 향후 bandit / safety 등 추가 보안 도구를 도입하면 자연스럽게 `dev` 와
  `security` 가 분리되는데, 미리 분리해두면 그 시점의 마이그레이션 비용이 0.
- `requirements-dev.txt:32-33` 의 기존 코멘트가 "별도 이슈로 분리 예정" 이라는
  표현으로 *별도 파일* 을 시사. 옵션 A 가 작성자의 의도와 일치.

## 변경 파일

| 파일 | 변경 종류 | 요약 |
|---|---|---|
| `backend/requirements-security.txt` | **신설** | `pip-audit==2.7.3` SSOT. `requirements-dev.txt:1-46` 의 헤더 코멘트 형식 그대로 따름. `-r requirements.txt` 의도적 미포함 (OPS-018 동일 논리). |
| `.github/workflows/ci.yml` | 수정 (lint job) | (1) `cache-dependency-path` 를 multi-line YAML literal 로 두 파일 명시. (2) `Install pip-audit` 스텝 → `Install security scanners` 로 리네임 + `pip install -r backend/requirements-security.txt` 로 전환. |
| `backend/requirements-dev.txt` | 수정 (코멘트만) | 라인 32-33 의 "분리 예정" 표현을 "분리 완료, OPS-025" 로 갱신. |
| `README.md` | 수정 (1줄 추가) | 프로젝트 구조 트리(라인 83 부근) 에 `requirements-security.txt` 한 줄 추가. §2.4 본문은 lint 셋업 목적이라 변경하지 않음 (security 는 별개 lifecycle). |
| `CLAUDE.md` §9 | 수정 (TODO 갱신) | `[ ] pip-audit 하드코딩 해소` → `[x] (완료, 2026-04-25)` + 변경 내역 / OPS-025 reference. |

## 회귀 방어선

- **silent miss 방지** — `cache-dependency-path` 에 두 파일을 모두 등록한다.
  multi-line YAML literal (`|`) 로 `requirements-dev.txt` 와
  `requirements-security.txt` 를 모두 명시. 어느 한 파일이라도 변경되면
  pip cache 가 invalidate 되어 stale 의존성으로 빌드되는 것을 방지.
- **OPS-018 패턴 재사용** — ruff/black 분리 시 학습한 "별도 파일 + SSOT" 구조를
  그대로 따라가므로 신규 함정 없음. 동일한 cache-bust 패턴이 두 번째 적용되는
  것이라 검증된 메커니즘.
- **버전 bump 검증** — 향후 `pip-audit==X.Y.Z` 로 bump 시
  `requirements-security.txt` 한 줄만 수정하면 끝. yml 수정 불필요한 것이 본
  작업의 핵심 효과.
- **로컬 vs CI 일치** — 로컬 개발자가 `pip install -r backend/requirements-security.txt`
  로 동일 버전을 설치할 수 있으므로 OPS-018 과 같은 "로컬 26.x vs CI 24.x drift"
  류의 silent miss 가 본 도구에서는 구조적으로 차단된다.

## 검증

### 로컬

```bash
cd backend
pip install -r requirements-dev.txt -r requirements-security.txt
pip-audit --version          # 2.7.3 출력
ruff check . --config pyproject.toml
black --check . --config pyproject.toml
pip-audit -r requirements.txt --strict --vulnerability-service osv
```

### CI

- lint job 의 새 step 이름 `Install security scanners` 가 통과
- `Python 의존성 CVE 스캔 (pip-audit)` step 이 동일 결과 (active CVE 0건 또는
  기존 ignore 와 동일)
- `cache-dependency-path` 변경으로 lint job 의 pip cache 가 한 번 invalidate
  되는 것이 정상. 다음 push 부터 hit 복구.

## 후속 작업 (Out-of-Scope)

- bandit / safety 등 추가 보안 도구 도입은 별도 PR. 본 분리로 추가 시 비용 0.
- pip-audit 자체의 버전 bump 도 별도 PR. 이제 한 줄 수정으로 가능.
- `OPS-021` 로 예약된 lxml 6.1.0 업그레이드는 별개 작업. 본 분리는 그 작업과
  독립적으로 통과.
