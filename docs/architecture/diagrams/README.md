# AQTS 코드베이스 다이어그램

본 디렉토리는 AQTS 백엔드의 **모듈 의존성** 과 **런타임 wiring** 을 시각화한 산출물입니다. 목적은 (1) 중복 구현 탐지, (2) 누락된 연결 탐지, (3) 레이어 위반 식별입니다.

## 재생성 방법

```bash
# 시스템 의존성 (최초 1회)
brew install graphviz            # macOS
# sudo apt-get install graphviz  # Linux

# Python 의존성
pip install -r backend/requirements-dev.txt

# 재생성
python scripts/generate_diagrams.py

# 드리프트 체크 (CI 용)
python scripts/generate_diagrams.py --check
```

`--check` 모드는 기존 산출물과 재생성 결과가 다르면 exit 1. PR 에서 "다이어그램 업데이트 잊음" 회귀를 잡기 위함.

---

## 읽는 순서 (지하철 노선도 비유)

다이어그램은 3 계층으로 구성되며 **위에서 아래로** 읽는 것을 권장합니다.

### 1 계층 — `module-deps.cross-team.mmd` (환승역만 보이는 노선도)

4 개 팀 + 공유(shared) subgraph 에 **팀 간 엣지를 가진 경계 모듈만** 배치. 팀 간 엣지는 굵은 선(`==>`) 으로 강조. 누적 노드 수 약 20~60.

**용도**: 오연결·팀 간 스파게티를 한눈에 확인. 가장 먼저 열어볼 파일.

### 2 계층 — `module-deps.overall.svg` (구글맵)

pydeps 가 생성한 전체 백엔드 렌더링. 80+ 모든 모듈 포함. 클러스터는 Python 패키지 경계.

**용도**: 특정 모듈의 전역 위치와 전이 의존성 파악.

**`module-deps.overall.dot`** — 동일 그래프의 Graphviz DOT 소스. PR diff 가 가능하도록 커밋됨. SVG 는 로컬 Graphviz 버전에 따라 바이트 단위로 달라질 수 있어 `--check` 는 DOT 만 검증합니다.

### 3 계층 — `module-deps.team{1,2,3}-*.mmd` (동네 지도)

팀별 **내부 엣지만** (팀 간 엣지 제외) 포함. 각 팀 단위 구조 리뷰용.

| 파일 | 팀 | 주요 영역 |
|---|---|---|
| `module-deps.team1-strategy.mmd` | 팀 1 | strategy_ensemble, backtest_engine, oos, hyperopt, param_sensitivity, quant_engine, rl |
| `module-deps.team2-scheduler.mmd` | 팀 2 | scheduler_*, notification, monitoring, reconciliation_*, circuit_breaker 등 |
| `module-deps.team3-api.mmd` | 팀 3 | main, api, db, alembic, audit, order_executor, portfolio_*, data_collector 등 |

**팀 4 (tests) 파일이 없는 이유**: 테스트 모듈끼리는 서로 import 하지 않으므로 within-team 엣지가 0. 팀 4 구조는 전체 뷰(`module-deps.overall.svg`) 에서 `tests.*` 클러스터로 확인합니다. 팀 4 소스→프로덕션 import 는 cross-team 뷰에서도 제외(잡음) — `CROSS_TEAM_SCOPE = (1, 2, 3)`.

---

## 런타임 wiring (`wiring.*.mmd`)

정적 import 분석으로 포착할 수 없는 **동적 wiring** (DI, `asyncio.create_task`, APScheduler job 등록, middleware register) 을 Mermaid 로 수작업. CLAUDE.md §6 "정의했다 ≠ 적용했다" 원칙 반영.

| 파일 | 유형 | 내용 |
|---|---|---|
| `wiring.lifespan-startup.mmd` | sequenceDiagram | `backend/main.py:106-320` FastAPI lifespan 시작 순서 |
| `wiring.scheduler-startup.mmd` | sequenceDiagram | `backend/scheduler_main.py:86-154` scheduler 프로세스 시작 순서 |
| `wiring.notification-5layer.mmd` | flowchart LR | CLAUDE.md §6 5 레이어 정의↔적용 매트릭스 |
| `wiring.request-rbac.mmd` | flowchart TD | CORS→RequestLogging→Prom→OTel→RBAC dep→handler |
| `wiring.reconciliation.mmd` | flowchart LR | ReconciliationRunner provider fan-out |

---

## 팀 색상 범례 (`cross-team.mmd` 및 팀별 파일 공통)

