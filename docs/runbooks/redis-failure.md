# 런북: Redis 장애 대응

## 증상

- backend 로그에 `redis.exceptions.ConnectionError` 또는 `Connection refused :6379`
- 주문 API 가 `503 IdempotencyStoreUnavailable` 반환
- `ORDER_IDEMPOTENCY_STORE_FAILURE_TOTAL` 메트릭 급증

## 영향 범위

Redis 는 두 가지 역할을 담당한다:

| 역할 | 유실 시 영향 |
|---|---|
| 주문 멱등성 스토어 | 모든 주문 API 가 503 반환 (fail-closed). 주문 자체는 불가하나 **중복 주문 위험 없음** |
| 캐시 (시장 데이터, 세션) | warm-up 필요. 일시적 성능 저하만 발생 |

## 1단계: 상태 확인

```bash
# 컨테이너 상태
docker compose ps aqts-redis

# 로그 확인 (OOM, maxmemory 초과)
docker compose logs aqts-redis --tail=100

# health check 직접 실행
docker compose exec aqts-redis redis-cli -a ${REDIS_PASSWORD} ping

# 메모리 사용량
docker compose exec aqts-redis redis-cli -a ${REDIS_PASSWORD} INFO memory \
  | grep -E "used_memory_human|maxmemory_human|maxmemory_policy"
```

## 2단계: 재시작

```bash
docker compose restart aqts-redis

# 10초 대기 후 확인 (health check: interval 10s, retries 5)
sleep 10
docker compose exec aqts-redis redis-cli -a ${REDIS_PASSWORD} ping

# backend 에서 Redis 연결 복구 확인
curl -s http://localhost:8000/api/system/health | python3 -m json.tool
```

## 3단계: 재시작 후 확인 사항

```bash
# 멱등성 키 잔존 확인 (재시작 시 전량 유실됨)
docker compose exec aqts-redis redis-cli -a ${REDIS_PASSWORD} \
  KEYS "aqts:order_idem:*" | wc -l

# claim 상태(__CLAIM__)로 남은 키 → 해당 주문은 30초 TTL 만료 후 재시도 가능
docker compose exec aqts-redis redis-cli -a ${REDIS_PASSWORD} \
  KEYS "aqts:order_idem:*" | while read k; do
    val=$(docker compose exec aqts-redis redis-cli -a ${REDIS_PASSWORD} GET "$k")
    if [ "$val" = "__CLAIM__" ]; then echo "STALE CLAIM: $k"; fi
  done
```

## 4단계: 데이터 warm-up

Redis 재시작 후 캐시가 비어있으므로:

```bash
# 시장 데이터 캐시 재구축은 다음 스케줄러 사이클에서 자동 수행
# 수동 트리거가 필요하면:
docker compose restart aqts-scheduler

# 스케줄러 heartbeat 확인 (stale threshold: 180s)
docker compose exec aqts-scheduler \
  stat /tmp/scheduler.heartbeat 2>/dev/null && echo "heartbeat OK"
```

## 거래 중 발생 시

```bash
# 1. 멱등성 스토어가 503 → 모든 주문이 자동 차단됨 (fail-closed)
#    → 중복 주문 위험 없음, 다만 신규 주문도 불가

# 2. Redis 복구 후 미체결 주문 상태 확인
#    claim TTL=30s 이므로 30초 이내 복구되면 진행 중인 주문은 정상 완료
#    30초 초과 시 claim 만료 → 클라이언트가 재시도하면 새 주문으로 처리됨

# 3. 대사 수동 실행으로 브로커 vs 내부 원장 비교
curl -s http://localhost:8000/api/system/health | python3 -m json.tool
```

## 예방 조치

- `maxmemory` 설정 확인: Redis 컨테이너에 메모리 제한 설정 권장
- Redis persistence (AOF/RDB) 는 volume `redis_data` 로 마운트됨 — 컨테이너 재생성 시에도 데이터 보존
- 멱등성 result TTL 은 24시간 — 일간 키 수는 일일 주문 수와 동일
- 모니터링: `ORDER_IDEMPOTENCY_STORE_FAILURE_TOTAL` 메트릭이 0 이 아니면 즉시 조사
