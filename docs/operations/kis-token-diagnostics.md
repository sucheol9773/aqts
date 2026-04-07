# KIS 토큰 발급 실패 진단 로깅

## 1. 배경

CD 배포 후 health 엔드포인트가 `kis_api: degraded` 상태로 고착되는 회귀가 관찰됐다.
컨테이너 로그에는 `RetryError[<Future raised HTTPStatusError>]` 한 줄만 남아 있어
정확한 status code 와 KIS 의 `error_code`/`error_description` 을 알 수 없었다.

원인 가설은 KIS 의 `EGW00133` (1분 1회 토큰 발급 제한) 등 일시적 4xx 였지만, 추론
만으로는 확정할 수 없었다. CLAUDE.md 의 **"오류 수정 시 관찰 우선 원칙"** 에 따라,
다음 실패 시점에 정확한 원인 신호를 잡기 위해 진단 로깅을 먼저 도입한다. 회복 로직
(주기 재시도/health-driven recovery)은 별도 커밋으로 분리한다.

## 2. 변경 사항

### 2.1 `core/data_collector/kis_client.py::KISTokenManager`

- `_issue_token()` 의 `try` 블록을 두 단계로 나눈다.
  1. 내부 `_do_issue()` 가 `@retry` 로 감싸진 채 토큰 요청을 수행.
  2. 바깥에서 `RetryError` 를 잡아 `last_attempt.exception()` 으로 원래 예외를 추출.
- 추출된 원래 예외를 다음 두 헬퍼로 분리해서 처리한다.
  - `_log_token_issue_failure(exc)`: status code / KIS error_code / error_description /
    raw body(앞 500자) 를 명시 로그로 출력. timeout/일반 HTTPError 는 예외 타입을
    그대로 노출.
  - `_wrap_token_issue_error(exc)`: `KISAPIError` 로 일관되게 wrap. KIS error_code 가
    파싱되면 `code=error_code`, 그렇지 않으면 `code=HTTP{status}`.
- `_parse_kis_error_body(text)`: KIS 응답 body 에서 `(error_code, error_description)`
  추출. 비정상 JSON·dict 가 아닌 payload 는 `(None, None)` 반환.
- 시크릿(`app_key`, `app_secret`) 은 어떤 경로에서도 로그에 남기지 않는다.

### 2.2 호출자 호환성

`main.py` lifespan 의 `KIS 토큰 초기화 실패 (degraded)` 분기는 그대로 유지된다.
바깥 계약(`KISAPIError` raise)이 동일하기 때문에 추가 와이어링은 필요 없다.

## 3. 테스트

신규 파일 `backend/tests/test_kis_token_diagnostics.py` (7 케이스):

| 케이스 | 검증 항목 |
|--------|-----------|
| `test_kis_rate_limit_egw00133_logs_status_and_error_code` | 403/EGW00133 상황에서 status, error_code, description 모두 로그 출력 + 시크릿 미노출 + `KISAPIError.code == "EGW00133"` |
| `test_unauthorized_401_logs_http_status` | 401/EGW00121 도 동일하게 로그 + `code == "EGW00121"` |
| `test_timeout_logs_timeout_type` | `httpx.ReadTimeout` 발생 시 `code == "ReadTimeout"`, 로그에 `timeout` 명시 |
| `test_unparseable_body_falls_back_to_http_status_code` | 비표준 응답(HTML) → `code == "HTTP503"` 로 fallback |
| `test_parse_kis_error_body_extracts_fields` | 정상 JSON 파싱 |
| `test_parse_kis_error_body_returns_none_on_invalid_json` | 비-JSON 처리 |
| `test_parse_kis_error_body_returns_none_on_non_dict_payload` | 배열 등 dict 가 아닌 payload 처리 |

추가로 `tests/test_gate_b_security.py::test_token_issue_http_error_propagates` 의
이전 계약(RetryError 그대로 새어나감)을 새 계약(`KISAPIError` 로 unwrap)으로 갱신했다.
이전 계약 자체가 회귀의 직접 원인이었기 때문에 기대값 자체를 새 계약으로 교체한다
(테스트 통과만을 위한 조정이 아니라, 기대 동작 자체가 바뀐 것).

### 3.1 loguru 캡처 fixture

`pytest` 의 `capsys`/`caplog` 는 loguru 의 sink 를 가로채지 못한다 (loguru 는 자체
sink 시스템을 사용). 본 테스트 파일은 `loguru_capture` fixture 를 정의해 임시 sink
를 추가한 뒤 메모리 버퍼로 메시지를 수집한다. 다른 loguru 캡처 테스트의 표준 패턴
으로 재사용 가능.

## 4. 검증 절차

```bash
cd backend
python -m ruff check . --config pyproject.toml          # 0 errors
python -m black --check . --config pyproject.toml       # All done
python -m pytest tests/ -q --no-cov                      # 3263 passed
python ../scripts/gen_status.py --update                 # doc-sync 갱신
```

## 5. 다음 단계 (별도 커밋)

본 커밋은 **진단 신호 확보** 까지만 책임진다. 다음 두 가지는 별도 PR 로 분리한다.

1. **회복 로직**: 한 번 `degraded` 가 되면 영구 고착되는 문제. health check 또는
   주기 트리거로 재발급을 시도하고, 성공 시 `app.state.kis_degraded = False` 로
   복원한다.
2. **EGW00133 회피**: CD 배포 직후 부팅 시점에 KIS 의 1분 1회 제한과 충돌하는 패턴
   을 줄이기 위해 grace period / jittered backoff 도입.

회복 로직과 진단 로깅을 한 커밋에 묶지 않는 이유는 CLAUDE.md 의 **"bug fix 커밋에
무관한 변경 끼워넣기 금지"** 원칙 준수 — 회귀 발생 시 책임 범위/검증 범위를 분리
하기 위함.

## 6. 변경 파일

- 수정: `backend/core/data_collector/kis_client.py`
- 수정: `backend/tests/test_gate_b_security.py` (계약 갱신)
- 신규: `backend/tests/test_kis_token_diagnostics.py`
- 신규: 본 문서 `docs/operations/kis-token-diagnostics.md`

Last reviewed: 2026-04-07
