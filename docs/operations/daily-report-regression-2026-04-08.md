# 일일 리포트 중복/0원 회귀 — 2026-04-08 POST_MARKET

## 0. 요약

2026-04-08 16:00 KST POST_MARKET 단계에서 `aqts-scheduler` 가 일일 텔레그램 리포트를
**동일 분(오후 4:00)에 두 번 송신**했고, 둘 다 비정상 평가값을 포함하고 있었다.

| 구분 | 첫 번째 리포트 | 두 번째 리포트 |
| --- | --- | --- |
| 시가 평가 | 0원 | 0원 |
| 종가 평가 | 0원 | 10,014,980원 |
| 일일 손익 | +0원 (0.00%) | +10,014,980원 (+0.00%) |
| 누적 손익 | -50,000,000원 (-100.00%) | -39,985,020원 (-79.97%) |
| 총 체결 | 0건 | 0건 |
| 보유 포지션 | 0건 | 1건 (삼성전자 1주) |
| 현금 잔고 | 0원 | 10,000,000원 |

이 회귀는 `a93fd8e fix(scheduler): 일일 리포트 중복/0원 회귀 — 멱등성 + 안전망 3종`
(2026-04-07 21:09 KST) 에서 이미 수정되어 있었던 동일 현상이다. 코드에는
수정이 병합되어 있었으나, **실제 운영 scheduler 컨테이너에 해당 코드가 도달하지
않은 상태**에서 POST_MARKET 이 실행되었다.

## 1. 관찰된 데이터

### 1.1 컨테이너 이미지 drift

현재(2026-04-09 기준) 운영 서버의 컨테이너 상태:

```
aqts-backend   : created 2026-04-09T02:18:50Z (11:18 KST)  image ghcr.io/sucheol9773/aqts-backend:latest
aqts-scheduler : created 2026-04-08T23:46:53Z (08:46 KST)  image ghcr.io/sucheol9773/aqts-backend:sha-d0f7212
```

핵심 사실:

1. 두 서비스는 `docker-compose.yml` 에서 동일한 이미지 템플릿
   `ghcr.io/${IMAGE_NAMESPACE}/aqts-backend:${IMAGE_TAG:-latest}` 을 참조한다.
2. 그럼에도 backend 는 `latest` 태그로, scheduler 는 `sha-d0f7212` 태그로 서로
   다른 digest 에 묶여 있다.
3. 2026-04-08 16:00 KST POST_MARKET 시점에는 **현재 돌고 있는 scheduler
   컨테이너(Apr 9 08:46 KST create)조차 존재하지 않았다**. 당시 POST_MARKET 을
   실행한 컨테이너는 이미 파기되어 직접 로그 추적이 불가능하지만, 그 컨테이너는
   정의상 Apr 9 08:46 KST 이전에 생성되었고 태그 역시 `sha-d0f7212` 이거나 혹은
   그보다 더 이전 sha 였다.
4. `a93fd8e` 는 2026-04-07 21:09 KST 커밋이고 `d0f7212` (2026-04-09 07:37 KST) 는
   그 이후 커밋이므로 `sha-d0f7212` 이미지 **자체에는** 가드가 포함되어 있다.
   즉 "고친 코드는 존재하지만 Apr 8 16:00 KST 에 돌던 컨테이너에는 들어가
   있지 않았다" 가 확정된 설명이다.

### 1.2 현재 scheduler 컨테이너의 unhealthy 상태 (별개이나 관련됨)

```
Status: unhealthy
FailingStreak: 597
Output: curl: (7) Failed to connect to localhost port 8000 after 0 ms
```

`docker-compose.yml` 의 `scheduler` 서비스가 `backend` 의 HTTP healthcheck
(`curl localhost:8000/health`) 를 그대로 상속받고 있으나, `scheduler_main.py`
는 FastAPI 프로세스가 아니라 `TradingScheduler` 만 구동하는 워커이다. 결과적으로
scheduler 컨테이너는 create 직후부터 healthcheck 가 계속 실패하며
`FailingStreak 597` 로 누적되어 있다.

healthcheck 가 형식적으로 실패해도 `restart: unless-stopped` 만 걸려 있는 현
구성에서는 컨테이너가 즉시 재시작되지는 않는다. 그러나 `depends_on:
condition: service_healthy` 를 거는 다른 서비스가 scheduler 에 의존하는 순간
블로킹되고, 운영자가 `docker compose ps` 로 건강 상태를 판단할 때 오진을
유발한다. 또한 이 잘못된 healthcheck 가 있는 상태에서는 CD 배포 시
`--wait` 옵션이 true 로 가면 배포가 영원히 timeout 될 수 있다.

