# AQTS Agent Teams Governance

> Cowork → Claude Code Agent Teams 전환 이후의 **팀 운영 규칙**을 한 곳에 정리합니다. "무엇을 코딩하는가"(development-policies.md), "어디에 있는가"(architecture.md) 와 달리, 본 문서는 "누가, 어떤 순서로, 어떻게 협업하는가" 를 다룹니다.
>
> Phase 1 마이그레이션의 배경·결정은 커밋 `b656186` (`docs: Phase 1 — Cowork→Agent Teams 마이그레이션`) 의 메시지 본문과 `docs/archive/CLAUDE-pre-phase1-migration.md` (이관 이전 원본 스냅샷) 에 보존되어 있습니다. 본 문서는 **실행 시점의 단일 진실원천** 으로, 팀 소유권 체계는 §2, 외부 참고 자료 도입 심사 절차는 §5 를 기준으로 합니다. Phase 2 이후 외부 도구(StyleSeed, Graphify, agent-skills 등) 도입은 `docs/architecture/` 하위 ADR 로 작성하여 심사합니다.

---

## 1. 구성 레이어 (Anthropic 4-Layer 프레임)

Claude Code Agent Teams 는 다음 4개 레이어로 구성되며, AQTS 는 각 레이어에 구체적 매핑을 둡니다.

