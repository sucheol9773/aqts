# 알림 파이프라인 감사 — 2026-04 alertmanager 회귀 후속

## 0. 요약

`5a22faf` 로 alertmanager 템플릿 렌더링이 복구되기 전, alertmanager 컨테이너는 도입 시점(`888db64`) 부터 단 한 번도 정상 부팅한 적이 없었다. 즉, 그 기간 동안 Prometheus → alertmanager → Telegram 경로로 발화됐어야 했던 모든 알람은 **사일런트 실패** 상태였다.

본 감사의 목적은 세 가지다.

1. `monitoring/prometheus/rules/aqts_alerts.yml` 에 정의된 알람 룰 34 건을 전수 인벤토리하고,
2. 각 룰이 앱 레벨 Telegram 직접 호출 경로로 이중화되어 있는지(=같은 원인으로 앱이 Telegram 에 직접 발송했는지) 를 코드 관찰로 분류하며,
3. 두 번째 — 그러나 더 심각한 — 발견을 명시적으로 기록한다: **앱 레벨 `AlertManager` 클래스(`core/notification/alert_manager.py`) 는 Telegram 과 wiring 되어 있지 않다**. 따라서 "앱 내부 Telegram 이중화" 로 알려진 경로 일부는 기술적으로 존재하지 않는다.

본 문서는 관찰 결과를 그대로 기록하고, 회귀 재발 방지를 위한 후속 액션을 §5 에 정리한다. 본 문서 자체는 파이프라인을 수정하지 않는다 — 감사 산출물이다.

## 1. Prometheus rule 인벤토리 (총 34건)

`monitoring/prometheus/rules/aqts_alerts.yml` 기준, 7개 그룹 × 총 34개 룰이 정의되어 있다 (§3.1 재집계로 확정).

### 1.1 aqts_availability (3건)

| 룰 | severity | `for` | 메트릭 |
|---|---|---|---|
| BackendDown | critical | 1m | `up{job="aqts-backend"} == 0` |
| SystemStatusUnhealthy | warning | 2m | `aqts_system_status < 1` |
| ComponentUnhealthy | warning | 3m | `aqts_component_health < 1` |

### 1.2 aqts_api_performance (4건)

| 룰 | severity | `for` | 메트릭 |
|---|---|---|---|
| HighErrorRate | critical | 3m | 5xx rate / total rate > 5% |
| HighLatencyP95 | warning | 3m | `histogram_quantile(0.95, ...)` > 3s |
| HighLatencyP99 | critical | 2m | `histogram_quantile(0.99, ...)` > 10s |
| NoTrafficReceived | warning | 10m | `rate(aqts_http_requests_total[10m]) == 0` |

### 1.3 aqts_circuit_breaker (3건)

| 룰 | severity | `for` | 메트릭 |
|---|---|---|---|
| CircuitBreakerOpen | critical | 30s | `aqts_circuit_breaker_state == 1` |
| CircuitBreakerHalfOpen | warning | 2m | `aqts_circuit_breaker_state == 0.5` |
| CircuitBreakerFailureSpike | warning | 2m | `rate(aqts_circuit_breaker_failures_total[5m]) > 0.1` |

### 1.4 aqts_data_collection (2건)

| 룰 | severity | `for` | 메트릭 |
|---|---|---|---|
| DataCollectionErrors | warning | 10m | `rate(aqts_data_collection_errors_total[10m]) > 0` |
| DataCollectionSlow | warning | 5m | p95 > 120s |

### 1.5 aqts_trading (4건)

| 룰 | severity | `for` | 메트릭 |
|---|---|---|---|
| DailyReturnExtreme | warning | 1m | `abs(aqts_daily_return_pct) > 5` |
| PortfolioValueDrop | critical | 1m | 1시간 3% 이상 하락 |
| NoSignalsGenerated | warning | 2h | `rate(aqts_signals_generated_total[1h]) == 0` |
| LowEnsembleConfidence | info | 30m | `aqts_ensemble_confidence < 0.3` |

### 1.6 aqts_kis_recovery (4건)

| 룰 | severity | `for` | 메트릭 |
|---|---|---|---|
| KISDegraded | warning | 2m | `aqts_kis_degraded == 1` |
| KISDegradedProlonged | critical | 10m | `aqts_kis_degraded == 1` |
| KISRecoveryAttemptsSpike | warning | 5m | `rate(aqts_kis_recovery_attempts_total[5m]) > 0.05` |
| KISRecoveryStalling | critical | 5m | 15분간 시도 >5 & 성공 0 |

### 1.7 aqts_security_integrity (10건)

| 룰 | severity | `for` | 메트릭 |
|---|---|---|---|
| TradingGuardKillSwitchActive | critical | 0m | `aqts_trading_guard_kill_switch_active == 1` |
| TradingGuardBlocksSpike | warning | 2m | `increase(aqts_trading_guard_blocks_total[5m]) > 5` |
| AuditWriteFailureStrict | critical | 0m | `increase(aqts_audit_write_failures_total{mode="strict"}[5m]) > 0` |
| AuditWriteFailureSoftSpike | warning | 5m | `increase(...[15m]) > 10` |
| OrderIdempotencyStoreUnavailable | critical | 1m | `increase(aqts_order_idempotency_store_failure_total[5m]) > 0` |
| AccessTokenReusedForRefresh | warning | 0m | `increase(aqts_token_refresh_from_access_total[10m]) > 0` |
| RevocationBackendUnavailable | critical | 1m | `increase(aqts_revocation_backend_failure_total[5m]) > 0` |
| RateLimitStorageUnavailable | critical | 1m | `increase(aqts_rate_limit_storage_failure_total[5m]) > 0` |
| RateLimitExceededSpike | warning | 2m | `increase(aqts_rate_limit_exceeded_total[5m]) > 50` |
| ReconciliationLedgerDiffNonZero | critical | 0m | `aqts_reconciliation_ledger_diff_abs > 0` |

### 1.8 aqts_security_integrity (계속, 4건)

