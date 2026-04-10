# AQTS 배포 및 검증 로드맵

> **문서 번호**: OPS-008
>
> **버전**: 1.3 | **최종 수정**: 2026-04-07
>
> **목적**: 현재 개발 완료 상태에서 실전 운영까지의 단계별 절차, 검증 기준, 의사결정 포인트를 정의합니다.

---

## 현재 상태 요약

| 항목 | 상태 |
|------|------|
| 코드 구현 | 151개 기능 전체 구현 + 테스트 완료 (100%) |
| 테스트 | 3,942건 전체 통과 (커버리지 89%) |
| 릴리즈 게이트 | Gate A~E 전체 PASS (ASC 서명 완료, 2026-04-05) |
| 부하 테스트 | 28건 스트레스 테스트 통과 (동시성/메모리/스케일링) |
| 미해결 CVE | ✅ torch 2.11.0+cpu 설치 완료 (CVE 해소) |
| 배포 인프라 | Docker Compose 구성 완료, GCP 서버 가동 중 (OPS-007 참조) |
| SSL/TLS | 자체 서명 인증서 + nginx 리버스 프록시 (443 → 8000) |
| 카나리 배포 | nginx 리버스 프록시 + docker-compose.canary.yml 구성 완료 |
| 배포 스크립트 | pre_deploy_check.sh (7단계 검증) + canary_deploy.sh (5개 명령) |

---

## 단계 개요

```
[현재] ──→ Phase 0 ──→ Phase 1 ──→ Phase 2 ──→ Phase 3 ──→ Phase 4
             배포 준비     DEMO 검증     LIVE 베타     안정화       확장
             (1~2일)      (2~4주)       (1~2개월)    (1~2개월)    (이후)
```

---

## Phase 0: 배포 준비 (목표: 1~2일)

### 0-1. 운영책임자 Gate E 최종 승인

- [x] release-gates.md Gate E 서명란에 운영책임자 서명 (ASC, 2026-04-05)
- [x] 고객 공지(OPS-006) 검토 완료 확인
- [x] 롤백 계획(OPS-005) 검토 완료 확인

### 0-2. 클라우드 인프라 프로비저닝

