# Phase 1 DEMO 검증 리포트 (2026-04-11)

> **문서 번호**: OPS-009
>
> **목적**: Phase 1 DEMO 모드 검증 결과를 기록하고, 발견된 버그 및 설정 이슈를 추적합니다.

---

## 1. 검증 환경

| 항목 | 상태 |
|------|------|
| 서버 | GCP Compute Engine (aqts-server, 34.64.216.144) |
| 거래 모드 | DEMO (KIS 모의투자) |
| 서비스 | 11개 Docker 컨테이너 전체 healthy |
| SSL/TLS | 자체 서명 인증서 + nginx (443 → 8000) |
| 스케줄러 | 5개 핸들러 등록, 다음 거래일 2026-04-13(월) 대기 |

---

## 2. Phase 1-1 데이터 수집 검증 결과

### 2-1. OHLCV 시세 데이터 — ✅ 정상

- 114개 종목 수집 완료 (universe 115개 대비 99% 매칭)
- 한국 KRX: 69종목 (005930 삼성전자, 000660 SK하이닉스 등)
- 미국 US: 45종목 (AAPL, MSFT, NVDA, AMZN, GOOGL 등)
- 데이터 범위: 2000-01-03 ~ 2026-04-09 (최신 거래일까지)
- 종목당 평균 ~5,500행 (일봉 기준 26년치)

### 2-2. 뉴스/DART — ✅ 수동 수집 성공

- 수동 실행으로 MongoDB `news_articles` 컬렉션에 데이터 저장 완료
- RSS 수집: 657건 (10개 피드, 한경 stock 피드 1개 404 제외)
  - NAVER_FINANCE: 200건 (주식/증시 키워드)
  - HANKYUNG: 83건 (economy/finance)
  - MAEKYUNG: 79건 (시장/종합)
  - REUTERS: 295건 (markets/economy/Asia)
- DART 공시: 385건 (20260409~20260410)
- MongoDB 저장: **907건 신규**, 135건 중복 스킵, 총 1,042건 처리
- **스케줄러 wiring**: ✅ `handle_pre_market()` 스텝 2에 `NewsCollectorService.collect_and_store()` 연결 완료
  - OHLCV 수집 직후, 건전성 검사 이전에 실행
  - 뉴스 수집 실패 시 다른 단계 차단하지 않음 (독립 try/except)
  - 다음 거래일(04-13 월) 08:30 KST handle_pre_market 자동 실행 시 검증 예정

### 2-3. 경제지표 (FRED/ECOS) — ✅ FRED 정상 / ⚠️ ECOS 버그 수정

- `handle_pre_market()` 스텝 3에 `EconomicCollectorService.collect_and_store()` 연결
- `_store_to_db()` 주석 해제 → TimescaleDB 영속화 활성화
- FRED API 키 설정 완료 (미국 지표 9개: GDP, CPI, 금리, VIX 등)
- ECOS API 키 설정 완료 (한국 지표 4개 활성: 기준금리, CPI, 실업률, 경상수지 / GDP 비활성화)
- **04-13 수동 검증**: FRED 9건 수집+DB 저장 성공, ECOS 0건 실패
- **ECOS 1차 버그 수정 (04-13)**:
  1. 날짜 형식: 월간(`M`) 주기에 `%Y%m%d` 전송 → `%Y%m` 으로 수정 (ERROR-101)
  2. 응답 파싱: `data.get("stat_code")` → `data["StatisticSearch"]["row"]` 구조로 수정
  3. 검색 범위: 30일 → 월간 6개월, 분기 2년으로 확대
  - 결과: 기준금리(722Y001), CPI(901Y009) 2건만 수집 성공, 나머지 3건 실패
