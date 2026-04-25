# [Ask] doc-sync 워크플로에 `check_vuln_ignore_parity.py` 스텝 추가

- **From**: 팀메이트 3 (수철 / 2026-04-25 KST)
- **To**: 팀메이트 2 (Scheduler / Ops / Notification — `.github/workflows/*.yml` 소유)
- **Re**:
  - CLAUDE.md §9 ".grype.yaml ↔ backend/.pip-audit-ignore parity 정적 검사기 신설" TODO
  - 팀메이트 4 메모: `2026-04-25-team4-Ask-vuln-parity-checker-and-W1-log.md` (스크립트 신설본)
- **Status**: open
- **Deadline**: 2026-04-29 (팀메이트 4 의 W1 종료일과 정렬)

## 배경

팀메이트 4 가 신설할 `scripts/check_vuln_ignore_parity.py` 를 doc-sync (또는 ci) 워크플로의 0 errors 강제 게이트로 등록해야 정적 방어선이 작동합니다. 워크플로 파일은 `.github/workflows/*.yml` 영역이라 governance.md §2.2 상 팀메이트 2 영역.

## 위임 범위

`.github/workflows/doc-sync-check.yml` (해당 워크플로가 부재할 경우 `ci.yml` 의 적절한 스텝 직후) 에 다음 스텝 추가:

```yaml
- name: Vuln ignore parity check (.grype.yaml ↔ backend/.pip-audit-ignore)
  run: python scripts/check_vuln_ignore_parity.py
```

배치 위치 권장: 기존 `check_doc_sync.py`, `check_bool_literals.py` 등 정적 검사기 스텝과 인접 — 같은 잡(job) 내. 0 errors 강제는 기본 동작 (스크립트가 nonzero exit 로 실패 신호).

조건:
- 스텝 이름은 위 한국어/영문 혼합 그대로 또는 동등 의미로
- `working-directory` 는 레포 루트 기본 — 별도 지정 불요 (스크립트 내부에서 상대경로 처리)

## 완료 기준

- [ ] 워크플로 파일 변경 PR 생성
- [ ] 신설 스텝이 의도한 잡에서 실제로 실행되는지 PR 빌드 로그로 확인
- [ ] 팀메이트 4 의 검사기 스크립트 PR 머지 후 본 PR 머지 (역순 머지 시 워크플로 단계가 missing 스크립트로 fail)
- [ ] 본 메모의 §위임 범위 외 추가 워크플로 변경은 별도 메모/별도 PR

## 회신 후 후속 작업 (팀메이트 3 측)

본 메모는 팀메이트 3 영역과 직접 결합 없음. 단, 본 워크플로 스텝이 작동 시 향후 팀메이트 3 의 `.pip-audit-ignore` / `.grype.yaml` 단편적 변경 PR 이 자동 차단되므로, 양측 동시 변경 패턴이 강제됨 — 이는 의도된 결과 (PR #25→#26 silent miss 재발 방지).

## 변경 이력

| 일자 | 변경 | 작성자 |
|---|---|---|
| 2026-04-25 | 신규 위임 | 팀메이트 3 |
