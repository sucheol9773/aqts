# 알림 파이프라인 아키텍처 (Notification Pipeline)

**작성일**: 2026-04-10
**범위**: Commit 1 (state machine + retry API) → Commit 2 (Router wiring) → Commit 3 (retry loop + metrics + meta-alerts) → Commit 5 (TelegramTransport SSOT 추출) 의 알림 파이프라인 시리즈
**관련 감사 문서**: [`docs/operations/alerting-audit-2026-04.md`](../operations/alerting-audit-2026-04.md)

---

## 1. 목적과 설계 원칙

AQTS 의 알림 파이프라인은 **at-least-once 전달**, **상태 가시성**, **파이프라인 자체의 실패 탐지** 세 가지를 동시에 만족해야 한다. 과거에는 `AlertManager` 가 알림을 in-memory / MongoDB 에 저장하기만 하고 `TelegramNotifier` 로 가는 경로가 단절돼 있었고 (wiring 결손), 한 번 실패한 알림은 재시도되지 않았다. 본 파이프라인은 다음 원칙 위에 재설계됐다.

1. **단일 상태 머신**: `AlertStatus` 가 알림의 유일한 진실원천(SSOT). 모든 전이는 `AlertManager` 메서드를 통해서만 일어난다. 경합은 원자 연산(`find_one_and_update`) 으로 해결한다.
2. **전달 보장 (at-least-once)**: 실패한 알림은 `FAILED` 로 영속되고, 비동기 재시도 루프가 고정 backoff 에 맞춰 재픽업한다. 중복 전달은 허용하되, 유실은 허용하지 않는다.
3. **감사성 우선**: backoff 는 지수 함수가 아니라 **고정 dict**(`{1:60, 2:300, 3:900}`) 로 선언된다. "이 알림이 언제 재시도될까" 를 수식 계산 없이 즉시 읽을 수 있어야 한다.
4. **파이프라인 자체의 관측**: Router 가 채널별로 발송 성공/실패 건수와 레이턴시를 Prometheus 로 노출하고, 파이프라인 실패율이 임계치를 넘으면 **메타알림**이 동일 Alertmanager 경로로 발화한다.
5. **Wiring Rule 준수**: 구현(state machine, retry policy, Router) 과 적용(lifespan 에서 주입, 루프 기동, 채널 구성) 이 분리된 순간 RBAC / 공급망 Wiring Rule 과 같은 결손이 재발한다. 본 문서는 그 연결선을 명시적으로 기록한다.

---

## 2. 전체 데이터 플로우

