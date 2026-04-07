# KIS degraded → healthy 자동 복원

## 1. 배경

진단 로깅 PR(`docs/operations/kis-token-diagnostics.md`) 로 KIS 토큰 발급 실패 원인
이 가시화됐지만, 한 번 `app.state.kis_degraded = True` 가 되면 컨테이너가 재시작될
때까지 영구히 degraded 로 남는 회귀가 그대로였다. lifespan startup 단계에서 KIS 의
일시적 4xx (특히 `EGW00133` — 1분 1회 토큰 발급 제한) 에 한 번 걸리면, 이후 정상
응답이 가능해도 health 가 계속 `kis_api: degraded` 를 출력한다.

본 변경은 health 엔드포인트가 호출될 때마다(쿨다운이 만료된 시점에 한해서) 토큰
재발급을 시도하고, 성공하면 글로벌 KIS 클라이언트를 교체 + degraded 플래그를 해제
하는 자동 복원 경로를 도입한다.

## 2. 설계

### 2.1 모듈 분리

- 신규 모듈: `backend/core/data_collector/kis_recovery.py`
  - `KISRecoveryState` dataclass — degraded 여부, `next_attempt_at`, `last_error`,
    `attempt_count`, `recovery_count`, `cooldown_seconds`, `asyncio.Lock`.
  - `try_recover_kis(state, client_factory, now=None)` — 비동기 함수. 쿨다운 만료
    여부 확인 → lock 진입 → 재확인(double-check) → factory 호출 → 성공/실패 분기.
- 본 모듈은 **FastAPI app.state 에 의존하지 않는다**. 순수 dataclass 와 비동기 콜백
  으로만 동작하므로 단위 테스트에서 FastAPI 부팅 없이 검증 가능하다. wiring 책임은
  `main.py` 가 진다.

### 2.2 쿨다운 정책

기본값 `DEFAULT_COOLDOWN_SECONDS = 75`. KIS 의 1분 제한보다 약 15초 여유를 둬서
경계선 충돌을 피한다. 환경변수 `KIS_RECOVERY_COOLDOWN_SECONDS` 로 조정 가능
(int 파싱 실패 시 기본값으로 폴백 + 경고 로그).

쿨다운 동작:
- `mark_degraded(error, now)` 호출 시 `next_attempt_at = now + cooldown_seconds` 로
  첫 시도 시점을 미래로 밀어둔다 (즉시 시도 금지 — startup 직후 EGW00133 의 직접
  원인이 1분 제한이므로 최소 한 차례 대기 필요).
- 시도 실패 시에도 동일한 쿨다운으로 `next_attempt_at` 을 재스케줄.

### 2.3 동시성

health 엔드포인트는 Prometheus / k8s probe / 사용자 호출로 동시 다발적으로 실행될
수 있다. `try_recover_kis` 는 다음 두 단계로 직렬화한다.

1. 락 진입 전: `next_attempt_at` 확인 — 쿨다운 미만이면 즉시 None.
2. `async with state.lock:` 진입 후 같은 조건을 한 번 더 확인 (double-checked
   locking). 첫 호출자가 이미 복원에 성공해서 `degraded=False` 가 됐으면 두 번째
   호출은 None 으로 빠져나온다.

이 패턴 덕분에 동시 N 개의 health 호출이 들어와도 KIS API 호출은 정확히 1번만
이루어진다.

### 2.4 main.py wiring

```python
# startup
kis_recovery_state = KISRecoveryState(cooldown_seconds=cooldown)
app.state.kis_recovery_state = kis_recovery_state
try:
    kis_client = KISClient()
    if not settings.kis.is_backtest:
        await kis_client._token_manager.get_access_token()
except Exception as e:
    kis_client = None
    app.state.kis_degraded = True
    kis_recovery_state.mark_degraded(str(e))

# health_check
if app.state.kis_degraded and state_obj is not None and not settings.kis.is_backtest:
    async def _kis_client_factory() -> KISClient:
        client = KISClient()
        await client._token_manager.get_access_token()
        return client
    recovered = await try_recover_kis(state_obj, _kis_client_factory)
    if recovered is not None:
        global kis_client
        kis_client = recovered
        app.state.kis_degraded = False
```

`backtest` 모드에서는 KIS 토큰이 필요 없으므로 복원 시도 자체를 건너뛴다.

## 3. 테스트

신규 파일: `backend/tests/test_kis_recovery.py` — 10 케이스.

| 카테고리 | 케이스 | 검증 |
|----------|--------|------|
| `KISRecoveryState` | `test_default_state_is_healthy` | 초기 상태 무결성 |
| `KISRecoveryState` | `test_mark_degraded_sets_next_attempt` | degraded 진입 + 쿨다운 스케줄 |
| `KISRecoveryState` | `test_mark_recovered_clears_state_and_increments_count` | 복원 후 카운터 |
| `try_recover_kis` | `test_returns_none_when_not_degraded` | degraded 아님 → 즉시 None, factory 미호출 |
| `try_recover_kis` | `test_returns_none_within_cooldown` | 쿨다운 미만 → factory 미호출 |
| `try_recover_kis` | `test_recovery_success_replaces_client_and_clears_degraded` | 성공 시 새 KISClient 반환 + 카운터 |
| `try_recover_kis` | `test_recovery_failure_reschedules_and_keeps_degraded` | KISAPIError 시 재스케줄 + 메시지 보존 |
| `try_recover_kis` | `test_recovery_failure_with_generic_exception_uses_type_name` | 임의 예외도 graceful |
| `try_recover_kis` | `test_concurrent_recovery_serializes_to_single_attempt` | 동시 호출 시 factory 정확히 1회 |
| `try_recover_kis` | `test_attempt_then_cooldown_then_retry_succeeds` | 실패→쿨다운→성공 시퀀스 |