### 1.3 Redis 멱등성/스냅샷 키 상태 (2026-04-09 관찰)

```text
KEYS scheduler:executed:*
  scheduler:executed:2026-04-09:PRE_MARKET
  scheduler:executed:2026-04-09:MARKET_OPEN
  scheduler:executed:2026-04-09:MIDDAY_CHECK

KEYS portfolio:snapshot:*
  portfolio:snapshot:2026-04-08
  portfolio:snapshot:2026-04-07

GET portfolio:snapshot:2026-04-08
  {"date": "2026-04-08",
   "portfolio_value": 10014980.0,
   "cash_balance": 10000000.0,
   "positions_count": 1,
   "positions": [
     {"ticker": "005930", "name": "삼성전자",
      "quantity": 1, "avg_price": 196000.0, "current_price": 211000.0,
      "eval_amount": 211000.0, "pnl_amount": 15000.0, "pnl_percent": 7.65}
   ],
   "timestamp": "2026-04-08T06:30:01.018576+00:00"}   # = 2026-04-08 15:30:01 KST

GET portfolio:snapshot:2026-04-07
  {"date": "2026-04-07",
   "portfolio_value": 0.0,
   "cash_balance": 0.0,
   "positions_count": 0,
   "positions": [],
   "timestamp": "2026-04-07T11:50:30.057096+00:00"}   # = 2026-04-07 20:50:30 KST
```

핵심 관찰:

1. **멱등성 키는 2026-04-09 것만 존재**. `2026-04-08:*` 항목이 **전혀 없다**. 이는
   2026-04-08 의 POST_MARKET/MARKET_CLOSE 등을 실행한 컨테이너가
   `mark_executed` 를 한 번도 호출한 적이 없다는 의미이다. 즉 해당 컨테이너는
   `a93fd8e` 의 `scheduler_idempotency` 모듈이 포함되지 않은 **이전 이미지**에서
   돌고 있었다. 이미지 drift 가설이 독립적 증거로 확정된다.
2. **`portfolio:snapshot:2026-04-08` 은 정상 데이터**이며 timestamp `15:30:01 KST`
   로 정확히 market_close 핸들러의 정규 실행 시점이다. 값도 Report 2 의
   수치와 정확히 일치한다 (portfolio 10,014,980 / cash 10,000,000 / 삼성전자
   1주). 즉 **`handle_market_close` 자체는 Apr 8 에 정상 동작했다** — 회귀는
   market_close 저장 측이 아니라 post_market 리포트 측에서 발생했다.
3. **`portfolio:snapshot:2026-04-07` 은 전부 0 인 오염 스냅샷**이고, timestamp
   가 `20:50:30 KST` 로 정규 market_close 시각(15:30)보다 5시간 20분이나
   늦다. 이는 정규 market_close 이후 다른 경로에서 0 스냅샷을 덧쓴 것으로,
   **정확히 `a93fd8e` 의 `snapshot_is_empty` 가드가 막으려던 패턴**이다.
   해당 가드가 이미 배포되어 있었다면 이 키는 존재하지 않거나 덧쓰이지
   않았을 것이다.

현재 scheduler 컨테이너 기동 로그에서는

```
load_executed_for_date(2026-04-09): 1건 복원 — ['PRE_MARKET']
```

이 관찰되어, 현재 배포된 `sha-d0f7212` 이미지의 `scheduler_idempotency` 모듈과
Redis wiring 은 **정상 동작 중**임이 확인되었다. 즉 회귀의 원인은 "코드가
잘못되었다" 가 아니라 "고친 코드가 Apr 8 운영 컨테이너까지 도달하지 않았다"
이다.

## 2. 근본 원인 (확정)

### 2.1 CD 파이프라인의 non-atomic 배포

`docker-compose.yml` 의 backend 와 scheduler 는 동일 이미지 템플릿을 공유하지만,
CD 파이프라인(`.github/workflows/cd.yml`)에서 두 서비스가 **동일한 디플로이
사이클 안에서 함께 재배포된다는 보장이 없다**. 관측된 digest drift
(`latest` vs `sha-d0f7212`) 가 이를 직접 증명한다.

non-atomic 배포의 구체적 실패 모드:

- backend 만 `docker compose up -d backend` 로 재배포되고 scheduler 는
  재시작되지 않는 경로가 존재한다.