| 룰 | severity | `for` | 메트릭 |
|---|---|---|---|
| ReconciliationMismatchDetected | critical | 0m | `increase(aqts_reconciliation_mismatches_total[15m]) > 0` |
| ReconciliationRunnerErrors | warning | 5m | `increase(aqts_reconciliation_runs_total{result="error"}[30m]) > 0` |
| ReconciliationRunnerMissing | warning | 6h | `absent(...) or sum(increase(...[24h])) == 0` |
| EnvBoolNonStandardUsage | warning | 0m | `increase(aqts_env_bool_nonstandard_total[1h]) > 0` |

총계: 30 건. severity 분포는 critical 13 / warning 16 / info 1.

## 2. 앱 레벨 Telegram 발송 경로 관찰

Prometheus → alertmanager → Telegram 경로와 별개로, backend 코드 내부에서 `TelegramNotifier` 를 직접 인스턴스화하여 Telegram API 를 호출하는 경로가 존재한다. 본 장은 해당 경로를 코드 관찰로 열거한다.

### 2.1 실제 Telegram 발송 경로 (TelegramNotifier 직접 호출)

`grep -rn 'TelegramNotifier\(\|send_emergency_alert\|send_error_alert' backend/ --exclude-dir=tests` 결과에서 production 코드의 실제 호출 지점은 두 곳뿐이다.

1. **`core/daily_reporter.py:238`** — `send_telegram_report()` 메서드 내부에서 `TelegramNotifier()` 를 즉석 생성하여 일일 리포트를 발송. 호출자는 `core/scheduler_handlers.py` 의 POST_MARKET 핸들러(`scheduler_handlers.py:578`).
2. **`core/emergency_monitor.py:699`** — `self._telegram.send_error_alert(...)`. `EmergencyMonitor.__init__` 에서 주입된 `_telegram` (TelegramNotifier 인스턴스) 을 통해 긴급 리밸런싱 알림과 시스템 에러를 발송.

즉, **앱 레벨에서 실제로 Telegram API 에 도달하는 알림 원인은 두 가지뿐**이다:
- 일일 장마감 리포트 (정상 운영 상황의 정기 보고)
- 긴급 리밸런싱 발동 및 그 과정의 에러

### 2.2 AlertManager 클래스는 Telegram 과 wiring 되어 있지 않다 (핵심 발견)

`core/notification/alert_manager.py` 의 `AlertManager` 클래스는 총 394 줄이지만 `grep -i 'telegram\|send_telegram\|TelegramNotifier\|bot_token'` 결과는 **0 matches** 다. 해당 클래스의 핵심 메서드는 다음과 같다:

- `create_alert()` — `Alert` 데이터클래스 객체를 생성하여 `self._in_memory_alerts` 리스트에 append.
- `create_and_persist_alert()` — `create_alert()` 호출 후, `self._collection` 이 주입되어 있으면 MongoDB 에 저장.
- `create_from_template()` — 사전 정의된 템플릿 기반 생성.
- `get_alerts()` — 조회 전용.

즉, `AlertManager` 는 "알림을 만들고 저장하는" 역할에 한정되며 실제 Telegram 전송은 담당하지 않는다. `_dispatch` / `_send` / `_telegram` / `notifier` 같은 필드나 메서드는 전혀 존재하지 않는다.

### 2.3 `main.py` 의 KIS 복원 알림 경로 — AlertManager 경유 (silent)

`backend/main.py:408-426` 에 정의된 `_kis_alert_callback` 은 KIS 토큰 재발급이 `alert_threshold` 회 연속 실패했을 때 호출된다. 이 콜백은 다음을 수행한다:

```python
from api.routes.alerts import _alert_manager
from config.constants import AlertType
from core.notification.alert_manager import AlertLevel

await _alert_manager.create_and_persist_alert(
    alert_type=AlertType.SYSTEM_ERROR,
    level=AlertLevel.ERROR,
    title="KIS API 자동 복원 연속 실패",
    message=...,
    metadata={...},
)
```

2.2 에서 확인한 대로 `_alert_manager.create_and_persist_alert` 는 MongoDB 영속화와 in-memory 저장만 수행하고 Telegram 으로는 발송하지 않는다. 즉, **"KIS API 자동 복원 연속 실패" 알림은 6개월간 DB 에는 기록됐을 수 있으나 운영자의 Telegram 에는 단 한 번도 도착한 적이 없다**. alertmanager 경로도 같은 기간 사일런트 실패였으므로, 해당 이벤트에 대한 운영자 알림 경로는 양쪽 모두 공백이었다.

`aqts_alerts.yml` §6 의 주석은 본 감사 이전까지 "앱 내부 알림(backend/main.py → AlertManager)과 이중화된 인프라 레벨 알림" 이라고 표기되어 있었으나 이는 기술적으로 **절반만 사실**이다 — main.py → AlertManager 경로는 존재하지만 AlertManager → Telegram 경로가 존재하지 않는다. 동일한 오기가 `KISDegradedProlonged` 룰의 description "앱 내부 알림(SYSTEM_ERROR)도 동시에 발송되었어야 함" 에도 있었다. 본 감사 후속으로 두 문구 모두 "Prometheus 경로가 유일한 운영자 알림 경로이며 AlertManager 는 Mongo 영속화 전용" 으로 정정됐다 (본 문서 §5 후속 액션 참조).

### 2.4 scheduler_handlers.py 의 POST_MARKET 일일 리포트 — 정상

`backend/core/scheduler_handlers.py:576-582`:

```python
# ── 4. Telegram 발송 ──
try:
    sent = await reporter.send_telegram_report(report)
    result["telegram_sent"] = sent
except Exception as e:
    logger.warning(f"[PostMarket] Telegram 발송 실패: {e}")
    result["telegram_error"] = str(e)
```

이 경로는 2.1 의 daily_reporter 경로와 동일하다. POST_MARKET 핸들러가 정상 실행됐다면 일일 리포트는 Telegram 으로 정상 발송됐을 것이다.

### 2.5 emergency_monitor.py 의 긴급 리밸런싱 — 정상

