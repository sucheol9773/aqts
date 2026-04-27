---
from: 1
to: 4
subject: rl-scheduler-integration-test-gap-ask
created: 2026-04-27T12:32:43Z
priority: Ask  # [P0] 긴급, [Ask] 응답 요청, [FYI] 참고, [Lead-Approval] 리드 승인
---

# rl-scheduler-integration-test-gap-ask

## 요약

`backend/tests/test_rl_production.py::TestSchedulerRLIntegration` 3건이 `_run_rl_inference` 의 **성공 경로 / no_ohlcv 경로 / 예외 경로** 를 전혀 검증하지 못해 RL 추론 라이브 실패가 silent 가 됩니다 (development-policies.md §8 Silence Error 패턴). 팀1 영역에서 식별한 3개 누락 케이스를 위임드립니다.

## 맥락

팀1(Strategy/Backtest) 워크트리에서 RL 파이프라인 통합 갭 점검 중 발견. 통합 호출처는 `backend/core/scheduler_handlers.py:789-865` (`_run_rl_inference`) 단 1군데이고, 거기서 다음 4개 분기가 존재합니다:

1. `service.load_model() == False` → `skip_reason = "no_champion_model"` ✅ (커버됨)
2. `service.load_model() == True` + `_load_ohlcv_for_inference` 가 빈 dict → `skip_reason = "no_ohlcv_data"` ❌
3. `service.load_model() == True` + ohlcv 정상 → `predict_batch` 성공 → Redis 캐시 + `signals_count`/`orders_count` 채움 ❌ (행복 경로)
4. `predict_batch` 도중 예외 → `rl_summary["error"] = str(e)` + `logger.warning` ❌ (silence error 검증)

현재 `TestSchedulerRLIntegration` 의 3건 (`test_rl_production.py:296-334`) 의 실태:

| 테스트 | 문제 |
|---|---|
| `test_run_rl_inference_no_model` | OK — 분기 1 만 검증 |
| `test_run_rl_inference_import_error` | **본문이 사실상 `pass`**. with-block 내부에 `pass` 만 있고, 이후 `_run_rl_inference` 를 그냥 호출 → `scheduler_handlers.py:859 except ImportError` 분기를 **실제로 검증하지 못함**. 주석에서도 "직접 호출은 정상 경로만 테스트" 라고 자인 |
| `test_scheduler_handler_has_rl_section` | docstring 문자열 검사만 — 통합 의미 없음 |

**왜 위험한가**: `scheduler_handlers.py:861-863` 의 광범위 `except Exception as e: logger.warning(...)` 는 §8 의 전형적 Silence Error 패턴. RL 추론이 라이브 운영 중 매번 실패해도 (a) 테스트는 그린, (b) `rl_summary["error"]` 가 다음 단계로 전파되지 않음, (c) Prometheus metric 도 없음. shadow_mode=True 라 실주문 영향은 없지만, 챔피언 모델 검증이 라이브 shadow 결과 의존이라 silent 실패 시 **승격 게이트 자체가 멈춥니다**.

## 요청 / 정보

`TestSchedulerRLIntegration` 을 다음과 같이 재정비 부탁드립니다 (3건 추가, 1건 교체, 1건 의미 강화):

**(1) 신규 — `test_run_rl_inference_no_ohlcv`**
- mock: `RLInferenceService.load_model` → `True`, `_load_ohlcv_for_inference` → `{}`
- assert: `result["enabled"] is True`, `result["skip_reason"] == "no_ohlcv_data"`, `result["model_version"] is not None`

**(2) 신규 — `test_run_rl_inference_happy_path`**
- mock: `load_model` → `True`, `_load_ohlcv_for_inference` → `{"005930": _make_ohlcv(400)}`, `RedisManager.get_client` → AsyncMock
- assert: `result["enabled"] is True`, `result["signals_count"] >= 0`, `result["inference_time_ms"] > 0`, Redis `set` 가 `"rl:inference:latest"` 키로 호출됨

**(3) 신규 — `test_run_rl_inference_predict_exception_is_silent_in_summary`**
- mock: `load_model` → `True`, `_load_ohlcv_for_inference` → `{"005930": ...}`, `predict_batch` → `raise RuntimeError("synthetic")`
- assert: `result.get("error") == "synthetic"`, `result["enabled"] is True`, **함수가 raise 하지 않음** (graceful degradation 보존), `logger.warning` 1회 호출 검증
- 주석에 §8 Silence Error 명시: "이 테스트는 silent 실패가 의도된 graceful degradation 임을 고정하기 위함. 향후 metric 노출이 추가되면 그것도 함께 검증."

**(4) 교체 — `test_run_rl_inference_import_error`**
- 현재 `pass` 만 있어 무효. `monkeypatch.setattr("core.scheduler_handlers.__import__", ...)` 또는 `sys.modules["core.rl.inference"] = None` 으로 실제 ImportError 를 강제 발생시키고
- assert: `result["skip_reason"] == "rl_module_not_available"`, raise 하지 않음

**(5) 의미 강화 — `test_scheduler_handler_has_rl_section`**
- docstring grep 만으로는 wiring 무효. `handle_market_open` 의 본문 또는 그 호출 그래프에서 `_run_rl_inference` 가 실제로 호출되는지 AST/source 검사로 강화 (예: `inspect.getsource(handle_market_open)` 에 `_run_rl_inference` 토큰 포함). 또는 통합 시뮬 1회로 호출 여부 어서트.

## 영향 범위 / 권장 작업량

- 수정 파일: `backend/tests/test_rl_production.py` 1개 (3건 추가 + 1건 교체 + 1건 강화 = 약 +120 LOC)
- `scheduler_handlers.py` 코드 수정 **불필요** — 본 작업은 기존 동작의 **고정**(characterization tests) 이지 동작 변경이 아닙니다.
- silence error 표면화(예: Prometheus metric `aqts_rl_inference_failures_total` 신설) 는 별건. 그건 팀2 영역(scheduler_handlers + monitoring/metrics) 이라 본 메일박스에는 포함하지 않았습니다. 필요하면 본 위임 머지 후 팀2에 별도 위임 보내겠습니다.

## 응답 기한

W1 종료(2026-04-28) 까지 답신만 부탁드립니다. 실제 PR 머지는 W2 중반까지 여유 있습니다. 의견 차이가 있으면 (3) 의 silence error 의도를 어떻게 고정할지부터 합의하면 좋겠습니다.

— 팀1 (Strategy/Backtest)
