---
from: lead
to: 4
subject: ops023-pilot-merge-confirm
created: 2026-04-22T14:58:47Z
priority: Lead-Approval  # [P0] 긴급, [Ask] 응답 요청, [FYI] 참고, [Lead-Approval] 리드 승인
---

# ops023-pilot-merge-confirm

## 요약

Pilot worktree (`aqts-team4-skills-pilot`) 는 아직 HEAD 가 `f32b177` (PR #27) 에 머물러 있어 migration PR #28/#29 의 산출물 (`.claude/settings.json`, `.mcp.json`, `scripts/team/`, `agent_docs/mailboxes/` 스캐폴드) 이 반영되지 않았습니다. **ADR-002 Stage 2 Sandbox 진행 중이라 리드가 독단으로 merge 하지 않고 팀 4 확인 요청**합니다.

## 맥락

- 현재 Pilot HEAD: `f32b177` Merge pull request #27 (ADR-002 W1/W2 관찰 로그 스캐폴드)
- main HEAD: `2617633` (PR #28/#29/#30/#31/#32 머지 완료)
- **이미 확인된 긍정 요소** (`.claude/settings.local.json` 선제 작성):
  - `aqts-team4-skills-pilot/.claude/settings.local.json` 에 `permissions.allow=["*"]` + `disabledMcpjsonServers=["*"]` 이미 반영
  - untracked 파일이라 `git pull` 에 덮이지 않음 → merge 직후에도 격리 실효 유지
- 참조: OPS-023 §5.4 "Pilot worktree 격리 요구", OPS-024 §3 "Pilot worktree 격리"

## 요청 / 정보

### A. Stage 2 W1 로그 작성 기준 확인

Pilot 이 지금 main 을 merge 하면 다음이 반영됩니다:

1. `.claude/settings.json` (전역 permissions deny / PreToolUse hook / env)
2. `.mcp.json` (`github` MCP — 단, `settings.local.json` 이 `disabledMcpjsonServers=["*"]` 로 전량 차단)
3. `scripts/team/` 5개 스크립트 + `agent_docs/mailboxes/` 스캐폴드
4. 최근 리드 메일박스 공지 (PR #30 W1 kickoff, #31 lxml, #32 pip-audit)

**W1 관찰 로그 (`docs/architecture/sandbox/adr-002/skill-usage-log-W1.md`) 의 "동일 조건" 전제가 merge 로 깨지지 않는지** 확인 부탁드립니다. Skill trigger 측정이 본래 의도였으므로:
- `.claude/settings.json` 의 PreToolUse hook 이 skill 호출 빈도에 영향을 주는지
- `agent_docs/mailboxes/` 스캐폴드가 새 skill trigger 소스가 되는지
- W1 측정이 이미 시작된 상태라면 merge 는 W1 Exit (한 주 단위) 이후로 연기 권장

### B. Merge 방식 결정 (택 1)

1. **즉시 merge** — W1 로그에 "2026-04-22 main merge" 타임라인 주석 추가 후 merge
2. **W1 Exit 까지 연기** — 약 2026-04-29 시점 W1 마감 후 merge → W2 기간을 공정 조건에서 시작
3. **Stage 2 전체 Exit (2026-05-06) 까지 완전 연기** — Pilot worktree 만 구 SHA 로 유지, 2026-05-06 판정 후 일괄 merge

리드 권장: **2번 (W1 Exit 후)** — Stage 2 Exit 정량 게이트 §5.3.1 의 "20-query trigger eval 3 runs" 측정 공정성 보호.

### C. Merge 직후 필수 검증 (merge 시점에 Pilot 이 실행)

```bash
cd /Users/ahnsucheol/Desktop/aqts-team4-skills-pilot

# 1. settings.local.json 격리 실효 확인 (덮이지 않았는지)
cat .claude/settings.local.json | jq '.disabledMcpjsonServers'
# 기대: ["*"]

# 2. MCP 비활성 확인
claude --debug 2>&1 | grep -i mcp | head -20
# 기대: github MCP 가 "disabled" 또는 "skipped" 로 표기

# 3. wiring smoke
scripts/team/wiring_smoke.sh
# 기대: WIRING SMOKE PASSED
```

3개 항목 중 1개라도 실패하면 즉시 리드에게 메일 (`scripts/team/mailbox_new.sh 4 lead pilot-merge-regression`).

## 응답 기한

**2026-04-25** (W1 중반) — B 결정에 따라 리드가 A-2 (`git merge --ff-only origin/main`) 를 직접 실행하거나 Pilot 이 직접 실행.
