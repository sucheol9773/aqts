# ADR-002 Stage 2 Sandbox — Week 1 관찰 로그

- **기간**: 2026-04-22 (수) ~ 2026-04-29 (수)
- **Pilot**: 팀메이트 4 (Tests / Doc-Sync / Static Checkers)
- **워크트리**: `../aqts-team4-skills-pilot` (branch: `pilot/team4-skills-w1`)
- **점검일**: 2026-04-29 (수) — Pilot 직접 작성 → 리드 공동 리뷰
- **관련 ADR**:
  - [ADR-002 Stage 2 Sandbox](../../adr-002-anthropic-skills-adoption.md) §7.5.5 (템플릿 출처), §5.3.1 (Exit Criteria)
  - ADR-005 Phase 2a Pilot (미발행 — 본 로그가 초안 근거 데이터)

---

## 1. 주간 개괄

> Pilot 이 직접 작성. W1 관찰 창 종료일(2026-04-29) 에 3~5 문단으로 서술.
> 필수 내용: 과제 a/b 진척도, 가장 큰 마찰 지점 1건, 다음 주 핵심 결정 1건.

(작성 대기)

---

## 2. 호출된 스킬 목록 (ADR-002 §7.5.5 항목 2)

각 호출은 아래 표에 1 행씩 기록. timestamp 는 KST 기준.

| # | timestamp (KST) | 스킬 이름 | 트리거 출처 (user prompt 요약) | 산출물 경로 | 결과 (pass/fail/partial) | 소요 토큰 (대략) | 비고 |
|---|---|---|---|---|---|---|---|
| 1 |  |  |  |  |  |  |  |

> 최소 1행 이상 필수. W1 목표: **누적 2회 이상** (§7.5.5 항목 3).

**W1 누적 참조 횟수**: 0회 (작성 대기)

---

## 3. Pilot 과제 진척도

### 3.1 과제 a — `scripts/check_vuln_ignore_parity.py` 신설

- **스코프 확정**: `.grype.yaml` ↔ `backend/.pip-audit-ignore` CVE/GHSA 차집합 감지. `# grype-only`/`# pip-audit-only` 주석 마커 예외 허용. Doc Sync 워크플로 0 errors 강제.
- **CLAUDE.md §9 대응 TODO**: "`.grype.yaml` ↔ `backend/.pip-audit-ignore` parity 정적 검사기 신설 (발견 2026-04-22)"
- **완료 기준**: CI 녹색 + 테스트 7건 통과 + OPS-022 작성 + CLAUDE.md §9 해당 TODO `[x]` 전환.

| 체크포인트 | 상태 | 근거 커밋/PR |
|---|---|---|
| 스크립트 초안 작성 (`scripts/check_vuln_ignore_parity.py`) | [ ] |  |
| 테스트 하니스 (`backend/tests/test_check_vuln_ignore_parity.py`) 7 시나리오 | [ ] |  |
| `.github/workflows/doc-sync-check.yml` 스텝 추가 | [ ] |  |
| `docs/operations/check-vuln-ignore-parity-2026-04-23.md` (OPS-022) 작성 | [ ] |  |
| CLAUDE.md §9 TODO `[x]` 전환 | [ ] |  |

### 3.2 과제 b — SKILL 템플릿 2종 실적용

- **대상**: `.claude/skills/aqts-doc-sync-runner/SKILL.md` + `.claude/skills/aqts-rbac-route-checker/SKILL.md`
- **출처**: ADR-002 §7.5.2 템플릿 2종. Pilot 이 실제 파일로 작성 + `skills-ref validate` 통과.
- **완료 기준**: 두 SKILL 디렉토리 생성 + validate 통과 + Pilot 세션이 실제로 각 SKILL 을 **1회 이상 자동 트리거** (§2 호출 표에 기록되어야 함).

| 체크포인트 | 상태 | 근거 커밋/PR |
|---|---|---|
| `.claude/skills/aqts-doc-sync-runner/SKILL.md` 작성 (500 줄 이하, G1~G7 명시, PEP 723) | [ ] |  |
| `.claude/skills/aqts-rbac-route-checker/SKILL.md` 작성 | [ ] |  |
| `skills-ref validate` (또는 동등 수동 검증) 통과 | [ ] |  |
| Pilot 세션에서 `aqts-doc-sync-runner` 자동 트리거 ≥ 1회 | [ ] |  |
| Pilot 세션에서 `aqts-rbac-route-checker` 자동 트리거 ≥ 1회 | [ ] |  |

---

## 4. 실패 모드 발현 여부 (ADR-002 §2.4 F1~F7)

