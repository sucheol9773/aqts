# Scheduler 멱등성 + Daily Report 안전망

## 1. 배경

CD 배포(워크플로 #91 이후) 직후 텔레그램으로 일일 리포트가 **하루 3~4회 중복 전송**되고,
일부 리포트의 PnL/포트폴리오 가치가 **0원 / -100%** 로 표시되는 회귀가 관찰되었다.

원인은 두 가지가 결합된 형태였다.

| # | 위치 | 문제 |
|---|------|------|
| 1 | `core/trading_scheduler.py` | `_state.events_executed_today` 가 인메모리 상태로만 관리되어, 컨테이너 재시작 시 비워진다. `_find_next_event` 는 "지나간 시각이지만 아직 실행되지 않은 이벤트" 를 즉시 다시 트리거하므로, CD 배포가 발생할 때마다 같은 거래일의 `POST_MARKET` 이 다시 실행된다. |
| 2 | `core/scheduler_handlers.py::handle_market_close` | KIS 잔고 조회가 실패하거나 빈 응답을 반환하면 `portfolio_value=0`, `cash_balance=0`, `positions=[]` 인 snapshot 을 그대로 Redis 에 덮어쓴다. 이후 `handle_post_market` 이 그 0 snapshot 을 읽어 -100% 리포트를 발사한다. |

이 문서는 두 회귀에 대한 동시 수정을 기록한다.

## 2. 설계

### 2.1 3-layer 방어

1. **Idempotency (1차 방어)** — Redis 키로 같은 거래일의 동일 이벤트 재실행 차단.
2. **Market-close skip (2차 방어)** — KIS 응답이 비정상이면 snapshot 자체를 저장하지 않아 직전 거래일의 정상 데이터를 보존.
3. **Post-market safety net (3차 방어)** — snapshot 이 부재하거나 전부 0 이면 텔레그램 발송을 명시적으로 skip.

세 층 중 어느 한 층만 성공해도 0원 리포트가 텔레그램으로 나가지 않는다.

### 2.2 Idempotency 키 설계

- 모듈: `backend/core/scheduler_idempotency.py`
- 키 형식: `scheduler:executed:{KST date}:{event_type}` (예: `scheduler:executed:2026-04-07:POST_MARKET`)
- 값: 실행 시각 ISO8601 (디버깅/관찰용)
- TTL: 다음 KST 자정까지 (`min 60s` clamp)
- API: `mark_executed`, `is_executed`, `load_executed_for_date`, `clear_for_date`
- Redis 장애 시 graceful degradation: `is_executed` 는 `False`, `mark_executed` 는 `False` 반환 → 인메모리 `events_executed_today` 가 단독 폴백.

### 2.3 trading_scheduler 와이어링

- `start()`: 부팅 직후 `load_executed_for_date(today)` 를 호출해 인메모리 상태를 복원.
- `_execute_event()` 진입 시: `is_executed` 로 사전 확인 → 이미 실행됨이면 `result["skipped"]=True` 로 반환.
- `_execute_event()` 성공 직후: `mark_executed` 로 영속화.

### 2.4 market_close skip 가드

```python
snapshot_is_empty = portfolio_value_end == 0 and cash_balance == 0 and not positions_data
if result.get("kis_error") or snapshot_is_empty:
    # snapshot 저장 skip — 직전 거래일 데이터 보존
    result["snapshot_saved"] = False
    result["snapshot_skip_reason"] = "kis_error" or "empty_response"
```

### 2.5 post_market safety net

```python
snapshot_missing_or_empty = portfolio_value_end == 0 and cash_balance == 0 and not positions_data
if snapshot_missing_or_empty:
    result["report_skipped"] = True
    result["skip_reason"] = "snapshot_missing_or_empty"
    return result   # 텔레그램/리포트 저장 진입 자체 차단
```

## 3. 테스트

`backend/tests/test_scheduler_idempotency.py` (8 케이스):

| 클래스 | 검증 항목 |
|--------|-----------|
| `TestSchedulerIdempotency` | mark/is roundtrip, load_executed_for_date 가 같은 날짜만 반환, clear_for_date 가 다른 날짜를 건드리지 않음, Redis 장애 시 안전 기본값 반환 |
| `TestMarketCloseSkipsOnEmpty` | KIS 빈응답 + KIS 예외 두 케이스 모두 `redis.set` 미호출 |
| `TestPostMarketSafetyNet` | snapshot 부재 + 전부 0 두 케이스 모두 `send_telegram_report` 미호출 |

기존 `tests/test_scheduler_handlers_extended.py` 의 5개 post_market 테스트는 안전망에 막히지 않도록
**입력 snapshot 만** 실제와 유사한 값으로 보강했다 (기대값은 그대로 — 안전망 우회가 본 테스트의
원래 목적이 아니므로 input 만 조정).

## 4. 운영 체크리스트

- [ ] 배포 직후 `scheduler:executed:*` 키가 KST 자정까지 유지되는지 `redis-cli SCAN` 으로 확인.
- [ ] CD 가 같은 거래일 안에서 두 번 실행되어도 텔레그램 일일 리포트는 1건만 도착하는지 확인.
- [ ] KIS API 가 `degraded` 인 시점에 `handle_market_close` 가 `snapshot_skip_reason=kis_error` 로 종료되는지 로그 확인.
- [ ] 멱등성 키 강제 초기화가 필요하면 `python -c "import asyncio; from core.scheduler_idempotency import clear_for_date; from datetime import date; asyncio.run(clear_for_date(date.today()))"`.

## 4a. Scheduler 컨테이너 Liveness Heartbeat (2026-04-09 추가)

### 배경

`scheduler` 컨테이너는 `scheduler_main.py` 로 기동되며 FastAPI HTTP 프로세스가 아니다.
그러나 backend 와 동일한 이미지를 공유하므로 Dockerfile 의
`HEALTHCHECK curl localhost:8000/api/system/health` 를 상속받아 **구조적으로 항상
unhealthy** 상태로 누적된다 (2026-04-08 관측: FailingStreak 597). 이 상태에서는
운영 가시성이 손상되고, `depends_on: condition: service_healthy` 로 scheduler 에
의존하는 후속 서비스가 있으면 블로킹된다.

### 설계

외부 의존성(redis, http)이 전혀 없는 **파일 mtime 기반 heartbeat** 을 도입한다.
healthcheck 자체가 또 다른 장애원이 되지 않도록 redis/network 를 쓰지 않는다.

- 모듈: `backend/core/scheduler_heartbeat.py`
- 경로: `SCHEDULER_HEARTBEAT_PATH` (기본 `/tmp/scheduler.heartbeat`)
- 임계치: `SCHEDULER_HEARTBEAT_STALE_SECONDS` (기본 180s — `_scheduler_loop` 의 최대 sleep 60s × 3 사이클 여유)
- `_scheduler_loop` 매 iteration 최상단에서 `write_heartbeat()` 를 호출하여 파일 mtime 갱신
- IO 오류는 호출자까지 전파되지 않고 조용히 무시 — heartbeat 실패가 스케줄러 본 루프를 멈추게 하면 안 된다
- `docker-compose.yml::scheduler.healthcheck` 는 순수 Python 한 줄로 mtime 검사 (redis/curl 불필요)

### 3-layer 방어와의 관계

heartbeat 은 멱등성/스냅샷 가드와 독립적이다. Idempotency + snapshot guard 가
**데이터 정합성**(중복/0원 리포트 차단)을 담당한다면, heartbeat 은 **컨테이너
liveness 관측**을 담당한다. 두 축은 직교한다.

### 테스트

`backend/tests/test_scheduler_heartbeat.py` (11 케이스):

| 클래스 | 검증 항목 |
|--------|-----------|
| `TestWriteHeartbeat` | 부재 시 생성, 재호출 시 mtime 갱신, 부모 디렉토리 자동 생성, PermissionError 비전파 |
| `TestCheckHeartbeatFresh` | 파일 부재 시 False, fresh 시 True, stale 시 False, 경계값, 환경변수 기본값 |
| `TestLoopWiring` | `_scheduler_loop` AST 에 `write_heartbeat()` 호출이 존재하는지, import 경로가 `core.scheduler_heartbeat` 인지 검증 |

루프 전체를 돌리는 통합테스트는 `now_kst/is_trading_day/event_time` 등 시각
의존성이 많아 취약하므로, wiring 회귀는 AST 레벨로 결정적으로 검증한다. 파일
갱신 본체 동작은 `TestWriteHeartbeat` 이 보장한다.

## 5. 변경 파일

- 신규: `backend/core/scheduler_idempotency.py`
- 신규: `backend/tests/test_scheduler_idempotency.py`
- 수정: `backend/core/trading_scheduler.py` (start/_execute_event 와이어링)
- 수정: `backend/core/scheduler_handlers.py` (market_close + post_market 가드)
- 수정: `backend/tests/test_scheduler_handlers_extended.py` (post_market 입력 보강)
- 신규: 본 문서 `docs/operations/scheduler-idempotency.md`
- 신규 (2026-04-09): `backend/core/scheduler_heartbeat.py`
- 신규 (2026-04-09): `backend/tests/test_scheduler_heartbeat.py`
- 수정 (2026-04-09): `backend/core/trading_scheduler.py` (`_scheduler_loop` heartbeat wiring)
- 수정 (2026-04-09): `docker-compose.yml` (scheduler 전용 healthcheck 블록 추가)

Last reviewed: 2026-04-09
