# Claude Cowork → Multi-Session Migration — 2026-04-22

> **문서 번호**: OPS-023
>
> **목적**: AQTS 프로젝트를 Claude Cowork 의 experimental Agent Teams 플래그 기반 운영에서 **4개 독립 Claude Code 세션 + worktree 격리** 방식으로 전환한 작업 기록. governance.md §1 의 harness 표 갱신, `.claude/settings.json` 신설, 자동화 스크립트 계획을 포함한다.
>
> **관련 계획 파일**: `/Users/ahnsucheol/.claude/plans/cluade-cowork-bright-raccoon.md` (리드 로컬, 미트래킹)

---

## 1. 배경

AQTS 프로젝트는 Phase 1 Agent Teams 마이그레이션 (커밋 `b656186`, 2026-04-21) 에서 `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1` + `Shift+Down` 팀메이트 순환 방식의 **단일 세션 내 4팀 운영** 을 전제로 governance / development-policies / rules 문서를 정비했다. 실제 운영 흐름을 돌려본 결과 리드는 다음 이유로 **4개 독립 `claude` CLI 세션** 방식을 선호함을 명확히 했다.

1. 팀 간 토큰/컨텍스트 격리가 명확해짐 — 한 세션이 과부화돼도 타 팀 무영향.
2. git worktree 와 1:1 매핑이 단순 — 세션-worktree 대응이 혼동 없음.
3. 터미널 다중화 (iTerm split, tmux) 로 시각적 병렬성 확보.
4. `Shift+Down` 단축키 학습 곡선 제거, 툴링 기대치 표준 CLI 수준.

본 migration 은 이 전환을 실제 운영 가능한 수준까지 (worktree 확보 + 설정 베이스라인 + 자동화) 4 Phase 로 분할하여 진행한다.

---

## 2. Phase 1 — 팀 1/2/3 worktree 생성 (2026-04-22, 완료)

### 2.1 산출물

```
$ git worktree list
/Users/ahnsucheol/Desktop/aqts                                      f32b177 [main]
/Users/ahnsucheol/Desktop/aqts-team1-strategy                       f32b177 [team1/init-worktree]
/Users/ahnsucheol/Desktop/aqts-team2-scheduler                      f32b177 [team2/init-worktree]
/Users/ahnsucheol/Desktop/aqts-team3-api                            f32b177 [team3/init-worktree]
/Users/ahnsucheol/Desktop/aqts-team4-skills-pilot                   f32b177 [pilot/team4-skills-w1]
/Users/ahnsucheol/Desktop/aqts/.claude/worktrees/nice-moore-3a5153  f32b177 [chore/multi-session-migration]
```

### 2.2 네이밍 근거

- 최상위 sibling 레이아웃 (`../aqts-team{N}-<role>`) 채택. 기존 팀 4 Pilot (`aqts-team4-skills-pilot`) 과 동일 패턴.
- `.claude/worktrees/` 하위 배치는 제외. 근거: recursive grep / ruff 가 타 worktree 를 스캔할 위험 + 프로젝트 내부라 파일 트리 출력 시 혼동.
- `.claude/worktrees/` 는 현행 "일회성 Claude 세션 전용" 역할 유지 (본 세션도 이 패턴).

### 2.3 브랜치 prefix 정책

`team{N}/<type>/<slug>` 로 고정.

CI (`.github/workflows/ci.yml:7-10`) 는 `push: [main, develop]` + `pull_request: [main, develop]` 에만 반응한다. 팀 브랜치 push 자체는 CI 무트리거 → runner 충돌 없음. PR 생성 시점에만 CI 1회 실행. CD 는 main push 에만 트리거 → 팀 브랜치 CD 무영향.

### 2.4 실행 로그

```
$ git branch -a | grep -E 'team[1-3]/init-worktree'
OK: no collision

$ git worktree add ../aqts-team1-strategy -b team1/init-worktree main
작업 트리 준비 중 (새 브랜치 'team1/init-worktree')
HEAD의 현재 위치는 f32b177입니다

$ git worktree add ../aqts-team2-scheduler -b team2/init-worktree main
작업 트리 준비 중 (새 브랜치 'team2/init-worktree')
...

$ git worktree add ../aqts-team3-api -b team3/init-worktree main
...
```

