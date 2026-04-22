# ADR-001 — Phase 2 진입 gate 및 외부 참고 도구 심사 프레임워크

- **상태 (Status)**: Accepted
- **작성일**: 2026-04-22
- **결정자**: 리드 (사용자 본인)
- **영향 팀메이트**: 전 팀 (1/2/3/4) + 리드
- **관련 문서**:
  - [agent_docs/governance.md §5 — 외부 자원 수용 정책](../../agent_docs/governance.md#5-외부-자원-수용-정책)
  - [agent_docs/development-policies.md §5 — Wiring Rule](../../agent_docs/development-policies.md)
  - [CLAUDE.md §9 — 미해결 TODO (Phase 2 마이그레이션 계획)](../../CLAUDE.md)
  - [docs/archive/CLAUDE-pre-phase1-migration.md — Phase 1 이전 원본 아카이브](../archive/CLAUDE-pre-phase1-migration.md)
  - [docs/operations/check-rbac-coverage-tests-2026-04-22.md §4 — Path A 종결 기록 (OPS-020)](../operations/check-rbac-coverage-tests-2026-04-22.md)

---

## 1. 배경 (Context)

Phase 1 마이그레이션 (`b656186`, 2026-04 초) 은 Cowork → Claude Code Agent Teams 구조 전환을 완료했고, 후속 순차 작업("Path A") 으로 다음 6 단계를 거쳐 팀 운영 기반이 안정됐다.

| Step | 산출물 | 커밋 / PR | 상태 |
|---|---|---|---|
| 1 | Node 24 bump + Phase 3 CLI fallback 선제 스크립트 | `chore/gha-node24-bump` | Completed |
| 2 | Dev 의존성 파일 분리 (`backend/requirements-dev.txt`) | `chore/dev-deps-split` / OPS-018 | Completed |
| 3 | CLAUDE.md §5 "최근 회귀 사례" 30 커밋 요약 반영 | `docs/claude-md-commit-summary-30` | Completed |
| 4 | `.claude/rules/` 6개 경로별 가드 파일 | `docs/claude-rules-path-guards` | Completed |
| 5a | Stage 2: `check_bool_literals` regex → AST 전환 | PR #19 / OPS-019 | Completed |
| 5b | Stage 3: `check_rbac_coverage` 테스트 하니스 | PR #20 / OPS-020 | Completed |
| 6 | **본 ADR** — Phase 2 진입 gate 확립 | 본 PR | **In Progress** |

Path A 의 6 단계 중 1~5 는 **실행 코드 / 정적 검사기 / 문서 구조** 에 집중했다. Step 6 은 성격이 다르다 — "향후 외부 도구 도입을 어떤 절차로 심사할 것인가" 라는 **메타 규칙** 을 확정하는 작업이다. governance.md §5 에 이미 1~4 기준 (라이선스 / 공급망 / Wiring / 문서화) 이 명시되어 있지만, 구체적 ADR 프로세스와 단계별 Exit Criteria 는 정의되어 있지 않다. 외부 도구 후보가 늘어나면 개별 도입 결정이 재현성 없이 흩어질 위험이 있다.

### 1.1 후보 도구 3종의 현재 상태 (2026-04-22)

CLAUDE.md §9 와 `docs/operations/check-rbac-coverage-tests-2026-04-22.md §4` 가 후속 ADR 대상으로 지목한 외부 참고는 다음 세 가지다.

1. **Anthropic Agent Skills** (`anthropic/skills`)
   - **성격**: Anthropic 공식. `SKILL.md` YAML frontmatter + 지시문 + 스크립트 자원 폴더 구조. 2025-12 `open standard` 로 공개되어 Cursor/ChatGPT 등 타 에이전트에서도 사용 가능.
   - **현재 AQTS 상태**: 본 세션 샌드박스에서 `xlsx`, `pptx`, `docx`, `pdf` 등이 이미 availability 상태로 떠 있음. `.claude/rules/` 와는 메커니즘이 다르며 (rules = 경로 편집 자동 로드, skills = 명시적 호출), 병립 가능.
   - **AQTS 활용 시나리오**: 팀메이트 4 (Tests/Doc-Sync) 가 산출하는 주간 리포트, 팀메이트 1 (Backtest) 이 산출하는 OOS 결과 리포트 등을 `xlsx`/`docx` 스킬로 구조화.
   - **심사 우선순위**: **1 (높음)**. 이미 부분 활성화 상태이며, 공식 표준이고 라이선스/공급망 리스크가 낮음.

2. **StyleSeed** (`bitjaru/styleseed`)
   - **성격**: 프론트엔드 UI 디자인 시스템. 69 design rules + 48 shadcn components + Tailwind v4 + Radix. `/ss-*` 슬래시 커맨드로 페이지·컴포넌트·디자인 리뷰 등 수행.
   - **AQTS 적용 가능성**: **없음**. AQTS 는 FastAPI 기반 **backend-only** 프로젝트로, shadcn/Tailwind/Radix 를 쓰는 프론트엔드 자산이 전혀 없다 (`backend/` + `scripts/` + `docs/` + `alembic/` + 인프라 yaml 구성).
   - **심사 우선순위**: **Not Applicable**. 본 ADR 의 심사 대상 목록에서 제외하며, 프론트엔드 자산이 추가되는 시점에 재심사.

3. **Graphify** (`safishamsi/graphify`)
   - **성격**: 코드베이스 knowledge graph. Tree-sitter 기반 AST 파싱 (25 언어), NetworkX + Leiden clustering, `PreToolUse` hook 으로 Claude Code 의 file-search 를 사전 lookup. `graph.html` / `GRAPH_REPORT.md` / `graph.json` 산출.
   - **AQTS 적용 가능성**: **제한적**. AQTS 는 Python 단일 언어, backend/ 하위 약 380 파일. 규모는 유효 범위에 들어가지만, Agent Teams 구조상 각 팀메이트가 자기 소유 디렉토리만 깊게 다루므로 단일 팀메이트 입장에서의 이득은 작다. 다만 리드의 교차 영역 (공급망, 알림 파이프라인, 회귀 경계) 이해에는 도움이 될 수 있다.
   - **심사 우선순위**: **2 (중간)**. Pilot 단계에서 14일 측정 후 ROI 판정이 적절하다.

### 1.2 심사 프레임워크 부재의 구체적 위험

governance.md §5 의 1~4 기준만으로는 다음 질문에 답할 수 없다.

- **언제 도입 결정을 최종화하는가?** 라이선스·CVE·Wiring·문서화가 모두 OK 여도, 실제 운영에서 유효한지 보이지 않는 상태로 main 에 병합되면 dead weight 가 된다 (Wiring Rule 위반과 동일 구조).
- **도입 후 롤백 기준은 무엇인가?** 도구가 설치된 뒤 회귀가 발생했을 때, "이 도구 때문인가 vs 다른 변경 때문인가" 를 판정할 근거가 없다.
- **재심사 트리거는 무엇인가?** 업스트림 메이저 버전 업, 공급망 사고, 내부 요구사항 변화 중 어느 것이 재심사를 유발하는지 불분명.
- **복수 도구 도입 순서는 어떻게 정하는가?** 세 도구를 동시에 도입하면 회귀 발생 시 책임 범위가 흐려진다 (development-policies.md §7 "한 가지 원인만 수정" 원칙의 확장).

본 ADR 은 위 네 질문에 대한 공식 답을 정의한다.

---

## 2. 결정 (Decision)

### 2.1 Phase 2 진입 선언

Path A Step 1~5 가 완료된 시점 (본 ADR 머지 직후) 을 **Phase 2 공식 진입** 으로 간주한다. Phase 2 의 범위는 다음 두 축으로 제한된다.

1. **외부 참고 도구 심사 및 단계적 도입** — 본 ADR 이 정의하는 4 단계 프로세스에 따른다.
2. **Phase 1 에서 관찰 경로로 남겨둔 정책의 strict 승격** — 예: `AQTS_STRICT_BOOL=true` 승격 (development-policies.md §13 관찰 Phase). 본 ADR 의 심사 대상은 아니지만 Phase 2 타임라인 안에서 병렬 진행 가능.

Phase 2 의 종료 조건은 정의하지 않는다 — Phase 2 는 "외부 참고가 쌓일 때마다 심사를 돌리는 상시 레인" 이다. 큰 구조 변경 (예: 프론트엔드 자산 추가, 멀티테넌시 도입, 다른 언어 스택 통합) 이 제안되면 Phase 3 ADR 로 별도 선언한다.

### 2.2 4 단계 심사 프로세스

도입 후보 1건당 다음 4 단계를 순차 통과해야 한다. 각 단계 Exit Criteria 가 모두 녹색이 되어야 다음 단계로 승격하며, 어느 단계에서든 빨강이 나오면 **즉시 롤백 + 원인 기록 + ADR Closure 로 전환** 한다.

#### Stage 1 — Proposal (ADR 초안)

- **목표**: governance.md §5 의 1~4 기준에 본 ADR 추가 요구사항을 얹어 도입 여부를 리드가 결정할 수 있는 근거를 만든다.
- **Exit Criteria**:
  1. `docs/architecture/adr-NNN-<tool-name>.md` 초안 작성. Status = Proposed.
  2. governance.md §5 의 1~4 기준에 대한 평가표 (아래 §2.3 템플릿) 포함.
  3. 도구의 **실패 모드** 최소 3개 이상 나열 (예: 공식 패키지가 취소되는 경우, 업스트림이 Node 24 runtime 으로 전환 실패, 내부 hook 충돌 등). 실패 모드마다 감지 방법과 롤백 경로 기재.
  4. **Pilot 담당 팀메이트 지정** — 한 명만 지정한다 (복수 지정 금지, 책임 범위 희석 방지).
  5. 리드 승인 (PR review). 승인 시 Status → Accepted.
- **Anti-pattern**:
  - 실패 모드를 "업스트림 이슈로 인한 중단" 같은 추상적 표현으로 퉁치지 않는다. 회귀가 발생했을 때 로그/메트릭으로 탐지 가능한 구체적 관찰점이 있어야 한다.
  - "모든 팀메이트가 쓸 수 있다" 는 주장 금지 — Pilot 단계는 단일 팀메이트 기준으로 측정한다.

#### Stage 2 — Sandbox (14일 격리 실험)

- **목표**: 도구가 AQTS 의 실 레포에서 동작하는지, Wiring Rule 을 위반하지 않는지, 예상치 못한 회귀를 유발하지 않는지 관찰.
- **설정**:
  - 지정된 Pilot 팀메이트의 **작업 worktree 안에서만** 활성화. main 브랜치에는 어떤 설정 파일도 병합하지 않는다.
  - 도구 설치/활성화에 필요한 파일은 `docs/architecture/sandbox/adr-NNN/` 하위로 격리. `.claude/rules/` / `backend/` / `scripts/` 는 건드리지 않는다.
  - 활성화 시작일을 ADR 에 명기하고, +14일을 Stage 2 종료일로 확정.
- **Exit Criteria (14일 시점)**:
  1. 해당 worktree 에서 Pilot 팀메이트가 실제로 도구를 **최소 5회 이상** 사용한 로그가 존재 (도구별 로그/산출물 또는 사용 체크리스트).
  2. 기간 내 CI 녹색 유지 (도구 사용으로 인한 lint/test/build 회귀 0건).
  3. 사용 중 발견된 모든 이슈 (라이선스 이슈 미리 놓친 조항 발견, 공급망 경보, 예상 밖 네트워크 호출 등) 를 ADR 본문의 "Sandbox 관찰 기록" 섹션에 정확히 기록.
  4. Pilot 팀메이트 주관 회고 (1 페이지 요약): 체감 이득, 체감 비용, 발견된 숨은 의존성.
  5. 공급망 재검사 — `pip-audit`, `grype`, (도구가 Python 이 아닐 경우) 대응 검사기. 14일 사이에 새로 노출된 CVE 가 있으면 즉시 Stage 2 연장 또는 Stop.
- **Stop 조건** (하나라도 해당되면 Stage 2 중단):
  - 회귀 1건이라도 발생하여 하루 이상 CI 가 빨갛게 유지됨.
  - 도구가 `.env` / 인증 토큰 / 개인정보에 접근하는 새 요구를 발견.
  - 업스트림이 조용히 repo 를 archive / delete / 라이선스 변경.

#### Stage 3 — Limited Rollout (30일, 전 팀메이트 옵트인)

- **목표**: Pilot 팀메이트 외 3명이 옵트인 방식으로 사용할 때에도 회귀가 없는지, Wiring Rule 이 유지되는지 확인.
- **설정**:
  - 도구 활성화에 필요한 설정 파일을 `docs/architecture/sandbox/adr-NNN/` 에서 정식 경로 (`.claude/`, `scripts/`, `backend/requirements-*.txt` 등) 로 이전. main 에 병합되지만, 팀메이트별 활성화 여부는 각자 선택.
  - 활성화 상태는 ADR 본문의 "Rollout 체크리스트" 에 팀메이트별로 기록 (예: `- [x] 팀메이트 1: 2026-05-07 opt-in`).
- **Exit Criteria (30일 시점)**:
  1. 최소 2명 추가 팀메이트가 옵트인 (Pilot 포함 총 3명 이상).
  2. 도입 전후 30일 기간의 CI 녹색 비율 유지 (±1% 이내). 회귀가 발생했다면 원인이 본 도구와 무관함을 명시적으로 증명.
  3. 도구 사용이 `.claude/rules/` 또는 `agent_docs/development-policies.md` 의 규칙과 충돌하지 않음을 확인. 충돌 시 규칙을 먼저 갱신하고, 갱신 내용을 PR 커밋 메시지에 명기.
  4. 공급망 재검사 (Stage 2 와 동일 절차).
- **Stop 조건**:
  - 추가 옵트인 팀메이트 0명 (유용성 부족 시그널).
  - Pilot 외 팀메이트 사용 후 회귀 1건 발생.

#### Stage 4 — Full Adoption (영구 편입)

- **목표**: 도구를 AQTS 의 표준 도구로 확정.
- **설정**:
  - `agent_docs/governance.md` 와 `agent_docs/development-policies.md` 에 사용 경로 명시.
  - 자동 활성화 여부 결정 — 자동 활성화하지 않고 옵트인 유지하는 것이 기본 (도구 폴백 비용 관리).
  - ADR Status → Accepted (Full Adoption).
- **Exit Criteria**:
  1. Stage 3 완료 후 14일 추가 관찰 기간 동안 회귀 0건.
  2. 도구 사용 실패 시의 폴백 경로를 `agent_docs/` 에 1문단 이상 기재.
  3. 재심사 트리거 명시 (예: "업스트림 메이저 버전 업", "CVE High 1건 이상", "공급망 사고" 등).

### 2.3 심사 평가표 템플릿

각 도구 ADR (Stage 1 Proposal) 은 반드시 아래 표를 포함한다.

| 기준 | 세부 항목 | 판정 | 근거 |
|---|---|---|---|
| §5-1 라이선스 | SPDX 식별자 / 상용 사용 가능 여부 | PASS/FAIL | LICENSE 파일 링크 |
| §5-2 공급망 신뢰성 | `pip-audit` 결과 | PASS/FAIL | 실행 로그 |
| §5-2 공급망 신뢰성 | `grype` 결과 (high+) | PASS/FAIL | 실행 로그 |
| §5-2 공급망 신뢰성 | 패키지 서명 여부 (cosign/sigstore) | PASS/N/A | verify 로그 |
| §5-3 Wiring | 도구가 호출되는 코드 경로 | 명시 | 파일:라인 |
| §5-3 Wiring | 통합 테스트 (실제 실행 경로 봉인) | PASS/MISSING | 테스트 파일 |
| §5-4 문서화 | 도입 이유 / 대안 / 롤백 경로 | 있음/없음 | ADR 섹션 링크 |
| ADR-001 추가 | 실패 모드 3개 이상 + 감지 방법 | 있음/없음 | ADR 섹션 링크 |
| ADR-001 추가 | Pilot 담당 팀메이트 1명 지정 | 지정됨/미지정 | ADR 섹션 링크 |
| ADR-001 추가 | Stage 2~4 타임라인 | 명기/미정 | ADR 섹션 링크 |

PASS 가 아닌 항목이 하나라도 있으면 Stage 1 승격 불가.

### 2.4 후속 ADR 목록 (예정)

본 ADR 은 프레임워크만 확립하고, 개별 도구 채택/거절은 별도 ADR 로 위임한다.

| ADR | 제목 | 대상 | 예상 Status | 예상 작성 시기 |
|---|---|---|---|---|
| ADR-002 | Anthropic Agent Skills 단계적 채택 | `anthropic/skills` SKILL.md 표준 | Proposed | Phase 2 진입 후 즉시 (우선순위 1) |
| ADR-003 | StyleSeed 대상 외 판정 | `bitjaru/styleseed` | Rejected (작성 시 즉시 Closure) | 선택적 — 프론트엔드 자산 추가 시 재검토 |
| ADR-004 | Graphify Pilot 평가 | `safishamsi/graphify` | Proposed | Phase 2 진입 후 ADR-002 완료 후 |

ADR-002/003/004 의 우선순위 결정은 본 ADR 머지 후 리드가 별도 결정한다. 본 ADR 에서는 세 도구의 심사 순서를 강제하지 않으며, 다만 **동시 착수 금지** (한 번에 한 ADR 진행) 를 원칙으로 둔다 — 회귀 발생 시 책임 범위 분리를 위해서다.

---

## 3. 결과 (Consequences)

### 3.1 긍정적 결과

- **재현성**: 외부 도구 도입 결정이 문서화된 4 단계를 거치므로, 6개월 뒤 다른 팀메이트가 "왜 이 도구를 채택했는가" 를 추적 가능하다.
- **회귀 봉쇄**: Stage 2 의 worktree 격리와 Stage 3 의 옵트인 구조가 main 브랜치 회귀를 2중으로 차단한다.
- **평가 표준화**: §2.3 평가표가 있으므로 새 후보가 들어와도 이전 도구와 같은 축에서 비교 가능.
- **공급망 리스크 감소**: 단계별 `pip-audit` / `grype` 재검사가 강제되어 도구 도입 후 노출된 CVE 를 놓치지 않는다.
- **Wiring Rule 준수**: Stage 1 평가표의 "통합 테스트" 항목이 Wiring Rule (development-policies.md §5) 을 외부 도구로 확장한다.

### 3.2 부정적 결과 / 수용 가능한 비용

- **도입 지연**: 4 단계를 모두 거치면 최소 44일 (0 + 14 + 30 + 0) + 14일 관찰 = 최소 58일이 소요된다. 긴급 도입이 필요한 도구가 나타날 경우 이 프로세스가 병목이 된다. — **수용 근거**: AQTS 는 trading 시스템으로 회귀 허용 비용이 매우 높으며, 외부 도구 도입은 본질적으로 비긴급 사안이다.
- **문서 오버헤드**: 도구 1개당 ADR 1개 + Sandbox 관찰 기록 + Rollout 체크리스트 = 약 3~4개 문서 단위 작업. — **수용 근거**: Phase 1 마이그레이션에서 이미 `agent_docs/` + `docs/operations/` + `.claude/rules/` 의 다층 문서 구조가 확립되었고, 본 ADR 은 그 위에 얹히는 구조로 중복이 크지 않다.
- **Pilot 팀메이트 지정의 비용**: 도구 한 개당 1명이 최소 14일간 추가 작업 책임을 진다. — **수용 근거**: Pilot 활동은 해당 팀메이트의 일상 작업에 통합되어 수행되며, 별도 공수가 아니다.
- **Rejected ADR 도 비용**: StyleSeed 의 경우 처음부터 대상 외로 판정되지만, ADR-003 형태로 "왜 대상 외인가" 를 남기는 비용이 발생한다. — **수용 근거**: 향후 프론트엔드 자산이 추가되는 시점에 "이전에 왜 제외했는가" 를 참조해야 하므로, 문서화 비용이 아니라 미래의 재심사 비용 선지급이다.

### 3.3 롤백 경로 (본 ADR 자체가 실패할 경우)

본 ADR 의 프레임워크가 과도하게 경직되어 Phase 2 진행이 stuck 되면 다음을 수행한다.

1. 리드가 본 ADR Status → Superseded 로 전환.
2. 후속 ADR (예: ADR-005) 로 간소화된 프로세스 제안.
3. governance.md §5 는 원래대로 유지 (본 ADR 이 §5 를 수정하지 않기 때문에 롤백 비용이 작음).

본 ADR 은 `agent_docs/*.md` 와 `CLAUDE.md` 에 **규칙을 추가하지 않는다** — ADR 참조 링크만 추가한다. 따라서 본 ADR 자체가 제거되어도 SSOT 에는 영향이 없다.

---

## 4. 대안 (Alternatives Considered)

### 4.1 대안 A — 심사 없이 자유 도입

- **내용**: governance.md §5 의 1~4 기준만 통과하면 main 에 직접 병합.
- **장점**: 도입 속도 최대화.
- **단점**: Wiring Rule 위반을 통합 테스트 이전 단계에서 걸러낼 방법이 없다. RBAC 결손 (9위 RBAC 도입 회고) 과 동일 구조의 회귀가 외부 도구에서도 재발할 위험.
- **거절 근거**: AQTS 는 trading 시스템으로 회귀 발생 시 손실 확정 가능성이 있다. 도입 속도보다 회귀 봉쇄가 우선한다.

### 4.2 대안 B — 모든 외부 도구 도입 영구 금지

- **내용**: Phase 2 를 "자체 개발만" 하는 단계로 정의.
- **장점**: 공급망 리스크 제로.
- **단점**: `anthropic/skills` 같은 공식 표준을 거부하는 것은 비합리적. 이미 샌드박스에서 xlsx/pptx/docx/pdf 가 활용되고 있어, 금지는 현실과 모순.
- **거절 근거**: 현재 운영 상태와 모순.

### 4.3 대안 C — 본 ADR 이 채택한 4 단계 심사

- **내용**: 위 §2.2 에서 정의한 Proposal → Sandbox → Limited Rollout → Full Adoption.
- **장점**: 도입 속도와 회귀 봉쇄의 균형. 각 단계 Exit Criteria 가 명시적이라 판단 자동화 가능.
- **단점**: 앞서 §3.2 에서 기술한 지연 · 문서 오버헤드.
- **채택 근거**: AQTS 의 현재 위험 프로파일과 팀 규모(4 팀메이트 + 리드) 에서 가장 균형 있는 선택.

### 4.4 대안 D — 단일 Phase 2 ADR 안에 3 도구 모두 결정

- **내용**: 본 ADR 에서 anthropic-skills 채택, StyleSeed 거절, Graphify Pilot 을 동시에 결정.
- **장점**: 한번에 Phase 2 전체 방향이 확정됨.
- **단점**: ADR 1건에 여러 결정이 들어가면 재심사 트리거 발생 시 어느 결정에 대한 재심사인지 분리가 어려움. 도구별 롤백 경로가 서로 간섭할 위험.
- **거절 근거**: ADR 은 "하나의 결정, 하나의 문서" 원칙 (ADR 커뮤니티 convention). 본 ADR 은 프레임워크 확립이라는 단일 결정을 다루고, 개별 도구는 ADR-002/003/004 로 위임.

---

## 5. 검증 (Validation)

### 5.1 본 ADR 문서 자체의 검증 절차

- [x] `docs/architecture/adr-001-phase2-entry-gate.md` 신설 (본 파일).
- [x] `agent_docs/governance.md §5` 의 마지막 문단에 본 ADR 참조 추가 예정 (본 커밋에 포함).
- [x] `CLAUDE.md §9` 의 "Phase 2 마이그레이션 계획" TODO 를 [x] 로 전환하고 본 ADR 링크 추가 (본 커밋에 포함).
- [x] `docs/operations/check-rbac-coverage-tests-2026-04-22.md §4` 의 "Phase 2 ADR 작성" 후속 작업 언급을 본 ADR 로 연결.
- [x] 최소 게이트 통과: `ruff check`, `black --check`, `check_bool_literals.py`, `check_doc_sync.py`.
- [x] 문서-only 커밋이므로 전체 pytest 생략 (CLAUDE.md §3.1 예외 적용). `.py` / `.toml` / `.sh` / `Dockerfile*` / `.github/workflows/*.yml` 변경 zero 임을 `git diff --stat` 로 확인.

### 5.2 본 ADR 발효 후 후속 검증

본 ADR 이 유효하게 동작하는지는 **첫 번째 개별 도구 ADR (ADR-002 예정)** 이 Stage 1 ~ 4 를 무사히 통과하는 것으로 확인한다. 그 과정에서 본 ADR 의 §2.2 / §2.3 이 실무적으로 운영 가능한지 드러나며, 필요 시 본 ADR 을 Accepted (Revised) 로 개정한다.

---

## 6. 부록 — 외부 참고 도구 상세 조사 (2026-04-22 시점)

### 6.1 anthropic/skills (Agent Skills)

- **저장소**: <https://github.com/anthropics/skills>
- **라이선스**: 저장소 LICENSE 파일 직접 확인 필요 (공식 저장소이나 라이선스 독립 검토 필요).
- **표준화 상태**: 2025-12 open standard 공개. Cursor, ChatGPT, Claude 간 호환.
- **AQTS 샌드박스 현재 상태**: `xlsx`, `pptx`, `docx`, `pdf` 등 스킬이 availability 상태. 본 세션에서 호출 시 즉시 실행 가능.
- **추정 공급망 리스크**: Anthropic 공식 배포 채널이므로 타 커뮤니티 스킬 대비 리스크 낮음. 다만 ADR-002 의 Stage 1 평가표 작성 시 개별 스킬 단위로 `pip-audit` 는 여전히 필요 (스킬이 내부적으로 Python 의존성을 선언할 수 있음).
- **주의점**: SKILL.md 가 open standard 로 전환되면서 제3자 스킬이 급증하는 중 (검색 결과상 "232+ Claude Code skills", "423 plugins, 2,849 skills" 등 커뮤니티 집합 존재). ADR-002 의 scope 는 **Anthropic 공식 스킬로 한정** 한다. 커뮤니티 스킬은 별도 ADR (예: ADR-006) 로 분리 심사.

### 6.2 bitjaru/styleseed (StyleSeed)

- **저장소**: <https://github.com/bitjaru/styleseed>
- **핵심 내용**: 69 design rules + 48 shadcn components + Tailwind v4 + Radix. `/ss-setup`, `/ss-page`, `/ss-component`, `/ss-review`, `/ss-a11y`, `/ss-audit`, `/ss-copy`, `/ss-feedback` 등 13 슬래시 커맨드.
- **AQTS 적용 가능성**: **Not Applicable**. AQTS 는 backend-only (FastAPI + SQLAlchemy + Alembic + Motor + Redis + Prometheus + Grafana) 로 React/Tailwind/shadcn 자산이 없다.
- **재심사 트리거**: 프론트엔드 자산 추가 (예: 운영자용 대시보드 프로젝트 신설). 그 시점에 ADR-003 을 Reopen.

### 6.3 safishamsi/graphify (Graphify)

- **저장소**: <https://github.com/safishamsi/graphify>
- **핵심 내용**: Tree-sitter AST 파싱 (25 언어), NetworkX + Leiden clustering, PreToolUse hook 으로 file-search 사전 lookup. `graph.html` 인터랙티브 그래프 + `GRAPH_REPORT.md` + `graph.json` 산출.
- **AQTS 적용 가능성**: **제한적**. Python 단일 언어에 backend/ 하위 약 380 파일. Pilot 단계에서 팀메이트 3 (API/RBAC) 또는 팀메이트 4 (Tests/Static) 가 교차 파일 이해가 잦은 영역에서 측정하는 것이 적절.
- **주의점**:
  - 공식 skill 표준을 따르지만 Anthropic 공식이 아닌 커뮤니티 skill. Stage 1 평가표의 공급망 기준 엄격 적용 필요.
  - PreToolUse hook 이 file-search 를 intercept 하므로, `agent_docs/development-policies.md §5` (Wiring Rule) 와 간섭 여부 검증이 핵심. Hook 실패 시 file-search 가 silent fail 하지 않는지 Stage 2 에서 확인.

---

## 7. 변경 이력

| 날짜 | 변경 내용 | 작성자 |
|---|---|---|
| 2026-04-22 | 최초 작성 (Path A Step 6) | 리드 |
