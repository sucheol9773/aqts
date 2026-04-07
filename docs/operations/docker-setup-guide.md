# AQTS Docker 환경 세팅 가이드

> **문서 번호**: OPS-007
>
> **버전**: 1.0 | **최종 수정**: 2026-04-05
>
> **목적**: 개발/스테이징/프로덕션 환경에서 Docker Compose 기반 AQTS 시스템을 구성하고 실행하는 전체 절차를 안내합니다.

---

## 1. 사전 요구사항

### 1.1 호스트 시스템

| 항목 | 최소 사양 | 권장 사양 |
|------|-----------|-----------|
| OS | Ubuntu 22.04 LTS / macOS 13+ | Ubuntu 24.04 LTS |
| CPU | 2코어 | 4코어+ |
| RAM | 4GB | 8GB+ |
| 디스크 | 20GB SSD | 50GB+ SSD |
| Docker | 24.0+ | 최신 안정 버전 |
| Docker Compose | v2.20+ (plugin) | 최신 안정 버전 |

### 1.2 Docker 설치 확인

```bash
docker --version          # Docker version 24.0+ 확인
docker compose version    # Docker Compose v2.20+ 확인
```

설치가 안 되어 있다면:

```bash
# Ubuntu
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
# 로그아웃 후 재접속

# macOS
# Docker Desktop 다운로드: https://www.docker.com/products/docker-desktop
```

---

## 2. 프로젝트 구조

```
aqts/
├── docker-compose.yml           # 서비스 오케스트레이션 (PostgreSQL, MongoDB, Redis, Backend)
├── docker-compose.override.yml  # 개발 환경 오버라이드 (자동 병합)
├── .env                         # 환경변수 (직접 생성 필요)
├── backend/
│   ├── Dockerfile               # 멀티 스테이지 빌드 (Python 3.11.9)
│   ├── requirements.txt         # Python 의존성
│   └── main.py                  # FastAPI 엔트리포인트
└── scripts/
    └── init_db.sql              # PostgreSQL 스키마 초기화 (자동 실행)
```

### 2.1 서비스 구성

| 서비스 | 이미지 | 포트 | 역할 |
|--------|--------|------|------|
| postgres | timescale/timescaledb:2.14.2-pg16 | 5432 | 시계열 시세 데이터 + 관계형 데이터 |
| mongodb | mongo:7.0.9 | 27017 | 뉴스/감성 분석/비정형 데이터 |
| redis | redis:7.2.5-alpine | 6379 | 캐시 + 세션 + 실시간 데이터 |
| backend | Python 3.11.9 (자체 빌드) | 8000 | FastAPI 애플리케이션 |

---

## 3. 환경변수 설정

### 3.1 .env 파일 생성

프로젝트 루트에 `.env` 파일을 생성합니다. 이 파일은 `.gitignore`에 포함되어 있으므로 Git에 커밋되지 않습니다.

```bash
cp .env.example .env   # 템플릿이 있는 경우
# 또는 아래 내용으로 직접 생성
```

### 3.2 환경변수 전체 목록