- 혹은 scheduler 이미지 태그가 과거의 명시적 sha 로 고정된 상태에서
  `docker compose pull scheduler` 가 새 태그를 당기지 않는 경로가 존재한다.
- backend 의 `latest` 가 scheduler 의 `sha-d0f7212` 보다 최신이라는 사실은
  Apr 9 오전에 CD 가 돌면서 backend 는 갱신되었지만 scheduler 는 갱신되지
  않았음을 의미한다. Apr 8 16:00 KST 시점에는 이보다 더 이전 상태였다.

### 2.2 "고쳤다" 의 두 층위 — 회귀 본질

`a93fd8e` 가 병합된 것은 "코드가 수정되었다" 를 의미하고,
`sha-d0f7212` 이미지가 scheduler 컨테이너에 실제 create 된 것은 "고친 코드가
그 환경에서 실행된다" 를 의미한다. 이 둘이 단절되면 코드 수정은 운영상 효력이
없다. 이는 보안/정합성 로드맵의 "정의 ≠ 적용" (Wiring Rule) 사고 패턴이
**공급망-운영 경계**에서 반복된 형태이다. RBAC 에서 `require_*` 헬퍼 정의 ≠
라우트 적용이었던 것과 동일한 구조이다.

### 2.3 관찰된 두 리포트의 데이터 재구성 (확정)

Redis 실측값과 `handle_post_market` 의 read 경로를 직접 대조하면 두 리포트의
모든 수치가 다음 타임라인으로 정확히 설명된다.

**15:30 KST — market_close (정상)**

정규 `handle_market_close` 가 KIS 잔고를 정상 수신하여 Redis 에 저장:

```
portfolio:snapshot:2026-04-08 =
  {portfolio_value: 10014980.0, cash_balance: 10000000.0,
   positions: [{ticker: 005930, quantity: 1, ...}]}
```

**16:00 KST — POST_MARKET 1차 실행 → Report 1**

`handle_post_market` 진입. `redis.get("portfolio:snapshot:2026-04-08")` 이
어떤 이유로 `None` 을 반환했거나 예외를 던졌다 (Redis 순간 장애, 컨테이너 간
cache 일관성 지연, 혹은 이 시점에는 아직 key 가 flush 되지 않았을 가능성). 해당
try/except 블록은 `result["snapshot_error"]` 만 설정하고 초기 값을 그대로 둔다:

```python
portfolio_value_end = 0.0
cash_balance = 0.0
positions_data = []
portfolio_value_start = 0.0   # yesterday 조회도 예외 경로면 동일하게 0
```

**`a93fd8e` 의 `snapshot_missing_or_empty` 가드가 존재하지 않는 이전 코드**이므로
이 값 그대로 리포트가 생성되고 텔레그램으로 송신된다. 누적 = 0 − 50,000,000
(initial_capital_krw) = **-50,000,000 (-100.00%)**. Report 1 의 모든 필드와
정확히 일치한다.

**16:00 KST — POST_MARKET 2차 실행 → Report 2**

**Redis 멱등성 키가 없고(`a93fd8e` 이전), `events_executed_today` 는 in-memory
이므로** `_find_next_event` 가 같은 event window 안에서 POST_MARKET 을 다시
잡는다. 이번엔 Redis read 가 성공 (Apr 8 snapshot 정상 반환):

```python
portfolio_value_end = 10_014_980.0          # snapshot:2026-04-08 에서
cash_balance        = 10_000_000.0
positions_data      = [{ticker: 005930, quantity: 1, ...}]

# yesterday 조회:
yesterday_utc = "2026-04-07"                # (now_utc - 1d).strftime(%Y-%m-%d)
prev_snapshot = snapshot:2026-04-07         # ← 오염된 0 스냅샷
portfolio_value_start = 0.0                 # prev_snapshot.portfolio_value
```

계산:
- daily_pnl = 10,014,980 − 0 = **+10,014,980** ✓ Report 2 와 일치
- daily_return_pct: `(10,014,980 − 0) / 0` → div-by-zero 가드로 0.00% ✓
- 누적 손익 = 10,014,980 − 50,000,000 = **−39,985,020 (−79.97%)** ✓

모든 수치가 정확히 맞아떨어진다. 중복 송신은 "POST_MARKET 이 한 번 더 fired"
가 아니라 **"한 번의 POST_MARKET window 안에서 handler 가 두 번 호출되고,
두 호출이 서로 다른 실패 모드로 서로 다른 리포트를 생성한 것"** 이다.

