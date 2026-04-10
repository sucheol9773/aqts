# 알림 파이프라인 Runbook

**작성일**: 2026-04-10
**대상**: AQTS 운영자
**아키텍처 참조**: [`docs/architecture/notification-pipeline.md`](../architecture/notification-pipeline.md)

---

## 1. 정상 상태 기준선 (Baseline)

다음 세 가지가 모두 확인돼야 파이프라인이 정상이다.

```bash
# 1) NotificationRouter wiring 확인
docker compose logs backend --tail=500 | grep 'NotificationRouter wired'
# 기대: "NotificationRouter wired: telegram → file → console cascade"

# 2) AlertRetryLoop 기동 확인
docker compose logs backend --tail=500 | grep 'AlertRetryLoop started'
# 기대: "AlertRetryLoop started (interval=60s)"

# 3) Prometheus 지표 노출 확인
curl -s http://localhost:8000/metrics | grep -c 'aqts_alert_dispatch'
# 기대: 4 이상 (Counter 2 + Histogram 2)
```

---

## 2. 메타알림 발화 시 대응

### 2.1 AlertPipelineFailureRate (severity: critical)

**의미**: 지난 5분간 알림 디스패치 실패율이 50% 를 초과했다.

**원인 특정**:

```bash
# 채널별 실패 건수 확인 — 어떤 채널이 실패하는지
curl -s http://localhost:8000/metrics | grep 'aqts_alert_dispatch_total'
```

| 패턴 | 원인 | 대응 |
|---|---|---|
| `telegram` failure 만 높음 | Telegram Bot API 장애 또는 토큰/chat_id 오류 | §2.1.1 참조 |
| 모든 채널 failure | 네트워크 장애 또는 Alert 직렬화 오류 | 백엔드 로그 확인 |
| `file` failure | 디스크 풀 또는 권한 문제 | `df -h`, 로그 디렉토리 확인 |

**§2.1.1 Telegram 채널 점검**:

```bash
# Bot 토큰 유효성 확인
curl -s "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/getMe" | python3 -m json.tool

# chat_id 로 테스트 메시지 발송
curl -s -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
  -d chat_id="${TELEGRAM_CHAT_ID}" \
  -d text="[AQTS runbook] Telegram connectivity test"
```

정상 응답이 오면 Bot 연결은 문제없다 → 백엔드 로그에서 디스패치 예외를 확인:

```bash
docker compose logs backend --tail=500 | grep -iE 'dispatch.*error|dispatch.*exception|telegram.*fail'
```

### 2.2 AlertPipelineDeadTransitions (severity: warning)

**의미**: 지난 30분 내에 3회 재시도를 소진하고 DEAD 로 전이된 알림이 있다.

**즉시 확인**:

```bash
# DEAD 알림 조회 (최근 10건)
docker exec aqts-mongodb mongosh -u "$MONGO_INITDB_ROOT_USERNAME" -p "$MONGO_INITDB_ROOT_PASSWORD" --authenticationDatabase admin --quiet --eval '
  db.alerts.find(
    { status: "DEAD" },
    { _id: 1, alert_type: 1, title: 1, send_attempts: 1, last_send_error: 1, created_at: 1 }
  ).sort({ created_at: -1 }).limit(10).toArray()
'
```

**수동 재처리** (원인 해소 후):

```bash
# 특정 알림 1건을 PENDING 으로 강제 리셋
docker exec aqts-mongodb mongosh -u "$MONGO_INITDB_ROOT_USERNAME" -p "$MONGO_INITDB_ROOT_PASSWORD" --authenticationDatabase admin --quiet --eval '
  db.alerts.updateOne(
    { _id: ObjectId("<ALERT_ID>"), status: "dead" },
    { $set: { status: "PENDING", send_attempts: 0, last_send_error: null } }
  )
'

# DEAD 알림 전체를 PENDING 으로 일괄 리셋 (주의: 원인 해소 확인 후에만)
docker exec aqts-mongodb mongosh -u "$MONGO_INITDB_ROOT_USERNAME" -p "$MONGO_INITDB_ROOT_PASSWORD" --authenticationDatabase admin --quiet --eval '
  const result = db.alerts.updateMany(
    { status: "DEAD" },
    { $set: { status: "PENDING", send_attempts: 0, last_send_error: null } }
  );
  print("Reset count: " + result.modifiedCount);
'
```

리셋 후 재시도 루프가 다음 60초 주기에 자동으로 재픽업한다.

---

## 3. 재시도 루프 무력화 절차

파이프라인 자체가 장애를 확산시키는 경우 (예: Telegram rate limit 폭주, 무한 실패 루프) 에 사용한다.

