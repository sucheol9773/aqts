# 런북: KIS API 타임아웃 / 장애 대응

## 증상

- backend 로그에 `KIS API timeout` 또는 `httpx.ReadTimeout` 반복
- Circuit breaker 상태가 `OPEN` (자동 차단)
- `GET /api/system/circuit-breakers` 에서 `kis` 상태 확인 가능
- WebSocket 연결 끊김 시 `KIS WebSocket disconnected` 로그

## Circuit Breaker 동작 (자동)

KIS API circuit breaker 설정:

| 파라미터 | 값 |
|---|---|
| failure_threshold | 5회 연속 실패 |
| recovery_timeout | 60초 |
| half_open_max_calls | 2회 |

상태 전이: `CLOSED` (정상) → 5회 실패 → `OPEN` (차단) → 60초 후 → `HALF_OPEN` (2회 시험) → 성공 → `CLOSED`

**OPEN 상태에서는 모든 KIS API 호출이 즉시 실패 반환**되어 불필요한 타임아웃 대기를 방지한다.

## 1단계: 상태 확인

```bash
# circuit breaker 상태 조회
curl -s http://localhost:8000/api/system/circuit-breakers | python3 -m json.tool

# KIS API 관련 최근 에러
docker compose logs aqts-backend --tail=500 | grep -i "kis\|circuit\|timeout"

# WebSocket 상태
docker compose logs aqts-backend --tail=200 | grep -i "websocket"
```

## 2단계: KIS API 측 장애인지 확인

```bash
# KIS 개발자센터 공지 확인: https://apiportal.koreainvestment.com
# 정기 점검 시간: 평일 05:00~05:30 (장 시작 전)

# 네트워크 레벨 확인
docker compose exec aqts-backend python3 -c "
import httpx
try:
    r = httpx.get('https://openapi.koreainvestment.com:9443', timeout=5)
    print(f'Status: {r.status_code}')
except Exception as e:
    print(f'Error: {e}')
"
```

## 3단계: Circuit Breaker 수동 리셋 (API 복구 확인 후)

KIS API 가 정상화되었으나 circuit breaker 가 아직 OPEN 인 경우:

```bash
# recovery_timeout(60s) 경과를 기다리거나
# backend 재시작으로 breaker 초기화
docker compose restart aqts-backend

# 재시작 후 health 확인
sleep 10
curl -s http://localhost:8000/api/system/health | python3 -m json.tool
```

## 4단계: 부분 체결 상태 확인

타임아웃이 주문 제출 중 발생한 경우:

```bash
# 1. 최근 SUBMITTED/PARTIAL 상태 주문 확인
docker compose exec aqts-postgres psql -U aqts_user -d aqts -c "
  SELECT order_id, ticker, side, quantity, filled_quantity, status, created_at
  FROM orders
  WHERE status IN ('SUBMITTED', 'PARTIAL')
  ORDER BY created_at DESC
  LIMIT 10;
"

# 2. settlement poller 가 자동으로 체결 상태를 업데이트하지만,
#    KIS API 가 다운이면 poller 도 실패한다.
#    KIS 복구 후 poller 가 자동 재시도한다.

# 3. 수동 대사: 브로커 포지션 vs 내부 원장
docker compose logs aqts-backend --tail=200 | grep -i "reconcil"
```

## WebSocket 재연결 실패 시

```bash
# WebSocket 은 자동 재연결 로직이 있으나, 장시간 실패 시:
docker compose logs aqts-backend --tail=100 | grep "WebSocket"

# 강제 재연결: backend 재시작
docker compose restart aqts-backend

# 실시간 호가 수신 복구 확인
docker compose logs aqts-backend --tail=50 | grep -i "subscri"
```

## 거래 시간대별 영향

| 시간대 | 영향 | 대응 |
|---|---|---|
| 장 시작 전 (09:00 이전) | 낮음 — 주문 없음 | 모니터링, 09:00 전 복구 목표 |
| 장중 (09:00~15:30) | 높음 — 주문/체결 불가 | 스케줄러 정지 + kill switch 검토 |
| 장 마감 후 (15:30 이후) | 낮음 — 미체결 처리만 영향 | settlement poller 복구 대기 |

## 예방 조치

- KIS API 정기 점검 시간(05:00~05:30) 에는 스케줄러가 주문을 제출하지 않도록 market_calendar 확인
- circuit breaker 메트릭 모니터링: OPEN 전이 감지 시 Telegram 알림
- 다른 외부 API 도 breaker 보유: FRED(3회/120s), ECOS(3회/120s), Anthropic(3회/90s)
