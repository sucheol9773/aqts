---
from: lead
to: 2
subject: ownership-realtime-hook
created: 2026-04-25T13:09:14Z
priority: Ask  # [P0] 긴급, [Ask] 응답 요청, [FYI] 참고, [Lead-Approval] 리드 승인
---

# ownership-realtime-hook

## 요약

팀 vs 팀 영역 침범을 **실시간(편집 직전) 차단**하기 위한 신규 PreToolUse Edit/Write hook 의 **셸 스크립트 + settings.json wiring + bypass 메커니즘** 구현을 팀메이트 2 영역으로 위임합니다. 본 메시지는 팀 2 (hook 인프라) + 팀 4 (파서 모듈) 공동 작업의 팀 2 분담분이며, 팀 4 의 sibling 메시지는 `agent_docs/mailboxes/team4/inbox/20260425-2209-ownership-realtime-parser.md` 입니다.

## 맥락

### 현재 보강 상태

1. **PR 레벨 사후 검사기** — `scripts/check_ownership_boundary.py` + `backend/tests/test_check_ownership_boundary.py` (이미 머지됨)
2. **Bash PreToolUse hook** — `scripts/team/pre_bash_guard.sh` (force-push / hard-reset / rm-rf 류 차단). 본 작업의 **셸 패턴 원형**.
3. **리드 영역 정적 deny** — `.claude/settings.json` permissions.deny 6 경로 (`CLAUDE.md`, `agent_docs/development-policies.md`, `backend/core/utils/{env,time}.py`, `backend/config/settings.py`, `.env.example`, `docs/archive/**`)

### 격차

- **팀 vs 팀 실시간 차단 부재** — 팀 1 worktree 에서 `backend/api/**` (팀 3 영역) 를 편집해도 PreToolUse Edit/Write hook 이 없어 통과. PR 단계 사후 검사기에서만 잡혀 30분 작업 후 차단되는 시나리오 발생.

### 아키텍처 결정 (리드)

PreToolUse Edit/Write hook 신설. 동작:

1. 현재 worktree 경로에서 팀 ID 추출 (`aqts-team{N}-*` 패턴, lead worktree 는 통과)
2. tool input 의 `file_path` 를 governance §2.3 매트릭스로 매핑 (팀 4 분담 — `scripts/check_team_boundary.py`)
3. owner ≠ 현재 팀 → deny + 메일박스 명령 안내

본 분담은 **1번 팀 ID 추출 + 3번 deny 출력 + bypass + Claude Code hooks 등록**.

## 요청

### 1. `scripts/team/pre_edit_guard.sh` 신설

기존 `scripts/team/pre_bash_guard.sh` 와 동일한 stdin/stdout JSON 프로토콜:

- **stdin**: Claude Code 가 PreToolUse hook payload 를 JSON 으로 전달. 핵심 필드:
  - `tool_input.file_path` (Edit/Write/MultiEdit/NotebookEdit 모두)
  - `cwd` (worktree 경로 추출용)
- **stdout (JSON)**:
  - 통과: `{}` (또는 `{"decision": "allow"}`)
  - 차단: `{"decision": "deny", "reason": "<한국어 사유>"}`
- **exit code**: 0 (통과 또는 deny 모두), 1 (예상치 못한 오류)

### 2. 동작 알고리즘

```bash
#!/usr/bin/env bash
set -euo pipefail

PAYLOAD="$(cat)"
FILE_PATH="$(echo "$PAYLOAD" | jq -r '.tool_input.file_path // empty')"
CWD="$(echo "$PAYLOAD" | jq -r '.cwd // .session_dir // empty')"

# 1. file_path 가 없으면 통과 (예: 일부 tool 변형)
[[ -z "$FILE_PATH" ]] && echo '{}' && exit 0

# 2. 환경변수 bypass (logged to stderr)
if [[ "${AQTS_OWNERSHIP_BYPASS:-0}" == "1" ]]; then
  echo "[pre_edit_guard] BYPASS active: $FILE_PATH" >&2
  echo '{}' && exit 0
fi

# 3. worktree 경로에서 팀 ID 추출
#    ^.../aqts-team1-strategy → team1
#    ^.../aqts$ (lead) → 통과
#    ^.../aqts-team4-skills-pilot → team4 (Pilot 도 팀 4 영역만)
TEAM=$(extract_team_from_path "$CWD")
if [[ "$TEAM" == "lead" ]]; then
  echo '{}' && exit 0
fi

# 4. 팀 4 의 파서 호출 (`scripts/check_team_boundary.py`)
REASON=$(python scripts/check_team_boundary.py "$FILE_PATH" --team "$TEAM" 2>&1) || {
  EXIT=$?
  if [[ "$EXIT" == "1" ]]; then
    # deny — REASON 을 JSON-escape 해서 반환
    JSON_REASON=$(echo "$REASON" | jq -Rs .)
    printf '{"decision": "deny", "reason": %s}\n' "$JSON_REASON"
    exit 0
  else
    echo "[pre_edit_guard] internal error (exit=$EXIT): $REASON" >&2
    exit 1
  fi
}

# 5. 통과
echo '{}'
```

### 3. 팀 ID 추출 함수

리드 worktree 와 팀 worktree 구분:

