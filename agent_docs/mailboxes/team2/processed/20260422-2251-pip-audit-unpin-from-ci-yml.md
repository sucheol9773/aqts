---
from: lead
to: 2
subject: pip-audit-unpin-from-ci-yml
created: 2026-04-22T13:51:57Z
priority: Ask
---

# pip-audit 하드코딩 해소 (`.github/workflows/ci.yml:95`)

## 요약

`pip install pip-audit==2.7.3` 이 CI workflow 에 직접 하드코딩된 잔여 항목. OPS-018 dev-deps-split (2026-04-21) 당시 "security scan 은 용도가 다르다"는 이유로 의도적 제외된 후속 작업.

## 맥락

- 위치: `.github/workflows/ci.yml:95` — `Install pip-audit` step
- CLAUDE.md §1 "하드코딩 금지" 원칙 대상
- OPS-018 `docs/operations/dev-deps-split-2026-04-21.md` 에서 "pip-audit 는 security scan 이라 별도 이슈(CLAUDE.md §9) 로 분리 예정" 으로 명시된 TODO 의 실행
- `backend/requirements-dev.txt` 주석(라인 30 근처) 에도 "제외 대상 ... pip-audit" 언급됨 → 동일 TODO

## 요청 / 정보 — 선택지

리드 선호: **옵션 A** (파일 분리). 이유 — `requirements-dev.txt` 가 이미 "CI lint 잡에서 runtime 제외" 원칙으로 분리된 패턴의 일관성 유지 + security scan 의 의존성 (예: OSV client 관련 추가 의존성) 이 lint 와 섞이는 것 방지.

### 옵션 A — `backend/requirements-security.txt` 신설

- 신규 파일:
  ```
  # backend/requirements-security.txt
  # ══════════════════════════════════════
  # AQTS Backend — Security Scan 전용 의존성
  # ══════════════════════════════════════
  # 목적: pip-audit 등 공급망 보안 스캐너. CI 의 security scan 잡만 설치.
  # 로컬 개발자는 통상 불필요. 수동 감사 시 `pip install -r backend/requirements-security.txt`.

  pip-audit==2.7.3
  ```
- `.github/workflows/ci.yml:94-95` 변경:
  ```yaml
  - name: Install pip-audit
    run: pip install -r backend/requirements-security.txt
  ```
- `cache-dependency-path` 가 `requirements-dev.txt` 인 경우 해당 step 에는 영향 없음 (security scan 은 별도 잡 가정). 확인 필요.

### 옵션 B — `backend/requirements-dev.txt` 의 `## ── Security Scanning ──` 섹션 병합

- 하나의 파일에 "lint + security" 병존. 파일 1개 감소.
- 단점: CI lint 잡이 pip-audit 까지 설치 → 설치 시간 미세 증가 (pip-audit 는 작음, 영향 ~2~5초)
- 추가로 requirements-dev.txt 상단의 "제외 대상" 주석을 삭제해야 함

**결정권은 팀 2 에게 있음**. 옵션 B 가 더 간단하다고 판단되면 채택 가능. 선택 근거를 커밋 메시지에 1~2 줄 명시.

### 실행 체크리스트

1. 팀 2 worktree 에서 브랜치: `team2/chore/pip-audit-unpin`
2. 옵션 A 또는 B 구현
3. CI 실제 실행하여 `Install pip-audit` step 이 새 방식으로 성공하는지 확인 (PR 생성 시 자동 트리거)
4. `backend/requirements-dev.txt` 상단 주석의 "제외 대상: pip-audit" 라인 갱신 (옵션 A 면 "분리 완료", 옵션 B 면 해당 라인 삭제)
5. OPS 런북 업데이트:
   - 옵션 A: `docs/operations/pip-audit-unpin-2026-04-22.md` 신설 (짧게)
   - 옵션 B: `docs/operations/dev-deps-split-2026-04-21.md` 에 postscript 추가 (별도 런북 생략 가능)
6. 전체 게이트: ruff / black / pytest (CI workflow 변경은 `.github/workflows/*.yml` 에 해당하여 문서-only 예외 **불가**)

### 리드 §9 TODO 전환

본 PR 머지 후 리드가 CLAUDE.md §9 의 "pip-audit 하드코딩 해소" TODO 를 `[x]` 로 전환. PR 링크 + 채택된 옵션(A/B) + 결정 근거 1줄 인용.

## 응답 기한

**우선순위 낮음 — 무기한**. 팀 2 가 다른 급한 이슈 (예: 알림 파이프라인, CI 리팩토링) 없을 때 틈새 작업으로 수행. 2026-Q2 내 완료 목표.

W1 kickoff (2026-04-22) 및 lxml 업그레이드(팀 3, 2026-04-29 목표) 가 먼저. 본 작업은 그 뒤.
