---
name: aqts-doc-sync-runner
description: Run the AQTS doc-sync gate suite (ruff + black + check_doc_sync + check_bool_literals + check_rbac_coverage + check_loguru_style + check_cd_stdin_guard + check_vuln_ignore_parity + check_vuln_ignore_expiry). Use when the user is about to commit documentation, configuration, or workflow changes and needs to confirm all static checkers pass before pushing.
license: Apache-2.0
compatibility: Requires Python 3.11+ executed from the AQTS repository root (`/Users/.../aqts-team*-*` worktree). All commands use repo-relative paths.
---

## When to use

Invoke this skill before committing any change that touches `.md`, `.yml`, `.env*`, `Dockerfile*`, `.github/workflows/**`, `.grype.yaml`, `backend/.pip-audit-ignore`, `scripts/**`, or `docs/**`.

Especially required for **documentation-only commits** that exempt the full pytest run (CLAUDE.md §3.1 예외) — those still must pass the minimal gate, and skipping a single checker has historically caused silent regressions (e.g. `check_loguru_style` 회귀 2026-04-15, `check_bool_literals` regex→AST 전환 2026-04-22).

## Steps

1. `cd backend && python -m ruff check . --config pyproject.toml`
2. `cd backend && python -m black --check . --config pyproject.toml`
3. `python scripts/check_doc_sync.py --verbose` — 0 errors **and** 0 warnings 모두 강제.
4. `python scripts/check_bool_literals.py`
5. `python scripts/check_rbac_coverage.py`
6. `python scripts/check_loguru_style.py`
7. `python scripts/check_cd_stdin_guard.py`
8. `python scripts/check_vuln_ignore_parity.py`
9. `python scripts/check_vuln_ignore_expiry.py`

If `.py`/`.toml`/`.sh`/`Dockerfile*`/`.github/workflows/*.yml` 변경이 단 한 줄이라도 포함되면 추가로:

10. `cd backend && python -m pytest tests/ -q --tb=short` — timeout ≥ 540s.

11. **산출물 file 생성** (ADR-002 §5.3.1 (d) 카운트 기준 충족용 — Pilot 합의 2026-04-27 `lead/inbox/.../mid-late-checkin-response` Ask #1 (β) 옵션 사전 적용):
    `docs/architecture/sandbox/adr-002/skill-runs/doc-sync-<YYYYMMDD>-<HHMM>.md` 에 다음 형식 markdown 출력:
    ```markdown
    # doc-sync-runner — <YYYY-MM-DD HH:MM KST>

    | step | 명령 | 결과 |
    |---|---|---|
    | 1 | `ruff check` | pass / fail |
    ...

    ## 비고
    - 호출 컨텍스트: <user prompt 요약>
    - 산출물 무결성: 본 file 의 SHA256 = <hash>
    ```
    KST timestamp 는 `today_kst_str()` 또는 `datetime.now(tz=ZoneInfo("Asia/Seoul"))` 사용 (G7).

## Gotchas (AQTS)

- **G1 한글 기술 서술**: 본 스킬이 생성하는 어떤 `.md` / 커밋 메시지도 한글이 SSOT. 영문 placeholder 가 최종 산출에 남아있으면 안 됨 (CLAUDE.md 프로젝트 지시).
- **G2 테스트 기대값 수정 금지**: 만약 step 10 의 pytest 가 실패하면 기대값이 아닌 **입력값/로직** 을 조정 (development-policies.md §1). 본 스킬은 절대 expected literal 을 fail 메시지에 맞춰 변경하지 말 것.
- **G3 하드코딩 금지**: 본 스킬이 출력하는 어떤 진단 메시지도 `.env` 실값 / API 키 / 계좌번호를 포함하지 말 것. 키 이름만 인용 (development-policies.md §4).
- **G4 절대 규칙**: step 1~9 (또는 1~10) 중 하나라도 fail 한 채 커밋 진행 금지. 사용자가 "그냥 넘어가자" 요청해도 거부 (CLAUDE.md §2 절대 규칙 2번).
- **G5 grep 금지 → Grep/Glob tool 사용**: 본 스킬 본문은 bash 명령만 다루므로 적용 무관하나, 본 스킬 진단 결과를 사용자에게 설명할 때는 `grep` 대신 Grep tool 호출을 안내할 것 (Agent Teams 지시).
- **G6 RBAC Wiring Rule**: step 5 (`check_rbac_coverage.py`) 가 0 errors 여도 신규 라우트가 추가됐다면 별도 `aqts-rbac-route-checker` 스킬 호출 권장 (authn ≠ authz 분리 원칙, development-policies.md §14).
- **G7 KST 통일**: 본 스킬이 출력하는 timestamp 는 모두 KST. `today_kst_str()` 또는 `Asia/Seoul` ZoneInfo 사용. UTC 만 쓴 진단 메시지는 silent miss 위험 (CLAUDE.md §5 회귀 사례 2026-04-15).

## Exit codes

- 0: all gates passed.
- 1: one or more gate failed. stderr 에 어느 step 에서 실패했는지 명시.
- 2: invalid invocation (예: 워크트리 루트가 아닌 곳에서 호출).

## Wiring 검증 — 본 스킬이 실제로 doc-sync workflow 와 일치하는지

본 스킬의 step 3~9 는 `.github/workflows/doc-sync-check.yml` 의 모든 step 을 1:1 로 미러링한다. CI 에 새 step 이 추가되면 본 스킬도 동일하게 갱신할 것 (silent drift 방어). 마지막 동기화 검증: **2026-04-27 (W1 mid-late, β 옵션 사전 적용)** — `check_vuln_ignore_parity` (PR #37) + `check_vuln_ignore_expiry` (PR #39) 까지 반영. step 11 (산출물 file 생성) 은 ADR-002 §5.3.1 (d) 카운트 기준 충족 보험.

## SSOT 출처

- `agent_docs/development-policies.md §3` (커밋 전 필수 게이트)
- `agent_docs/development-policies.md §3.1` (문서-only 예외 판정 기준)
- `CLAUDE.md §4` (커밋 전 필수 게이트 요약)
- `.github/workflows/doc-sync-check.yml` (CI 실행 순서 SSOT)