- 리드 = `/Users/ahnsucheol/Desktop/aqts` (정확 일치) 또는 `${REPO_ROOT}` 환경변수
- 팀 1 = `/Users/ahnsucheol/Desktop/aqts-team1-*` (basename 정규식)
- 팀 2 = `/Users/ahnsucheol/Desktop/aqts-team2-*`
- 팀 3 = `/Users/ahnsucheol/Desktop/aqts-team3-*`
- 팀 4 = `/Users/ahnsucheol/Desktop/aqts-team4-*` (Pilot 포함)
- `.claude/worktrees/*` (Claude Code isolation worktree) — owner 추적이 본 hook 의 부담 → **리드와 동일 통과** (이미 PR 단계 사후 검사기로 보호됨)

추출 실패 (예: 외부 디렉토리에서 호출) → 보수적 통과 (Claude Code 가 worktree 외에서 실행되는 경우는 사용자 직접 의도)

### 4. `.claude/settings.json` 등록

`hooks.PreToolUse` 에 새 entry 추가 (기존 Bash matcher 와 별개):

```json
{
  "matcher": "Edit|Write|MultiEdit|NotebookEdit",
  "hooks": [
    {
      "type": "command",
      "command": "scripts/team/pre_edit_guard.sh"
    }
  ]
}
```

### 5. bypass 메커니즘 (3 단계)

1. **환경변수** `AQTS_OWNERSHIP_BYPASS=1` — 가장 단순. 의도된 우회. stderr 에 로그.
2. **lead worktree** — 항상 통과. cwd 가 정확히 `/Users/ahnsucheol/Desktop/aqts` 인 경우.
3. **(향후 v2)** `[Lead-Approval]` 메일박스로 위임받은 임시 화이트리스트 — **본 PR 에 포함하지 않음**, follow-up TODO 로 README 에 명시.

### 6. `scripts/team/README.md` 업데이트

새 hook 의 동작 + bypass 사용법 추가. 기존 `pre_bash_guard.sh` 섹션과 동일 구조:

- 트리거 조건
- 차단 시 사용자 액션 (`scripts/team/mailbox_new.sh ...` 또는 lead worktree 로 이동)
- bypass 사용 가이드 (환경변수, lead worktree)

### 7. 회귀 테스트 하니스

`backend/tests/test_pre_edit_guard.py` (또는 셸 단위 테스트는 `backend/tests/test_pre_edit_guard_sh.py`):

- stdin JSON payload → stdout JSON 결과 매칭 (subprocess 호출, jq 파싱)
- 4 케이스: lead 통과 / team1→team1 통과 / team1→team3 deny / `AQTS_OWNERSHIP_BYPASS=1` 통과
- 팀 4 파서가 미설치 (mock) 인 경우의 동작 — exit 1 + stderr 로그
- claude code hook stdin payload 의 실제 스키마 변형 대응 (`tool_input.file_path` 누락, `cwd` 누락)

목표 ≥ 8 tests.

### 8. 게이트

- ruff + black 무영향 (셸 스크립트만)
- shellcheck PASS (`shellcheck scripts/team/pre_edit_guard.sh`)
- `pytest backend/tests/test_pre_edit_guard*.py` ≥ 8 tests PASS
- 통합 검증: lead worktree 에서 `Edit(./backend/api/v1/orders.py)` 호출 시도 → 통과 (lead 면제)
- 통합 검증: `aqts-team1-strategy` 에서 `Edit(./backend/api/v1/orders.py)` 시도 → deny + reason 에 team3 명시
- 통합 검증: `aqts-team1-strategy` 에서 `AQTS_OWNERSHIP_BYPASS=1 + Edit(...)` → 통과 + stderr 로그

### 9. 작업 순서 (팀 4 와 협업)

1. **팀 4 (sibling)** — `scripts/check_team_boundary.py` 파서 + 테스트 우선 머지 (PR 1)
2. **팀 2 (본 메시지)** — 머지된 파서를 호출하는 `scripts/team/pre_edit_guard.sh` + settings.json + bypass + README (PR 2)
3. PR 1 머지 전 팀 4 의 인터페이스 (`lookup_owner` / `check_violation` / CLI exit code) 가 본 hook 호출 패턴 (위 §2 알고리즘) 과 호환되는지 합의 — 변경 시 양쪽 메일박스에 알림

## 응답 기한

**합의 응답**: 2026-04-29 (W1 마감 전후) — 팀 4 의 인터페이스 §1 + 본 hook 동작 §2 가 일관되는지 확인 회신.

**구현 머지**: 우선순위 P2. PR 1 (팀 4 파서) 머지 후 즉시 착수 가능. ADR-002 Stage 2 측정 공정성 (팀 4 = Pilot) 보호 위해 W2 진입 시점 (2026-04-29~) 머지 권장.

## 참조

- `agent_docs/governance.md §2.3, §2.4, §2.5` (SSOT)
- `scripts/team/pre_bash_guard.sh` (셸 hook 원형)
- `.claude/settings.json` (현재 hooks.PreToolUse 에 Bash matcher 1개만 등록됨)
- `scripts/check_ownership_boundary.py` (사후 검사기 — 본 작업의 사전 차단 대응짝)
- 팀 4 sibling: `agent_docs/mailboxes/team4/inbox/20260425-2209-ownership-realtime-parser.md`
