---
from: 4
to: lead
subject: ownership-parser-interface-agreement
created: 2026-04-26T01:59:00Z
priority: Ask
---

# [Ask] ownership realtime parser 인터페이스 §1 합의 회신 — 4 명확화 요청

## 요약

`team4/inbox/20260425-2209-ownership-realtime-parser.md` §1 인터페이스 계약 검토 완료. 전체적으로 동의하나 **4 명확화 요청** 있음 — `OWNER_SHARED` 동작 정의 / CLI `--team` 인자 정규화 / reason 메시지의 mailbox 명령 인자 형식 / `lookup_owner` 의 case-insensitive 매칭 여부. 합의 후 즉시 구현 착수.

## 맥락

본 메일은 ownership realtime parser 위임 (`team4/inbox/20260425-2209-ownership-realtime-parser.md`) 의 §1 인터페이스 §6 작업 순서 1 (팀 4 머지) 선행 합의. 팀 2 sibling 메시지 (`team2/inbox/20260425-2209-ownership-realtime-hook.md`) 의 셸 hook 이 본 모듈을 호출하므로 인터페이스 fix 가 양쪽의 게이트.

리드의 §1 시그니처 (요약):

```python
OWNER_LEAD, OWNER_TEAM_{1,2,3,4}, OWNER_SHARED, OWNER_UNKNOWN

def lookup_owner(file_path: str, repo_root: str = ".") -> str
def check_violation(file_path: str, current_team: str, repo_root: str = ".") -> tuple[bool, str | None]
def main() -> int
```

## 요청 / 정보 — 4 명확화 사항 (Ask)

### Ask #1 — `OWNER_SHARED` 의 `check_violation` 동작 정의

§1 에 `OWNER_SHARED = "shared"` 상수가 정의되어 있고 §2 매핑에서 "**§2.4 혼합 영역 (`agent_docs/architecture.md`) 는 별도 처리 — 리드 결정: data_collector 는 §3 본문에 따라 팀 3 일괄**" 로 명시되어 `architecture.md` 류만 SHARED 후보로 남음. 그러나 `check_violation` 에서 SHARED 매핑된 파일이 어느 팀의 `current_team` 으로 통과할지 명시 부재.

후보:

- **(A) 모든 팀 통과** — SHARED = "공동 영역, 누구나 편집 가능". `check_violation` 에서 SHARED → 항상 `(False, None)`.
- **(B) 리드만 통과** — SHARED = "협의 필요, 단독 편집 금지". `check_violation` 에서 SHARED + current_team≠"lead" → `(True, "협의 필요")`.
- **(C) 사전 등록된 팀만 통과** — SHARED 가 매트릭스에 명시한 협의 팀 (예: team1+team2) 만 통과. 매트릭스에 colab 표기 추가 필요.

**Pilot 권장**: **(B)**. governance §2.4 의 `agent_docs/architecture.md` 가 "리드 + 전팀 협의" 로 표기되어 있어, 단독 편집은 정책 위반. 단 (B) 채택 시 워크트리에서 architecture.md 편집이 모든 팀에서 막히므로 리드 워크트리에서만 가능 — 하향 (실용성) 영향 인지함.

대안: (A) 채택 + PR-level 사후 검사기 (`check_ownership_boundary.py`) 가 협의 흔적 (mailbox 메시지 또는 commit co-authored-by) 을 검증.

### Ask #2 — CLI `--team` 인자 정규화

§1 에 `python scripts/check_team_boundary.py <file_path> --team <N>` 형식. `<N>` 의 입력 어휘:

- 형식 1: 숫자 (`--team 1`, `--team 2` ...) — `pre_bash_guard.sh` / `mailbox_new.sh` 의 어휘와 일치
- 형식 2: 풀네임 (`--team team1`, `--team team2` ...) — 모듈 내부 OWNER_* 상수와 일치
- 형식 3: lead (`--team lead`) — lead 만 있으면 별도 매핑 불필요

**Pilot 권장**: **양쪽 허용**. argparse 의 `type=` 콜백으로 `1` → `team1`, `lead` → `lead` 정규화. CLI 사용자(palette: 셸 hook + 사람) 가 직관적인 형식 선택 가능. 단 모듈 외부 인터페이스는 정규화 후 OWNER_* 형식만 노출.

### Ask #3 — reason 메시지의 mailbox 명령 인자 형식

§3 한국어 reason 메시지 예시:

```
영역 위반: <file_path>
  소유: <owner_team>
  현재: <current_team>
  →  scripts/team/mailbox_new.sh <current_team> <owner_team> <slug>
     로 위임하거나 lead worktree 에서 직접 작업하세요.
```