```
 ┌─────────────────────────────────────────────────────────────────┐
 │ [Producer]                                                      │
 │   trading_guard / kis_recovery / scheduler_handlers / ...       │
 │     └─ _kis_alert_callback(...) → alert_manager.create_and_     │
 │                                    persist_alert(alert)         │
 └──────────────────────────────┬──────────────────────────────────┘
                                │  ① PENDING 으로 영속
                                ▼
 ┌─────────────────────────────────────────────────────────────────┐
 │ [AlertManager] (single SSOT)                                    │
 │   create_and_persist_alert                                      │
 │     ├─ save_alert → Mongo {status: PENDING, send_attempts: 0}   │
 │     └─ _dispatch_via_router(alert)  ← Commit 2                  │
 │          ├─ claim_for_sending(id)     [PENDING → SENDING]        │
 │          ├─ router.dispatch(alert)                              │
 │          ├─ 성공 → mark_sent_by_id    [SENDING → SENT]           │
 │          └─ 실패 → mark_failed_with_retry  [SENDING → FAILED /   │
 │                                              DEAD if gte 3]     │
 └──────────────────────────────┬──────────────────────────────────┘
                                │  ② Router 로 즉시 디스패치
                                ▼
 ┌─────────────────────────────────────────────────────────────────┐
 │ [NotificationRouter] (cascade)                                  │
 │   dispatch(alert) — primary → fallback 순으로 시도               │
 │     ├─ TelegramChannelAdapter  (primary)                        │
 │     ├─ FileNotifier            (fallback 1)                     │
 │     └─ ConsoleNotifier         (fallback 2)                     │
 │                                                                 │
 │   각 채널마다 try/finally 로 perf_counter 계측:                   │
 │     ALERT_DISPATCH_LATENCY_SECONDS{channel=...}.observe(dt)     │
 │     ALERT_DISPATCH_TOTAL{channel=..., result=success|failure}   │
 └──────────────────────────────┬──────────────────────────────────┘
                                │  ③ 실패 영속 → 재픽업 대기
                                ▼
 ┌─────────────────────────────────────────────────────────────────┐
 │ [_alert_retry_loop] (Commit 3, main.py lifespan 에서 create_    │
 │                      task 로 기동된 독립 coroutine)               │
 │   while True:                                                    │
 │     await asyncio.sleep(ALERT_RETRY_LOOP_INTERVAL_SECONDS=60)   │
 │     await alert_manager.dispatch_retriable_alerts(               │
 │       max_attempts=3, limit=100                                 │
 │     )                                                            │
 │       ├─ find_retriable_alerts(now)                              │
 │       │    — FAILED ∧ 0 < send_attempts < 3                      │
 │       │    — last_send_attempt_at + backoff[attempts] ≤ now      │
 │       ├─ requeue_failed_to_pending(id) [FAILED → PENDING, 원자]  │
 │       ├─ _alert_from_doc(doc)          [Mongo dict → Alert]      │
 │       ├─ _dispatch_via_router(alert)                             │
 │       └─ 디스패치 후 상태 재조회로 DEAD 전이 탐지                 │
 │          → ALERT_RETRY_DEAD_TOTAL.inc()                         │
 └─────────────────────────────────────────────────────────────────┘
                                │
                                ▼  ④ 파이프라인 자체의 관측
 ┌─────────────────────────────────────────────────────────────────┐
 │ [Prometheus + Alertmanager] (기존 인프라 재사용, Decision 3-A)   │
 │   monitoring/prometheus/rules/aqts_alerts.yml                    │
 │     group: aqts_alert_pipeline                                   │
 │       - AlertPipelineFailureRate  (critical, 5m)                 │
 │           failure/total > 0.5                                    │
 │       - AlertPipelineDeadTransitions  (warning, 5m)              │
 │           increase(aqts_alert_retry_dead_total[30m]) > 0         │
 │                                                                 │
 │   → Alertmanager → Telegram receiver (기존 운영 중)              │
 └─────────────────────────────────────────────────────────────────┘
```

---

## 3. 상태 머신

### 3.1 전이 다이어그램

```
                    create_and_persist_alert
                            │
                            ▼
                      ┌──────────┐
                      │ PENDING  │◄────────────────┐
                      └────┬─────┘                 │
                           │ claim_for_sending     │
                           ▼                       │
                      ┌──────────┐                 │
                      │ SENDING  │                 │
                      └────┬─────┘                 │
              ┌────────────┼────────────┐          │
        성공  │            │            │ 실패      │
              ▼            │            ▼          │
         ┌────────┐        │      ┌─────────┐      │
         │  SENT  │        │      │ FAILED  │──────┤ requeue_failed_to_pending
         └────────┘        │      └────┬────┘      │ (재시도 루프가 backoff 경과 후)
         (terminal)        │           │           │
                           │           │ gte 3     │
                           │           ▼           │
                           │      ┌────────┐       │
                           │      │  DEAD  │       │
                           │      └────────┘       │
                           │      (terminal)       │
                           │                       │
                           └───────────────────────┘
```

### 3.2 전이표

| 출발 상태 | 도착 상태 | 트리거 | 원자 연산 | 부수효과 |
|---|---|---|---|---|
| (신규) | PENDING | `create_and_persist_alert` | `insert_one` | `send_attempts=0`, `created_at=now` |
| PENDING | SENDING | `claim_for_sending(id)` | `find_one_and_update({status: PENDING}, {$set: {status: SENDING, last_send_attempt_at: now}, $inc: {send_attempts: 1}})` | `send_attempts` 증가 |
| SENDING | SENT | `mark_sent_by_id(id)` | `update_one({_id, status: SENDING}, {$set: {status: SENT, sent_at: now}})` | terminal |
| SENDING | FAILED | `mark_failed_with_retry(id, err)` (attempts < 3) | `update_one({_id, status: SENDING}, {$set: {status: FAILED, last_send_error: err}})` | — |
| SENDING | DEAD | `mark_failed_with_retry(id, err)` (attempts gte 3) | 동일 (경계는 Python 단에서 판정) | `ALERT_RETRY_DEAD_TOTAL.inc()` |
| FAILED | PENDING | `requeue_failed_to_pending(id)` | `find_one_and_update({_id, status: FAILED}, {$set: {status: PENDING}})` | `send_attempts` 불변 (감사 추적 보존) |