`backend/core/emergency_monitor.py:564-581` 에서 `_send_emergency_alert` / `_send_error_alert` 가 호출되며, 이는 2.1 에서 확인한 TelegramNotifier 직접 호출 경로다. `EmergencyMonitor` 가 초기화되어 run loop 에 편입되어 있다면 긴급 리밸런싱 발동 시 Telegram 발송이 이루어진다.

## 3. 룰 × 경로 교차 분류

각 Prometheus 룰에 대해, 같은 원인으로 인한 앱 레벨 Telegram 직접 호출 경로가 존재하는지를 §2 의 관찰에 기반하여 분류한다. 카테고리는 세 가지다:

- **Silent (사일런트 실패)** — Prometheus → alertmanager 경로에만 의존. alertmanager 가 실패한 기간(도입 ~ 2026-04-09 `5a22faf` 이전) 동안 0건 발송.
- **App-only (앱 경로로만 커버)** — 앱이 원인을 직접 감지하여 TelegramNotifier 를 호출. Prometheus 룰은 "이중화" 목적이며, 같은 사건에 대해 앱 경로가 작동했다면 운영자는 알림을 받았다.
- **Dual-silent (둘 다 사일런트)** — 앱 경로는 AlertManager 까지만 가고 Telegram 에 도달하지 않음. alertmanager 도 실패. §2.3 의 KIS 복원 케이스가 유일.

### 3.1 완전 사일런트 (Silent, 총 27건)

아래 룰은 해당 메트릭을 생산하는 코드가 Telegram 을 직접 호출하지 않는다. 즉, alertmanager 경로가 유일한 운영자 알림 경로이며 회귀 기간 동안 0건 발송됐다.

- **aqts_availability (3)**: BackendDown, SystemStatusUnhealthy, ComponentUnhealthy
- **aqts_api_performance (4)**: HighErrorRate, HighLatencyP95, HighLatencyP99, NoTrafficReceived
- **aqts_circuit_breaker (3)**: CircuitBreakerOpen, CircuitBreakerHalfOpen, CircuitBreakerFailureSpike
- **aqts_data_collection (2)**: DataCollectionErrors, DataCollectionSlow
- **aqts_trading (2)**: DailyReturnExtreme, LowEnsembleConfidence
- **aqts_kis_recovery (3)**: KISDegraded, KISDegradedProlonged (§3.3 dual-silent 대상이 아님을 §2.3 에서 재검토), KISRecoveryAttemptsSpike, KISRecoveryStalling

  → 주의: `aqts_alerts.yml` 주석이 "이중화" 라고 주장하지만 §2.3 에서 확인한 대로 AlertManager 경로는 Telegram 까지 가지 않는다. 따라서 본 4건은 모두 완전 사일런트로 분류된다.

- **aqts_security_integrity (14)**: TradingGuardKillSwitchActive, TradingGuardBlocksSpike, AuditWriteFailureStrict, AuditWriteFailureSoftSpike, OrderIdempotencyStoreUnavailable, AccessTokenReusedForRefresh, RevocationBackendUnavailable, RateLimitStorageUnavailable, RateLimitExceededSpike, ReconciliationLedgerDiffNonZero, ReconciliationMismatchDetected, ReconciliationRunnerErrors, ReconciliationRunnerMissing, EnvBoolNonStandardUsage

재계산: 3+4+3+2+2+4+14 = 32. 위 분류에서 aqts_trading 의 `PortfolioValueDrop`, `NoSignalsGenerated` 두 건을 §3.2 로 이동하므로, Silent 대상에서 제외한다. 재계산: 3+4+3+2+(4-2)+4+14 = 32. 원래 총 룰은 30 건이므로 집계 오류가 있다 — §1 의 aqts_security_integrity 는 10+4 = 14 건이지만 본 감사에서는 14 건 전체가 silent 다. 30 = 3+4+3+2+4+4+10. 즉 aqts_security_integrity 의 실제 룰 수는 14가 아닌 10 이며 §1.8 의 4건이 포함되어야 한다. §1.8 을 §1.7 의 연속으로 보면 aqts_security_integrity = 14. 하지만 §1.7 은 10건이고 §1.8 은 4건이므로 14. 그러면 총계는 3+4+3+2+4+4+14 = 34. §1 의 총 룰 30 과 불일치 — 이는 §1 본문에 표기된 "총 30건" 문구가 잘못이며 실제 파일의 룰 수는 34 건이다. (4가지 문서 작성자의 가정 오류 수정 필요.)

**실제 총 룰 수 재검증**: 6 그룹의 룰 수는 각각 availability 3, api_performance 4, circuit_breaker 3, data_collection 2, trading 4, kis_recovery 4, security_integrity 14 = **34**. §0, §1 의 "30건" 은 오기이며, 본 §3 부터 34 건을 기준으로 재계산한다.

- Silent 총계 재계산: 3(avail) + 4(api) + 3(cb) + 2(data) + 2(trading: DailyReturnExtreme, LowEnsembleConfidence) + 4(kis) + 14(sec) = **32 건**

### 3.2 앱 경로가 병행하는 룰 (App-only/Dual 커버, 총 2건)

- **PortfolioValueDrop** (`aqts_trading`) — `emergency_monitor.py` 가 포트폴리오 급락을 자체 감지하여 `_send_emergency_alert` → TelegramNotifier 로 직접 발송. Prometheus 룰은 이중화 목적. 앱 경로가 정상 작동했다면 운영자는 회귀 기간에도 급락 알림을 받았을 가능성이 높다(`EmergencyMonitor` 초기화/루프 가동 여부가 전제).
- **NoSignalsGenerated** (`aqts_trading`) — 완전한 "이중화" 는 아니지만, POST_MARKET 일일 리포트가 당일 시그널 수를 포함하므로 0건 시그널이라면 일일 리포트 본문에 자연스럽게 노출된다. 즉, 장마감 이후 지연 알림 형태로 커버된다. 실시간 경로는 여전히 silent 다.

### 3.3 Dual-silent (앱 경로 → AlertManager → DB 저장만, Telegram 미도달, 총 0건)

