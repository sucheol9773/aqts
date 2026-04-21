# AQTS 개발 정책 (Development Policies)

> 본 문서는 Cowork 환경에서 운용되던 기존 `CLAUDE.md` (284줄) 의 **상세 규칙 원문을 이반(transfer) 한 단일 진실원천 (SSOT)** 입니다. Claude Code Agent Teams 전환 후 프로젝트 루트 `CLAUDE.md` 는 이 문서를 포함한 `agent_docs/` 참조 목록 + 최신 현황 + 남은 작업만 유지하며, 규칙 원문은 이 곳에만 둡니다. 규칙이 중복 정의되면 드리프트가 발생하므로, 신규 규칙은 반드시 이 문서에만 추가합니다.

---

## 0. 기본 원칙

모든 테스트 코드는 해당 기능에 실제로 기대하는 값에 해당하는 값만 통과를 해야하며 오류가 발생한 경우 단순 테스트 통과를 위한 기대값 수정은 절대 허용하지않습니다.
또한 모든 작업내용은 .md 파일에 정확히 작성해야합니다.
모든 코드는 black format 형식에 맞춰서 작성해야합니다.
코드를 수정할 때 항상 그와 연동되어 변화하는 코드에 대한 검증도 해야합니다.

## 1. 유닛테스트 작성 규칙

- 새로운 함수나 모듈을 작성할 때 반드시 유닛테스트를 함께 작성한다.
- 특히 multiprocessing, 외부 프로세스 실행, 직렬화(pickle) 등 런타임 환경에 의존하는 코드는 실제 실행 환경을 재현하는 테스트를 포함해야 한다 (예: Pool.map() 실제 호출).
- 기존 함수의 시그니처나 동작을 변경하는 경우, 관련 테스트가 존재하는지 확인하고 없으면 추가한다.
- 커밋 전에 반드시 전체 테스트를 실행하여 통과를 확인한다.
- 커밋 전에 반드시 `cd backend && python -m black --check . --config pyproject.toml`로 포맷을 확인한다.

### 1.1 AsyncMock vs MagicMock 선택 규칙

`unittest.mock.AsyncMock` 은 호출 시 coroutine 을 반환하는 mock 이다. Production 코드 경로에 있는 모든 메서드가 **실제로 async 일 때만** 사용한다. Sync 메서드(속성 접근, fluent pipeline, `asyncio.Task.done()` 같은 플래그 조회 등)를 AsyncMock 으로 두면, 호출 결과 coroutine 이 생성되지만 production 코드는 await 하지 않으므로:

1. `RuntimeWarning: coroutine '...AsyncMockMixin._execute_mock_call' was never awaited` 경고가 남는다.
2. 후속 로직이 coroutine 을 실제 반환값으로 다루려다 AttributeError 등이 발생하고, 광범위 `except Exception` 블록이 이를 삼켜 silent miss (§8) 로 이어진다.
3. 테스트는 "실패 경로" 가 아닌 "예외 삼킴 경로" 로 통과해 **기능 검증 자체가 무효** 가 된다.

선택 기준:

| 대상 | 실제 타입 | Mock 선택 |
|---|---|---|
| `httpx.Response.json()` / `.raise_for_status()` / `.status_code` / `.text` | sync | `MagicMock` |
| `httpx.AsyncClient.get()` / `.post()` | async | `AsyncMock` |
| `redis.asyncio.Pipeline.set()` / `.hset()` 등 명령 큐잉 | sync (fluent self 반환) | `MagicMock` |
| `redis.asyncio.Pipeline.execute()` | async | `AsyncMock` |
| `redis.asyncio.Redis.pipeline()` | sync | `MagicMock` |
| `asyncio.Task.done()` / `.cancel()` / `.cancelled()` | sync | `MagicMock` (실제 Task 사용 권장) |
| `asyncio.Task` 자체 (`await task` 지원) | awaitable | **실제 `asyncio.create_task(...)` 사용**. AsyncMock 은 `__await__` 를 구현하지 않아 `await task` 에서 `TypeError: object AsyncMock can't be used in 'await' expression` 이 발생한다. |
| SQLAlchemy async `session.execute()` / `commit()` / `close()` | async | `AsyncMock` |
| SQLAlchemy Result `.scalars().all()` 체이닝 | sync | `MagicMock` |

Response 같이 전부 sync 인 객체는 `MagicMock` 하나로 일원화한다. `asyncio.Task` 처럼 awaitable + sync 플래그 메서드가 혼재한 객체는 **mock 이 아닌 진짜 Task 를 만들어** `asyncio.create_task(_noop())` 로 사용한다. AsyncMock 을 쓰고 `done`/`cancel` 만 override 하는 패턴은 `await task` 경로에서 `TypeError` 가 나므로 금지.