- **ECOS 2차 stat_code/item_code 수정 (04-13)**:
  - `discover_ecos_codes.py` 스크립트로 서버에서 ECOS API 직접 탐색
  - 실업률: `902Y014/0` → `901Y027/I61BC` (경제활동인구 테이블의 실업률% 항목)
    - 변경 전: 902Y014/KR은 경제활동인구 수(천명)를 반환 — 실업률(%)이 아님
    - 변경 후: 901Y027/I61BC → 202402=3.2%, 202403=3% 확인
  - 경상수지: `721Y017/0` → `301Y017/SA000` (경상수지 계절조정, 백만달러)
    - 변경 전: 721Y017 테이블에 StatisticItemList 항목 없음
    - 변경 후: 301Y017/SA000 → 202405=9378.6 백만달러 확인
  - GDP: **비활성화** — ECOS StatisticSearch API에서 GDP 테이블 발견 불가
    - 111Y002 = 금융기관유동성(Lf), GDP와 무관
    - 200Y001~200Y004, 111Y055~111Y056 모두 INFO-200(데이터 없음)
    - StatisticTableList 검색("GDP","국민소득","성장률") 모두 INFO-200
    - ECOS_SERIES_MAP에서 주석 처리, 테이블이 확인되면 재활성화 예정

### 2-4. 환율 — ⚠️ 캐시 히트 시 DB 미저장 버그 수정

- `ExchangeRateManager._store_rate_to_db()` 메서드 추가 (TimescaleDB UPSERT)
- `get_current_rate(persist=True)` 파라미터로 DB 저장 제어
- `scheduler_main.py`에 1시간 간격 백그라운드 수집 루프 추가 (`_exchange_rate_loop`)
- Redis 캐시 + TimescaleDB 이중 영속화 구조
- **04-13 버그 발견**: 캐시 히트 시 `persist=True` 여부와 무관하게 즉시 return → DB 미저장
- **수정**: 캐시 히트 경로에도 `persist=True`이면 `_store_rate_to_db()` 호출 추가
- 수정 전: exchange_rates 테이블 3건만 존재 (캐시 미스 시에만 저장)
- 수정 후: 매 수집 주기(1시간)마다 DB 영속화 보장

### 2-5. Circuit Breaker — ✅ 정상 대기

- Redis에 circuit/breaker 관련 키 없음
- 외부 API 장애 미발생 → 트리거 없음 (정상)

---

## 3. Phase 1-2 파이프라인 E2E 검증 결과

### 3-0. 파이프라인 E2E 수동 테스트 — ✅ 전 구간 성공

`POST /api/system/pipeline?tickers=005930` 최종 실행 결과 (2026-04-10 16:19 UTC):

```json
{
    "005930": {
        "status": "completed",
        "ensemble_signal": 0.0567,
        "action": "HOLD",
        "confidence": 0.09
    }
}
```

전체 파이프라인 흐름 확인:
- **DataGate: PASS** — 뉴스 데이터 존재 확인
- **Sentiment 분석: SUCCESS** — Anthropic API (Haiku 4.5) 호출 성공
- **Opinion 생성: SUCCESS** — Anthropic API (Sonnet 4) 호출 성공
- **Ensemble: 0.0567** — 약간 매수 방향, BUY 임계값 미달
- **SignalGate: PASS** — 유의미한 시그널 생성 (HOLD이지만 conviction > 0)

발견 및 해결한 이슈:
1. API 크레딧 부족 → $25 충전으로 해결
2. `EnsembleSignal.confidence` 속성명 불일치 → `final_confidence`로 수정 (§6 참조)

### 3-1. 앙상블 시그널 — ✅ 실행됨 (단, SQL 버그 발견)

- Redis `ensemble:latest:*` 키 115종목 캐시 확인
- Redis `ensemble:latest:_summary` 요약 데이터 존재
- **CRITICAL BUG**: 개별 종목 앙상블 조회 시 SQL 구문 오류 발생 (§4 참조)

### 3-2. 포트폴리오 — ✅ 실제 운용 중

