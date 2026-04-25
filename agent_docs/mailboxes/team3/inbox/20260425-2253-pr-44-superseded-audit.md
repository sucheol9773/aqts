---
from: lead
to: 3
subject: pr-44-superseded-audit
created: 2026-04-25T13:53:34Z
priority: FYI  # [P0] 긴급, [Ask] 응답 요청, [FYI] 참고, [Lead-Approval] 리드 승인
---

# pr-44-superseded-audit

## 요약

PR #44 (`docs/mailbox-init-and-task-delegation`) 를 **superseded** 사유로 close 합니다. 동봉된 메모 4종 (A/B/C/D) 의 모든 ask 가 다른 머지된 PR 들로 이미 해소되었고, 제안된 `agent_docs/mailbox/` (단수) 디렉토리는 PR #28 (OPS-023) 이 채택한 `agent_docs/mailboxes/` (복수, team-subdir 구조) SSOT 와 충돌합니다.

## 맥락

PR #44 가 2026-04-25 11:58 UTC 에 오픈된 시점에는 PR #28 (`agent_docs/mailboxes/` 구조 도입) 이 이미 머지된 상태였습니다 (2026-04-22). 동시 작업으로 인한 SSOT 인지 격차로, PR #44 가 의도한 "메일박스 인프라 신설" 은 이미 다른 경로로 완료되어 있었습니다. 본 메시지는 (a) 각 메모의 해소 경로 audit, (b) 향후 메일박스 작성 SSOT 재공지를 위한 기록입니다.

## 4 메모 해소 경로 audit

### 메모 A — `agent_docs/mailbox/2026-04-25-team4-Ask-lxml-OPS-021.md`

**원래 ask**: 팀 4 가 OPS-021 운영 회고 문서 작성

**해소 경로**: PR #45 (`chore/lxml-6.1.0-upgrade`) 머지로 `docs/operations/lxml-6.1.0-upgrade-2026-04-25.md` 생성됨. CLAUDE.md §9 의 lxml TODO 도 `0944cea` 커밋으로 `[x]` 전환 완료.

### 메모 B — `agent_docs/mailbox/2026-04-25-team4-Ask-vuln-parity-checker-and-W1-log.md`

**원래 ask**: 팀 4 가 `scripts/check_vuln_ignore_parity.py` (vuln-parity 검사기) + 7 시나리오 테스트 + OPS-022 문서 + W1 로그 §3.1 갱신

**해소 경로**: `scripts/check_vuln_ignore_parity.py` 존재, `docs/operations/check-vuln-ignore-parity-2026-04-23.md` (OPS-022) 머지됨. CLAUDE.md §9 parity TODO 도 `[x]` 전환 완료. ADR-002 W1 로그 갱신은 팀 4 Pilot worktree 에서 별도 진행 중 (2026-05-06 Stage 2 Exit 판정).

### 메모 C — `agent_docs/mailbox/2026-04-25-team2-Ask-doc-sync-vuln-parity-step.md`

**원래 ask**: 팀 2 가 `.github/workflows/doc-sync-check.yml` 에 parity 검사기 0 errors 강제 스텝 추가

**해소 경로**: `.github/workflows/doc-sync-check.yml` 에 vuln-ignore parity step 등록 완료 (`grep -l "check_vuln_ignore_parity" .github/workflows/*.yml` 가 본 워크플로 hit).

### 메모 D — `agent_docs/mailbox/2026-04-25-lead-Lead-Approval-bundle.md`

**원래 ask 3종**: (1) lxml bump 사전 동의, (2) CLAUDE.md §9 두 TODO `[x]` 전환, (3) 메일박스 위치 (a)/(b)/(c) 결정

**해소 경로**:
- (1) PR #45 (lxml 6.1.0) 머지로 자체 해소
- (2) `0944cea` 커밋으로 lxml + parity TODO `[x]` 전환 완료. 본 세션의 `a199603` (PR #46 pip-audit 머지) 까지 origin/main 반영.
- (3) PR #28 (OPS-023) 이 `agent_docs/mailboxes/` (복수, team-subdir 구조) 채택 — PR #44 의 옵션 (a)/(b)/(c) 와 다른 설계 (path-prefix 가 아닌 subdir-by-team) 가 SSOT 로 확정됨

## 향후 메일박스 작성 SSOT 재공지

신규 메일박스 작성 시 다음 패턴을 따릅니다 (PR #28, OPS-023 / OPS-024 결정):

```bash
scripts/team/mailbox_new.sh <from_team> <to_team> <subject-slug>
# 예: scripts/team/mailbox_new.sh 3 4 lxml-smoke-test-help
# 생성 위치: agent_docs/mailboxes/team<to>/inbox/YYYYMMDD-HHMM-<slug>.md
```

- **디렉토리**: `agent_docs/mailboxes/` (복수형, plural). PR #44 의 `agent_docs/mailbox/` (단수) 는 미채택.
- **구조**: `mailboxes/team{1,2,3,4}/inbox/` + `mailboxes/lead/inbox/` (각 팀 + 리드별 별도 inbox)
- **파일명**: `YYYYMMDD-HHMM-<slug>.md` (mailbox_new.sh 가 자동 생성)
- **우선순위 어휘**: front-matter `priority` 필드에 `P0` / `Ask` / `FYI` / `Lead-Approval` 중 택 1
- **README**: `agent_docs/mailboxes/README.md` 가 SSOT

PR #44 의 메모 4종은 이미 다른 경로로 해소되었으므로 새 SSOT 위치에 재작성할 필요 **없음**. 단, 향후 신규 cross-team 위임 발생 시 위 패턴 사용 부탁드립니다.

## 요청 / 정보

**FYI 메시지** — 별도 응답 불필요. 다음 사항만 확인 부탁드립니다:

1. **본 메시지 인지** — PR #44 close 사실 + 해소 경로 audit 결과
2. **로컬 워크트리 정리 (선택)** — 팀 3 worktree (`aqts-team3-api`) 가 `docs/mailbox-init-and-task-delegation` 브랜치를 점유하고 있다면 정리 권장:
   ```bash
   cd /Users/ahnsucheol/Desktop/aqts-team3-api
   git checkout main && git pull --ff-only origin main
   # 또는 새 작업 브랜치로 전환
   ```
3. **향후 메일박스 사용 SSOT** — `agent_docs/mailboxes/` (복수) + `mailbox_new.sh` 활용

## 참조

- PR #28 (OPS-023, multi-session migration) — 메일박스 인프라 SSOT 확정
- PR #45 (`chore/lxml-6.1.0-upgrade`) — 메모 A/D-(1) 해소
- 머지된 OPS-022 (`scripts/check_vuln_ignore_parity.py`) — 메모 B 해소
- `.github/workflows/doc-sync-check.yml` — 메모 C 해소
- `0944cea` 커밋 (CLAUDE.md §9 + ops-numbering 전환) — 메모 D-(2) 해소
- `agent_docs/mailboxes/README.md` — 신규 SSOT 입문

## 응답 기한

없음 (FYI). 별도 응답 불필요하나 인지 후 메일을 `processed/` 로 이동해 주시면 inbox 정리에 도움이 됩니다.