### 2.4 `a93fd8e` 가 막았어야 할 것들과 실제 관찰의 매핑

| `a93fd8e` 방어층 | Apr 8 에서의 실패 | 결과 |
| --- | --- | --- |
| `handle_market_close` 의 `snapshot_is_empty` skip | Apr 7 20:50 에 이미 발동했어야 했으나 코드 부재로 0 스냅샷이 Redis 에 저장됨 | `snapshot:2026-04-07` 오염 |
| `handle_post_market` 의 `snapshot_missing_or_empty` skip | Apr 8 16:00 1차 호출에서 Redis read 실패로 0 default 로 떨어졌을 때 송신을 막았어야 했으나 코드 부재 | Report 1 (전부 0원) 송신 |
| `trading_scheduler._execute_event` 의 Redis `is_executed` pre-check | Apr 8 16:00 1차 성공 이후 2차 호출을 막았어야 했으나 코드 부재 | Report 2 중복 송신 |
| `scheduler_idempotency.mark_executed` 호출 | Apr 8 16:00 1차 성공 후 Redis 에 마킹했어야 했으나 호출 없음 | `scheduler:executed:2026-04-08:*` 키 0개 (관찰값과 일치) |

네 개 방어 경로 모두 **코드 레벨에서는 a93fd8e 에 존재**하지만 **운영 컨테이너에
해당 이미지가 배포되지 않았기 때문에** 작동하지 않았다.

## 3. 왜 Apr 7 커밋의 수정이 Apr 8 16:00 에 작동하지 않았는가

가능한 시나리오 두 가지 — 둘 다 동일한 구조적 결함의 다른 발현이다.

**시나리오 A**: Apr 7 21:09 KST 의 `a93fd8e` 커밋 푸시 이후 CI 는 성공했으나
CD 파이프라인이 scheduler 컨테이너까지 재배포하지 못했다 (backend 만 재배포 경로).
→ Apr 8 16:00 KST POST_MARKET 시점의 scheduler 는 `a93fd8e` 이전 이미지
(`sha-<pre-a93fd8e>`) 에 고정되어 있었다.

**시나리오 B**: Apr 8 16:00 KST 이전에 CD 가 전혀 돌지 않았다. `a93fd8e` 커밋이
단순히 repo 에 머문 채로 scheduler 는 그 이전 태그를 계속 사용하고 있었다.

두 시나리오 모두 공통 원인은 **CD 가 "backend 와 scheduler 를 같은 digest 로
묶어 함께 배포한다" 를 강제하지 않았다는 것**이다.

## 4. 재발 방지책

### 4.0 우선순위 P0 — 오염된 Apr 7 스냅샷 정리 (즉시)

현재 `portfolio:snapshot:2026-04-07` 에는 전부 0 인 오염 스냅샷이 30일 TTL 로
남아있다. 향후 어떤 post_market 호출에서 이 키를 yesterday 로 참조하면 또 다시
`portfolio_value_start = 0` 이 되어 잘못된 일일/누적 PnL 이 계산될 수 있다
(비록 현재 코드에서는 snapshot_missing_or_empty 가드가 end 값을 기준으로
동작하므로 리포트 송신 자체는 막히지만, `portfolio_value_start` 자체가 오염된
상태로 계산되는 경로는 여전히 남아있다).

운영자 즉시 조치:

```bash
cd ~/aqts
REDIS_PW=$(grep '^REDIS_PASSWORD=' .env | cut -d= -f2-)
# 오염 확인
docker compose exec -T redis redis-cli -a "$REDIS_PW" --no-auth-warning \
  GET 'portfolio:snapshot:2026-04-07'
# 삭제
docker compose exec -T redis redis-cli -a "$REDIS_PW" --no-auth-warning \
  DEL 'portfolio:snapshot:2026-04-07'
```

삭제 이후 `handle_post_market` 은 fallback 경로(`initial_capital_krw`)로
떨어진다. 본 절의 P1 코드 보강(§4.5)이 커밋/배포되기 전까지의 단기 완화책이다.

### 4.1 우선순위 P0 — CD atomic 배포 강제 (2026-04-09 반영)

`.github/workflows/cd.yml` 에 다음 세 층 방어를 적용했다 (커밋:
`fix(cd): atomic deploy 강제 — EXPECTED_IMAGE_ID + force-recreate + digest
assertion`).

**(1) EXPECTED_IMAGE_ID 잠금 (Step 4 직후)**

```bash
EXPECTED_IMAGE_ID=$(docker image inspect "${IMAGE_REF}" --format '{{.Id}}')
```