- 삼성전자(005930) 1주 보유
  - 매입가: 196,000원
  - 현재가: 206,000원
  - 수익: +10,000원 (+5.1%)
- 현금: 9,803,980원
- 총 자산: 10,009,980원

### 3-3. 일일 리포트 — ✅ 생성 (4일치)

- Redis `report:daily:2026-04-07` ~ `report:daily:2026-04-10`
- 포트폴리오 스냅샷 3일치 (`portfolio:snapshot:2026-04-08~10`)
- **BUG**: 누적 수익률 -79.98% 오계산 (§5 참조)

### 3-4. 감사 로그 — ✅ 기록 중

- audit_logs 8건 (MARKET_CLOSE 이벤트, 04-07 ~ 04-10)
- 포트폴리오 가치 추이: 10,000,280 → 10,014,980 → 10,007,980 → 10,009,980

### 3-5. 텔레그램 — ⏸️ 미확인

- `NotificationRouter wired: telegram → file → console cascade` 로그 확인
- 실제 발송 로그는 미확인 (추가 검증 필요)

---

## 4. CRITICAL BUG: 앙상블 SQL IN 구문 오류

### 증상

Redis `ensemble:latest:005930` 조회 시 SQL syntax error:

```
syntax error at or near "$2"
SQL: SELECT ... FROM market_ohlcv WHERE ticker = $1 AND market IN $2 ...
parameters: ('005930', ('KRX',), 300)
```

### 원인

`core/strategy_ensemble/runner.py:196` — `market IN :markets` 구문.
asyncpg는 SQLAlchemy의 `IN` + tuple 바인딩을 지원하지 않음.
`IN $2`로 컴파일되면서 PostgreSQL 구문 오류 발생.

### 수정

```python
# Before (line 196)
AND market IN :markets
# params: {"markets": tuple(market_filter)}

# After
AND market = ANY(:markets)
# params: {"markets": list(market_filter)}
```

### 영향 범위

- `_fetch_ohlcv()`를 사용하는 DB 경로 전체 (`DynamicEnsembleRunner.run()`)
- `run_with_ohlcv()` (in-memory 경로)는 영향 없음
- 앙상블 요약(`ensemble:latest:_summary`)은 생성되지만, 개별 종목 시그널은 에러 저장

### 회귀 테스트

`tests/test_ensemble_runner.py`에 2개 테스트 추가:
- `test_fetch_ohlcv_sql_uses_any_not_in`: SQL 문법 + list 파라미터 타입 검증
- `test_fetch_ohlcv_us_market_filter`: US 종목 market 필터 검증

---

## 5. BUG: 누적 수익률 오계산 (-79.98%)

### 증상

일일 리포트에서 `cumulative_return_pct: -79.98%`, `cumulative_pnl: -39,990,020원`

### 원인

`config/settings.py`의 `initial_capital_krw` 기본값이 **50,000,000원**이나,
실제 투입 자본은 **~10,000,000원**.

```
cumulative_return = (10,009,980 - 50,000,000) / 50,000,000 = -79.98%
```

### 수정 방법 (설정 변경)

서버 `.env`에 실제 투입 자본에 맞게 설정:

```env
INITIAL_CAPITAL_KRW=10000000
```

설정 변경 후 서비스 재시작 필요: `docker compose restart backend scheduler`

---

## 6. BUG: EnsembleSignal 속성명 불일치 (confidence → final_confidence)

### 증상

`POST /api/system/pipeline?tickers=005930` 호출 시:

```
'EnsembleSignal' object has no attribute 'confidence'
```

### 원인

`api/routes/system.py:263` — `ensemble.confidence`로 접근하지만, `EnsembleSignal` dataclass(`core/strategy_ensemble/engine.py:63`)의 실제 속성명은 `final_confidence`.

### 수정

```python
# Before (system.py:263)
"confidence": float(ensemble.confidence) if ensemble else None,

# After
"confidence": float(ensemble.final_confidence) if ensemble else None,
```

