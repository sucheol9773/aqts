# ADR-002 — Anthropic Agent Skills (`anthropics/skills`) 단계적 채택

- **상태 (Status)**: Stage 2 Sandbox (Active) — 2026-04-22 Stage 1 통과, 동일자 Stage 2 진입 kickoff
- **작성일**: 2026-04-22 (Stage 1 초안) / 2026-04-22 (Stage 2 kickoff — 본 개정)
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

본 Stage 2 kickoff 개정에서 2026-04-22 자 실측으로 확인된 **Agent Skills 표준의 삼중 SSOT 구조** 를 명시한다. (Stage 1 초안에서는 단일 저장소 `anthropic/skills` 로만 기술되었다.)

| 축 | 위치 | 역할 | 라이선스 |
|---|---|---|---|
| 구현체 SSOT | `github.com/anthropics/skills` | 공식 예제 스킬 17 디렉토리 (repo root `/skills/*`) | Apache-2.0 (commit `b9e19e6f44773509fbdd7001d77ff41a49a486c1`) |
| 스펙 SSOT | `github.com/agentskills/agentskills` + `agentskills.io` | `SKILL.md` 포맷 명세, documentation, Reference SDK | Code: Apache-2.0 / Documentation: CC-BY-4.0 |
| Reference SDK | `agentskills/agentskills/skills-ref/` | `skills-ref validate` CLI — frontmatter 검증 + prompt XML 생성 | Apache-2.0 |

> `agentskills/agentskills` README 상 "About: Agent Skills is an open format maintained by Anthropic and open to contributions from the community." — 표준 거버넌스는 여전히 Anthropic 이 유지한다. `agentskills.io` README 의 "Example Skills" 링크도 `github.com/anthropics/skills` 를 명시적으로 가리킨다 (2026-04-22 확인).

Agent 제품 측의 채택 현황은 `agentskills.io/clients` 페이지 기준 **37 개 이상** 의 벤더가 `SKILL.md` 포맷을 지원한다 (Claude Code, OpenAI Codex, Google Gemini CLI, Microsoft GitHub Copilot / VS Code, Cursor, JetBrains Junie, Snowflake Cortex Code, Databricks Genie Code, Kiro, Goose, OpenHands, Amp 등 — 2026-04-22 스냅샷).

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

ADR-001 §2.2 의 4 단계를 본 ADR 에 구체 적용한다. 기준일은 본 ADR Stage 2 kickoff PR 머지일이다 (D = 2026-04-22). ADR-002 Stage 1 초안은 동일자 오전에 머지됐으며, Stage 2 진입을 블로킹하던 LICENSE 확인 (§5.2 항목 1) 은 본 kickoff PR §7.1 에서 완료된다.