회귀 사례 (PR #8, 2026-04-21): `mock_response = AsyncMock()` + `mock_response.json.return_value = {...}` 패턴이 `economic_collector.py:201` `data = response.json()` 와 만나 RuntimeWarning 3건 + silent miss 의심 경로를 만들었다. `mock_pipe = AsyncMock()` + `pipe.set(...)` 패턴이 `scheduler_handlers.py:947` 캐시 루프에서 동일 증상을 냈다. 수정은 Response/Pipeline 을 MagicMock 으로 일원화하는 것이었다.

회귀 사례 2 (PR #8 재수정, 2026-04-21): `tests/test_coverage_collectors_v2.py::test_disconnect` 에서 `client._receive_task = AsyncMock()` + `done = MagicMock(return_value=False)` 로 수정한 결과, production `disconnect()` 의 `await self._receive_task` 가 `TypeError: object AsyncMock can't be used in 'await' expression` 을 일으켰다. **동시에 발견한 원인**: 원래 테스트(`.done.return_value=False` 만 설정, `done` 자체는 AsyncMock 이라 호출 시 coroutine 반환)는 `not <coroutine>` 이 항상 False 로 평가돼 if-block 을 스킵, cancel+await 경로를 **한 번도 실행하지 못한** silent miss 였다. 올바른 수정은 `asyncio.create_task(asyncio.sleep(3600))` 로 실제 Task 를 만들어 cancel+await 경로를 실제로 검증하는 것. 이후 `task.cancelled()` 어서트로 경로 실행까지 확인한다.

## 2. 커밋 시 문서화 규칙

- 기능 추가, 변경, 버그 수정 등 **모든 커밋에는 관련 .md 파일 업데이트가 반드시 포함**되어야 한다.
- 나중에 몰아서 문서화하지 않고, 해당 커밋 시점에 즉시 작성한다.
- 새 기능: 설계 근거, 파라미터 설명, 기대 효과를 문서에 기록한다.
- 변경/수정: 변경 전후 비교, 변경 이유를 문서에 기록한다.
- OOS/백테스트 결과가 바뀌는 변경: 결과 비교 테이블을 분석 리포트에 추가한다.

## 3. 커밋 전 필수 검증 절차

커밋하기 전에 아래 두 명령을 **실제로 실행하고 결과를 확인**한 뒤에만 커밋한다. "통과할 것이다"라는 추측으로 커밋하지 않는다.

```bash
cd backend && python -m ruff check . --config pyproject.toml
cd backend && python -m black --check . --config pyproject.toml
cd backend && python -m pytest tests/ -q --tb=short
```

### 3.1 문서-only 커밋의 테스트 범위 예외

커밋에 **코드 변경이 전혀 없고 .md / .env.example / 문서성 yaml 주석 등 문서만 수정**되는 경우, 전체 `pytest tests/` 를 생략할 수 있다. 근거는 두 가지다.

1. 현재 pytest 러너는 파일 필터가 없어 수집·임포트 단계부터 전 모듈을 로드하므로, 코드 zero-diff 커밋에서 총 소요 시간(수 분대)을 그대로 지불하는 것이 실질 검증 가치가 0 이다.
2. 문서 변경으로 인해 실패할 수 있는 테스트(예: `check_doc_sync.py`, 링크 체크, FEATURE_STATUS 카운트 검증 등)는 별도로 존재하며, 해당 테스트만 실행하면 충분하다.

대신 **다음 최소 게이트는 문서-only 커밋이라도 반드시 실행**한다:

```bash
cd backend && python -m ruff check . --config pyproject.toml       # .py 영향 zero 확인
cd backend && python -m black --check . --config pyproject.toml    # .py 영향 zero 확인
python scripts/check_bool_literals.py                              # .env.example 수정 시 필수
python scripts/check_doc_sync.py --verbose                         # 존재하는 경우 필수
# 해당 문서에 직접 연관된 테스트가 있으면 그 파일만 실행
# 예: pytest tests/test_doc_sync.py
```

"코드 변경이 전혀 없음"의 판정은 `git diff --stat` 에 `.py`/`.toml`/`.sh`/`Dockerfile*`/`.github/workflows/*.yml` 같은 실행 경로 파일이 단 하나도 포함되지 않는 경우로 한정한다. 실행 경로 파일이 **한 줄이라도 섞여 있으면** 전체 pytest 를 실행한다.

**예외**: `.yml` 파일 중 `docker-compose.yml`의 변경이 Python 코드와 테스트에 구조적으로 영향을 줄 수 없는 경우(예: `logging` 설정만 추가, 주석만 수정, 환경변수 기본값 변경 등)에는 전체 pytest 를 생략할 수 있다. 판단 기준: 변경된 `.yml` 섹션이 Python import/실행 경로(이미지 태그, command, environment 중 코드가 읽는 변수)를 건드리는지 여부. 건드리지 않으면 문서-only 커밋과 동일한 최소 게이트만 실행한다.

### 3.2 pytest 타임아웃 설정 원칙

전체 pytest 를 실행할 때는 `timeout` wrapper 의 값을 **최근 관측된 러너 시간보다 최소 1.5배 이상**으로 설정한다. 직전 관측 기준선(2026-04-09): 3667 passed 기준 약 349s, 따라서 최소 540s 를 권장한다. 러너 시간은 테스트 수 증가에 따라 변동하므로, 타임아웃에 걸리면 먼저 실제 러너 시간을 관측한 뒤 다음 실행의 타임아웃 값을 조정한다. "일단 짧게 주고 실패하면 늘린다" 패턴은 불필요한 재실행 비용이다.

### 3.3 로컬 정적 검사기 버전 = CI pin

`.github/workflows/ci.yml` 에서 린터/포매터를 `black==X.Y.Z`, `ruff==A.B.C` 처럼 정확 버전으로 pin 한 경우, 로컬 검증도 반드시 같은 버전으로 수행한다. 로컬이 신년도 버전이면 `black --check` 가 로컬에서 통과해도 CI 에서 `would reformat` 으로 실패하는 시간차 회귀가 발생한다.

커밋 전 확인 절차 (권장 셸):

```bash
BLACK_PIN=$(grep -oE 'black==[0-9.]+' .github/workflows/ci.yml | head -1 | cut -d= -f3-)
RUFF_PIN=$(grep -oE 'ruff==[0-9.]+' .github/workflows/ci.yml | head -1 | cut -d= -f3-)
python -m pip install "black==${BLACK_PIN}" "ruff==${RUFF_PIN}" --quiet
cd backend && python -m black --check . --config pyproject.toml
cd backend && python -m ruff check . --config pyproject.toml
```

#### 회귀 사례 (PR #6, 2026-04-21)

로컬 `black==26.3.1` 로 포맷한 26 파일이 CI `black==24.4.2` 런너에서 `would reformat` 26건으로 실패. triple-quoted 인자 래핑 스타일이 24 ↔ 25+ 사이에서 바뀐 결과. 재포맷(`bfb5ce0`) 후 CI 3개 체크(Lint & Format / Smoke Tests / Full Test Suite) 모두 녹색 복구. 근본 원인은 "로컬 black == CI pin" 확인 절차 부재.

## 4. 설정값 일관성 규칙

임계값, 상수, 설정값을 수정할 때는 반드시 해당 값이 사용되는 **모든 위치**를 확인하고 동시에 수정한다.

- `operational_thresholds.yaml`과 코드 내 `DEFAULT_THRESHOLDS` (또는 유사한 기본값 딕셔너리)는 항상 동일한 값을 유지해야 한다.
- 설정값을 변경할 때는 `grep` 등으로 해당 값이 참조되는 모든 파일을 검색하여 누락 없이 수정한다.
- yaml 설정이 코드 기본값을 override하는 구조에서는, yaml 수정을 빠뜨리면 코드 변경이 무효화되므로 특히 주의한다.
- 테스트 코드의 입력값도 변경된 임계값에 맞게 조정한다 (단, 기대값 자체를 바꾸는 것이 아니라 임계값을 초과/미달하는 테스트 입력값을 조정하는 것).

## 5. 설정-전달 일관성 규칙 (Wiring Rule)

STRATEGY_RISK_PRESETS(또는 유사 설정 딕셔너리)에 새 키를 추가할 때는 반드시 해당 값을 **실제로 사용하는 코드까지 전달 경로를 확인**한다.

- 프리셋 dict에 키 추가 → config 객체 생성부에서 해당 파라미터 전달 확인
- 유닛테스트만으로는 wiring 검증 불가 (엔진은 독립 동작) → **통합 테스트** 또는 로그로 활성화 확인 필수
- 커밋 전 체크: 새로 추가한 설정값이 실행 시 로그에 출력되는지, 실제 동작에 영향을 미치는지 확인
- 예시: `dd_cushion_start`를 프리셋에 추가했으면, `BacktestConfig(dd_cushion_start=...)` 전달 확인

## 6. 현재 환경 기준 코드 작성 규칙

이미 확인된 실행 환경(서버, 라이브러리 버전 등)에 대해 코드를 작성할 때는 **현재 환경에 맞는 코드만 작성**한다.

- 사용하지 않는 이전 버전에 대한 호환 코드를 "혹시 모르니까"라는 이유로 추가하지 않는다.
- 불필요한 호환 코드는 가독성을 해치고 실행 시간을 늘린다.
- 예시: 서버가 Docker Compose v2를 사용한다면, v1의 `"Up"` 포맷까지 커버하는 코드를 작성하지 않는다. `"running"`만 잡으면 된다.
- 다중 버전 지원이 실제로 필요한 경우에만 호환 코드를 추가하되, 그 이유를 주석으로 명시한다.

## 7. 오류 수정 시 관찰 우선 원칙

오류가 발생했을 때 **추측으로 코드를 수정하지 않는다**. 반드시 실제 데이터를 관찰한 뒤에 수정한다.

- 에러 로그를 볼 때, 어떤 step/함수에서 실패했는지 **정확히 특정**한 뒤에 수정한다. "아마 이쪽일 것이다"로 수정하지 않는다.
- 서버/외부 환경의 실제 출력값을 모르면, 사용자에게 확인을 요청한다. 추측으로 값을 가정하지 않는다.
  - 예시: `docker compose ps`의 출력이 `"Up"`인지 `"running"`인지 모르면, 서버에서 직접 실행 결과를 확인한 뒤에 grep 패턴을 정한다.
- 원인이 불확실한 경우, **디버깅 코드(로그 출력, exit code 캡처 등)를 먼저 추가**하여 원인을 확인한 뒤 수정한다.
- 한 번의 수정으로 해결되지 않으면, 다음 수정 전에 반드시 새로운 데이터(로그, 실행 결과)를 수집한다. 같은 추측을 반복하지 않는다.
- **추론 ≠ 확정**: 추론을 통해 원인 후보를 좁히고 점검 대상을 정하는 것은 권장되지만, **추론만으로 원인을 확정 짓고 수정 코드를 작성/커밋하면 절대 안 된다**. 추론으로 도출한 가설은 반드시 다음 중 하나의 방식으로 검증한 뒤에 수정한다:
  1. 실제 로그/출력을 직접 관찰
  2. 디버깅 코드(로그/exit code/print)를 먼저 추가하여 가설을 검증
  3. 사용자에게 실제 출력값을 요청
  4. 공식 문서나 소스 코드를 통해 동작을 명시적으로 확인
- 문서/규칙에 적힌 일반화된 원칙(예: "compose v2 는 running")을 다른 맥락(다른 명령, 다른 필드)에 자동으로 일반화하여 적용하지 않는다. 그 원칙이 만들어진 정확한 맥락(어떤 명령의 어떤 필드인가)까지 같이 검증한 뒤에만 적용한다.
- 회귀 사례 (CD #91): `docker compose ps --format '{{.Status}}'` 의 grep 패턴을 "Up" → "running" 으로 바꿨다가 회귀. 실제로는 `{{.Status}}` 필드가 v2 에서도 "Up X seconds" 형식을 반환함. CLAUDE.md 의 "running" 규칙은 `--format json` 의 `.State` 필드 맥락이었는데 `{{.Status}}` 에 그대로 일반화한 결과. 실제 명령을 한 번도 실행해보지 않고 추론만으로 패턴을 정한 것이 직접 원인.
- bug fix 커밋에 무관한 "이왕 고치는 김에" 식 변경을 끼워넣지 않는다. 한 가지 원인만 수정하고, 다른 개선은 별도 커밋으로 분리한다. 무관한 변경을 끼워넣으면 회귀 발생 시 책임 범위가 흐려지고 검증 범위도 흐려진다.

## 8. 코드 수정 시 Silence Error 의심 원칙

코드를 수정할 때, 수정 전에는 실패하던 것이 수정 후 **에러 없이 조용히 무시**되는 경로가 생기지 않았는지 반드시 의심한다. 에러가 사라진 것이 아니라 **은폐된 것**일 수 있다.

### 8.1 핵심 질문

코드 변경 후 반드시 다음 두 질문을 던진다:

1. **"이 변경으로 기존에 실패하던 경로가 조용히 성공하는 것처럼 보이게 되지 않았는가?"**
2. **"에러가 사라진 이유가 '문제 해결'인가, 아니면 '문제를 만나기 전에 다른 경로로 빠져나감'인가?"**

### 8.2 대표 패턴

- **키/식별자 불일치 (silent miss)**: Redis/DB 키를 계산하는 로직을 변경하면, 기존 데이터를 조회할 때 키가 달라져서 `None` 을 반환하고, 코드가 `None` 을 "데이터 없음"으로 정상 처리한다. 에러는 발생하지 않지만 실제로는 데이터가 존재하는데도 없는 것처럼 동작한다.
- **try/except swallow**: 광범위한 `except Exception` 블록 안에서 변경된 코드가 새로운 종류의 예외를 발생시키지만, 기존 except 가 이를 삼켜서 warning 로그만 남기고 넘어간다.
- **조건 분기 우회**: 변경된 값이 기존 조건문의 기대 범위를 벗어나서 early return / skip 분기로 빠진다. 기능은 "실행되지 않은 것"이지 "실패한 것"이 아니므로 에러가 나지 않는다.
- **타입/포맷 불일치**: 날짜 포맷, 타임존, 문자열 인코딩 등을 변경하면 비교/매칭 로직에서 항상 불일치(`!=`, `not in`)가 되어 "해당 없음" 분기로 빠진다.
- **출력 채널 버퍼링 silent miss**: 프로세스는 정상 동작하고 내부적으로 로그를 생산하지만, stdout 버퍼링 / 비활성 sink / 수집기 설정 오류로 인해 관측 레이어(docker logs / Loki / Fluentd) 에 도달하지 못한다. healthcheck 가 "기능 작동" 을 판정하지 못하고 "프로세스 fd 정상" 수준만 판정하면, 외부에서는 정상으로 보이면서 내부 wiring 결손이 관측되지 않는다. 회귀 사례: scheduler stdout block-buffering (2026-04-15, `docs/operations/phase1-demo-verification-2026-04-11.md §10.14`). `PYTHONUNBUFFERED` 미설정으로 4KB block buffer 가 채워지지 않아 49 분간 `docker compose logs scheduler` 가 0 bytes 를 반환했다. 수정: compose `environment:` 에 `PYTHONUNBUFFERED: "1"` 추가.
- **로그 포맷 라이브러리 mismatch silent miss**: loguru 의 `logger.info("...%d...", n)` 처럼 stdlib `logging` 스타일 `%` posarg 포맷을 loguru 에 사용하면, loguru 는 이를 해석하지 않고 **메시지를 literal 로 기록하고 posargs 를 조용히 버린다**. 런타임 에러가 없고, 테스트가 메시지 문자열을 정확 일치로 검증하지 않으면 CI 도 통과한다. critical/kill-switch 경로(TradingGuard 차단 / price-guard 초과 / ledger refuse / reconcile mismatch / audit fail-closed) 에서 발화하면 **진단 정보 전량이 관측 레이어에서 손실**된다. 회귀 사례: 10.15 에서 10건 수정, 10.16 에서 추가 5건 발견 (regex 검사기 커버리지 결손). 정적 방어선은 반드시 AST 기반으로 구현해야 한다 — regex 는 메시지 문자열의 괄호/이스케이프/멀티라인/다양한 포맷 지시자(`%d`/`%s`/`%f`/`%x` …)를 전수 커버하지 못한다. 상세: `docs/operations/phase1-demo-verification-2026-04-11.md §10.15`, `§10.16`.
- **정적 방어선 커버리지 결손 (검사기 정의 ≠ 전수 적용)**: 정적 검사기를 추가해 CI 에 통합했더라도, 검사기의 구현 수단이 방어 대상을 전수 커버하지 못하면 결손은 잠복한다. 이는 RBAC Wiring Rule / 공급망 Wiring Rule / 알림 파이프라인 Wiring Rule 의 정적 방어선 도메인 확장이다 — "검사기를 만들었다 ≠ 모든 위반을 잡는다". 문자열 내용의 의미 판정 같은 작업은 regex 의 한계 밖이며, 반드시 파서(AST) 로 구현해야 한다. 회귀 사례: `check_loguru_style.py` 의 regex 가 메시지 내부 괄호로 매치가 끊겨 `backend/main.py:207` 을 놓친 사례 (2026-04-15, `docs/operations/phase1-demo-verification-2026-04-11.md §10.16`). **정적 검사기 추가 시 필수 점검**: 검사기가 타겟팅하는 패턴의 edge case (문자열 내부 특수문자, 멀티라인, 주석, 이스케이프, 다양한 포맷 변종) 를 최소 4~5개 준비해 검사기 자체에 대한 회귀 테스트로 고정한다. `backend/tests/test_check_*.py` 형태의 검사기 테스트는 RBAC/공급망/알림 정적 방어선 모두에 동일하게 요구된다.
- **설정 파일 경로 이동 시 상대 경로 resolver silent miss**: config 파일의 저장 위치를 변경하면, 해당 config 내부의 상대 경로 참조(예: Prometheus `rule_files`, Alertmanager `templates`, nginx `include`, Python `sys.path` 기반 import) 는 **새 위치 기준**으로 resolve 된다. 기존 위치 기준으로 작성된 상대 경로는 새 위치에서 존재하지 않는 경로를 가리키게 되고, 많은 loader 는 **빈 glob / 파일 부재를 에러로 처리하지 않고 조용히 0개 로드**한다. 결과적으로 "config 로드 성공 + 구성 항목 0개 + 에러 로그 없음" 의 완벽한 은폐 상태가 된다. **필수 점검**: config 파일 경로를 이동하거나 entrypoint 렌더링 파이프라인을 도입할 때, 해당 config 안에 상대 경로 키(`rule_files`, `include`, `templates`, `import`, `source`, 상대 URL 등) 가 있는지 grep 으로 전수 확인하고, 있으면 **절대 경로로 고정**하거나 새 위치 기준으로 재작성한다. 회귀 사례 (2026-04-16): `prometheus.yml` 을 `/etc/prometheus/` → `/tmp/` 로 옮기면서 `rule_files: ["rules/*.yml"]` 이 `/tmp/rules/*.yml` 로 resolve 되어 9개 알림 그룹 39개 rule 전체가 로드되지 않은 채 동작. 수정: 절대 경로 `/etc/prometheus/rules/*.yml`. 상세: `docs/operations/cd-auto-prune-2026-04-16.md §4.1`.
- **bind-mount 파일 내용 변경을 compose change-detection 이 감지하지 못하는 CD silence miss**: `docker compose up -d` 는 service definition (image digest, env, command, volume definition, network) 이 변경될 때만 컨테이너를 recreate 한다. bind-mount 된 파일의 **내용** 변경은 compose 의 change-detection 입력이 아니므로 "변경 없음" 으로 판단하여 기존 컨테이너를 그대로 둔다. 해당 파일만 수정된 배포는 디스크에는 새 내용이 반영되지만 **프로세스 메모리의 구 config 가 그대로 유지**되며, 외부 관측점이 없으면 CD 는 성공 로그를 남기고 결손이 은폐된다. **필수 대응**: bind-mount 된 config 파일만 수정하는 배포 경로가 있다면, CD 파이프라인이 해당 파일 변경 여부를 `git diff` 로 산출하여 **조건부 restart** 를 실행하고, restart 후 실제 로드 상태를 외부 API 로 어서트한다 (예: Prometheus `/api/v1/rules` groups≥1). SIGHUP reload 만으로는 entrypoint 렌더링 파이프라인(sed 치환 등) 이 재실행되지 않으므로 `restart` 를 기본으로 한다. 회귀 사례 (2026-04-16): `prometheus.yml.tmpl` 만 수정한 커밋이 배포된 후 `Up About an hour` 로 기존 컨테이너가 유지되어 새 템플릿이 프로세스에 반영되지 않았고, §4.1 수정 자체가 ~1시간 동안 무효 상태. 수정: `.github/workflows/cd.yml` 에 조건부 restart + groups≥1 어서트 스텝. 상세: `docs/operations/cd-auto-prune-2026-04-16.md §4.2`.

### 8.3 회귀 사례 (KST 통일, 2026-04-15)

`scheduler_handlers.py`의 Redis 스냅샷 키를 `datetime.now(timezone.utc).strftime("%Y-%m-%d")` → `today_kst_str()`로 변경했을 때, **테스트 fixture의 키는 UTC를 그대로 사용**. UTC 00:00~08:59 시간대에서 코드는 KST 날짜(+1일)로 조회하고, 테스트는 UTC 날짜로 데이터를 준비하여 키 불일치 발생. 코드는 `snapshot_raw is None` → "스냅샷 부재" → `report_skipped=True`로 정상 처리했고, 에러는 전혀 발생하지 않았다. 12건의 테스트가 실패했지만, **실패 원인이 "기능 오류"가 아닌 "데이터 부재 시 정상 skip" 경로**여서 발견이 지연될 수 있었다.

### 8.4 필수 점검 절차

코드 변경 시 다음을 점검한다:

- **키/식별자 변경**: 해당 키를 생성하는 곳과 조회하는 곳이 **모두** 동일하게 변경되었는지 grep 으로 전수 확인. 특히 테스트 fixture 의 키도 포함.
- **포맷/타임존 변경**: 해당 포맷을 사용하는 비교/매칭 로직이 변경 후에도 정상 매칭되는지 확인. "None 반환 후 skip" 경로가 아닌 "데이터 존재 후 처리" 경로를 테스트가 실제로 커버하는지 확인.
- **except 블록 범위**: 변경된 코드 주변의 try/except 가 새로운 실패를 삼킬 수 있는지 확인. warning 로그가 남더라도 **기능이 실행되지 않은 것**과 **기능이 정상 완료된 것**은 다르다.
- **테스트가 "성공 경로"를 커버하는지**: 테스트가 "데이터가 없을 때 skip" 만 검증하고 "데이터가 있을 때 처리" 를 검증하지 않는다면, silent miss 를 잡을 수 없다.

## 9. CI/CD 검증 결과 전수 처리 원칙

CI/CD 검증(doc-sync, ruff, black, pytest 등)에서 발견된 **모든 문제는 error든 warning이든 즉시 수정**한다.

- "기존부터 있던 warning이라 이번 커밋 범위가 아니다"라는 판단으로 넘어가지 않는다. 발견한 시점이 수정할 시점이다.
- warning을 무시하고 커밋하면, 다음 사람도 같은 이유로 무시하게 되어 warning이 영구적으로 방치된다.
- 커밋 전 검증 절차에서 warning이 출력되면, 해당 원인을 파악하고 수정한 뒤 0 errors + 0 warnings 상태에서만 커밋한다.
- 예시: `check_doc_sync.py --verbose`에서 TEST_COUNT warning이 나오면, FEATURE_STATUS.md의 테스트 수를 실제 값과 맞춘 뒤 커밋한다.

## 10. 상태 전이 로직 검증 규칙

risk-off/cooldown, 회복 조건 등 상태(state)가 전환되는 로직을 작성할 때는 반드시 다음 edge case를 코드 리뷰 단계에서 검토한다:

- 특정 상태에 진입한 후 빠져나올 수 있는 경로가 수학적으로 존재하는지 (예: 현금 100% 상태에서 고점 회복이 가능한가?)
- 상태 전환 조건이 순환하거나 교착(deadlock)되지 않는지
- 각 상태에서의 거래 횟수가 기대 범위 내인지

## 11. 환경변수 Boolean 표기 표준 규칙

- Python 코드 내부는 `True`/`False` 만 사용한다.
- 환경변수/`docker-compose*.yml`/`.github/workflows/*.yml`/`.env*` 의 bool 표기는 **소문자 `"true"`/`"false"`** 만 표준이다.
- 환경변수 → bool 변환은 반드시 `core.utils.env.env_bool()` 단일 진입점만 사용한다. `os.environ.get(...) == "true"`, `.lower() in (...)` 같은 ad-hoc 파싱은 금지.
- Phase 1 (현재): 하위호환으로 `1/0/yes/no/on/off` 도 허용되지만 경고 1회 + Prometheus counter `aqts_env_bool_nonstandard_total` 가 증가한다.
- Phase 2 (예정): `AQTS_STRICT_BOOL=true` 또는 호출 시 `strict=True` 면 비표준 표기는 `ValueError` 로 승격된다. 승격 조건: 14일 관찰 + CI/운영 비표준 0건 + 정적 검사 0 errors.
- 커밋 전 `python scripts/check_bool_literals.py` 가 0 errors 인지 확인한다 (Doc Sync 워크플로에서도 자동 실행).
- 새로운 bool 환경변수를 추가할 때는 `scripts/check_bool_literals.py::BOOL_ENV_KEYS` 화이트리스트에 등록하고, `docs/conventions/boolean-config.md` 에 사용 예를 추가한다.
- 자세한 정책: `docs/conventions/boolean-config.md`.

## 12. 인증(authn) ≠ 인가(authz) 분리 원칙 (RBAC Wiring Rule)

새 라우트를 추가하거나 기존 라우트를 수정할 때, **`get_current_user` 의존성만으로는 충분하지 않다**. `get_current_user` 는 "누구냐"(authn)만 검증하고 "무엇을 할 수 있냐"(authz)는 검증하지 않는다.

- 모든 mutation 라우트(`@router.post|put|patch|delete`)에는 반드시 `require_operator` 또는 `require_admin` 의존성을 명시한다.
- 모든 read 라우트(`@router.get`)에는 `require_viewer` (또는 더 엄격한 가드)를 명시한다.
- `Depends(get_current_user)` 를 라우트 핸들러에서 직접 쓰는 것은 `auth.py` 의 `/me`, `/refresh`, `/logout`, `/mfa/*` 같은 자기 세션 관리 엔드포인트에 한정한다.
- RBAC 헬퍼(`require_*`)를 정의했다는 것과 실제 라우트에 적용했다는 것은 다른 문제다 — 정의 ≠ 적용. Wiring Rule 의 RBAC 도메인 확장이다.
- 신규 라우트 PR 시 `docs/security/rbac-policy.md` 의 권한 매트릭스를 함께 업데이트한다.

### 12.1 강제 검사 절차

1. **정적 검사**: `python scripts/check_rbac_coverage.py` — 라우터 파일을 AST 로 파싱해서 모든 mutation 데코레이터에 `require_*` 의존성이 붙어있는지 확인. Doc Sync 워크플로에 등록하고 0 errors 강제.
2. **통합 테스트**: `tests/test_rbac_routes.py` — viewer 토큰으로 모든 mutation 라우트가 403 을 반환하는지, admin 토큰으로 200 을 반환하는지 검증. 신규 라우트는 자동으로 매트릭스에 추가되거나 명시적 예외 처리해야 한다.
3. **수동 검증**: 신규 라우트가 추가된 PR 은 머지 전에 viewer 토큰으로 직접 호출하여 403 을 확인한다. CI 녹색 = 안전이 아니다.

### 12.2 회고: 9위 RBAC 작업에서의 누락

9위 RBAC 도입 시 `users.py` 외 9개 라우터에 가드가 누락된 채로 머지됐다. 원인:
- 헬퍼 정의 = 적용 으로 동치시킨 사고 누락
- "기존 라우터는 `get_current_user` 를 쓰니 인증된다" 는 가정 (인증/인가 미분리)
- wiring 검증 단계 부재 (위 1, 2, 3 절차 모두 없었음)
- 단위 테스트만으로 통과 확인 → CI 녹색에 안주

같은 실수 재발 방지: 위 강제 검사 절차를 반드시 작동시킨다.

## 13. 공급망 보안 검증 규칙 (Supply-Chain Security Rule)

빌드/배포 산출물(컨테이너 이미지, Python 의존성)은 **빌드 → SBOM/CVE 게이트 → 서명 → 배포 시 검증**의 단일 흐름을 깨지 않는다. 단일 진실원천: `docs/security/supply-chain-policy.md`.

- **레지스트리**: `ghcr.io/${IMAGE_NAMESPACE}/aqts-backend` 만 사용한다. `IMAGE_NAMESPACE` 는 GitHub `repository_owner`. `docker-compose.yml` 의 `backend`/`scheduler` 는 `image:` 만 참조하고 `build:` 블록은 `docker-compose.override.yml` (개발 전용)에만 둔다. 운영 배포는 `docker compose -f docker-compose.yml up -d` (override 미사용).
- **CI 게이트** (`.github/workflows/ci.yml`):
  - `pip-audit` (OSV) — 화이트리스트는 `backend/.pip-audit-ignore` 만 인정. 만료일 + 구체적 사유 필수.
  - `grype` 컨테이너 스캔 — `severity-cutoff: high` 에서 차단. SARIF 는 GitHub Security 탭에 업로드.
  - `syft` SBOM (CycloneDX JSON) — 90일 아티팩트 보관.
  - `cosign sign` keyless (Fulcio + Rekor, OIDC) → `cosign attest --type cyclonedx` 로 SBOM attestation → `cosign verify` sanity check.
- **CD 게이트** (`.github/workflows/cd.yml`): 배포/롤백 모두 다음을 강제한다.
  ```bash
  cosign verify \
    --certificate-identity-regexp "^https://github.com/${REPO_FULL}/" \
    --certificate-oidc-issuer "https://token.actions.githubusercontent.com" \
    "${IMAGE_REF}"
  ```
  실패 시 `docker pull` 로 진입하지 않고 즉시 중단한다. 서명되지 않은 임의 이미지가 배포 경로에 진입할 수 없다.
- **새 의존성 추가 시**: `pip-audit` 통과를 직접 확인한 뒤 커밋한다. 일시적 화이트리스트가 필요하면 PR 본문에 만료일 + 후속 액션을 기재한다.
- **새 베이스 이미지 / Dockerfile 변경 시**: PR 머지 전 `grype` 결과를 직접 확인한다. high 이상 CVE 가 출력되면 머지 금지 (또는 명시적 화이트리스트 + 후속 일정).
- **수동 점검**: 운영자는 `docs/security/supply-chain-policy.md` §7 의 절차로 임의 시점에 `cosign verify` 를 직접 실행할 수 있어야 한다.
- **회고 — 도구 정의 ≠ 게이트 작동**: SBOM/서명을 만드는 것과 그 산출물이 실제로 배포되는지는 다른 문제다. 빌드/검증/실행이 동일 digest 를 따라가지 않으면 통제는 형식적이다. RBAC Wiring Rule 과 동일한 사고 — "정의했다 ≠ 적용했다" — 가 공급망에도 그대로 적용된다.

## 14. 알림 파이프라인 Wiring Rule (Alerting Pipeline Wiring Rule)

알림 파이프라인은 **다층 wiring** 으로 구성된다. 각 레이어를 정의한 것과 실제로 적용(주입·기동·노출)한 것은 독립적으로 누락될 수 있으므로, RBAC Wiring Rule / 공급망 Wiring Rule 과 동일한 원칙 — **"정의했다 ≠ 적용했다"** — 이 적용된다.

### 14.1 5 개 레이어와 검증 방법

| 레이어 | 정의 위치 | 적용 위치 | 검증 방법 |
|---|---|---|---|
| 상태 머신 메서드 (`claim_for_sending`, `mark_*`, `requeue_*`) | `alert_manager.py` | `_dispatch_via_router`, `dispatch_retriable_alerts` 가 호출 | 단위 테스트 (`test_alert_manager.py`) |
| NotificationRouter 인스턴스 | `fallback_notifier.py` | `main.py` lifespan 에서 `set_notification_router` 호출 | 통합 테스트 (`test_alert_manager_dispatch_wiring.py`), 기동 로그 `NotificationRouter wired` |
| 재시도 루프 (`_alert_retry_loop`) | `main.py` 함수 정의 | lifespan 에서 `asyncio.create_task` | 기동 로그 `AlertRetryLoop started` |
| Prometheus 메트릭 훅 | `metrics.py` Counter/Histogram 정의 | `NotificationRouter.dispatch` 내부 try/finally | `/metrics` 엔드포인트에 `aqts_alert_dispatch_*` 계열 노출 |
| 메타알림 규칙 (`aqts_alert_pipeline`) | `aqts_alerts.yml` | Alertmanager 로드 | `promtool check rules`, Prometheus UI `/rules` |

### 14.2 필수 배포 후 검증 (수동)

다음 세 로그/지표가 **모두** 확인돼야 wiring 완료로 간주한다. 하나라도 빠지면 wiring 결손이다.

1. `docker compose logs backend --tail=500 | grep 'NotificationRouter wired'` — 출력 있어야 함
2. `docker compose logs backend --tail=500 | grep 'AlertRetryLoop started'` — 출력 있어야 함
3. `curl -s http://<backend>/metrics | grep -c 'aqts_alert_dispatch'` — 0 이면 결손

### 14.3 신규 코드 작성 시 주의

- `AlertManager` 에 새 상태 전이 메서드를 추가하면, 해당 메서드를 **실제로 호출하는 경로**가 존재하는지 확인한다.
- `metrics.py` 에 새 Counter/Histogram 을 추가하면, **실제로 `.inc()` / `.observe()` 를 호출하는 코드**가 있는지 확인한다.
- `main.py` lifespan 에 새 task 를 추가하면, shutdown 에서 **cancel + await** 경로가 있는지 확인한다.
- 아키텍처 상세: [`docs/architecture/notification-pipeline.md`](../docs/architecture/notification-pipeline.md)

### 14.4 회고: Commit 2 이전의 wiring 결손

`AlertManager` 는 Commit 1 시점에 `save_alert` + 상태 전이 메서드를 모두 갖추고 있었지만, `NotificationRouter` 가 주입되지 않아 `_dispatch_via_router` 가 noop 으로 동작했다. 알림은 MongoDB 에 저장됐지만 Telegram 으로 나가지 않았다. 기동 로그에 wiring 관련 메시지가 없었지만 서버는 정상 기동했으므로, 외부에서는 장애를 인지할 수 없었다. Commit 2 에서 lifespan wiring + 기동 로그 + 통합 테스트를 추가해 결손을 해소했다.

## 15. SSH Heredoc 에서 비대화형 원격 명령 작성 규칙

CD/운영 스크립트에서 원격 실행을 `ssh -T ... bash -s << 'EOF' ... EOF` 형태로 구성할 때, heredoc 안의 명령에는 **부모 셸의 stdin 을 자식 프로세스로 forward 하는 플래그를 절대 사용하지 않는다**. 구체적으로 금지되는 패턴:

- `docker exec -i ...` / `docker exec --interactive ...`
- `kubectl exec -i ...` / `kubectl exec --stdin ...`
- `docker compose run ...` (플래그 없이 실행되면 **기본값으로** stdin 을 attach 한다. 반드시 `-T` 로 TTY 할당을 끈 뒤 `</dev/null` 로 stdin 을 격리한다.)
- `docker run ...` (동일한 이유. `-T`/`--interactive=false` + `</dev/null` 필수)
- 기타 "stdin 을 읽어 컨테이너/원격으로 파이프" 하는 모든 플래그 또는 **플래그 없이도 stdin 을 attach 하는 기본 동작**

**근거**: `bash -s` 는 heredoc 을 stdin 으로 스트리밍 입력받아 한 줄씩 파싱·실행한다. heredoc 내부에서 `-i` 같은 stdin forwarding 자식 프로세스가 실행되면, 자식이 부모의 fd 0 을 inherit 하여 heredoc 의 나머지 모든 줄을 소진한다. 부모 bash 는 자식 종료 후 다음 줄을 읽으려다 EOF 를 만나 **정상 종료 (exit 0)** 한다. `set -e` 는 이 경로를 잡지 못한다 — 비정상 종료가 아니기 때문이다. 외부 관찰(CI UI)로는 `step ✓` 로 보여 수 분~수 시간 동안 드리프트가 은폐된다.

**회귀 사례 1 (2026-04-09, cd.yml Step 5b)**: `docker exec -i aqts-postgres bash -c '...'` 가 Step 5c (alembic upgrade), Step 5d (force-recreate), Step 5e (digest assert), Step 6 (health wait) 을 모두 은폐했다. 세 번의 배포(`051c453`, `6500bcb`, `a48c4c8`) 가 모두 Step 5b 에서 조용히 clean exit 했고, scheduler 는 `sha-70eee29` 로 고정된 채 새 이미지가 단 한 번도 실행되지 않았다. 외부에서는 `Deploy to server ✓ 9s` 로 성공으로 보였다. 자세한 경위: `docs/operations/daily-report-regression-2026-04-08.md §4.7`.

**회귀 사례 2 (2026-04-09, cd.yml Step 5c, 사례 1 수정 직후)**: 사례 1 을 `docker exec -i` → `docker exec ... </dev/null` 로 고친 `8fcd6c6` 이 푸시된 직후의 CD 에서, 이번엔 Step 5c 의 `docker compose run --rm backend alembic upgrade head` 가 동일한 stdin 소진 패턴으로 Step 5d/5e/6 을 은폐했다. `docker compose run` 은 `-T` 플래그 없이 호출되면 기본값으로 TTY 할당 + stdin attach 를 수행하므로 heredoc 잔여 라인을 모두 소진한다. 외부 관찰로는 Deploy to server 로그가 `INFO [alembic.runtime.migration] Will assume transactional DDL.` 에서 끊기고 Post-deploy smoke C4 에서 heartbeat age=2375s 로 실패했다. 자세한 경위: `docs/operations/daily-report-regression-2026-04-08.md §4.8`. **교훈**: "docker exec 만 금지" 는 플래그-중심 규칙이고, 이번 실패는 기본값-중심 규칙(`-T` 없는 `docker compose run`) 에서 났다. 규칙은 플래그가 아니라 "자식이 fd 0 을 읽는가" 로 일반화되어야 한다.

**올바른 패턴**:

```bash
# 비대화형 명령에는 -i 를 쓰지 않는다. 방어적으로 </dev/null 을 명시한다.
HAS_VER=$(docker exec aqts-postgres bash -c 'psql ...' </dev/null | tr -d '[:space:]')

# docker compose run 은 플래그 없이 실행되면 기본값으로 stdin 을 attach 한다.
# 반드시 -T 로 TTY 를 끄고, </dev/null 로 stdin 을 이중 격리한다.
docker compose -f docker-compose.yml run --rm -T backend \
  alembic -c alembic.ini upgrade head </dev/null
```

**강제 검사**: `.github/workflows/*.yml` 과 `scripts/**/*.sh` 에 다음 패턴이 등장하지 않는지 Doc Sync 레인트에서 grep 으로 가드한다 (별도 후속 커밋):

- `docker exec -i` / `docker exec --interactive`
- `kubectl exec -i` / `kubectl exec --stdin`
- `-T` 없는 `docker compose run` (정규식: `docker compose .* run (?!.*-T)`)
- `-T` 없는 `docker run` (동일 논리)
- heredoc 내부에서 `</dev/null`/파이프/파일 redirect 없는 `bash X.sh` / `sh X.sh` / `./X.sh` 하위 스크립트 호출 (Rule 5, 2026-04-09 §4.11). 자식 bash 가 부모 heredoc fd 0 을 상속하므로 하위 스크립트의 장래 변경으로부터 호출 지점에서 격리해야 한다.

**일반화된 원칙**: SSH heredoc 안에서 실행되는 모든 자식 프로세스에 대해 "이 프로세스가 fd 0 을 읽는가" 를 먼저 물어본다. 읽는다면 반드시 `</dev/null` 로 격리한다. "비대화형이라 stdin 이 필요 없다" 는 의도만으로는 불충분하다 — **자식 프로세스는 의도와 무관하게 부모 fd 0 을 상속하고, 어떤 프로세스는 그 stream 을 "그냥 읽어 버린다"**. 이 사고는 RBAC Wiring Rule / 공급망 Wiring Rule 의 CD 도메인 확장이다 — "스크립트를 적었다 ≠ 스크립트의 모든 줄이 실행됐다".

---

## 문서 소유권

- 이 문서의 모든 섹션(0~15) 은 `docs/archive/CLAUDE-pre-phase1-migration.md` (2026-04-16 284줄 시점) 에서 원문 이반되었다. 향후 규칙 추가/수정은 **반드시 이 문서에만** 반영하며, `CLAUDE.md` 는 포인터만 유지한다.
- 이반 과정에서 §14.3 의 `docs/architecture/notification-pipeline.md` 링크는 `agent_docs/` 가 프로젝트 루트 하위 한 단계 깊이이므로 `../docs/...` 로 상대 경로를 재작성했다 (내용 동일). 그 외 본문은 문장 단위 무손실이다.
- 동기 담당: Tests/Doc-Sync 팀메이트 (`docs/migration/cowork-to-agent-teams-plan.md` §6-1).