| 팀 | 색상 (fill/stroke) | 주요 영역 |
|---|---|---|
| 팀 1 (Strategy / Backtest) | 파랑 (#cfe / #0bf) | 전략·백테스트·OOS·하이퍼옵트 |
| 팀 2 (Scheduler / Ops / Notification) | 주황 (#fea / #f80) | 스케줄러·알림·메트릭 |
| 팀 3 (API / RBAC / Security) | 초록 (#cfc / #0a0) | API·DB·주문·포트폴리오·데이터수집 |
| 팀 4 (Tests / Doc-Sync) | 회색 (#ddd / #666) | 테스트·정적 검사기 |
| 공유 (Shared, 팀 0) | 흰색 (#fff / #333) | config·utils·pipeline 등 리드/공동 영역 |

팀 0 (공유) 는 `agent_docs/governance.md §2.3` 에 특정 팀으로 명시되지 않은 모듈을 의미합니다. 향후 명시화 되면 이 분류가 팀 1~4 로 이동합니다.

---

## 레이어 위반 리포트 (`layer-violations.txt`)

다음 3 종의 import 경계 위반을 감지합니다:

1. `backend.db.*` 가 `backend.api.*` import (DB → API 역전)
2. `backend.core.*` 가 `backend.api.*` import (코어 → API 역전)
3. `backend.core.utils.*` 가 `backend.core.*` 의 도메인 모듈 import (utils → domain 역전)

**v1 정책 (현재)**: soft-warn. 리포트만 작성되며 스크립트는 성공 종료. 첫 clean run 이후 error 승격 예정 (CLAUDE.md §9 TODO).

정당한 사유로 위반이 필요한 경우, 본 README 에 "의도된 예외" 섹션을 만들어 사유·승인자·재검토 일자를 기록하고 스크립트의 화이트리스트에 등록합니다.

---

## 정적 분석의 한계 (꼭 알아둘 것)

pydeps 와 커스텀 AST 패스는 **정적 import** 만 추적합니다. 다음 wiring 은 **보이지 않으므로** `wiring.*.mmd` 에서 수작업으로 보완:

- `asyncio.create_task(...)` 로 시작되는 백그라운드 태스크 (예: `_alert_retry_loop`, `_exchange_rate_loop`)
- FastAPI `Depends(...)` 의존성 주입
- APScheduler `add_job(callback)` 동적 콜백 등록
- `importlib` 기반 플러그인 로딩
- 모듈 레벨 싱글턴 주입 (`set_X(...)`, `register_X(...)`)

이 공백을 메우는 것이 정확히 런타임 wiring 다이어그램 (④) 의 존재 이유입니다.

---

## ADR-004 (Graphify Pilot) 와의 관계 — 미편향 선언

CLAUDE.md §9 에 **ADR-004 Graphify Pilot** 이 Phase 2 외부 도구 심사 후보로 올라 있습니다. 본 디렉토리의 산출물은 **ADR-004 심사와 무관**하며, OSS 도구(pydeps + Mermaid) 로만 구성되어 Graphify 평가를 선점하거나 편향시키지 않습니다.

ADR-004 가 Stage 2 심사를 통과할 경우, 본 산출물은 **비교 baseline** 으로 활용될 수 있습니다 — "Graphify 가 pydeps + 수작업 Mermaid 대비 어떤 부가가치를 제공하는가" 를 정량 평가하는 기준선. 다만 이는 ADR-001 §2.2 sandbox 프로세스의 판정 권한을 침해하지 않으며, 본 디렉토리는 Graphify 채택 여부와 독립적으로 유지·확장됩니다.

---

## 향후 작업 (이 PR 범위 외)

- [ ] `layer-violations.txt` soft-warn → error 승격 (첫 clean run 이후)
- [ ] CI `.github/workflows/doc-sync-check.yml` 에 `generate_diagrams.py --check` 스텝 추가 (팀 2 영역)
- [ ] 루트의 레거시 PNG 4장 (`aqts_system_architecture.png`, `aqts_investment_decision.png`, `aqts_rebalancing_flow.png`, `Phase.png`) 정리 (별도 PR)
- [ ] 공유(팀 0) 분류된 모듈들의 명시적 소유 배정 검토 (agent_docs/governance.md 갱신)
- [ ] 2026-05-06 ADR-002 Pilot lockout 해제 후 팀 4 로 소유권 이관

---

## 소유권

- **현재 (2026-04-23 ~ 2026-05-06)**: 리드 임시 소유 (governance.md §2.5 기반, 팀 4 ADR-002 Pilot lockout 기간)
- **2026-05-06 이후**: 팀 4 (Tests / Doc-Sync) 로 정식 이관

변경 제안은 리드에게 메일박스로 `[Ask]` 접두로 전달합니다.