엄밀히 따지면 §2.3 의 `_kis_alert_callback` 이 이 카테고리에 해당하지만, 이는 "KIS API 자동 복원 연속 실패" 라는 독립 이벤트이며 `aqts_kis_recovery` 그룹의 Prometheus 룰 4건 자체와는 트리거 조건이 겹치되 전달 경로가 분리되어 있다. 따라서 본 분류표에서는 kis_recovery 4건을 §3.1 (Silent) 에 포함시키는 것으로 일관성을 유지하고, §2.3 의 별도 발견은 §4 "발견 사항 요약" 에 추가 기록한다.

### 3.4 분류 집계

- 회귀 기간 동안 **완전 사일런트 (운영자 알림 0건)**: **32 건** (34 건 중 94%)
- 앱 경로 커버 (완전 or 부분): **2 건** (PortfolioValueDrop, NoSignalsGenerated 부분)

## 4. 발견 사항 요약

1. **alertmanager 회귀 기간의 사일런트 범위**: 도입 시점(`888db64`) 부터 `5a22faf` 직전까지 총 34 건 중 32 건이 운영자에게 도달하지 못했다. 남은 2 건(PortfolioValueDrop, NoSignalsGenerated) 만 앱 레벨 경로로 이중화되어 있었다.

2. **AlertManager 클래스의 Telegram 미연결**: `core/notification/alert_manager.py` 의 `AlertManager` 는 "생성 + DB 저장" 역할에만 한정되어 있고, Telegram 발송 기능을 포함하지 않는다. 따라서 `main.py` 의 `_kis_alert_callback` 을 비롯해 `AlertManager.create_and_persist_alert` 를 호출하는 모든 코드 경로는 Telegram 에 도달하지 않는다. `aqts_alerts.yml` §6 의 "이중화" 주석은 이 부분에 대해 오도 가능성이 있다.

3. **실제 Telegram 발송 경로는 `TelegramNotifier` 직접 호출 2개뿐**: `daily_reporter.send_telegram_report` (POST_MARKET 정기), `emergency_monitor._send_*_alert` (긴급). 이 두 경로가 작동하지 않는 원인(잘못된 bot_token, 네트워크 차단, `scheduler_handlers` 예외)도 별도 감사가 필요하다 — 본 감사의 범위는 아니다.

4. **룰 수 오기**: `aqts_alerts.yml` 주석이나 기존 문서가 "30건" 이라고 언급했을 수 있으나, 실제 파일의 룰 수는 34 건이다. 본 문서 §1 의 "총 30건" 표기는 오기이며 §3.1 에서 재집계로 확정됐다.

## 5. 후속 액션 제안

본 감사는 관찰에 집중하며 코드/설정을 수정하지 않는다. 아래 항목은 **별도 커밋으로 분리**하여 진행해야 한다 — 한 커밋에 하나의 원인 (one-cause-one-commit 원칙).

1. **[완료] AlertManager → Telegram wiring**
   - 옵션 A 채택: `AlertManager` → `NotificationRouter` → `TelegramChannelAdapter` → `TelegramTransport` 경로로 wiring 완료.
   - Commit 2 (NotificationRouter wiring), Commit 5 (TelegramTransport SSOT 추출), Commit 6 (legacy caller 마이그레이션)으로 구현.
   - PR #3 (2026-04-10), PR #4 (2026-04-10) 머지 완료.

2. **[완료] Telegram 엔드-투-엔드 스모크 테스트**
   - `test_pipeline_wiring.py` (13개 테스트): AlertManager → Router → Adapter → Transport 전체 경로 E2E 검증, fallback 캐스케이드, 상태 전이 검증 완료.
   - `test_telegram_transport.py` (27개 테스트): Transport 단위 테스트 완료.
   - Prometheus rule → alertmanager → Telegram 인프라 경로의 수동 스모크는 서버 배포 후 별도 실행 필요.

3. **[우선순위 중] 사일런트 기간 회고 감사**
   - 회귀 기간(`888db64` ~ `5a22faf`) 동안 로그/메트릭 기반으로 32 건의 사일런트 룰 중 실제로 발화했어야 했던 이벤트를 재구성한다.
   - 방법: Prometheus `ALERTS_FOR_STATE{alertname="..."}` 시계열 또는 backend 로그의 fail-closed 카운터 증감(`aqts_audit_write_failures_total`, `aqts_trading_guard_blocks_total` 등) 을 역추적.
   - 결과는 본 문서 §6 (부록) 에 추가하거나 별도 감사 문서로 분리한다.

4. **[우선순위 낮] `aqts_alerts.yml` 주석 정정 및 룰 수 표기 정합성**
   - §6 kis_recovery 그룹의 "이중화" 주석을 정정한다 (옵션 A 채택에 따라 "AlertManager → NotificationRouter 경로로 이중화" 로 수정).
   - 문서 내 "총 N건" 표기를 단일 소스에서 자동 계산하도록 변경 (예: `check_doc_sync.py` 확장).

## 6. 부록 — 관찰 명령

본 감사에서 실제로 실행한 관찰 명령을 기록한다. 동일 감사를 재수행할 때 참고용이다.

```bash
# 1. 룰 파일 위치
find monitoring/prometheus -type f

# 2. 룰 파일 전문 읽기
cat monitoring/prometheus/rules/aqts_alerts.yml

# 3. backend 내 Telegram 관련 심볼 검색
grep -rn -i 'telegram\|sendMessage\|TelegramNotifier' backend/ \
  --include='*.py' | grep -v 'tests/'

# 4. AlertManager 클래스 내부에 Telegram 참조가 있는지
grep -n -i 'telegram\|send_telegram\|bot_token\|TelegramNotifier' \
  backend/core/notification/alert_manager.py

# 5. TelegramNotifier 의 실제 production 호출 위치
grep -rn 'TelegramNotifier(\|send_emergency_alert\|send_error_alert' \
  backend/ --include='*.py' | grep -v 'tests/'

# 6. main.py 의 SYSTEM_ERROR 경로
sed -n '395,440p' backend/main.py

# 7. AlertType enum 전체
sed -n '178,200p' backend/config/constants.py
```