**경계 규칙**: `send_attempts gte 3` 에서 DEAD. `2` 는 아직 FAILED (backoff 900s 후 3번째 시도 가능). `3` 은 이미 3회 시도했으므로 더 이상 재시도 없음.

### 3.3 불변식 (Invariants)

1. SENT 와 DEAD 는 terminal. 어떤 경로로도 다른 상태로 전이하지 않는다.
2. `send_attempts` 는 `claim_for_sending` 이 성공할 때만 증가한다. `requeue_failed_to_pending` 은 건드리지 않는다. 따라서 `send_attempts` 는 "지금까지 실제로 시도된 횟수" 의 정확한 카운트다.
3. `SENDING` 상태의 알림은 최대 하나의 consumer 만 처리한다 (원자 `find_one_and_update` 로 경합 해결).
4. `FAILED → PENDING` 전이 후 `claim_for_sending` 이 실패하면 다시 FAILED 로 돌려놓을 책임은 재시도 루프가 아니라 `_dispatch_via_router` 에 있다 (Commit 2 설계).

---

## 4. 재시도 정책 (retry_policy.py)

### 4.1 고정 dict backoff

```python
# backend/core/notification/retry_policy.py
MAX_SEND_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = {1: 60, 2: 300, 3: 900}

def backoff_seconds_for(attempts: int) -> int:
    """시도 횟수 attempts 이후 다음 재시도까지 대기 초.

    범위 밖 입력은 clamp:
      - attempts <= 0 → RETRY_BACKOFF_SECONDS[1]
      - attempts >= MAX → RETRY_BACKOFF_SECONDS[MAX]
    """
    if attempts <= 0:
        return RETRY_BACKOFF_SECONDS[1]
    if attempts >= MAX_SEND_ATTEMPTS:
        return RETRY_BACKOFF_SECONDS[MAX_SEND_ATTEMPTS]
    return RETRY_BACKOFF_SECONDS[attempts]
```

### 4.2 왜 지수 backoff 가 아닌가 (Decision 1-A)

| 기준 | 지수 backoff | 고정 dict |
|---|---|---|
| 감사성 | `base * 2^n` 계산 필요 | 표로 즉시 확인 |
| 테스트 결정성 | floating point 오차 | 정수 비교 |
| 운영 직관성 | "4번째 재시도는 언제?" 계산 | "3회 후 DEAD" 명확 |
| 유연성 | 파라미터만 바꾸면 스케줄 변경 | 코드 수정 필요 |

AQTS 는 알림 볼륨이 낮고 (일 수십~수백 건), 각 알림이 운영 판단에 사용되므로 **감사성**이 유연성보다 우선한다.

### 4.3 시도 횟수별 재시도 시점

| send_attempts | 의미 | 다음 재시도까지 대기 | 누적 경과 |
|---|---|---|---|
| 0 | 아직 claim 전 | — | — |
| 1 | 1차 시도 실패 | 60s | +60s |
| 2 | 2차 시도 실패 | 300s | +360s |
| 3 | 3차 시도 실패 → DEAD | ∞ | terminal |

즉 최악의 경우 알림 생성 후 약 **6분** 에 DEAD 판정이 난다. 메타알림의 5분 `for` 절은 이 타임스케일에 맞춰 조정됐다.

---

## 5. TelegramTransport SSOT (HTTP 전송 레이어)

### 5.1 설계 근거

기존에는 `TelegramNotifier` 가 httpx 호출, 재시도, 메시지 분할을 모두 직접 구현하고, `TelegramChannelAdapter` 가 `TelegramNotifier` 를 래핑하여 사용했다. 이 구조에서 두 가지 문제가 발생했다:

1. **HTTP 호출 경로의 이중화**: Notifier 의 `_send_single_message()` 와 Adapter 의 Notifier 래핑이 서로 다른 설정 해석 경로를 갖고 있어, 한쪽만 수정하면 다른 쪽이 회귀할 수 있었다.
2. **테스트 패치 경로의 불일치**: 통합 테스트마다 `core.notification.telegram_notifier.httpx.AsyncClient` 또는 `httpx.AsyncClient` 를 혼용 패치하여, 리팩토링 시 패치 경로 오류가 반복됐다.

### 5.2 구조

```
telegram_transport.py   ← SSOT: HTTP POST + 재시도 + 메시지 분할
    TelegramTransport   (send_text, _send_single, is_configured)
    split_message()     (모듈 레벨 유틸리티)
    create_transport()  (설정 팩토리)

telegram_notifier.py    ← AlertManager 연동 + 포맷팅 + 필터링
    TelegramNotifier    (dispatch_alert, send_message → Transport 위임)

telegram_adapter.py     ← NotificationChannel 프로토콜 적합
    TelegramChannelAdapter (send → Transport.send_text 직접 호출)
```

**SSOT 원칙**: Telegram Bot API HTTP 호출은 `TelegramTransport.send_text()` → `_send_single()` 만 수행한다. 다른 모듈이 `httpx` 로 직접 Bot API 를 호출하는 것은 금지한다.

### 5.3 하위호환

- `TelegramNotifier._split_message()` → `telegram_transport.split_message()` 위임 (기존 import 경로 유지)
- `TELEGRAM_MAX_LENGTH` 는 `telegram_transport` 에서 정의하고 `telegram_notifier` 에서 re-export
- `TelegramNotifier._bot_token`, `._chat_id` 프로퍼티 유지 (Transport 에서 읽기)
- `create_transport(bot_token=None)` 은 `None` 일 때만 settings fallback. 빈 문자열 `""` 은 그대로 전달 (의도적 미설정 구분)

---

## 6. NotificationRouter 와 채널 캐스케이드

### 6.1 구성

```python
# backend/main.py lifespan 에서 주입
router = NotificationRouter()
router.add_channel(TelegramChannelAdapter())  # Transport 자동 생성 (settings)
router.add_channel(FileNotifier())
router.add_channel(ConsoleNotifier())
alert_manager.set_router(router)
```

`set_router` 는 순수 setter — 주입 전에는 `_dispatch_via_router` 가 noop (로그만 남김). 이 구조 덕분에 단위 테스트는 모두 router 없이 실행되고, 통합 테스트와 운영에서만 실제 채널로 나간다.

### 6.2 dispatch 알고리즘

```python
async def dispatch(self, alert: Alert) -> DispatchResult:
    last_error = None
    for channel in [self.primary, *self.fallbacks]:
        start = time.perf_counter()
        success = False
        try:
            await channel.send(alert)
            success = True
            return DispatchResult(success=True, channel=channel.name)
        except Exception as e:
            last_error = e
            continue
        finally:
            dt = time.perf_counter() - start
            ALERT_DISPATCH_LATENCY_SECONDS.labels(channel=channel.name).observe(dt)
            ALERT_DISPATCH_TOTAL.labels(
                channel=channel.name,
                result="success" if success else "failure",
            ).inc()
    return DispatchResult(success=False, error=str(last_error))
```

**핵심 설계 (Decision 2-A)**:
- `try/finally` 로 예외 경로에서도 `failure` counter 가 반드시 증가한다. 데코레이터 방식으로 하면 `continue` 가 데코레이터의 catch 를 우회할 위험이 있다.
- `success` 플래그는 finally 실행 시점에 bound 돼 있어야 하므로 try 진입 전에 `False` 로 초기화한다.
- 라벨 카디널리티는 `channel ∈ {telegram, file, console} × result ∈ {success, failure} = 6` 계열로 상한 고정.

### 6.3 캐스케이드 의미론

- primary 가 성공하면 fallback 은 호출되지 않는다 → 정상 경로의 latency 는 1 채널 분만.
- primary 가 실패하면 fallback 순으로 시도하고 **하나라도** 성공하면 `DispatchResult.success=True` 반환. 이 경우 counter 는 primary `failure` + fallback `success` 가 동시에 증가 → 메타알림은 "실패율" 이 아니라 "primary 실패 + fallback 복구율" 을 볼 수 있다.
- 모든 채널이 실패하면 `DispatchResult.success=False` → `mark_failed_with_retry` 호출 → FAILED 로 영속 → 재시도 루프 대기.