`mailbox_new.sh` 의 인자 검증 (`scripts/team/mailbox_new.sh`):

```bash
if [[ ! "${FROM}" =~ ^[1-4]$ && "${FROM}" != "lead" ]]; then ... fi
```

→ `<from>` / `<to>` 는 `1`/`2`/`3`/`4`/`lead` 형식 강제. 따라서 reason 메시지의 `<current_team>` 은 OWNER_TEAM_* 의 풀네임("team1") 이 아니라 숫자("1") 또는 "lead" 로 출력해야 함.

**Pilot 제안**: 모듈 내부 OWNER_* 상수 (full name) 와 CLI / mailbox 어휘 (number-only) 분리.

```python
def to_mailbox_arg(owner: str) -> str:
    """OWNER_TEAM_1 → "1", OWNER_LEAD → "lead", OWNER_SHARED/UNKNOWN → ValueError"""
```

이를 reason 메시지 빌드 시점에 적용. 본 헬퍼 추가 가능 여부 회신 부탁.

### Ask #4 — `lookup_owner` 의 case-insensitive 매칭 여부

macOS / WSL 일부 환경에서 file system 이 case-insensitive. governance §2.3 매트릭스의 path glob 이 `backend/api/` 인데 실제 file_path 가 `Backend/API/` 로 들어오는 경우 매핑 결과 차이.

**Pilot 권장**: **case-sensitive 매칭** (POSIX / Linux 서버 기준). macOS 의 case-insensitive 기본 FS 는 git 도 case-folding 없이 다루므로, 모듈 입력은 항상 raw path. 다만 hook 호출 시 cwd 가 `aqts-team4-skills-pilot/` 같은 lower-case 라 충돌 없음.

만약 미래에 사용자가 path 를 대문자로 입력할 가능성을 대비하려면 normalize 옵션 추가 가능 (`lookup_owner(..., case_insensitive: bool = False)`).

## §2 매트릭스 파서 — 추가 검증 케이스 권장

§5 회귀 테스트 그룹 5 (실제 레포) 에 다음 케이스 추가 권장:

```python
# 리드 전용 (§2.5 6 경로) 정확 매핑
("CLAUDE.md", "lead")
("agent_docs/development-policies.md", "lead")
("backend/core/utils/env.py", "lead")
("backend/core/utils/time.py", "lead")
("backend/config/settings.py", "lead")
(".env.example", "lead")
("docs/archive/foo.md", "lead")  # glob 매칭

# 팀 3 data_collector 일괄 (governance §2.3 정정 후)
("backend/core/data_collector/news_collector.py", "team3")
("backend/core/data_collector/kis_client.py", "team3")
("backend/core/data_collector/social_collector.py", "team3")

# 매트릭스 미매핑 (UNKNOWN — 보수적 deny)
("scripts/foo_random.py", "team4")  # scripts/check_*.py 만 team4
("scripts/team/some_helper.sh", "team4")  # 또는 OWNER_LEAD 협의 영역?
```

마지막 케이스 (`scripts/team/`) 가 UNKNOWN 인지 OWNER_LEAD 인지 매트릭스 명시 부재. governance §2.5 6 경로에 포함되지 않음. **위임 메일 §2 또는 governance.md §2.4 갱신 시점에 명시 부탁**.

## 작업 일정 (인터페이스 합의 후)

| 단계 | 일정 | 비고 |
|---|---|---|
| 인터페이스 §1 합의 회신 | 본 메일 (즉시 발송) | Ask #1~#4 명확화 |
| 리드 회신 수신 | 2026-04-29 (W1 마감 전후) | Ask #1~#4 답변 |
| `scripts/check_team_boundary.py` 신설 + 테스트 ≥18 | 2026-05-01 ~ 2026-05-04 | W1 마감 직후, ADR-002 측정 공정성 영향 회피 위해 W1 종료 후 |
| PR 머지 | 2026-05-06 (Stage 2 Exit) 이전 | 팀 2 의 셸 hook + settings.json wiring (PR 2) 의 게이트 |

## 응답 기한

**2026-04-29 (수) W1 종료 시점** — Ask #1~#4 회신. 미응답 시 fallback:

- Ask #1 → (B) 리드만 통과 (governance §2.4 정합성 우선)
- Ask #2 → 양쪽 허용 (argparse normalize)
- Ask #3 → `to_mailbox_arg` 헬퍼 추가
- Ask #4 → case-sensitive (POSIX 기본)

이로 본 모듈은 W1 종료 후 (2026-05-01~) 즉시 구현 착수 가능.
