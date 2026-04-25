# 런북: PostgreSQL 장애 대응

## 증상

- backend 로그에 `Connection refused :5432` 또는 `asyncpg.ConnectionDoesNotExistError` 반복
- `GET /api/system/health` 응답에서 PostgreSQL 상태가 `UNHEALTHY`
- `ready_for_trading: false`

## 1단계: 상태 확인

```bash
# 컨테이너 상태
docker compose ps aqts-postgres

# 최근 로그 (OOM/disk full 확인)
docker compose logs aqts-postgres --tail=200

# health check 직접 실행
docker compose exec aqts-postgres pg_isready -U aqts_user -d aqts

# 디스크 사용량 (volume: postgres_data)
docker compose exec aqts-postgres df -h /var/lib/postgresql/data
```

## 2단계: 재시작 시도

```bash
docker compose restart aqts-postgres

# 30초 대기 후 health check 확인 (interval: 10s, retries: 5 → 최대 50s)
sleep 30
docker compose exec aqts-postgres pg_isready -U aqts_user -d aqts

# backend 에서 연결 복구 확인
curl -s http://localhost:8000/api/system/health | python3 -m json.tool
```

## 3단계: 재시작 실패 시

```bash
# 컨테이너 완전 재생성
docker compose down aqts-postgres
docker compose up -d aqts-postgres

# volume 상태 확인 (데이터 유실 여부)
docker volume inspect aqts_postgres_data

# WAL archive 확인
docker volume inspect aqts_postgres_wal_archive
```

## 4단계: 데이터 복구 필요 시

```bash
# 최신 백업 확인
ls -lt ${BACKUP_DIR}/pg/aqts_pg_*.sql | head -5

# 기존 연결 종료 + 복구 (restore_db.sh 사용)
bash scripts/restore_db.sh <backup_file>

# 복구 후 alembic head 확인 (현재 009)
docker compose exec aqts-backend alembic -c alembic.ini current
docker compose exec aqts-backend alembic -c alembic.ini upgrade head </dev/null
```

## 거래 중 발생 시 (추가 절차)

```bash
# 1. 스케줄러 즉시 정지 (신규 주문 방지)
docker compose stop aqts-scheduler

# 2. 미체결 주문 확인 (Redis idempotency store)
docker compose exec aqts-redis redis-cli -a ${REDIS_PASSWORD} \
  KEYS "aqts:order_idem:*"

# 3. KIS API 에서 실제 체결 상태 수동 조회
#    → backend 로그에서 마지막 order_id 추출
docker compose logs aqts-backend --tail=500 | grep "ORDER_EXECUTED"

# 4. DB 복구 후 대사(reconciliation) 수동 실행
#    → 브로커 포지션 vs 내부 원장 비교
#    mismatch 시 TradingGuard kill switch 자동 발동 (threshold: 0)
```

## 예방 조치

- `backup_cron.sh`가 정상 동작 중인지 주기 확인 (기본 보존: 7일)
- `docker compose logs aqts-db-backup --tail=50` 으로 마지막 백업 성공 확인
- PostgreSQL `pool_pre_ping=True` + `pool_recycle=3600` 설정으로 idle 연결 자동 정리 중
- 디스크 사용량 80% 이상 시 Prometheus alert 확인