```env
# ══════════════════════════════════════
# 실행 환경
# ══════════════════════════════════════
ENVIRONMENT=development          # development | staging | production
LOG_LEVEL=INFO                   # DEBUG | INFO | WARNING | ERROR

# ══════════════════════════════════════
# PostgreSQL (TimescaleDB)
# ══════════════════════════════════════
DB_HOST=postgres                 # Docker 네트워크 내부 호스트명
DB_PORT=5432
DB_NAME=aqts
DB_USER=aqts_user
DB_PASSWORD=<강력한_비밀번호>     # 필수 — 미설정 시 컨테이너 시작 실패
DB_POOL_SIZE=20
DB_MAX_OVERFLOW=10

# ══════════════════════════════════════
# MongoDB
# ══════════════════════════════════════
MONGO_HOST=mongodb               # Docker 네트워크 내부 호스트명
MONGO_PORT=27017
MONGO_DB=aqts
MONGO_USER=aqts_user
MONGO_PASSWORD=<강력한_비밀번호>  # 필수

# ══════════════════════════════════════
# Redis
# ══════════════════════════════════════
REDIS_HOST=redis                 # Docker 네트워크 내부 호스트명
REDIS_PORT=6379
REDIS_PASSWORD=<강력한_비밀번호>  # 필수
REDIS_DB=0

# ══════════════════════════════════════
# KIS 한국투자증권 OpenAPI
# ══════════════════════════════════════
KIS_TRADING_MODE=DEMO            # BACKTEST | DEMO | LIVE
# DEMO 모드 (모의투자)
KIS_DEMO_APP_KEY=<모의_앱키>
KIS_DEMO_APP_SECRET=<모의_앱시크릿>
KIS_DEMO_ACCOUNT_NO=<모의_계좌번호>
# LIVE 모드 (실전투자) — production에서만 사용
KIS_LIVE_APP_KEY=
KIS_LIVE_APP_SECRET=
KIS_LIVE_ACCOUNT_NO=

# ══════════════════════════════════════
# Anthropic Claude API
# ══════════════════════════════════════
ANTHROPIC_API_KEY=<API_키>       # 필수

# ══════════════════════════════════════
# 텔레그램 알림
# ══════════════════════════════════════
TELEGRAM_BOT_TOKEN=<봇_토큰>
TELEGRAM_CHAT_ID=<채팅_ID>
TELEGRAM_ALERT_LEVEL=IMPORTANT   # ALL | IMPORTANT | ERROR

# ══════════════════════════════════════
# 대시보드 인증 (초기 admin 사용자의 비밀번호 (Alembic 마이그레이션 시 시드용, 첫 로그인 후 변경 권장))
# ══════════════════════════════════════
DASHBOARD_SECRET_KEY=<JWT_시크릿>
ADMIN_BOOTSTRAP_USERNAME=admin
ADMIN_BOOTSTRAP_PASSWORD=<강한_초기_비밀번호>


# ══════════════════════════════════════
# 외부 데이터 API (선택)
# ══════════════════════════════════════
DART_API_KEY=                    # DART 전자공시 (선택)
FRED_API_KEY=                    # FRED 경제지표 (선택)
ECOS_API_KEY=                    # 한국은행 ECOS (선택)

# ══════════════════════════════════════
# 리스크 관리 (기본값 있음, 필요 시 오버라이드)
# ══════════════════════════════════════
# INITIAL_CAPITAL_KRW=50000000
# DAILY_LOSS_LIMIT_KRW=5000000
# MAX_ORDER_AMOUNT_KRW=10000000
# MAX_POSITIONS=20
# MAX_DRAWDOWN=0.20
# STOP_LOSS_PERCENT=-0.10
```

### 3.3 비밀번호 생성 팁

```bash
# 안전한 랜덤 비밀번호 생성
openssl rand -base64 24
```

**주의**: `DB_PASSWORD`, `MONGO_PASSWORD`, `REDIS_PASSWORD`는 필수입니다. 미설정 시 `docker compose up`이 실패합니다.

---

## 4. 실행 방법

### 4.1 개발 환경 (Development)

개발 모드에서는 `docker-compose.override.yml`이 자동 병합되어 소스 코드 마운트 + 자동 리로드가 활성화됩니다.

```bash
# 1. 전체 서비스 시작 (백그라운드)
docker compose up -d

# 2. 로그 확인
docker compose logs -f backend

# 3. 헬스체크
curl http://localhost:8000/api/system/health
```

**개발 모드 특성**:
- 소스 코드 변경 시 자동 리로드 (`--reload`)
- `./backend` 디렉토리가 컨테이너에 마운트
- root 사용자로 실행 (볼륨 권한 문제 방지)

### 4.2 스테이징/프로덕션 환경

프로덕션에서는 override 파일을 제외하고 실행합니다.

```bash
# override 파일 제외 → 프로덕션 설정만 적용
docker compose -f docker-compose.yml up -d
```

**프로덕션 모드 특성**:
- 멀티 스테이지 빌드 이미지 사용 (최소 크기)
- Non-root 사용자 (`appuser`, UID 1000)
- `--reload` 없음, `uvloop` + `httptools` 사용
- 헬스체크: `/api/system/health` 30초 간격

### 4.3 개별 서비스 실행

DB만 먼저 올리고 백엔드는 로컬에서 실행하는 경우:

```bash
# DB 서비스만 시작
docker compose up -d postgres mongodb redis

# 로컬 백엔드 실행 (호스트 네트워크에서 접속)
# .env의 DB_HOST, MONGO_HOST, REDIS_HOST를 localhost로 변경
cd backend
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

---

## 5. 초기 데이터 확인

### 5.1 PostgreSQL 스키마 확인

`scripts/init_db.sql`은 컨테이너 최초 시작 시 자동 실행됩니다.

```bash
# PostgreSQL 접속
docker exec -it aqts-postgres psql -U aqts_user -d aqts

# 테이블 목록 확인
\dt

# TimescaleDB hypertable 확인
SELECT hypertable_name FROM timescaledb_information.hypertables;

# 초기 전략 가중치 확인
SELECT * FROM strategy_weights;