### 3.1 무력화

```bash
# .env 에 환경변수 추가 (또는 기존 값을 false 로 변경)
echo "ALERT_RETRY_LOOP_ENABLED=false" >> /opt/aqts/.env

# backend 만 재기동 (scheduler 에는 영향 없음)
docker compose -f docker-compose.yml up -d backend

# 확인: "AlertRetryLoop started" 가 출력되지 않아야 함
docker compose logs backend --tail=100 | grep -i alertretryloop
```

**무력화 후 상태**:
- `create_and_persist_alert` 의 즉시 디스패치 경로는 **그대로 동작** (Router wiring 은 살아있음)
- 실패한 알림은 FAILED 에 쌓이지만 자동 재시도는 안 됨 → 수동 재처리 필요 (§2.2)
- Prometheus 지표는 Router 디스패치가 발생하면 계속 증가 (루프만 멈춘 것)

### 3.2 복원

```bash
# .env 에서 false 를 true 로 변경 (또는 해당 줄 삭제 — 기본값이 true)
sed -i 's/ALERT_RETRY_LOOP_ENABLED=false/ALERT_RETRY_LOOP_ENABLED=true/' /opt/aqts/.env

# backend 재기동
docker compose -f docker-compose.yml up -d backend

# 확인
docker compose logs backend --tail=100 | grep 'AlertRetryLoop started'
# 기대: "AlertRetryLoop started (interval=60s)"
```

---

## 4. NotificationRouter wiring 결손 대응

기동 로그에 `NotificationRouter wired` 가 없으면 Router 주입이 실패한 것이다. 서버는 정상 기동되지만 **모든 알림이 in-memory 에만 저장되고 Telegram 으로 나가지 않는다**.

**원인 진단**:

```bash
# wiring 실패 warning 확인
docker compose logs backend --tail=500 | grep -iE 'warning.*notification|warning.*router|warning.*telegram'
```

| 에러 메시지 | 원인 | 대응 |
|---|---|---|
| `TELEGRAM_BOT_TOKEN not set` | 환경변수 누락 | `.env` 에 토큰 추가 후 재기동 |
| `TELEGRAM_CHAT_ID not set` | 환경변수 누락 | `.env` 에 chat_id 추가 후 재기동 |
| `TelegramChannelAdapter init failed` | 토큰 유효성 검증 실패 | §2.1.1 의 Bot 토큰 확인 절차 실행 |
| warning 없음 | lifespan wiring 블록 도달 전에 다른 오류로 중단 | `docker compose logs backend --tail=500 | grep -i error` 로 전체 기동 오류 확인 |

---

## 5. 재시도 백오프 스케줄

| send_attempts | 다음 재시도까지 대기 | 누적 경과 시간 |
|---|---|---|
| 1 (1차 실패) | 60초 | +1분 |
| 2 (2차 실패) | 300초 | +6분 |
| 3 (3차 실패) | **DEAD** (재시도 없음) | terminal |

루프 주기가 60초이므로 실제 재시도 시점은 위 대기 시간 + 최대 60초 오차가 있을 수 있다.

---

## 6. 모니터링 대시보드 쿼리 (Grafana 참고)

```promql
# 채널별 디스패치 성공률 (5분 이동 평균)
sum(rate(aqts_alert_dispatch_total{result="success"}[5m])) by (channel)
/
clamp_min(sum(rate(aqts_alert_dispatch_total[5m])) by (channel), 1e-9)

# 채널별 p95 레이턴시
histogram_quantile(0.95, sum(rate(aqts_alert_dispatch_latency_seconds_bucket[5m])) by (le, channel))

# DEAD 전이 속도
rate(aqts_alert_retry_dead_total[30m])

# 현재 FAILED 상태 알림 수 (MongoDB 직접 조회)
# docker exec aqts-mongodb mongosh -u "$MONGO_INITDB_ROOT_USERNAME" -p "$MONGO_INITDB_ROOT_PASSWORD" --authenticationDatabase admin --quiet --eval 'db.alerts.countDocuments({ status: "FAILED" })'
```

---

## 7. 관련 문서

- 아키텍처: [`docs/architecture/notification-pipeline.md`](../architecture/notification-pipeline.md)
- 감사 기록: [`docs/operations/alerting-audit-2026-04.md`](./alerting-audit-2026-04.md)
- Wiring Rule: [`CLAUDE.md`](../../CLAUDE.md) §"알림 파이프라인 Wiring Rule"
- 기존 운영 절차: [`docs/operations/alerting.md`](./alerting.md)
