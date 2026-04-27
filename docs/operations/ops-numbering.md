# OPS 문서 번호 발급 history & 운영 규약

> **목적**: AQTS `docs/operations/` 의 `OPS-NNN` 문서 번호 충돌 방지를 위한 단일
> 진실원천(SSOT). 신규 OPS 문서를 작성하기 전 본 문서의 §2 발급 history 를
> 확인하여 다음 빈 번호를 사용하고, **동일 PR 안에서 §2 표에 한 줄을 추가**한다.
>
> 본 문서 자체는 OPS 번호를 가지지 않는다 — registry 이지 entry 가 아니다.

**소유**: 공동 (작업 팀이 발급, 리드가 검토)
**갱신 주기**: 신규 OPS 발급 시점 즉시 — PR 안에 본 표 갱신 동봉
**최종 갱신**: 2026-04-27 (OPS-030 활성 — 인프라 backfill audit, §14.5 축 2)

---

## 1. 발급 규칙

1. **번호 1:1 매핑**. 동일 번호를 두 문서가 공유할 수 없다 (§3 충돌 회고 참조). 번호는 단조 증가하며, 결손(gap)은 의도적으로 비워두고 새 번호 발급 시 사용 중 최댓값 + 1 을 사용한다.
2. **예약 번호**는 §2 표에 `(예약)` 표기 + 사유 + 예약 만료 조건을 기록한다. 예약된 번호로 실제 작업이 완료되면 `활성` 으로 전환하고, 만료되면 결손으로 비워둔 채 재사용은 허용하지 않는다 (혼동 방지).
3. **PR atomic**: 신규 OPS 문서 PR 안에 본 §2 표 entry 추가가 반드시 함께 들어간다 — 두 PR 로 분리 금지. 분리 시 본 표가 silent miss 상태가 되어 다음 작업자가 이미 사용 중인 번호를 재발급하는 충돌이 발생한다 (§3.1 회고).
4. **SSOT 두 곳 일치**: 문서 헤더의 `**문서 번호**: OPS-NNN` 표기와 본 표 한 줄은 항상 일치해야 한다. 향후 정적 검사기 (`scripts/check_ops_numbering.py`, §6 TODO) 로 자동 검증할 예정.
5. **충돌 발견 시 절차**: §3.1 OPS-023 정정 사례를 표준 절차로 사용 — 외부 reference 가 더 적은 쪽을 새 번호로 재발급하고, 본 §2 표에 두 변경(원래 번호 → 활성 유지, 재발급 번호 → 신규 entry) 을 모두 기록한다.

---

## 2. 발급 history

**상태 어휘**:

- `활성` — 문서가 main 에 머지되어 `docs/operations/<file>.md` 가 존재하고 헤더의 `**문서 번호**: OPS-NNN` 표기가 본 표와 일치.
- `예약` — 번호만 점유, 실제 문서는 미작성. §1.2 에 따라 만료 조건 + 사유 기록 필수.
- `branch-only` — 문서 commit 은 존재하나 main 에 미머지. **§1.4 SSOT 일치 규칙의 잠정 예외** (main 기준으로 파일이 없는 상태). 다음 작업자가 동일 번호를 재발급하지 않도록 본 표에 기록하되, PR 머지 즉시 `활성` 으로 전환. branch-only 가 30일 이상 머무르면 작업 폐기 의심으로 본 row 를 제거하거나 `예약` 으로 전환 검토.
- `결손` — 의도적으로 비워둔 번호 (gap). §1.1 에 따라 재사용 금지.
- `⚠️ 충돌` — 동일 번호를 두 문서가 공유 중. §3 회고로 정정 PR 추적.

