# ADR-002 Stage 2 → 3 Exit 판정 (2026-05-06)

- **판정일**: 2026-05-06 (수)
- **Stage 2 기간**: 2026-04-22 (수) ~ 2026-05-06 (수)
- **Pilot**: 팀메이트 4 (Tests / Doc-Sync / Static Checkers)
- **워크트리**: `../aqts-team4-skills-pilot` (branch: `pilot/team4-skills-w1` → `w2`)
- **판정자**: Pilot + 리드 공동
- **관련 문서**:
  - [ADR-002 Stage 2 Sandbox](../../adr-002-anthropic-skills-adoption.md) §5.3.1 (본 판정의 기준)
  - [skill-usage-log-W1.md](skill-usage-log-W1.md)
  - [skill-usage-log-W2.md](skill-usage-log-W2.md)

> **사용 안내**: 본 문서는 2026-04-23 에 리드가 **선제 스캐폴드**한 것으로, 2026-05-06 판정일에 Pilot/리드가 데이터를 채워넣는 것이 목적입니다. 각 `(입력 대기)` 란은 판정일 기준 **직전 24시간 내에** 확정해야 합니다 (W2 로그가 닫혀야 Gate C 의 마지막 3일 데이터가 안정).

---

## 1. Input 데이터 요약

### 1.1 W1/W2 로그에서 가져오는 값

| 항목 | W1 값 | W2 값 | 누적 | 출처 |
|---|---|---|---|---|
| 스킬 호출 총 횟수 | (입력 대기) | (입력 대기) | (합) | W1/W2 §2 표 행 수 |
| 고유 스킬 종류 수 | (입력 대기) | (입력 대기) | (합집합) | W1/W2 §2 "스킬 이름" 열 unique |
| 5회 이상 호출된 스킬 | (입력 대기) | — | — | §5.3.1 A 의 eval 대상 선정 |
| F1~F5 발현 건수 | (입력 대기) | (입력 대기) | (합) | W1/W2 §4 |
| F6/F7 발현 건수 | (입력 대기) | (입력 대기) | (합) | W1/W2 §4 |
| G1~G7 위반 총 건수 | (입력 대기) | (입력 대기) | (합) | W1/W2 §5 |

### 1.2 Pilot 과제 완료 여부 (W1/W2 §3)

- 과제 a (`scripts/check_vuln_ignore_parity.py` 신설): (입력 대기 — 완료/부분완료/미완료)
- 과제 b (SKILL.md 2종 실적용): (입력 대기 — 완료/부분완료/미완료)

---

## 2. Gate A — 스킬 출력 품질 (Output quality)