pull 직후 로컬 digest 를 잠가두어, 이후 단계의 모든 비교 기준점으로 사용한다.

**(2) `--force-recreate` 로 원자적 재생성 (Step 5d)**

```bash
docker compose -f docker-compose.yml up -d --force-recreate --no-deps backend scheduler
docker compose -f docker-compose.yml up -d
```

compose 가 "이미지 태그가 바뀌지 않았다" 고 판단하여 한쪽만 recreate 하거나
skip 하는 경로를 원천 차단한다. 두 컨테이너를 같은 명령 안에서 강제 교체한
뒤, 나머지 서비스는 일반 `up -d` 로 수렴시킨다.

**(3) 배포 직후 digest 어서트 (Step 5e)**

```bash
BACKEND_IMAGE_ID=$(docker inspect --format '{{.Image}}' aqts-backend)
SCHEDULER_IMAGE_ID=$(docker inspect --format '{{.Image}}' aqts-scheduler)
[ "${BACKEND_IMAGE_ID}"   = "${EXPECTED_IMAGE_ID}" ] || exit 1
[ "${SCHEDULER_IMAGE_ID}" = "${EXPECTED_IMAGE_ID}" ] || exit 1
```

둘 중 하나라도 불일치하면 즉시 exit 1 → rollback 경로로 진입.

**(4) Verify 단계 2중 체크 (Step 5e 와 독립)**

`Post-deploy verification` 단계에서 backend/scheduler digest 가 서로 일치하는지
다시 확인한다. Step 5e 이후 수동 개입이나 부분 재시작으로 drift 가 발생했는지
잡는 2중 방어선이다.

**(5) Rollback 경로 동일 적용**

롤백 스크립트에도 `EXPECTED_IMAGE_ID` 캡처 + `--force-recreate` + digest 어서트를
동일하게 적용하여 롤백 중에도 drift 가 재발하지 않도록 한다.

**회귀 테스트**: `backend/tests/test_cd_atomic_deploy.py` 가 `cd.yml` 을 정적
파싱하여 위 다섯 항목의 문자열 존재를 어서트한다. 누구든 실수로 `--force-recreate`
나 digest 비교를 제거하면 CI 가 즉시 실패한다. 이는 CLAUDE.md 의 RBAC Wiring
Rule("정의 ≠ 적용") 을 CD 도메인에 확장한 것이다.

### 4.2 우선순위 P0 — scheduler healthcheck 분리 (별도 커밋)

`docker-compose.yml` 의 scheduler 서비스에 backend 와 다른 healthcheck 를 명시:

- `scheduler_main.py` 가 주기적으로 Redis 에 heartbeat 키
  (`scheduler:heartbeat:<pid>`, TTL 2 × loop interval) 를 쓰고, healthcheck 는
  해당 키의 존재를 확인하는 방식이 가장 잘 맞는다. 현재 `TradingScheduler.start`
  의 이벤트 루프가 이미 일정 주기로 `_find_next_event` 를 돌리므로 그 루프에
  heartbeat 한 줄만 추가하면 된다.
- 임시 대안으로 `pgrep -f 'python.*scheduler_main.py'` 를 healthcheck 명령으로
  쓸 수도 있으나, 이는 "프로세스가 살아있다" 만 보장하고 "스케줄 루프가
  동작한다" 는 보장하지 않는다. heartbeat 방식을 권장한다.

### 4.3 우선순위 P1 — `handle_post_market` yesterday read 강화 (별도 커밋)

현재 `handle_post_market` 의 yesterday 처리:

```python
if prev_raw:
    prev_snapshot = json.loads(prev_raw)
    portfolio_value_start = prev_snapshot.get("portfolio_value", 0)
else:
    portfolio_value_start = get_settings().risk.initial_capital_krw
```

`prev_raw` 가 "존재하지만 값이 0" 인 경우(= Apr 7 시나리오) 초기자본 fallback
으로 가지 않고 0 을 그대로 사용한다. 보강:

```python
if prev_raw:
    prev_snapshot = json.loads(prev_raw)
    prev_value = prev_snapshot.get("portfolio_value", 0)
    if prev_value <= 0:
        logger.warning(
            f"[PostMarket] 전일({yesterday}) 스냅샷 portfolio_value={prev_value} — "
            "오염 추정, 초기자본 fallback 으로 대체"
        )
        portfolio_value_start = get_settings().risk.initial_capital_krw
    else:
        portfolio_value_start = prev_value
else:
    portfolio_value_start = get_settings().risk.initial_capital_krw
```