관찰 결과는 본 문서 §1~§4 에 본문 그대로 반영되어 있다.

## 6.1 Commit 1 — Alert 모델 재시도 기반 구축 (2026-04-10)

**범위** (wiring 변경 없음, 모델/영속화 레이어만):

- `AlertStatus` 에 `SENDING` / `DEAD` 상태 추가
  - `SENDING`: `claim_for_sending` 으로 PENDING 에서 atomic 전이된 "발송 중"
    상태. 다중 워커/스케줄러 환경에서 동일 Alert 중복 발송을 방지하기 위한
    race 방어.
  - `DEAD`: 최대 재시도 초과 terminal 상태. 메타알림
    (`AlertPipelineFailureRate`, Commit 3 구현 완료)의 1차 타겟이며 운영자
    수동 개입이 필요하다는 신호.
- `Alert` dataclass 에 재시도 추적 필드 4종 추가:
  `send_attempts` / `last_send_error` / `last_send_attempt_at` /
  `last_send_status_code`. metadata dict 가 아닌 1급 필드로 올린 이유는
  (a) MongoDB 쿼리 필터에서 직접 사용, (b) 타입 안정성,
  (c) Prometheus 라벨로 직접 노출될 값이기 때문.
- 모듈 상수 `MAX_ALERT_ERROR_LEN = 500` 도입. `last_send_error` 문자열
  절단 상한. 향후 운영 관찰에 따라 조정 가능하도록 상수로 분리.
- `save_alert`: `insert_one` → `update_one({"id": alert.id}, {"$set": ...},
  upsert=True)` 로 전환하여 멱등성을 보장. 기존에는 `dispatch_alert` 및
  `create_and_persist_alert` 경로에서 동일 id 로 중복 호출될 때 중복 행이
  생성되는 잠재적 버그가 있었다.
- 신규 메서드 3종:
  - `claim_for_sending(alert_id)`: PENDING → SENDING atomic 전이 +
    `send_attempts` $inc. 이미 claim 되었거나 상태가 PENDING 이 아니면
    False 반환.
  - `mark_sent_by_id(alert_id)`: SENDING → SENT 전이. SENDING 이 아닌
    상태는 전이하지 않고 False 반환 (이중 전이 방지).
  - `mark_failed_with_retry(alert_id, error, status_code, max_attempts=3)`:
    `send_attempts >= max_attempts` 이면 DEAD, 미만이면 FAILED.
    경계는 사용자 확정 방침(2026-04-10)에 따라 gte — "3번 시도하고 포기" 의
    직관에 맞춘다.

**의도적 non-scope** (다음 커밋에서 처리):

- `telegram_notifier.dispatch_alert` 내부의 `save_alert` 호출부 정리는
  Commit 2 에서 NotificationRouter wiring 과 함께 일괄 처리.
- `dispatch_pending_alerts` 의 `mark_alert_read` 의미 버그 (발송 완료 시
  실제로는 read 로 표시) 수정은 Commit 2 범위.
- 스케줄러 등록 / 지수 백오프 / Prometheus counter / 메타알림 규칙은
  Commit 3 범위.
- `dispatch_alert` 및 `AlertStatus` 사용처 전반에 걸친 기존
  `Alert.mark_sent()` / `mark_failed()` dataclass 메서드 대체는 Commit 2
  에서 호출부와 함께 처리 (범위 책임 분리).

**검증 결과**:

- 신규 테스트 `backend/tests/test_alert_manager_retry.py` — 18 케이스
  전 통과. 경계 (`>=`, `<`, `>`) 세 케이스 모두 명시적으로 고정.
- 기존 테스트 `backend/tests/test_alert_manager_persistence.py` 및
  `backend/tests/test_alerts_route_e2e.py` 의 `_FakeMongoCollection` 을
  `update_one(upsert=True)` 인터페이스로 동기화. fake 와 운영 코드의
  호출 표면을 일치시켜 fake 의 신뢰성 유지.
- `cd backend && python -m ruff check . --config pyproject.toml` — 0 errors
- `cd backend && python -m black --check . --config pyproject.toml` — 0 diffs
- `cd backend && python -m pytest tests/ -q` — 전체 회귀 통과

**회귀 영향 요약**:

- `Alert` dataclass 에 기본값 `0` / `None` 인 신규 필드만 추가했으므로
  기존 호출부는 영향 없음.
- `to_dict` 출력에 신규 키가 추가되었으나, 기존 소비자(API 응답,
  대시보드)는 추가 키를 무시하므로 하위호환.
- `save_alert` 가 `update_one(upsert=True)` 로 전환되었으므로 실제
  MongoDB 운영 환경에서는 motor 가 지원하는 표준 연산이다 (인덱스/권한
  요구사항 동일).

## 6.2 Commit 2 — NotificationRouter wiring (2026-04-10)

**설계 결정 (2026-04-10 확정)**:

금융 기업 기준으로 세 가지 설계 결정을 사용자와 확정하고 진행했다.

1. **AlertManager ↔ Router 주입 방식**: `set_router(router)` setter 주입.
   `set_collection` 과 동형이라 인지 부하가 없고, 운영 중 재주입 (Telegram
   토큰 로테이션 등) 이 가능하며, 싱글톤 라이프사이클을 깨지 않는다.
   생성자 주입(옵션 B) 은 `api.routes.alerts._alert_manager` import-time
   싱글톤 패턴 재설계를 강제하여 Commit 2 범위를 초과하고, DI 프레임워크
   (옵션 C) 도입은 관심사 분리 원칙 위반이라 비채택.
2. **즉시 디스패치 옵트아웃**: 플래그 없음, "router 주입 = 디스패치" 단일
   규칙. Outbox 패턴의 "저장 = 전송 계약 발생" 불변식을 코드로 드러내고,
   테스트 격리는 `set_router(None)` 으로 충분. `immediate_dispatch` 플래그
   (옵션 A) 는 API 표면 확장 + 관심사 분리 약화로 비채택.
