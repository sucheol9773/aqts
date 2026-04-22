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
| 3 | API / RBAC / Security | `backend/api/`, `backend/db/`, `backend/alembic/`, `backend/core/{audit, order_executor, portfolio_*, idempotency, trading_guard, data_collector}` |
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
- **greenlet transitive dep silent miss (2026-04-21, `d4eb70e`)**: `backend/requirements.txt` 에 `greenlet` 미명시 → 환경에 따라 SQLAlchemy 2.0 async 전이 설치 누락 → `ValueError: the greenlet library is required` 가 `orders.py` 의 광범위 except 블록에서 `success=False` 로 삼켜짐. `greenlet==3.4.0` 명시 고정. development-policies.md §8 Silence Error 패턴.
- **vendored 디렉토리 정적 검사기 제외 파편화 (2026-04-21, `c209551`)**: `check_bool_literals.py`(226 false positives)·`check_loguru_style.py`(15s sandbox timeout) 가 각자 다른 방식으로 `.venv`/`site-packages` 를 처리. `scripts/_check_utils.py::iter_python_files` 로 제외 로직 SSOT 집약 (`os.walk` + `dirnames[:]` mutation 관용구). `docs/operations/static-checker-venv-audit-2026-04-21.md` (OPS-017).
- **로컬 정적 검사기 버전 ↔ CI pin 불일치 (2026-04-21, `bfb5ce0` / `3497f2a`)**: 로컬 `black 26.x` 로 재포맷한 커밋이 CI `black==24.4.2` 에서 26 파일 drift 로 실패. 로컬 개발자는 `pip install -r backend/requirements-dev.txt` 로 CI 와 동일 버전 고정. development-policies.md §3.3 + `docs/operations/dev-deps-split-2026-04-21.md` (OPS-018).

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
- [x] **Python 버전 정합성 (완료, 2026-04-21)**: `chore/python-version-align` 브랜치에서 3축 drift(로컬 pyenv 3.10.15 / pyproject py310 / CI·Docker 3.11) 를 py311 로 통일. `backend/pyproject.toml` 의 ruff+black `target-version` 을 `py311` 로 상향, `.python-version` 을 `3.11.14` (Dockerfile 주석 lower bound) 로 tracked 추가, `README.md` 라인 67 의 "3.11.9" 표기를 "3.11.14+" 로 교정, `agent_docs/architecture.md §1.2` 에 정렬 사유·결정 근거 기록. 위험도 1/5 — 런타임/Docker/cosign 무변경, 메타데이터만 정렬. 3.11 전용 문법(`typing.Self`, `except*`, `TaskGroup`) 은 현재 미사용이나 향후 도입 시 즉시 가능. 3.11 EOL = 2027-10 (18개월 여유).
- [x] **최근 30커밋 요약 (완료, 2026-04-21)**: `docs/claude-md-commit-summary-30` 브랜치에서 `git log --oneline -30` 분석 후 §5 "최근 회귀 사례" 에 3개 경계 포인트 추가 — (1) greenlet transitive dep silent miss, (2) vendored 디렉토리 정적 검사기 제외 파편화, (3) 로컬 vs CI lint pin 불일치. 단발 bug fix 는 §5 자격 미달로 제외하고 "재발 가능성이 있는 패턴" 만 선별. 나머지 27 커밋은 정상 흐름(feature/merge/format)으로 §5 에 기록할 경계점 없음.
- [x] **.claude/rules/ 경로별 가드 (완료, 2026-04-21)**: `docs/claude-rules-path-guards` 브랜치에서 6개 경로별 규칙 파일 생성 — `backtest-engine.md`(팀 1), `scheduler.md`(팀 2), `api-routes.md`(팀 3), `tests.md`(팀 4), `config.md`(혼합: 리드+팀 1/2), `docs.md`(혼합: 리드+팀 1/2/3/4). YAML frontmatter `paths:` 로 경로 스코프 지정하며, Claude Code 가 해당 경로 편집 시 자동 로드. SSOT 는 `agent_docs/development-policies.md` / `governance.md` 에 유지하고 본 규칙 파일은 요약/포인터 + 소유권 경계 명시. 상세 구조: `agent_docs/governance.md §2.6`.
- [x] **CD 조건부 restart 자동화 (완료, 2026-04-16, 체크박스 갱신 2026-04-21)**: `.github/workflows/cd.yml` 의 `Prometheus config/rules drift 시 재기동` 스텝(현재 라인 438-516, Step 3.4)에서 구현 완료. 핵심 로직 — 서버의 git 으로 `git diff --name-only ${PREV_SHA} HEAD -- 'monitoring/prometheus/prometheus.yml.tmpl' 'monitoring/prometheus/rules/'` 를 수행하여 bind-mount 파일 변경 여부를 산출하고, 변경이 감지되면 `docker compose restart prometheus </dev/null` + 30초 내 `curl http://localhost:9090/api/v1/rules` 응답의 `data.groups` 길이가 1 이상인지 어서트. 어서트 실패 시 `exit 1` 로 `Post-deploy verification` 이후 단계가 실패하여 rollback 경로로 진입. `PREV_SHA='none'` (최초 배포 / 서버 재프로비저닝 직후) 및 git 에서 resolve 되지 않는 SHA 는 `CHANGED=1` 로 강제 재기동 경로로 빠짐. 설계 근거·회귀 방어선: `docs/operations/cd-auto-prune-2026-04-16.md §3.4, §4.2`. 2026-04-16 회귀(`ed36573` 배포 후 `prometheus.yml.tmpl` 만 수정한 커밋이 compose change-detection 을 우회하여 알림 9 그룹 39 rule 전체가 구 config 상태로 약 1시간 silent miss)에 대한 공식 방어선.
- [x] **AST 기반 정적 검사기 커버리지 확장 (완료, 2026-04-22)**: 세 검사기 모두 AST 기반 구현 + 회귀 테스트 하니스 확보. Stage 1 (`check_loguru_style.py`, 2026-04-15), Stage 2 (`check_bool_literals.py` regex → AST 전환, `chore/check-bool-literals-ast` → PR #19, `test_check_bool_literals.py` 27 tests), Stage 3 (`check_rbac_coverage.py` 테스트 하니스, `chore/check-rbac-coverage-tests`, `test_check_rbac_coverage.py` 21 tests). Stage 2 는 regex 4대 결손(중첩 괄호·멀티라인·문자열 리터럴 false positive·비교 순서 역전) 을 AST 노드 판정으로 구조적 해소 + `_BOOL_LITERAL_TOKENS` 로 enum-style 멤버십 통과(Codex P2 회귀 방어). Stage 3 은 6 그룹 구조(정책 하위 호환 / 위반 검출 / 오탐 방지 / 구문 오류 / 실제 레포 / main() 진입)로 Stage 2 와 동일 패턴 재사용. `test_get_current_user_only_is_still_flagged` 로 "인증 ≠ 인가" 원칙 자동 집행 고정, `test_whitelist_entries_refer_to_existing_files_and_functions` 로 stale whitelist silent miss 방지. 작업 기록: `docs/operations/check-bool-literals-ast-2026-04-22.md` (OPS-019) + `docs/operations/check-rbac-coverage-tests-2026-04-22.md` (OPS-020).
- [x] **Dev 의존성 파일 분리 (완료, 2026-04-21)**: `chore/dev-deps-split` 브랜치에서 옵션 A 로 해소. `backend/requirements-dev.txt` 신설(lint 도구 only — ruff/black, `-r requirements.txt` 의도적 미포함으로 CI lint 잡이 runtime 의존성을 설치하지 않도록 절충), `.github/workflows/ci.yml` 의 `Install linters` 스텝을 `pip install ruff==0.5.0 black==24.4.2` → `pip install -r backend/requirements-dev.txt` 로 전환, `cache-dependency-path` 도 `backend/requirements-dev.txt` 로 갱신. 로컬 개발자 셋업은 README §2.4 "로컬 개발자 lint/format 도구 설치" 에 `pip install -r backend/requirements.txt -r backend/requirements-dev.txt` 형태로 문서화. 작업 기록: `docs/operations/dev-deps-split-2026-04-21.md` (OPS-018). 옵션 B (PEP 621) 는 `[project]` 섹션 전체 신설 + installable package 로 전환이 선행되어야 해서 범위 초과로 미채택.
- [ ] **pip-audit 하드코딩 해소 (발견 2026-04-21)**: `.github/workflows/ci.yml` 라인 89 의 `pip install pip-audit==2.7.3` 가 여전히 워크플로에 하드코딩되어 있음. 용도가 lint/format 과 다른 "security scan" 이라 Dev 의존성 분리 커밋에서 의도적으로 제외. 후속 커밋에서 `backend/requirements-security.txt` 를 신설하거나, dev/security 하위 구분 대신 `requirements-dev.txt` 의 `## ── Security Scanning ──` 섹션으로 병합할지 결정 필요. 위험도 낮음, 예상 수정 범위 2 파일.
- [ ] **lxml 6.1.0 업그레이드 (CVE-2026-41066 후속, 발견 2026-04-22)**: `chore/pip-audit-ignore-lxml-xxe` 브랜치에서 `backend/.pip-audit-ignore` 에 `GHSA-vfmq-68hx-4jfw` 를 만료일 2026-06-06 으로 등록하여 CI 블록 우선 해소. 현재 앱 코드의 `iterparse`/`ETCompatXMLParser`/`etree.parse`/`etree.fromstring` 직접 호출은 0 건이며, 유일한 lxml 간접 사용(`backend/core/data_collector/news_collector.py:222/224` 의 `BeautifulSoup(raw, "lxml").get_text()`) 은 HTML 파서 경로로 취약 sink 외. 만료일 이전에 반드시 수행해야 할 후속 작업: (1) `backend/requirements.txt:71` 의 `lxml==5.2.2` → `lxml==6.1.0` 업그레이드, (2) `beautifulsoup4==4.12.3` 과 lxml 6.x 조합 smoke test — 특히 `news_collector._parse_entry` 의 RSS 본문 파싱이 깨지지 않는지 실측(`pytest tests/ -k news_collector` + 실 RSS feed 1~2개 수동 pull), (3) 업그레이드 후 `.pip-audit-ignore` 의 GHSA-vfmq-68hx-4jfw 항목 삭제, (4) CLAUDE.md §9 본 TODO 를 [x] 로 전환하고 retrospective 문서 필요 시 `docs/operations/lxml-6.1.0-upgrade-<date>.md` (OPS-021) 로 기록. 우선순위: 만료일 2주 전(2026-05-23) 까지 완료 목표. silent miss 방어: 만료일 이전에 누군가 `iterparse` 또는 `ETCompatXMLParser` 를 새로 도입하면 본 ignore 의 "코드 경로 미사용" 전제가 깨지므로, 만약 향후 해당 sink 사용이 필요해지는 PR 이 생기면 그 PR 이 곧바로 lxml 업그레이드 PR 로 합쳐져야 한다.
- [x] **Phase 2 마이그레이션 계획 — ADR-001 프레임워크 확립 (완료, 2026-04-22)**: `docs/adr-001-phase2-entry-gate` 브랜치에서 `docs/architecture/adr-001-phase2-entry-gate.md` 신설. Phase 1 Path A Step 6 의 공식 산출물. 핵심 결정: (1) Phase 2 는 외부 참고 도구 심사 + Phase 1 관찰 정책의 strict 승격 두 축으로 한정, (2) 도구 1건당 4 단계(Proposal → Sandbox 14일 → Limited Rollout 30일 → Full Adoption 14일 관찰) 심사 프로세스, (3) 각 단계 Exit Criteria / Stop 조건 / §2.3 평가표 템플릿 명시, (4) 개별 도구 채택은 ADR-002 (anthropic-skills, 우선순위 1) / ADR-003 (StyleSeed, AQTS 는 backend-only 이므로 Rejected 예정) / ADR-004 (Graphify Pilot) 로 위임, (5) 동시 착수 금지 (한 번에 한 ADR 만 진행) 로 회귀 시 책임 분리. `agent_docs/governance.md §5` 마지막 문단에 본 ADR 링크 추가. **후속 작업**: ADR-002 (anthropic-skills 단계적 채택) 를 Phase 2 첫 개별 도구 심사로 착수.
- [x] **data_collector 전체 팀메이트 3 일괄 배정 (완료, 2026-04-22)**: `docs/governance-data-collector-team3` 브랜치에서 `backend/core/data_collector/` 소유 경계를 `kis_*` 에서 디렉토리 전체로 확장. 배경: lxml 6.1.0 업그레이드 smoke test 가 `news_collector._parse_entry` (RSS) 에 의존하는데, 기존 §2.3 은 `kis_*` 만 팀 3 으로 명시하여 `news_collector.py`·`social_collector.py`·`economic_collector.py`·`financial_collector.py`·`daily_collector.py`·`market_data.py`·`realtime_manager.py`·`corp_action.py` 8 파일이 소유자 불명 상태였음. 확장 근거: (1) 모든 collector 가 외부 API/RSS 진입점으로 공급망·인증·RBAC 경계와 맞닿음, (2) `kis_*` 와 동일한 `httpx`/`feedparser` 패턴 사용, (3) ADR-002 이후 외부 도구 심사 증가 시 일관된 단일 소유자 필요. 변경 파일: `agent_docs/governance.md §2.3` + §2.6 표, `.claude/rules/api-routes.md` frontmatter `paths:`, 본 CLAUDE.md §3 요약 표. ADR-002 진입 전 선결 조건.

---

## 10. 본 문서 유지 책임

- **본 문서는 200줄 이하**를 유지합니다. 세부 규칙이 늘어나면 본 문서에 추가하지 않고 `agent_docs/` 해당 파일로 편입합니다.
- 규칙 변경은 **반드시 `agent_docs/development-policies.md` 를 먼저 수정**하고, 본 문서는 요약/포인터만 갱신합니다.
- `docs/archive/CLAUDE-pre-phase1-migration.md` 는 마이그레이션 이전 원본 아카이브. 역사적 참조 용도이며 수정 금지. 로컬 `*.bak` 은 리드 임시 작업용으로 `.gitignore:56` 에 의해 계속 ignored.
- 본 문서 수정은 리드 전용 (governance.md §2.5).