동시에 동일 원리를 end 값에도 적용: `portfolio_value_end` 가 0 이지만
`snapshot_raw is not None` 인 경로(저장된 스냅샷 자체가 0 인 경우) 는 이미
`snapshot_missing_or_empty` 가드가 포함한다. 다만 "read 예외 → default 0" 경로는
아래 §4.5 에서 명시적으로 구분한다.

### 4.4 우선순위 P1 — read 예외 경로 명시적 skip

현재 Redis read 실패 시

```python
except Exception as e:
    logger.warning(f"[PostMarket] 스냅샷 조회 실패: {e}")
    result["snapshot_error"] = str(e)
```

만 설정하고 계속 진행한다. 기본값 0 이 그대로 가드까지 흘러가기 때문에 가드가
우연히 차단하지만, **"Redis 장애로 0 이 된 것"** 과 **"실제로 빈 snapshot"** 은
의미가 다르다. 전자는 직후 재시도 가치가 있고 후자는 영구 skip 이 맞다.

```python
snapshot_read_failed = False
try:
    redis = RedisManager.get_client()
    ...
except Exception as e:
    logger.warning(f"[PostMarket] 스냅샷 조회 실패: {e}")
    result["snapshot_error"] = str(e)
    snapshot_read_failed = True

# 1.5. 안전망 확장
if snapshot_read_failed:
    result["report_skipped"] = True
    result["skip_reason"] = "snapshot_read_failed"
    return result
```

이 변경으로 Apr 8 Report 1 과 정확히 같은 실패 모드(Redis read 실패 → 0 default
→ 리포트 송신)가 **현재 코드에서도** 일어나지 않도록 명시적으로 차단된다.

### 4.5 우선순위 P1 — 배포 검증 smoke test

배포 직후 다음을 자동 검증:

- `docker compose exec scheduler python -c 'from core.scheduler_idempotency import is_executed; print("ok")'`
  로 import 경로 존재 확인 (코드 롤백 방지).
- scheduler 컨테이너 로그에서 `load_executed_for_date` 문자열이 60초 안에
  출력되는지 확인 (trading_scheduler.start 실행 확인).

### 4.4 우선순위 P2 — 운영 서버 `.env` 정리 (운영자 수동 작업)

- `IMAGE_TAG` 를 `.env` 에 명시 (예: `IMAGE_TAG=latest`).
- 사용되지 않는 legacy 환경변수 제거 (`DOCKER_IMAGE_NAME=aqts-quant-trading`).
- `DASHBOARD_PASSWORD=dks1()()` 에서 `()` 때문에 `source .env` 가 syntax error
  를 내는 문제 → 쌍따옴표로 감싸거나 `()` 를 이스케이프. docker-compose
  `env_file:` 은 정상 동작하지만 운영 쉘 수동 조회가 막힌다.

## 5. 이미 병합된 코드 수정과의 관계

`a93fd8e` 의 3-layer 방어는 그대로 유지한다. 본 문서가 제안하는 변경은 **해당
방어가 실제로 실행되는 환경에 도달하도록** 공급망-배포 층위의 보강을 추가하는
것이다. 애플리케이션 레이어의 가드/멱등성은 회귀가 없다.

## 6. 후속 조치 (체크리스트)

- [x] Redis 실측값 §1.3 에 채워 넣기 (2026-04-09 관찰 완료)
- [ ] (P0) 운영 서버에서 `portfolio:snapshot:2026-04-07` 오염 키 삭제 (수동, §4.0)
- [ ] (P0) `.github/workflows/cd.yml` atomic 재배포 + digest assertion 커밋
- [ ] (P0) `docker-compose.yml` scheduler healthcheck 분리 커밋 (+ heartbeat 구현)
- [ ] (P1) `handle_post_market` yesterday read 강화 커밋 (§4.3)
- [ ] (P1) `handle_post_market` read 예외 경로 명시적 skip 커밋 (§4.4)
- [ ] (P1) 배포 smoke test 스크립트 추가 (§4.5)
- [ ] (P2) 운영 서버 `.env` 정리 (수동 작업)

## 참고

- 수정 커밋: `a93fd8e fix(scheduler): 일일 리포트 중복/0원 회귀 — 멱등성 + 안전망 3종`
- 관련 문서: `docs/operations/scheduler-idempotency.md`
- 사고 패턴 참조: `docs/security/security-integrity-roadmap.md` "정의 ≠ 적용" 항목군
