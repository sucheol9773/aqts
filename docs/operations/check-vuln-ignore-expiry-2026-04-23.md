# check_vuln_ignore_expiry.py — 화이트리스트 만료일 정적 검사기 (OPS-023)

**작업일**: 2026-04-23
**브랜치**: `chore/check-vuln-ignore-expiry`
**소유**: 리드 (ADR-002 Pilot lockout 기간, 2026-05-06 이후 팀메이트 4 인계)
**후속**: CLAUDE.md §9 "whitelist 만료일 자동 검증" TODO

---

## 1. 배경

OPS-022 (`check_vuln_ignore_parity.py`) 는 `.grype.yaml` 과 `backend/.pip-audit-ignore`
의 **집합 차이**만 검증한다. 화이트리스트 엔트리는 각 파일의 규칙상 `YYYY-MM-DD`
만료일 명시를 의무화하지만, 실제 만료 시점에 아무도 갱신 PR 을 올리지 않으면
다음 사일런스 경로가 열린다:

1. 엔트리의 만료일이 경과
2. `.github/workflows/ci.yml` 의 pip-audit 스텝이 `TODAY (UTC) 와 만료일 비교` 로
   해당 엔트리를 무효화 → CVE 가 pip-audit 결과에 **재노출**
3. pip-audit 은 `--strict` 로 실행되어 CI 가 차단
4. 차단 시점에서야 "왜 지금 깨졌지" 를 역추적

즉, **만료 당일에 CI 가 빨갛게 되어야 비로소 대응**하는 반응형 경로였다. 본 검사기는
만료일을 사전 검증하여 만료 이전에 **갱신 PR 을 선제적으로 유도**한다.

### 2026-04-23 PR #38 hotfix 와의 관계

PR #37 (OPS-022 parity 검사기) 머지 직후 main CD 의 grype 스캔이 **CVE-2026-3298**
(Python 3.11.15, no-fix, High) 로 차단되어 PR #38 로 hotfix. 이 케이스는 parity
검사기로 잡을 수 없는 "완전히 새로운 CVE" 카테고리 — grype DB 갱신이 원인이다.
본 만료일 검사기도 마찬가지로 신규 CVE 자체는 포착하지 못하지만, **만료된
화이트리스트 엔트리** 카테고리를 구조적으로 차단하여 시큐리티 레이어의 분할
책임을 완성한다.

---

## 2. 설계 목표

1. **선제적 차단**: 만료 당일에 CI 가 빨갛게 되는 것이 아니라, 만료 이전 / 당일에
   이미 PR 레벨에서 "만료일이 이틀 뒤다" / "만료되었다" 를 고지.
2. **단일 진실원천**: 만료일 파싱 로직은 본 검사기 + 기존 pip-audit workflow
   스텝 두 곳이 동일한 `YYYY-MM-DD` 규칙을 공유. 규칙 변경 시 두 곳을 함께 수정.
3. **영구 예외 금지**: 만료일이 없는 엔트리도 error. 파일 상단 규칙과 정합.
4. **테스트 가능성**: `check_expiry(entries, today)` 에 `today` 를 주입 가능하게
   하여 회귀 테스트가 시간을 고정할 수 있도록.

### 의도적 비포함

- **사전 경고 윈도우**: "만료 7일 전부터 warning" 같은 soft 경고는 v1 에 포함하지
  않음. 이유는 (a) `Doc Sync` 워크플로의 최소 게이트 철학상 "warning 도 에러"
  원칙이 있어 soft 단계가 의미 없고, (b) 만료일을 한 번에 하드 차단하면 갱신
  PR 이 즉시 필요해져 silent drift 가 없다는 장점이 크다. 향후 필요 시 별도
  스위치로 추가.
- **만료일 자동 연장**: 스크립트가 자동으로 만료일을 수정하지 않음. 갱신은 반드시
  사람이 사유와 함께 PR 을 올리는 경로.

---

## 3. 구현 개요

### 파일

| 경로 | 역할 |
|---|---|
| `scripts/check_vuln_ignore_expiry.py` | 검사 스크립트 |
| `backend/tests/test_check_vuln_ignore_expiry.py` | 회귀 테스트 (13 tests) |
| `.github/workflows/doc-sync-check.yml` | Doc Sync 워크플로 스텝 |

### 파싱

- `_ID_PATTERN` 은 parity 검사기와 동일 — 두 검사기가 같은 domain 대상이므로 의도적
  통일. 변경 시 두 스크립트를 함께 갱신한다.
- 라인에서 `#` 이후 부분만 대상으로 `\d{4}-\d{2}-\d{2}` 를 탐색. ID 자체의
  `CVE-2026-...` 에 포함된 연도가 날짜로 오인되지 않도록 `#` 기준으로 제한.
- 달력상 존재하지 않는 날짜 (`2026-02-30` 등) 는 `None` 으로 귀결되어 "만료일 없음"
  error 로 처리.

### 판정

- `entry.expiry is None` → error (만료일 없음)
- `entry.expiry < today` → error (만료됨, 경과 일수 표기)
- `entry.expiry >= today` → pass (오늘 당일은 아직 유효)

