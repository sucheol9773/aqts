# ADR-002 Stage 2 Sandbox — Week 2 관찰 로그

- **기간**: 2026-04-29 (수) ~ 2026-05-06 (수)
- **Pilot**: 팀메이트 4 (Tests / Doc-Sync / Static Checkers) — W1 결정에 따라 팀메이트 1 로 교체될 수 있음
- **워크트리**: `../aqts-team4-skills-pilot` (branch: `pilot/team4-skills-w2` — W1 브랜치와 분리)
- **점검일**: 2026-05-06 (수) — **Stage 2 Exit 판정일**
- **관련 ADR**:
  - [ADR-002 Stage 2 Sandbox](../../adr-002-anthropic-skills-adoption.md) §5.3.1 (Stage 2→3 Exit Criteria), §7.5.5 (템플릿 출처)
  - [skill-usage-log-W1.md](./skill-usage-log-W1.md) (전주 로그 — Exit 판정 시 합산 근거)
  - ADR-005 Phase 2a Pilot (미발행 — 본 로그가 초안 근거 데이터)

---

## 1. 주간 개괄

> Pilot 이 직접 작성. W2 관찰 창 종료일(2026-05-06) 에 3~5 문단으로 서술.
> 필수 내용: W1 대비 누적 진척, 과제 a/b 최종 완료 여부, Stage 3 진입 판정 근거 요약.

(작성 대기)

---

## 2. 호출된 스킬 목록 (ADR-002 §7.5.5 항목 2)

| # | timestamp (KST) | 스킬 이름 | 트리거 출처 (user prompt 요약) | 산출물 경로 | 결과 (pass/fail/partial) | 소요 토큰 (대략) | 비고 |
|---|---|---|---|---|---|---|---|
| 1 |  |  |  |  |  |  |  |

> W2 목표: **W1+W2 누적 5회 이상** (§7.5.5 항목 3, ADR-002 §5.3.1 Gate C 의 "참조 ≥ 5회" 와 일치).

**W2 개별 참조 횟수**: 0회 (작성 대기)
**W1+W2 누적 참조 횟수**: (W1 값 + W2 값 — W1 로그에서 복사하여 합산)

---

## 3. Pilot 과제 최종 완료 체크

### 3.1 과제 a — `scripts/check_vuln_ignore_parity.py`

| 체크포인트 | 상태 | 근거 커밋/PR |
|---|---|---|
| 스크립트 최종 병합 (main) | [ ] |  |
| 테스트 7 시나리오 CI 통과 | [ ] |  |
| Doc Sync 워크플로에서 0 errors 확인 | [ ] |  |
| OPS-022 문서 병합 | [ ] |  |
| CLAUDE.md §9 TODO `[x]` 전환 커밋 | [ ] |  |

### 3.2 과제 b — SKILL 템플릿 2종

| 체크포인트 | 상태 | 근거 커밋/PR |
|---|---|---|
| `.claude/skills/aqts-doc-sync-runner/SKILL.md` 병합 | [ ] |  |
| `.claude/skills/aqts-rbac-route-checker/SKILL.md` 병합 | [ ] |  |
| 두 SKILL 각 누적 트리거 ≥ 1회 확인 (§2 표 근거) | [ ] |  |
| SKILL.md 500 줄 이하 + G1~G7 명시 검증 | [ ] |  |

---

## 4. 실패 모드 최종 판정 (ADR-002 §2.4 F1~F7)

W1+W2 누적 관찰 결과. **1건이라도 발현 시 Stage 3 진입 불가**.

| # | 실패 모드 | 발현 여부 | 발현 주차 (W1/W2) | 상세 (timestamp / 호출 맥락 / 영향) |
|---|---|---|---|---|
| F1 | 스킬 프롬프트가 AQTS 문맥을 오염 | [ ] | | |
| F2 | 스킬 호출이 기대 산출물을 생성하지 못함 | [ ] | | |
| F3 | 외부 도구 전제로 실행 실패 | [ ] | | |
| F4 | 스킬 간 충돌 | [ ] | | |
| F5 | 업스트림 Breaking change | [ ] | | |
| F6 | Gotchas 누락 (G1~G7 재현 실패) | [ ] | | |
| F7 | SKILL.md 500 줄 초과 | [ ] | | |

---

## 5. Gotchas 위반 누적 (ADR-002 §7.4 G1~G7)

