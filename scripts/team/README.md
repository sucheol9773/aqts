# scripts/team/ — Multi-Session Migration 운영 도구

Claude Code 4개 독립 세션 운영을 지원하는 쉘 스크립트. OPS-023 migration 의 Phase 3 산출물.

## 스크립트 목록

| 스크립트 | 용도 | 위험도 |
|---|---|---|
| `bootstrap_worktree.sh <N> [slug]` | 팀 worktree + 브랜치 + `.env.worktree` 포트 override 생성 | 낮음 — 기존 worktree 가 있으면 exit 3 으로 중단 |
| `teardown_worktree.sh <N>` | 팀 worktree 안전 제거 (uncommitted 변경 검사 후) | 낮음 — 변경이 있으면 중단 |
| `mailbox_new.sh <from> <to> <slug>` | `agent_docs/mailboxes/<to>/inbox/` 에 메시지 생성 | 없음 — 신규 파일만 생성 |
| `wiring_smoke.sh [path]` | `.claude/settings.json` 구조 검증 + governance.md §2.5 리드 전용 가드 존재 확인 | 없음 — 읽기 전용 검사 |
| `pre_bash_guard.sh` | PreToolUse hook — force push / `git reset --hard` / `rm -rf` 차단 | 없음 — Claude 하네스가 stdin JSON 으로 호출 |

## 팀 4 lockout

`bootstrap_worktree.sh 4` / `teardown_worktree.sh 4` 는 **ADR-002 Stage 2 Pilot 관찰 기간 (2026-04-22 ~ 2026-05-06)** 동안 명시적으로 차단됩니다. 기존 `aqts-team4-skills-pilot` worktree 는 그대로 유지되어야 하며, 재생성이 필요하면 리드가 수동으로 `git worktree add` 를 실행합니다.

## 포트 override 전략

`bootstrap_worktree.sh` 는 worktree 별 `.env.worktree` 파일을 생성합니다. docker-compose.yml 은 `${DB_PORT:-5432}` 형태로 기본값을 두므로, 실행 전 `.env.worktree` 를 source 하면 충돌 없이 동시에 3개 worktree 에서 docker compose up 이 가능합니다.

```bash
cd /path/to/aqts-team1-strategy
export $(grep -v '^#' .env.worktree | xargs)
docker compose up -d
```

| 팀 | DB_PORT | MONGO_PORT | REDIS_PORT | BACKEND_PORT |
|---|---|---|---|---|
| 1 | 5442 | 27027 | 6389 | 8010 |
| 2 | 5452 | 27037 | 6399 | 8020 |
| 3 | 5462 | 27047 | 6409 | 8030 |

`.env.worktree` 는 tracked 가 아닙니다 — `.env.example` 이 리드 전용 (`governance.md §2.5`) 이므로 이 파일 수정 없이 worktree 별 override 를 분리합니다.

## Wiring Rule 집행

`wiring_smoke.sh` 는 정적 검사 (structural) 만 수행합니다. 실제 permission deny 가 하네스 런타임에서 발화하는지는 **세션 내 수동 검증** 이 필요:

```bash
cd /path/to/aqts-team1-strategy
claude       # 세션 시작
# Claude 에게: "Write ./CLAUDE.md" 시도 요청
# 예상: harness deny 로그 출력 + write 차단
```

CI 에서 `wiring_smoke.sh` 를 0 errors 로 강제하여 structural 드리프트를 방어합니다 (향후 Phase 5 에서 `.github/workflows/ci.yml` 의 lint 잡에 추가 예정 — OPS-023 §4 참조).

## 관련 문서

- `docs/operations/claude-multisession-migration-2026-04-22.md` (OPS-023) — 전체 migration 맥락
- `agent_docs/governance.md §2.5` — 리드 전용 파일 목록 (스크립트가 차단하는 대상)
- `agent_docs/development-policies.md §5` — Wiring Rule ("정의했다 ≠ 적용했다")
- `CLAUDE.md §5` — 과거 회귀 사례 (scheduler stdout silent miss, SSH heredoc stdin 등)