| 번호 | 문서 | 발급일 | 분류 | 상태 |
|---|---|---|---|---|
| OPS-001 | `trading-halt-policy.md` | 2026-03 | 정책 | 활성 |
| OPS-002 | `incident-runbook.md` | 2026-03 | 런북 | 활성 |
| OPS-003 | `model-change-policy.md` | 2026-03 | 정책 | 활성 |
| OPS-004 | `release-gates.md` | 2026-03 | 정책 | 활성 |
| OPS-005 | `rollback-plan.md` | 2026-03 | 런북 | 활성 |
| OPS-006 | `customer-notice.md` | 2026-04-05 | 정책 | 활성 (선발급) |
| OPS-006 | `midday-check-path-a-runbook.md` | 2026-04-15 | 런북 | ⚠️ **충돌 — 재발급 필요** (§3.2) |
| OPS-007 | `docker-setup-guide.md` | 2026-04 | 인프라 | 활성 |
| OPS-008 | `deployment-roadmap.md` | 2026-04 | 정책 | 활성 |
| OPS-009 | `gcp-provisioning-guide.md` | 2026-04-05 | 인프라 | 활성 (선발급) |
| OPS-009 | `phase1-demo-verification-2026-04-11.md` | 2026-04-11 | 작업 기록 | ⚠️ **충돌 — 재발급 필요** (§3.3) |
| OPS-010 ~ OPS-016 | — | — | — | 결손 (gap) |
| OPS-017 | `static-checker-venv-audit-2026-04-21.md` | 2026-04-21 | 작업 기록 | 활성 |
| OPS-018 | `dev-deps-split-2026-04-21.md` | 2026-04-21 | 작업 기록 | 활성 |
| OPS-019 | `check-bool-literals-ast-2026-04-22.md` | 2026-04-22 | 정적 검사기 | 활성 |
| OPS-020 | `check-rbac-coverage-tests-2026-04-22.md` | 2026-04-22 | 정적 검사기 | 활성 |
| OPS-021 | `lxml-6.1.0-upgrade-2026-04-25.md` | 2026-04-25 | 보안 업그레이드 | 활성 (예약 2026-04-22 → 활성 전환, `chore/lxml-6.1.0-upgrade` PR) |
| OPS-022 | `check-vuln-ignore-parity-2026-04-23.md` | 2026-04-23 | 정적 검사기 | 활성 |
| OPS-023 | `claude-multisession-migration-2026-04-22.md` | 2026-04-22 | 작업 기록 | 활성 (단일 할당, §3.1 정정 후) |
| OPS-024 | `mcp-setup-2026-04-22.md` | 2026-04-22 | 작업 기록 | 활성 |
| OPS-025 | `security-deps-split-2026-04-25.md` | 2026-04-25 | 작업 기록 | 활성 (`chore/pip-audit-deps-split` PR, branch-only → 활성 전환 2026-04-25) |
| OPS-026 | `check-vuln-ignore-expiry-2026-04-23.md` | 2026-04-23 (재발급 2026-04-25) | 정적 검사기 | 활성, §3.1 참조 |
| OPS-027 | (예약 — `scripts/check_ops_numbering.py` 정적 검사기) | 예약 2026-04-25 | 정적 검사기 | 예약, 만료 조건 = 팀 4 가 PR #49 메일 (`agent_docs/mailboxes/team4/inbox/20260425-2201-ops-027-check-ops-numbering.md`) 처리하여 검사기 신설 PR 머지 시 `활성` 전환. 만료 deadline = 2026-05-13 (W2 진입 + 14일 마진). |
| OPS-028 | `postgres-wal-archive-permission-2026-04-26.md` | 2026-04-26 | 작업 기록 (P0 incident retro) | 활성 |
| OPS-029 | `infrastructure-setup-checklist-2026-04-26.md` | 2026-04-26 | 운영 체크리스트 | 활성 (§14.5 정책의 운영 implementation, OPS-028 §4 후속) |
| OPS-030 | `infrastructure-backfill-audit-2026-04-27.md` | 2026-04-27 | 인프라 audit | 활성 (§14.5 축 2, OPS-029 직접 후속) |

**다음 발급 가능 번호**: **OPS-031** (OPS-030 다음).

**현재 미해결 충돌**: 2건 (OPS-006, OPS-009 — §3.2, §3.3 참조).

---

## 3. 충돌 회고 (incident log)

### 3.1 OPS-023 충돌 (resolved 2026-04-25, PR #40)

**증상**: 2개 문서가 동일 OPS-023 번호 사용
- `claude-multisession-migration-2026-04-22.md` — 2026-04-22 발급, 외부 reference 11곳 (mcp-setup, scripts/team/README, agent_docs/governance harness 표, mailbox 파일명 포함)
- `check-vuln-ignore-expiry-2026-04-23.md` — 2026-04-23 발급, 외부 reference 3곳

**근본 원인**: 본 registry 가 존재하지 않아, 두 번째 문서를 작성한 작업자가 다음 빈 번호를 다른 위치(CLAUDE.md §9 TODO 항목 / 다른 문서의 reference) 에서 추정해 발급. 이때 첫 문서가 이미 OPS-023 을 점유 중인 사실이 SSOT 부재로 silent miss.

**해소 절차**:
1. 두 문서의 외부 reference 를 grep 으로 enumerate
2. reference 가 적은 쪽 (`check-vuln-ignore-expiry`) 을 OPS-026 으로 재발급
3. 변경 3 위치 (헤더 / 본문 회귀 방어 표 / CLAUDE.md §9 TODO reference) 단일 atomic PR
4. 정상 위치 (`claude-multisession-migration` 측 OPS-023 reference 11곳) 무변경

**교훈**: 본 registry (ops-numbering.md) 가 작업 시작 전 첫 번째 확인처가 되어야 한다. CLAUDE.md §9 의 TODO 목록은 진행 상태 기록이지 OPS 발급 SSOT 가 아니다.

### 3.2 OPS-006 충돌 (open, 2026-04-25 발견)

**증상**:
- `customer-notice.md` (2026-04-05 발급, Gate E 비즈니스 승인 commit `e31acf1`) — 활성, 경영진 승인 정책
- `midday-check-path-a-runbook.md` (2026-04-15 발급, commit `5bb0092`) — SUSPENDED 상태 런북