# 종료
\q
```

**자동 생성되는 테이블 (17개)**:
- 시세: `market_ohlcv` (hypertable), `exchange_rates` (hypertable)
- 기본: `economic_indicators`, `financial_statements`, `business_calendars`
- 사용자: `user_profiles`, `universe`
- 포트폴리오: `portfolio_holdings`, `portfolio_snapshots` (hypertable)
- 거래: `orders`, `alerts`, `audit_logs` (hypertable)
- AI: `sentiment_scores` (hypertable), `investment_opinions` (hypertable), `ensemble_signals` (hypertable)
- 전략: `strategy_weights`, `weight_update_history`, `backtest_results`

### 5.2 MongoDB 확인

```bash
# MongoDB 접속
docker exec -it aqts-mongodb mongosh -u aqts_user -p <비밀번호> --authenticationDatabase admin aqts

# 컬렉션 목록 (최초에는 비어 있음)
show collections

# 종료
exit
```

MongoDB는 뉴스 원문, 크롤링 데이터, 비정형 감성 분석 결과 등을 저장합니다. 컬렉션은 앱이 최초 데이터를 저장할 때 자동 생성됩니다.

### 5.3 Redis 확인

```bash
# Redis 접속
docker exec -it aqts-redis redis-cli -a <비밀번호>

# 연결 확인
PING
# 응답: PONG

# 종료
exit
```

---

## 6. 네트워크 구조

```
┌─────────────────────────── aqts-network (bridge) ──────────────────────────┐
│                                                                            │
│  ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────────────────┐  │
│  │ postgres │    │ mongodb  │    │  redis   │    │      backend         │  │
│  │  :5432   │    │  :27017  │    │  :6379   │    │       :8000          │  │
│  └──────────┘    └──────────┘    └──────────┘    └──────────────────────┘  │
│                                                    │ depends_on: healthy   │
│                                                    │ ├─ postgres           │
│                                                    │ ├─ mongodb            │
│                                                    │ └─ redis              │
└────────────────────────────────────────────────────────────────────────────┘
         │                  │               │                  │
    host:5432          host:27017      host:6379          host:8000
```

- 컨테이너 간 통신: 서비스명으로 접근 (`postgres`, `mongodb`, `redis`)
- 호스트에서 접근: `localhost:<포트>`
- backend는 모든 DB가 healthy 상태일 때만 시작

---

## 7. 데이터 영속성

### 7.1 Named Volumes

| Volume | 마운트 경로 | 용도 |
|--------|------------|------|
| `postgres_data` | `/var/lib/postgresql/data` | 시세/거래/감사 데이터 |
| `mongodb_data` | `/data/db` | 뉴스/비정형 데이터 |
| `redis_data` | `/data` | 캐시 RDB 스냅샷 |

### 7.2 볼륨 관리

```bash
# 볼륨 목록 확인
docker volume ls | grep aqts

# 볼륨 상세 정보 (크기, 경로)
docker volume inspect aqts_postgres_data

# 주의: 아래 명령은 모든 데이터를 삭제합니다
# docker compose down -v    # 컨테이너 + 볼륨 전체 삭제
```

### 7.3 백업

```bash
# PostgreSQL 백업
docker exec aqts-postgres pg_dump -U aqts_user aqts > backup_$(date +%Y%m%d).sql

# MongoDB 백업
docker exec aqts-mongodb mongodump -u aqts_user -p <비밀번호> \
  --authenticationDatabase admin --db aqts --out /tmp/mongodump
docker cp aqts-mongodb:/tmp/mongodump ./mongodump_$(date +%Y%m%d)

# Redis 백업 (RDB 스냅샷)
docker exec aqts-redis redis-cli -a <비밀번호> BGSAVE
docker cp aqts-redis:/data/dump.rdb ./redis_backup_$(date +%Y%m%d).rdb
```

---

## 8. 모드별 운영 흐름

### 8.1 BACKTEST 모드 (API 호출 없음)

```env
ENVIRONMENT=development
KIS_TRADING_MODE=BACKTEST
```

과거 데이터로 전략 검증. KIS API를 호출하지 않으므로 KIS 키가 불필요합니다.

### 8.2 DEMO 모드 (모의투자)

```env
ENVIRONMENT=development
KIS_TRADING_MODE=DEMO
KIS_DEMO_APP_KEY=<모의투자_앱키>
KIS_DEMO_APP_SECRET=<모의투자_앱시크릿>
KIS_DEMO_ACCOUNT_NO=<모의투자_계좌>
```

KIS 모의투자 서버에 연결. 실제 자금이 이동하지 않습니다. 배포 전 반드시 이 단계에서 충분히 검증해야 합니다.

### 8.3 LIVE 모드 (실전투자)

```env
ENVIRONMENT=production
KIS_TRADING_MODE=LIVE
KIS_LIVE_APP_KEY=<실전_앱키>
KIS_LIVE_APP_SECRET=<실전_앱시크릿>
KIS_LIVE_ACCOUNT_NO=<실전_계좌>
```

**주의**: `is_live_trading` 프로퍼티는 `ENVIRONMENT=production` AND `KIS_TRADING_MODE=LIVE` 두 조건이 모두 충족될 때만 `True`를 반환합니다. 개발 환경에서 실수로 LIVE 모드가 활성화되는 것을 방지하는 이중 안전장치입니다.

---

## 9. 문제 해결

### 9.1 서비스 상태 확인

```bash
# 전체 서비스 상태
docker compose ps

