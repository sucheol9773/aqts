# 스키마-코드 불일치 수정 (2026-04-13)

## 개요

전체 22개 테이블의 Alembic 마이그레이션 스키마와 코드 내 61개 raw SQL 쿼리를
전수 비교하여 발견된 **HIGH 심각도 4건**의 불일치를 수정했다.

## 발견된 문제

| # | 파일 | 테이블 | 심각도 | 문제 |
|---|------|--------|--------|------|
| 1 | `financial_collector.py` | `financial_statements` | HIGH | INSERT/SELECT가 실제 DB에 없는 컬럼(`corp_code`, `corp_name`, `bsns_year`, `reprt_code`, `fs_div`, `collected_at`) 사용 |
| 2 | `financial_collector.py` | `company_info` | HIGH | 존재하지 않는 테이블 참조 (Alembic 마이그레이션 없음) |
| 3 | `emergency_monitor.py` | `positions` | HIGH | 존재하지 않는 테이블 참조 (실제 테이블: `portfolio_holdings`) |
| 4 | `rebalancing.py`, `emergency_monitor.py` | `rebalancing_history` | HIGH | 존재하지 않는 테이블 참조 (Alembic 마이그레이션 누락) |

## 수정 내용

### 1. financial_statements INSERT/SELECT 컬럼 매핑 수정

**변경 전**: DART API 필드를 그대로 DB 컬럼으로 사용
```sql
INSERT INTO financial_statements
(corp_code, ticker, corp_name, bsns_year, reprt_code, fs_div, ..., collected_at)
ON CONFLICT (corp_code, bsns_year, reprt_code, fs_div)
```

**변경 후**: DART → DB 매핑 레이어 추가
```sql
INSERT INTO financial_statements
(ticker, market, report_date, period_type, ..., accounting_standard)
ON CONFLICT (ticker, report_date, period_type)
```

매핑 규칙:
- `bsns_year` + `reprt_code` → `report_date` (date 타입, 예: 2023 + 11011 → 2023-12-31)
- `reprt_code` → `period_type` (11013→Q1, 11012→H1, 11014→Q3, 11011→FY)
- `fs_div` → `accounting_standard` (CFS→K-IFRS, OFS→K-GAAP)
- `market` → 기본값 'KRX' (DART 데이터는 국내)
- `collected_at` → 제거 (DB는 `created_at` DEFAULT NOW() 사용)

새 메서드: `_to_db_record(stmt: FinancialStatement) -> dict`

`get_factor_data` SELECT도 `ORDER BY bsns_year DESC, reprt_code DESC` → `ORDER BY report_date DESC`로 수정.

### 2. company_info → universe 테이블 폴백

**변경 전**: 존재하지 않는 `company_info` 테이블 쿼리 → ProgrammingError
**변경 후**: `universe` 테이블에서 ticker/name 조회 시도 + try/except 래핑

`corp_code`→`ticker` 직접 매핑은 불가 (universe에 corp_code 없음).
향후 corp_code↔ticker 매핑 테이블 도입 시 확장 가능하도록 구조화.

### 3. positions → portfolio_holdings 테이블 변경

**변경 전**: 존재하지 않는 `positions` 테이블 쿼리
```sql
SELECT ticker, market, quantity, avg_purchase_price, current_price, sector
FROM positions
```

**변경 후**: 실제 존재하는 `portfolio_holdings` 테이블 사용
```sql
SELECT ticker, market, quantity, avg_price, current_price
FROM portfolio_holdings
```

컬럼 매핑:
- `avg_purchase_price` → `avg_price`
- `sector` → 제거 (portfolio_holdings에 없음, 기본값 "" 사용)
- `current_price` NULL 대비: avg_price로 폴백

### 4. rebalancing_history 마이그레이션 추가

**신규 파일**: `alembic/versions/006_rebalancing_history.py`