**권장 해소 방향**: 후자 (`midday-check-path-a-runbook.md`) 를 OPS-027 또는 후속 가용 번호로 재발급. 이유:
- 전자가 시간순 선발급 (10일 차이)
- 전자가 외부 reference 가 더 광범위할 가능성 (정책 문서)
- 후자가 SUSPENDED 상태라 reference 변경 cost 가 낮음

**우선순위**: 낮음 — `midday-check-path-a-runbook.md` 가 SUSPENDED 라 즉시 회귀 위험 없음. Path A 재개 결정 시 함께 정정.

**작업 절차**: §3.1 표준 절차 그대로 적용.

### 3.3 OPS-009 충돌 (open, 2026-04-25 발견)

**증상**:
- `gcp-provisioning-guide.md` (2026-04-05 발급, commit `c88608d` 이 commit 메시지에 명시적으로 `(OPS-009)` 표기) — 활성 인프라 가이드
- `phase1-demo-verification-2026-04-11.md` (2026-04-11 발급, commit `f755ad1`) — 활성 검증 리포트

**권장 해소 방향**: 후자 (`phase1-demo-verification-2026-04-11.md`) 를 OPS-028 또는 후속 가용 번호로 재발급. 이유:
- 전자가 시간순 선발급 (6일 차이)
- 전자의 commit 메시지가 명시적으로 OPS-009 를 선포 (`docs: GCP 프로비저닝 가이드 추가 (OPS-009)`)
- 후자는 commit 메시지가 OPS 번호를 명시하지 않음 — 작성자가 임의로 동일 번호를 부여한 것으로 추정

**우선순위**: 중간 — 두 문서 모두 활성이라 reference 정합성 확인 필요. CLAUDE.md / agent_docs / 다른 ops 문서의 OPS-009 reference 가 어느 쪽을 지칭하는지 grep 으로 분리한 뒤 진행.

**작업 절차**: §3.1 표준 절차 그대로 적용.

---

## 4. 다음 OPS 번호 발급 절차 (체크리스트)

신규 OPS 문서 작성 시:

- [ ] 본 §2 발급 history 표에서 사용 중 최댓값 확인 (현재 = OPS-026)
- [ ] §2 의 "다음 발급 가능 번호" 줄 확인 (현재 = OPS-027)
- [ ] 미해결 충돌 (§3.2, §3.3) 정정 PR 이 진행 중이면 그쪽이 사용할 번호와 중복되지 않게 협의
- [ ] 신규 문서 헤더에 `**문서 번호**: OPS-NNN` 표기 (인용부호·들여쓰기는 기존 문서 패턴 참조)
- [ ] 본 문서 §2 표에 한 줄 추가 (번호 / 파일 / 발급일 / 분류 / 상태=활성)
- [ ] §2 의 "다음 발급 가능 번호" 줄을 +1 갱신
- [ ] 두 변경을 동일 PR 에 commit (separate PR 금지)
- [ ] PR 머지 전 reviewer 가 본 §2 표 갱신 누락 여부 확인

---

## 5. 관련 문서

- `agent_docs/governance.md` — 팀 소유권 / Wiring Rule
- `agent_docs/development-policies.md` §2 — 커밋 문서화 규칙 (PR atomic 동봉)
- `docs/operations/check-vuln-ignore-parity-2026-04-23.md` (OPS-022) — 화이트리스트 parity 정적 검사기 (본 registry 와 유사한 SSOT 정합성 강제 패턴)
- `docs/operations/check-vuln-ignore-expiry-2026-04-23.md` (OPS-026) — 만료일 정적 검사기 (재발급 사례, §3.1 회고 대상)

---

## 6. 후속 작업 (TODO)

- [ ] **OPS-006 충돌 정정 PR**: `midday-check-path-a-runbook.md` 를 다음 가용 번호로 재발급. SUSPENDED 상태라 우선순위 낮음, Path A 재개 결정 시 함께 진행 (§3.2).
- [ ] **OPS-009 충돌 정정 PR**: `phase1-demo-verification-2026-04-11.md` 를 다음 가용 번호로 재발급. 두 문서 모두 활성이므로 reference 정합성 확인 후 진행 (§3.3).
- [ ] **`scripts/check_ops_numbering.py` 정적 검사기 신설**: 본 §2 표와 실제 `docs/operations/*.md` 헤더의 `**문서 번호**: OPS-NNN` 표기 간 정합성을 자동 검증. 위반 카테고리:
  - 표에는 있는데 파일이 없거나 헤더 OPS 번호가 다름
  - 파일 헤더에는 있는데 표에 없음
  - 동일 번호가 두 파일 헤더에 등장 (충돌)
  - "다음 발급 가능 번호" 줄이 실제 최댓값+1 과 불일치
  Doc Sync 워크플로에 0 errors 강제 등록. 본 문서 신설 후속 작업 (예상 OPS-027 또는 후속 번호).