| 레이어 | 정의 | AQTS 매핑 |
|---|---|---|
| Model | 각 팀메이트가 사용하는 LLM | Claude Code 기본 (Opus/Sonnet 혼합). 리서치·리팩토링은 Opus, 일상 작업은 Sonnet 을 권장 |
| Harness | 세션 운영 방식 | **4개 독립 `claude` CLI 세션 (worktree 격리)**. 팀별 `../aqts-team{N}-<role>/` worktree + `team{N}/<type>/<slug>` 브랜치 prefix. `.claude/settings.json` (permissions/hooks) + `.mcp.json` (opt-in MCP) + `scripts/team/*.sh` (bootstrap/teardown/mailbox/wiring_smoke/pre_bash_guard) 공통 계약. 이전 Cowork `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1` + `Shift+Down` 팀메이트 순환 방식은 2026-04-22 OPS-023 migration (PR #28) 으로 폐기 |
| Tools | 사용 가능한 도구 | 파일 I/O, bash, git, `scripts/check_*`, `scripts/post_deploy_smoke.sh`, pytest, ruff, black |
| Env | 작업 환경 | 각 팀메이트는 `git worktree` 로 분리된 브랜치에서 작업. 공유 파일은 리드만 수정 |

---

## 2. 팀 구성 (4 Teammate 모델)

Phase 1 마이그레이션 커밋 `b656186` 에서 확정된 4-팀메이트 분배를 AQTS 실 디렉토리에 정렬합니다. (분배 근거의 역사적 맥락은 `docs/archive/CLAUDE-pre-phase1-migration.md` 참조.)

### 2.1 팀메이트 1 — Strategy / Backtest

- **소유**: `backend/core/strategy_ensemble/`, `backend/core/backtest_engine/`, `backend/core/oos/`, `backend/core/hyperopt/`, `backend/core/param_sensitivity/`, `backend/core/quant_engine/`, `backend/core/weight_optimizer.py`, `backend/config/ensemble_config.yaml`, `backend/config/ensemble_config_loader.py`, `scripts/run_backtest.py`, `scripts/run_hyperopt.py`, `scripts/run_walk_forward.py`
- **목표**: OOS Sharpe 개선, 임계값 튜닝, 게이트 통과율 모니터링
- **참고 문서**: [backtest-operations.md](./backtest-operations.md)

### 2.2 팀메이트 2 — Scheduler / Ops / Notification

- **소유**: `backend/scheduler_main.py`, `backend/core/trading_scheduler.py`, `backend/core/scheduler_handlers.py`, `backend/core/scheduler_heartbeat.py`, `backend/core/scheduler_idempotency.py`, `backend/core/market_calendar.py`, `backend/core/periodic_reporter.py`, `backend/core/daily_reporter.py`, `backend/core/reconciliation*.py`, `backend/core/notification/`, `backend/core/monitoring/`, `backend/core/emergency_monitor.py`, `backend/core/circuit_breaker.py`, `backend/core/graceful_shutdown.py`, `backend/core/health_checker.py`, `docker-compose*.yml`, `prometheus/`, `alertmanager/`, `.github/workflows/*.yml`
- **목표**: 알림 파이프라인 wiring 유지 (development-policies.md §14), KST 키 일관성 (§8.3), CD 안정화 (§15)
- **참고 문서**: [architecture.md §3, §7, §12](./architecture.md), `docs/operations/`, `docs/architecture/notification-pipeline.md`

### 2.3 팀메이트 3 — API / RBAC / Security

- **소유**: `backend/main.py` (lifespan 제외 리드 공동관리), `backend/api/`, `backend/db/models/`, `backend/db/repositories/`, `backend/alembic/`, `backend/core/audit/`, `backend/core/compliance/`, `backend/core/order_executor/`, `backend/core/trading_guard.py`, `backend/core/portfolio_manager/`, `backend/core/portfolio_ledger.py`, `backend/core/idempotency/`, `backend/core/data_collector/` (전체 — `kis_*` 외 `news_collector.py`·`social_collector.py`·`economic_collector.py`·`financial_collector.py`·`daily_collector.py`·`market_data.py`·`realtime_manager.py`·`corp_action.py` 포함. 2026-04-22 Phase 1 Path A 후속으로 경계 확장)
- **목표**: RBAC Wiring Rule 0 errors (development-policies.md §12), 공급망 서명 흐름 유지 (§13), 스키마-코드 동기 (alembic 006 재발 방지)
- **참고 문서**: [api_contracts.md](./api_contracts.md), [database_schema.md](./database_schema.md), `docs/security/`

### 2.4 팀메이트 4 — Tests / Doc-Sync / Static Checkers

- **소유**: `backend/tests/`, `scripts/check_*.py`, `scripts/post_deploy_smoke.sh`, `scripts/pre_deploy_check.sh`, `scripts/gen_status.py`, `docs/FEATURE_STATUS.md`, `docs/PRD.md`, `docs/YAML_CONFIG_GUIDE.md`, `docs/conventions/boolean-config.md`, `docs/backtest/*`, `docs/operations/*.md` (아카이브·런북)
- **목표**: pytest 0 fail + 0 warning, 문서-코드 싱크 유지, 정적 검사기(AST 기반) 커버리지 확장
- **참고 문서**: [development-policies.md §1, §3, §8, §9](./development-policies.md)

### 2.5 리드 (사용자 본인) 전용 변경 영역

다음 파일은 **리드 승인 필요** 이며, 팀메이트가 메일박스로 변경 제안을 보낸 뒤 리드가 직접 수정합니다.

- `CLAUDE.md` (본 문서와 교차 참조가 많아 부주의한 수정 시 드리프트 발생)
- `agent_docs/development-policies.md` (단일 진실원천)
- `backend/core/utils/env.py`, `backend/core/utils/time.py` (여러 팀이 의존)
- `backend/config/settings.py` (환경변수 스키마)
- `.env.example` (키 추가는 `check_bool_literals.py` 화이트리스트 동시 수정 필요)
- `docs/archive/CLAUDE-pre-phase1-migration.md` (마이그레이션 이전 원본 아카이브, 수정 금지)
- `docs/archive/` 이하 모든 파일 (역사적 스냅샷, 향후 Phase 2+ 마이그레이션 시 동일 경로 사용)

### 2.6 `.claude/rules/` 경로별 가드 (Phase 1 산출물, 2026-04-21)

Claude Code 의 공식 `.claude/rules/*.md` 기능을 사용해 **경로 편집 시 자동 로드되는 슬림 가드** 를 다음 6개 영역에 배치합니다. YAML frontmatter `paths:` 필드로 스코프를 지정하며, 세부 규칙은 본 문서 / `development-policies.md` 가 SSOT 입니다. 규칙 파일은 요약 + 포인터 + 소유권 경계만 담고 중복 서술을 피합니다.

| 파일 | 스코프 (frontmatter `paths`) | 소유 | 주요 내용 |
|---|---|---|---|
| `.claude/rules/backtest-engine.md` | `backend/core/{strategy_ensemble,backtest_engine,oos,hyperopt,param_sensitivity,quant_engine,weight_optimizer}/**/*.py`, `scripts/run_*.py` | 팀메이트 1 | 기대값 수정 금지, STRATEGY_RISK_PRESETS Wiring, 상태 전이 edge case |
| `.claude/rules/scheduler.md` | `backend/scheduler_main.py`, `backend/core/{notification,monitoring,scheduler*}/**/*.py`, `docker-compose*.yml`, `.github/workflows/*.yml` | 팀메이트 2 | 알림 파이프라인 5 레이어, 최근 회귀 경계(KST/PYTHONUNBUFFERED/loguru/prometheus 상대경로/SSH heredoc) |
| `.claude/rules/api-routes.md` | `backend/api/**/*.py`, `backend/db/**/*.py`, `backend/alembic/**/*.py`, `backend/core/{audit,order_executor,portfolio_*,idempotency,data_collector}/**/*.py`, `backend/core/trading_guard.py` | 팀메이트 3 | RBAC Wiring Rule, 공급망 서명 흐름, 스키마-코드 동기 |
| `.claude/rules/tests.md` | `backend/tests/**/*.py`, `scripts/check_*.py`, `scripts/post_deploy_smoke.sh`, `scripts/pre_deploy_check.sh`, `scripts/gen_status.py` | 팀메이트 4 | 테스트 기대값 수정 금지, silent miss 의심, AST 검사기 결손 패턴 |
| `.claude/rules/config.md` | `backend/config/**/*.py`, `backend/config/**/*.yaml`, `.env.example` | **혼합** (리드 + 팀 1/2) | bool 환경변수 표준, 설정값 일관성, 파일별 소유권 표 |
| `.claude/rules/docs.md` | `docs/**/*.md`, `agent_docs/**/*.md`, `CLAUDE.md`, `README.md` | **혼합** (리드 + 팀 1/2/3/4) | 문서-only 커밋 예외(§3.1), 파일별 소유권 표, CLAUDE.md 200줄 원칙 |

**운영 원칙**:

- 본 규칙 파일들은 SSOT 가 아닙니다. 규칙 변경은 반드시 `agent_docs/development-policies.md` (또는 본 문서) 를 먼저 수정하고, `.claude/rules/` 는 요약만 갱신합니다.
- nested CLAUDE.md 방식은 lazy-load 버그 이슈(#2571, #3529, #24987)가 있어 채택하지 않습니다. `.claude/rules/` 는 공식 purpose-built 기능입니다.
- 신규 경로/도메인이 생기면 본 표에 행을 추가하고 대응 `.claude/rules/<area>.md` 를 함께 생성합니다.

---

## 3. 워크플로 (작업 단위)

### 3.1 작업 시작

1. 리드가 메인 브랜치에서 작업 티켓을 할당한다.
2. 팀메이트가 `git worktree add ../aqts-<team>-<task> <branch>` 로 독립 워크트리를 만든다.
3. 작업 시작 전 `CLAUDE.md` 와 자기 담당 `agent_docs/*.md` 를 재확인한다.
4. 필요 시 Plan Mode 로 구현 계획을 먼저 수립하고, 리드에게 요약 승인 (고위험 변경 — 예: 알림 파이프라인, RBAC, 공급망 — 은 필수).

### 3.2 구현 중

- 파일 소유권을 위반하는 변경은 **금지**. 교차 파일 수정이 필요하면 메일박스로 해당 팀메이트에 위임한다.
- 공유 유틸(`core/utils/`) 수정 제안은 리드에게 메일박스 전달 후 리드가 commit.
- 상시 체크: `python scripts/check_rbac_coverage.py`, `python scripts/check_bool_literals.py`, `python scripts/check_loguru_style.py`, `python scripts/check_cd_stdin_guard.py` (해당 도메인 변경 시).

### 3.3 커밋 직전

development-policies.md §3 의 세 명령을 **실제로 실행**한다 (추측 금지):

```bash
cd backend && python -m ruff check . --config pyproject.toml
cd backend && python -m black --check . --config pyproject.toml
cd backend && python -m pytest tests/ -q --tb=short   # 540s timeout 권장
```

문서-only 커밋은 §3.1 의 예외 규칙을 따르되 최소 게이트(ruff/black + 관련 검사기) 는 생략 금지.

### 3.4 PR/머지

1. 커밋 메시지에 변경 이유 + 영향 범위 + 관련 문서 경로 명시.
2. 리드가 리뷰 후 머지. CD (`cd.yml`) 의 `cosign verify` 까지 성공해야 배포 완료로 간주 (development-policies.md §13).
3. 머지 후 발견된 warning/회귀는 발견 시점에 즉시 수정 — 다음 사람에게 넘기지 않는다 (development-policies.md §9).

### 3.5 배포 후 검증 (운영 변경 시)

development-policies.md §14.2 의 3종 확인:

1. `docker compose logs backend --tail=500 | grep 'NotificationRouter wired'`
2. `docker compose logs backend --tail=500 | grep 'AlertRetryLoop started'`
3. `curl -s http://<backend>/metrics | grep -c 'aqts_alert_dispatch'` (0 이면 결손)

---

## 4. 팀 간 통신 프로토콜

### 4.1 메일박스 (Agent Teams 기본)

- 긴급 차단/롤백 필요 시: **제목 `[P0]` 접두**, 본문에 영향 범위·재현 경로·임시 우회·필요한 팀 명시.
- 일반 협의: **제목 `[FYI]`** 또는 `[Ask]`. 응답은 가능한 한 같은 스레드에 append.
- 리드 승인 요청: **제목 `[Lead-Approval]`** + 제안 diff 요약 + 영향 받는 `agent_docs/` 파일 링크.

### 4.2 회고

- 주 1회 리드가 주간 싱크 세션 주재: 각 팀메이트가 완료/진행/블로커를 1분 단위로 보고.
- 회귀가 발생한 커밋은 회고에 포함: 원인 + 정적 방어선 확장 후속 작업을 티켓화.

---

## 5. 외부 자원 수용 정책

외부 프레임워크·스킬을 도입할 때 반드시 거쳐야 할 심사:

1. **라이선스**: 상용 사용 가능한지 확인.
2. **공급망 신뢰성**: `pip-audit` / `grype` 로 CVE 스캔. high 이상이면 머지 금지 (development-policies.md §13).
3. **Wiring 적용 여부**: 기능만 설치하고 호출하지 않으면 회귀. 사용 경로를 통합 테스트로 봉인 (development-policies.md §5).
4. **문서화**: 도입 이유·대안·롤백 경로를 `docs/architecture/` 하위에 ADR(Architecture Decision Record) 로 남긴다.

Phase 2 이후 외부 참고(StyleSeed, Graphify, agent-skills 등) 는 본 섹션의 1~4 기준으로 심사를 거쳐 단계적으로 도입하며, 각 도입 건은 `docs/architecture/` 하위 ADR 로 문서화합니다. **구체적 심사 프로세스 (4 단계: Proposal → Sandbox → Limited Rollout → Full Adoption) 와 평가표 템플릿은 [ADR-001 Phase 2 진입 gate 및 외부 참고 도구 심사 프레임워크](../docs/architecture/adr-001-phase2-entry-gate.md) 를 단일 진실원천으로 사용합니다.**

---

## 6. 비용·성능 주의

- Agent Teams 는 단일 세션 대비 **3~5 배** 토큰을 소모합니다. 2~3 명으로 시작해 숙련 후 4명 풀팀으로 확장합니다.
- 장시간 작업(하이퍼옵트, OOS) 은 로컬 `scripts/run_*` 로 수행하고 결과만 팀메이트 컨텍스트로 가져옵니다. LLM 세션 내에서 직접 오랜 계산을 돌리지 않습니다.

---

## 7. 보안 기본 원칙

- `.env` 실값은 어떤 문서·프롬프트·팀메이트 컨텍스트에도 포함하지 않는다. `.env.example` 키 이름만 인용한다.
- API 키·계좌번호·개인정보는 하드코딩 금지 (imported_knowledge custom_instructions).
- RBAC 라우트 추가 시 `check_rbac_coverage.py` 0 errors 를 커밋 전에 확인 (development-policies.md §12).
- 공급망 검증은 `cosign verify` 가 CD 에서 실패 시 즉시 중단 (development-policies.md §13).

---

## 8. 본 문서 유지 책임

- 팀 구성 변경, 새 팀메이트 추가, 외부 도구 도입 결정은 **리드**가 본 문서에 기록합니다.
- 절차 변경(§3, §4) 은 리드 + 해당 영향 팀메이트 공동으로 갱신합니다.
- 세부 코딩 규칙은 반드시 [development-policies.md](./development-policies.md) 로 돌려보내고 본 문서에 중복 정의하지 않습니다.