| # | 실패 모드 | 발현 여부 | 발현 시 상세 (timestamp / 호출 맥락 / 영향) |
|---|---|---|---|
| F1 | 스킬 프롬프트가 AQTS 문맥을 오염 (한국어 절대 규칙 누락 등) | [ ] | |
| F2 | 스킬 호출이 기대 산출물을 생성하지 못함 (progressive disclosure 실패) | [ ] | |
| F3 | 스킬이 AQTS 에 없는 외부 도구(bash cmd) 를 전제하여 실행 실패 | [ ] | |
| F4 | 스킬 간 충돌 (두 스킬이 같은 파일을 동시 수정 시도) | [ ] | |
| F5 | 업스트림 릴리스가 Breaking change 를 도입 (SKILL.md 포맷 변경 등) | [ ] | |
| F6 | Gotchas 누락 — AQTS 고유 규칙을 스킬이 재현하지 못함 (G1~G7 위반) | [ ] | |
| F7 | SKILL.md 가 500 줄 초과 — progressive disclosure 위반 | [ ] | |

> 1건 이상 발현 시 ADR-002 §5.3.1 Gate C 미충족 → Stage 3 진입 불가 + ADR Status=Rejected 후보.

---

## 5. Gotchas 위반 건수 (ADR-002 §7.4 G1~G7)

| # | Gotcha | 위반 건수 | 위반 상세 (timestamp / 파일 / 설명) |
|---|---|---|---|
| G1 | 한글 기술 서술 누락 (영문으로만 작성된 .md 또는 커밋 메시지) | 0 | |
| G2 | 기대값 수정 금지 위반 (실패 테스트의 기대값을 임의 변경) | 0 | |
| G3 | 하드코딩 금지 위반 (임계값/API 키/계좌번호를 코드에 직접 작성) | 0 | |
| G4 | 절대 규칙 위반 (CLAUDE.md §2 7가지 원칙 중 1개 이상 위반) | 0 | |
| G5 | grep 금지 위반 (AST 검사기를 grep/regex 기반으로 작성) | 0 | |
| G6 | RBAC Wiring 위반 (신규 라우트에 `require_*` 가드 누락) | 0 | |
| G7 | KST 통일 위반 (timezone.utc 직접 사용, `today_kst_str()` 미사용) | 0 | |

> 합계 1건 이상이면 G 가 ADR-002 §5.3.1 Gate C 에 카운트됨.

---

## 6. ADR-005 Phase 2a Pilot 데이터 수집 (보조 — Multi-Agent 전환 근거)

> 본 섹션은 ADR-002 §7.5.5 의 필수 항목은 아니며, ADR-005 초안이 작성될 때 §2.3 평가표의 1열을 채우기 위한 **보조 데이터** 로 기록한다. 선택적.

### 6.1 토큰 사용량 실측

| 세션 | 일자 | 총 소모 토큰 (대략) | 주 과제 | 비고 (대비 기준: 리드 단일 세션 평균) |
|---|---|---|---|---|
|  |  |  |  |  |

> CLAUDE.md §8 은 "3~5배" 경험칙만 기록. 본 표가 첫 실측 데이터셋.

### 6.2 워크트리 독립성 위반 0건 기대

| 일자 | 이벤트 | 상세 (파일/명령) |
|---|---|---|

> Pilot 이 `../aqts-team4-skills-pilot` 외부의 main 레포를 직접 수정하려 한 시도가 있으면 기록. 0 기대.

### 6.3 리드 중재 개입 이벤트

| 일자 | 이벤트 유형 | 상세 | 해소 시간 |
|---|---|---|---|

> "Pilot 이 자력으로 진행 불가하여 리드 판단을 요청" 사례. W1 목표: ≤ 2건 (숙달 전이라 일부 허용).

---

## 7. 다음 주 액션 결정 (ADR-002 §7.5.5 항목 6)

W1 종료 시점에 아래 3 중 하나 선택:

- [ ] **Stage 2 계속 (W2 진입)** — 기본 경로. F1~F7 미발현 + 누적 참조 ≥ 2회 달성 시.
- [ ] **Pilot 교체 (팀메이트 1 로 전환)** — 누적 참조 < 2회 이나 F1~F7 미발현 시. ADR-002 §2.2 Stop 조건 4 발동.
- [ ] **Stop (Stage 2 조기 종료)** — F1~F7 중 1건 이상 발현 시. ADR Status=Rejected 후보.

**W1 종료 결정 (Pilot + 리드 공동 서명)**:

- Pilot 서명 (date / initial):
- 리드 서명 (date / initial):
- 결정: (위 3 항목 중 선택)

---

## 8. 변경 이력

| 날짜 | 변경 내용 | 작성자 |
|---|---|---|
| 2026-04-22 | 템플릿 선제 스캐폴드 (`chore/adr-002-w1-log-scaffold`) | 리드 |