| # | Gotcha | W1 건수 | W2 건수 | 누적 건수 |
|---|---|---|---|---|
| G1 | 한글 기술 서술 누락 | 0 | 0 | 0 |
| G2 | 기대값 수정 금지 위반 | 0 | 0 | 0 |
| G3 | 하드코딩 금지 위반 | 0 | 0 | 0 |
| G4 | 절대 규칙 위반 | 0 | 0 | 0 |
| G5 | grep 금지 위반 | 0 | 0 | 0 |
| G6 | RBAC Wiring 위반 | 0 | 0 | 0 |
| G7 | KST 통일 위반 | 0 | 0 | 0 |
| **합계** | | 0 | 0 | 0 |

---

## 6. Stage 2 → Stage 3 Exit Criteria 판정 (ADR-002 §5.3.1)

세 게이트 **모두 충족** 해야 Stage 3 진입.

### Gate A — Output quality (정량)

| 지표 | 목표 | 실측 | 판정 |
|---|---|---|---|
| `delta.pass_rate` (스킬 유/무 비교) | ≥ 0.2 | | [ ] PASS / [ ] FAIL |
| `tokens_delta` (스킬 사용 시 토큰 배수) | ≤ 2.0x | | [ ] PASS / [ ] FAIL |

> 측정 방법: agentskills.io/skill-creation/evaluating-skills 표준 — `evals/evals.json` 기준 with/without skill 비교, 3회 반복 평균.

### Gate B — Trigger accuracy (정량)

| 지표 | 목표 | 실측 | 판정 |
|---|---|---|---|
| 20-query eval (positive 8~10 + negative 8~10) / 3 runs / validation pass rate | ≥ 0.7 | | [ ] PASS / [ ] FAIL |

> 측정 방법: 60/40 train/val split, 0.5 threshold, agentskills.io/skill-creation/optimizing-descriptions 표준.

### Gate C — 정성 관찰

| 지표 | 목표 | 실측 | 판정 |
|---|---|---|---|
| 누적 참조 횟수 (W1+W2) | ≥ 5회 | | [ ] PASS / [ ] FAIL |
| F1~F7 실패 모드 발현 | 0건 | | [ ] PASS / [ ] FAIL |

### 최종 전환 결정

- [ ] **Gate A + B + C 모두 PASS → Stage 3 Limited Rollout 진입** (2026-05-06 ~ 2026-06-05, ADR-002 §2.1)
- [ ] **Gate C 에서 참조 < 5회 (F1~F7 미발현) → Pilot 교체 + Stage 2 재시작** (팀메이트 1 로 전환, 2026-05-06 ~ 2026-05-20)
- [ ] **F1~F7 중 1건 이상 발현 또는 Gate A/B FAIL → Stop + ADR Status=Rejected** (CLAUDE.md §9 Stage 2 관찰 TODO 에 결과 기록)

**판정 서명**:

- Pilot 서명 (date / initial):
- 리드 서명 (date / initial):
- 결정: (위 3 항목 중 선택)

---

## 7. ADR-005 Phase 2a Pilot 데이터 최종 집계 (보조)

> W1+W2 누적 실측. ADR-005 초안 작성 시 §2.3 평가표의 Phase 2a 열을 채우는 근거.

### 7.1 토큰 사용량 누적

| 주차 | 세션 수 | 총 소모 토큰 | 리드 단일 세션 평균 대비 배수 |
|---|---|---|---|
| W1 | | | |
| W2 | | | |
| 합계 | | | |

> CLAUDE.md §8 "3~5배" 경험칙 vs 실측 비교. ADR-005 Phase 2b (2 에이전트) 진입 판정 시 핵심 데이터.

### 7.2 워크트리 독립성

| 주차 | 위반 건수 | 상세 |
|---|---|---|
| W1 | 0 | |
| W2 | 0 | |

> 0 기대. 1건 이상 발생 시 ADR-005 Phase 2b (다중 워크트리) 진입 불가.

### 7.3 리드 중재 개입 건수

| 주차 | 건수 | 평균 해소 시간 | 상세 |
|---|---|---|---|
| W1 | | | |
| W2 | | | |

> W2 목표: ≤ 1건 (W1 대비 감소 추세). 감소 추세 확인 시 ADR-005 Phase 2b 진입 근거 확보.

---

## 8. 변경 이력

| 날짜 | 변경 내용 | 작성자 |
|---|---|---|
| 2026-04-22 | 템플릿 선제 스캐폴드 (`chore/adr-002-w1-log-scaffold`) | 리드 |