# 특정 서비스 로그
docker compose logs -f postgres
docker compose logs -f backend --tail=100

# 헬스체크 상태
docker inspect --format='{{.State.Health.Status}}' aqts-postgres
docker inspect --format='{{.State.Health.Status}}' aqts-backend
```

### 9.2 자주 발생하는 문제

**"DB_PASSWORD is required" 에러**

```
ERROR: Missing required env var: DB_PASSWORD
```

원인: `.env` 파일이 없거나 필수 환경변수 미설정. 3.1절 참고하여 `.env` 파일을 생성하세요.

**PostgreSQL 접속 실패**

```bash
# 로그 확인
docker compose logs postgres

# 볼륨 초기화 후 재시작 (데이터 손실 주의)
docker compose down
docker volume rm aqts_postgres_data
docker compose up -d
```

**init_db.sql이 실행되지 않음**

`init_db.sql`은 PostgreSQL 볼륨이 비어 있을 때(최초 시작)만 실행됩니다. 스키마를 재적용하려면:

```bash
# 방법 1: 볼륨 삭제 후 재시작
docker compose down
docker volume rm aqts_postgres_data
docker compose up -d

# 방법 2: 수동 실행
docker exec -i aqts-postgres psql -U aqts_user -d aqts < scripts/init_db.sql
```

**torch 설치 (CPU 전용)**

Dockerfile에서 torch를 설치할 때 CPU 전용 인덱스를 사용해야 이미지 크기를 줄일 수 있습니다:

```dockerfile
# Dockerfile의 builder 스테이지에 추가
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu
```

또는 requirements.txt에서 torch 라인을 분리하여 별도로 설치:

```bash
pip install torch>=2.6.0 --index-url https://download.pytorch.org/whl/cpu
pip install -r requirements.txt
```

**백엔드가 DB 연결 실패로 시작하지 않음**

```bash
# DB 헬스체크 대기 후 백엔드 재시작
docker compose restart backend
```

`depends_on: condition: service_healthy` 설정이 되어 있으므로 일반적으로 발생하지 않지만, DB 초기화에 시간이 오래 걸리면 발생할 수 있습니다.

### 9.3 리소스 정리

```bash
# 서비스 중지 (데이터 유지)
docker compose down

# 서비스 + 볼륨 삭제 (데이터 삭제)
docker compose down -v

# 미사용 이미지 정리
docker image prune -f
```

---

## 10. 배포 전 체크리스트

배포 전 아래 항목을 확인하세요. release-gates.md의 Gate E 승인이 완료된 이후 진행합니다.

- [ ] `.env` 파일 생성, 모든 필수 환경변수 설정 완료
- [ ] `docker compose up -d` 정상 시작 확인
- [ ] `docker compose ps` 전체 서비스 healthy 확인
- [ ] `curl http://localhost:8000/api/system/health` 200 OK 확인
- [ ] PostgreSQL 스키마 17개 테이블 생성 확인 (`\dt`)
- [ ] TimescaleDB hypertable 6개 확인
- [ ] `KIS_TRADING_MODE=DEMO` 상태에서 모의투자 E2E 검증
- [ ] 텔레그램 알림 발송 테스트 완료
- [ ] 백업 스크립트 실행 확인
- [ ] 로그 확인 (`docker compose logs backend`)
- [ ] torch 2.6.0+ 설치 확인 (`docker exec aqts-backend pip show torch`)
- [ ] 운영책임자 Gate E 최종 서명 완료
- [ ] LIVE 전환: `ENVIRONMENT=production`, `KIS_TRADING_MODE=LIVE` 설정

---

## 11. 참고 문서

| 문서 | 경로 | 내용 |
|------|------|------|
| 릴리즈 게이트 | docs/operations/release-gates.md | Gate A~E 통과 기준 |
| 롤백 계획 | docs/operations/rollback-plan.md | 장애 시 롤백 절차 |
| 인시던트 런북 | docs/operations/incident-runbook.md | 장애 진단 및 복구 |
| 거래 중지 정책 | docs/operations/trading-halt-policy.md | 자동/수동 중지 조건 |
| 고객 공지 | docs/operations/customer-notice.md | 투자 위험 고지 |
| 기능 현황 | docs/FEATURE_STATUS.md | 전체 기능 구현 상태 |
