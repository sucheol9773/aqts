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

### 6.7 verify_phase1_demo.sh DB 인증 및 쿼리 로직 수정 (2026-04-13)

**문제 3건**:

1. **MongoDB 인증 누락**: `mongosh --quiet --eval '...' aqts` — credentials 없이 호출 → `MongoServerError: Authentication failed`. 뉴스 MongoDB 저장 확인이 항상 0건(WARN) 반환.
2. **Redis 인증 누락**: `redis-cli GET "portfolio:snapshot:..."` — 비밀번호 없이 호출 → `(error) NOAUTH Authentication required.` 문자열이 비어있지 않고 `(nil)`도 아니므로 **거짓 PASS** 발생. 포트폴리오 스냅샷/일일 리포트 확인이 실제 데이터와 무관하게 항상 PASS.
3. **경제지표 PostgreSQL 날짜 조건 오류**: `WHERE time::date = '2026-04-13'` — `time` 컬럼은 FRED/ECOS API의 **관측일**(예: 2026-03-01)이지 수집일이 아님. 오늘 수집해도 관측일이 오늘이 아니면 0건 반환.

**추가 발견**: Redis/MongoDB 비밀번호에 `!` 특수문자가 포함되어 있어, bash 히스토리 확장(`set +H` 필요)과 `redis-cli -a`/`mongosh -p` 등 CLI 인자 전달에서 반복적으로 인증 실패 발생. shell 레벨에서의 credentials 전달은 구조적으로 취약.

