# AQTS — Claude Code Agent Teams 가이드 (슬림 버전)

> **본 문서는 단일 진실원천이 아닙니다.** 모든 상세 규칙은 `agent_docs/` 하위 문서가 단일 진실원천이며, 본 문서는 세션 시작 시 팀메이트가 가장 먼저 읽는 **진입점 + 요약 + 포인터** 입니다.
>
> 원본 전체 규칙은 `docs/archive/CLAUDE-pre-phase1-migration.md` 에 아카이브되어 있으며 수정 금지입니다. 규칙 변경은 `agent_docs/development-policies.md` 만 갱신합니다.

---

## 0. 30초 부트스트랩

| 상황 | 읽을 문서 |
|---|---|
| 코딩/커밋/검증 규칙이 필요할 때 | `agent_docs/development-policies.md` |
| 팀 구조·소유권·워크플로가 필요할 때 | `agent_docs/governance.md` |
| 코드 위치/아키텍처가 궁금할 때 | `agent_docs/architecture.md` |
| API 엔드포인트·RBAC 권한 매트릭스 | `agent_docs/api_contracts.md` |
| DB 스키마 (Postgres/Mongo/Redis) | `agent_docs/database_schema.md` |
| 백테스트·OOS·하이퍼옵트 운영 | `agent_docs/backtest-operations.md` |
| Agent Teams 기동 프롬프트 템플릿 | `agent_docs/team_prompt_draft.md` |

---

## 1. 프로젝트 개요

- **이름**: AQTS — AI Quant Trade System
- **언어/런타임**: Python (Dockerfile 기준 `python:3.11-slim`, pyproject `py310`). 버전 불일치 정리 필요 — TODO §9.
- **핵심 스택**: FastAPI + SQLAlchemy (async) + Motor (MongoDB) + Redis + Alembic + Optuna + Prometheus/Grafana/Alertmanager/OpenTelemetry/Jaeger
- **배포**: Docker Compose v2, GitHub Container Registry (`ghcr.io/<owner>/aqts-backend`), GitHub Actions CI/CD (cosign keyless + syft SBOM + grype + pip-audit)
- **운영 모드**: 스케줄러 컨테이너(`backend/scheduler_main.py`) + 백엔드 API 컨테이너(`backend/main.py`) 분리 기동

---

## 2. 절대 규칙 (세부는 development-policies.md)

1. **테스트 기대값 수정 절대 금지**. 오류 발생 시 기대값이 아닌 입력값/로직을 조정합니다 (development-policies.md §1).
2. **black + ruff + pytest** 세 명령을 **직접 실행**한 결과를 확인한 뒤에만 커밋 (development-policies.md §3).
3. **모든 커밋에 관련 `.md` 업데이트를 동봉** (development-policies.md §2).
4. **하드코딩 금지**. `.env` 실값·API 키·계좌번호·개인정보는 어떤 문서/프롬프트/커밋에도 포함하지 않고, `.env.example` 의 키 이름만 인용합니다.
5. **추측 금지, 관찰 우선**. 에러 원인을 로그/출력으로 먼저 확인한 뒤 수정합니다 (development-policies.md §7).
6. **소유권 경계 준수**. governance.md §2 영역 밖 파일은 메일박스로 담당자에게 위임합니다.
7. **Wiring Rule**: 설정/가드/메트릭은 "정의했다 ≠ 적용했다". 통합 테스트 또는 런타임 로그로 주입·기동 확인 (development-policies.md §5, §12, §13, §14).

---

## 3. 팀 구성 요약 (governance.md §2 참조)

| # | 팀메이트 | 주요 영역 |
|---|---|---|
| 1 | Strategy / Backtest | `backend/core/{strategy_ensemble, backtest_engine, oos, hyperopt, param_sensitivity, quant_engine}`, `scripts/run_*.py` |
| 2 | Scheduler / Ops / Notification | `backend/scheduler_main.py`, `backend/core/{notification, monitoring, scheduler*}`, `docker-compose*.yml`, `.github/workflows/*.yml` |
| 3 | API / RBAC / Security | `backend/api/`, `backend/db/`, `backend/alembic/`, `backend/core/{audit, order_executor, portfolio_*, idempotency, trading_guard}` |
| 4 | Tests / Doc-Sync / Static Checkers | `backend/tests/`, `backend/scripts/check_*.py`, `scripts/gen_status.py`, `docs/FEATURE_STATUS.md`, `docs/PRD.md` |

**리드 전용 변경 영역**: `CLAUDE.md`, `agent_docs/development-policies.md`, `backend/core/utils/env.py`, `backend/core/utils/time.py`, `backend/config/settings.py`, `.env.example`, `docs/archive/` (governance.md §2.5).