**기준** ([ADR-002 §5.3.1](../../adr-002-anthropic-skills-adoption.md#L220)):
- 5회 이상 호출된 공식 스킬 상위 **2종** 에 대해 with_skill / without_skill 비교 eval.
- Exit 임계값: `delta.pass_rate ≥ 0.2` AND `tokens_delta ≤ 2.0x`.

### 2.1 대상 스킬 선정

| Rank | 스킬 이름 | 누적 호출 | eval workspace 경로 |
|---|---|---|---|
| 1 | (입력 대기) | (N 회) | `<skill-name>-workspace/iteration-1/<eval-id>/` |
| 2 | (입력 대기) | (N 회) | `<skill-name>-workspace/iteration-1/<eval-id>/` |

### 2.2 Benchmark 결과

| 스킬 | `delta.pass_rate` | `tokens_delta` | A 통과 여부 |
|---|---|---|---|
| 1 | (입력 대기) | (입력 대기) | PASS/FAIL |
| 2 | (입력 대기) | (입력 대기) | PASS/FAIL |

**Gate A 종합**: (**PASS / FAIL**) — 상위 2종 모두 PASS 여야 A 전체 PASS. 1종이라도 FAIL 이면 A FAIL.

### 2.3 데이터 수집 명령 (판정일 실행 예시)

```bash
# 각 스킬 workspace 에서
for skill_dir in docs/architecture/sandbox/adr-002/*-workspace; do
  jq '{skill: input_filename, delta: .delta}' "$skill_dir/iteration-1/*/benchmark.json"
done
```

---

## 3. Gate B — 트리거 정확도 (Trigger accuracy)

**기준** ([ADR-002 §5.3.1](../../adr-002-anthropic-skills-adoption.md#L225)):
- 후보 스킬별 20-query eval set (should_trigger=true 8~10 + false 8~10, near-miss negative 필수).
- 각 쿼리 **3회 반복** 후 trigger rate 측정, threshold 0.5.
- Train/validation **60/40 split**, **validation pass rate ≥ 0.7**.
- SKILL.md `description` ≤ 1024 chars.

### 3.1 Eval 대상 스킬 (Gate A 대상과 동일)

| 스킬 | eval set 경로 | 20-query 완비 | 3× 반복 완료 |
|---|---|---|---|
| 1 | (입력 대기) | [ ] | [ ] |
| 2 | (입력 대기) | [ ] | [ ] |

### 3.2 Validation pass rate

| 스킬 | should_trigger 맞춤 | should_not 맞춤 | Validation pass rate | B 통과 여부 |
|---|---|---|---|---|
| 1 | (입력 대기) | (입력 대기) | (입력 대기) | PASS/FAIL |
| 2 | (입력 대기) | (입력 대기) | (입력 대기) | PASS/FAIL |

### 3.3 SKILL.md description 길이

| 스킬 | 문자 수 | ≤ 1024 |
|---|---|---|
| 1 | (입력 대기) | [ ] |
| 2 | (입력 대기) | [ ] |

**Gate B 종합**: (**PASS / FAIL**) — 모든 스킬이 pass rate ≥ 0.7 AND description ≤ 1024 여야 PASS.

---

## 4. Gate C — 정성적 판정 (Qualitative)

**기준** ([ADR-002 §5.3.1](../../adr-002-anthropic-skills-adoption.md#L231)):
- **F1~F5 중 1건 이상 발현 → Stop 또는 Pilot 교체**
- **F6/F7 발현 → Stage 3 확장 과제 편입 (Stop 아님)**
- Pilot 참조 횟수 **누적 5회 이상** (ADR-002 §2.2 Stop 조건 4)

### 4.1 실패 모드 집계 (W1+W2 합산)

| # | 실패 모드 | 누적 발현 | Gate C 영향 |
|---|---|---|---|
| F1 | AQTS 문맥 오염 | (입력 대기) | 1+ 발현 시 FAIL |
| F2 | Progressive disclosure 실패 | (입력 대기) | 1+ 발현 시 FAIL |
| F3 | 외부 도구 전제 실행 실패 | (입력 대기) | 1+ 발현 시 FAIL |
| F4 | 스킬 간 파일 충돌 | (입력 대기) | 1+ 발현 시 FAIL |
| F5 | 업스트림 Breaking change | (입력 대기) | 1+ 발현 시 FAIL |
| F6 | Gotchas 누락 | (입력 대기) | Stage 3 확장 과제 편입 (Stop 아님) |
| F7 | SKILL.md 500 줄 초과 | (입력 대기) | Stage 3 확장 과제 편입 (Stop 아님) |

### 4.2 Gotchas 위반 누적

| # | Gotcha | 누적 건수 |
|---|---|---|
| G1 | 한글 기술 서술 누락 | (입력 대기) |
| G2 | 기대값 수정 금지 위반 | (입력 대기) |
| G3 | 하드코딩 금지 위반 | (입력 대기) |
| G4 | CLAUDE.md §2 절대 규칙 위반 | (입력 대기) |
| G5 | grep/regex 기반 검사기 (AST 의무 위반) | (입력 대기) |
| G6 | RBAC Wiring 누락 | (입력 대기) |
| G7 | KST 통일 위반 | (입력 대기) |

### 4.3 참조 횟수 체크

- Pilot 누적 스킬 호출: (입력 대기) 회
- ADR-002 §2.2 Stop 조건 4 임계값: 5회
- 통과 여부: [ ] PASS  [ ] FAIL (Pilot 교체 분기)

**Gate C 종합**: (**PASS / FAIL**)
- F1~F5 0건 AND 누적 참조 ≥ 5회 → PASS
- F1~F5 1건 이상 → FAIL (Stop 후보)
- F1~F5 0건 AND 누적 참조 < 5회 → **Pilot 교체 분기** (FAIL 과 분리)
- F6/F7 1건 이상이라도 Stop 직결 아님. "Stage 3 확장 과제" 주석 추가.

---

## 5. 판정 매트릭스

| Gate A | Gate B | Gate C | **판정 결과** |
|---|---|---|---|
| PASS | PASS | PASS | **Stage 3 진입** — ADR-002 Status 갱신 + CLAUDE.md §9 [x] |
| PASS | PASS | Pilot 교체 분기 | **Stage 2 재시작 (팀메이트 1 로 Pilot 전환)** |
| PASS | PASS | FAIL (F1~F5) | **ADR Status=Rejected** |
| FAIL | * | * | **ADR Status=Rejected** (A 필수) |
| * | FAIL | * | **ADR Status=Rejected** (B 필수) |

F6/F7 발현은 Gate C 자체 PASS/FAIL 판정에 영향 없음. Stage 3 진입 시 별도 확장 과제로 기록.

---

## 6. 최종 판정

**판정 결과**: (입력 대기 — 위 매트릭스에서 1개 선택)

### 6.1 판정 근거 요약

(3~5 문장으로 Gate A/B/C 결과 + 판정 이유 기술. 판정일 작성.)

### 6.2 후속 액션 (판정 결과별)

**[Stage 3 진입] 선택 시**:
- [ ] ADR-002 Status: Proposed → Accepted (Stage 3)
- [ ] ADR-002 §5.3.2 (Stage 3 → 4 Exit) 가 Stage 3 기간 시작 기준 활성화
- [ ] CLAUDE.md §9 "ADR-002 Stage 2 Sandbox 관찰" TODO `[x]` 전환
- [ ] `agent_docs/governance.md §5` "Phase 2 이후 외부 참고" 문단 Stage 3 내용 추가
- [ ] 팀메이트 1/2/3 중 2명 이상 옵트인 공지 메일 (mailbox_new.sh)
- [ ] 2026-05-06 기준 Pilot worktree 가 main 을 아직 merge 하지 않았다면 즉시 merge (팀 4 `ops023-pilot-merge-confirm` B-3 옵션 처리)

**[Pilot 교체 (팀메이트 1)] 선택 시**:
- [ ] ADR-002 Status: Proposed (변경 없음)
- [ ] Stage 2 재시작: 2026-05-06 ~ 2026-05-20 (14일 Sandbox)
- [ ] 팀메이트 1 로 Pilot 전환 공지 메일 + `aqts-team1-strategy` worktree 에 `.claude/settings.local.json` 격리 override 세팅 (OPS-024 §3)
- [ ] 팀메이트 4 는 Stage 2 lockout 해제 → 과제 a/b 정상 진행 복귀
- [ ] W3/W4 로그 템플릿 스캐폴드 (본 decision 문서를 2026-05-20 판정용으로 복제)

**[ADR Status=Rejected] 선택 시**:
- [ ] ADR-002 Status: Proposed → Rejected
- [ ] Rejected 사유를 ADR-002 § "Alternatives considered" 에 추가
- [ ] CLAUDE.md §9 ADR-002 TODO `[x]` 전환 (Rejected 도 종결 상태)
- [ ] Pilot worktree 회수: `aqts-team4-skills-pilot` worktree 제거 + `pilot/team4-skills-w1` 브랜치 archive
- [ ] 팀메이트 4 lockout 해제, `.claude/skills/` 디렉토리 제거 PR
- [ ] 2026-05-06 이후 anthropic-skills 재심사는 ADR-002 Revision 또는 ADR-006 별도 발행

---

## 7. 서명

| 역할 | 이름 (initial) | 일자 | 서명 |
|---|---|---|---|
| Pilot (팀메이트 4) | (입력 대기) | 2026-05-06 | |
| 리드 | (입력 대기) | 2026-05-06 | |

> 양쪽 서명이 모두 있어야 본 판정이 효력 발생. 이견 있을 시 Plan Mode 로 재협의 후 재서명.

---

## 8. 변경 이력

| 날짜 | 변경 내용 | 작성자 |
|---|---|---|
| 2026-04-23 | 템플릿 선제 스캐폴드 (판정일 2026-05-06 대비) | 리드 |