`RebalancingEngine`(F-05-03)과 `EmergencyRebalancingMonitor`(F-05-04)가
INSERT/SELECT하는 테이블의 마이그레이션이 누락되어 있었다.

스키마:
```
rebalancing_history:
  id (serial PK)
  user_id (varchar 100, NOT NULL)
  rebalancing_type (varchar 20, NOT NULL)
  trigger_reason (text)
  orders (text, JSON-encoded)
  old_summary (text, JSON-encoded)
  new_summary (text, JSON-encoded)
  executed_at (timestamptz, DEFAULT NOW())
  INDEX: (user_id, rebalancing_type, executed_at)
```

## 새 테스트

- `TestReportCodeToDbMappings`: DART reprt_code → DB period_type/report_date 매핑 검증
- `TestToDbRecord`: `_to_db_record` 변환 로직 검증 (연간/분기/반기)

## 검증 결과

```
ruff check:    0 errors
black --check: 0 reformats
pytest:        4007 passed, 0 failed
check_doc_sync: 0 errors, 0 warnings
check_bool_literals: PASSED
```

## Silent Error 방지 추가 수정 (동일 커밋)

스키마 수정 과정에서 발견된 4건의 silent error 리스크를 동시에 수정했다.

### 1. `_to_db_record` — 알 수 없는 reprt_code 즉시 실패

**변경 전**: `.get(reprt_code, "FY")` 기본값 폴백 → 잘못된 데이터가 DB에 저장됨
**변경 후**: `.get(reprt_code)` + None이면 `ValueError` 발생 → 데이터 오염 차단

### 2. `_load_positions_from_db` — logger.debug → logger.error + RuntimeError

**변경 전**: DB 장애 시 `logger.debug` + `return []` → 비상 모니터링 무효화, 로그에서도 안 보임
**변경 후**:
- DB 실패 시 `logger.error` 출력 + `RuntimeError` 발생
- 호출자 `_fetch_current_positions`에서 RuntimeError 캐치 후 빈 리스트 반환
- `current_price` NULL 시 `logger.warning` 출력 (avg_price 폴백 사실 명시)
- `run_check`의 "No positions" 로그: `debug` → `info`

### 3. universe LIKE 쿼리 제거 — false positive 방지

**변경 전**: `WHERE ticker = :corp_code OR name LIKE '%' || :corp_code || '%'`
→ 부분 문자열 매칭으로 관계없는 종목이 반환될 수 있음
**변경 후**: `WHERE ticker = :corp_code` 정확 매칭만 수행

### 4. `_fetch_corp_info` — "데이터 없음" vs "DB 장애" 구분

**변경 전**: try/except → `logger.warning` → `return None` (두 경우 동일)
**변경 후**:
- 데이터 없음: `logger.warning` + `return None`
- DB 장애: `logger.error` + `raise RuntimeError` (호출자가 구분 가능)

### 5. 리밸런싱 DB 기록 실패 로그 승격

- `rebalancing.py::_record_rebalancing`: `logger.debug` → `logger.error`
- `emergency_monitor.py::_record_emergency_event`: `logger.debug` → `logger.error`
- `rebalancing.py::_get_last_rebalancing_time`: `logger.debug` → `logger.warning`

## 배포 시 주의사항

1. `alembic upgrade head` 실행 필요 (006_rebalancing_history 마이그레이션)
2. 기존 rebalancing/emergency 기록은 없으므로 데이터 마이그레이션 불필요
3. `financial_collector.py` 변경은 DART API 호출 흐름에만 영향 (Phase 1 데모에서는 미사용)

## 미해결 (MEDIUM)

- `user_profiles` 테이블: 코드에서 `user_id`, `seed_capital`, `investment_purpose` 사용하나 실제 DB에는 `investment_types`, `seed_amount`, `investment_goal` 등 다른 컬럼명
- `universe` 테이블: 코드에서 `market_cap`, `avg_daily_volume` 참조하나 실제 DB에 해당 컬럼 없음
