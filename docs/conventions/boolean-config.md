# Boolean 환경변수 표기 표준

## 목적

환경변수/Compose/CI/.env 파일의 bool 표기 불일치로 인한 장애를 차단한다.
실제 사례: 2026-04-07 CI 실패. `tracing.py`가 `TESTING == "1"` 만 체크했고
CI는 `TESTING="true"` 를 넘겨 OTel이 비활성화되지 않고 OTLP exporter가 죽음.

## 정책 요약

| 레이어 | 표준 |
| --- | --- |
| Python 내부 | `bool` (`True` / `False`) |
| 환경변수 / YAML / Compose / CI / .env | 소문자 `"true"` / `"false"` |
| 파싱 | `core.utils.env.env_bool()` 단일 진입점 |

신규 코드, 신규 설정값, 신규 문서는 **표준 표기만** 사용한다.

## env_bool() 사용법

```python
from core.utils.env import env_bool

# 미설정 시 기본값
otel_enabled = env_bool("OTEL_ENABLED", default=False)

# 미설정 시 KeyError (필수 옵션)
required = env_bool("MUST_BE_SET")

# Phase 2 strict mode 미리 적용 (호출 단위)
strict_value = env_bool("DRY_RUN", default=False, strict=True)
```

### 시그니처

```python
env_bool(key: str, default: bool | None = None, *, strict: bool | None = None) -> bool
```

- `default=None` 이고 환경변수가 없으면 `KeyError` (fail-fast).
- `strict=None` 이면 글로벌 환경변수 `AQTS_STRICT_BOOL` 을 따른다.
- 알 수 없는 값은 항상 `ValueError`.

## 허용 표기

### Phase 1 (현재)

| 분류 | 허용 (대소문자 무시) | 동작 |
| --- | --- | --- |
| 표준 truthy | `true` | `True` 반환, 경고 없음 |
| 표준 falsy | `false` | `False` 반환, 경고 없음 |
| 하위호환 truthy | `1`, `yes`, `on` | `True` 반환 + **경고 1회** + Prometheus counter 증가 |
| 하위호환 falsy | `0`, `no`, `off` | `False` 반환 + **경고 1회** + Prometheus counter 증가 |
| 빈 문자열 / 미설정 | — | `default` 반환 (`None` 이면 `KeyError`) |
| 그 외 | `maybe`, `2`, `enabled` 등 | `ValueError` |

### Phase 2 (예정)

`AQTS_STRICT_BOOL=true` 또는 호출 시 `strict=True` 면 하위호환 표기도
`ValueError` 로 승격된다.

## 경고 중복 억제

- 동일한 `(key, raw_value)` 쌍에 대해 프로세스당 1회만 `WARNING` 출력.
- Prometheus counter `aqts_env_bool_nonstandard_total{key,value}` 로
  비표준 사용 빈도를 정량 추적.
- 멀티프로세스 워커는 프로세스별 1회로 충분하며, 글로벌 집계는
  메트릭 수집기에서 처리한다.

## Phase 2 승격 조건

다음을 **모두** 충족해야 기본값을 strict 로 전환한다.

1. Phase 1 머지 후 **최소 14일** 관찰.
2. CI 로그의 `non-standard bool literal` 경고 **0건**.
3. 운영 환경 `aqts_env_bool_nonstandard_total` **0건**
   (backend, scheduler, db-backup, otel-collector 사이드카 등 전수).
4. 정적 점검 (`scripts/check_bool_literals.py`) 0 errors.

전환 시 다음 마이너 릴리즈 노트의 Breaking Change 섹션에 명시한다.

### 롤백 기준

전환 후 7일 내 `ValueError` 가 운영 환경에서 1건이라도 발생하면
`AQTS_STRICT_BOOL` 기본값을 즉시 `false` 로 되돌리고 원인 수정 후 재전환.

## 정적 검사

`scripts/check_bool_literals.py` 가 다음을 차단한다.

- Python 코드의 ad-hoc 파싱 패턴
  (`os.environ.get(...) ==`, `.lower()`, `in (..., "true", ...)` 등).
- `.env*`, `docker-compose*.yml`, `.github/workflows/*.yml` 의
  알려진 bool 키 (`TESTING`, `OTEL_ENABLED`, `SCHEDULER_ENABLED`,
  `DEBUG`, `AQTS_STRICT_BOOL`, `COLLECTOR_OTLP_ENABLED`) 가 비표준 표기
  사용 시.

CI 워크플로 `Doc Sync` 에 통합되어 PR 단계에서 자동 실행된다.

## 마이그레이션 가이드

1. `from core.utils.env import env_bool` 추가.
2. `os.environ.get(key, "false").lower() == "true"` 등 ad-hoc 파싱을
   `env_bool(key, default=False)` 로 교체.
3. `.env*`, Compose, CI 의 bool 값을 소문자 `true`/`false` 로 일괄 변경.
4. `python scripts/check_bool_literals.py` 로 검증.
5. 문서/리뷰에서 표준 표기 사용 여부 확인.