테스트 mock도 동일하게 수정 (`tests/test_system_routes.py:282, 346`).

---

## 7. 완료된 조치

| 항목 | 상태 | 비고 |
|------|------|------|
| SQL 버그 수정 배포 | ✅ 완료 | commit f755ad1, `= ANY(:markets)` |
| INITIAL_CAPITAL_KRW 설정 | ✅ 완료 | 서버 `.env` → 10,000,000원 |
| 수동 뉴스 수집 테스트 | ✅ 완료 | 907건 MongoDB 저장 성공 |
| 관리자 계정 생성 | ✅ 완료 | admin 계정, operator 권한 |
| 파이프라인 API 호출 | ✅ 완료 | E2E 전 구간 성공 (005930: signal=0.0567, HOLD) |
| EnsembleSignal 속성 버그 수정 | ✅ 완료 | confidence → final_confidence |
| Anthropic API 크레딧 충전 | ✅ 완료 | $25 충전 |
| p95 레이턴시 heavy endpoint 분리 | ✅ 완료 | pipeline/backtest/oos/batch/sweep → 별도 히스토그램 |
| Docker 포트 보안 강화 | ✅ 완료 | 전 서비스 127.0.0.1 바인딩 (defense in depth) |
| 환율 DB 영속화 + 스케줄러 | ✅ 완료 | `_store_rate_to_db()` + 1시간 간격 `_exchange_rate_loop` |
| NewsCollector 스케줄러 wiring | ✅ 완료 | `handle_pre_market()` 스텝 2에 연결, 실패 시 비차단 |
| 경제지표 스케줄러 wiring | ✅ 완료 | `handle_pre_market()` 스텝 3, `_store_to_db()` 활성화, FRED 9개 지표 |
| 보안: Revocation 백엔드 강제 | ✅ 완료 | `AQTS_REVOCATION_BACKEND` 미설정 시 부팅 실패 (memory 기본값 제거) |
| 보안: Grafana 비밀번호 fallback 제거 | ✅ 완료 | `docker-compose.yml` `:-aqts2026` fallback 삭제, `GRAFANA_PASSWORD` 필수 |
| 보안: CORS 변수명 정정 | ✅ 완료 | `.env.example` `CORS_ORIGINS` → `CORS_ALLOWED_ORIGINS` |
| 보안: DB 포트 노출 | ✅ 해당없음 | 전 서비스 `127.0.0.1` 바인딩 확인 완료 |
| 보안: KIS WebSocket ws:// | ✅ 완료 | 부팅 가드 구현: 운영+LIVE에서 ws:// 차단, 예외 만료일 통제(23:59:59 UTC), scheme allowlist(ws/wss만), 설정 단일화. 런북: `docs/security/kis-websocket-security.md` |
| 보안: OTel insecure | ⚠️ acceptable risk | Docker 내부 네트워크 통신, 호스트 바인딩 127.0.0.1 적용 완료 |

## 8. 미해결 항목

| 항목 | 우선순위 | 비고 |
|------|----------|------|
| 텔레그램 발송 검증 | P1 | 다음 거래일(04-13 월) MARKET_CLOSE 이후 확인 |
| 환율 수집 배포 검증 | P2 | DB 영속화 코드 완료, 배포 후 `exchange_rates` 테이블 데이터 확인 필요 |
| NewsCollector 자동 수집 검증 | P2 | 04-13(월) 08:30 KST handle_pre_market 실행 시 검증 |
| 경제지표 자동 수집 검증 | P2 | 04-13(월) 08:30 KST FRED 9개 지표 수집 성공, DB 저장 실패 → 스키마 불일치 수정 완료 (아래 §8.1 참조) |
| ECOS API 키 설정 | ~~P3~~ | ✅ 2026-04-13 적용 완료, ECOS 4/4 활성 지표 수집 정상 (GDP 비활성화) |
| Docker 로그 영속화 | P1 | `docker-compose.yml` logging 설정 추가 — scheduler 100m×10, 나머지 50m×5 |
| ~~서버 .env CORS 변수명 변경~~ | ~~P2~~ | ✅ 2026-04-11 적용 완료 |
| ~~서버 .env AQTS_REVOCATION_BACKEND 추가~~ | ~~P1~~ | ✅ 2026-04-11 적용 완료 |