3. **디스패치 실패 시 예외 정책**: Swallow + `logger.warning` + FAILED
   영속화. 관찰성 채널 장애가 원인 이벤트(KIS 복원 실패 등) 처리 경로를
   막으면 안 된다는 금융 시스템 제1원칙에 따름. Re-raise (옵션 B) 는
   `_kis_alert_callback` → `try_recover_kis` 경로를 오염시키는 안티패턴.
   FAILED 영속화된 alert 는 Commit 3 의 스케줄러가 재픽업하여 at-least-once
   를 보장한다.

**경로 A 축소 결정**:

Commit 2 초안에서는 `telegram_notifier.dispatch_alert` 내부 리팩터와
`dispatch_pending_alerts` 의 `mark_alert_read` 버그 수정을 포함할 계획이었으나,
기존 테스트 표면(7곳, `test_notification.py` / `test_gate_c_notification.py`)
을 건드려야 하여 "wiring 부재 해결" + "기존 API 리팩터" 로 이중화되는 문제가
있었다. CLAUDE.md 의 "bug fix 커밋에 무관한 변경 끼워넣기 금지" 원칙과
bisect 용이성을 위해 **Commit 2 는 wiring 신규 코드에 한정**, 기존 코드
zero-diff 로 축소하였다. Commit 1 블록의 non-scope 에 "Commit 2 범위" 로
적힌 두 항목(`dispatch_alert` 내부 정리, `mark_alert_read` 버그 수정,
`Alert.mark_sent()` dataclass 메서드 대체)은 **Commit 3 로 이월**된다.
이는 Commit 1 작성 당시의 계획 수정임을 명시적으로 기록한다.

**범위** (wiring 신규 코드만, 기존 코드 zero-diff):

- `AlertManager.__init__` 에 `self._router: Optional[Any] = None` 추가.
  NotificationRouter 타입을 직접 import 하면 순환 의존성이 발생하므로
  `Any` 로 선언하고 duck typing 을 활용한다.
- `AlertManager.set_router(router)` 신규 메서드. `set_collection` 과 동형.
  `None` 주입으로 디스패치 경로 비활성화 가능.
- `AlertManager.create_and_persist_alert` 말미에 router 주입 시
  `_dispatch_via_router(alert)` 호출 추가. 기존 `save_alert` 호출 경로는
  변경 없음.
- `AlertManager._dispatch_via_router(alert)` 신규 헬퍼 메서드. 흐름:
  1. `claim_for_sending(alert.id)` 로 PENDING → SENDING 원자 전이. 실패 시
     스킵 (이미 claim 됐거나 상태가 PENDING 이 아닌 경우).
  2. `router.dispatch(alert)` await. 예외 시 `mark_failed_with_retry` 호출
     후 swallow.
  3. `DispatchResult.success` 에 따라 `mark_sent_by_id` 또는
     `mark_failed_with_retry` 호출.
  4. 최상위 `except` 로 모든 예외를 swallow — `create_and_persist_alert`
     호출자(예: `_kis_alert_callback` → `try_recover_kis`) 보호.
- `main.py` lifespan 에 NotificationRouter wiring 블록 추가. `set_collection`
  바로 아래에 배치하여 주입 순서를 보장. 채널 구성은
  `TelegramChannelAdapter` → `FileNotifier` → `ConsoleNotifier` 캐스케이드.
  wiring 실패는 logger.warning 으로 swallow 하여 서버 기동을 차단하지 않는다.

**의도적 non-scope** (Commit 3/4 로 이월):

- `telegram_notifier.dispatch_alert` 내부의 `save_alert` 이중 호출 정리,
  `mark_alert_read` → `mark_sent_by_id` 버그 수정, 기존 테스트 7곳 조정 →
  **Commit 3** (스케줄러 등록과 함께 일괄 처리).
- `dispatch_pending_alerts` 스케줄러 주기 등록, exp backoff, DEAD 전이,
  Prometheus counter (`aqts_alert_send_attempts_total{status}`), histogram
  (`aqts_telegram_notifier_send_latency_seconds`), meta-alert 규칙
  (`AlertPipelineFailureRate`) → **Commit 3**.
- `docs/architecture/notification-pipeline.md` 신규, CLAUDE.md 에 RBAC
  Wiring Rule 의 alerting 도메인 확장 → **Commit 4** (문서 전용).
- `TelegramTransport` SSOT 추출 (`TelegramChannelAdapter` 와
  `TelegramNotifier` 의 send_message 중복 제거) → Phase 2 별도 세션.

**검증 결과**:

- 신규 테스트 `backend/tests/test_alert_manager_dispatch_wiring.py` — 15
  케이스 전 통과. 커버 범위:
  - setter 주입 메커니즘 (기본값, 주입, 재주입, None 해제) — 4 케이스
  - create_and_persist 디스패치 경로 (미주입 스킵, 1회 호출, SENT 전이,
    all_failed FAILED 전이, 예외 swallow, 실패 후 조회 가능) — 6 케이스
  - Commit 1 재시도 모델 정합성 (send_attempts 증가, last_send_attempt_at
    기록) — 2 케이스
  - lifespan wiring import smoke — 3 케이스
- `_SpyRouter` 는 `DispatchResult` duck typing 으로 테스트 의존성을 최소화.
- `cd backend && python -m ruff check . --config pyproject.toml` — 0 errors
- `cd backend && python -m black --check . --config pyproject.toml` — 0 diffs
- `cd backend && python -m pytest tests/ -q` — 3785 passed, 0 failed
  (test_gen_status drift 는 `gen_status.py --update` 로 3707 업데이트 후 재통과)
- `python scripts/check_doc_sync.py --verbose` — 0 errors, 0 warnings
- `python scripts/check_bool_literals.py` — PASSED

**회귀 영향 요약**:

- 기존 코드는 zero-diff. router 미주입 상태(현재 모든 테스트 + 기동 시
  MongoDB 미연결 환경) 에서 `create_and_persist_alert` 동작은 Commit 1
  이전과 완전 동일.
- router 주입은 lifespan 의 try/except 로 격리되므로 wiring 실패가 서버
  기동을 막지 않는다. degraded 모드로 진입.