---

## 7. 재시도 루프 (_alert_retry_loop)

### 7.1 lifespan 기동

```python
# backend/main.py
_alert_retry_task: asyncio.Task | None = None
ALERT_RETRY_LOOP_INTERVAL_SECONDS = 60

async def _alert_retry_loop():
    while True:
        try:
            await asyncio.sleep(ALERT_RETRY_LOOP_INTERVAL_SECONDS)
            await alert_manager.dispatch_retriable_alerts(max_attempts=3, limit=100)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(f"AlertRetryLoop iteration failed: {e}")

async def lifespan(app: FastAPI):
    global _alert_retry_task
    # ... (NotificationRouter wiring)
    if env_bool("ALERT_RETRY_LOOP_ENABLED", default=True):
        _alert_retry_task = asyncio.create_task(_alert_retry_loop())
        logger.info("AlertRetryLoop started (interval=60s)")
    yield
    # shutdown
    if _alert_retry_task is not None and not _alert_retry_task.done():
        _alert_retry_task.cancel()
        try:
            await _alert_retry_task
        except asyncio.CancelledError:
            pass
```

### 7.2 왜 TradingScheduler 가 아닌 독립 task 인가

- `TradingScheduler` 는 장 시간 기반 작업 (시가/종가 핸들러) 에 특화돼 있고, 알림 재시도는 24×7 동작이 필요하다.
- 과거 heartbeat 회귀 사례에서 "scheduler 에 블로킹 작업을 올렸다가 다른 잡이 밀린" 패턴이 있었다 (CD #91 관련 회고). 재시도 루프는 독립 task 로 분리하여 책임 범위를 명확히 한다.
- `asyncio.create_task` 는 이벤트 루프 수명과 결합되므로 lifespan 종료 시 cancel + await 만으로 그레이스풀 정지가 가능하다.

### 7.3 dispatch_retriable_alerts 알고리즘

```python
async def dispatch_retriable_alerts(
    self, max_attempts: int = 3, limit: int = 100
) -> dict[str, int]:
    if self._notification_router is None:
        return {"dispatched": 0, "skipped": 0, "dead": 0}

    now = datetime.now(timezone.utc)
    docs = await self.find_retriable_alerts(now, max_attempts=max_attempts, limit=limit)

    dispatched = skipped = dead = 0
    for doc in docs:
        alert_id = doc["_id"]
        if not await self.requeue_failed_to_pending(alert_id):
            skipped += 1
            continue
        alert = self._alert_from_doc(doc)
        try:
            await self._dispatch_via_router(alert)
        except Exception as e:
            logger.warning(f"Retry dispatch swallowed exception for {alert_id}: {e}")
            continue
        # 디스패치 후 상태 재조회로 DEAD 전이 탐지
        post = await self._collection.find_one({"_id": alert_id}, {"status": 1, "send_attempts": 1})
        if post and post.get("status") == AlertStatus.DEAD.value:
            ALERT_RETRY_DEAD_TOTAL.inc()
            dead += 1
        else:
            dispatched += 1
    return {"dispatched": dispatched, "skipped": skipped, "dead": dead}
```

### 6.4 find_retriable_alerts 의 2 단계 필터

Mongo 쿼리는 `status == FAILED ∧ 0 < send_attempts < max` 만 prefilter 한다. backoff 조건은 **Python 단에서** 평가한다:

```python
ready = []
for doc in cursor:
    attempts = doc["send_attempts"]
    last = doc["last_send_attempt_at"]
    if last is None:
        continue
    if last + timedelta(seconds=RETRY_BACKOFF_SECONDS[attempts]) <= now:
        ready.append(doc)
    if len(ready) >= limit:
        break
```

**왜 Python 단인가**: Mongo 에서 `$expr` + `$switch` 로 attempts 별 backoff 를 표현할 수는 있지만, 쿼리가 복잡해지고 감사성이 떨어진다. 재시도 루프가 주기 60s + limit 100 으로 제한되므로 Python 단 필터의 CPU 비용은 무시할 만하다 (최악 100 doc × 단순 산술).

---

## 8. Prometheus 지표와 메타알림

### 8.1 지표 카탈로그

| 지표 | 타입 | 라벨 | 용도 |
|---|---|---|---|
| `aqts_alert_dispatch_total` | Counter | `channel`, `result` | 채널별 성공/실패 건수 누적 |
| `aqts_alert_dispatch_latency_seconds` | Histogram | `channel` | 채널별 send 레이턴시 분포 |
| `aqts_alert_retry_dead_total` | Counter | — | DEAD 전이 누적 수 |

**버킷 선택 근거**: `[0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0]` 초. Telegram Bot API 의 p95 레이턴시가 300~800ms 구간에 주로 분포하므로 0.25~1.0 해상도를 높게 잡고, 장애 상황의 timeout (10~30s) 까지 커버한다.

### 8.2 메타알림 규칙

```yaml
# monitoring/prometheus/rules/aqts_alerts.yml
groups:
  - name: aqts_alert_pipeline
    rules:
      - alert: AlertPipelineFailureRate
        expr: |
          (
            sum(rate(aqts_alert_dispatch_total{result="failure"}[5m]))
            /
            clamp_min(sum(rate(aqts_alert_dispatch_total[5m])), 1e-9)
          ) > 0.5
        for: 5m
        labels:
          severity: critical
          component: alert-pipeline
        annotations:
          summary: "알림 파이프라인 실패율 50% 초과"
          description: "지난 5분간 알림 디스패치 실패율이 50%를 넘었습니다."

      - alert: AlertPipelineDeadTransitions
        expr: increase(aqts_alert_retry_dead_total[30m]) > 0
        for: 5m
        labels:
          severity: warning
          component: alert-pipeline
        annotations:
          summary: "DEAD 전이 발생"
          description: "지난 30분 내에 재시도 한도를 초과한 알림이 있습니다."
```

**설계 주석**:
- `clamp_min(..., 1e-9)` 은 분모가 0 인 경우 (발송 0 건) division-by-zero 를 막는다. 에러 대신 `0 / 1e-9 = 0` 으로 평가되어 rule 이 조용히 비활성 상태로 유지된다.
- `increase[30m]` + `for 5m` 조합은 단발성 DEAD 1 건도 놓치지 않으면서, 플래핑을 막기 위해 5분 지속 확인한다. 알림 생성 후 DEAD 판정까지 약 6분이 걸리므로 30분 창은 충분히 여유롭다.

### 7.3 왜 기존 Alertmanager 를 재사용하는가 (Decision 3-A)

Observation B-1 에서 확인: Prometheus Alertmanager + Telegram receiver 가 이미 운영 중이고, 다른 AQTS 알림(`AqtsApiDown`, `AqtsSchedulerStalled` 등) 이 그 경로로 발화하고 있다. 신규 meta-alert 인프라를 세우는 대신 규칙 그룹만 추가하면 다음 이득이 있다.

1. **독립성**: 알림 파이프라인이 죽어도 메타알림 경로는 Prometheus 에서 직접 나가므로 "알림 파이프라인이 자기 자신의 장애를 알리는 순환" 이 발생하지 않는다.
2. **운영 단일화**: 운영자는 Alertmanager 한 곳에서 모든 critical 알림을 본다.
3. **인프라 변경 zero**: Commit 3 의 위험 반경이 코드 + 규칙 파일로 한정된다.

---

## 9. 운영 토글과 환경변수

| 환경변수 | 기본값 | 효과 |
|---|---|---|
| `ALERT_RETRY_LOOP_ENABLED` | `true` | `false` 로 설정하면 lifespan 에서 `_alert_retry_loop` 자체를 기동하지 않는다. 런타임 장애 시 무력화 용도. |
| (상수) `ALERT_RETRY_LOOP_INTERVAL_SECONDS` | 60 | 루프 주기. 환경변수화 대상은 아님 — 변경 시 backoff 스케줄과 함께 검토 필요. |

`ALERT_RETRY_LOOP_ENABLED` 는 `BOOL_ENV_KEYS` 화이트리스트에 등록되어 있고, 값은 `core.utils.env.env_bool()` 단일 진입점으로 파싱된다.

**무력화 절차**:
```bash
# 서버에서
echo "ALERT_RETRY_LOOP_ENABLED=false" >> /opt/aqts/.env
docker compose -f docker-compose.yml up -d backend
# 로그에서 "AlertRetryLoop started" 가 출력되지 않는지 확인
docker compose -f docker-compose.yml logs backend --tail=100 | grep -i alertretryloop
```

무력화 후에도 `create_and_persist_alert` 의 즉시 디스패치 경로는 살아 있다. 즉, **정상 경로는 그대로 동작하고 재시도만 멈춘다**. 실패한 알림은 FAILED 에 쌓이므로, 수동으로 재처리하려면 runbook 의 mongo 스크립트를 사용한다.

---

## 10. Wiring Rule 준수 체크리스트

알림 파이프라인은 다음 다층 wiring 을 요구한다. **정의했다 ≠ 적용했다** — 각 단계가 독립적으로 누락될 수 있다.

| 레이어 | 정의 위치 | 적용 위치 | 검증 방법 |
|---|---|---|---|
| 상태 머신 메서드 | `alert_manager.py` (`claim_for_sending`, `mark_*`, `requeue_*`) | `_dispatch_via_router`, `dispatch_retriable_alerts` 가 호출 | 단위 테스트 (`test_alert_manager.py`) |
| Router 인스턴스 | `fallback_notifier.py` (`NotificationRouter`) | `main.py` lifespan 에서 `set_notification_router` 호출 | 통합 테스트 (`test_alert_manager_dispatch_wiring.py`), 기동 로그 |
| 재시도 루프 | `_alert_retry_loop` 함수 정의 | `main.py` lifespan 에서 `asyncio.create_task` | 기동 로그 grep (`AlertRetryLoop started`) |
| 메트릭 훅 | `metrics.py` Counter/Histogram 정의 | `NotificationRouter.dispatch` 내부 try/finally | Prometheus `/metrics` 엔드포인트에 계열 노출 확인 |
| 메타알림 규칙 | `aqts_alerts.yml` | Alertmanager 로드 | `promtool check rules`, Alertmanager UI |

**회귀 사례 예방**: RBAC Wiring Rule (9 위 라우터 가드 누락), 공급망 Wiring Rule (cosign 서명 검증 누락), SSH heredoc Rule (stdin forwarding 으로 단계 은폐) 과 동일한 사고 — "정의 ≠ 적용" — 가 알림 파이프라인에도 그대로 적용된다. CLAUDE.md 에는 이 체크리스트를 Wiring Rule 의 alerting 도메인 확장으로 추가한다 (Commit 4).

---

## 11. 테스트 매트릭스

| 테스트 파일 | 커버 범위 | 케이스 수 |
|---|---|---|
| `test_alert_manager.py` | 상태 전이 원자성, `claim_for_sending` 경합, `mark_sent_by_id` / `mark_failed_with_retry` | 기존 |
| `test_alert_manager_dispatch_wiring.py` | Commit 2: setter 주입, `create_and_persist_alert` 디스패치 경로, 예외 swallow | 15 |
| `test_alert_retry_loop.py` | Commit 3: retry_policy, find_retriable_alerts, requeue, dispatch loop, Router 메트릭 훅, DEAD counter, lifespan import smoke | 25 |

모든 테스트는 `mongo_collection=None` 메모리 모드에서 실행되어 DB 의존성이 없고, `_SpyRouter` dataclass 로 채널 경로를 스텁한다. 런타임 전체 파이프라인 검증은 통합 테스트가 아니라 **기동 로그 + Prometheus 지표 노출 확인** 으로 대체한다 (wiring 은 단위 테스트로 검증 불가하다는 CLAUDE.md 의 원칙).

---

## 12. 관련 문서

- 감사 및 커밋 시리즈: [`docs/operations/alerting-audit-2026-04.md`](../operations/alerting-audit-2026-04.md)
- 운영 절차 및 템플릿: [`docs/operations/alerting.md`](../operations/alerting.md)
- 런북 (Commit 4 예정): `docs/operations/alert-pipeline-runbook.md`
- Wiring Rule 원칙: [`CLAUDE.md`](../../CLAUDE.md) §"인증(authn) ≠ 인가(authz) 분리 원칙" 및 "공급망 보안 검증 규칙"

---

**문서 상태**: 초안 (Commit 4 브랜치에서 최종 확정 예정)
**마지막 검토**: 2026-04-10
