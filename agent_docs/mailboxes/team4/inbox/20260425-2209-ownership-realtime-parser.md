---
from: lead
to: 4
subject: ownership-realtime-parser
created: 2026-04-25T13:09:14Z
priority: Ask  # [P0] 긴급, [Ask] 응답 요청, [FYI] 참고, [Lead-Approval] 리드 승인
---

# ownership-realtime-parser

## 요약

팀 vs 팀 영역 침범을 **실시간(편집 직전) 차단**하기 위한 신규 PreToolUse Edit/Write hook 의 **핵심 파서 모듈** 구현을 팀메이트 4 영역으로 위임합니다. 본 메시지는 팀 4 (파서) + 팀 2 (셸 hook + settings.json wiring) 공동 작업의 팀 4 분담분이며, 팀 2 의 sibling 메시지는 `agent_docs/mailboxes/team2/inbox/20260425-2209-ownership-realtime-hook.md` 입니다.

## 맥락

### 현재 보강 상태

1. **PR 레벨 사후 검사기** — `scripts/check_ownership_boundary.py` + `backend/tests/test_check_ownership_boundary.py` (이미 머지됨, PR #48 추정)
2. **Bash PreToolUse hook** — `scripts/team/pre_bash_guard.sh` (force-push / hard-reset / rm-rf 류 차단)
3. **리드 영역 정적 deny** — `.claude/settings.json` permissions.deny 6 경로 (`CLAUDE.md`, `agent_docs/development-policies.md`, `backend/core/utils/{env,time}.py`, `backend/config/settings.py`, `.env.example`, `docs/archive/**`)

### 격차

- **팀 vs 팀 실시간 차단 부재** — 팀 1 worktree (`aqts-team1-strategy`) 에서 `backend/api/**` (팀 3 영역) 를 편집해도 PreToolUse Edit/Write hook 이 없어 통과. 위반은 PR 단계의 사후 검사기에서만 잡혀, 30분 작업 후 차단되는 시나리오 발생.
- 사용자(리드) 가 본 격차를 직접 지적: "5개 터미널 (리드 + 팀 4) 운용 중 영역 미준수 인지".

### 아키텍처 결정 (리드)

PreToolUse Edit/Write hook 신설. 동작:

1. 현재 worktree 경로 (`pwd` 또는 hook 의 cwd) 에서 팀 ID 추출 (`aqts-team{N}-*` 패턴, lead worktree 는 통과)
2. tool input 의 `file_path` 를 governance §2.3 매트릭스로 매핑하여 owner team 산출
3. owner ≠ 현재 팀 → deny + 메일박스 명령 안내 메시지

본 분담은 **2번 매핑 로직** — Python 모듈 + governance §2.3 markdown 표 파서 + 단위 테스트.

## 요청

`scripts/check_team_boundary.py` 신설 (위치는 governance §2.4 의 "정적 검사기 = 팀 4 영역" 에 따라 root `scripts/`. 다만 본 모듈은 *호출되는 라이브러리* 성격이므로 파일명에 `check_` prefix 는 어색 — 대안 `scripts/team_boundary_lookup.py` 도 고려. 리드 권장: **`scripts/check_team_boundary.py`** — 기존 `scripts/check_*.py` 패턴 유지로 일관성 우선, `--check` 모드(전수 검사) + import 라이브러리 모드 둘 다 지원).

### 1. 인터페이스 계약 (팀 2 와 합의 고정 필요)

```python
# scripts/check_team_boundary.py

OWNER_LEAD = "lead"           # 리드 전용 (governance §2.5)
OWNER_TEAM_1 = "team1"
OWNER_TEAM_2 = "team2"
OWNER_TEAM_3 = "team3"
OWNER_TEAM_4 = "team4"
OWNER_SHARED = "shared"       # 명시적 공동 영역 (예: agent_docs/architecture.md)
OWNER_UNKNOWN = "unknown"     # 매트릭스에 매핑 안 됨 — 보수적 deny 또는 warning

def lookup_owner(file_path: str, repo_root: str = ".") -> str:
    """governance §2.3 매트릭스를 읽어 file_path 의 owner team 을 반환.
    - file_path 는 repo_root 기준 상대 또는 절대 경로 둘 다 허용
    - 매핑 안 되면 OWNER_UNKNOWN
    - 한 파일이 여러 팀 매핑을 만족할 수 있는 경우 (가장 깊은 prefix 우선)
    """

def check_violation(
    file_path: str,
    current_team: str,
    repo_root: str = ".",
) -> tuple[bool, str | None]:
    """편집이 영역 위반인지 판정.
    Returns: (is_violation, reason_or_None)
      - is_violation=True → reason 에 한국어 사유 + 메일박스 명령 안내 포함
      - is_violation=False → reason=None
    - current_team="lead" 는 항상 통과 (False 반환)
    - current_team=OWNER_UNKNOWN 은 보수적 deny (True)
    """

def main() -> int:
    """CLI: `python scripts/check_team_boundary.py <file_path> --team <N>`
    - exit 0: 통과
    - exit 1: 위반 (stderr 에 reason)
    - exit 2: 사용법 오류 / 매트릭스 파싱 실패
    """
```

### 2. governance §2.3 매트릭스 파서

`agent_docs/governance.md` 의 §2.3 를 SSOT 로 함. 파서 요구사항:

- markdown 표 (`| # | 팀메이트 | 주요 영역 |` 헤더 + 4 row) 추출
- "주요 영역" 셀의 backtick-quoted 경로 (`backend/core/{strategy_ensemble, ...}`, `backend/api/`, …) 를 glob 패턴 리스트로 변환
- brace expansion 지원 (`{strategy_ensemble, backtest_engine, ...}` → 개별 prefix 펼치기)
- 와일드카드 지원 (`backend/core/scheduler*` → glob `backend/core/scheduler*` + 디렉토리 prefix)
- §2.5 리드 전용 6 경로는 `OWNER_LEAD` 로 별도 등록
- §2.4 매트릭스 (혼합 영역 — `architecture.md`, `data_collector/{kis_*, news_*, ...}`) 는 별도 처리 — 리드 결정: data_collector 는 §3 본문에 따라 팀 3 일괄 (이미 OPS 작업으로 정정됨)

### 3. 한국어 reason 메시지 포맷

```
영역 위반: <file_path>
  소유: <owner_team>
  현재: <current_team>
  →  scripts/team/mailbox_new.sh <current_team> <owner_team> <slug>
     로 위임하거나 lead worktree 에서 직접 작업하세요.
  governance §2.3 SSOT 참조.
```

### 4. 회귀 테스트 하니스 (6 그룹 패턴 — OPS-022/026 재사용)

`backend/tests/test_check_team_boundary.py`:

1. **유효 통과** — 각 팀이 자기 영역 편집 시 통과 (4 케이스)
2. **위반 검출** — 각 팀이 타 팀 영역 편집 시 deny (12 조합 중 대표 4-6 케이스)
3. **오탐 방지** — `governance.md` / `CLAUDE.md` 자체 편집은 lead 만 통과, brace expansion 의 부분 매칭 false positive 방어
4. **파서 견고성** — §2.3 표 외 마크다운 (코드 블록, 다른 표) 이 파서를 오인하지 않음, "주요 영역" 셀의 backtick / 콤마 / 공백 변형 처리
5. **실제 레포 회귀** — 현재 main 의 governance §2.3 로 구동 → 알려진 파일 (`backend/api/v1/orders.py` → team3, `backend/scheduler_main.py` → team2, `backend/tests/foo.py` → team4 등) 매핑 확인. 또한 §2.4 혼합 영역 (`agent_docs/architecture.md` → SHARED) 매핑 확인.
6. **`main()` 진입** — argparse / exit code / `current_team` 누락 시 사용법 출력

목표 ≥ 18 tests.

### 5. 게이트

- ruff + black PASS
- `pytest backend/tests/test_check_team_boundary.py` ≥ 18 tests PASS
- self-test: `python scripts/check_team_boundary.py backend/api/v1/orders.py --team team1` → exit 1, reason 에 team3 명시
- self-test: `python scripts/check_team_boundary.py backend/scheduler_main.py --team team2` → exit 0
- self-test: `python scripts/check_team_boundary.py CLAUDE.md --team team1` → exit 1, owner=lead

### 6. 작업 순서 (팀 2 와 협업)

1. **팀 4 (본 메시지)** — 파서 + 테스트 우선 머지 (PR 1)
2. **팀 2 (sibling 메시지)** — 머지된 파서를 호출하는 `scripts/team/pre_edit_guard.sh` + `.claude/settings.json` PreToolUse 등록 + bypass 메커니즘 (PR 2)
3. PR 1 머지 전 인터페이스 (위 §1) 가 팀 2 와 합의되어야 함 — 변경 시 양쪽 메일박스에 알림

## 응답 기한

**합의 응답**: 2026-04-29 (W1 마감 전후) — 인터페이스 §1 에 동의/수정안 회신. 팀 2 와 평행 진행하므로 인터페이스 fix 가 양쪽 작업의 게이트.

**구현 머지**: 우선순위 P2. ADR-002 Stage 2 종료 (2026-05-06) 이전이면 좋음. 단, ADR-002 측정 공정성에 영향 가능 (팀 4 = Stage 2 Pilot) 이므로 W1 측정에 반영 안 되도록 W2 진입 시점 (2026-04-29~) 머지 권장.

## 참조

- `agent_docs/governance.md §2.3, §2.4, §2.5` (SSOT)
- `scripts/check_ownership_boundary.py` (사후 검사기 — 본 작업의 사전 차단 대응짝)
- `scripts/team/pre_bash_guard.sh` (Bash hook 원형 — 셸 스크립트는 팀 2 분담)
- `scripts/check_vuln_ignore_parity.py`, `check_vuln_ignore_expiry.py` (markdown / config 파서 패턴 원형)
- 팀 2 sibling: `agent_docs/mailboxes/team2/inbox/20260425-2209-ownership-realtime-hook.md`
