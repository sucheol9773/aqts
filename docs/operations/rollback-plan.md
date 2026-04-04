# 배포 롤백 계획 (Deployment Rollback Plan)

**문서 번호**: OPS-005
**버전**: 1.0
**최종 수정**: 2026-04-05
**승인자**: 운영책임자

## 1. 목적

프로덕션 배포 후 장애·성능 저하·예기치 않은 동작 발생 시, 이전 안정 버전으로 신속·안전하게 복구하는 절차를 정의합니다.

## 2. 롤백 트리거 조건

| 조건 | 등급 | 판정 기준 | 자동/수동 |
|------|------|----------|----------|
| 핵심 API 응답 불가 | SEV-1 | 헬스체크 3회 연속 UNHEALTHY | 자동 |
| 주문 실행 장애 | SEV-1 | 주문 3건 연속 실패 | 자동 |
| 데이터베이스 마이그레이션 실패 | SEV-1 | Alembic 롤백 필요 | 수동 |
| 성능 저하 | SEV-2 | API 응답 시간 p95 > 3초 (5분 지속) | 수동 |
| 비정상 매매 동작 | SEV-2 | 예상 외 주문 발생 또는 포지션 이상 | 수동 (즉시 매매 중단 후) |
| 에러율 급증 | SEV-2 | 5xx 에러율 > 5% (3분 지속) | 자동 |

## 3. 롤백 전 필수 조치

```
1. 매매 즉시 중단
   → POST /api/system/halt 또는 Telegram /halt
   → PipelineStateMachine → HALTED 전이 확인

2. 미체결 주문 일괄 취소
   → KIS API cancel_order (전 종목)
   → 취소 확인 후 감사 로그 기록

3. 현재 포지션 스냅샷 저장
   → PostgreSQL positions 테이블 스냅샷
   → Redis 캐시 상태 덤프
   → 타임스탬프 기록 (롤백 이전 시점 증거 보존)
```

## 4. 롤백 절차

### 4.1 애플리케이션 롤백 (Docker 기반)

```bash
# 1. 현재 버전 확인
docker ps --format "{{.Image}}" | grep aqts

# 2. 이전 안정 이미지로 롤백
export ROLLBACK_TAG="v$(cat .last_stable_version)"
docker compose down
docker compose -f docker-compose.yml up -d --no-build \
  -e IMAGE_TAG=$ROLLBACK_TAG

# 3. 헬스체크 확인 (30초 대기 후)
curl -s http://localhost:8000/api/system/settings | jq '.trading_mode'

# 4. 로그 확인
docker logs aqts-backend --tail 50 --since 1m
```

### 4.2 데이터베이스 롤백

```bash
# PostgreSQL 마이그레이션 롤백 (Alembic)
docker exec aqts-backend alembic downgrade -1

# 롤백 확인
docker exec aqts-backend alembic current

# MongoDB는 스키마리스 — 호환성 문제 시 컬렉션 복구
mongorestore --uri="$MONGODB_URI" --db=aqts --drop /backup/pre_deploy/
```

### 4.3 설정 롤백

```bash
# .env 파일 복원
cp .env.backup.pre_deploy .env

# Redis 캐시 초기화 (stale 데이터 방지)
docker exec aqts-redis redis-cli -a $REDIS_PASSWORD FLUSHDB

# 설정 재로드
docker compose restart backend
```

## 5. 롤백 후 검증 체크리스트

| 항목 | 검증 방법 | 기대 결과 |
|------|----------|----------|
| 헬스체크 | GET /api/system/settings | HEALTHY 응답 |
| DB 연결 | PostgreSQL/MongoDB/Redis ping | 전체 응답 정상 |
| API 응답 | GET /api/portfolio/summary | 200 OK |
| 매매 모드 | trading_mode 확인 | BACKTEST 또는 DEMO |
| 감사 로그 | 롤백 이벤트 기록 확인 | SYSTEM_ROLLBACK 엔트리 존재 |
| 알림 발송 | Telegram 테스트 메시지 | 정상 수신 |

## 6. 매매 재개 절차

```
1. 롤백 후 검증 체크리스트 전 항목 PASS
   ↓
2. 운영책임자 승인 (Telegram 확인 또는 승인 API)
   ↓
3. BACKTEST 모드에서 최근 데이터 파이프라인 1회 실행
   ↓
4. 결과 정상 확인 후 DEMO 모드 전환
   ↓
5. DEMO 30분 정상 가동 확인
   ↓
6. LIVE 모드 전환 (운영책임자 최종 승인)
   ↓
7. 정상 매매 재개 알림 발송
```

## 7. 롤백 실패 시 에스컬레이션

| 단계 | 조건 | 담당 | 조치 |
|------|------|------|------|
| L1 | 롤백 10분 내 미복구 | 개발 리드 | 수동 디버깅 + 대체 버전 배포 |
| L2 | 롤백 30분 내 미복구 | 운영책임자 | 전체 서비스 중단 결정 |
| L3 | 데이터 손실 의심 | 경영진 | 외부 전문가 투입 + 고객 공지 |

## 8. 배포 전 롤백 준비 체크리스트

배포 실행 전 반드시 아래 항목을 완료해야 합니다:

- [ ] `.last_stable_version` 파일에 현재 안정 버전 태그 기록
- [ ] `.env.backup.pre_deploy` 파일 생성
- [ ] PostgreSQL 전체 백업 (`pg_dump`)
- [ ] MongoDB 전체 백업 (`mongodump`)
- [ ] Redis RDB 스냅샷 (`BGSAVE`)
- [ ] 현재 Docker 이미지 태그 기록
- [ ] 롤백 담당자 지정 + 연락처 확인
- [ ] Telegram 알림 채널 정상 동작 확인

## 9. 자동 롤백 (Canary 배포)

향후 Canary 배포 도입 시 자동 롤백 기준:

```yaml
canary:
  initial_weight: 10%        # 초기 10% 트래픽
  promotion_interval: 5m     # 5분 간격 승격
  max_weight: 100%
  rollback_triggers:
    - error_rate > 5%
    - latency_p95 > 3000ms
    - health_check_failures >= 3
```

---

Last reviewed: 2026-04-05 | Maintained by: AQTS Team