---

## 4. 커밋 전 필수 게이트 (development-policies.md §3 요약)

```bash
cd backend && python -m ruff check . --config pyproject.toml
cd backend && python -m black --check . --config pyproject.toml
cd backend && python -m pytest tests/ -q --tb=short   # timeout ≥ 540s
```

문서-only 커밋 예외: `.py`/`.toml`/`.sh`/`Dockerfile*`/`.github/workflows/*.yml` 변경이 **단 한 줄도 없는** 경우 전체 pytest 생략 가능하나, **최소 게이트**(ruff + black + `check_bool_literals.py` + `check_doc_sync.py` + 관련 단위 테스트) 는 생략 금지. 판정 기준과 예외 상세는 development-policies.md §3.1.

**RBAC 변경 시 추가 게이트**: `python scripts/check_rbac_coverage.py` 0 errors + `tests/test_rbac_routes.py` 통과 + viewer 토큰 수동 403 확인.

**알림/스케줄러 변경 시 추가 게이트** (배포 후, development-policies.md §14.2):

```bash
docker compose logs backend --tail=500 | grep 'NotificationRouter wired'
docker compose logs backend --tail=500 | grep 'AlertRetryLoop started'
curl -s http://<backend>/metrics | grep -c 'aqts_alert_dispatch'   # 0 이면 결손
```

---

## 5. 최근 회귀 사례 (경계 포인트)

