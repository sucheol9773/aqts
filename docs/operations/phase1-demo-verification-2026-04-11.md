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

### 2-3. 경제지표 (FRED/ECOS) — ⚠️ 미수집

- `economic_indicators` 테이블 0건
- **원인**: FRED/ECOS API 키 미설정 (Phase 0에서 선택사항으로 분류)
- `EconomicCollectorService.collect_and_store()` 스케줄러 미연결

### 2-4. 환율 — ✅ DB 영속화 구현 완료

- `ExchangeRateManager._store_rate_to_db()` 메서드 추가 (TimescaleDB UPSERT)
- `get_current_rate(persist=True)` 파라미터로 DB 저장 제어
- `scheduler_main.py`에 1시간 간격 백그라운드 수집 루프 추가 (`_exchange_rate_loop`)
- Redis 캐시 + TimescaleDB 이중 영속화 구조
- 배포 후 `exchange_rates` 테이블에 데이터 적재 시작 예정

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

## 8. 미해결 항목

| 항목 | 우선순위 | 비고 |
|------|----------|------|
| 텔레그램 발송 검증 | P1 | 다음 거래일(04-13 월) MARKET_CLOSE 이후 확인 |
| 환율 수집 배포 검증 | P2 | DB 영속화 코드 완료, 배포 후 `exchange_rates` 테이블 데이터 확인 필요 |
| NewsCollector 자동 수집 검증 | P2 | 04-13(월) 08:30 KST handle_pre_market 실행 시 검증 |
| 경제지표 수집 (FRED/ECOS) | P3 | API 키 설정 후 수집 가능 |
