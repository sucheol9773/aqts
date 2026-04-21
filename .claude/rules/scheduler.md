---
paths:
  - "backend/scheduler_main.py"
  - "backend/core/trading_scheduler.py"
  - "backend/core/scheduler_handlers.py"
  - "backend/core/scheduler_heartbeat.py"
  - "backend/core/scheduler_idempotency.py"
  - "backend/core/market_calendar.py"
  - "backend/core/periodic_reporter.py"
  - "backend/core/daily_reporter.py"
  - "backend/core/reconciliation*.py"
  - "backend/core/notification/**/*.py"
  - "backend/core/monitoring/**/*.py"
  - "backend/core/emergency_monitor.py"
  - "backend/core/circuit_breaker.py"
  - "backend/core/graceful_shutdown.py"
  - "backend/core/health_checker.py"
  - "docker-compose*.yml"
  - "monitoring/**/*.yml"
  - ".github/workflows/*.yml"
---

# Scheduler / Ops / Notification 영역 가드

**소유**: 팀메이트 2 (Scheduler / Ops / Notification). 상세: `agent_docs/governance.md §2.2`.
**SSOT**:
- 알림 파이프라인: `agent_docs/development-policies.md §14` + `docs/architecture/notification-pipeline.md`
- CD/공급망: `agent_docs/development-policies.md §13`, `§13.1` (Node 24 contingency), `§15` (SSH heredoc stdin 격리)
- 아키텍처: `agent_docs/architecture.md §3, §7, §12`

## 알림 파이프라인 5-레이어 Wiring Rule

다음 레이어를 **정의했다 ≠ 적용했다** 원칙으로 반드시 wiring 검증:

| 레이어 | 정의 위치 | 적용 위치 |
|---|---|---|
| 상태 머신 메서드 | `alert_manager.py` | `_dispatch_via_router`, `dispatch_retriable_alerts` |
| NotificationRouter 인스턴스 | `fallback_notifier.py` | `backend/main.py` lifespan `set_notification_router` |
| 재시도 루프 `_alert_retry_loop` | `backend/main.py` 함수 정의 | lifespan `asyncio.create_task` |
| Prometheus 메트릭 훅 | `backend/core/monitoring/metrics.py` | `NotificationRouter.dispatch` try/finally |
| 메타알림 규칙 `aqts_alert_pipeline` | `monitoring/prometheus/rules/aqts_alerts.yml` | Alertmanager 로드 |

**배포 후 수동 검증 (모두 통과해야 wiring 완료)**:

```bash
docker compose logs backend --tail=500 | grep 'NotificationRouter wired'
docker compose logs backend --tail=500 | grep 'AlertRetryLoop started'
curl -s http://<backend>/metrics | grep -c 'aqts_alert_dispatch'   # 0 이면 결손
```

## 최근 회귀 경계 포인트

- **KST 통일 (2026-04-15)**: Redis 스냅샷 키는 `today_kst_str()` 사용. 테스트 fixture 도 동일 key. UTC 혼용 silent miss 방지.
- **scheduler stdout block-buffering**: compose `environment:` 에 `PYTHONUNBUFFERED: "1"` 유지 — 누락 시 `docker compose logs scheduler` 가 비어 보인다.
- **loguru %-format mismatch**: `logger.info("...%d...", n)` posarg 스타일 금지. f-string 또는 loguru `{}` 포맷만 사용 (AST 검사기 `check_loguru_style.py` 가 차단).
- **Prometheus `rule_files` 상대경로 silent miss**: 절대경로로 고정. 상대경로 resolve 기준이 config 이동 시 바뀌면 rule 전체가 로드 실패.
- **compose change-detection 미감지**: bind-mount 파일 내용만 수정한 배포는 `docker compose up -d` 가 recreate 하지 않음. CD 에서 조건부 `restart prometheus` + `/api/v1/rules` groups≥1 어서트 필수.
- **SSH heredoc stdin 소진**: `docker exec -i`, `-T` 없는 `docker compose run` 금지. fd 0 을 읽는 자식은 `</dev/null` 격리 필수 (`§15` 전체 목록 참조).

## 환경변수 Boolean 표기 표준

- `docker-compose*.yml` / `.github/workflows/*.yml` / `.env*` 의 bool 은 소문자 `"true"`/`"false"` 만 표준.
- Python 쪽 파싱은 `core.utils.env.env_bool()` 만 사용. ad-hoc `.lower() in (...)` 금지.
- 신규 bool 환경변수 추가 시 `scripts/check_bool_literals.py::BOOL_ENV_KEYS` 화이트리스트 갱신 + `docs/conventions/boolean-config.md` 예시 추가.

## 커밋 전 체크

```bash
cd backend && python -m ruff check . --config pyproject.toml
cd backend && python -m black --check . --config pyproject.toml
python scripts/check_bool_literals.py
python scripts/check_cd_stdin_guard.py
python scripts/check_loguru_style.py
cd backend && python -m pytest tests/ -q --tb=short   # 540s timeout 권장
```

## 소유권 경계

- `backend/main.py` lifespan 수정은 팀메이트 3 (API/RBAC) 와 공동 — `[Ask]` 메일박스.
- `backend/core/utils/env.py`, `backend/core/utils/time.py` 는 리드 전용 — `[Lead-Approval]`.
- RBAC 라우트, alembic, db/models 는 팀메이트 3 영역.