- **KST 통일 (2026-04-15)**: Redis 스냅샷 키 `utcnow().strftime("%Y-%m-%d")` → `today_kst_str()` 변경 시 테스트 fixture 가 UTC 를 사용하여 silent miss. development-policies.md §8 "Silence Error 의심 원칙".
- **scheduler stdout block-buffering (2026-04-15)**: `PYTHONUNBUFFERED` 미설정 → 49분 동안 `docker compose logs scheduler` 가 0 bytes. compose `environment:` 에 `PYTHONUNBUFFERED: "1"` 유지.
- **loguru %-format mismatch (2026-04-15, 10.15/10.16)**: `logger.info("...%d...", n)` posarg 스타일은 loguru 에서 literal 로 기록되고 posargs 를 버림. 정적 방어선은 반드시 AST 기반으로 구현 (regex 누락 사례 있음).
- **Prometheus `rule_files` 상대경로 silent miss (2026-04-16)**: config 이동 시 상대경로 resolve 기준이 바뀌며 39 rule 전체가 로드 실패. 절대경로로 고정.
- **compose change-detection 미감지 (2026-04-16)**: bind-mount 파일 내용만 수정된 배포는 `docker compose up -d` 가 recreate 하지 않음. CD 에서 조건부 restart + `/api/v1/rules` groups≥1 어서트 필수.
- **SSH heredoc stdin 소진 (2026-04-09, #91/#92)**: `docker exec -i` 및 `-T` 없는 `docker compose run` 이 heredoc 잔여 라인을 소진하여 후속 Step 을 은폐. 자식이 fd 0 을 읽는 모든 프로세스는 `</dev/null` 격리 필수 (development-policies.md §15).

**규칙**: CI/CD 에서 발견된 warning 이든 error 든 **발견 시점이 수정 시점**. 다음 사람에게 넘기지 않는다 (development-policies.md §9).

---

## 6. 알림 파이프라인 5 레이어 (development-policies.md §14)

| 레이어 | 정의 위치 | 적용 위치 |
|---|---|---|
| 상태 머신 메서드 | `backend/core/notification/alert_manager.py` | `_dispatch_via_router`, `dispatch_retriable_alerts` |
| NotificationRouter 인스턴스 | `fallback_notifier.py` | `backend/main.py` lifespan `set_notification_router` |
| 재시도 루프 `_alert_retry_loop` | `backend/main.py` 함수 정의 | lifespan `asyncio.create_task` |
| Prometheus 메트릭 훅 | `backend/core/monitoring/metrics.py` | `NotificationRouter.dispatch` try/finally |
| 메타알림 규칙 `aqts_alert_pipeline` | `prometheus/rules/aqts_alerts.yml` | Alertmanager 로드 |

---

## 7. 공급망 보안 요약 (development-policies.md §13)

- 레지스트리: `ghcr.io/${IMAGE_NAMESPACE}/aqts-backend` 단일
- CI: `pip-audit` + `grype high+` + `syft` SBOM(CycloneDX JSON) + `cosign sign` keyless(Fulcio/Rekor) + `cosign attest`
- CD: `cosign verify` 실패 시 즉시 중단, `docker pull` 진입 금지
- 화이트리스트: `backend/.pip-audit-ignore` 만 인정. 만료일 + 사유 필수
- 상세 정책: `docs/security/supply-chain-policy.md`

---

## 8. Agent Teams 운영 주의

- 단일 세션 대비 **3~5 배** 토큰 소모. 2~3 명으로 시작해 숙련 후 4명 풀팀으로 확장 (governance.md §6).
- 하이퍼옵트/OOS 등 장시간 계산은 `scripts/run_*` 로 오프라인 수행, 결과만 세션으로 가져옴.
- `git worktree add ../aqts-<team>-<task> <branch>` 로 독립 환경 필수.
- Plan Mode 는 고위험 변경(알림 파이프라인, RBAC, 공급망, 스케줄러 동시성) 에 **필수**.
- 메일박스 제목 규칙: `[P0]` 긴급, `[FYI]`/`[Ask]` 일반, `[Lead-Approval]` 리드 승인 필요.

---

## 9. 미해결 TODO (리드 갱신 항목)

- [x] **black 포맷 drift 해소 (완료, 26/26)**: 2026-04-21 `chore/black-format-drift` 브랜치에서 팀메이트 1/2/3/4 영역 순차 해소. `python -m black --check . --config pyproject.toml` → 379 files unchanged. 작업 분할: 팀메이트 1(strategy_ensemble, 2 파일), 팀메이트 2(scheduler/data_collector, 6 파일), 팀메이트 3(alembic/api/ai_analyzer/idempotency/order_executor/portfolio_manager/db, 16 파일), 팀메이트 4(tests, 2 파일). 사용자 dev 환경 pytest 통과 확인 후 `docs/phase1-agent-teams-migration` 로 PR/머지 예정.
- [x] **GitHub Actions Node 20 → Node 24 bump (Phase 1 완료, 2026-04-21)**: `chore/gha-node24-bump` 브랜치에서 8종 15건 액션 버전 상향 + Phase 3 CLI fallback 스크립트(`scripts/ci/install_{syft,grype,cosign}.sh`) 선제 작성 + `agent_docs/development-policies.md §13.1` contingency plan SSOT 등록. 잔여 3종(`anchore/sbom-action@v0`, `anchore/scan-action@v6`, `sigstore/cosign-installer@v4.1.1`) 은 업스트림이 Node 20 runtime 을 유지 중이라 미전환. **Phase 2 월 단위 모니터링 필요** — 매월 1일 위 3개 releases 페이지를 확인하고 Node 24 전환 태그가 나오면 즉시 1줄 bump PR. **Phase 3 강제 deadline = 2026-08-01** (9-16 hard removal 45일 전) — 그 시점에도 업스트림이 안 움직이면 CLI fallback 으로 전환. 상세 타임라인/위험/치환 패턴: `agent_docs/development-policies.md §13.1`.
- [ ] **Python 버전 정합성**: Dockerfile `python:3.11-slim` ↔ `pyproject.toml` `target-version = "py310"` 불일치 정리. 팀메이트 4 조사 후 팀메이트 3 확정.
- [ ] **최근 30커밋 요약**: `git log --oneline -30` 분석 결과를 본 문서에 추가 (Phase 1 2차 세션 스코프).
- [ ] **.claude/rules/ 경로별 가드**: `config/`, `backtest-engine/`, `scheduler/`, `api-routes/`, `tests/`, `docs/` 별 슬림 규칙 파일 생성 (Phase 1 2차 세션 스코프).
- [ ] **CD 조건부 restart 자동화**: bind-mount 파일 변경 감지 후 `restart` + `/api/v1/rules` 어서트를 `cd.yml` 에 안착 (development-policies.md §8 참조).
- [ ] **AST 기반 정적 검사기 커버리지 확장**: `check_loguru_style.py` 외 `check_bool_literals.py`, `check_rbac_coverage.py` 도 regex 결손이 없는지 재검토.
- [ ] **Phase 2 마이그레이션 계획**: 외부 참고(StyleSeed, Graphify, agent-skills) 도입 심사 및 ADR 작성.

---

## 10. 본 문서 유지 책임

- **본 문서는 200줄 이하**를 유지합니다. 세부 규칙이 늘어나면 본 문서에 추가하지 않고 `agent_docs/` 해당 파일로 편입합니다.
- 규칙 변경은 **반드시 `agent_docs/development-policies.md` 를 먼저 수정**하고, 본 문서는 요약/포인터만 갱신합니다.
- `docs/archive/CLAUDE-pre-phase1-migration.md` 는 마이그레이션 이전 원본 아카이브. 역사적 참조 용도이며 수정 금지. 로컬 `*.bak` 은 리드 임시 작업용으로 `.gitignore:56` 에 의해 계속 ignored.
- 본 문서 수정은 리드 전용 (governance.md §2.5).
