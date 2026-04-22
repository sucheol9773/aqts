# AQTS Mailboxes

> 팀 간 비동기 커뮤니케이션을 위한 파일 기반 메일박스. CLAUDE.md §8 의 "메일박스 제목 규칙" 을 따릅니다.

## 디렉토리 구조

```
agent_docs/mailboxes/
├── team1/
│   ├── inbox/          # 다른 팀·리드가 팀 1 에게 보낸 메시지
│   └── processed/      # 팀 1 이 처리 완료 후 이동
├── team2/
│   ├── inbox/
│   └── processed/
├── team3/
│   ├── inbox/
│   └── processed/
├── team4/
│   ├── inbox/
│   └── processed/
└── lead/
    ├── inbox/
    └── processed/
```

`inbox/` 및 `processed/` 디렉토리는 첫 메시지 생성 시 자동으로 만들어집니다 (`scripts/team/mailbox_new.sh`).

## 메시지 생성

```bash
scripts/team/mailbox_new.sh <from> <to> <subject-slug>
# 예: scripts/team/mailbox_new.sh 2 1 kst-key-regression
```

생성 위치: `agent_docs/mailboxes/<to>/inbox/YYYYMMDD-HHMM-<slug>.md`

## 메시지 포맷

각 메시지는 YAML front-matter + 섹션형 본문:

```markdown
---
from: 2
to: 1
subject: kst-key-regression
created: 2026-04-22T12:15:00Z
priority: Ask  # [P0] 긴급, [Ask] 응답 요청, [FYI] 참고, [Lead-Approval] 리드 승인
---

# kst-key-regression

## 요약
...

## 맥락
...

## 요청 / 정보
...

## 응답 기한
...
```

## 처리 워크플로

1. 팀메이트가 세션 시작 시 `agent_docs/mailboxes/team<N>/inbox/` 디렉토리를 확인
2. 메시지를 읽고 대응 (응답 메시지 생성 또는 코드 작업)
3. 처리 완료된 메시지는 `processed/` 로 이동:
   ```bash
   mv agent_docs/mailboxes/team1/inbox/20260422-*.md agent_docs/mailboxes/team1/processed/
   ```

## git 관리

메시지는 **git tracked** 로 유지합니다. 근거:
- 팀 간 의사결정/요청의 기록 보존 — 나중에 "왜 이렇게 됐는지" 추적 가능
- 여러 worktree 간 동기화 — 한 팀이 작성한 메시지가 다른 팀 worktree 에서 즉시 가시
- PR review 시 "이 변경은 어느 요청 대응인가" 연결

회귀 시 `git log agent_docs/mailboxes/` 로 히스토리 감사. noise 가 문제되면 향후 Phase 5 에서 squash 정책 검토.

## 관련 문서

- `CLAUDE.md §8` — Agent Teams 운영 주의
- `agent_docs/governance.md §2` — 팀 소유권 경계
- `agent_docs/team_prompt_draft.md` — 세션 부트스트랩 템플릿