### 시간 기준

`datetime.now(timezone.utc).date()` 로 UTC 고정. `date.today()` 는 러너 로컬을
쓰는데, CI 러너는 UTC 이지만 로컬 개발자 환경이 KST 일 때 하루 경계에서
판정이 갈라질 수 있어 UTC 명시.

---

## 4. 초기 상태 정합

검사기 실행 시 현재 저장소 상태:

```
vuln-ignore expiry OK (grype=26, pip-audit=4, reference=2026-04-23 UTC)
```

모든 엔트리의 만료일이 `2026-06-06` (현재 +44일). 즉, 본 검사기는 **2026-06-06
이후 즉시 차단** 한다. 그 전에 수행해야 할 만료 해소 작업:

| 엔트리 | 해소 경로 | 담당 TODO (CLAUDE.md §9) |
|---|---|---|
| GHSA-7gcm-g887-7qv7 (protobuf) | OTel 1.27+ 업그레이드 PR | - |
| GHSA-jr27-m4p2-rc6r / GHSA-wj6h-64fc-37mp | python-jose → PyJWT 마이그레이션 | - |
| GHSA-vfmq-68hx-4jfw (lxml) | lxml 6.1.0 업그레이드 (팀 3 PR #31) | lxml TODO |
| OS/Python 3.11 CVE 다수 | debian 백포트 또는 3.12 업그레이드 | 3.12 upgrade PR |

---

## 5. 회귀 테스트 구조

`test_check_vuln_ignore_expiry.py` 는 13 tests / 6 그룹. parity 검사기 및 Stage
2/3 (`check_bool_literals` / `check_rbac_coverage`) 와 동일 계보:

1. **유효 만료일 통과** (2): 미래 날짜, 빈 파일
2. **만료/결손 에러** (3): 과거 날짜(하루), 과거 날짜(100일), 만료일 없음
3. **경계 케이스** (3): 오늘 당일 통과, 달력상 없는 날짜, 혼재 시 선별
4. **파서 오탐 방지** (2): 주석 내 복수 날짜, ID 안 연도 misparse
5. **실제 레포 회귀 고정** (1): 현재 저장소 엔트리 통과 검증
6. **main() 진입** (2): subprocess exit 0, 파일 누락 시 exit 1

`TODAY = date(2026, 4, 23)` 로 테스트 고정. 실제 오늘이 바뀌어도 회귀 테스트는
안정적.

---

## 6. 운영 가이드

### 만료 임박 시

1. 근본 해소 가능: upstream fix 적용 PR + 만료 엔트리 삭제.
2. 근본 해소 불가 (upstream 미해결 등): 만료일 연장 PR. 연장 사유에 새 근거를
   추가하여 기존 엔트리 교체. 연장은 **최대 3개월** 관례 (규칙 아님).

### 신규 CVE 억제 시

parity 검사기 통과 방법을 따라 두 파일 동시 업데이트 + 본 만료일 검사기 통과를
위해 `YYYY-MM-DD` 만료일 명시.

### 로컬 검증

```bash
python scripts/check_vuln_ignore_expiry.py
python scripts/check_vuln_ignore_parity.py
cd backend && .venv/bin/python -m pytest tests/test_check_vuln_ignore_expiry.py -q
```

---

## 7. 검증

- 로컬 실행: `vuln-ignore expiry OK (grype=26, pip-audit=4, reference=2026-04-23 UTC)`
- 회귀 테스트: 13 passed / 2.95s
- Doc Sync 워크플로에 스텝 추가 (기존 parity 스텝 직후)
- 문서-only 최소 게이트 (ruff / black / check_bool_literals / check_doc_sync) 통과

---

## 8. 회귀 방어 포지션

| 차단 카테고리 | 담당 |
|---|---|
| 집합 비대칭 (한 파일만 등록) | `check_vuln_ignore_parity.py` (OPS-022) |
| 만료된 화이트리스트 | `check_vuln_ignore_expiry.py` (OPS-023, 본 문서) |
| 만료일 형식 오류 | pip-audit workflow 스텝 (ci.yml) + 본 검사기 |
| 신규 CVE 자체 | grype scan / pip-audit strict |

네 축이 서로 다른 카테고리를 담당하며 집합적으로 공급망 보안 레이어를 완성한다.

---

## 9. 관련 문서

- OPS-022: `docs/operations/check-vuln-ignore-parity-2026-04-23.md`
- OPS-019: `docs/operations/check-bool-literals-ast-2026-04-22.md`
- OPS-020: `docs/operations/check-rbac-coverage-tests-2026-04-22.md`
- 공급망 정책: `docs/security/supply-chain-policy.md`
- CLAUDE.md §7: 공급망 보안 요약
- CLAUDE.md §9: TODO 목록

---

## 10. 팀 소유권

- 현재: 리드 임시 소유 (2026-05-06 ADR-002 Pilot lockout 기간 중).
- 2026-05-06 이후: 팀메이트 4 (Tests / Doc-Sync / Static Checkers) 에 이관.
