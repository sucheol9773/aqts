# ADR-002 — Anthropic Agent Skills (`anthropic/skills`) 단계적 채택

- **상태 (Status)**: Proposed
- **작성일**: 2026-04-22
- **결정자**: 리드 (사용자 본인)
- **Pilot 담당**: 팀메이트 4 (Tests / Doc-Sync / Static Checkers)
- **Pilot 교체 지정자**: 팀메이트 1 (Strategy / Backtest) — Stage 2 §2.2 Stop 조건 4 발동 시
- **심사 프레임워크**: [ADR-001 Phase 2 진입 gate 및 외부 참고 도구 심사 프레임워크](./adr-001-phase2-entry-gate.md)
- **관련 문서**:
  - [agent_docs/governance.md §5 — 외부 자원 수용 정책](../../agent_docs/governance.md#5-외부-자원-수용-정책)
  - [agent_docs/development-policies.md §5 — Wiring Rule](../../agent_docs/development-policies.md)
  - [CLAUDE.md §9 — 미해결 TODO](../../CLAUDE.md)

---

## 1. 배경 (Context)

ADR-001 (Phase 2 진입 gate) 머지 직후, §2.4 후속 ADR 목록에서 우선순위 1 로 지정된 외부 참고 도구 심사다. 2025-12 에 `SKILL.md` 가 Anthropic 의 open standard 로 공개되면서 Claude Code · Cursor · ChatGPT 등 다수 agent 플랫폼에서 호환되는 공유 포맷이 되었고, 본 샌드박스에서도 `anthropic-skills:xlsx` · `pptx` · `docx` · `pdf` · `schedule` · `skill-creator` 등이 이미 availability 상태로 노출되어 있다.

### 1.1 AQTS 에서의 예상 사용처

AQTS 는 backend-only 시스템이지만 정기적으로 **문서 산출물** 을 생성한다.

1. **팀메이트 4** — 주간 테스트 커버리지 리포트, 정적 검사기 결과 요약, FEATURE_STATUS 스냅샷. 현재는 `.md` 수기 작성 + `scripts/gen_status.py` 자동화 혼합. `xlsx` / `docx` 스킬로 구조화하면 리드 리뷰 비용 감소 예상.
2. **팀메이트 1** — OOS 결과 리포트, 하이퍼옵트 수렴 곡선, 백테스트 비교 테이블. 현재는 `scripts/run_*` 산출 CSV 를 수동 편집. `xlsx` 스킬로 자동 차트 생성 가능.
3. **팀메이트 2** — 배포 후 smoke test 결과, 알림 파이프라인 헬스체크 요약. `pdf` 스킬로 포스트모템 템플릿화 가능성.
4. **팀메이트 3** — RBAC 권한 매트릭스 스냅샷, API 계약 diff. 현재는 `agent_docs/api_contracts.md` 수기 관리. `docx` 스킬로 외부 공유용 포맷 변환 가능.

단일 Pilot 으로 검증하기 위해 가장 산출 빈도가 높은 **팀메이트 4** 를 Pilot 으로 지정한다.

### 1.2 Pilot 팀메이트 4 지정 근거

| 축 | 팀메이트 4 | 팀메이트 1 | 팀메이트 2/3 |
|---|---|---|---|
| SKILL.md 산출 친화성 | 주간 리포트 / FEATURE_STATUS 등 정형 산출 | OOS / 하이퍼옵트 결과 CSV ↔ `xlsx` 매핑 직접 | 운영 리포트는 비정기 / API 매트릭스는 이미 `agent_docs/` |
| 14일 내 5회 참조 달성 가능성 | 높음 (주 2~3건 정형 리포트) | 중간 (하이퍼옵트 run 주기 의존) | 낮음 (운영 이벤트 비정기) |
| 회귀 발생 시 영향 범위 | 문서 / `scripts/gen_status.py` 주변으로 격리 | `backend/core/oos/` 결과 해석 경로에 영향 가능 | 알림 파이프라인 / RBAC 라우트에 영향 가능 (고위험) |
| Agent Teams 관점 부하 | 기존 Doc-Sync 업무와 동일 레인 | Hyperopt 작업 중단 없이 병행 가능 | mutation 경로 집중 기간과 충돌 가능 |

팀메이트 4 는 산출 빈도 · 회귀 격리성 · 일상 레인 자연 통합 세 축에서 모두 우수하다. 팀메이트 2/3 는 실시간 운영 경로를 소유하여 Pilot 기간의 관찰 부담을 지우기에 부적절하다.

### 1.3 커뮤니티 스킬은 본 ADR 의 범위 밖

ADR-001 §6.1 은 본 ADR 의 scope 를 **Anthropic 공식 스킬 (`anthropic-skills:` prefix)** 로 한정하고, 제3자 커뮤니티 스킬은 별도 ADR-006 에서 심사하도록 명시했다. 본 ADR 은 이 원칙을 준수하며, Stage 1 평가표의 PASS 범위도 공식 스킬에만 적용한다.

---

## 2. 결정 (Decision)

### 2.1 심사 타임라인

ADR-001 §2.2 의 4 단계를 본 ADR 에 구체 적용한다. 기준일은 본 ADR 머지일이다 (D = ADR-002 Merge date).

| Stage | 기간 | 시작 | 종료 | 활성 Pilot |
|---|---|---|---|---|
| 1 Proposal | 본 PR 심사 | 2026-04-22 | 본 ADR 머지 | — |
| 2 Sandbox | 14일 | D | D+14 | 팀메이트 4 (worktree `aqts-team4-skills-pilot`) |
| 3 Limited Rollout | 30일 | D+14 | D+44 | 팀메이트 4 + 옵트인 2명 이상 |
| 4 Full Adoption | +14일 관찰 | D+44 | D+58 | 전 팀 옵트인 |

### 2.2 Stage 2 Stop 조건 — ADR-001 원본 + 도구별 확장

ADR-001 §2.2 Stage 2 의 기본 Stop 조건 3종을 승계하고, 본 도구에 특화된 4번째 Stop 조건을 추가한다.

1. **(ADR-001 원본)** 회귀 1건이라도 발생하여 하루 이상 CI 가 빨갛게 유지됨.
2. **(ADR-001 원본)** 도구가 `.env` / 인증 토큰 / 개인정보에 접근하는 새 요구를 발견.
3. **(ADR-001 원본)** 업스트림이 조용히 repo 를 archive / delete / 라이선스 변경.
4. **(ADR-002 확장)** **Pilot 팀메이트 4 의 14일 내 실제 스킬 참조 호출이 5회 미만이면, Stage 2 를 중단하지 않고 팀메이트 1 로 Pilot 교체 후 재시작.** 근거: 사용 빈도 부족은 "도구 결함" 이 아니라 "Pilot 선정 부적합" 시그널이므로 Stop 이 아닌 Pilot 교체 경로로 처리한다. 교체 후에도 14일 내 5회 미만이면 Stage 2 Stop 으로 전환하고, 도구가 AQTS 운영 레인에 구조적으로 맞지 않음을 판정한다.

§2.2 Stop 조건 판정 로그는 본 ADR 의 "Sandbox 관찰 기록" 섹션 (§7 부록) 에 주간 단위로 남긴다.

### 2.3 ADR-001 §2.3 심사 평가표

| 기준 | 세부 항목 | 판정 | 근거 |
|---|---|---|---|
| §5-1 라이선스 | SPDX 식별자 / 상용 사용 가능 여부 | **Pending** | `anthropic/skills` LICENSE 확인 필요 — Stage 1 머지 전 리드가 직접 확인. 공식 저장소는 Apache-2.0 또는 MIT 추정 |
| §5-2 공급망 신뢰성 | `pip-audit` 결과 (Python 의존성) | **N/A (현재)** | 샌드박스 availability 상태는 Anthropic 배포 채널이 제공. AQTS 저장소에 설치되는 Python 패키지는 없음 (Stage 3 에서 만약 로컬 설치로 전환 시 재평가) |
| §5-2 공급망 신뢰성 | `grype` 결과 (high+) | **N/A (현재)** | 배포 채널 신뢰 기준. Stage 3 전환 시 재평가 |
| §5-2 공급망 신뢰성 | 패키지 서명 여부 | **N/A** | SKILL.md 는 표준 문서 형식이며 cosign 서명 대상 아님. Anthropic 공식 배포 엔드포인트 신뢰 기반 |
| §5-3 Wiring | 도구가 호출되는 코드 경로 | **명시** | Claude Code 세션 내 `Skill` tool 호출 — AQTS 저장소 코드에 의존성 주입 없음 (외부 의존) |
| §5-3 Wiring | 통합 테스트 (실제 실행 경로 봉인) | **MISSING → Stage 2 과제** | 스킬 호출이 실제로 수행되었는지 확인하는 로그 수집 체계를 Stage 2 에서 수립. `docs/architecture/sandbox/adr-002/skill-usage-log.md` 신설 예정 |
| §5-4 문서화 | 도입 이유 / 대안 / 롤백 경로 | **있음** | 본 ADR §1, §4, §3.3 |
| ADR-001 추가 | 실패 모드 3개 이상 + 감지 방법 | **있음** | 본 ADR §2.4 |
| ADR-001 추가 | Pilot 담당 팀메이트 1명 지정 | **지정됨** | 팀메이트 4 (근거 §1.2) |
| ADR-001 추가 | Stage 2~4 타임라인 | **명기** | §2.1 표 |

**판정**: 라이선스 PASS 확인 후 Stage 1 승격 (리드 검증 대기). 공급망 N/A 항목은 Stage 3 전환 시 재평가로 미뤄짐이 명시되어 Pending 이 아닌 판정 완결.

### 2.4 실패 모드 최소 3개 + 감지 방법

1. **업스트림 저장소 archive / delete / 라이선스 변경**
   - 감지 방법: 월 1회 `https://github.com/anthropics/skills` 의 README / LICENSE / archive 배지 상태를 팀메이트 4 가 Stage 2 주간 점검에 포함. Anthropic 공식 블로그 announcement 병행 구독.
   - 롤백 경로: 해당 스킬 호출을 제거하고 기존 `scripts/gen_status.py` / 수기 `.md` 경로로 복귀. 스킬 호출 자체가 AQTS 저장소 코드에 의존하지 않으므로 code diff 없음.

2. **SKILL.md 표준 breaking change (Claude Code runtime 비호환)**
   - 감지 방법: 스킬 호출 시 YAML frontmatter validation 에러 또는 runtime schema mismatch 에러가 세션 로그에 기록됨. 팀메이트 4 의 worktree 에서 주간 스킬 사용 로그를 확인할 때 에러 카운트 0 이 아니면 즉시 보고.
   - 롤백 경로: 실패 모드 1 과 동일. 추가로 Anthropic release notes 의 Breaking Change 섹션 확인 후 재심사 트리거 작동.

3. **커뮤니티 스킬의 공식 prefix squat**
   - 감지 방법: `anthropic-skills:` prefix 와 타 prefix 를 availability 목록에서 명시적으로 구분. Stage 2 주간 점검에서 "사용한 스킬 목록 + prefix 로그" 를 확인. 공식 prefix 가 아닌 스킬이 호출되면 즉시 사용 중단.
   - 롤백 경로: 해당 스킬 호출 경로 제거. 본 ADR 의 scope 위반이므로 재발 방지 기록을 Stage 2 관찰 로그에 남기고, 필요 시 ADR-006 (커뮤니티 스킬 심사) 선제 착수.

4. **개별 스킬 내부 의존성 취약점 (예: `xlsx` 스킬의 Python 라이브러리)**
   - 감지 방법: Stage 3 에서 스킬이 로컬 Python 환경에 의존성을 설치한다면, `backend/requirements.txt` 에 해당 패키지가 고정되어 기존 `pip-audit` 경로로 탐지. 샌드박스 availability 상태에서는 스킬이 runtime 에 자체 sandbox 를 제공하므로 AQTS runtime 에 영향 없음 (Stage 2 범위 밖).
   - 롤백 경로: 취약 스킬만 호출 중단. 다른 스킬은 유지.

5. **Pilot 사용 빈도 미달 (14일 내 5회 미만)** — Stage 2 Stop 조건 4 와 연결
   - 감지 방법: 팀메이트 4 worktree 의 주간 점검 로그에서 참조 횟수 누적. 7일 차 기준 2회 미만이면 조기 경보 (Pilot 교체 사전 준비).
   - 롤백 경로: 팀메이트 1 로 Pilot 교체. 본 ADR 의 Pilot 지정 섹션과 타임라인을 개정하고 Stage 2 재시작.

---

## 3. 결과 (Consequences)

### 3.1 긍정적 결과

- **문서 산출 자동화 기반 확보**: 팀메이트 4 의 주간 리포트 생성 비용이 감소하면, Doc-Sync 강화 (development-policies.md §9 "발견 시점이 수정 시점") 에 더 많은 시간을 배분 가능.
- **Agent Teams 간 산출물 포맷 표준화**: `xlsx` / `docx` / `pdf` 스킬이 공통 포맷을 제공하므로, 팀 간 인수인계 문서의 구조 drift 가 감소한다.
- **공식 표준 조기 채택**: 2025-12 open standard 출시 후 4개월 시점 채택은 "Anthropic 공식 권고 경로" 를 따르는 방향이다. 자체 유사 구현 개발 대비 유지보수 비용 절감.
- **ADR-001 프레임워크 검증**: 본 ADR 의 Stage 1~4 실 운영이 ADR-001 의 실효성을 증명하는 첫 케이스가 된다.

### 3.2 부정적 결과 / 수용 가능한 비용

- **Pilot 지정 팀메이트 4 에 14일 관찰 부담**: 주간 스킬 사용 로그 수집 + 에러 카운트 점검. 주당 약 30분 추정. 수용 근거: Doc-Sync 업무에 통합되는 형태라 별도 공수가 아니다.
- **Full Adoption 까지 최소 58일 소요**: ADR-001 의 기본 타임라인 승계. 긴급한 리포트 산출은 기존 `scripts/gen_status.py` 경로를 계속 사용 가능하므로 업무 차단 없음.
- **커뮤니티 스킬 배제로 인한 기회비용**: `skill-creator` 로 자체 스킬을 만들어 공유해도 커뮤니티 스킬 생태계는 본 ADR scope 밖이므로 활용 불가. 수용 근거: ADR-006 에서 재심사 경로가 열려 있다.

### 3.3 롤백 경로

본 ADR 의 스킬 호출은 **AQTS 저장소 코드에 의존성 주입을 만들지 않는다** (Stage 2 한정). 따라서 롤백 비용이 극히 낮다.

1. 해당 스킬 호출을 제거 — 기존 `scripts/gen_status.py` · 수기 `.md` · `python-pptx`/`openpyxl` 직접 사용 경로로 복귀.
2. 본 ADR Status → Superseded 또는 Rejected 로 전환하고 회귀 사유를 §7 부록에 기록.
3. CLAUDE.md §9 와 governance.md §5 에는 본 ADR 을 참조 링크로만 추가하므로 링크 제거 외 SSOT 변경 없음.

Stage 3 이후 로컬 Python 의존성이 추가되는 경우에는 `backend/requirements.txt` 에서 해당 패키지 pin 제거 + `alembic` / `backend/core/` 에 해당 호출 경로 제거의 2단계 롤백이 필요하다. 이 경우의 상세 롤백 체크리스트는 Stage 2 종료 시점에 본 ADR §7 부록에 추가한다.

---

## 4. 대안 (Alternatives Considered)

### 4.1 대안 A — 심사 없이 즉시 Full Adoption

- **내용**: 샌드박스 availability 상태에서 이미 동작 중이므로 4 단계 생략하고 바로 표준 도구로 등록.
- **장점**: 즉시 사용 가능.
- **단점**: ADR-001 이 확립한 프레임워크의 첫 적용 사례를 건너뛰면 ADR-001 자체가 dead letter 가 된다. 재현성 · 회귀 봉쇄 목적 달성 불가.
- **거절 근거**: ADR-001 §2.1 이 Phase 2 진입 선언과 동시에 4 단계 준수를 의무화했다.

### 4.2 대안 B — 커뮤니티 스킬까지 포함한 포괄 채택

- **내용**: `anthropic-skills:` prefix 외에 커뮤니티 스킬 (search 결과의 "423 plugins, 2,849 skills") 까지 본 ADR 에서 일괄 심사.
- **장점**: 심사 효율. 한번에 전체 생태계 결정.
- **단점**: ADR-001 §6.1 과 모순. 책임 범위 분리 원칙 (§2.4 "동시 착수 금지") 위반.
- **거절 근거**: ADR 은 하나의 결정에 하나의 문서. 커뮤니티 스킬은 ADR-006 에서 다룬다.

### 4.3 대안 C — 본 ADR 이 채택한 4 단계 심사 + Pilot 교체 경로

- **내용**: ADR-001 §2.2 의 Stop 조건 3종 + 본 ADR 의 4번째 Stop (Pilot 사용 빈도 미달 시 교체).
- **장점**: 사용 빈도 부족을 "도구 결함" 이 아닌 "Pilot 선정 미스매치" 로 분리. 판정 모호성 제거.
- **단점**: Pilot 교체 시 Stage 2 가 재시작되어 최대 14일 추가 지연 가능.
- **채택 근거**: 사용 빈도 미달을 Stop 으로 처리하면 도구 자체의 유효성 평가가 왜곡된다. 교체 경로는 판정 정확도와 지연의 합리적 trade-off.

### 4.4 대안 D — Pilot 을 팀메이트 1 로 지정

- **내용**: 팀메이트 1 (Strategy/Backtest) 이 OOS 결과 리포트 자동화를 Pilot 으로 수행.
- **장점**: `xlsx` 스킬과 CSV 산출물의 매핑이 직접적.
- **단점**: §1.2 표에서 기록한 대로, 하이퍼옵트 run 주기에 따라 14일 내 5회 참조가 불확실. 회귀 발생 시 `backend/core/oos/` 결과 해석 경로에 영향.
- **거절 근거**: Pilot 은 회귀 격리성과 산출 빈도를 동시에 만족해야 한다. 팀메이트 1 은 Pilot 교체 후보 (Stop 조건 4 발동 시) 로 남겨둔다.

---

## 5. 검증 (Validation)

### 5.1 본 ADR 문서 자체의 검증 절차

- [x] `docs/architecture/adr-002-anthropic-skills-adoption.md` 신설 (본 파일).
- [x] `CLAUDE.md §9` 에 "ADR-002 작성 완료 (Stage 1 통과 대기)" 항목을 후속 PR 에서 `[x]` 또는 진행 중 [ ] 로 전환 예정.
- [x] 최소 게이트 통과: `ruff check`, `black --check`, `check_bool_literals`, `check_doc_sync`.
- [x] 문서-only 커밋이므로 전체 pytest 생략 (CLAUDE.md §3.1 예외 적용). `.py` / `.toml` / `.sh` / `Dockerfile*` / `.github/workflows/*.yml` 변경 zero 임을 `git diff --stat` 로 확인.

### 5.2 Stage 2 진입 전 리드 확인 항목

1. `https://github.com/anthropics/skills` LICENSE 파일 직접 확인 (Apache-2.0 또는 MIT 기대, 그 외 라이선스면 Stage 1 승격 중단).
2. `anthropic-skills:` prefix 스킬 목록을 본 ADR §7.1 에 스냅샷 기록 (Stage 2 기간 동안 prefix squat 발견 위한 baseline).
3. 팀메이트 4 의 `aqts-team4-skills-pilot` worktree 생성 및 주간 점검 일정 등록.

### 5.3 후속 검증 (Stage 2 → 3 → 4 경계)

- Stage 2 종료일 (D+14) 에 본 ADR §7.2 "Sandbox 관찰 기록" 을 업데이트하고 판정 (Pass / Stop / Pilot 교체).
- Stage 3 진입 시 §2.3 평가표의 "공급망 N/A → 재평가" 항목을 Stage 3 기준으로 재기재.
- Stage 4 진입 시 governance.md §5 의 "Phase 2 이후 외부 참고" 문단을 "anthropic-skills 공식 채택" 으로 구체화하는 별도 PR 발행.

---

## 6. 재심사 트리거

다음 중 하나라도 발생하면 본 ADR Status 를 Revised / Superseded / Rejected 로 재평가한다.

- `anthropic/skills` 업스트림 메이저 버전 업 (예: v2 표준 전환).
- SKILL.md open standard 의 Breaking Change 공지.
- 공식 스킬 또는 간접 의존성에 CVE High 1건 이상 노출.
- Claude Code runtime 이 Skill tool 을 deprecate.
- Pilot 교체를 2회 이상 수행해도 Stage 2 참조 빈도 5회 미달.

---

## 7. 부록

### 7.1 Stage 2 Baseline — `anthropic-skills:` prefix 스킬 목록 (2026-04-22 스냅샷)

본 세션에서 availability 상태로 노출된 공식 스킬 목록은 다음과 같다. Stage 2 기간 동안 신규 squat 이 발생하지 않는지 주간 점검한다.

| Skill | 주요 용도 |
|---|---|
| `anthropic-skills:xlsx` | 스프레드시트 생성/편집 |
| `anthropic-skills:docx` | Word 문서 생성/편집 |
| `anthropic-skills:pptx` | 프레젠테이션 생성 |
| `anthropic-skills:pdf` | PDF 처리 / 폼 / 추출 |
| `anthropic-skills:schedule` | 스케줄 작업 등록 |
| `anthropic-skills:consolidate-memory` | 메모리 파일 정리 |
| `anthropic-skills:setup-cowork` | Cowork 셋업 가이드 |
| `anthropic-skills:skill-creator` | 신규 skill 작성 |

이 외의 prefix (`cowork-plugin-management:*` 등) 는 본 ADR scope 밖이므로 별도 심사 (예: ADR-006 커뮤니티 스킬 심사) 대상.

### 7.2 Sandbox 관찰 기록 (Stage 2 기간 동안 주간 업데이트)

| 주차 | 기간 | 참조 횟수 누적 | 관찰된 이슈 | 판정 |
|---|---|---|---|---|
| W1 | D ~ D+7 | _TBD_ | _TBD_ | _TBD_ |
| W2 | D+7 ~ D+14 | _TBD_ | _TBD_ | Stage 3 진입 / Stop / Pilot 교체 |

### 7.3 Rollout 체크리스트 (Stage 3 기간 동안 업데이트)

- [ ] 팀메이트 4: 2026-_MM-DD_ opt-in (Pilot 연속)
- [ ] 팀메이트 1: _MM-DD_ opt-in
- [ ] 팀메이트 2: _MM-DD_ opt-in
- [ ] 팀메이트 3: _MM-DD_ opt-in

Stage 3 Exit Criteria 의 "최소 2명 추가 옵트인" 을 충족하려면 팀메이트 1/2/3 중 2명 이상 체크 필요.

---

## 8. 변경 이력

| 날짜 | 변경 내용 | 작성자 |
|---|---|---|
| 2026-04-22 | 최초 작성 (Stage 1 Proposal) | 리드 |