**해결 방식**: MongoDB/Redis 쿼리를 `docker exec aqts-scheduler python -c "..."` 로 변경. scheduler 컨테이너 내부에서 Python `os.environ` + `quote_plus()` 로 credentials를 처리하므로 **모든 특수문자**(!, @, #, $ 등)에 안전.

| 항목 | 변경 전 | 변경 후 |
|---|---|---|
| 경제지표 PostgreSQL | `WHERE time::date = TODAY` | `WHERE time >= CURRENT_DATE - INTERVAL '90 days'`, `count(DISTINCT indicator_code)` |
| 뉴스 MongoDB | `docker exec aqts-mongodb mongosh ...` (인증 없음) | `docker exec aqts-scheduler python -c "..."` (PyMongo + 환경변수 인증) |
| Redis 스냅샷 | `docker exec aqts-redis redis-cli GET ...` (인증 없음, 거짓 PASS) | `docker exec aqts-scheduler python -c "..."` (redis-py + 환경변수 인증) |
| Redis 리포트 | 동일 거짓 PASS | 동일 방식으로 수정 |

**검증 기대 결과**: WARN 6 → WARN 2 이하 (MarketClose/PostMarket 에러는 KIS Rate Limit 관련, 텔레그램은 설정 상태 의존).

### 6.8 verify_phase1_demo.sh 검색 패턴 보정 — false-negative 해소

**문제**: 실제 실행 완료된 이벤트가 FAIL로 판정되는 false-negative 7건.

**원인과 수정 내역**:

| 항목 | 원인 | 수정 |
|---|---|---|
| 거래일 인식 | `거래일.*${TODAY}` 패턴이 실제 로그 `=== 거래일 2026-04-13 ===`와 불일치 | 멱등성 복원 로그도 매칭하도록 OR 패턴 추가 |
| PRE_MARKET/MARKET_CLOSE/POST_MARKET 시작·완료 | 컨테이너 재생성 후 실행 로그 유실, 멱등성 복원 로그만 존재 | `▶`/`✓` 로그 외에 `멱등성.*EVENT\|이미 실행된 이벤트.*EVENT` 패턴 추가 |
| 환율 조회 | `backend` 컨테이너 검색했으나 실제 환율 수집은 `scheduler`의 ExchangeRateCollectionLoop | 검색 대상 `scheduler`로 변경, 패턴을 `환율 DB 저장\|[ExchangeRate] 수집 완료`로 수정 |
| PostMarket 핸들러 완료 | `[PostMarket] 완료:` 패턴만 검색하나 KIS 실패 시 `skip` 로그 출력 | `skip` 도 "실행 확인" 으로 인정 (에러가 아닌 방어 동작) |
| MarketClose 에러/스킵 | `skip` 을 에러와 동일 취급 | `실패`만 에러로 판정, `skip`은 별도 warn으로 분리 |

### 6.9 verify_phase1_demo.sh 경제지표 DB 쿼리 컬럼명 수정 (collected_at → time)

**문제**: `economic_indicators` 테이블의 타임스탬프 컬럼은 `time`이지만, 검증 스크립트에서 `collected_at`으로 참조하여 항상 0건으로 조회됨 (WARN 판정).
**해결**: `collected_at::date` → `time::date`로 수정.

### 6.10 KIS 토큰 발급 재시도 백오프 강화 (EGW00133 대응)

**문제**: KIS API 토큰 발급의 tenacity 재시도가 `max=10`(초)으로 설정되어 있었으나, EGW00133 에러는 1분당 1회 발급 제한이므로 3회 × 최대 10초 대기로는 모든 재시도가 실패할 수 있었다. 04/13 PRE_MARKET에서 OHLCV 수집 지연의 간접 원인.

**수정**:

- `config/settings.py`: `token_retry_count=5`, `token_retry_max_wait=60` 신규 설정 추가. 기존 `api_retry_count`/`api_timeout`은 REST API 호출 전용으로 유지.
- `kis_client.py::_issue_token()`: `stop_after_attempt(api_retry_count)` → `stop_after_attempt(token_retry_count)`, `wait_exponential(max=10)` → `wait_exponential(multiplier=2, max=token_retry_max_wait)`.
- 재시도 대기 시간: 4초 → 8초 → 16초 → 32초 → 60초 (총 ~120초, 1분 제한 2회 커버).

**기존 방어 레이어와의 관계**: 이 변경은 토큰 발급 자체의 tenacity 재시도를 강화한 것이다. `kis_recovery.py`의 75초 쿨다운 복구 경로는 이 재시도가 모두 실패한 이후 작동하는 상위 레이어이며, 변경 없이 유지된다.

### 6.11 Scheduler freeze (Redis SCAN) 관찰 종료

**결론**: 실질적 위험 없음. 관찰 종료.

**분석 결과**:

- Redis SCAN은 `scheduler_idempotency.py::load_executed_for_date()`에서 스케줄러 부팅 시 1회만 호출 (async, non-blocking).
- 일일 최대 5개 키 (`scheduler:executed:{YYYY-MM-DD}:{EVENT_TYPE}`), TTL 자동 만료 — 키 누적 위험 없음.
- 기존에 "freeze"로 보고된 현상은 스케줄러 루프의 긴 sleep 경로로 인한 heartbeat stale이었으며, `scheduler_heartbeat.py`의 30초 단위 chunk sleep으로 이미 해결 완료.

### 6.12 앙상블 시그널 레짐 enum 캐스팅 버그 수정 (2026-04-13)

**증상**: 116개 앙상블 시그널 중 일부 티커에서 `{"error": "'str' object has no attribute 'value'"}` 발생. 레짐 전환이 일어나지 않는 SIDEWAYS 티커는 정상, 레짐 전환(TRENDING_UP/DOWN, HIGH_VOLATILITY)이 발생하는 티커에서만 실패.

**근본 원인**: `pandas.Series.where()`에 `DynamicRegime(str, Enum)` 객체를 전달하면 numpy 고정폭 문자열 변환 과정에서 `str(enum)`이 `'DynamicRegime.TRENDING_UP'` (25자)로 변환된 뒤 기존 시리즈 길이(11자)로 잘려 `'DynamicRegi'`라는 무효한 문자열이 됨. 이후 `to_summary_dict()`에서 `.value` 접근 시 `AttributeError` 발생.

**수정 내용**:

- `dynamic_ensemble.py::_assign_regime_weights()`: `regime_series`에 enum 대신 `.value` 문자열을 직접 저장 (`DynamicRegime.SIDEWAYS` → `DynamicRegime.SIDEWAYS.value`)
- `dynamic_ensemble.py::compute()`: 최종 추출 시 `DynamicRegime(regime_series.iloc[-1])`로 enum 복원
- `runner.py::to_summary_dict()`, `_compute()`: 방어적 `hasattr` 체크 추가
- `test_dynamic_ensemble.py`: `TestRegimeEnumPreservation` 테스트 클래스 추가 (레짐 전환 시 enum 타입 보존 + 모든 레짐 유형에서 `.value` 접근 검증)
- 연동 영향: `hyperopt/objective.py`의 `regime_series == regime_enum` 비교는 `DynamicRegime(str, Enum)`이므로 문자열 비교와 호환되어 영향 없음

**검증**: 전체 pytest 4009 passed.

---

## 7. Phase 1 후속 개선 작업 (2026-04-14)

Phase 1 DEMO 검증 완료 후, 미완성 API wiring 4건에 대해 순차적으로 개선 작업을 수행했습니다.

### 7.1 Portfolio Construction API 엔드포인트 신규 추가

**변경 전**: 포트폴리오 구성(최적화) API 없음. 프론트엔드에서 직접 `PortfolioConstructionEngine`을 호출할 방법이 없었음.

**변경 후**: `POST /api/portfolio/construct` 엔드포인트 추가

- 요청 파라미터: `method` (mean_variance / risk_parity / black_litterman), `risk_profile` (CONSERVATIVE / BALANCED / AGGRESSIVE / DIVIDEND), `seed_capital` (옵션)
- 처리 흐름:
  1. Redis `ensemble:latest:*` 키에서 앙상블 시그널 조회
  2. DB `universe` 테이블에서 섹터/시장 정보 조회
  3. DB `orders` 테이블에서 현재 포지션 비중 산출
  4. `PortfolioConstructionEngine.construct()` 실행
  5. `ConstructionResponse` (할당 목록, 현금 비중, 섹터/시장 가중치) 반환
- RBAC: `require_operator` 적용 (CLAUDE.md RBAC Wiring Rule 준수)
- 스키마: `ConstructionRequest`, `TargetAllocationResponse`, `ConstructionResponse` 3개 신규 추가

**파일 변경**: `api/routes/portfolio.py`, `api/schemas/portfolio.py`

### 7.2 Rebalancing Trigger API — stub에서 실제 엔진 연결

**변경 전**: `POST /api/system/rebalancing`이 감사 로그만 기록하고 하드코딩된 더미 응답 반환 (stub 상태)

**변경 후**: 실제 `RebalancingEngine` 연결

- 처리 흐름:
  1. `InvestorProfileManager.get_profile(current_user)` — 투자자 프로필 조회 (없으면 에러 반환)
  2. Redis `ensemble:latest:*` 키에서 앙상블 시그널 조회 (없으면 에러 반환)
  3. DB에서 현재 포지션 및 유니버스 정보 조회
  4. `PortfolioConstructionEngine` + `RebalancingEngine` 인스턴스 생성
  5. `execute_scheduled_rebalancing()` 실행
  6. `rebal_result.to_dict()` 반환
- RBAC: 기존 `require_operator` 유지

**파일 변경**: `api/routes/system.py`

### 7.3 Telegram 알림 파이프라인 Wiring 점검 — ✅ 정상

코드 레벨 wiring 검증 결과, 파이프라인 전체 경로가 정상 연결되어 있음을 확인:

- `AlertManager._dispatch_via_router()` → `NotificationRouter.dispatch()` → `TelegramChannelAdapter.send()` → `TelegramTransport.send_text()`
- `main.py` lifespan에서 `set_notification_router()` 호출 및 `_alert_retry_loop` asyncio task 기동
- 3단계 fallback: Telegram → File → Console
- 관련 테스트: `test_alert_manager_dispatch_wiring.py`, `test_alert_retry_loop.py`

### 7.4 Daily Report 생성 로직 POST_MARKET 연결 점검 — ✅ 정상

- `handle_post_market()` → `DailyReporter.generate_report()` → `send_telegram_report()` 전체 경로 연결 확인
- `register_pipeline_handlers(scheduler)`에서 POST_MARKET 핸들러 정상 등록
- 3단계 안전 검증: 스냅샷 읽기 실패 / 빈 스냅샷 / 상태 불일치 체크
- Redis `report:daily:{날짜}` 키로 90일 보관
- Phase 1 서버에서 daily report 0건 이유: POST_MARKET이 아직 한 번도 정상 실행되지 않음 (스냅샷 데이터 부재). 코드 wiring 결손 아님.

### 7.5 테스트 변경 사항

| 파일 | 변경 내용 |
|------|-----------|
| `tests/test_coverage_api_routes_v2.py` | `TestPortfolioConstructRoute` 클래스 추가 (3개 테스트) |
| `tests/test_system_routes.py` | 리밸런싱 stub 테스트 3개 → 실제 엔진 테스트 4개로 교체 |

**검증**: ruff 0 errors, black 0 reformatted, doc-sync PASSED, 전체 pytest 4012 passed.

### 7.6 AuthenticatedUser → user_id wiring 버그 수정 (2026-04-14)

**증상**: `PUT /api/profile/` 호출 시 `asyncpg.exceptions.DataError: invalid input for query argument $1: AuthenticatedUser(id='2ff026cd-...') (expected str, got AuthenticatedUser)` 발생.

**근본 원인**: RBAC 미들웨어(`require_operator`, `require_viewer`)가 반환하는 `AuthenticatedUser(NamedTuple)` 객체를 `InvestorProfileManager.get_profile()` 등에 그대로 전달. `get_profile()`은 `str` 타입 `user_id`를 기대하지만, `AuthenticatedUser` 객체가 전달되어 SQL 파라미터 바인딩 실패.

**영향 범위**: `profile.py`, `system.py`, `market.py` — `current_user`를 DB 쿼리에 직접 전달하는 5개 라우트.

**수정 내용**:

- `api/routes/profile.py`: `get_profile(current_user)` → `get_profile(current_user.id)` 등 4건
- `api/routes/system.py`: `get_profile(current_user)` → `get_profile(current_user.id)` 1건
- `api/routes/market.py`: `get_profile(current_user)` → `get_profile(current_user.id)` 1건 + `InvestorProfile(user_id=current_user)` → `user_id=current_user.id` 1건
- 테스트: `test_system_routes.py`, `test_coverage_api_routes_v2.py`의 `current_user` mock을 문자열 → `AuthenticatedUser` 객체로 교체

**검증**: ruff 0 errors, black 0 reformatted, pytest 4013 passed.

### 7.7 감사 로그 / API 응답의 raw AuthenticatedUser 직렬화 수정 (2026-04-14)

**증상**: 감사 로그 metadata와 API 응답의 `"user"` 필드에 `AuthenticatedUser(NamedTuple)` 객체가 그대로 전달됨. NamedTuple은 JSON 직렬화 시 배열(tuple)로 변환되어 API 응답에서 `"user": ["uuid", "admin", "admin"]` 형태가 됨. `json.dumps(default=str)` 경유 시 `"AuthenticatedUser(id='...', username='...', role='...')"` 문자열이 저장됨.

**근본 원인**: §7.6의 user_id wiring 수정이 DB 쿼리 경로만 커버하고, 감사 로그 metadata 딕셔너리와 API 응답 딕셔너리에 포함된 `current_user` 참조는 누락.

**영향 범위**: `system.py` (3건), `oos.py` (1건), `orders.py` (1건) — 총 5건.

**수정 내용**:

- `api/routes/system.py`: metadata `"user": current_user` → `current_user.id` (2건), description f-string `{current_user}` → `{current_user.username}` (1건), API 응답 `"user": current_user` → `current_user.id` (1건)
- `api/routes/oos.py`: logger.info f-string `user={current_user}` → `user={current_user.username}` (1건)
- `api/routes/orders.py`: audit description `user {current_user}` → `user {current_user.username}` (1건)

**검증**: ruff 0 errors, black 0 reformatted, pytest 4013 passed (233.94s).

### 7.8 InvestorProfileManager 생성자 인자 전달 버그 수정 (2026-04-14)

**증상**: `POST /api/system/rebalancing` 호출 시 `InvestorProfileManager() takes no arguments` TypeError 발생.

**근본 원인**: `system.py:194`에서 `InvestorProfileManager(db)`로 DB 세션을 인자로 전달했으나, `InvestorProfileManager` 클래스에는 `__init__`이 정의되어 있지 않음. 다른 라우트(`profile.py`, `market.py`)에서는 `InvestorProfileManager()`로 정상 호출.

**수정**: `InvestorProfileManager(db)` → `InvestorProfileManager()`

**검증**: ruff 0 errors, black 0 reformatted, test_system_routes 15 passed.

### 7.9 InvestorProfile Decimal→float 캐스팅 누락 수정 (2026-04-14)

**증상**: `POST /api/system/rebalancing` 호출 시 `unsupported operand type(s) for *: 'float' and 'decimal.Decimal'` TypeError 발생.

**근본 원인**: PostgreSQL `NUMERIC(18,2)` 컬럼(`seed_amount`, `loss_tolerance`)은 Python `decimal.Decimal`로 반환되지만, `InvestorProfile.from_dict()`에서 `float()` 캐스팅 없이 그대로 저장. 이후 리밸런싱 엔진에서 `float * Decimal` 연산 시 TypeError 발생.

**수정**: `from_dict()`에서 `seed_amount=float(data["seed_amount"])`, `loss_tolerance=float(data["loss_tolerance"])` 명시적 캐스팅 추가.

**검증**: ruff 0 errors, black 0 reformatted, test_profile 32 passed + test_rebalancing 26 passed.

### 7.10 RebalancingEngine에 OrderExecutor / TelegramTransport wiring 추가 (2026-04-14)

**증상**: 리밸런싱 성공(20건 주문 생성)했으나 `OrderExecutor not available, orders not executed` 경고와 함께 실제 KIS 주문이 미실행. DB orders 테이블 0건.

**근본 원인**: `system.py`의 `trigger_rebalancing`에서 `RebalancingEngine(profile, construction_engine)`으로 생성 시 `order_executor`와 `telegram_notifier` 파라미터를 전달하지 않아 `None` 상태. Wiring Rule 위반 — "정의했다 ≠ 적용했다".

**수정**:
- `OrderExecutor()` 인스턴스 생성하여 `order_executor` 파라미터로 전달
- `create_telegram_transport()` 팩토리로 `telegram_notifier` 파라미터 전달
- import 추가: `core.notification.telegram_transport.create_transport`, `core.order_executor.executor.OrderExecutor`

**검증**: ruff 0 errors, black 0 reformatted, test_system_routes 15 passed.

### 7.11 OrderExecutor QuoteProvider 주입 + Telegram 시간대 KST 수정 (2026-04-14)

**증상 1**: 리밸런싱 주문 20건 전부 FAILED — `live OrderExecutor requires a quote_provider; refusing to trade blind`. OrderExecutor의 fail-closed 정책에 의해 quote_provider 없이는 주문이 거부됨.

**증상 2**: Telegram 알림 메시지의 시간이 UTC로 표시됨 (사용자는 KST 기대).

**수정**:
- `system.py`: `KISQuoteProvider()` 생성 후 `OrderExecutor(quote_provider=quote_provider)`로 전달
- `rebalancing.py`: 정기/비상 리밸런싱 알림의 시간 포맷을 `astimezone(timezone(timedelta(hours=9)))` + `KST` 표기로 변경
- `rebalancing.py`: `timedelta` import 추가

**검증**: ruff 0 errors, black 0 reformatted, test_system_routes 15 + test_rebalancing 36 = 51 passed.

### 7.12 리밸런싱 market 하드코딩 제거 + OrderResult order_type NULL 수정 (2026-04-14)

**증상 1**: 한국 종목(047050, 261240 등)이 `market=NYSE`로 주문되어 US 시세 조회 실패. `_generate_rebalancing_orders`에서 `market=Market.NYSE`로 하드코딩(`# 단순화`).

**증상 2**: 주문 실패 시 DB 저장 실패 — `null value in column "order_type" violates not-null constraint`. `_store_order` INSERT에 `order_type` 컬럼 누락, `OrderResult`에 `order_type` 필드 없음.

**수정 1 (market 하드코딩)**:
- `rebalancing.py` `_generate_rebalancing_orders`: `TargetPortfolio.allocations`의 `market` 정보를 딕셔너리로 추출하여 종목별 올바른 market 전달

**수정 2 (order_type NULL)**:
- `OrderResult` dataclass에 `order_type: OrderType = OrderType.MARKET` 필드 추가
- `_store_order` INSERT 문에 `order_type` 컬럼/파라미터 추가
- 에러 경로 `OrderResult` 생성 시 `order_type=request.order_type` 명시적 전달

**검증**: ruff 0 errors, black 0 reformatted, 51 passed (system+rebalancing) + 325 passed (order 관련).

### 7.13 order_id UNIQUE 제약 위반 수정 — UUID fallback 생성 (2026-04-14)

**증상**: 20건 주문 중 첫 번째만 DB에 저장되고 나머지 19건은 `duplicate key value violates unique constraint "orders_order_id_key"` 에러로 저장 실패. 모든 주문의 `order_id`가 빈 문자열(`""`)이어서 UNIQUE 제약 위반.

**근본 원인**:
- 에러 경로: `OrderResult(order_id="", ...)` 하드코딩
- 성공 경로: `api_result.get("order_id", "")` — KIS API가 order_id를 반환하지 않으면 빈 문자열

**수정**:
- 에러 경로: `order_id=f"FAIL_{uuid.uuid4().hex[:12]}"` — 고유 ID 생성
- 성공 경로: `raw_order_id if raw_order_id else f"KIS_{uuid.uuid4().hex[:12]}"` — KIS 응답이 비어있으면 fallback UUID
- `import uuid` 추가

**검증**: ruff 0 errors, black 0 reformatted, 325 passed (order 관련).

### 7.14 KRX 주문 실패 원인 수정 — 토큰 사전 체크 + RetryError unwrap + 주문 간 딜레이 (2026-04-14)

**발견**: E2E 검증 시 KRX 6건 중 SUBMITTED 3건 / FAILED 6건 발생 (일부 이전 검증 누적 포함).
DB의 `error_message`가 `RetryError[<Future ... raised KISAPIError>]` 형태로만 저장되어 실제 KIS 에러 코드가 은폐됨.

**근본 원인 분석** (로그 관찰):
1. KIS 토큰 발급 EGW00133 (1분당 1회 제한)으로 토큰 미확보
2. 토큰 recovery 완료 전(02:38:29 예정)에 주문 실행 시작(02:38:04)
3. 토큰 없는 상태에서 `_request()` → `_get_auth_headers()` → `get_access_token()` → `_issue_token()` 재시도 → 전부 EGW00133으로 실패
4. tenacity `RetryError`가 원본 `KISAPIError`를 감싸서 `str(e)`가 진단 불가한 형태로 DB에 저장

**수정 내용**:

| 파일 | 변경 | 목적 |
|------|------|------|
| `kis_client.py` | `KISTokenManager.has_valid_token` property 추가 | 네트워크 호출 없이 토큰 유효 여부 확인 |
| `kis_client.py` | `KISClient.has_valid_token` property 추가 | TokenManager에 위임 |
| `executor.py` | `_unwrap_retry_error()` 헬퍼 함수 추가 | `RetryError` → 원본 예외 메시지 추출 |
| `executor.py` | `execute_order()` catch 블록에서 unwrap 적용 | DB에 실제 KIS 에러 코드 저장 |
| `executor.py` | `_execute_market_order()` / `_execute_limit_order()`에 토큰 사전 체크 | 토큰 미확보 시 즉시 TOKEN_UNAVAILABLE 에러 (무의미한 API 재시도 방지) |
| `rebalancing.py` | `_execute_orders()`에 주문 간 0.5초 딜레이 추가 | `execute_batch_orders()`와 동일한 rate limit 대응 |

**추가된 테스트** (10건):
- `TestUnwrapRetryError` (3건): RetryError unwrap, 일반 예외, KISAPIError 보존
- `TestHasValidToken` (5건): 토큰 없음/만료/임박/유효/Client 위임
- `TestExecuteOrdersDelay` (2건): 복수 주문 딜레이 적용, 단건 주문 딜레이 미적용

**검증**: ruff 0 errors, black 0 reformatted, 4023 passed, 0 failed.

### 7.15 리밸런싱 중복 실행 방지 — 멱등성 체크 + 분산 락 (2026-04-14)

**발견**: E2E 검증 시 `/api/system/rebalancing` 엔드포인트를 504 타임아웃 후 재호출하여 동일 거래일에 20건씩 2회(총 40건)의 주문이 생성됨.

**근본 원인**: 리밸런싱 엔드포인트에 중복 실행 방지 메커니즘이 없었음.
- 같은 거래일 내 재요청을 차단하지 않음
- 동시 요청에 대한 동시성 제어 없음

**수정 내용**:

| 파일 | 변경 | 목적 |
|------|------|------|
| `system.py` | `is_executed()` 기반 멱등성 체크 추가 (Step 0-a) | 같은 거래일(KST) 중복 실행 차단 |
| `system.py` | Redis 분산 락 (`SETNX`, TTL 5분) 추가 (Step 0-b) | 동시 실행 차단 (409 반환) |
| `system.py` | `force: bool = Query(default=False)` 파라미터 추가 | 운영자 판단 시 멱등성 우회 허용 |
| `system.py` | 성공 후 `mark_executed()` 호출 (Step 5) | 해당 거래일 실행 완료 기록 |
| `system.py` | `finally` 블록에서 락 해제 | 예외 발생 시에도 락 누수 방지 |

**동작 흐름**:
1. `force=False`(기본) + 오늘 이미 실행 → `already_executed` 응답 (200, 재실행 없음)
2. `force=True` → 멱등성 체크 우회, 분산 락만 적용
3. 분산 락 미획득 → 409 Conflict 응답
4. 정상 실행 완료 → `mark_executed()` 기록 + 락 해제

**추가된 테스트** (3건):
- `test_trigger_rebalancing_idempotency_block`: 같은 거래일 중복 요청 시 `already_executed` 반환 확인
- `test_trigger_rebalancing_idempotency_force_bypass`: `force=True` 시 멱등성 우회 확인
- `test_trigger_rebalancing_lock_conflict`: 분산 락 충돌 시 409 반환 확인

**기존 테스트 수정** (4건): 모든 리밸런싱 테스트에 `is_executed`, `mark_executed`, Redis 락 mock 추가 및 `force=False` 명시적 전달 (FastAPI `Query` 객체 기본값 문제 해결).

**검증**: ruff 0 errors, black 0 reformatted, 4025 passed, 0 failed (gen_status 반영 후 3929 total).

### 7.16 주문 실패 Telegram 알림 추가 (2026-04-14)

**발견**: E2E 검증에서 20건 주문 전부 FAILED(모의투자 잔고 부족 + 장외 시간 KIS 서버 오류)인데, Telegram 알림이 한 건도 발송되지 않음.

**근본 원인**: `RebalancingEngine`에 주문 실패 알림 코드 경로가 존재하지 않았음.
- `_send_rebalancing_completed_notification`: 성공 시에만 호출
- `_send_emergency_notification`: 비상 리밸런싱에만 호출
- 주문 FAILED에 대한 알림: **미구현**

**참고**: NotificationRouter wiring 자체는 정상 (기동 로그 `NotificationRouter wired`, `AlertRetryLoop started` 확인). 알림 파이프라인 인프라가 아닌 비즈니스 로직 누락.

**수정 내용**:

| 파일 | 변경 | 목적 |
|------|------|------|
| `rebalancing.py` | `_execute_orders()` 반환 타입 `None` → `list[OrderResult]` | 주문 결과 수집 |
| `rebalancing.py` | 실행 후 FAILED 건 필터링 → `_send_order_failure_notification()` 호출 | 실패 알림 트리거 |
| `rebalancing.py` | `_send_order_failure_notification()` 메서드 추가 | 에러 유형별 그룹핑하여 Telegram 발송 |
| `rebalancing.py` | `OrderStatus`, `OrderResult` import 추가 | 반환 타입 및 상태 비교용 |

**알림 메시지 형식**:
```
⚠️ 리밸런싱 주문 실패 알림

전체: 20건 | 성공: 0건 | 실패: 20건

• 모의투자 주문가능금액이 부족합니다
  → 005930, 000660, 047050, 261240, 058470 외 6건

• Server error '500 Internal Server Error' ...
  → XOM, QQQ, AAPL, PG, KO 외 2건
```

**추가된 테스트** (4건):
- `test_failure_notification_sent_when_orders_fail`: FAILED 시 Telegram 호출 + 메시지 내용 확인
- `test_no_failure_notification_when_all_succeed`: 전체 성공 시 알림 미발송
- `test_failure_notification_groups_errors`: 에러 유형별 그룹핑 확인
- `test_failure_notification_without_telegram`: Telegram 미설정 시 예외 없이 로그만 남김

**기존 테스트 수정** (2건): `TestExecuteOrdersDelay` 테스트에 `OrderResult` 반환값 설정 + `engine._telegram = None` 추가 (반환 타입 변경 대응).

**배포 후 검증에서 추가 발견**:
- `OrderExecutor.execute_order()`는 FAILED 결과를 DB에 저장한 뒤 **예외를 재전파**(raise)함
- `_execute_orders`의 except 블록에서 예외를 잡았지만 `results`에 추가하지 않아 실패 건이 누락됨
- 수정: except 블록에서 `OrderResult(status=FAILED)`를 직접 생성하여 `results`에 추가
- 추가 테스트 1건: `test_failure_notification_on_exception_raise` — 예외 raise 시에도 결과 수집 + 알림 발송 확인

**검증**: ruff 0 errors, black 0 reformatted, 4030 passed, 0 failed (gen_status 반영 후 3934 total).

---

## 10. E2E 실거래 사이클 검증 결과 (2026-04-14)

### 10.1 검증 환경

| 항목 | 값 |
|------|------|
| 서버 | GCP Compute Engine (aqts-server, 34.64.216.144) |
| 거래 모드 | DEMO (KIS 모의투자) |
| 위험 성향 | BALANCED |
| 투자 스타일 | DISCRETIONARY (자동 매매) |
| 초기 자본 | 10,000,000원 |
| 투자 목적 | WEALTH_GROWTH |
| 앙상블 시그널 | 115+ 종목 (Redis 캐시) |
| 검증 시간 | 2026-04-14 09:30~11:20 KST |

### 10.2 E2E 경로 검증 결과

| 단계 | 결과 | 비고 |
|------|------|------|
| 1. InvestorProfile 생성 (PUT /api/profile/) | ✅ 성공 | BALANCED/DISCRETIONARY/WEALTH_GROWTH |
| 2. 리밸런싱 트리거 (POST /api/system/rebalancing) | ✅ 성공 | 20건 주문 생성 |
| 3. PortfolioConstructionEngine | ✅ 성공 | 114 positions, mean_variance, cash=5% |
| 4. RebalancingEngine → 주문 생성 | ✅ 성공 | 올바른 market 할당 (KRX/NYSE/NASDAQ) |
| 5. OrderExecutor → KIS API 주문 전송 | ✅ 성공 | KIS DEMO 토큰 발급, 한국 종목 SUBMITTED |
| 6. DB 주문 저장 | ✅ 성공 | order_type=MARKET, 고유 order_id |
| 7. Telegram 알림 | ✅ 성공 | 리밸런싱 완료 알림 KST 시간대 |
| 8. 미국 종목 장외 거부 | ✅ 정상 | fail-closed (장 마감 시간) |

### 10.3 최종 주문 DB 현황

```
 market |  status   | count
--------+-----------+-------
 KRX    | FAILED    |     3
 KRX    | SUBMITTED |     3
 NASDAQ | FAILED    |     2
 NYSE   | FAILED    |     4
```

- KRX SUBMITTED 3건: 한국 종목 KIS 모의투자 주문 정상 접수
- KRX FAILED 3건: KIS rate limit (EGW00133) 또는 모의투자 계좌 제한
- NYSE/NASDAQ FAILED 6건: 미국 장 마감 시간 (정상 동작)
- 총 12건/20건 처리: 나머지 8건은 KIS rate limit으로 2분 윈도우 내 미처리

### 10.4 발견 및 수정된 버그 (총 8건)

| # | 버그 | 근본 원인 | 수정 커밋 |
|---|------|-----------|-----------|
| 1 | AuthenticatedUser → user_id 타입 불일치 | RBAC 미들웨어가 NamedTuple 반환, DB는 str 기대 | 6d6a85c |
| 2 | 감사 로그/API 응답에 raw AuthenticatedUser 직렬화 | NamedTuple → JSON 배열 변환 | eb3b3d5 |
| 3 | InvestorProfileManager(db) 불필요 인자 전달 | 클래스에 __init__ 미정의 | 274f264 |
| 4 | Decimal → float 캐스팅 누락 | PostgreSQL NUMERIC → Python Decimal | 0eac1b4 |
| 5 | OrderExecutor/TelegramTransport 미주입 | Wiring Rule 위반 ("정의 ≠ 적용") | e580f8f |
| 6 | QuoteProvider 미주입 + Telegram UTC 시간대 | fail-closed 정책 + 시간대 누락 | b277b09 |
| 7 | market 하드코딩(NYSE) + order_type NULL | 단순화 주석 방치 + INSERT 컬럼 누락 | a2a74a3 |
| 8 | order_id UNIQUE 제약 위반 | 빈 문자열 중복 | f792cd4 |

### 10.5 E2E 추가 검증 (2026-04-14)

#### 10.5.1 POST_MARKET SQL 타입 버그 수정

| 항목 | 내용 |
|------|------|
| 증상 | POST_MARKET 핸들러에서 거래 내역 조회 실패: `'str' object has no attribute 'toordinal'` |
| 원인 | `DATE(created_at) = :today` 쿼리에 문자열 `'2026-04-14'`를 전달. asyncpg는 `$1`에 `datetime.date` 객체를 기대 |
| 영향 | 일일 리포트에 당일 체결 내역 누락 (리포트 자체는 생성됨) |
| 수정 | `today_str = ...strftime(...)` → `today_date = ...date()` (handle_market_close, handle_post_market 2곳) |

#### 10.5.2 리밸런싱 완료 알림 정확도 개선

| 항목 | 내용 |
|------|------|
| 증상 | 20건 전부 FAILED인데 `✅ 정기 리밸런싱 완료 — 주문 20건 체결`로 표시 |
| 원인 | `_send_rebalancing_completed_notification`이 `_execute_orders` 결과를 받지 않고 주문 수만 표시 |
| 수정 | `_handle_rebalancing_by_style`에서 `order_results`를 완료 알림에 전달, 아이콘/메시지를 실제 결과 반영 (✅전체체결/⚠️일부실패/❌전체실패) |
| 테스트 | `TestRebalancingCompletedNotification` 5건 추가 (전체실패, 전체성공, 일부실패, fallback, wiring 검증) |

#### 10.5.3 RetryError 로그 품질 수정

| 항목 | 내용 |
|------|------|
| 증상 | 리밸런싱 로그에 `RetryError[<Future at 0x... state=finished>]`로 원인 불명 |
| 원인 | `_execute_orders` except 블록에서 `str(e)` 사용, `_unwrap_retry_error` 미적용 |
| 수정 | `_unwrap_retry_error(e)` 결과를 로그에 사용하여 실제 KIS 에러 코드/메시지 표시 |

#### 10.5.4 스케줄러 이벤트 검증

| 이벤트 | 실행 시각 (UTC) | 상태 |
|--------|----------------|------|
| PRE_MARKET | ~01:xx | ✅ 실행 완료 |
| MARKET_OPEN | ~01:xx | ✅ 파이프라인 + 리밸런싱 실행 |
| MIDDAY_CHECK | ~03:xx | ✅ 포지션 모니터링 |
| MARKET_CLOSE | ~06:xx | ✅ 스냅샷 저장 |
| POST_MARKET | 07:00 | ✅ 일일 리포트 생성 + Telegram 발송 (1.1초) |
| 멱등성 복원 | 06:42 (재시작) | ✅ 5건 복원, 중복 실행 없음 |

#### 10.5.5 주문 실행 현황 분석 (2026-04-14)

| 상태 | 건수 | 비율 |
|------|------|------|
| SUBMITTED | 10 | 7% |
| FAILED | 130 | 93% |
| FILLED | 0 | 0% |

에러 유형 분포:

| 에러 유형 | 건수 | 설명 |
|-----------|------|------|
| 주문가능금액 부족 | 61 | DEMO 계좌 잔고 소진 후 |
| 500 Server Error | 48 | KIS DEMO 미국주식 API 불안정 |
| 모의투자 장종료 | 12 | KRX 장 마감 후 주문 시도 |
| TOKEN_UNAVAILABLE | 7 | EGW00133 (1분 1회 토큰 제한) |
| Quote fetch failed | 2 | 시세 조회 실패 |

### 10.6 후속 개선 사항 (E2E 경로 외)

1. ~~**리밸런싱 API 비동기화**: 현재 동기 처리로 20건 주문 시 nginx 504 발생.~~ → **§10.7에서 구현 완료**
2. **HighLatencyP95 WARNING 임계값 조정**: 리밸런싱 엔드포인트를 p95 계산에서 제외하거나, 비동기화 완료 후 자연 해소.
3. **KIS rate limit 대응**: 주문 간 적절한 간격 배치 또는 배치 주문 API 활용.
4. **미국 장 시간 사전 검증**: 장외 시간에는 미국 종목 주문을 사전 필터링하여 불필요한 API 호출 방지.
5. **[P0] 주문 체결 상태 폴링 로직 구현**: 현재 주문 제출 시점의 KIS 응답만으로 상태를 결정하며, 이후 체결 확인 재조회가 없음. SUBMITTED 상태에서 영구히 멈추는 주문이 발생하고, 실제 체결 여부를 DB에 반영할 수 없음. 실 거래 전환 전 필수 구현.
   - 방안 A: KIS 주문 체결 조회 API(`TTTC8001R` 등) 주기적 폴링 (POST_MARKET 또는 별도 스케줄러)
   - 방안 B: KIS WebSocket 체결 통보(`H0STCNI0`/`H0STCNI9`) 구독 → 실시간 상태 업데이트
   - 방안 C: A+B 병행 — 웹소켓 실시간 + 폴링 보정(fallback)

### 10.8 주문 체결 결과 분석 (2026-04-14)

#### 오늘 주문 현황

| 상태 | 건수 | 비고 |
|---|---|---|
| FAILED | 170 | 장외 시간 + DEMO 제약 |
| SUBMITTED | 10 | 장중 제출, 체결 확인 미수행으로 상태 미갱신 |
| FILLED | 0 | — |

#### FAILED 170건 에러 유형 분포

| 마켓 | 에러 유형 | 건수 |
|---|---|---|
| US | 500 Internal Server Error (KIS DEMO 미국주식 API) | 62 |
| KRX | 주문가능금액 부족 (DEMO 잔고 소진) | 61 |
| KRX | 모의투자 장종료 | 36 |
| US/KRX | TOKEN_UNAVAILABLE (1분 1회 토큰 제한) | 9 |
| US | Quote fetch failed | 2 |

#### SUBMITTED 10건 분석

| 종목 | 건수 | 시간대(KST) |
|---|---|---|
| 060310 | 3건 | 12:38~14:17 |
| 360750 | 3건 | 12:39~14:18 |
| 293490 | 4건 | 12:39~14:18 |

모두 KRX 장중에 제출되었으나, 체결 확인 폴링 로직이 없어 SUBMITTED 상태에서 영구 정체. 현재 시스템은 `OrderExecutor._execute_market_order()` 에서 KIS API 주문 응답의 `filled_qty` 를 일회성으로 파싱할 뿐, 이후 재조회를 수행하지 않음.

#### 구조적 문제: 체결 상태 갱신 경로 부재

현재 주문 상태 관리 아키텍처:

1. **동기식 일회성 확인만 존재**: 주문 제출 → KIS 응답 파싱 → SUBMITTED/FILLED 기록. 이후 재조회 없음.
2. **체결 폴링 로직 없음**: KIS 체결 조회 API 호출 코드 부재.
3. **WebSocket 체결 통보 미구현**: 실시간 시세만 수신, 주문 체결 피드백(H0STCNI0) 미구독.
4. **`_handle_unfilled()`**: 장마감 30분전 미체결 강제 재주문만 수행, 최종 상태 확정(FILLED/FAILED) 미보장.

→ **실 거래 전환 전 P0 필수 구현 항목으로 분류**

### 10.7 리밸런싱 API 비동기화 (2026-04-14)

#### 문제

`POST /api/system/rebalancing` 엔드포인트가 동기적으로 주문 20건을 실행하면서 총 소요 시간이 nginx `proxy_read_timeout`(60s)을 초과하여 504 Gateway Timeout이 반환되었다. 클라이언트는 빈 응답을 받아 `JSONDecodeError`가 발생하고, 실제 주문 실행 결과를 확인할 수 없었다.

#### 해결 방안: 비동기 태스크 분리 (202 Accepted 패턴)

API 호출을 두 단계로 분리한다:

1. **검증 단계 (동기, ~수백ms)**: 멱등성 체크, 분산 락 획득, 프로필 조회, 앙상블 시그널 조회, 포지션/유니버스 DB 조회. 실패 시 즉시 에러 응답 반환 + 락 해제.
2. **실행 단계 (비동기, 백그라운드)**: `asyncio.create_task()`로 주문 실행을 백그라운드에서 처리. API는 검증 완료 즉시 `202 Accepted` + `task_id`를 반환.

#### 변경 파일

| 파일 | 변경 내용 |
|---|---|
| `api/routes/system.py` | `trigger_rebalancing()` 비동기 리팩터링, `_run_rebalancing_background()` 헬퍼 추가, `_update_rebalancing_status()` Redis 상태 저장 함수 추가, `GET /rebalancing/status/{task_id}` 조회 엔드포인트 추가 |
| `tests/test_system_routes.py` | 성공 케이스 202 반환 검증, status 엔드포인트 테스트 3건, 락 해제 검증 1건 추가 (총 +4건) |

#### API 변경 사항

**POST /api/system/rebalancing** (변경)

- 이전: 동기 실행 → `200 OK` + 실행 결과
- 이후: 검증 후 즉시 `202 Accepted` + `task_id` 반환

```json
{
  "success": true,
  "data": {
    "type": "MANUAL",
    "status": "accepted",
    "task_id": "20260414_abc12345",
    "signal_count": 15
  },
  "message": "리밸런싱 요청 수락됨 (task_id=20260414_abc12345). GET /api/system/rebalancing/status/20260414_abc12345로 진행 상태를 조회하세요."
}
```

**GET /api/system/rebalancing/status/{task_id}** (신규)

- `accepted` → `running` → `completed` / `failed` 상태 전이
- Redis에 24시간 보존 (`REBALANCING_STATUS_TTL = 86400`)
- 404: 존재하지 않는 task_id

#### 락 관리 전략

- 검증 단계에서 실패하면 `background_started = False` 플래그에 의해 `finally` 블록에서 즉시 락 해제
- 백그라운드 태스크에 진입하면 태스크의 `finally` 블록이 락 해제를 책임
- 둘 다 실패해도 TTL(300s)에 의한 자동 만료로 데드락 방지

#### 테스트 (3943 passed)

| 테스트 | 검증 내용 |
|---|---|
| `test_trigger_rebalancing_success` | 202 반환, body.data.status == "accepted", create_task 호출 확인 |
| `test_trigger_rebalancing_idempotency_force_bypass` | force=True → 202 반환 |
| `test_get_rebalancing_status_found` | Redis에서 정상 조회 |
| `test_get_rebalancing_status_not_found` | 존재하지 않는 task_id → 404 |
| `test_get_rebalancing_status_redis_error` | Redis 오류 → 실패 응답 |
| `test_trigger_rebalancing_lock_released_on_validation_failure` | 프로필 없음 시 락 해제 확인 |

### 10.9 주문 체결 상태 폴링 시스템 구현 (2026-04-14)

#### 문제

§10.8에서 분석한 바와 같이, SUBMITTED 상태 주문 10건이 체결 여부가 갱신되지 않은 채 남아있었다. 원인은 두 가지이다:

1. **체결 조회 로직 부재**: 주문 제출 후 KIS 체결 조회 API(`TTTC8001R`/`VTTS3035R`)를 호출하여 체결 여부를 확인하는 로직이 없었다.
2. **WebSocket 체결 통보 미연동**: KIS WebSocket 체결 통보(`H0STCNI0`/`H0STCNI9`) 수신 시 DB 갱신 콜백이 없었다.

서버의 `ReconciliationRunnerMissing` 경고(스케줄러가 작동하지만 reconcile이 24h 동안 미실행)도 동일 원인이다.

#### 해결 방안: 방안 C (폴링 + WebSocket 병행)

##### 1단계: 폴링 기반 체결 조회 (본 커밋)

두 가지 실행 모드를 제공한다:

- **`poll_after_execution()`**: 주문 직후 비동기 태스크로 단기 폴링 (30초 간격 × 5회). `asyncio.create_task()`로 생성되어 백그라운드에서 동작한다.
- **`reconcile_all_submitted()`**: POST_MARKET 스케줄러(16:00 KST)에서 SUBMITTED 상태 전량 일괄 조회. 일일 리포트 생성 전에 실행되어 체결 내역 정확도를 보장한다.

##### 2단계: WebSocket 체결 통보 연동 (후속 커밋)

KIS WebSocket `H0STCNI0`(국내) / `H0STCNI9`(해외) 체결 통보 수신 시 DB 즉시 갱신. 폴링과 병행하여 이중 안전망 역할.

#### 변경 파일

| 파일 | 변경 내용 |
|---|---|
| `core/order_executor/settlement_poller.py` | **신규**. `_fetch_kis_ccld_records()` KIS 체결 조회, `_match_ccld_record()` 주문 매칭, `_parse_ccld_status()` 상태 파싱, `_update_order_status()` DB 갱신, `poll_after_execution()` 단기 폴링, `reconcile_all_submitted()` 일괄 조회 |
| `core/data_collector/kis_client.py` | `inquire_kr_daily_ccld()` 국내 체결 조회 API 추가 (TR: `VTTC8001R`/`TTTC8001R`), `inquire_us_ccld()` 해외 체결 조회 API 추가 (TR: `VTTS3035R`/`TTTS3035R`) |
| `core/order_executor/executor.py` | `_start_settlement_polling()` 메서드 추가. `_execute_market_order()` 및 `_execute_limit_order()` 완료 후 SUBMITTED 상태이면 폴링 태스크 생성 |
| `core/scheduler_handlers.py` | `handle_post_market()` §1.9에 `reconcile_all_submitted()` 호출 추가 (체결 내역 조회 전 실행) |
| `tests/test_settlement_poller.py` | **신규**. 20건 단위 테스트 — 상태 파싱 7건, 주문 매칭 4건, DB 갱신 3건, 폴링 루프 3건, reconcile 4건 |

#### 설계 근거

- **KIS API 특성**: 주문 제출 시점에 체결 정보가 완전히 반환되지 않음. 모의투자(DEMO)에서는 시장가 주문도 즉시 체결되지 않을 수 있음.
- **상태 머신 준수**: `order_state_machine.py`의 전이 규칙(`SUBMITTED→FILLED`, `SUBMITTED→PARTIAL`)을 검증한 후에만 DB 갱신.
- **Rate limit 최적화**: `reconcile_all_submitted()`는 마켓별로 KIS API를 한 번씩만 호출하여 rate limit 소비를 최소화.
- **Optimistic locking**: `WHERE status = :current_status` 조건으로 동시 갱신 충돌 방지 (WebSocket과 폴링이 동시에 같은 주문을 갱신하려는 경우).

#### 테스트 (4060 passed)

| 테스트 | 검증 내용 |
|---|---|
| `test_kr_fully_filled` | 국내주식 전량 체결 → FILLED 상태 + 수량/가격 정확 |
| `test_kr_partial_fill` | 국내주식 부분 체결 → PARTIAL 상태 |
| `test_kr_no_fill` | 국내주식 미체결 → SUBMITTED 유지 |
| `test_us_fully_filled` | 해외주식 전량 체결 → FILLED (FT_ 필드 파싱) |
| `test_us_partial_fill` | 해외주식 부분 체결 → PARTIAL |
| `test_empty_fields_fallback` | 빈 필드 → SUBMITTED + 에러 없음 |
| `test_match_by_order_id` | 주문번호 정확 매칭 |
| `test_match_by_ticker_fallback` | UUID 폴백 시 종목코드로 매칭 |
| `test_no_match` / `test_empty_records` | 매칭 실패 → None |
| `test_update_submitted_to_filled` | SUBMITTED→FILLED DB 갱신 + commit 호출 |
| `test_skip_terminal_state` | 이미 FILLED → 갱신 스킵 |
| `test_skip_same_submitted` | SUBMITTED→SUBMITTED 동일 상태 → 스킵 |
| `test_poll_stops_on_terminal_state` | DB에서 이미 종결 → KIS API 미호출 |
| `test_poll_updates_on_fill` | KIS 체결 확인 → DB 갱신 후 중단 |
| `test_poll_order_not_found_in_db` | DB 주문 미발견 → 폴링 중단 |
| `test_reconcile_no_submitted_orders` | SUBMITTED 없음 → 즉시 종료 |
| `test_reconcile_updates_filled_orders` | 체결 건 DB 갱신 |
| `test_reconcile_handles_processing_error` | 예외 시 errors 카운트 증가 |
| `test_reconcile_market_batch_optimization` | 같은 마켓 → API 1회 호출 |

### 10.10 시간대 통일 — UTC/KST 혼용 해소 (2026-04-14)

#### 문제

API 응답, Redis 키, 스케줄러 핸들러 결과에서 UTC와 KST가 혼용되어 사용자에게 혼란을 주었다. 특히 주문 체결 시간, 감사 로그, 포트폴리오 거래 이력 등이 UTC로 반환되어 한국 시간과 9시간 차이가 있었다. Redis 스냅샷 키(`portfolio:snapshot:YYYY-MM-DD`)도 UTC 날짜 기준이라 장중 날짜 불일치가 발생할 수 있었다.

#### 해결: KST 변환 유틸리티 단일 진입점

**시간대 정책**: DB 저장은 UTC 유지 (국제 표준), 사용자 노출(API 응답, Telegram, Redis 상태)은 KST 변환.

`core/utils/timezone.py` 모듈을 신규 생성하여 KST 변환 함수를 단일 진입점으로 제공한다:
- `KST`: `timezone(timedelta(hours=9))` 상수
- `to_kst(dt)`: datetime → KST 변환 (None/date 안전 처리)
- `to_kst_iso(dt)`: datetime → KST ISO 문자열 변환
- `now_kst()`: 현재 KST 시각
- `today_kst_str(fmt)`: KST 기준 오늘 날짜 문자열

#### 변경 범위

| 카테고리 | 파일 | 변경 내용 |
|---|---|---|
| 유틸리티 | `core/utils/timezone.py` | **신규**. KST 변환 함수 5개 |
| API 라우트 | `api/routes/system.py` | `updated_at`, 감사 로그 `time`, 분산 락 시간 → KST |
| API 라우트 | `api/routes/orders.py` | `filled_at` → `to_kst()` 변환 |
| API 라우트 | `api/routes/users.py` | `created_at`, `updated_at`, `last_login_at` → `to_kst_iso()` |
| API 라우트 | `api/routes/profile.py` | `created_at`, `updated_at` → `to_kst_iso()` |
| API 라우트 | `api/routes/market.py` | `fetched_at`, 경제지표 `date` → `to_kst_iso()` |
| API 라우트 | `api/routes/portfolio.py` | `updated_at`, 거래 이력 `date` → KST |
| API 라우트 | `api/routes/dry_run.py` | `started_at` → `to_kst_iso()` |
| 스케줄러 | `core/scheduler_handlers.py` | Redis 스냅샷 키, 핸들러 응답 시간 → KST |
| 중복 제거 | `core/daily_reporter.py` | 로컬 `KST` 정의 → `core.utils.timezone.KST` import |
| 중복 제거 | `core/trading_scheduler.py` | 동일 |
| 중복 제거 | `core/market_calendar.py` | 동일 |
| 중복 제거 | `core/scheduler_idempotency.py` | 동일 |
| 중복 제거 | `core/order_executor/settlement_poller.py` | 로컬 `kst` 변수 → `now_kst()` |
| 테스트 | `tests/test_timezone_utils.py` | **신규**. 17건 단위 테스트 |

#### 테스트 (4077 passed)
