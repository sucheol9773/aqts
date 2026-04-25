---
from: lead
to: 4
subject: ops-027-check-ops-numbering
created: 2026-04-25T13:01:58Z
priority: Ask  # [P0] 긴급, [Ask] 응답 요청, [FYI] 참고, [Lead-Approval] 리드 승인
---

# ops-027-check-ops-numbering

## 요약

`docs/operations/ops-numbering.md §6` 후속 TODO — `scripts/check_ops_numbering.py` 정적 검사기 신설을 팀메이트 4 영역으로 위임합니다. 본 검사기 자체가 다음 OPS 번호 = **OPS-027** 로 발급됩니다 (`ops-numbering.md §2` "다음 발급 가능 번호: OPS-027").

## 맥락

OPS-006/009 충돌 2건 (`ops-numbering.md §3.2, §3.3`) 의 근본 원인이 "발급 전 §2 표 미확인 → 동일 번호 중복 점유" 였습니다. registry 가 신설되었지만 (PR #41), 사람의 절차에만 의존하는 한 회귀 가능. OPS-022 (`check_vuln_ignore_parity.py`) / OPS-026 (`check_vuln_ignore_expiry.py`) 와 동일한 "두 SSOT 정합성 자동 강제" 패턴으로 마감하는 것이 자연스러운 후속 작업입니다.

본 작업이 정적 검사기이므로 governance §3 매트릭스 상 팀메이트 4 영역. 리드가 직접 작업하면 §2.5 외 영역 침범 + 사용자가 4-세션 격리 정책으로 명시한 경계 위반.

## 요청

`scripts/check_ops_numbering.py` 신설 + 테스트 하니스 + Doc Sync 워크플로 등록 + OPS-027 작업 기록 문서.

### 위반 카테고리 (4종, `ops-numbering.md §6` 명시)

각각 별도 exit code 또는 명료한 에러 메시지로 구분:

1. **표에는 있는데 파일이 없거나 헤더 OPS 번호가 다름** — registry §2 의 row 가 stale
2. **파일 헤더에는 있는데 표에 없음** — silent 발급 (OPS-023 충돌의 직접 원인)
3. **동일 번호가 두 파일 헤더에 등장** — 충돌 (OPS-006/009 회고와 동일 패턴, 자동 검출 핵심 가치)
4. **"다음 발급 가능 번호" 줄이 실제 최댓값+1 과 불일치** — 발급자가 표 갱신을 누락한 silent miss

### 입력 파서 설계

- registry §2 표는 markdown table — 각 row 의 첫 셀이 `OPS-NNN` 또는 `OPS-NNN ~ OPS-MMM` (gap 표기) 또는 `(예약 — ...)` 패턴. 정규식 `^\| OPS-(\d{3})` 로 충분하나, range·예약·branch-only 어휘 처리 필요.
- 각 `docs/operations/*.md` 파일 헤더에서 `**문서 번호**: OPS-NNN` 라인 추출. **단, 일부 OPS 문서는 헤더에 명시적 표기가 없고 commit 메시지로만 발급된 경우가 있음** (OPS-009 `gcp-provisioning-guide.md` commit `c88608d` 가 이 패턴). 회피 옵션 — (a) registry 의 파일명 column 을 1차 진실원천으로 사용 + 헤더 표기는 mismatch 검증용, (b) 파일에 헤더 표기가 없으면 warning 으로 분류.
- 상태 어휘 (`활성` / `예약` / `branch-only` / `결손` / `⚠️ 충돌`) 별 검증 분기:
  - `활성` → 파일 존재 필수
  - `예약` → 파일 존재 금지 (만료 조건만 검사)
  - `branch-only` → 파일 부재 허용 (§1.4 잠정 예외) + branch 명 표기 필수
  - `결손` → row 자체가 없거나 명시적 gap 표기 (`OPS-010 ~ OPS-016 | — | — | — | 결손 (gap)`)
  - `⚠️ 충돌` → §3 회고 링크 필수 + 정정 PR 추적

### 테스트 하니스 (6 그룹 구조 — OPS-022/026 패턴 재사용)

`backend/tests/test_check_ops_numbering.py`:

1. **유효 통과** — 정합 상태 (`docs/operations/` 실제 스냅샷) PASS
2. **위반 검출** — 각 4 카테고리당 최소 1 케이스 (총 ≥ 4 tests)
3. **오탐 방지** — 정상 패턴이 false positive 안 나는지 (예: gap 표기, 예약, branch-only, 충돌 §3 링크)
4. **파서 견고성** — markdown table 외 텍스트 (코드 블록, 설명 문단) 가 파서를 오인하지 않음
5. **실제 레포 회귀** — 현재 main 의 `ops-numbering.md` + `docs/operations/*.md` 헤더로 구동, **현재 미해결 충돌 2건 (OPS-006, OPS-009) 은 registry 가 `⚠️ 충돌` 로 명시 + §3 회고 링크 보유 → 정상 PASS** (registry 가 의도적으로 인정한 충돌은 카테고리 3 위반이 아님)
6. **`main()` 진입** — argparse / exit code 표준화

### Doc Sync 워크플로 등록

`.github/workflows/doc-sync.yml` (또는 동등 워크플로) 의 vuln-ignore parity / expiry 스텝 인근에 `Run OPS numbering check` 스텝 추가. 0 errors 강제.

### OPS-027 작업 기록 문서

`docs/operations/check-ops-numbering-2026-04-NN.md` (작업일 NN). registry §2 표에 한 줄 추가 + "다음 발급 가능 번호" `OPS-027` → `OPS-028` 갱신을 **동일 PR 안에 동봉** (§1.3 PR atomic). 본 검사기가 자기 자신 row 를 검증할 수 있어야 하므로 row 추가 직후 검사기가 그린이어야 함 (자기 적용성 = self-hosting 검증).

### 게이트

- ruff + black PASS
- `pytest backend/tests/test_check_ops_numbering.py` ≥ 12 tests PASS
- 본 검사기 직접 실행: `python scripts/check_ops_numbering.py` 0 errors
- (선택) main 에 머지 전 `--dry-run` 으로 미해결 충돌 2건이 §3 link 로 인해 PASS 되는지 수동 확인

### 회귀 방어선 (PR 본문에 명시 권장)

- "registry 와 헤더 양방향 sync" 자체 보호 — 한쪽만 수정 시 즉시 차단
- OPS-006/009 패턴 재발 방지 — 신규 OPS 발급 PR 이 §2 표 갱신 누락 시 CI 차단
- self-hosting 회귀 — 검사기 신설 PR 자체가 OPS-027 row 추가 누락 시 본 검사기로 자기 차단

## 참조

- `docs/operations/ops-numbering.md` (registry SSOT, 본 작업 트리거)
- `docs/operations/check-vuln-ignore-parity-2026-04-23.md` (OPS-022, 패턴 원형)
- `docs/operations/check-vuln-ignore-expiry-2026-04-23.md` (OPS-026, 6 그룹 테스트 하니스 원형)
- `scripts/check_vuln_ignore_parity.py`, `scripts/check_vuln_ignore_expiry.py` (구현 원형)
- `agent_docs/governance.md §2.4` (팀메이트 4 영역 = `backend/scripts/check_*.py` + `scripts/gen_status.py` 류 정적 검사기)

## 응답 기한

없음 (P2 — 우선순위 낮음, ADR-002 Stage 2 관찰 (~2026-05-06) 종료 후 W2 안에 착수 권장). 다만 신규 OPS 문서가 그 사이 발급될 가능성이 있어 — 발급자가 §2 표 갱신을 빠뜨릴 silent miss 위험은 그 전까지 수동 확인에 의존.