### 8.1 경제지표 DB 저장 스키마 불일치 수정 (2026-04-13)

**증상**: `handle_pre_market` 실행 시 FRED 9개 지표 수집은 성공하나, `_store_to_db()`에서 `column "date" of relation "economic_indicators" does not exist` 에러 발생. 경제지표가 Redis 캐시에만 존재하고 TimescaleDB에는 저장되지 않음.

**근본 원인**: `001_initial_schema.py` Alembic 마이그레이션이 정의한 테이블 스키마와 `_store_to_db()` INSERT문의 컬럼명이 불일치.

| 항목 | DB 스키마 (마이그레이션) | 코드 INSERT문 (수정 전) |
|------|--------------------------|-------------------------|
| 시간 컬럼 | `time` | `date` |
| 식별 컬럼 | `indicator_code` (PK) | (미사용) |
| PK/UNIQUE | `PK(time, indicator_code)` | `ON CONFLICT(indicator_name, date, source)` |
| 추가 컬럼 | (없음) | `unit`, `change_pct`, `collected_at` |

**수정 방안**: 코드를 기존 DB 스키마에 맞춤 (DB 무변경, TimescaleDB 하이퍼테이블 ALTER 위험 회피).

**변경 파일**:

- `core/data_collector/economic_collector.py`: `EconomicIndicator` dataclass 필드 `date`→`time`, `indicator_code` 추가, `unit`/`change_pct`/`collected_at` 제거. FRED/ECOS 수집부에 `indicator_code` 매핑(FRED: series_id, ECOS: stat_code). INSERT문 DB 스키마 정렬.
- `api/routes/market.py`: `item.date` → `item.time` (2곳)
- `tests/test_economic_collector.py`: dataclass 테스트 갱신

**검증**: ruff/black 통과, `test_economic_collector.py` 17개 전수 통과, 전체 pytest 3752 passed (기존 실패 제외).

---

## 9. 월요일(04-13) 자동 검증 스크립트

### 사용법

```bash
# 서버에서 직접 실행
cd ~/aqts
./scripts/verify_phase1_demo.sh              # 전체 검증
./scripts/verify_phase1_demo.sh pre_market    # 08:30 구간만
./scripts/verify_phase1_demo.sh market_close  # 15:30 구간만
./scripts/verify_phase1_demo.sh post_market   # 16:00 구간만
./scripts/verify_phase1_demo.sh exchange_rate # 환율 수집만
./scripts/verify_phase1_demo.sh health        # 시스템 상태만

# gcloud 원격 실행
gcloud compute ssh aqts-server --zone=asia-northeast3-a \
  --command="cd ~/aqts && ./scripts/verify_phase1_demo.sh all"
```

### 검증 시점별 실행 가이드

| 시각 (KST) | 명령 | 검증 대상 |
|-------------|------|-----------|
| 08:35 | `./scripts/verify_phase1_demo.sh pre_market` | 뉴스 수집, FRED 경제지표, DB 저장 |
| 10:00+ | `./scripts/verify_phase1_demo.sh exchange_rate` | 환율 DB 영속화 |
| 15:35 | `./scripts/verify_phase1_demo.sh market_close` | MarketClose 핸들러, 포트폴리오 스냅샷 |
| 16:05 | `./scripts/verify_phase1_demo.sh post_market` | PostMarket 핸들러, 텔레그램 발송, 일일 리포트 |
| 언제든 | `./scripts/verify_phase1_demo.sh health` | Docker 상태, API health, 스케줄러 heartbeat |