CLAUDE.md 의 **유닛테스트 작성 규칙** 에 따라 모든 케이스는 실제 기대값(반환값,
카운터, 호출 횟수) 을 검증하며 단순 통과를 위한 expectation 조정은 없다.

## 4. 검증 절차

```bash
cd backend
python -m ruff check . --config pyproject.toml          # 0 errors
python -m black --check . --config pyproject.toml       # All done
python -m pytest tests/ -q --no-cov                     # 3274 passed
python ../scripts/gen_status.py --update                # doc-sync 갱신
```

## 5. 운영 시나리오

1. **CD 배포 직후 EGW00133 충돌**: lifespan startup 이 KISAPIError(EGW00133) 로
   실패 → degraded + 75초 쿨다운 스케줄 → 다음 health 호출(또는 75s 후 probe)에서
   재발급 시도 → 성공 시 클라이언트 교체 + degraded 해제. 컨테이너 재시작 불필요.
2. **KIS 점검/네트워크 장애**: 회복 시도가 계속 실패 → `attempt_count` 증가, last_error
   에 사유 누적. 회복 후 `recovery_count` 증가. (Prometheus 메트릭 노출은 후속 PR.)
3. **backtest 모드**: KIS 토큰 자체가 필요 없는 모드. 복원 경로는 비활성.

## 6. Prometheus 메트릭

`core/monitoring/metrics.py` 에 다음 3개를 노출 (`/metrics` 엔드포인트로 자동 수집).

| 이름 | 타입 | 의미 |
|------|------|------|
| `aqts_kis_recovery_attempts_total` | Counter | 쿨다운 만료 후 실제 토큰 재발급을 시도한 횟수. 쿨다운으로 스킵된 호출은 제외. |
| `aqts_kis_recovery_success_total` | Counter | 그중 성공한 횟수. `mark_recovered()` 시점에 +1. |
| `aqts_kis_degraded` | Gauge | 현재 KIS 가 degraded(1) / healthy(0). `mark_degraded()` 시 1, `mark_recovered()` 시 0. |

연결 방식은 `core/data_collector/kis_recovery.py` 안에 lazy import 된 `_record_*`
헬퍼로 격리되어 있어, 메트릭 모듈 import 실패 시에도 회복 경로는 그대로 동작한다
(예외는 silently swallow). 시크릿/키 라벨은 절대 두지 않는다.

대시보드/알림 룰 예시:
- "지난 1시간 동안 회복 성공 횟수": `increase(aqts_kis_recovery_success_total[1h])`
- "현재 KIS degraded?": `aqts_kis_degraded == 1`
- "1시간 동안 회복 실패율":
  `1 - rate(aqts_kis_recovery_success_total[1h]) / rate(aqts_kis_recovery_attempts_total[1h])`

## 7. Startup jittered backoff

`core/data_collector/kis_startup.py::jittered_token_issue` 가 lifespan startup
시점의 토큰 발급 호출을 감싼다. 동시 부팅 컨테이너들이 KIS 발급 윈도우를 균등
하게 나눠 쓰도록 `[0, jitter_max_seconds)` 구간 균등분포 jitter 후 1회 발급한다.

| 환경변수 | 기본 | 의미 |
|----------|------|------|
| `KIS_STARTUP_JITTER_MAX_SECONDS` | `15.0` | jitter 상한 (초). `0` 이하면 비활성, 기존 동작 유지. |

설계 결정:
- **in-startup 재시도는 두지 않는다.** 1차 실패는 그대로 degraded 진입 → 75초
  쿨다운 후 health_check 의 `try_recover_kis()` 가 회복을 책임진다. 책임 분리
  (single-purpose 모듈) + k8s readiness probe 와의 충돌 회피.
- **테스트 가능성**: `sleep_fn` / `random_fn` 을 주입 가능하게 하여 실제 시간
  대기 없이 단위 테스트로 검증한다 (`tests/test_kis_startup.py`, 7 cases).
- **운영 영향**: 평균 startup 지연 ≈ jitter_max/2 (기본 7.5s). 상한 15s 는 일반
  적인 readiness probe 임계(30s+) 보다 충분히 작다.

기대 효과:
- N 개 컨테이너가 동시에 부팅할 때 토큰 발급 호출이 [0, 15s) 균등 분산되어,
  KIS 1분 1회 제한 윈도우 안에서 충돌하는 컨테이너 수를 줄인다.
- 효과 측정은 §6 의 `aqts_kis_degraded` 게이지가 1로 진입하는 빈도로 가능.

## 8. 비범위 (별도 PR 로 분리)

본 PR 은 **자동 복원 + 메트릭 + startup jitter** 까지 책임진다. 아래는 별도 후속.

- 회복이 N 회 연속 실패 시 알림(웹훅/슬랙) 트리거.

CLAUDE.md 의 **"bug fix 커밋에 무관한 변경 끼워넣기 금지"** 원칙에 따라 책임 범위
를 분리한다.

## 9. 변경 파일

- 신규: `backend/core/data_collector/kis_recovery.py`
- 신규: `backend/tests/test_kis_recovery.py`
- 신규: 본 문서 `docs/operations/kis-degraded-recovery.md`
- 수정: `backend/main.py` (startup 단계 + health_check 단계 wiring)
- 갱신: `docs/FEATURE_STATUS.md`, `README.md`, `docs/operations/release-gates.md`
  (gen_status.py 자동 갱신, 테스트 수 3265 → 3274)

Last reviewed: 2026-04-07
