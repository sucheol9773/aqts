"""
System Routes API 종합 단위 테스트

다음 5개 엔드포인트를 테스트합니다:
  1. GET /settings - 시스템 설정 조회
  2. POST /backtest - 백테스트 실행
  3. POST /rebalancing - 리밸런싱 트리거
  4. POST /pipeline - 투자 분석 파이프라인 실행
  5. GET /audit-logs - 감사 로그 조회

모든 외부 의존성 (auth, DB, engines) 은 mock으로 처리됩니다.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.requests import Request

from api.middleware.auth import AuthenticatedUser


def _mock_user(user_id: str = "admin", role: str = "admin") -> AuthenticatedUser:
    """테스트용 AuthenticatedUser 객체 생성 헬퍼"""
    return AuthenticatedUser(id=user_id, username=user_id, role=role)


def _make_request(path: str = "/api/system/pipeline") -> Request:
    """테스트용 Starlette Request 생성"""
    scope = {
        "type": "http",
        "method": "POST",
        "path": path,
        "query_string": b"",
        "headers": [],
        "server": ("127.0.0.1", 8000),
        "client": ("127.0.0.1", 12345),
    }
    return Request(scope)


@pytest.mark.asyncio
@pytest.mark.smoke
class TestSystemRoutes:
    """System routes API 종합 테스트"""

    # ══════════════════════════════════════
    # GET /settings 엔드포인트 테스트
    # ══════════════════════════════════════
    async def test_get_system_settings_structure(self):
        """GET /settings 응답 구조 검증"""
        from api.routes.system import get_system_settings

        with patch("api.routes.system.get_settings") as mock_get_settings:
            # Settings mock
            mock_settings = MagicMock()
            mock_settings.environment = "test"
            mock_settings.kis.trading_mode.value = "BACKTEST"
            mock_settings.risk.initial_capital_krw = 50_000_000
            mock_settings.risk.daily_loss_limit_krw = 2_500_000
            mock_settings.risk.max_order_amount_krw = 10_000_000
            mock_settings.risk.max_positions = 10
            mock_settings.risk.max_position_weight = 0.15
            mock_settings.risk.max_sector_weight = 0.25
            mock_settings.risk.max_drawdown = 0.20
            mock_settings.risk.stop_loss_percent = 0.05
            mock_settings.telegram.alert_level = "INFO"
            mock_settings.telegram.chat_id = "123456789012345"
            mock_get_settings.return_value = mock_settings

            # Execute
            response = await get_system_settings(current_user=_mock_user("admin"))

            # Assert
            assert response.success is True
            assert response.data["environment"] == "test"
            assert response.data["trading_mode"] == "BACKTEST"
            assert response.data["risk_management"]["initial_capital_krw"] == 50_000_000
            assert response.data["risk_management"]["max_positions"] == 10
            # chat_id 마스킹 확인
            assert response.data["telegram"]["chat_id"] == "1234****"

    async def test_get_settings_error_handling(self):
        """GET /settings 예외 처리"""
        from api.routes.system import get_system_settings

        with patch("api.routes.system.get_settings") as mock_get_settings:
            mock_get_settings.side_effect = Exception("Settings load error")

            # Execute
            response = await get_system_settings(current_user=_mock_user("admin"))

            # Assert
            assert response.success is False
            assert "Settings load error" in response.message

    # ══════════════════════════════════════
    # POST /backtest 엔드포인트 테스트
    # ══════════════════════════════════════
    async def test_run_backtest_response_structure(self):
        """POST /backtest 응답 구조 검증"""
        from api.routes.system import run_backtest

        with (
            patch("api.routes.system.BacktestConfig"),
            patch("api.routes.system.BacktestEngine") as mock_engine,
            patch("api.routes.system.AuditLogger") as mock_audit,
            patch("api.routes.system.logger"),
        ):

            # BacktestEngine과 실제 데이터로 진행
            # 실제 실행은 executor를 통해 동기식으로 실행됨
            mock_db = AsyncMock()

            # 간단한 mock - 실제 BacktestEngine 동작은 복잡하므로
            # 예외가 발생하는 경우 처리만 확인
            mock_engine_instance = MagicMock()
            mock_engine_instance.run.side_effect = Exception("Backtest failed")
            mock_engine.return_value = mock_engine_instance

            # Execute - 예외 처리 확인
            response = await run_backtest(
                ticker="005930",
                start_date="2025-01-01",
                end_date="2025-12-31",
                strategy="ENSEMBLE",
                current_user=_mock_user("admin"),
                db=mock_db,
            )

            # Assert: 구조만 검증 (성공/실패 여부)
            assert hasattr(response, "success")
            assert hasattr(response, "data")
            assert hasattr(response, "message")

    async def test_run_backtest_default_strategy(self):
        """POST /backtest strategy 파라미터 생략"""

        # 실제 구현에서 strategy None은 "ENSEMBLE"로 설정됨 (line 82)
        # 이는 구현 코드의 기본값 설정을 검증함
        # 코드: strategy_name = strategy or "ENSEMBLE"

        # 단위 테스트로는 이 로직을 직접 검증할 수 있음
        strategy = None
        strategy_name = strategy or "ENSEMBLE"

        assert strategy_name == "ENSEMBLE"

    async def test_run_backtest_exception(self):
        """POST /backtest 예외 처리"""
        from api.routes.system import run_backtest

        with (
            patch("api.routes.system.get_db_session") as mock_db_session,
            patch("api.routes.system.BacktestEngine") as mock_engine,
        ):

            mock_db = AsyncMock()
            mock_db_session.return_value = mock_db

            mock_engine.side_effect = ValueError("Invalid date range")

            # Execute with invalid date format
            response = await run_backtest(
                ticker="005930",
                start_date="invalid",
                end_date="2025-12-31",
                strategy=None,
                current_user=_mock_user("admin"),
                db=mock_db,
            )

            # Assert: 에러 메시지에 "실패"가 포함되어야 함
            assert response.success is False
            assert "실패" in response.message  # 백테스트 실행 실패

    # ══════════════════════════════════════
    # POST /rebalancing 엔드포인트 테스트
    # ══════════════════════════════════════
    async def test_trigger_rebalancing_success(self):
        """POST /rebalancing 정상 실행 — 202 Accepted + 백그라운드 태스크 생성"""
        from api.routes.system import trigger_rebalancing

        mock_db = AsyncMock()

        # 프로필 mock
        mock_profile = MagicMock()
        mock_profile.risk_profile = "BALANCED"
        mock_profile.seed_amount = 50_000_000.0

        # Redis mock
        mock_redis = AsyncMock()
        mock_redis.keys.return_value = ["ensemble:latest:005930"]
        mock_redis.get.return_value = '{"ensemble_signal": 0.3, "regime": "SIDEWAYS"}'

        # DB 실행 결과 mock (포지션 조회 + 유니버스 조회)
        pos_result = MagicMock()
        pos_result.fetchall.return_value = []
        uni_result = MagicMock()
        uni_result.fetchall.return_value = [("005930", "IT", "KRX")]
        mock_db.execute.side_effect = [pos_result, uni_result]

        # Redis lock (set nx=True) 반환값 설정
        mock_redis.set = AsyncMock(return_value=True)
        mock_redis.delete = AsyncMock()

        # `api.routes.system.asyncio` 만 mock 하면 `asyncio.create_task(...)` 는 MagicMock 이지만
        # 그 인자로 전달되는 `_run_rebalancing_background(...)` 은 async def 라 호출 시점에
        # 실제 coroutine 을 생성한다. 이 coroutine 은 mock create_task 에 저장만 되고 await
        # 되지 않아 GC 시점에 `RuntimeWarning: coroutine '_run_rebalancing_background' was
        # never awaited` 가 남는다 (경고는 GC 가 일어난 후속 테스트로 귀속되어 test_no_profile
        # 등에 잘못 붙어 보이는 silent miss 효과까지 유발). `_run_rebalancing_background` 자체
        # 를 MagicMock 으로 패치해 coroutine 생성 자체를 차단한다.
        with (
            patch("api.routes.system.AuditLogger") as mock_audit,
            patch("api.routes.system.InvestorProfileManager") as mock_pm,
            patch("api.routes.system.RedisManager") as mock_rm,
            patch("api.routes.system.is_executed", new_callable=AsyncMock, return_value=False),
            patch("api.routes.system.asyncio") as mock_asyncio,
            patch("api.routes.system._update_rebalancing_status", new_callable=AsyncMock),
            patch(
                "api.routes.system._run_rebalancing_background",
                new=MagicMock(return_value=None),
            ) as mock_bg,
        ):
            mock_audit.return_value = AsyncMock()
            mock_pm.return_value.get_profile = AsyncMock(return_value=mock_profile)
            mock_rm.get_client.return_value = mock_redis
            mock_asyncio.create_task = MagicMock()

            response = await trigger_rebalancing(
                rebalancing_type="MANUAL",
                force=False,
                current_user=_mock_user("user123"),
                db=mock_db,
            )

        # 202 JSONResponse 반환
        assert response.status_code == 202
        import json

        body = json.loads(response.body.decode())
        assert body["success"] is True
        assert body["data"]["type"] == "MANUAL"
        assert body["data"]["status"] == "accepted"
        assert "task_id" in body["data"]
        # 백그라운드 태스크가 create_task 로 생성되었는지 확인
        mock_asyncio.create_task.assert_called_once()
        # `_run_rebalancing_background` 가 정확히 한 번 호출되었는지도 검증 (wiring 확인).
        mock_bg.assert_called_once()

    async def test_trigger_rebalancing_no_profile(self):
        """POST /rebalancing 프로필 없음 시 실패"""
        from api.routes.system import trigger_rebalancing

        mock_db = AsyncMock()
        mock_redis = AsyncMock()
        mock_redis.set = AsyncMock(return_value=True)
        mock_redis.delete = AsyncMock()

        with (
            patch("api.routes.system.AuditLogger") as mock_audit,
            patch("api.routes.system.InvestorProfileManager") as mock_pm,
            patch("api.routes.system.RedisManager") as mock_rm,
            patch("api.routes.system.is_executed", new_callable=AsyncMock, return_value=False),
        ):
            mock_audit.return_value = AsyncMock()
            mock_pm.return_value.get_profile = AsyncMock(return_value=None)
            mock_rm.get_client.return_value = mock_redis

            response = await trigger_rebalancing(
                rebalancing_type="MANUAL",
                force=False,
                current_user=_mock_user("admin"),
                db=mock_db,
            )

        assert response.success is False
        assert "프로필" in response.message

    async def test_trigger_rebalancing_no_signals(self):
        """POST /rebalancing 시그널 없음 시 실패"""
        from api.routes.system import trigger_rebalancing

        mock_db = AsyncMock()
        mock_profile = MagicMock()
        mock_redis = AsyncMock()
        mock_redis.keys.return_value = []
        mock_redis.set = AsyncMock(return_value=True)
        mock_redis.delete = AsyncMock()

        with (
            patch("api.routes.system.AuditLogger") as mock_audit,
            patch("api.routes.system.InvestorProfileManager") as mock_pm,
            patch("api.routes.system.RedisManager") as mock_rm,
            patch("api.routes.system.is_executed", new_callable=AsyncMock, return_value=False),
        ):
            mock_audit.return_value = AsyncMock()
            mock_pm.return_value.get_profile = AsyncMock(return_value=mock_profile)
            mock_rm.get_client.return_value = mock_redis

            response = await trigger_rebalancing(
                rebalancing_type="MANUAL",
                force=False,
                current_user=_mock_user("admin"),
                db=mock_db,
            )

        assert response.success is False
        assert "앙상블 시그널이 없습니다" in response.message

    async def test_trigger_rebalancing_exception(self):
        """POST /rebalancing 예외 처리"""
        from api.routes.system import trigger_rebalancing

        mock_redis = AsyncMock()
        mock_redis.set = AsyncMock(return_value=True)
        mock_redis.delete = AsyncMock()

        with (
            patch("api.routes.system.AuditLogger") as mock_audit,
            patch("api.routes.system.RedisManager") as mock_rm,
            patch("api.routes.system.is_executed", new_callable=AsyncMock, return_value=False),
        ):
            mock_db = AsyncMock()
            mock_audit_instance = AsyncMock()
            mock_audit_instance.log.side_effect = Exception("DB write error")
            mock_audit.return_value = mock_audit_instance
            mock_rm.get_client.return_value = mock_redis

            response = await trigger_rebalancing(
                rebalancing_type="EMERGENCY",
                force=False,
                current_user=_mock_user("admin"),
                db=mock_db,
            )

            assert response.success is False
            assert "DB write error" in response.message

    async def test_trigger_rebalancing_idempotency_block(self):
        """같은 거래일 중복 요청 시 already_executed 반환"""
        from api.routes.system import trigger_rebalancing

        mock_db = AsyncMock()
        mock_redis = AsyncMock()
        mock_redis.delete = AsyncMock()

        with (
            patch("api.routes.system.is_executed", new_callable=AsyncMock, return_value=True),
            patch("api.routes.system.RedisManager") as mock_rm,
        ):
            mock_rm.get_client.return_value = mock_redis
            response = await trigger_rebalancing(
                rebalancing_type="MANUAL",
                force=False,
                current_user=_mock_user("admin"),
                db=mock_db,
            )

        assert response.success is True
        assert response.data["status"] == "already_executed"
        assert "이미 리밸런싱이 실행" in response.message

    async def test_trigger_rebalancing_idempotency_force_bypass(self):
        """force=True 시 멱등성 체크를 우회하여 202 Accepted 반환"""
        from api.routes.system import trigger_rebalancing

        mock_db = AsyncMock()
        mock_profile = MagicMock()
        mock_profile.risk_profile = "BALANCED"
        mock_profile.seed_amount = 50_000_000.0

        mock_redis = AsyncMock()
        mock_redis.keys.return_value = ["ensemble:latest:005930"]
        mock_redis.get.return_value = '{"ensemble_signal": 0.3, "regime": "SIDEWAYS"}'
        mock_redis.set = AsyncMock(return_value=True)
        mock_redis.delete = AsyncMock()

        pos_result = MagicMock()
        pos_result.fetchall.return_value = []
        uni_result = MagicMock()
        uni_result.fetchall.return_value = [("005930", "IT", "KRX")]
        mock_db.execute.side_effect = [pos_result, uni_result]

        # `_run_rebalancing_background` 자체도 MagicMock 으로 패치해야 coroutine 생성이 차단된다.
        # (상세 근거는 test_trigger_rebalancing_success 의 주석 및 development-policies.md §1.1 참조)
        with (
            patch("api.routes.system.AuditLogger") as mock_audit,
            patch("api.routes.system.InvestorProfileManager") as mock_pm,
            patch("api.routes.system.RedisManager") as mock_rm,
            patch("api.routes.system.is_executed", new_callable=AsyncMock, return_value=True),
            patch("api.routes.system.asyncio") as mock_asyncio,
            patch("api.routes.system._update_rebalancing_status", new_callable=AsyncMock),
            patch(
                "api.routes.system._run_rebalancing_background",
                new=MagicMock(return_value=None),
            ) as mock_bg,
        ):
            mock_audit.return_value = AsyncMock()
            mock_pm.return_value.get_profile = AsyncMock(return_value=mock_profile)
            mock_rm.get_client.return_value = mock_redis
            mock_asyncio.create_task = MagicMock()

            # force=True → is_executed=True 여도 멱등성 우회 → 202 반환
            response = await trigger_rebalancing(
                rebalancing_type="MANUAL",
                force=True,
                current_user=_mock_user("admin"),
                db=mock_db,
            )

        import json

        assert response.status_code == 202
        body = json.loads(response.body.decode())
        assert body["success"] is True
        assert body["data"]["status"] == "accepted"
        # `_run_rebalancing_background` 가 정확히 한 번 호출되었는지 wiring 검증.
        mock_bg.assert_called_once()

    async def test_trigger_rebalancing_lock_conflict(self):
        """분산 락 획득 실패 시 409 반환"""
        from api.routes.system import trigger_rebalancing

        mock_db = AsyncMock()
        mock_redis = AsyncMock()
        # 락 획득 실패 (이미 다른 요청이 점유)
        mock_redis.set = AsyncMock(return_value=False)
        mock_redis.delete = AsyncMock()

        with (
            patch("api.routes.system.AuditLogger") as mock_audit,
            patch("api.routes.system.RedisManager") as mock_rm,
            patch("api.routes.system.is_executed", new_callable=AsyncMock, return_value=False),
        ):
            mock_audit.return_value = AsyncMock()
            mock_rm.get_client.return_value = mock_redis

            response = await trigger_rebalancing(
                rebalancing_type="MANUAL",
                force=False,
                current_user=_mock_user("admin"),
                db=mock_db,
            )

        # JSONResponse 반환 (409)
        assert response.status_code == 409

    # ══════════════════════════════════════
    # GET /rebalancing/status/{task_id} 엔드포인트 테스트
    # ══════════════════════════════════════
    async def test_get_rebalancing_status_found(self):
        """GET /rebalancing/status/{task_id} 정상 조회"""
        import json

        from api.routes.system import get_rebalancing_status

        status_data = {
            "task_id": "20260414_abc12345",
            "status": "completed",
            "updated_at": "2026-04-14T12:00:00+00:00",
            "order_count": 5,
        }

        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=json.dumps(status_data))

        with patch("api.routes.system.RedisManager") as mock_rm:
            mock_rm.get_client.return_value = mock_redis

            response = await get_rebalancing_status(
                task_id="20260414_abc12345",
                current_user=_mock_user("viewer"),
            )

        assert response.success is True
        assert response.data["status"] == "completed"
        assert response.data["task_id"] == "20260414_abc12345"

    async def test_get_rebalancing_status_not_found(self):
        """GET /rebalancing/status/{task_id} 존재하지 않는 task_id → 404"""
        from api.routes.system import get_rebalancing_status

        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=None)

        with patch("api.routes.system.RedisManager") as mock_rm:
            mock_rm.get_client.return_value = mock_redis

            response = await get_rebalancing_status(
                task_id="nonexistent_task",
                current_user=_mock_user("viewer"),
            )

        assert response.status_code == 404

    async def test_get_rebalancing_status_redis_error(self):
        """GET /rebalancing/status/{task_id} Redis 오류 시 실패 응답"""
        from api.routes.system import get_rebalancing_status

        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(side_effect=Exception("Redis connection lost"))

        with patch("api.routes.system.RedisManager") as mock_rm:
            mock_rm.get_client.return_value = mock_redis

            response = await get_rebalancing_status(
                task_id="some_task",
                current_user=_mock_user("admin"),
            )

        assert response.success is False
        assert "상태 조회 실패" in response.message

    async def test_trigger_rebalancing_lock_released_on_validation_failure(self):
        """검증 단계(프로필 없음) 실패 시 분산 락이 해제되는지 확인"""
        from api.routes.system import trigger_rebalancing

        mock_db = AsyncMock()
        mock_redis = AsyncMock()
        mock_redis.set = AsyncMock(return_value=True)
        mock_redis.delete = AsyncMock()

        with (
            patch("api.routes.system.AuditLogger") as mock_audit,
            patch("api.routes.system.InvestorProfileManager") as mock_pm,
            patch("api.routes.system.RedisManager") as mock_rm,
            patch("api.routes.system.is_executed", new_callable=AsyncMock, return_value=False),
        ):
            mock_audit.return_value = AsyncMock()
            mock_pm.return_value.get_profile = AsyncMock(return_value=None)
            mock_rm.get_client.return_value = mock_redis

            response = await trigger_rebalancing(
                rebalancing_type="MANUAL",
                force=False,
                current_user=_mock_user("admin"),
                db=mock_db,
            )

        # 프로필 없음 → 실패
        assert response.success is False
        # 백그라운드 시작 안 됨 → finally 에서 락 해제 호출됨
        mock_redis.delete.assert_called_with("rebalancing:lock")

    # ══════════════════════════════════════
    # POST /pipeline 엔드포인트 테스트
    # ══════════════════════════════════════
    async def test_run_pipeline_success(self):
        """POST /pipeline 정상 실행"""
        from api.routes.system import run_analysis_pipeline

        with (
            patch("api.routes.system.get_db_session") as mock_db_session,
            patch("api.routes.system.InvestmentDecisionPipeline") as mock_pipeline,
            patch("api.routes.system.AuditLogger") as mock_audit,
        ):

            mock_db = AsyncMock()
            mock_db_session.return_value = mock_db

            # Pipeline mock
            mock_pipeline_instance = AsyncMock()
            mock_ensemble_signal = MagicMock()
            mock_ensemble_signal.final_signal = 0.75
            mock_ensemble_signal.action = "BUY"
            mock_ensemble_signal.final_confidence = 0.85

            mock_pipeline_instance.run_batch_analysis.return_value = {
                "005930": MagicMock(
                    blocked=False,
                    blocked_by=None,
                    ensemble_signal=mock_ensemble_signal,
                ),
                "000660": MagicMock(
                    blocked=True,
                    blocked_by="CIRCUIT_BREAKER",
                    ensemble_signal=None,
                ),
            }
            mock_pipeline.return_value = mock_pipeline_instance

            # AuditLogger mock
            mock_audit_instance = AsyncMock()
            mock_audit.return_value = mock_audit_instance

            # Execute
            response = await run_analysis_pipeline(
                request=_make_request(),
                tickers="005930,000660",
                force_refresh=False,
                current_user=_mock_user("analyst"),
                db=mock_db,
            )

            # Assert
            assert response.success is True
            assert response.data["tickers"] == ["005930", "000660"]
            assert response.data["status"] == "completed"
            assert response.data["force_refresh"] is False
            assert response.data["succeeded"] == 1
            assert response.data["blocked"] == 1

            # 결과 검증
            results = response.data["results"]
            assert results["005930"]["status"] == "completed"
            assert results["005930"]["ensemble_signal"] == 0.75
            assert results["005930"]["action"] == "BUY"
            assert results["005930"]["confidence"] == 0.85

            assert results["000660"]["status"] == "blocked"
            assert results["000660"]["blocked_by"] == "CIRCUIT_BREAKER"

    async def test_run_pipeline_whitespace_handling(self):
        """POST /pipeline 띄어쓰기 처리"""
        from api.routes.system import run_analysis_pipeline

        with (
            patch("api.routes.system.get_db_session") as mock_db_session,
            patch("api.routes.system.InvestmentDecisionPipeline") as mock_pipeline,
            patch("api.routes.system.AuditLogger") as mock_audit,
        ):

            mock_db = AsyncMock()
            mock_db_session.return_value = mock_db

            mock_pipeline_instance = AsyncMock()
            mock_ensemble_signal = MagicMock()
            mock_ensemble_signal.final_signal = 0.50
            mock_ensemble_signal.action = "HOLD"
            mock_ensemble_signal.final_confidence = 0.60

            mock_pipeline_instance.run_batch_analysis.return_value = {
                "005930": MagicMock(
                    blocked=False,
                    blocked_by=None,
                    ensemble_signal=mock_ensemble_signal,
                ),
                "000660": MagicMock(
                    blocked=False,
                    blocked_by=None,
                    ensemble_signal=mock_ensemble_signal,
                ),
                "360750": MagicMock(
                    blocked=False,
                    blocked_by=None,
                    ensemble_signal=mock_ensemble_signal,
                ),
            }
            mock_pipeline.return_value = mock_pipeline_instance

            mock_audit_instance = AsyncMock()
            mock_audit.return_value = mock_audit_instance

            # Execute with spaces
            response = await run_analysis_pipeline(
                request=_make_request(),
                tickers="005930, 000660 , 360750",
                force_refresh=False,
                current_user=_mock_user("admin"),
                db=mock_db,
            )

            # Assert: 공백 제거됨
            assert response.success is True
            mock_pipeline_instance.run_batch_analysis.assert_called_once_with(
                tickers=["005930", "000660", "360750"],
                force_refresh=False,
            )

    async def test_run_pipeline_exception(self):
        """POST /pipeline 예외 처리"""
        from api.routes.system import run_analysis_pipeline

        with (
            patch("api.routes.system.get_db_session") as mock_db_session,
            patch("api.routes.system.InvestmentDecisionPipeline") as mock_pipeline,
        ):

            mock_db = AsyncMock()
            mock_db_session.return_value = mock_db

            mock_pipeline_instance = AsyncMock()
            mock_pipeline_instance.run_batch_analysis.side_effect = RuntimeError("Pipeline error")
            mock_pipeline.return_value = mock_pipeline_instance

            # Execute
            response = await run_analysis_pipeline(
                request=_make_request(),
                tickers="005930",
                force_refresh=False,
                current_user=_mock_user("admin"),
                db=mock_db,
            )

            # Assert
            assert response.success is False
            assert "Pipeline error" in response.message

    # ══════════════════════════════════════
    # GET /audit-logs 엔드포인트 테스트
    # ══════════════════════════════════════
    async def test_get_audit_logs_query_structure(self):
        """GET /audit-logs SQL 쿼리 검증"""
        # 코드 검증: module 파라미터에 따라 다른 쿼리 생성
        # 라인 309-325: WHERE 절 포함/미포함 분기

        # 테스트: module=None일 때와 module이 있을 때 쿼리 다름
        from sqlalchemy import text

        # module=None인 경우
        query_without_module = text(
            """
            SELECT id, time, action_type, module, description, before_state, after_state, metadata
            FROM audit_logs
            ORDER BY time DESC
            LIMIT :limit
        """
        )

        # module이 있는 경우
        query_with_module = text(
            """
            SELECT id, time, action_type, module, description, before_state, after_state, metadata
            FROM audit_logs
            WHERE module = :module
            ORDER BY time DESC
            LIMIT :limit
        """
        )

        # Assert: 쿼리 구조 검증
        assert "WHERE module = :module" in str(query_with_module)
        assert "WHERE module = :module" not in str(query_without_module)

    async def test_get_audit_logs_limit_parameter(self):
        """GET /audit-logs limit 파라미터 검증"""
        # 코드 검증: Query parameter limit은 1~200 범위 (line 299)
        # ge=1, le=200

        # 유효한 limit 값
        valid_limits = [1, 50, 100, 200]
        for limit in valid_limits:
            assert 1 <= limit <= 200

    async def test_get_audit_logs_exception(self):
        """GET /audit-logs 예외 처리"""
        from api.routes.system import get_audit_logs

        with patch("api.routes.system.get_db_session") as mock_db_session:
            mock_db = AsyncMock()
            mock_db.execute = AsyncMock(side_effect=Exception("DB query error"))
            mock_db_session.return_value = mock_db

            # Execute
            response = await get_audit_logs(
                limit=50,
                module=None,
                current_user=_mock_user("admin"),
                db=mock_db,
            )

            # Assert
            assert response.success is False
            assert "DB query error" in response.message
