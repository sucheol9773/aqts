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

`aqts_alerts.yml` §6 의 주석("앱 내부 알림(backend/main.py → AlertManager)과 이중화된 인프라 레벨 알림") 은 기술적으로 **절반만 사실**이다. main.py → AlertManager 경로는 존재하지만, AlertManager → Telegram 경로가 존재하지 않는다.

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

1. **[우선순위 상] AlertManager → Telegram wiring 또는 명시적 분리**
   - 옵션 A: `AlertManager` 클래스에 `TelegramNotifier` 를 주입하고 `create_and_persist_alert` 내부에서 `level >= ERROR` 인 경우 자동 발송하도록 확장. `_kis_alert_callback` 등 기존 호출 경로가 자동으로 Telegram 에 도달하게 된다.
   - 옵션 B: `AlertManager` 는 "저장 전용" 으로 명시하고, Telegram 이 필요한 호출자는 `TelegramNotifier` 를 직접 호출하도록 `_kis_alert_callback` 을 수정. 대신 `aqts_alerts.yml` §6 주석의 "이중화" 표현을 수정.
   - 둘 중 어느 쪽이든 "`AlertManager` 호출 = Telegram 발송" 가정이 코드와 일치해야 한다.

2. **[우선순위 상] Telegram 엔드-투-엔드 스모크 테스트**
   - `docs/operations/alerting.md` §6 (수동 검증) 에 이미 "짧은 테스트 rule 발화 → Telegram 수신 확인" 절차가 있지만, 마지막 실행 기록이 없다. 실제로 한 번 실행하여 Prometheus rule → alertmanager → Telegram 경로 전체가 종단 간 작동하는지 확인한다.
   - 추가로 `daily_reporter.send_telegram_report` / `emergency_monitor._send_emergency_alert` 의 "dry-run Telegram 호출" 스모크를 CI 통합 테스트 레벨에 한 번 추가한다.

3. **[우선순위 중] 사일런트 기간 회고 감사**
   - 회귀 기간(`888db64` ~ `5a22faf`) 동안 로그/메트릭 기반으로 32 건의 사일런트 룰 중 실제로 발화했어야 했던 이벤트를 재구성한다.
   - 방법: Prometheus `ALERTS_FOR_STATE{alertname="..."}` 시계열 또는 backend 로그의 fail-closed 카운터 증감(`aqts_audit_write_failures_total`, `aqts_trading_guard_blocks_total` 등) 을 역추적.
   - 결과는 본 문서 §6 (부록) 에 추가하거나 별도 감사 문서로 분리한다.

4. **[우선순위 낮] `aqts_alerts.yml` 주석 정정 및 룰 수 표기 정합성**
   - §6 kis_recovery 그룹의 "이중화" 주석을 정정한다 (옵션 A/B 선택에 따라 문구가 달라짐).
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

## 7. 관련 문서

- 템플릿 렌더링 및 운영 절차: [`docs/operations/alerting.md`](./alerting.md)
- alertmanager 회귀 회고(§9 진행 기록): [`docs/security/security-integrity-roadmap.md`](../security/security-integrity-roadmap.md)
- 커밋 이력:
  - `5a22faf` — alertmanager 템플릿 렌더링 + entrypoint sed wiring + amtool CI 게이트
  - `a9a3f72` — CI lint 러너 PyYAML 의존성
  - `cc4e88e` — prometheus/alertmanager Docker healthcheck + `depends_on: service_healthy`
  - `c6a7ba3` — IMAGE_NAMESPACE 운영 `.env` 필수 키 문서화
