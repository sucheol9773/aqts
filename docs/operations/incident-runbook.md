# 장애 대응 런북 (Incident Runbook)

**문서 번호**: OPS-002
**버전**: 1.0
**최종 수정**: 2026-04-04

## 1. 장애 등급 정의

| 등급 | 정의 | 예시 | RTO |
|------|------|------|-----|
| SEV-1 (Critical) | 매매 불가 또는 자금 손실 위험 | 주문 실행 장애, DB 데이터 유실 | 5분 |
| SEV-2 (High) | 핵심 기능 저하 | 환율 조회 실패, 분석 파이프라인 중단 | 15분 |
| SEV-3 (Medium) | 부수 기능 장애 | 알림 발송 실패, 감사 로그 지연 | 1시간 |
| SEV-4 (Low) | 경미한 이상 | 대시보드 지연, 리포트 생성 지연 | 4시간 |

## 2. 공통 대응 절차

```
1. 감지 (Prometheus Alert / 사용자 신고 / 로그 이상)
   ↓
2. 초기 분류 (SEV 등급 판정)
   ↓
3. 즉시 조치 (매매 중단 여부 판단)
   ↓
4. 원인 분석
   ↓
5. 복구 조치
   ↓
6. 정상 확인 + 매매 재개
   ↓
7. 사후 분석 (Post-mortem) 작성
```

## 3. 시나리오별 런북

### 3.1 KIS API 연결 장애

**증상**: 주문 실행 타임아웃, HTTP 5xx 응답
**등급**: SEV-1

**진단**:
```bash
# KIS API 상태 확인
curl -v https://openapi.koreainvestment.com:9443/uapi/domestic-stock/v1/quotations/inquire-price

# 백엔드 로그 확인
docker logs aqts-backend --tail 100 | grep -i "kis\|timeout\|connection"

# Redis 캐시 상태
docker exec aqts-redis redis-cli -a $REDIS_PASSWORD get "exchange_rate:USD_KRW"
```

**조치**:
1. 매매 즉시 중단 (`POST /api/system/halt`)
2. KIS API 상태 페이지 확인 (한국투자증권 공지)
3. 토큰 만료 여부 확인 → 재발급 시도
4. 복구 시 소규모 테스트 주문으로 검증 후 재개

### 3.2 PostgreSQL 장애

**증상**: API 500 에러, 주문/포트폴리오 조회 실패
**등급**: SEV-1

**진단**:
```bash
# DB 컨테이너 상태
docker ps | grep postgres
docker logs aqts-postgres --tail 50

# DB 연결 테스트
docker exec aqts-postgres pg_isready -U aqts_user -d aqts

# 디스크 사용량
docker exec aqts-postgres df -h /var/lib/postgresql/data
```

**조치**:
1. 매매 즉시 중단
2. 컨테이너 재시작: `docker restart aqts-postgres`
3. 복구 안 되면 볼륨 상태 확인 + WAL 복구 시도
4. 최악의 경우 백업에서 복원: `pg_restore`

### 3.3 Redis 장애

**증상**: 환율 캐시 미스, 세션 만료, 응답 지연
**등급**: SEV-2

**진단**:
```bash
docker logs aqts-redis --tail 50
docker exec aqts-redis redis-cli -a $REDIS_PASSWORD ping
docker exec aqts-redis redis-cli -a $REDIS_PASSWORD info memory
```

**조치**:
1. 캐시 미스는 자동 fallback (KIS/FRED 직접 조회)으로 서비스 유지
2. `docker restart aqts-redis`
3. 메모리 초과 시 `maxmemory-policy` 확인

### 3.4 분석 파이프라인 ERROR 상태

**증상**: 파이프라인이 ERROR 상태에서 멈춤
**등급**: SEV-2

**진단**:
```bash
# 파이프라인 상태 확인
curl http://localhost:8000/api/system/health

# 백엔드 로그에서 에러 추적
docker logs aqts-backend --tail 200 | grep -i "error\|exception\|traceback"
```

**조치**:
1. 에러 원인 파악 (Anthropic API 한도? 데이터 수집 실패?)
2. `POST /api/system/reset` 으로 IDLE 상태 복귀
3. 원인 해소 후 재실행

### 3.5 Anthropic API 한도 초과

**증상**: Claude 분석 실패, HTTP 429 응답
**등급**: SEV-3

**진단**:
```bash
docker logs aqts-backend --tail 100 | grep -i "anthropic\|429\|rate.limit"
```

**조치**:
1. 분석 파이프라인 자동 재시도 (tenacity 설정 확인)
2. 요청 간격 조절 (백오프 확대)
3. API 사용량 대시보드 확인
4. 필요 시 분석 주기 조절

## 4. 사후 분석 (Post-mortem) 템플릿

```
제목: [SEV-N] 장애 요약
일시: YYYY-MM-DD HH:MM ~ HH:MM (KST)
영향 범위: (매매 중단 / 데이터 지연 / ...)
타임라인:
  - HH:MM 최초 감지
  - HH:MM 매매 중단
  - HH:MM 원인 파악
  - HH:MM 복구 완료
  - HH:MM 매매 재개
근본 원인: (Root Cause)
재발 방지: (Action Items + 담당자 + 기한)
```
