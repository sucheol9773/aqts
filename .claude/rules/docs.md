---
paths:
  - "docs/**/*.md"
  - "agent_docs/**/*.md"
  - "CLAUDE.md"
  - "README.md"
---

# 문서 영역 가드

**소유**: **혼합 영역** — 리드 전용 문서와 팀메이트 4 소유 문서가 공존합니다. 상세: `agent_docs/governance.md §2.4, §2.5`.
**SSOT**: `agent_docs/development-policies.md §2,§3.1` (커밋 문서화 규칙 / 문서-only 예외).

## 파일별 소유권 (반드시 확인 후 수정)

| 경로 | 소유 | 비고 |
|---|---|---|
| `CLAUDE.md` (루트) | **리드 전용** | 200줄 이하 유지. 진입점+요약+포인터만 |
| `agent_docs/development-policies.md` | **리드 전용** | 규칙 SSOT. 규칙 변경은 여기부터 |
| `agent_docs/governance.md` | **리드 전용** | 팀 구조 / 소유권 매트릭스 |
| `docs/archive/**` | **리드 전용** | 수정 금지. 역사적 참조 전용 |
| `docs/FEATURE_STATUS.md` | 팀메이트 4 | `gen_status.py` 로 자동 생성 |
| `docs/PRD.md` | 팀메이트 4 | 요구사항 / 테스트 카운트 |
| `docs/operations/**` | 팀메이트 2 | 운영 런북 |
| `docs/backtest/**` | 팀메이트 1 | 백테스트 리포트 |
| `docs/security/**` | 팀메이트 3 | RBAC / 공급망 정책 |
| `agent_docs/backtest-operations.md` | 팀메이트 1 | |
| `agent_docs/api_contracts.md` | 팀메이트 3 | |
| `agent_docs/database_schema.md` | 팀메이트 3 | |
| `agent_docs/architecture.md` | 리드 + 전팀 협의 | |

경계 밖 파일을 수정할 필요가 있으면 메일박스로 담당자/리드에게 위임합니다.

## 절대 규칙

- **모든 커밋에 관련 `.md` 업데이트 동봉** (development-policies.md §2). 나중에 몰아서 문서화하지 않고, 해당 커밋 시점에 즉시 작성합니다.
  - 새 기능: 설계 근거, 파라미터 설명, 기대 효과
  - 변경/수정: 변경 전후 비교, 변경 이유
  - OOS/백테스트 결과가 바뀌는 변경: 결과 비교 테이블을 분석 리포트에 추가
- **하드코딩 금지**. `.env` 실값 / API 키 / 계좌번호는 문서에도 포함 금지. 키 이름만 인용.
- **저작권/인용**: 외부 자료 인용 시 15 단어 미만 + 인용부호 + 출처. 장문의 displacive 요약 금지.

## 문서-only 커밋 예외 (development-policies.md §3.1)

코드 변경이 **단 한 줄도 없고** `.md` / `.env.example` / 문서성 yaml 주석만 수정되는 경우, 전체 `pytest tests/` 생략 가능.

판정 기준: `git diff --stat` 에 다음 파일이 단 하나도 포함되지 않을 것
- `.py`
- `.toml`
- `.sh`
- `Dockerfile*`
- `.github/workflows/*.yml`

**예외 중 예외**: `docker-compose.yml` 이 logging 설정 추가, 주석 수정, 환경변수 기본값 변경 등 Python import/실행 경로를 건드리지 않는 경우에 한해 전체 pytest 생략 가능. 이미지 태그 / command / 코드가 읽는 environment 변수는 건드리면 전체 pytest 실행.

## 문서-only 최소 게이트 (생략 금지)

```bash
cd backend && python -m ruff check . --config pyproject.toml       # .py 영향 zero 확인
cd backend && python -m black --check . --config pyproject.toml    # .py 영향 zero 확인
python scripts/check_bool_literals.py                              # .env.example 수정 시 필수
python scripts/check_doc_sync.py --verbose                         # 존재 시 필수
# 해당 문서에 직접 연관된 테스트가 있으면 그 파일만 실행
# 예: pytest tests/test_doc_sync.py
```

## CLAUDE.md 유지 원칙 (리드 전용)

- 본 문서(CLAUDE.md)는 **200줄 이하** 유지. 세부 규칙이 늘어나면 본 문서에 추가하지 않고 `agent_docs/` 해당 파일로 편입.
- 규칙 변경은 반드시 `agent_docs/development-policies.md` 를 먼저 수정하고, CLAUDE.md 는 요약/포인터만 갱신.
- `docs/archive/CLAUDE-pre-phase1-migration.md` 는 마이그레이션 이전 원본 아카이브. **수정 금지**.
- 로컬 `*.bak` 은 리드 임시 작업용. `.gitignore:56` 에 의해 계속 ignored.

## doc-sync warning 처리

`check_doc_sync.py --verbose` 에서 TEST_COUNT warning 이 나오면, `docs/FEATURE_STATUS.md` 의 테스트 수를 실제 값과 맞춘 뒤 커밋합니다 (development-policies.md §9). 0 errors + 0 warnings 상태에서만 커밋.
