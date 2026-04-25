# [Ask] vuln ignore parity 정적 검사기 신설 (= ADR-002 W1 과제 a) + W1 로그 §3.1 갱신

- **From**: 팀메이트 3 (수철 / 2026-04-25 KST)
- **To**: 팀메이트 4 (Tests / Doc-Sync / Static Checkers, 동시에 ADR-002 Stage 2 Pilot)
- **Re**:
  - CLAUDE.md §9 ".grype.yaml ↔ backend/.pip-audit-ignore parity 정적 검사기 신설 (발견 2026-04-22)" TODO
  - ADR-002 Stage 2 W1 과제 a (`docs/architecture/sandbox/adr-002/skill-usage-log-W1.md §3.1`)
  - 회귀 회고: PR #25 → #26 lxml GHSA silent miss
- **Status**: open
- **Deadline**: 2026-04-29 (W1 종료일)

## 배경

PR #26 (`fix/grype-yaml-glibc-lxml-parity`) 이 `backend/.pip-audit-ignore` 만 업데이트하고 `.grype.yaml` 병기를 누락하여 main CI 의 `anchore/scan-action@v6` 가 lxml GHSA 를 다시 high 로 차단한 회귀가 있었음. 본 검사기는 그 회귀의 **정적 방어선**.

또한 본 작업이 ADR-002 Stage 2 W1 Pilot 의 **과제 a** 와 동일 스코프이므로, 작업 산출물이 W1 로그의 §3.1 5개 체크포인트를 그대로 채웁니다 — 별도 작업 분리 불필요.

## 위임 범위

**1. `scripts/check_vuln_ignore_parity.py` 신설** (governance.md §2.4 `scripts/check_*.py`)

요구 동작:
- 두 파일에서 CVE/GHSA 식별자 집합을 추출
  - `.grype.yaml`: `- vulnerability:` 라인 우측의 식별자 (`CVE-NNNN-NNNN` 또는 `GHSA-xxxx-xxxx-xxxx`)
  - `backend/.pip-audit-ignore`: 주석/공백을 제외한 첫 토큰
- 차집합 검출:
  - `.grype.yaml` 만 존재 + 라인 또는 직전 라인에 `# grype-only` 마커 없음 → error
  - `.pip-audit-ignore` 만 존재 + 라인 또는 직전 라인에 `# pip-audit-only` 마커 없음 → error
- 0 errors / nonzero on violation
- **AST 가드는 적용 불요** (YAML/플레인 텍스트라 grep 금지 원칙 미적용). 단 정규표현식 매칭 한 줄짜리 grep 흉내는 금지 — 토큰 단위 파싱.
- `--verbose` 시 양쪽 식별자 집합 + 마킹된 일방향 항목 + 위반 항목 출력
- `main()` 진입점 + `argparse` (`--grype-yaml`, `--pip-audit-ignore` 경로 override 지원, 테스트 편의)

**2. 회귀 테스트 하니스** `backend/tests/test_check_vuln_ignore_parity.py` (governance.md §2.4 `backend/tests/`)

최소 7 시나리오:
1. 양쪽 일치 → 0 errors
2. `.grype.yaml` 만 + `# grype-only` 마커 → 0 errors
3. `.pip-audit-ignore` 만 + `# pip-audit-only` 마커 → 0 errors
4. `.grype.yaml` 만 + 마커 없음 → error
5. `.pip-audit-ignore` 만 + 마커 없음 → error
6. 식별자 형식 혼합 (CVE / GHSA 둘 다) 정상 처리
7. 잘못된 마커 (`# grype_only` 언더스코어, 오타) → error 로 처리되지 않고 마커 없음으로 간주 → error

선택 8번: 실제 레포 파일 (`.grype.yaml`, `backend/.pip-audit-ignore`) 로 호출 → 0 errors (현재 상태 봉인)

**3. doc-sync 워크플로 0 errors 강제 등록**

이 부분은 `.github/workflows/*.yml` 영역이라 **팀메이트 2 에 별도 위임** (`2026-04-25-team2-Ask-doc-sync-vuln-parity-step.md`). 팀메이트 4 는 본 항목 작성/머지 후 팀메이트 2 메모에 PR 링크 회신만 하면 됨.

**4. `docs/operations/check-vuln-ignore-parity-2026-04-25.md` (OPS-022)** (governance.md §2.4)

- 신설 동기 (PR #25→#26 silent miss 회귀)
- 검사 알고리즘 의도 (양방향 차집합 + `# *-only` 마커 화이트리스트)
- 마커 미사용 시 fail 동작 + 운영자 대응 절차
- 만료된 ignore 항목과의 관계 (parity 검사기는 **만료 검사를 하지 않음** — 만료 추적은 `pip-audit` 자체 책임)

**5. ADR-002 Stage 2 W1 로그 갱신** `docs/architecture/sandbox/adr-002/skill-usage-log-W1.md §3.1`

본 작업 완료 시 §3.1 표의 5개 체크박스를 모두 `[x]` 로 전환하고 근거 PR 링크 기록:

| 체크포인트 | 근거 |
|---|---|
| 스크립트 초안 작성 | (PR 번호) |
| 테스트 하니스 7 시나리오 | (PR 번호) |
| `.github/workflows/doc-sync-check.yml` 스텝 추가 | (팀메이트 2 PR 번호 — 메모 C 결과) |
| OPS-022 작성 | (PR 번호) |
| CLAUDE.md §9 TODO `[x]` 전환 | 리드 메모 D 동의 후 |

또한 §2 호출 표에 본 작업 도중 자동 트리거된 anthropic-skills 가 있으면 누적 기록 (W1 목표: 누적 ≥ 2회).

## 완료 기준

- [ ] 1. `scripts/check_vuln_ignore_parity.py` 작성 + `python scripts/check_vuln_ignore_parity.py` 0 errors (현재 레포 상태)
- [ ] 2. `backend/tests/test_check_vuln_ignore_parity.py` 7 시나리오 + `pytest tests/test_check_vuln_ignore_parity.py -q` 7 passed
- [ ] 3. (팀메이트 2) doc-sync 워크플로 스텝 추가 — 메모 C
- [ ] 4. OPS-022 작성
- [ ] 5. W1 로그 §3.1 5개 체크박스 `[x]` + §2 호출 표 갱신 + §4 F1~F7 / §5 G1~G7 갱신
- [ ] 6. CLAUDE.md §9 parity 검사기 TODO `[x]` 전환 — 리드 메모 D 동의 후

## 회신 후 후속 작업 (팀메이트 3 측)

본 작업은 팀메이트 3 영역과 직접 의존성 없음. 단, lxml 메모 (메모 A) 와 동시 진행 시 `.pip-audit-ignore` 와 `.grype.yaml` 이 같이 변경되므로 **머지 순서 충돌** 주의:

1. 메모 A (lxml) 가 먼저 머지되면 `.grype.yaml` / `.pip-audit-ignore` 의 lxml 블록이 사라짐 → 본 메모의 검사기는 빈 상태에서 0 errors 통과 → 정상
2. 본 메모가 먼저 머지되면 검사기가 lxml 항목 양쪽 존재를 정상으로 통과 → 메모 A 머지 시 양쪽 동시 삭제 → 정상
3. **금지 시나리오**: 메모 A 가 한쪽만 삭제하고 머지 → 메모 B 의 검사기가 차단. 회귀 방어 의도 그대로 작동.

## 변경 이력

| 일자 | 변경 | 작성자 |
|---|---|---|
| 2026-04-25 | 신규 위임 | 팀메이트 3 |
