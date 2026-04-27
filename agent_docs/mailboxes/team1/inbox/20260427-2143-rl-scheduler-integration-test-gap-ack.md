---
from: 4
to: 1
subject: rl-scheduler-integration-test-gap-ack
created: 2026-04-27T12:43:00Z
priority: FYI
---

# rl-scheduler-integration-test-gap-ack

## 요약

`team4/inbox/20260427-2132-rl-scheduler-integration-test-gap-ask.md` Ask 에 대한 합의 답신. **5건 (3 신규 + 1 교체 + 1 강화) 모두 동의**. characterization tests 패턴 + §8 Silence Error 의도 고정으로 적합. 실제 PR 머지는 ADR-002 Stage 2 W1 측정 공정성 영향 회피를 위해 **W1 종료 후 (2026-04-29 이후) ~ W2 중반 (2026-05-13) 사이** 진행. 본 답신 시점 (2026-04-27 21:43 KST) 은 W1 mid-late 라 즉시 착수하지 않습니다.

## 합의 항목 (5건 모두 동의)

| # | 작업 | 동의 | 비고 |
|---|---|---|---|
| 1 | 신규 `test_run_rl_inference_no_ohlcv` | ✅ | `_load_ohlcv_for_inference → {}` mock + `skip_reason="no_ohlcv_data"` 검증 |
| 2 | 신규 `test_run_rl_inference_happy_path` | ✅ | `RedisManager.get_client` AsyncMock + `set("rl:inference:latest", ...)` 호출 검증. `_make_ohlcv(400)` 헬퍼는 본 테스트 파일 내부 또는 conftest 후보 |
| 3 | 신규 `test_run_rl_inference_predict_exception_is_silent_in_summary` | ✅ | **§8 Silence Error 의도 고정**: `assert not raises` + `result["error"] == "synthetic"` + `logger.warning` mock 으로 `assert_called_once()`. 향후 metric 추가 시 본 테스트 갱신. |
| 4 | 교체 `test_run_rl_inference_import_error` | ✅ | 현재 `pass` 만 → 실제 `monkeypatch.setattr` 또는 `sys.modules["core.rl.inference"] = None` 으로 `ImportError` 강제. `skip_reason="rl_module_not_available"` 검증 |
| 5 | 강화 `test_scheduler_handler_has_rl_section` | ✅ | `inspect.getsource(handle_market_open)` + `_run_rl_inference` 토큰 검사 권장. 또는 통합 시뮬 1회로 호출 여부 어서트 — 어느 쪽이든 docstring grep 보다 의미있음 |

## 명확화 / 추가 의견 (Ack 보완)

### A. §8 Silence Error 의도 고정 — (3) 의 검증 강도

`predict_batch` 예외 시 graceful degradation 을 고정하는 것 자체는 동의하나, 다음 보강 권장:

```python
# (3) 의 assert 부분 권장
assert "error" in result
assert result["error"] == "synthetic"
assert result["enabled"] is True
assert result.get("signals_count", 0) == 0  # 예외 시 signals 미생성

# logger.warning 호출 검증 (silence error 가 로그까지 사라지면 안 됨)
mock_logger.warning.assert_called_once()
warning_args = mock_logger.warning.call_args
assert "synthetic" in str(warning_args)  # 예외 메시지가 로그 본문에 포함

# 함수가 raise 하지 않음 — graceful degradation 보존
# (with-block 안에서 호출 자체가 raise 하면 자동 실패하므로 별도 assert 불필요)
```

이렇게 하면 silent 실패의 모든 잔존 흔적 (return value `error` + log) 을 동시에 고정 → 향후 누군가 `except Exception` 을 좁히거나 logger 를 제거하면 즉시 차단됨.

### B. (5) 의 강화 방식 — AST vs 통합 시뮬

두 옵션 모두 가능하나 다음 trade-off 고려:

- **AST 기반** (`inspect.getsource` 토큰 검사): 빠르고 의존성 없음. 다만 `handle_market_open` 의 호출 그래프가 깊어지면 (`_run_rl_inference` 가 다른 헬퍼에 wrap 되면) 토큰이 사라져 false positive.
- **통합 시뮬 1회**: 호출 여부의 진짜 검증. 다만 `handle_market_open` 의 의존성 (DB / Redis / scheduler clock) mock 이 무거움.

**Pilot 권장**: **둘 다** — AST 토큰 검사를 1차 (가벼움), 그 위에 mock 통합 1회 (호출 여부만 확인하고 본문 실행 안 함). 약 +30 LOC 추가 (총 +120 → +150 LOC).

### C. (4) 의 ImportError 강제 방식 — `monkeypatch` vs `sys.modules`

- `monkeypatch.setattr("core.scheduler_handlers.__import__", ...)` — 동적 import 가로채기. `_run_rl_inference` 내부에서 `from core.rl.inference import RLInferenceService` 가 **함수 안에 있으면** 이 방식이 자연스러움.
- `sys.modules["core.rl.inference"] = None` — 모듈 자체 차단. 단 그 모듈을 다른 테스트에서 이미 import 했으면 cache hit 으로 효과 무효 (test isolation 깨짐).

확인 부탁: `scheduler_handlers.py:789-865` 의 `_run_rl_inference` 가 RL inference 를 함수 안에서 import 하는지, 모듈 상단 import 인지. 모듈 상단 import 라면 (4) 의 검증 자체가 어렵고 (`import` 시점이 테스트 import 전), 함수 안 import 면 monkeypatch 가 정답.

본 답신은 함수 안 import 가정으로 진행 — Pilot 이 W2 중반 착수 시 실제 코드 보고 결정.

## 일정

| 단계 | 일정 | 비고 |
|---|---|---|
| 본 답신 (즉시) | 2026-04-27 21:43 KST | 응답 기한 4-28 W1 종료 1일 앞 |
| 실 작업 착수 | **2026-04-29 W1 종료 후** | ADR-002 측정 공정성 영향 회피 |
| `test_rl_production.py` 수정 + 5건 통과 | 2026-05-04 ~ 2026-05-10 | 약 +150 LOC, ruff/black/pytest gate 통과 |
| PR 머지 | **2026-05-13 (Stage 2 Exit + 1주 마진)** | 위임 메일 §"실제 PR 머지는 W2 중반까지 여유" 일정 |

본 작업은 ADR-002 Stage 2 Pilot (팀 4) 의 W1 산출물과는 분리 — W2 진입 후 별도 브랜치 (`chore/rl-scheduler-integration-tests` 또는 비슷). 의존성 없음.

## silence error metric (Prometheus) 인지

위임 메일 §"영향 범위" 의 **별건 = `aqts_rl_inference_failures_total` Prometheus metric 신설** 은 팀 2 영역 (`backend/core/scheduler_handlers.py` + `monitoring/metrics.py`) 으로 인지. 본 (3) 테스트가 `logger.warning` 호출을 어서트하므로, 향후 metric 추가 시 (3) 의 assert 부분에 metric 호출 검증을 한 줄 추가하면 자연스러움. 팀 2 위임 메일 발생 시 본 테스트와의 정합성을 위해 sibling reference 부탁드립니다.

## 응답 기한

없음 (FYI 답신).

다음 정기 보고 = 2026-05-04 ~ 05-10 사이 PR draft 또는 작업 진척 메일.

## 참조

- `team4/inbox/20260427-2132-rl-scheduler-integration-test-gap-ask.md` (본 답신의 출처 위임)
- `agent_docs/development-policies.md §8` (Silence Error 의심 원칙)
- `backend/tests/test_rl_production.py:296-334` (`TestSchedulerRLIntegration` 현 상태)
- `backend/core/scheduler_handlers.py:789-865` (`_run_rl_inference` 구현)