| Stage | 기간 | 시작 | 종료 | 활성 Pilot |
|---|---|---|---|---|
| 1 Proposal | 초안 심사 | 2026-04-22 | 2026-04-22 (ADR-002 Stage 1 PR #24 머지) | — |
| 2 Sandbox | 14일 | 2026-04-22 (D) | 2026-05-06 (D+14) | 팀메이트 4 (worktree `aqts-team4-skills-pilot`) |
| 3 Limited Rollout | 30일 | 2026-05-06 (D+14) | 2026-06-05 (D+44) | 팀메이트 4 + 옵트인 2명 이상 |
| 4 Full Adoption | +14일 관찰 | 2026-06-05 (D+44) | 2026-06-19 (D+58) | 전 팀 옵트인 |

Stage 전환 게이트는 §2.3 평가표 (진입 판정) 와 §5.3 (Stage 2→3→4 경계 검증) 에서 Exit Criteria 를 정의한다.

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
| §5-1 라이선스 | SPDX 식별자 / 상용 사용 가능 여부 | **PASS** | 구현체 SSOT `github.com/anthropics/skills` LICENSE = **Apache-2.0** (commit `b9e19e6f44773509fbdd7001d77ff41a49a486c1`). 스펙 SSOT `github.com/agentskills/agentskills` LICENSE = **Apache-2.0 (code) + CC-BY-4.0 (docs)**. 두 라이선스 모두 AQTS 의 상용/비상용 파생 사용 가능. 리드 확인 2026-04-22 |
| §5-2 공급망 신뢰성 | `pip-audit` 결과 (Python 의존성) | **N/A (현재)** | 샌드박스 availability 상태는 Anthropic 배포 채널이 제공. AQTS 저장소에 설치되는 Python 패키지는 없음 (Stage 3 에서 만약 로컬 설치로 전환 시 재평가) |
| §5-2 공급망 신뢰성 | `grype` 결과 (high+) | **N/A (현재)** | 배포 채널 신뢰 기준. Stage 3 전환 시 재평가 |
| §5-2 공급망 신뢰성 | 패키지 서명 여부 | **N/A** | SKILL.md 는 표준 문서 형식이며 cosign 서명 대상 아님. Anthropic 공식 배포 엔드포인트 신뢰 기반 |
| §5-3 Wiring | 도구가 호출되는 코드 경로 | **명시** | Claude Code 세션 내 `Skill` tool 호출 — AQTS 저장소 코드에 의존성 주입 없음 (외부 의존) |
| §5-3 Wiring | 통합 테스트 (실제 실행 경로 봉인) | **MISSING → Stage 2 과제** | 스킬 호출이 실제로 수행되었는지 확인하는 로그 수집 체계를 Stage 2 에서 수립. `docs/architecture/sandbox/adr-002/skill-usage-log.md` 신설 예정 |
| §5-4 문서화 | 도입 이유 / 대안 / 롤백 경로 | **있음** | 본 ADR §1, §4, §3.3 |
| ADR-001 추가 | 실패 모드 3개 이상 + 감지 방법 | **있음 (F1~F7 중 5가지 필수 + 2가지 Stage 2 kickoff 확장)** | 본 ADR §2.4 |
| ADR-001 추가 | Pilot 담당 팀메이트 1명 지정 | **지정됨** | 팀메이트 4 (근거 §1.2) |
| ADR-001 추가 | Stage 2~4 타임라인 | **명기 (구체 날짜 포함)** | §2.1 표 (2026-04-22 ~ 2026-06-19) |
| Stage 2 kickoff 추가 | 채택 증거 (Adoption evidence) | **PASS** | `agentskills.io/clients` 기준 **37+ 벤더** 가 `SKILL.md` 포맷 지원 — Anthropic / OpenAI / Google / Microsoft / Cursor / JetBrains / Snowflake / Databricks / Kiro / Goose / OpenHands / Amp 등 (2026-04-22 스냅샷). 단일 벤더 락인 위험 낮음 |
| Stage 2 kickoff 추가 | Stage 3 Exit Criteria 정량 임계값 | **명기** | 본 ADR §5.3.1 |

**판정**: **PASS — Stage 2 Sandbox 진입 (2026-04-22)**. LICENSE 와 Adoption 근거 확인 완료, 공급망 N/A 항목은 Stage 3 전환 시 재평가로 명시, Wiring 통합 테스트는 Stage 2 의 공식 과제로 수립. 판정 완결성 확보.

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

6. **AQTS 고유 규칙이 스킬 적용 시 재현되지 않음 (Gotchas 누락)** — Stage 2 kickoff 확장
   - 배경: `agentskills.io/skill-creation/best-practices` 에서 "Gotchas 섹션이 스킬의 가장 높은 가치 콘텐츠" 로 명시된다. 일반적인 문서 작성 스킬 (`xlsx`/`docx`/`pptx`) 은 AQTS 의 고유 규칙 (하드코딩 금지, 테스트 기대값 수정 절대 금지, 한글 기술 서술, black 포맷, RBAC wiring rule 등) 을 알지 못한다.
   - 감지 방법: Pilot Stage 2 주간 점검 시 스킬이 산출한 문서가 CLAUDE.md / development-policies.md 의 절대 규칙을 위반했는지 diff 검증. 특히 영어 템플릿이 한글 정책을 덮어쓰거나, 테스트 코드 스크린샷에서 기대값이 수정된 예시를 포함하는지 확인.
   - 롤백 경로: AQTS 전용 래퍼 스킬 (예: `aqts-team4-report` 자체 스킬) 을 `.claude/skills/` 에 두어 공식 스킬 앞단에서 Gotchas 를 주입. §7.5 Pilot 온보딩 가이드의 "AQTS Gotchas 체크리스트" 참조.

7. **SKILL.md 500 줄 초과 → progressive disclosure 원칙 위반** — Stage 2 kickoff 확장
   - 배경: `agentskills.io/specification` 은 "Instructions < 5000 tokens, 500 lines 권장" 명시. 초과 시 스킬 활성화마다 context window 의 상당 비중을 점유하여 다른 활성 스킬/대화 이력을 압박한다.
   - 감지 방법: `anthropics/skills/*/SKILL.md` 각 파일의 라인 수 및 토큰 수 카운트를 Stage 2 주간 점검 항목에 포함. 500 줄 초과 또는 5000 토큰 초과 스킬은 우선순위를 낮춰 호출 대상에서 제외.
   - 롤백 경로: 해당 스킬 대신 경량 대안 사용. 향후 AQTS 자체 스킬 작성 시 Best practices 의 "scripts/ references/ assets/ 분리" 패턴을 강제하여 SKILL.md 본체는 최소화.

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

- [x] `docs/architecture/adr-002-anthropic-skills-adoption.md` 신설 (Stage 1 초안, PR #24 에서 머지).
- [x] `CLAUDE.md §9` 에 "ADR-002 작성 완료 (Stage 1 통과 대기)" 항목 등록 (PR #24).
- [x] Stage 2 kickoff 개정: §1 삼중 SSOT / §2.1 구체 날짜 / §2.3 LICENSE PASS + Adoption PASS / §2.4 F6~F7 / §7.1 베이스라인 재작성 / §7.5 Pilot 온보딩.
- [x] 최소 게이트 통과: `ruff check`, `black --check`, `check_bool_literals`, `check_doc_sync`.
- [x] 문서-only 커밋이므로 전체 pytest 생략 (CLAUDE.md §3.1 예외 적용). `.py` / `.toml` / `.sh` / `Dockerfile*` / `.github/workflows/*.yml` 변경 zero 임을 `git diff --stat` 로 확인.

### 5.2 Stage 2 진입 전 리드 확인 항목

1. [x] `https://github.com/anthropics/skills` LICENSE 파일 직접 확인 — **Apache-2.0 확인 완료** (commit `b9e19e6f44773509fbdd7001d77ff41a49a486c1`, 2026-04-22). `agentskills/agentskills` README 는 "Code: Apache-2.0, Documentation: CC-BY-4.0" 이원 라이선스를 명시.
2. [x] `anthropic-skills:` prefix 스킬 목록을 본 ADR §7.1 에 스냅샷 기록 — **baseline 재작성 완료** (Stage 1 초안의 샌드박스 번들 8종 → Stage 2 kickoff 의 삼중 SSOT + `anthropics/skills` 17 디렉토리 + 샌드박스 번들 3종 분리 기록).
3. [ ] 팀메이트 4 의 `aqts-team4-skills-pilot` worktree 생성 및 주간 점검 일정 등록 — **리드 실행 대기**. Stage 2 kickoff PR 머지 직후 `git worktree add ../aqts-team4-skills-pilot main` 로 생성, 주간 점검일은 매주 수요일 오후로 권장 (D = 2026-04-22 이므로 W1 점검 = 2026-04-29, W2 점검 = 2026-05-06).

### 5.3 후속 검증 (Stage 2 → 3 → 4 경계)

#### 5.3.1 Stage 2 → 3 Exit Criteria (2026-05-06 판정)

`agentskills.io/skill-creation/evaluating-skills` 와 `optimizing-descriptions` 에서 공식화된 평가 프로토콜을 AQTS Stage 3 진입 게이트로 채택한다.

**A. 스킬 출력 품질 (Output quality gate)**
- Pilot 이 Stage 2 기간 동안 최소 5회 이상 호출한 공식 스킬 중 상위 2종에 대해 with_skill / without_skill 비교 eval 수행.
- 워크스페이스 구조: `docs/architecture/sandbox/adr-002/<skill-name>-workspace/iteration-1/<eval-id>/{with_skill,without_skill}/{outputs,timing.json,grading.json}` + `benchmark.json`.
- Exit 임계값: `benchmark.json.delta.pass_rate ≥ 0.2` (with_skill 이 without_skill 대비 assertion pass rate 20%p 이상 향상) AND `tokens_delta ≤ 2.0x` (토큰 증분이 2배 이내).

**B. 트리거 정확도 (Trigger accuracy gate)**
- 후보 스킬별 **20-query eval set** (should_trigger = true 8~10개 + should_trigger = false 8~10개, near-miss negative 필수 포함).
- **각 쿼리 3회 반복 실행** 후 trigger rate (호출 비율) 측정, threshold 0.5.
- Train/validation 60/40 split 로 overfitting 방지, **validation pass rate ≥ 0.7** (should_trigger 쿼리가 threshold 이상, should_not 쿼리가 threshold 미만인 비율).
- SKILL.md `description` 필드 1024 character 하드 리밋 준수 확인.

**C. 정성적 판정 (Qualitative gate)**
- Pilot 이 작성한 주간 점검 로그 (§7.2) 에 F1~F7 실패 모드 발현 여부 기록, F1~F5 중 하나라도 발현 시 Stop 또는 Pilot 교체로 분기.
- F6 (Gotchas 누락) / F7 (SKILL.md 500 줄 초과) 발현 시 즉시 Stop 이 아니라 "AQTS 전용 래퍼 스킬 필요" 경고로 Stage 3 확장 과제에 편입.

#### 5.3.2 Stage 3 → 4 Exit Criteria (2026-06-05 판정)

- Stage 3 의 옵트인 팀메이트 2명 이상이 각자 최소 3회 호출을 달성.
- §2.3 평가표의 "공급망 N/A → 재평가" 항목을 Stage 3 기준으로 재기재 (만약 로컬 Python 의존성이 추가됐다면 `pip-audit` / `grype` 결과 수집).
- Stage 2 Exit Criteria A/B 재측정에서 저하 없음 (`delta.pass_rate` 유지 또는 향상).

#### 5.3.3 Stage 4 전환 (2026-06-19 판정)

- governance.md §5 의 "Phase 2 이후 외부 참고" 문단을 "anthropic-skills 공식 채택" 으로 구체화하는 별도 PR 발행.
- CLAUDE.md §9 의 ADR-002 TODO 를 [x] 로 전환.

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

### 7.1 Stage 2 Baseline — 공식 스킬 전수 목록 (2026-04-22 스냅샷)

Stage 1 초안은 본 Cowork 샌드박스에서 availability 상태로 노출된 8종만 기록했으나, 실제 `github.com/anthropics/skills` 레포 (구현체 SSOT) 의 `/skills/` 하위에는 **17 개 공식 스킬 디렉토리** 가 존재한다 (2026-04-22 실측, `totalCount: 17` 확인). Stage 2 kickoff 개정에서 이를 정정한다.

#### 7.1.1 구현체 SSOT — `github.com/anthropics/skills/skills/*` (17 디렉토리)

| 디렉토리 | 주요 용도 | 샌드박스 번들 여부 |
|---|---|---|
| `algorithmic-art` | 알고리듬 기반 생성 아트 | 미번들 |
| `brand-guidelines` | 브랜드 가이드라인 템플릿 | 미번들 |
| `canvas-design` | 캔버스 기반 디자인 보조 | 미번들 |
| `claude-api` | Claude API 호출 템플릿 | 미번들 |
| `doc-coauthoring` | 다자 협업 문서 작성 | 미번들 |
| `docx` | Word 문서 생성/편집 | **번들** (`anthropic-skills:docx`) |
| `frontend-design` | 프론트엔드 UI 설계 보조 | 미번들 |
| `internal-comms` | 내부 커뮤니케이션 템플릿 | 미번들 |
| `mcp-builder` | MCP 서버 구축 가이드 | 미번들 |
| `pdf` | PDF 처리 / 폼 / 추출 | **번들** (`anthropic-skills:pdf`) |
| `pptx` | 프레젠테이션 생성 | **번들** (`anthropic-skills:pptx`) |
| `skill-creator` | 신규 skill 작성 | **번들** (`anthropic-skills:skill-creator`) |
| `slack-gif-creator` | Slack GIF 생성 | 미번들 |
| `theme-factory` | 테마 일괄 생성 | 미번들 |
| `web-artifacts-builder` | 웹 artifact 빌드 | 미번들 |
| `webapp-testing` | 웹앱 테스트 시나리오 | 미번들 |
| `xlsx` | 스프레드시트 생성/편집 | **번들** (`anthropic-skills:xlsx`) |

#### 7.1.2 샌드박스 번들 (Cowork 전용, `anthropics/skills` 레포 외)

다음 3 종은 `anthropics/skills` 레포에는 포함되지 않고 Cowork 플러그인 번들 (`anthropic-skills:` prefix) 에만 제공된다. `anthropics/skills` 관찰 대상에서는 제외하고, Cowork 플러그인 릴리스 노트를 별도 모니터링 대상으로 취급한다.

| Skill | 주요 용도 | 관찰 채널 |
|---|---|---|
| `anthropic-skills:schedule` | 스케줄 작업 등록 | Cowork 플러그인 릴리스 노트 |
| `anthropic-skills:consolidate-memory` | 메모리 파일 정리 | Cowork 플러그인 릴리스 노트 |
| `anthropic-skills:setup-cowork` | Cowork 셋업 가이드 | Cowork 플러그인 릴리스 노트 |

#### 7.1.3 스펙 SSOT — `github.com/agentskills/agentskills` + `agentskills.io`

본 ADR 이 참조하는 표준 스펙 및 Pilot 온보딩 자료의 출처는 다음과 같다 (2026-04-22 수집).

| 문서 | 경로 | 용도 |
|---|---|---|
| Overview | `agentskills.io/home` | 표준 개요, 채택 배경 |
| Specification | `agentskills.io/specification` | `SKILL.md` 프론트매터 필드 명세, 디렉토리 구조, progressive disclosure |
| Adding skills support (client) | `agentskills.io/client-implementation/adding-skills-support` | Claude Code 등 agent 가 `.claude/skills/` / `.agents/skills/` 를 스캔/로드하는 프로토콜 |
| Quickstart | `agentskills.io/skill-creation/quickstart` | 1 파일 20줄 최소 유효 스킬 튜토리얼 (roll-dice) |
| Best practices | `agentskills.io/skill-creation/best-practices` | Gotchas 섹션, 진보적 공개 구조, calibrating control |
| Optimizing descriptions | `agentskills.io/skill-creation/optimizing-descriptions` | 20-query eval, train/val 60/40 split, trigger rate 임계값 |
| Evaluating skills | `agentskills.io/skill-creation/evaluating-skills` | `evals/evals.json` + `iteration-N/` 워크스페이스, `benchmark.json` 집계 |
| Using scripts | `agentskills.io/skill-creation/using-scripts` | PEP 723 (Python `uv run`), non-interactive shell 설계, structured output |
| Client showcase | `agentskills.io/clients` | 37+ 벤더 채택 증거 (§2.3 Adoption row) |

#### 7.1.4 Reference SDK — `skills-ref validate`

`agentskills/agentskills/skills-ref/` 에 포함된 CLI. Stage 3 에서 AQTS 가 자체 스킬을 작성할 경우 커밋 전 검증 도구로 채택 검토. Stage 2 기간에는 사용 계획 없음.

#### 7.1.5 Scope 제외

이 외의 prefix (`cowork-plugin-management:*`, 커뮤니티 저장소의 제3자 스킬 등) 는 본 ADR scope 밖이므로 별도 심사 (예: ADR-006 커뮤니티 스킬 심사) 대상. Stage 2 주간 점검에서 `anthropic-skills:` 외 prefix 가 호출되면 즉시 사용 중단 (§2.4 F3).

### 7.2 Sandbox 관찰 기록 (Stage 2 기간 동안 주간 업데이트)

| 주차 | 기간 | 참조 횟수 누적 | 관찰된 이슈 | 판정 |
|---|---|---|---|---|
| W1 | 2026-04-22 ~ 2026-04-29 | _TBD_ | _TBD_ | _TBD_ (중간 점검 — 2회 미만이면 조기 경보) |
| W2 | 2026-04-29 ~ 2026-05-06 | _TBD_ | _TBD_ | Stage 3 진입 / Stop / Pilot 교체 |

### 7.3 Rollout 체크리스트 (Stage 3 기간 동안 업데이트)

- [ ] 팀메이트 4: 2026-05-06 opt-in (Pilot 연속)
- [ ] 팀메이트 1: _MM-DD_ opt-in (권장 — `xlsx` 스킬이 OOS 결과 CSV 와 직접 매핑)
- [ ] 팀메이트 2: _MM-DD_ opt-in
- [ ] 팀메이트 3: _MM-DD_ opt-in

Stage 3 Exit Criteria 의 "최소 2명 추가 옵트인" 을 충족하려면 팀메이트 1/2/3 중 2명 이상 체크 필요.

### 7.4 AQTS Gotchas 체크리스트 (Stage 2 kickoff 신설, 실패 모드 F6 대응)

공식 스킬이 생성한 산출물 (`docx` / `xlsx` / `pptx` / `pdf`) 이 AQTS 의 절대 규칙을 위반하는지 Pilot 주간 점검 시 확인할 항목이다. 위반 발견 시 AQTS 전용 래퍼 스킬을 `.claude/skills/` 에 두어 공식 스킬 앞단에서 Gotchas 를 주입한다.

| 번호 | 규칙 (SSOT) | 점검 방법 |
|---|---|---|
| G1 | 한글 기술 서술 (CLAUDE.md 프로젝트 지시) | 산출 문서의 영어 템플릿/플레이스홀더가 최종 산출에 남아있지 않은지 확인 |
| G2 | 테스트 기대값 수정 절대 금지 (development-policies.md §1) | 스킬이 생성한 문서에 "예상값을 실제 출력에 맞춰 조정" 같은 안티패턴 예시 없는지 |
| G3 | 하드코딩 금지 (development-policies.md §4) | 환경변수/상수 예시가 `.env.example` 의 키 이름만 인용하는지 (실값 금지) |
| G4 | 절대 규칙 (CLAUDE.md §2) 1~7 위반 없음 | 산출 문서의 명령 예시가 `ruff check` / `black --check` / `pytest` 세 게이트를 우회하지 않는지 |
| G5 | `grep` 금지 → Grep/Glob tool 사용 (Agent Teams 지시) | 산출 문서가 터미널 예시로 `grep` 을 권하지 않는지 (scripts 내 예시는 예외) |
| G6 | RBAC Wiring Rule (authn ≠ authz, development-policies.md §14) | API 관련 문서 산출 시 mutation 라우트 예시에 `require_operator`/`require_admin` 포함되는지 |
| G7 | KST 통일 (CLAUDE.md §5 회귀 사례) | 시간/날짜 예시에 `today_kst_str()` 또는 KST 타임존 명시가 있는지 — UTC 만 쓴 예시는 silent miss 위험 |

### 7.5 Pilot 온보딩 가이드 (Stage 2 kickoff 신설, 팀메이트 4 기준)

#### 7.5.1 경로 결정 — `.claude/skills/` 를 1차 선택

`agentskills.io/client-implementation/adding-skills-support` 는 두 경로를 권장한다:
- **클라이언트 전용**: `<project>/.claude/skills/` (Claude Code 네이티브)
- **크로스 클라이언트**: `<project>/.agents/skills/` (VS Code / Cursor / OpenAI Codex 등과 공유)

AQTS 는 Stage 2 기간 동안 **`.claude/skills/` 만 사용**한다. 근거: (a) AQTS 는 현재 `.claude/rules/` 경로를 이미 사용 중이어서 `.claude/skills/` 가 관례상 자연스럽다, (b) Stage 2 는 Claude Code 단일 클라이언트 검증이 목적이므로 다중 클라이언트 상호운용성을 Stage 2 에 포함시키면 회귀 범위가 불명확해진다. `.agents/skills/` 로의 확장은 Stage 3 Exit Criteria 에서 별도 과제로 평가.

#### 7.5.2 최소 유효 스킬 템플릿 (Quickstart 기반)

`agentskills.io/skill-creation/quickstart` 의 roll-dice 패턴 (1 파일 20줄 미만) 으로 시작한다. Pilot 초기 타겟은 다음 2종:

**(a) `aqts-doc-sync-runner`** — Doc Sync 전수 게이트 자동화
```
.claude/skills/aqts-doc-sync-runner/SKILL.md
---
name: aqts-doc-sync-runner
description: Run AQTS doc-sync gate suite (ruff + black + check_bool_literals + check_doc_sync + check_rbac_coverage + check_loguru_style). Use when the user is about to commit documentation or configuration changes and needs to confirm all static checkers pass.
license: Apache-2.0
compatibility: Requires Python 3.11+ and project root of AQTS repository.
---

## When to use
Before committing any `.md`, `.yml`, `.env*`, or configuration change — especially documentation-only commits that exempt the full pytest run (CLAUDE.md §3.1 예외).

## Steps
1. `cd backend && python -m ruff check . --config pyproject.toml`
2. `cd backend && python -m black --check . --config pyproject.toml`
3. `python scripts/check_bool_literals.py`
4. `python scripts/check_doc_sync.py --verbose`
5. `python scripts/check_rbac_coverage.py`
6. `python scripts/check_loguru_style.py`

## Gotchas (AQTS)
- KST 통일 규칙: 모든 날짜 비교는 `today_kst_str()` 사용 (CLAUDE.md §5).
- `doc_sync` warning 도 error 로 취급 (CLAUDE.md §9 CI/CD 검증 결과 전수 처리 원칙).
```

**(b) `aqts-rbac-route-checker`** — 신규 라우트 PR 에 RBAC wiring 자동 검증
```
.claude/skills/aqts-rbac-route-checker/SKILL.md
---
name: aqts-rbac-route-checker
description: Verify RBAC wiring on new AQTS API routes. Use when adding or modifying @router.post/put/patch/delete/get decorators in backend/api/*.py, to ensure require_operator/require_admin/require_viewer is attached.
license: Apache-2.0
compatibility: Requires Python 3.11+ and project root of AQTS repository.
---

## When to use
Whenever a PR touches `backend/api/*.py` route decorators. 인증(authn) ≠ 인가(authz) 분리 원칙 (development-policies.md §14) 강제.

## Steps
1. `python scripts/check_rbac_coverage.py` — 정적 AST 검사, 0 errors 강제.
2. `cd backend && python -m pytest tests/test_rbac_routes.py -q` — viewer 토큰 403 / admin 토큰 200 통합 테스트.
3. 수동: viewer JWT 를 생성해 신규 mutation 라우트를 직접 호출, 403 확인.

## Gotchas (AQTS)
- `Depends(get_current_user)` 단독 사용은 authz 아님 — `require_*` 별도 필수.
- 라우트 정의와 `docs/security/rbac-policy.md` 매트릭스를 같은 커밋에 갱신.
```

#### 7.5.3 스크립트 작성 표준 — PEP 723 + `uv run`

Pilot 이 `scripts/` 디렉토리를 동반하는 스킬을 작성할 경우, `agentskills.io/skill-creation/using-scripts` 에서 권장하는 PEP 723 인라인 의존성 선언을 기본 포맷으로 채택한다. AQTS Python 3.11 스택 부합.

```python
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "openpyxl>=3.1,<4",
# ]
# ///
import openpyxl
# ...
```

실행: `uv run scripts/example.py`. AQTS CI 환경에 `uv` 를 도입할지 여부는 Stage 3 의 별도 과제로 평가 (`backend/requirements.txt` 가 현재 SSOT 이므로 Stage 2 에서는 로컬 Pilot 환경에만 적용).

#### 7.5.4 에이전트 실행 환경 요구사항

- **Non-interactive shell**: TTY 프롬프트, password dialog, `input()` 금지. 모든 입력은 flag / env / stdin.
- **`--help` 표준화**: 스킬 스크립트는 usage + examples + exit code 의미를 `--help` 출력에 포함.
- **stdout = structured data, stderr = diagnostics** 분리. 출력 크기 예측 불가능 시 `--output <file>` 또는 `--offset` 페이지네이션 지원.
- **Meaningful exit codes**: 0=성공, 1=일반 오류, 2=잘못된 인자, 3=인증 실패 등.

#### 7.5.5 주간 점검 로그 템플릿

`docs/architecture/sandbox/adr-002/skill-usage-log-W<N>.md` 파일로 매주 수요일 점검. 필수 항목:

1. W<N> 기간 (시작일 ~ 종료일)
2. 호출된 스킬 목록 + 각 호출의 timestamp / prompt 요약 / 산출물 경로
3. 누적 참조 횟수 (목표: W1 종료 시 2회 이상, W2 종료 시 5회 이상)
4. F1~F7 실패 모드 발현 여부 (각 항목 체크박스)
5. G1~G7 Gotchas 위반 건수
6. 다음 주 액션 (필요 시 Pilot 교체 / Stop / Stage 3 진입)

---

### 7.6 Open Questions (Stage 3 이후 재평가 대상)

1. **AQTS 가 자체 스킬을 upstream 기여할지**: Stage 3 에서 `aqts-doc-sync-runner` / `aqts-rbac-route-checker` 가 실효성을 증명하면, `agentskills/agentskills` 에 contribution 제안 또는 별도 `agentskills.io/clients` 등록 여부 결정.
2. **VS Code agent mode 를 보조 agent 클라이언트로 시험할지**: Claude Code 단일 의존 위험 완화 목적. `.agents/skills/` 로 경로 이전 시 Stage 3 범위에서 평가.
3. **AQTS 가 로컬 `uv` 도입할지**: Stage 2 는 Pilot 개인 환경에만 적용. CI 도입 여부는 Stage 3 의 Python dev deps 확장 검토와 연계.
4. **Cowork 샌드박스 번들 3종 (`schedule`/`consolidate-memory`/`setup-cowork`) 과 `anthropics/skills` 17종 간 관찰 채널 분리 유지 vs 통합**: Stage 2 주간 점검에서 별도 관찰로 운영해 보고 Stage 3 전환 시 합리성 재평가.

---

## 8. 변경 이력

| 날짜 | 변경 내용 | 작성자 |
|---|---|---|
| 2026-04-22 | 최초 작성 (Stage 1 Proposal) — PR #24 에서 머지 | 리드 |
| 2026-04-22 | Stage 2 kickoff 개정: §1 삼중 SSOT (`anthropics/skills` 구현체 + `agentskills/agentskills` 스펙 + `skills-ref` SDK) 명시, §2.1 타임라인 구체 날짜 (2026-04-22 ~ 2026-06-19), §2.3 LICENSE Apache-2.0 (code) + CC-BY-4.0 (docs) PASS 확정 + Adoption 37+ 벤더 근거 추가, §2.4 실패 모드 F6 (Gotchas 누락) / F7 (SKILL.md 500 줄 초과) 확장, §5.2 항목 1/2 완료 표기, §5.3 Stage 2→3 Exit Criteria 정량화 (`delta.pass_rate ≥ 0.2` / tokens ≤ 2x / 20-query trigger eval), §7.1 베이스라인 재작성 (8종 → 17 디렉토리 + 샌드박스 번들 3종 분리), §7.4 AQTS Gotchas G1~G7 체크리스트 신설, §7.5 Pilot 온보딩 가이드 신설 (경로/템플릿/PEP 723/비대화형 shell/주간 로그), §7.6 Open Questions 추가 | 리드 |
