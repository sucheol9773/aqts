# 런북: 알림 파이프라인 Silent Miss 진단

## 증상

- 예상되는 Telegram 알림이 오지 않음
- `aqts_alert_dispatch` Prometheus 메트릭이 0 또는 증가하지 않음
- 장애 발생했으나 알림 없이 지나감

## 알림 파이프라인 5 레이어 진단 순서

각 레이어를 순서대로 확인한다. **어느 레이어에서 끊겼는지** 특정하는 것이 핵심.

### 레이어 1: 상태 머신 메서드

AlertManager 의 `_dispatch_via_router`, `dispatch_retriable_alerts` 가 호출되는지 확인.

```bash
docker compose logs aqts-backend --tail=1000 | grep -i "dispatch"
# 아무것도 없으면: 알림을 생성하는 상위 로직(EmergencyMonitor 등)이 호출되지 않은 것
```

### 레이어 2: NotificationRouter 인스턴스

`fallback_notifier.py` 의 NotificationRouter 가 lifespan 에서 주입되었는지 확인.

```bash
docker compose logs aqts-backend --tail=500 | grep 'NotificationRouter wired'
# 출력 없으면: backend/main.py lifespan 에서 set_notification_router 가 실행되지 않음
# → backend 재시작 필요
```

### 레이어 3: 재시도 루프

`_alert_retry_loop` asyncio task 가 기동되었는지 확인.

```bash
docker compose logs aqts-backend --tail=500 | grep 'AlertRetryLoop started'
# 출력 없으면: lifespan 에서 asyncio.create_task 가 실행되지 않음
# → backend 재시작 필요
```

### 레이어 4: Prometheus 메트릭 훅

NotificationRouter.dispatch 의 try/finally 에서 메트릭이 기록되는지 확인.

```bash
curl -s http://localhost:8000/metrics | grep -c 'aqts_alert_dispatch'
# 0 이면: 메트릭 자체가 등록되지 않음 → monitoring/metrics.py import 누락 가능
# 1 이상이면: 메트릭은 등록됨, 값 확인:

curl -s http://localhost:8000/metrics | grep 'aqts_alert_dispatch'
# ALERT_DISPATCH_LATENCY_SECONDS 히스토그램이 있어야 함
```

### 레이어 5: 메타알림 규칙

Prometheus → Alertmanager 규칙이 로드되었는지 확인.

```bash
# Prometheus 에서 rule groups 확인
curl -s http://localhost:9090/api/v1/rules | python3 -c "
import json, sys
data = json.load(sys.stdin)
groups = data.get('data', {}).get('groups', [])
print(f'Rule groups loaded: {len(groups)}')
for g in groups:
    print(f'  - {g[\"name\"]}: {len(g[\"rules\"])} rules')
"
# groups 가 0 이면: prometheus.yml 의 rule_files 경로가 잘못됨
# → 절대경로인지 확인 (상대경로 silent miss 회귀 방지)

# Alertmanager 도달 확인
curl -s http://localhost:9093/api/v2/alerts | python3 -m json.tool
```

## Fallback 채널 상태 확인

알림 전송 실패 시 fallback 체인: Telegram → File → Console

```bash
# Telegram 전송 실패 확인
docker compose logs aqts-backend --tail=500 | grep -i "telegram\|fallback"

# File fallback 로그 확인
docker compose exec aqts-backend ls -la logs/alerts/ 2>/dev/null
docker compose exec aqts-backend cat logs/alerts/alerts_$(date +%Y-%m-%d).jsonl 2>/dev/null | tail -5

# 채널 건강 상태 (consecutive_failures ≥ 5 → DOWN)
docker compose logs aqts-backend --tail=200 | grep -i "channel.*status\|DEGRADED\|DOWN"
```

## Telegram 연결 문제 진단

```bash
# Telegram Bot API 연결 테스트 (실제 토큰은 .env 에서)
docker compose exec aqts-backend python3 -c "
import httpx, os
token = os.environ.get('TELEGRAM_BOT_TOKEN', '')
chat_id = os.environ.get('TELEGRAM_CHAT_ID', '')
if not token:
    print('TELEGRAM_BOT_TOKEN not set')
else:
    r = httpx.get(f'https://api.telegram.org/bot{token}/getMe', timeout=5)
    print(f'Bot API status: {r.status_code}')
    print(f'Response: {r.json()}')
"
```

## 복구 절차

```bash
# 대부분의 경우 backend 재시작으로 레이어 2~4 복구
docker compose restart aqts-backend

# 30초 대기 후 5레이어 전체 재검증
sleep 30
echo "=== Layer 2 ===" && docker compose logs aqts-backend --tail=100 | grep 'NotificationRouter wired'
echo "=== Layer 3 ===" && docker compose logs aqts-backend --tail=100 | grep 'AlertRetryLoop started'
echo "=== Layer 4 ===" && curl -s http://localhost:8000/metrics | grep -c 'aqts_alert_dispatch'
echo "=== Layer 5 ===" && curl -s http://localhost:9090/api/v1/rules | python3 -c "
import json,sys; d=json.load(sys.stdin); print(f'groups: {len(d.get(\"data\",{}).get(\"groups\",[]))}')
"
```

## 과거 회귀 사례

- **Prometheus rule_files 상대경로 (2026-04-16)**: config 이동 시 상대경로 resolve 기준이 바뀌며 39 rule 전체 로드 실패. 절대경로로 고정.
- **compose change-detection 미감지 (2026-04-16)**: bind-mount 파일만 수정한 배포에서 `docker compose up -d` 가 recreate 하지 않아 구 config 로 1시간 운영. CD 에서 조건부 `restart prometheus` + rules groups≥1 어서트로 방어.
- **PYTHONUNBUFFERED 미설정 (2026-04-15)**: scheduler stdout 이 49분간 버퍼링되어 `docker compose logs scheduler` 가 비어 보임.