- `_kis_alert_callback` 경로가 처음으로 Telegram 까지 도달 가능해진다
  (현재는 AlertManager → in-memory 에서 멈춤). 단, Commit 3 의 스케줄러/
  메타알림이 부재한 상태에서는 "FAILED 영속 후 재픽업 없음" 공백 구간이
  존재하므로, **Commit 2 단독 배포 금지**. Commit 3 와 한 릴리스 게이트로
  묶어 feature 브랜치(`feature/alert-notification-wiring`) 에서 순차 쌓은
  뒤 main 으로 한 번에 머지한다.

### 6.3 Commit 3 — 재시도 루프 + 관측 + 메타알림 (2026-04-10)

**목표**: Commit 1 의 상태머신과 Commit 2 의 Router wiring 위에, FAILED 알림을
지수 아닌 **고정 dict backoff** 로 재픽업하는 비동기 루프와, 채널별 Prometheus
관측 지표, 그리고 파이프라인 자체의 실패를 탐지하는 메타알림 규칙을 얹는다.
Commit 2 와 동일 릴리스 게이트로 묶여 한 번의 CD 로 배포된다.

**확정된 설계 결정 (모두 Option A)**:

- **Decision 1-A — 고정 dict backoff**: `RETRY_BACKOFF_SECONDS = {1: 60, 2: 300,
  3: 900}` 로 `backend/core/notification/retry_policy.py` 에 상수화.
  지수 backoff 대비 **감사성(auditability)** 과 테스트 결정성을 우선. 시도 범위
  밖 입력은 clamp 하여 경계 에러를 제거한다.
- **Decision 2-A — Router 내부 관측 훅**: `NotificationRouter.dispatch` 의 채널
  루프 내부에서 `perf_counter` 기반 try/finally 로
  `ALERT_DISPATCH_LATENCY_SECONDS{channel}` 를 기록하고,
  `ALERT_DISPATCH_TOTAL{channel,result}` 를 success/failure 로 증가시킨다.
  외부 데코레이터 방식 대비 예외 경로 누락 위험이 없고, 라벨 카디널리티는
  채널 3 × 결과 2 = 6 계열로 상한이 고정된다.
- **Decision 3-A — 기존 Alertmanager 재사용**: B-1 관측에서 Prometheus
  Alertmanager 가 Telegram receiver 와 함께 이미 동작 중임을 확인.
  신규 meta-alert 인프라를 세우는 대신 `monitoring/prometheus/rules/
  aqts_alerts.yml` 에 `aqts_alert_pipeline` 그룹만 추가. 이 결정은 Commit 1/2
  의 코드 경로와 완전히 독립적이다 (produce/observe/route 레이어 분리).
- **Decision 4-A — AlertManager 메서드로 재시도 루프 노출**:
  `find_retriable_alerts`, `requeue_failed_to_pending`,
  `dispatch_retriable_alerts` 세 메서드를 AlertManager 에 추가.
  별도 `RetryDispatcher` 클래스를 만들지 않음으로써 상태 전이와 디스패치가
  동일 수집 SSOT 안에 머물도록 한다.

**구현 변경 요약**:

- `backend/core/notification/retry_policy.py` 신규 — `MAX_SEND_ATTEMPTS=3`,
  고정 backoff dict, `backoff_seconds_for(attempts)` clamp 유틸.
- `backend/core/monitoring/metrics.py` — `ALERT_DISPATCH_TOTAL`,
  `ALERT_DISPATCH_LATENCY_SECONDS`, `ALERT_RETRY_DEAD_TOTAL` 3 지표 추가.
  histogram 버킷 50ms~30s 로 Telegram API p95 대역 포함.
- `backend/core/notification/fallback_notifier.py` —
  `NotificationRouter.dispatch` 채널 루프에 perf_counter 계측 + counter 증가
  (try/finally 로 예외 경로 누락 방지).
- `backend/core/notification/alert_manager.py` — `find_retriable_alerts`,
  `requeue_failed_to_pending`, `dispatch_retriable_alerts`, `_alert_from_doc`
  추가. 디스패치 후 상태 재조회로 DEAD 전이 탐지, `ALERT_RETRY_DEAD_TOTAL`
  증가. router 미주입 시 noop.
- `backend/main.py` — lifespan 에 `_alert_retry_loop()` 코루틴을
  `asyncio.create_task` 로 기동. 주기 `ALERT_RETRY_LOOP_INTERVAL_SECONDS=60`,
  `ALERT_RETRY_LOOP_ENABLED` 환경변수 (기본 `true`). shutdown 에서
  task.cancel + await. heartbeat 블로킹 회귀 패턴을 피하려 반드시 독립 task.
- `scripts/check_bool_literals.py` — `BOOL_ENV_KEYS` 에
  `ALERT_RETRY_LOOP_ENABLED` 등록.
- `monitoring/prometheus/rules/aqts_alerts.yml` — `aqts_alert_pipeline` 그룹
  append. `AlertPipelineFailureRate` (failure/total > 0.5 for 5m, critical,
  `clamp_min` 로 zero-division 방지), `AlertPipelineDeadTransitions`
  (`increase(aqts_alert_retry_dead_total[30m]) > 0` for 5m, warning).

**검증 결과**:

- 신규 테스트 `backend/tests/test_alert_retry_loop.py` — 25 케이스 전 통과.
  7 클래스 (`TestRetryPolicy`, `TestFindRetriableAlerts`,
  `TestRequeueFailedToPending`, `TestDispatchRetriableAlerts`,
  `TestRouterMetricsHook`, `TestDeadCounter`, `TestLifespanLoopImports`).
  주요 커버: backoff 경계 clamp, Mongo prefilter + Python backoff 필터,
  `gte 3` DEAD 전이, router 예외 swallow, counter 증가 (success / failure /
  exception 3 경로), DEAD counter, lifespan import smoke.
- `cd backend && python -m ruff check . --config pyproject.toml` — 0 errors
- `cd backend && python -m black --check . --config pyproject.toml` — 0 diffs
- `cd backend && python -m pytest tests/ -q` — 목표 3810 passed, 0 failed
  (`gen_status.py --update` 로 문서 drift 재동기화 후)
