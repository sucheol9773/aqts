# ADR-002 SKILL run 산출물 디렉토리

본 디렉토리는 ADR-002 Stage 2 Pilot 의 (β) 옵션 사전 적용 (2026-04-27, W1 mid-late) 결과로 신설되었습니다. wrapper 성격의 두 SKILL (`aqts-doc-sync-runner`, `aqts-rbac-route-checker`) 이 stdout 외에 file 시스템에 산출물을 강제 생성하도록 SKILL.md step 을 추가하면서, 그 산출물이 떨어지는 위치입니다.

## 배경

ADR-002 §5.3.1 의 (d) 카운트 기준 ("skill 산출물이 실제 file 로 떨어졌을 때만 1회") 이 lead 의 mid-week 회신(`team4/inbox/20260425-1352-w1-midweek-checkin-response.md` Ask #2) 으로 채택됨. 그러나 두 wrapper SKILL 모두 stdout 만 출력하고 새 file 미생성 → 본질적 (d) 미충족 가능성 발견. mid-late 메일(`lead/inbox/20260426-0154-w1-mid-late-checkin.md` Ask #1) 으로 (β) 산출물 file 강제 옵션 회신 요청, 응답 기한(2026-04-26 21:00 KST) 미수신으로 fallback (α) wrapper 면제 활성화. **그러나** Pilot 측에서 (α) 만 믿으면 §2 = 0 위험이 실존하므로, fail-safe 로 (β) 사전 적용.

## 파일 명명 규칙

- `doc-sync-<YYYYMMDD>-<HHMM>.md` — `aqts-doc-sync-runner` 산출물
- `rbac-route-<YYYYMMDD>-<HHMM>.md` — `aqts-rbac-route-checker` 산출물
- timestamp 는 KST 기준 (G7 KST 통일).

## 카운트 규칙

본 디렉토리에 새 file 이 떨어질 때마다 W1 로그 §2 호출 표에 1 행 추가. 산출물 경로 + skill 이름 + 호출 컨텍스트 + 결과 (pass/fail/partial) 기록.

## 머지 정책

- 본 README 는 git tracked.
- 실 산출물 (`doc-sync-*.md`, `rbac-route-*.md`) 은 W1 종료 PR 시 일괄 commit. 산출물 파일 자체가 W1 §2 호출 표의 audit trail 이므로 git 보존이 자연스러움.
- 매 호출마다 별도 commit 은 noise — 신규 Pilot 세션 종료 시 1 commit 으로 묶음.

## 관련 문서

- ADR-002 §5.3.1 (Exit Criteria, (d) 카운트 기준)
- ADR-002 §7.5.5 (W1 로그 §2 호출 표)
- W1 로그 `docs/architecture/sandbox/adr-002/skill-usage-log-W1.md` §3.2, §8 (변경 이력)
- `.claude/skills/aqts-doc-sync-runner/SKILL.md` step 11
- `.claude/skills/aqts-rbac-route-checker/SKILL.md` step 5

## 변경 이력

| 날짜 | 변경 | 작성자 |
|---|---|---|
| 2026-04-27 | 디렉토리 신설 + README ((β) 옵션 사전 적용) | Pilot |