### 결과 해석

- **PASS**: 해당 항목 정상 동작 확인
- **FAIL**: 즉시 로그 확인 필요 (`docker compose logs scheduler --since '오늘T00:00:00' | less`)
- **WARN**: 해당 시점이 아직 지나지 않았거나, 선택적 기능(텔레그램 등)이 미설정된 경우

---

## 6. 2026-04-13 추가 수정 사항

### 6.1 verify_phase1_demo.sh 스케줄러 heartbeat 검증 방식 변경

**변경 전**: backend health API(`/api/system/health`)에서 `scheduler_heartbeat.age_seconds`를 파싱.
**문제**: scheduler가 별도 컨테이너로 분리되어 `SCHEDULER_ENABLED=false` → backend가 `scheduler: "external"` 반환 → heartbeat 확인 불가.
**변경 후**: `docker compose ps scheduler --format json`으로 Docker health status 직접 확인. `healthy`면 PASS, `running`(healthcheck 없음)이면 WARN.

### 6.2 CD 배포 시 로그 백업 (cd.yml)

**문제**: `docker compose up -d --force-recreate`로 컨테이너가 재생성되면 기존 컨테이너의 JSON 로그 파일이 함께 삭제됨. 배포 전 스케줄러 로그(freeze 디버깅용 등)가 유실.
**해결**: `--force-recreate` 직전에 `docker compose logs` 출력을 `~/aqts/logs/deploy-backups/` 호스트 디렉토리에 타임스탬프 기반 파일로 백업. 30일 이상 된 백업은 자동 정리. 배포 경로와 롤백 경로 모두 적용.

### 6.3 verify_phase1_demo.sh pipefail 환경 grep 0건 종료 수정

**문제**: `set -eo pipefail` 환경에서 `grep pattern | wc -l`의 grep이 0건 매칭 시 exit 1 → pipefail로 파이프 실패 → 스크립트 즉시 종료. heartbeat 경고 이후 스크립트가 조용히 종료되는 현상.
**해결**: 모든 `grep ... | wc -l` 패턴을 `{ grep ... || true; } | wc -l`로 변경.

### 6.4 asyncio.gather return_exceptions=True 추가 (economic_collector.py)

**문제**: FRED/ECOS 병렬 수집 시 한쪽 예외가 다른 쪽을 취소 → 전체 수집 실패 및 이벤트루프 freeze 가능성.
**해결**: `return_exceptions=True` 추가, 예외 발생 시 빈 리스트 대체 + error 로깅. 4건 테스트 추가.

### 6.5 orders 테이블 SQL 컬럼명 불일치 수정 (filled_qty/avg_price → filled_quantity/filled_price)

**문제**: `orders` 테이블의 실제 스키마는 `filled_quantity` (integer), `filled_price` (numeric(18,4)) 이지만, 다수의 SQL 쿼리에서 존재하지 않는 `filled_qty`, `avg_price` 컬럼을 참조. 15:30 MARKET_CLOSE 핸들러에서 `column "filled_qty" does not exist` 에러로 거래 통계 조회 실패. 같은 이유로 주문 저장(INSERT), 포트폴리오 조회, 주문 이력 조회도 모두 실패 상태였음.

**근본 원인**: 초기 마이그레이션(`001_initial_schema.py`)은 처음부터 `filled_quantity`/`filled_price`로 정의했으나, SQL raw query 작성 시 Python `OrderResult` dataclass의 필드명(`avg_price`)이나 KIS API 응답 키(`filled_qty`)와 혼동하여 잘못된 컬럼명을 사용.

**영향 범위 및 수정 내역**:

| 파일 | 수정 위치 | 변경 내용 |
|---|---|---|
| `core/scheduler_handlers.py` | L356, L559 | SELECT 쿼리 `filled_qty` → `filled_quantity`, `avg_price` → `filled_price` |
| `core/order_executor/executor.py` | L950-969 | INSERT INTO orders 컬럼명 및 파라미터 키 수정 |
| `api/routes/portfolio.py` | L49-55, L128-134, L201-206, L268-269 | 4개 SELECT 쿼리 컬럼명 수정 |
| `api/routes/orders.py` | L459, L471, L518 | 3개 SELECT 쿼리 컬럼명 수정 |

**변경하지 않은 항목** (SQL 컬럼이 아닌 Python/API 레벨):

- `OrderResult.avg_price` — Python dataclass 필드명 (executor.py L120)
- `PositionInfo.avg_price` — Pydantic 스키마 (schemas/portfolio.py)
- `api_result.get("filled_qty")` — KIS API 응답 파싱 (executor.py L538, L635)
- `positions_data[].avg_price` — KIS 잔고 조회 결과 딕셔너리 키 (scheduler_handlers.py L330)
- `portfolio_holdings.avg_price` — 별도 테이블의 올바른 컬럼명 (001_initial_schema.py L203)

**검증**: ruff 0 errors, black 0 reformats, pytest 4002 passed / 0 failed.

### 6.6 verify_phase1_demo.sh 배포 후 로그 유실 대응 — 백업 로그 fallback 검색

**문제**: CD 파이프라인의 `--force-recreate`로 컨테이너가 재생성되면 `docker compose logs`에 이전 이벤트 로그가 남지 않음. 배포 후 검증 스크립트를 실행하면 실제로 정상 실행된 이벤트도 FAIL로 판정되는 false-negative 발생. §6.2에서 배포 전 로그 백업을 추가했으나, 검증 스크립트가 이를 활용하지 않아 백업의 효과가 절반만 달성됨.

**해결**: `_combined_logs()` 함수를 추가하여, 현재 컨테이너 로그(`docker compose logs`)와 당일 백업 로그(`~/aqts/logs/deploy-backups/{service}-pre-deploy-{YYYYMMDD}*.log`)를 합산 검색. `check_log`, `check_no_error`, 텔레그램 발송 확인 등 모든 로그 검색 경로에 적용.

**추가 수정**: 경제지표 DB 쿼리의 사용자명 `aqts` → `aqts_user` (§6.1에서 exchange_rates만 수정했고 economic_indicators 쿼리가 누락되어 있었음).

### 6.7 verify_phase1_demo.sh 검색 패턴 보정 — false-negative 해소

**문제**: 실제 실행 완료된 이벤트가 FAIL로 판정되는 false-negative 7건.

**원인과 수정 내역**:

| 항목 | 원인 | 수정 |
|---|---|---|
| 거래일 인식 | `거래일.*${TODAY}` 패턴이 실제 로그 `=== 거래일 2026-04-13 ===`와 불일치 | 멱등성 복원 로그도 매칭하도록 OR 패턴 추가 |
| PRE_MARKET/MARKET_CLOSE/POST_MARKET 시작·완료 | 컨테이너 재생성 후 실행 로그 유실, 멱등성 복원 로그만 존재 | `▶`/`✓` 로그 외에 `멱등성.*EVENT\|이미 실행된 이벤트.*EVENT` 패턴 추가 |
| 환율 조회 | `backend` 컨테이너 검색했으나 실제 환율 수집은 `scheduler`의 ExchangeRateCollectionLoop | 검색 대상 `scheduler`로 변경, 패턴을 `환율 DB 저장\|[ExchangeRate] 수집 완료`로 수정 |
| PostMarket 핸들러 완료 | `[PostMarket] 완료:` 패턴만 검색하나 KIS 실패 시 `skip` 로그 출력 | `skip` 도 "실행 확인" 으로 인정 (에러가 아닌 방어 동작) |
| MarketClose 에러/스킵 | `skip` 을 에러와 동일 취급 | `실패`만 에러로 판정, `skip`은 별도 warn으로 분리 |