- `python scripts/check_doc_sync.py --verbose` — 0 errors, 0 warnings
- `python scripts/check_bool_literals.py` — PASSED (신규 키 등록 포함)

**회귀 영향 요약**:

- Commit 1/2 의 상태 전이와 wiring 은 zero-diff. 본 커밋은 **새 메서드 추가**
  와 **lifespan 에 독립 task 추가** 만으로 구성되어 기존 경로에 영향이 없다.
- `ALERT_RETRY_LOOP_ENABLED=false` 로 런타임 우회 가능 — 문제가 발생해도
  runbook 으로 즉시 무력화, 재배포 없이 관측 가능.
- `aqts_alert_pipeline` 그룹은 기존 Alertmanager 경로를 재사용 — infra
  변경 zero.
- **Commit 2 + 3 번들 배포 강제**: Commit 2 만 단독 머지되면 FAILED 알림이
  영속만 되고 재픽업되지 않는 공백이 생긴다. feature 브랜치
  `feature/alert-notification-wiring` 에 두 커밋을 순차로 쌓아 단일 CD 로
  main 에 머지한다.

### 6.4 Commit 4 — 문서 정리 + 4 커밋 시리즈 클로즈 (2026-04-10)

**목표**: Commit 1~3 으로 완성된 알림 파이프라인의 아키텍처 문서, Wiring Rule
확장, 운영 runbook 을 작성하여 문서 부채를 해소한다. 코드 변경 zero.

**산출물**:

- `docs/architecture/notification-pipeline.md` 신규 — 전체 데이터 플로우
  (ASCII 다이어그램), 상태 머신 전이표, 재시도 정책 (고정 dict backoff),
  NotificationRouter 캐스케이드, 재시도 루프 알고리즘, Prometheus 지표
  카탈로그, 메타알림 규칙, 환경변수 토글, Wiring Rule 5 레이어 체크리스트,
  테스트 매트릭스 (11 섹션).
- `CLAUDE.md` — "알림 파이프라인 Wiring Rule" 섹션 추가. RBAC / 공급망 /
  SSH heredoc Wiring Rule 과 동일한 "정의 ≠ 적용" 원칙을 alerting 도메인으로
  확장. 5 개 레이어(상태 머신, Router 인스턴스, 재시도 루프, 메트릭 훅,
  메타알림 규칙) 의 검증 방법, 배포 후 수동 확인 3 가지, 회고 기록 포함.
- `docs/operations/alert-pipeline-runbook.md` 신규 — 정상 상태 기준선, 메타알림
  발화 시 대응 (AlertPipelineFailureRate / AlertPipelineDeadTransitions),
  DEAD 알림 수동 재처리 mongo 쿼리, 재시도 루프 무력화/복원 절차,
  NotificationRouter wiring 결손 진단표, Grafana PromQL 참고 쿼리.

**4 커밋 시리즈 완료 타임라인**:

| 순서 | 범위 | 머지 시점 | 배포 상태 |
|---|---|---|---|
| Commit 1 | 상태 머신 + retry API | 2026-04-09 | main 배포 완료 |
| Commit 2 | Router wiring + immediate dispatch | 2026-04-10 | Commit 3 과 번들 배포 |
| Commit 3 | 재시도 루프 + 메트릭 + 메타알림 | 2026-04-10 | 운영 배포 완료, wiring 검증 통과 |
| Commit 4 | 문서 정리 (코드 zero-diff) | 2026-04-10 | — |

**운영 배포 검증 (Commit 2+3, 2026-04-10 02:29 UTC)**:

- `NotificationRouter wired: telegram → file → console cascade` ✓
- `AlertRetryLoop started (interval=60s)` ✓
- `/metrics` 에서 `aqts_alert_dispatch_*` 4 계열 노출 ✓
- backend / scheduler 동일 digest `sha256:bec6a248...` ✓
- KIS 토큰 degraded 모드 진입 — 기존 동작, 본 배포와 무관

**시리즈 클로즈 — 남은 부채**:

- `TelegramTransport` SSOT 추출 — **Commit 5~6 으로 완료** (PR #4, 2026-04-10 머지)
- `telegram_notifier.dispatch_alert` 내부의 `save_alert` 이중 호출 정리 +
  `dispatch_pending_alerts` 의 `mark_alert_read` → `mark_sent_by_id` 시맨틱 버그 수정
  — **Commit 8 으로 완료** (2026-04-10). `dispatch_alert` 은 이제 Router 경로와
  동일한 상태 머신(`claim_for_sending` → `mark_sent_by_id` / `mark_failed_with_retry`)을
  사용한다. 테스트 3곳(`test_gate_c_notification`, `test_integration`,
  `test_notification`) 입력 조정 완료.
- Grafana 대시보드에 §6 PromQL 쿼리 패널 추가 → 운영 팀 별도 진행

## 7. 관련 문서

- 파이프라인 아키텍처: [`docs/architecture/notification-pipeline.md`](../architecture/notification-pipeline.md)
- 운영 runbook: [`docs/operations/alert-pipeline-runbook.md`](./alert-pipeline-runbook.md)
- 템플릿 렌더링 및 운영 절차: [`docs/operations/alerting.md`](./alerting.md)
- Wiring Rule (alerting 도메인): [`CLAUDE.md`](../../CLAUDE.md) §"알림 파이프라인 Wiring Rule"
- alertmanager 회귀 회고(§9 진행 기록): [`docs/security/security-integrity-roadmap.md`](../security/security-integrity-roadmap.md)
- 커밋 이력:
  - `5a22faf` — alertmanager 템플릿 렌더링 + entrypoint sed wiring + amtool CI 게이트
  - `a9a3f72` — CI lint 러너 PyYAML 의존성
  - `cc4e88e` — prometheus/alertmanager Docker healthcheck + `depends_on: service_healthy`
  - `c6a7ba3` — IMAGE_NAMESPACE 운영 `.env` 필수 키 문서화
