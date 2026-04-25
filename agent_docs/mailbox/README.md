# 메일박스 (Agent Teams 팀 간 통신)

> governance.md §4.1 가 선언한 메일박스 시스템의 **물리적 저장소**. 본 디렉토리 신설 자체는 리드 승인 사안이며, 첫 메모 `2026-04-25-lead-Lead-Approval-bundle.md` §3 에 그 동의 요청을 포함합니다. 리드 동의 전까지는 **provisional** 상태 — 위치 변경 시 `git mv` 로 통째 이동.

## 1. 목적

팀메이트 간 파일 소유권 경계를 위반하지 않으면서 교차 팀 변경을 조율합니다. 본 디렉토리는 **레포에 영구 추적되며**, 메모는 처리 후에도 회고 자료로 보존합니다 (소유권 경계와 위임 이력의 단일 진실원천).

## 2. 제목 prefix 규칙 (CLAUDE.md §8 / governance.md §4.1)

| Prefix | 용도 | 회신 SLA |
|---|---|---|
| `[P0]` | 긴급 차단/롤백 | 24h 내 |
| `[Ask]` | 일반 협의·작업 위임 | 1주 내 |
| `[FYI]` | 통보 — 회신 불필요 | — |
| `[Lead-Approval]` | 리드 승인 필요 변경 (소유권 §2.5) | 1주 내 |

## 3. 파일명 규칙

```
YYYY-MM-DD-<to>-<Prefix>-<short-subject>.md
```

- 일자는 KST 기준 발신일
- `<to>` = 수신 팀: `team1` / `team2` / `team3` / `team4` / `lead`
- `<Prefix>` = 위 표 그대로 (대시 포함)
- `<short-subject>` = kebab-case, 80자 이내

예: `2026-04-25-team4-Ask-vuln-parity-checker-and-W1-log.md`

## 4. 메모 본문 최소 구조

```markdown
# [Ask] (제목)

- **From**: 팀메이트 N (이름 / 발신일 KST)
- **To**: 팀메이트 M
- **Re**: (CLAUDE.md §9 항목 / ADR / PR 등 관련 컨텍스트)
- **Status**: open | in-progress | resolved | rejected
- **Deadline**: YYYY-MM-DD (있을 경우)

## 배경
(왜 위임하는지, 현재 진행 상황)

## 위임 범위
(파일 경로 + 변경 요지. "이 영역은 직접 수정 금지" 의 정확한 경계)

## 완료 기준
- [ ] 체크리스트 형태

## 회신 후 후속 작업 (발신자 측)
(수신자 작업 완료 시 발신자가 이어서 할 일)
```

## 5. 라이프사이클

1. **open** — 발신 직후. 수신 팀이 아직 인지 못한 상태.
2. **in-progress** — 수신 팀이 작업 시작. 메모 상단 `Status` 갱신.
3. **resolved** — 완료 기준 모두 충족. 머지 PR 링크를 메모 하단 §변경 이력 에 기록.
4. **rejected** — 수신 팀이 거부. 사유와 대안을 메모 하단에 추가.

처리 완료된 메모는 **삭제하지 않고** Status 만 갱신해 보존합니다 (회고용).

## 6. 본 디렉토리에 두지 않는 것

- ADR (`docs/architecture/`) 와 운영 회고 (`docs/operations/`) 는 별도 SSOT. 메모는 그 작성을 **트리거** 하는 용도이지 그 자체가 되지 않습니다.
- `.env` 실값 / API 키 / 계좌번호 (development-policies.md 절대 규칙).
