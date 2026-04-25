# [Ask] OPS-021 운영 회고 문서 작성 — lxml 6.1.0 업그레이드

- **From**: 팀메이트 3 (수철 / 2026-04-25 KST)
- **To**: 팀메이트 4 (Tests / Doc-Sync / Static Checkers)
- **Re**: CLAUDE.md §9 "lxml 6.1.0 업그레이드 (CVE-2026-41066 후속)" TODO
- **Status**: open
- **Deadline**: 2026-05-23 (lxml ignore 만료일 2026-06-06 의 2주 전)

## 배경

`backend/requirements.txt:71` 의 `lxml==5.2.2` 를 `lxml==6.1.0` 으로 bump 하면서 CVE-2026-41066 / GHSA-vfmq-68hx-4jfw (XXE High, CVSS 7.5) 를 정식 해소합니다. 현재는 `backend/.pip-audit-ignore:40` 과 `.grype.yaml:93` 에 만료일 2026-06-06 으로 양측 등록된 상태.

팀메이트 3 가 본 작업의 코드/설정/테스트 부분을 수행하지만, **운영 회고 문서 (`docs/operations/*.md`) 는 governance.md §2.4 상 팀메이트 4 영역**이라 본 메모로 위임합니다.

## 위임 범위

**팀메이트 4 가 작성**:

- `docs/operations/lxml-6.1.0-upgrade-2026-04-25.md` (OPS-021)
  - 업그레이드 일자, 작업자, 머지 PR 번호
  - 변경 파일 목록 (`backend/requirements.txt`, `backend/.pip-audit-ignore`, `.grype.yaml`, `backend/tests/test_news_collector_*.py` 추가/갱신분, `backend/core/data_collector/news_collector.py` (있을 경우))
  - smoke test 결과 요약 (RSS 피드 1~2 개 실측 / `pytest tests/ -k news_collector` 통과 로그 발췌)
  - silent miss 회귀 방어 — `.pip-audit-ignore` 와 `.grype.yaml` 의 GHSA-vfmq-68hx-4jfw 항목을 **동일 커밋에서 동시 삭제** 했음을 명시 (PR #25 → #26 회귀 사례에 대한 대응 근거 인용)
  - CLAUDE.md §9 lxml TODO `[x]` 전환 시점

**팀메이트 3 가 동반 수행 (별도 메모 없이 같은 PR 묶음)**:

- `backend/requirements.txt:71` `lxml==5.2.2` → `lxml==6.1.0`
- `backend/.pip-audit-ignore:31~40` 의 lxml/GHSA-vfmq-68hx-4jfw 블록 전체 삭제
- `.grype.yaml:87~93` 의 동일 블록 전체 삭제
- `backend/tests/test_news_collector_*.py` 에 `_parse_entry` smoke test 추가 (RSS 피드 fixture 기반, `BeautifulSoup(raw, "lxml").get_text()` 경로 회귀 방어)
- `backend/core/data_collector/news_collector.py:222/224` 코드 변경 없음 — BeautifulSoup HTML 파서 경로는 lxml 6.x 호환 확인만 필요

## 완료 기준

- [ ] OPS-021 작성 (위 §위임 범위 첫 블록)
- [ ] 팀메이트 3 PR 머지 후 OPS-021 본문에 머지 PR 번호 + 머지일 기록
- [ ] CLAUDE.md §9 lxml TODO `[x]` 전환은 리드 메모 D (`2026-04-25-lead-Lead-Approval-bundle.md`) 의 동의가 선결

## 회신 후 후속 작업 (팀메이트 3 측)

- OPS-021 머지 후, 다음 PR 의 코밋 메시지에 OPS-021 경로를 인용하여 추적 봉합
- silent miss 정적 방어선 강화는 별도 메모 `2026-04-25-team4-Ask-vuln-parity-checker-and-W1-log.md` 에서 다룸 (parity 검사기)

## 변경 이력

| 일자 | 변경 | 작성자 |
|---|---|---|
| 2026-04-25 | 신규 위임 | 팀메이트 3 |
