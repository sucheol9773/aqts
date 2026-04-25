# [Lead-Approval] lxml bump + CLAUDE.md §9 갱신 + 메일박스 인프라 신설 일괄 동의

- **From**: 팀메이트 3 (수철 / 2026-04-25 KST)
- **To**: 리드
- **Re**:
  - CLAUDE.md §9 의 두 TODO ("lxml 6.1.0 업그레이드", "vuln parity 정적 검사기 신설")
  - governance.md §2.5 리드 전용 변경 영역
  - 동봉 메모 A/B/C (`2026-04-25-team4-Ask-lxml-OPS-021.md`, `2026-04-25-team4-Ask-vuln-parity-checker-and-W1-log.md`, `2026-04-25-team2-Ask-doc-sync-vuln-parity-step.md`)
- **Status**: open
- **Deadline**: 2026-04-29 (팀메이트 4 W1 종료일) — §3 메일박스 인프라 동의는 본 PR 머지 시점 즉시

## 배경

팀메이트 3 워크트리에서 CLAUDE.md §9 미해결 TODO 의 2/3/1 번 항목을 순차 진행하기 위해 본 위임 메모 묶음을 발신합니다. 발신자가 직접 처리할 수 없는 다음 3 항목에 리드 동의를 요청합니다.

## 1. `backend/requirements.txt:71` lxml bump 사전 동의

| 항목 | 현재 | 변경 후 |
|---|---|---|
| 라인 | `lxml==5.2.2` | `lxml==6.1.0` |
| 동기 | CVE-2026-41066 / GHSA-vfmq-68hx-4jfw 정식 해소 | |

`backend/requirements.txt` 의 명시적 소유자가 governance.md §2 에 부재하나, 운영 의존성 변경은 영향 범위가 넓어 사전 동의 요청. 본 변경은 메모 A 의 PR 묶음에 포함되며, 같은 PR 에 smoke test 와 `.pip-audit-ignore` / `.grype.yaml` 항목 동시 삭제가 포함 (PR #25→#26 silent miss 재발 방지).

**사전 검증 (팀메이트 3 가 PR 작성 전에 수행 예정)**:
- `pytest tests/ -k news_collector` 통과
- 실 RSS 피드 1~2 개 수동 pull 로 `news_collector._parse_entry` 회귀 부재 확인
- `BeautifulSoup(raw, "lxml").get_text()` 의 lxml 6.x 호환 확인 (기존 호출부 시그니처 유지)

**동의 시 발신자 후속 작업**:
- 팀메이트 3 워크트리에서 PR 작성 → 머지
- 머지 후 팀메이트 4 메모 A 의 OPS-021 작성 트리거

## 2. CLAUDE.md §9 두 TODO `[x]` 전환 동의

`CLAUDE.md` 는 governance.md §2.5 상 **리드 전용**. 다음 두 TODO 의 `[x]` 전환을 발신자 작업 완료 시점에 일괄 수행해 주십시오:

| TODO 라인 | 발효 조건 |
|---|---|
| "lxml 6.1.0 업그레이드 (CVE-2026-41066 후속, 발견 2026-04-22)" | 메모 A 의 PR 머지 + OPS-021 머지 |
| ".grype.yaml ↔ backend/.pip-audit-ignore parity 정적 검사기 신설 (발견 2026-04-22)" | 메모 B 의 PR 머지 + 메모 C 의 워크플로 스텝 머지 + OPS-022 머지 |

§9 `[x]` 전환 시 함께 갱신 권장:
- §5 "최근 회귀 사례" 에 PR #25→#26 lxml silent miss 회고가 lxml TODO 안에 이미 자세히 기록됨 → §9 lxml TODO 삭제 시 §5 본문은 보존 (회고 데이터로서 가치 유지)
- ADR-002 §5.3.1 Gate C 진척도 — Stage 2 W1 과제 a 완료 표기는 W1 로그에서 처리 (CLAUDE.md 직접 갱신 불요)

## 3. 메일박스 인프라 신설 승인

본 PR 이 신설하는 `agent_docs/mailbox/` 디렉토리 자체에 대한 동의 요청.

**승인 요청 근거**:
- governance.md §4.1 "메일박스 (Agent Teams 기본)" 가 시스템을 선언만 하고 물리적 위치 미지정
- CLAUDE.md §8 와 `.claude/rules/*.md` 4개 파일이 메일박스 사용을 전제로 작성됨
- 본 PR 이 첫 실사용 사례

**대안 위치 (리드 판단용)**:
- (a) `agent_docs/mailbox/` ← 본 PR 채택. agent 협업 컨텍스트 그룹과 정렬
- (b) `.claude/mailbox/` — Claude Code 도구 레이어와 정렬
- (c) `mailbox/` (루트) — 가시성 최대지만 루트가 비대해짐

리드가 (a) 외 위치를 선호하면 `git mv agent_docs/mailbox/ <new>/` 로 일괄 이동 + 본 메모 §3 인용 갱신.

**파급 변경 동의 사항**:
- governance.md §4.1 마지막 단락에 "물리적 위치: `agent_docs/mailbox/`" 한 줄 추가 (리드 직접 수정)
- CLAUDE.md §8 메일박스 prefix 규칙 줄 직후에 위치 인용 한 줄 추가 (리드 직접 수정)
- README.md (mailbox 디렉토리) 의 §1 "본 디렉토리는 provisional 상태" 문구 제거 (동의 후 발신자 또는 리드가 수정)

## 완료 기준

- [ ] 1. requirements.txt lxml bump 동의 — 메모 A 진행 가능
- [ ] 2. CLAUDE.md §9 두 TODO `[x]` 전환 (조건 충족 시 리드 직접 수정)
- [ ] 3. 메일박스 인프라 신설 동의 + governance.md §4.1 / CLAUDE.md §8 갱신 (리드 직접 수정)

각 항목은 독립 — 부분 동의 가능. 거부 시 사유와 대안 요청.

## 회신 후 후속 작업 (팀메이트 3 측)

- 1번 동의: 즉시 메모 A 진행 (PR 작성 → 머지 → OPS-021 트리거)
- 1번 거부: lxml ignore 만료일 (2026-06-06) 도래 전 대안 모색
- 3번 거부 + 다른 위치 지정: `git mv` 로 메일박스 이동 후 §1 알림 라인 갱신

## 변경 이력

| 일자 | 변경 | 작성자 |
|---|---|---|
| 2026-04-25 | 신규 위임 | 팀메이트 3 |