- [x] 클라우드 서버 선택: GCP Compute Engine (aqts-server, 34.64.216.144)
- [x] 서버 사양: 최소 4코어 / 8GB RAM / 50GB SSD
- [x] Docker 및 Docker Compose 설치
- [x] 방화벽 설정: 443(HTTPS), 22(SSH)만 개방, 8000(API 직접)/DB 포트(5432/27017/6379) 외부 차단 (GCP VPC 방화벽)
- [x] SSL/TLS 인증서 설정: 자체 서명 인증서 + nginx 리버스 프록시 (Phase 2 LIVE 전환 시 도메인 + Let's Encrypt로 교체 예정)

### 0-3. 환경 구성

- [x] `.env` 파일 생성 (OPS-007 3절 참조)
- [x] KIS 모의투자 + 실전 API 키 발급 및 설정
- [x] Anthropic API 키 설정
- [x] 텔레그램 봇 생성 및 알림 채널 설정
- [ ] DART/FRED/ECOS API 키 설정 (선택)

### 0-4. 최초 배포

```bash
# 1. 소스 코드 업로드
git clone <repository_url>
cd aqts

# 2. torch CPU 설치 (Dockerfile 수정 또는 별도 실행)
# Dockerfile builder 스테이지에서:
# pip install torch>=2.6.0 --index-url https://download.pytorch.org/whl/cpu

# 3. 서비스 시작
docker compose -f docker-compose.yml up -d

# 4. 전체 서비스 healthy 확인
docker compose ps

# 5. 헬스체크
curl https://<도메인>/api/system/health

# 6. torch CVE 해소 확인
docker exec aqts-backend pip show torch | grep Version
# Version: 2.6.0 이상 확인

# 7. DB 마이그레이션 베이스라인 마킹 (init_db.sql로 생성된 기존 DB)
docker exec aqts-backend alembic stamp head

# 이후 스키마 변경 시: docker exec aqts-backend alembic upgrade head
# 롤백 시: docker exec aqts-backend alembic downgrade -1
```

### 0-4 완료 기준

- 4개 서비스 전체 healthy
- `/api/system/health` 200 OK
- torch >= 2.6.0 확인 → Gate A/B 완전 PASS
- 텔레그램 테스트 알림 수신 확인

### 0-5. 배포 전 자동 검증

```bash
# 사전 검증 스크립트 실행 (7단계 자동 검증)
bash scripts/pre_deploy_check.sh [--skip-docker] [--skip-tests]
```

검증 항목: Git 상태 → 린트/포맷 → 테스트+커버리지 → 문서 동기화 → Docker 빌드 → 환경 변수 → Release Gates

### 0-6. 카나리 배포 (선택)

업데이트 배포 시 카나리 전략을 사용하여 점진적으로 트래픽을 전환합니다.

```bash
# 카나리 배포 시작 (10% 트래픽)
bash scripts/canary_deploy.sh start

# 모니터링 확인 후 비중 증가 (10→30→50→100%)
bash scripts/canary_deploy.sh promote

# 문제 발생 시 즉시 롤백
bash scripts/canary_deploy.sh rollback

# 100% 프로모션 완료 후 일반 모드 복귀
bash scripts/canary_deploy.sh finish
```

**카나리 인프라 구성**:
- `nginx/nginx-canary.conf`: split_clients 기반 트래픽 분할
- `docker-compose.canary.yml`: stable/canary 듀얼 백엔드 + nginx 프록시
- 롤백 트리거: error_rate > 5%, latency_p95 > 3000ms, health_check_failures >= 3

---

## Phase 1: DEMO 모드 검증 (목표: 2~4주)

### 1-1. 데이터 수집 검증 (1~3일)

KIS 모의투자 API를 통해 실시간 데이터 수집이 정상 동작하는지 확인합니다.

- [ ] KRX 시세 수집 정상 동작 (market_ohlcv 테이블 INSERT 확인)
- [ ] 뉴스 크롤링 정상 동작 (MongoDB 컬렉션 데이터 확인)
- [ ] 경제지표 수집 정상 동작 (FRED/ECOS)
- [ ] DART 재무제표 수집 정상 동작
- [ ] 환율 데이터 수집 정상 동작
- [ ] Circuit Breaker 정상 동작 (외부 API 장애 시 자동 차단 확인)

**검증 방법**:
```bash
# PostgreSQL 시세 데이터 확인
docker exec -it aqts-postgres psql -U aqts_user -d aqts \
  -c "SELECT ticker, COUNT(*) FROM market_ohlcv GROUP BY ticker ORDER BY COUNT(*) DESC LIMIT 10;"

# MongoDB 뉴스 데이터 확인
docker exec -it aqts-mongodb mongosh -u aqts_user -p <비밀번호> \
  --authenticationDatabase admin aqts --eval "db.news_raw.countDocuments()"
```

### 1-2. 파이프라인 E2E 검증 (3~5일)

전체 투자 결정 파이프라인이 실제 데이터로 정상 실행되는지 확인합니다.

- [ ] 감성 분석 (Claude API) 정상 응답
- [ ] 팩터 분석 → 시그널 생성 → 앙상블 결합 파이프라인 정상 실행
- [ ] 9개 Gate 순차 평가 정상 통과
- [ ] 투자 의견 생성 및 audit_logs 기록 확인
- [ ] 모의 주문 생성 및 체결 확인 (orders 테이블)
- [ ] 일일 리포트 생성 및 텔레그램 발송 확인

### 1-3. 성과 측정 및 파라미터 튜닝 (1~3주)

최소 5영업일 이상 모의투자를 실행하고 벤치마크 대비 성과를 측정합니다.

**핵심 지표**:

| 지표 | 기준 | 비고 |
|------|------|------|
| 일일 수익률 | KOSPI 대비 ±2% 이내 추적 | 첫 주는 편차 허용 |
| 최대 낙폭 (MDD) | -20% 이내 | RiskManagementSettings.max_drawdown |
| 승률 | 45% 이상 | 매매 건 기준 |
| Sharpe Ratio | 0.5 이상 | 연환산 기준 |
| 일평균 거래 수 | 1~10건 | 과매매/무거래 감시 |

**성과 부진 시 조정 순서**:

1단계: `config/operational_thresholds.yaml` 임계값 조정 (코드 변경 없음)
2단계: 앙상블 가중치 조정 (`strategy_weights` 테이블 UPDATE)
3단계: ParamSensitivityEngine으로 핵심 파라미터 특정 후 최적화 (`/api/param-sensitivity/run`)
4단계: OOS Walk-Forward 재검증 (`/api/oos/run`)

**조정 후 검증 사이클**: 조정 → DEMO 3일 재실행 → 성과 비교 → 반복

### 1-4. 리스크 시나리오 테스트

- [ ] 일일 손실 한도 도달 시 자동 거래 중지 확인
- [ ] 연속 손실(5회) 시 자동 중지 확인
- [ ] MDD -20% 도달 시 자동 중지 확인
- [ ] 그레이스풀 셧다운 정상 동작 (주문 드레이닝 확인)
- [ ] 롤백 절차 1회 실습 (OPS-005 절차대로)

### Phase 1 → Phase 2 전환 기준

| 조건 | 충족 여부 |
|------|----------|
| 데이터 수집 5일 이상 연속 무장애 | [ ] |
| 파이프라인 E2E 5일 이상 정상 실행 | [ ] |
| MDD -20% 미초과 | [ ] |
| 일일 리포트 정상 발송 | [ ] |
| 리스크 시나리오 테스트 전체 통과 | [ ] |
| 운영책임자 Phase 2 전환 승인 | [ ] |

---

## Phase 2: LIVE 베타 (목표: 1~2개월)

### 2-1. LIVE 전환

```env
# .env 변경
ENVIRONMENT=production
KIS_TRADING_MODE=LIVE
KIS_LIVE_APP_KEY=<실전_앱키>
KIS_LIVE_APP_SECRET=<실전_앱시크릿>
KIS_LIVE_ACCOUNT_NO=<실전_계좌>
```

```bash
docker compose -f docker-compose.yml down
docker compose -f docker-compose.yml up -d
```

**주의**: `is_live_trading = True`는 `ENVIRONMENT=production` + `KIS_TRADING_MODE=LIVE` 두 조건이 모두 필요합니다.

### 2-2. 소규모 실전 운영

- [ ] 초기 투자금: `INITIAL_CAPITAL_KRW` 설정값의 20~50%로 시작
- [ ] 첫 1주: 최대 포지션 5개로 제한 (`MAX_POSITIONS` 조정)
- [ ] 2주차: 정상 확인 후 포지션 수 점진적 확대
- [ ] 4주차: 전체 투자금 투입 여부 판단

### 2-3. 일일 모니터링 체크리스트

매일 확인해야 할 항목:

- [ ] `/api/system/health` 정상
- [ ] 텔레그램 일일 리포트 수신
- [ ] 주문 체결률 확인 (미체결 주문 없는지)
- [ ] 포트폴리오 수익률 vs 벤치마크 비교
- [ ] audit_logs에 이상 기록 없는지
- [ ] 에러 로그 확인 (`docker compose logs backend --tail=200 | grep ERROR`)

### 2-4. 비상 대응

| 상황 | 대응 |
|------|------|
| MDD -15% 도달 | 알림 확인, 포지션 축소 검토 |
| MDD -20% 도달 | 자동 거래 중지, 수동 확인 후 재개 여부 결정 |
| API 장애 | Circuit Breaker 자동 차단, 인시던트 런북(OPS-002) 참조 |
| 시스템 오류 | 롤백 계획(OPS-005) 실행 |
| 거래 일시 중지 필요 | 거래 중지 정책(OPS-003) 참조 |

### Phase 2 → Phase 3 전환 기준

| 조건 | 충족 여부 |
|------|----------|
| 실전 거래 30일 이상 운영 | [ ] |
| MDD -20% 미초과 | [ ] |
| 월간 Sharpe Ratio 0.5 이상 | [ ] |
| 시스템 무장애 가동률 99% 이상 | [ ] |
| 롤백 없이 안정 운영 | [ ] |

---

## Phase 3: 안정화 (목표: 1~2개월)

### 3-1. 성과 분석 및 최적화

- [ ] 월간 성과 리포트 분석 (수익률, MDD, Sharpe, 승률)
- [ ] 전략별 기여도 분석 (어떤 전략이 수익/손실에 기여했는지)
- [ ] 앙상블 가중치 최종 조정
- [ ] 레짐별 성과 차이 분석 (상승장/하락장/횡보장)

### 3-2. 인프라 강화

- [ ] 자동 백업 스케줄 설정 (일 1회 DB 백업)
- [ ] 모니터링 대시보드 외부 접근 설정 (필요 시)
- [ ] 로그 로테이션 설정
- [ ] 서버 리소스 모니터링 (CPU/RAM/디스크)

### 3-3. 운영 절차 정착

- [ ] 일일/주간/월간 체크리스트 루틴화
- [ ] 인시던트 대응 1회 이상 실습 완료
- [ ] 백업 복원 테스트 1회 이상 완료

---

## Phase 4: 확장 (이후)

Phase 3 안정화 완료 후, 실전 데이터가 충분히 쌓인 상태에서 검토합니다.

### 4-1. RL/학습형 전략 확장

실전 데이터 기반으로 강화학습 전략을 기존 앙상블에 추가합니다.

전제 조건:
- 최소 3개월 이상의 실전 거래 데이터 축적
- 기존 앙상블의 약점 영역 구체적 식별 (진입 타이밍, 포지션 사이징, 리밸런싱 등)
- 백테스트/OOS 환경에서 RL Agent가 기존 전략 대비 개선 확인

검토 대상:
- PPO/SAC 기반 포지션 사이징 Agent
- DQN 기반 리밸런싱 타이밍 최적화
- Multi-Agent 구조 (전략별 Agent → 메타 Agent 조율)

### 4-2. 서비스 확장 (PRD Phase 2~3)

| 단계 | 내용 | 선행 조건 |
|------|------|----------|
| Phase 2: 자문형 | 다중 사용자, 투자 추천 서비스 | 투자자문업 등록 |
| Phase 3: 일임형 | 자동 집행, 고액자산가/법인 대상 | 투자일임업 등록 |

### 4-3. 프론트엔드 개발 (PRD M5)

- 웹 기반 대시보드 (React/Next.js)
- 사용자 포트폴리오 시각화
- 실시간 시세/알림 WebSocket
- 사용자 프로필 관리 UI

### 4-4. 인프라 고도화

- Kubernetes 마이그레이션 (트래픽 증가 시)
- ~~Canary 배포 자동화~~ ✅ (nginx split_clients + canary_deploy.sh 구성 완료)
- 멀티 리전 배포 (해외 시장 확장 시)
- ~~CI/CD 파이프라인 자동화~~ ✅ (GitHub Actions ci.yml + cd.yml 구성 완료)

---

## 의사결정 포인트 요약

```
Phase 0 완료 → 운영책임자 승인 필요
                    ↓
Phase 1 완료 → 전환 기준 6개 항목 전체 충족 + 운영책임자 승인
                    ↓
Phase 2 완료 → 전환 기준 5개 항목 전체 충족
                    ↓
Phase 3 완료 → 확장 여부 판단 (실전 데이터 기반)
```

각 Phase 전환 시 운영책임자의 명시적 승인이 필요하며, 전환 기준을 충족하지 못한 경우 해당 Phase를 연장합니다. Phase 간 역전환(예: LIVE → DEMO)은 인시던트 발생 시 즉시 가능하며, 롤백 계획(OPS-005)을 따릅니다.

---

## 참고 문서

| Doc ID | 문서 | 참조 시점 |
|--------|------|----------|
| OPS-001 | release-gates.md | Phase 0 (Gate E 승인) |
| OPS-002 | incident-runbook.md | Phase 2~3 (장애 대응) |
| OPS-003 | trading-halt-policy.md | Phase 1~3 (거래 중지) |
| OPS-005 | rollback-plan.md | Phase 1~3 (롤백 실행) |
| OPS-006 | customer-notice.md | Phase 0 (투자 위험 고지) |
| OPS-007 | docker-setup-guide.md | Phase 0 (환경 구성) |
| PRD | PRD.md | Phase 4 (서비스 확장) |