---

## 3. Phase 2 — `.claude/settings.json` + `.gitignore` (2026-04-22, 본 커밋)

### 3.1 신규 파일

**`.claude/settings.json`** (tracked, 4 worktree 공통 계약):

- `permissions.allow`: ruff/black/pytest, static checkers, git 조회, docker 조회, gh 조회
- `permissions.deny`:
  - Bash: `git push --force*`, `git reset --hard*`, `rm -rf /*`/`~*`/`**
  - Write/Edit: governance.md §2.5 리드 전용 파일 7개 (CLAUDE.md, agent_docs/development-policies.md, backend/config/settings.py, backend/core/utils/{env,time}.py, .env.example, docs/archive/**)
- `env.PYTHONUNBUFFERED`: `"1"` — scheduler stdout silent miss (CLAUDE.md §5, 2026-04-15) 재발 방지 최소 보험
- `hooks`: `{}` (Phase 3 에서 `scripts/team/*.sh` 와 동시 주입)

**JSON 스키마**: `https://json.schemastore.org/claude-code-settings.json` 선언하여 IDE intellisense 연결.

### 3.2 `.gitignore` 수정

```diff
 CLAUDE.md.bak
+
+# ── Claude Code local overrides (per-user, per-worktree) ──
+.claude/settings.local.json
+.claude/worktrees/*/.claude/settings.local.json
```

`.claude/settings.local.json` 은 이미 `~/.config/git/ignore:1` 에서 **글로벌** gitignore 로 잡혀 있어 리드 로컬에서는 중복이지만, 프로젝트 portability (다른 개발자·CI) 를 위해 프로젝트 `.gitignore` 에도 명시적 기록.

### 3.3 ADR-002 Pilot 격리 방침

`aqts-team4-skills-pilot` worktree 의 `.claude/settings.local.json` 에 `"permissions": {"allow": ["*"]}` 형식 로컬 override 를 권장 — Stage 2 Sandbox 관찰 (2026-04-22 ~ 2026-05-06) 기간 동안 permission/hook 발화가 skill 트리거 관찰 데이터를 오염시키는 것을 방지. 본 커밋은 Pilot worktree 를 수정하지 않음.

### 3.4 커밋 전 게이트 (문서-only 예외 적용)

Python 파일 변경 0건 → development-policies.md §3.1 의 문서-only 예외 발동 → 전체 `pytest tests/` 생략. 최소 게이트만 실행.

| 게이트 | 결과 |
|---|---|
| `python -m ruff check . --config pyproject.toml` | ✓ All checks passed |
| `python scripts/check_bool_literals.py` | ✓ BOOL LITERAL CHECK PASSED |
| `python scripts/check_doc_sync.py` | ✓ SYNC CHECK PASSED (0 errors / 0 warnings) |
| `python -m black --check . --config pyproject.toml` | ⚠ 26 파일 drift (CLAUDE.md §9 기완료, 머지 대기 중 — 본 커밋 무관) |

black drift 26 파일은 `chore/black-format-drift` 브랜치에서 이미 해소되었으나 (`docs/phase1-agent-teams-migration` 경로 머지 대기) main 에 아직 미반영 상태. 본 커밋은 `.py` 파일을 단 한 줄도 수정하지 않으므로 drift 악화 없음.

### 3.5 Wiring Rule (development-policies.md §5) 준수

본 커밋은 "정의" 단계. "적용" 은 Phase 3 의 `scripts/team/wiring_smoke.sh` 가 실제 `Write(./CLAUDE.md)` 시도를 하여 deny 가 발화하는지 로그로 확인한다. Phase 2 단독으로는 "정의했다 ≠ 적용했다" 상태이므로 `hooks` 블록은 의도적으로 빈 `{}` 로 남김.

---

## 4. 후속 Phase (계획)

| Phase | 산출물 | 예상 시점 |
|---|---|---|
| 3 | `scripts/team/{bootstrap,teardown}_worktree.sh`, `mailbox_new.sh`, `wiring_smoke.sh`, `pre_bash_guard.sh` | 2026-04-23 ~ 2026-04-27 |
| 4 | `.mcp.json` (GitHub MCP, GCP MCP opt-in) | 2026-04-29 이후 |

Phase 3 의 `wiring_smoke.sh` 가 본 Phase 2 의 `settings.json` deny 가 실제 발화하는지 통합 smoke 로 검증 → **Wiring Rule 집행 루프** 가 마련된 뒤에만 Phase 3 를 완료 처리.

---

## 5. 리드 후속 필요 작업 (본 커밋 범위 밖)

1. **`agent_docs/governance.md §1` harness 표 갱신**: 현재 "`claude` CLI + Agent Teams (`CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`), `Shift+Down` 으로 팀메이트 순환" 문구를 "4개 독립 `claude` CLI 세션 (worktree 격리)" 으로 교체. governance.md 는 §2.5 리드 전용 파일 목록에 명시되어 있지는 않으나 §1 은 근본적 운영 모델 정의부이므로 리드 승인 후 수정 권장.
2. **`CLAUDE.md §9 미해결 TODO` 갱신**: 본 migration 의 Phase 1/2/3/4 체크박스 4개 추가. CLAUDE.md 는 §2.5 리드 전용 파일이므로 팀메이트 세션에서는 `settings.json` 의 deny 로 물리 차단.
3. **팀 4 Pilot worktree `.claude/settings.local.json` 생성**: ADR-002 Stage 2 관찰 무오염을 위한 local override — 리드가 `aqts-team4-skills-pilot` 에서 직접 실행.

---

## 6. 회귀 방어선 회고

본 migration 에서 발굴된 잠재적 silent miss 후보:

1. **`docs/operations/` 소유권 이중 기재**: `agent_docs/governance.md §2.4` 는 팀 4 소유 (`docs/operations/*.md` 아카이브·런북) 로, `.claude/rules/docs.md` 의 파일별 소유권 표는 팀 2 소유 (운영 런북) 로 명시되어 있어 **소유권 충돌**. 본 커밋은 migration 코디네이터 역할로 작성되었으나 향후 순수 운영 런북 PR 시 소유권 판단에 혼선. → 리드 후속 — governance.md 또는 `.claude/rules/docs.md` 중 하나를 SSOT 로 확정.
2. **permission matcher 패턴 실효성 미검증**: `Write(./CLAUDE.md)`, `Write(CLAUDE.md)`, `Edit(./CLAUDE.md)`, `Edit(CLAUDE.md)` 중 실제 Claude Code 하네스가 어떤 패턴을 매칭하는지는 Phase 3 의 `wiring_smoke.sh` 실행 전까지 미검증. Phase 3 에서 1개라도 미발화 시 settings.json 재조정.
3. **ADR-002 Pilot 격리 실제 작동 여부**: 본 커밋은 Pilot worktree 에 파일 변경을 가하지 않았으나, main 으로 머지 후 Pilot worktree 가 `git pull` 을 하면 `.claude/settings.json` 이 들어온다. Pilot 리드가 직후 `settings.local.json` 으로 완전 override 해야 Stage 2 관찰 무오염. 리드 주의 필요.

---

## 7. 참고

- 계획 원본: `/Users/ahnsucheol/.claude/plans/cluade-cowork-bright-raccoon.md`
- Phase 1 Path A 배경: `agent_docs/governance.md §2.3`, `CLAUDE.md §9` (data_collector 팀 3 일괄 배정, 2026-04-22)
- Phase 2 ADR-002 진행: `docs/architecture/adr-002-*.md` (anthropic-skills Stage 2 관찰 중, Exit 2026-05-06)
- 관련 정책: `agent_docs/development-policies.md §3.1` (문서-only 예외), §5 (Wiring Rule), §8 (Silence Error)
