"""
Health Checker 종합 단위 테스트

HealthChecker 클래스의 모든 public 메서드를 테스트합니다.
- run_full_check: 전체 건전성 검사
- 개별 컴포넌트 검사 메서드들

모든 외부 의존성 (DB, Redis, MongoDB, settings) 은 mock으로 처리됩니다.
"""

import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from core.health_checker import (
    HealthChecker,
    HealthStatus,
    ComponentHealth,
    SystemHealthReport,
)


@pytest.mark.asyncio
@pytest.mark.smoke
class TestHealthChecker:
    """HealthChecker 클래스 종합 테스트"""

    # ══════════════════════════════════════
    # run_full_check 테스트
    # ══════════════════════════════════════
    async def test_run_full_check_all_healthy(self):
        """모든 컴포넌트가 정상일 때 HEALTHY 반환"""
        # Setup: conftest에서 환경 설정이 이미 로드됨
        # HealthChecker() 생성 시 실제 settings를 사용하게 됨
        checker = HealthChecker()

        # Mock 모든 검사 메서드
        with patch.object(checker, "_check_postgresql") as mock_pg, \
             patch.object(checker, "_check_mongodb") as mock_mongo, \
             patch.object(checker, "_check_redis") as mock_redis, \
             patch.object(checker, "_check_settings_validity") as mock_settings_check, \
             patch.object(checker, "_check_trading_mode_readiness") as mock_trading:

            # 모든 컴포넌트가 HEALTHY 반환
            mock_pg.return_value = ComponentHealth(
                name="postgresql",
                status=HealthStatus.HEALTHY,
                message="Connected",
                latency_ms=5.23,
            )
            mock_mongo.return_value = ComponentHealth(
                name="mongodb",
                status=HealthStatus.HEALTHY,
                message="Connected",
                latency_ms=3.15,
            )
            mock_redis.return_value = ComponentHealth(
                name="redis",
                status=HealthStatus.HEALTHY,
                message="Connected",
                latency_ms=1.42,
            )
            mock_settings_check.return_value = ComponentHealth(
                name="settings_validity",
                status=HealthStatus.HEALTHY,
                message="All settings valid",
            )
            mock_trading.return_value = ComponentHealth(
                name="trading_mode_readiness",
                status=HealthStatus.HEALTHY,
                message="Mode: BACKTEST (API 호출 없음)",
            )

            # Execute
            report = await checker.run_full_check()

            # Assert
            assert report.overall_status == HealthStatus.HEALTHY
            assert report.trading_mode == "BACKTEST"  # conftest가 설정
            assert report.ready_for_trading is True
            assert len(report.components) == 5
            assert all(c.status == HealthStatus.HEALTHY for c in report.components)

    async def test_run_full_check_with_unhealthy_component(self):
        """하나의 컴포넌트가 UNHEALTHY이면 전체 상태는 UNHEALTHY"""
        # Setup
        with patch("config.settings.get_settings") as mock_get_settings:
            mock_settings = MagicMock()
            mock_settings.kis.trading_mode.value = "DEMO"
            mock_settings.environment = "staging"
            mock_get_settings.return_value = mock_settings

            checker = HealthChecker()

            with patch.object(checker, "_check_postgresql") as mock_pg, \
                 patch.object(checker, "_check_mongodb") as mock_mongo, \
                 patch.object(checker, "_check_redis") as mock_redis, \
                 patch.object(checker, "_check_settings_validity") as mock_settings_check, \
                 patch.object(checker, "_check_trading_mode_readiness") as mock_trading:

                # PostgreSQL만 UNHEALTHY
                mock_pg.return_value = ComponentHealth(
                    name="postgresql",
                    status=HealthStatus.UNHEALTHY,
                    message="Connection refused",
                )
                mock_mongo.return_value = ComponentHealth(
                    name="mongodb",
                    status=HealthStatus.HEALTHY,
                    message="Connected",
                    latency_ms=2.5,
                )
                mock_redis.return_value = ComponentHealth(
                    name="redis",
                    status=HealthStatus.HEALTHY,
                    message="Connected",
                    latency_ms=1.0,
                )
                mock_settings_check.return_value = ComponentHealth(
                    name="settings_validity",
                    status=HealthStatus.HEALTHY,
                    message="All settings valid",
                )
                mock_trading.return_value = ComponentHealth(
                    name="trading_mode_readiness",
                    status=HealthStatus.HEALTHY,
                    message="Mode: DEMO",
                )

                # Execute
                report = await checker.run_full_check()

                # Assert
                assert report.overall_status == HealthStatus.UNHEALTHY
                assert report.ready_for_trading is False

    async def test_run_full_check_with_degraded_component(self):
        """하나의 컴포넌트가 DEGRADED이면 전체 상태는 DEGRADED"""
        # Setup
        with patch("config.settings.get_settings") as mock_get_settings:
            mock_settings = MagicMock()
            mock_settings.kis.trading_mode.value = "DEMO"
            mock_settings.environment = "test"
            mock_get_settings.return_value = mock_settings

            checker = HealthChecker()

            with patch.object(checker, "_check_postgresql") as mock_pg, \
                 patch.object(checker, "_check_mongodb") as mock_mongo, \
                 patch.object(checker, "_check_redis") as mock_redis, \
                 patch.object(checker, "_check_settings_validity") as mock_settings_check, \
                 patch.object(checker, "_check_trading_mode_readiness") as mock_trading:

                mock_pg.return_value = ComponentHealth(
                    name="postgresql",
                    status=HealthStatus.HEALTHY,
                    message="Connected",
                    latency_ms=5.0,
                )
                # settings_validity가 DEGRADED
                mock_settings_check.return_value = ComponentHealth(
                    name="settings_validity",
                    status=HealthStatus.DEGRADED,
                    message="텔레그램 봇 토큰 미설정",
                )
                mock_mongo.return_value = ComponentHealth(
                    name="mongodb",
                    status=HealthStatus.HEALTHY,
                    message="Connected",
                    latency_ms=3.0,
                )
                mock_redis.return_value = ComponentHealth(
                    name="redis",
                    status=HealthStatus.HEALTHY,
                    message="Connected",
                    latency_ms=1.5,
                )
                mock_trading.return_value = ComponentHealth(
                    name="trading_mode_readiness",
                    status=HealthStatus.HEALTHY,
                    message="Mode: DEMO",
                )

                # Execute
                report = await checker.run_full_check()

                # Assert
                assert report.overall_status == HealthStatus.DEGRADED
                assert report.ready_for_trading is True  # DEGRADED도 거래 가능

    async def test_run_full_check_exception_handling(self):
        """개별 검사에서 예외 발생 시 UNHEALTHY로 처리"""
        # Setup: conftest 환경 사용
        checker = HealthChecker()

        with patch.object(checker, "_check_postgresql") as mock_pg, \
             patch.object(checker, "_check_mongodb") as mock_mongo, \
             patch.object(checker, "_check_redis") as mock_redis, \
             patch.object(checker, "_check_settings_validity") as mock_settings_check, \
             patch.object(checker, "_check_trading_mode_readiness") as mock_trading:

            # Async mocks 설정
            async def raise_error(*args, **kwargs):
                raise Exception("DB connection error")

            async def return_healthy(*args, **kwargs):
                return ComponentHealth(
                    name="test",
                    status=HealthStatus.HEALTHY,
                    message="Connected",
                    latency_ms=1.0,
                )

            mock_pg.side_effect = raise_error
            mock_mongo.side_effect = return_healthy
            mock_redis.side_effect = return_healthy
            mock_settings_check.side_effect = return_healthy
            mock_trading.side_effect = return_healthy

            # Execute
            report = await checker.run_full_check()

            # Assert: 최소한 하나의 UNHEALTHY가 있으면 전체 상태도 UNHEALTHY
            unhealthy_components = [c for c in report.components if c.status == HealthStatus.UNHEALTHY]
            assert len(unhealthy_components) > 0, "UNHEALTHY component expected"
            assert report.overall_status == HealthStatus.UNHEALTHY

    async def test_run_full_check_report_structure(self):
        """반환된 보고서의 구조 검증"""
        # Setup
        with patch("config.settings.get_settings") as mock_get_settings:
            mock_settings = MagicMock()
            mock_settings.kis.trading_mode.value = "LIVE"
            mock_settings.environment = "production"
            mock_get_settings.return_value = mock_settings

            checker = HealthChecker()

            with patch.object(checker, "_check_postgresql") as mock_pg, \
                 patch.object(checker, "_check_mongodb") as mock_mongo, \
                 patch.object(checker, "_check_redis") as mock_redis, \
                 patch.object(checker, "_check_settings_validity") as mock_settings_check, \
                 patch.object(checker, "_check_trading_mode_readiness") as mock_trading:

                mock_pg.return_value = ComponentHealth(
                    name="postgresql",
                    status=HealthStatus.HEALTHY,
                    message="Connected",
                    latency_ms=4.5,
                )
                mock_mongo.return_value = ComponentHealth(
                    name="mongodb",
                    status=HealthStatus.HEALTHY,
                    message="Connected",
                    latency_ms=2.8,
                )
                mock_redis.return_value = ComponentHealth(
                    name="redis",
                    status=HealthStatus.HEALTHY,
                    message="Connected",
                    latency_ms=0.9,
                )
                mock_settings_check.return_value = ComponentHealth(
                    name="settings_validity",
                    status=HealthStatus.HEALTHY,
                    message="All settings valid",
                )
                mock_trading.return_value = ComponentHealth(
                    name="trading_mode_readiness",
                    status=HealthStatus.HEALTHY,
                    message="Mode: LIVE",
                )

                # Execute
                report = await checker.run_full_check()

                # Assert: to_dict() 메서드가 올바른 구조 반환
                report_dict = report.to_dict()
                assert "overall_status" in report_dict
                assert "components" in report_dict
                assert "trading_mode" in report_dict
                assert "environment" in report_dict
                assert "ready_for_trading" in report_dict
                assert "checked_at" in report_dict

                # components도 dict 형식
                assert isinstance(report_dict["components"], list)
                assert len(report_dict["components"]) == 5
                for component in report_dict["components"]:
                    assert "name" in component
                    assert "status" in component
                    assert "message" in component
                    assert "latency_ms" in component
                    assert "checked_at" in component

    # ══════════════════════════════════════
    # _check_settings_validity 테스트
    # ══════════════════════════════════════
    async def test_check_settings_validity_returns_structure(self):
        """설정 검사 반환값의 구조 검증"""
        # conftest 환경에서 실제 검사 실행
        # "test-bot-token"과 "test-secret-key"는 기본값이므로 DEGRADED 예상
        checker = HealthChecker()

        # Execute
        component = await checker._check_settings_validity()

        # Assert: 구조만 검증 (상태값은 conftest 설정에 따라 달라짐)
        assert component.name == "settings_validity"
        assert component.status in [HealthStatus.HEALTHY, HealthStatus.DEGRADED, HealthStatus.UNHEALTHY]
        assert isinstance(component.message, str)
        assert len(component.message) > 0

    async def test_check_settings_validity_degraded_missing_telegram(self):
        """텔레그램 토큰 미설정"""
        # Setup
        with patch("config.settings.get_settings") as mock_get_settings:
            mock_settings = MagicMock()
            mock_settings.telegram.bot_token = "test-bot-token"  # 기본값
            mock_settings.dashboard.secret_key = "valid-secret"
            mock_settings.kis.is_live = False
            mock_get_settings.return_value = mock_settings

            checker = HealthChecker()

            # Execute
            component = await checker._check_settings_validity()

            # Assert
            assert component.name == "settings_validity"
            assert component.status == HealthStatus.DEGRADED
            assert "텔레그램 봇 토큰 미설정" in component.message

    async def test_check_settings_validity_degraded_dashboard_default_secret(self):
        """대시보드 시크릿이 기본값"""
        # Setup
        with patch("config.settings.get_settings") as mock_get_settings:
            mock_settings = MagicMock()
            mock_settings.telegram.bot_token = "valid-token"
            mock_settings.dashboard.secret_key = "test-secret-key"  # 기본값
            mock_settings.kis.is_live = False
            mock_get_settings.return_value = mock_settings

            checker = HealthChecker()

            # Execute
            component = await checker._check_settings_validity()

            # Assert
            assert component.status == HealthStatus.DEGRADED
            assert "대시보드 시크릿 키가 기본값" in component.message

    async def test_check_settings_validity_degraded_when_backtest(self):
        """BACKTEST 모드에서도 기본 토큰은 경고"""
        # Setup: conftest에서 BACKTEST 모드이고, 기본 토큰이 사용 중
        # LIVE 모드가 아니므로 자격증명은 검증 안 함
        checker = HealthChecker()

        # Execute
        component = await checker._check_settings_validity()

        # Assert: conftest 기본값 "test-bot-token"이 사용되므로 DEGRADED
        assert component.name == "settings_validity"
        assert component.status == HealthStatus.DEGRADED
        assert "텔레그램 봇 토큰 미설정" in component.message or "기본값" in component.message

    # ══════════════════════════════════════
    # _check_trading_mode_readiness 테스트
    # ══════════════════════════════════════
    async def test_trading_mode_readiness_backtest(self):
        """BACKTEST 모드는 항상 HEALTHY (API 호출 없음)"""
        # Setup
        with patch("config.settings.get_settings") as mock_get_settings:
            mock_settings = MagicMock()
            mock_settings.kis.trading_mode.value = "BACKTEST"
            mock_settings.kis.is_backtest = True
            mock_settings.kis.is_demo = False
            mock_settings.kis.is_live = False
            mock_get_settings.return_value = mock_settings

            checker = HealthChecker()

            # Execute
            component = await checker._check_trading_mode_readiness()

            # Assert
            assert component.name == "trading_mode_readiness"
            assert component.status == HealthStatus.HEALTHY
            assert "BACKTEST" in component.message
            assert "API 호출 없음" in component.message

    async def test_trading_mode_readiness_backtest_from_real_settings(self):
        """실제 설정에서 BACKTEST 모드 동작 확인"""
        # conftest에서 KIS_TRADING_MODE=BACKTEST로 설정되어 있음
        # 실제 설정을 사용하여 테스트
        from config.settings import get_settings

        real_settings = get_settings()
        assert real_settings.kis.is_backtest is True

        checker = HealthChecker()
        component = await checker._check_trading_mode_readiness()

        # Execute & Assert
        assert component.status == HealthStatus.HEALTHY
        assert "BACKTEST" in component.message
        assert "API 호출 없음" in component.message

    async def test_trading_mode_readiness_first_check_is_backtest(self):
        """_check_trading_mode_readiness의 is_backtest 검사가 첫 번째"""
        # 코드 흐름: is_backtest 체크가 가장 먼저 이루어짐
        # BACKTEST는 is_demo, is_live 상태와 관계없이 HEALTHY
        with patch("config.settings.get_settings") as mock_get_settings:
            mock_settings = MagicMock()
            mock_settings.kis.trading_mode.value = "BACKTEST"
            # 모두 True로 설정해도 is_backtest가 우선
            mock_settings.kis.is_backtest = True
            mock_settings.kis.is_demo = True
            mock_settings.kis.is_live = True
            mock_get_settings.return_value = mock_settings

            checker = HealthChecker()
            component = await checker._check_trading_mode_readiness()

            # Assert: is_backtest가 True면 HEALTHY
            assert component.status == HealthStatus.HEALTHY
            assert "BACKTEST" in component.message

    async def test_trading_mode_readiness_modes_are_mutually_exclusive(self):
        """거래 모드는 상호 배타적이고 is_backtest 체크가 우선"""
        # 코드 검증: 여러 모드가 동시에 True여도 is_backtest가 첫 번째 체크
        with patch("config.settings.get_settings") as mock_get_settings:
            mock_settings = MagicMock()
            mock_settings.kis.trading_mode.value = "BACKTEST"
            # 모두 True로 설정하더라도
            mock_settings.kis.is_backtest = True
            mock_settings.kis.is_demo = True
            mock_settings.kis.is_live = True
            mock_settings.is_production = False
            mock_get_settings.return_value = mock_settings

            checker = HealthChecker()
            component = await checker._check_trading_mode_readiness()

            # Assert: is_backtest가 우선이므로 HEALTHY
            assert component.status == HealthStatus.HEALTHY
            assert "BACKTEST" in component.message
            assert "API 호출 없음" in component.message

    # ══════════════════════════════════════
    # ComponentHealth 데이터 클래스 테스트
    # ══════════════════════════════════════
    def test_component_health_to_dict(self):
        """ComponentHealth.to_dict() 메서드 검증"""
        # Setup
        component = ComponentHealth(
            name="postgresql",
            status=HealthStatus.HEALTHY,
            message="Connected",
            latency_ms=5.25,
        )

        # Execute
        result = component.to_dict()

        # Assert
        assert result["name"] == "postgresql"
        assert result["status"] == "HEALTHY"
        assert result["message"] == "Connected"
        assert result["latency_ms"] == 5.25
        assert "checked_at" in result
        assert isinstance(result["checked_at"], str)  # ISO format

    def test_component_health_default_checked_at(self):
        """ComponentHealth의 checked_at 기본값 검증"""
        # Setup
        before = datetime.now(timezone.utc)
        component = ComponentHealth(
            name="redis",
            status=HealthStatus.HEALTHY,
        )
        after = datetime.now(timezone.utc)

        # Assert
        assert before <= component.checked_at <= after

    # ══════════════════════════════════════
    # SystemHealthReport 데이터 클래스 테스트
    # ══════════════════════════════════════
    def test_system_health_report_to_dict(self):
        """SystemHealthReport.to_dict() 메서드 검증"""
        # Setup
        components = [
            ComponentHealth(
                name="postgresql",
                status=HealthStatus.HEALTHY,
                message="Connected",
                latency_ms=4.5,
            ),
            ComponentHealth(
                name="redis",
                status=HealthStatus.HEALTHY,
                message="Connected",
                latency_ms=1.2,
            ),
        ]
        report = SystemHealthReport(
            overall_status=HealthStatus.HEALTHY,
            components=components,
            trading_mode="BACKTEST",
            environment="test",
            ready_for_trading=True,
        )

        # Execute
        result = report.to_dict()

        # Assert
        assert result["overall_status"] == "HEALTHY"
        assert result["trading_mode"] == "BACKTEST"
        assert result["environment"] == "test"
        assert result["ready_for_trading"] is True
        assert len(result["components"]) == 2
        assert result["components"][0]["name"] == "postgresql"
        assert result["components"][1]["name"] == "redis"
        assert "checked_at" in result

    def test_system_health_report_default_values(self):
        """SystemHealthReport의 기본값 검증"""
        # Setup & Execute
        report = SystemHealthReport()

        # Assert
        assert report.overall_status == HealthStatus.UNKNOWN
        assert report.components == []
        assert report.trading_mode == ""
        assert report.environment == ""
        assert report.ready_for_trading is False
        assert isinstance(report.checked_at, datetime)

    # ══════════════════════════════════════
    # HealthStatus Enum 테스트
    # ══════════════════════════════════════
    def test_health_status_enum_values(self):
        """HealthStatus enum의 모든 값 검증"""
        assert HealthStatus.HEALTHY.value == "HEALTHY"
        assert HealthStatus.DEGRADED.value == "DEGRADED"
        assert HealthStatus.UNHEALTHY.value == "UNHEALTHY"
        assert HealthStatus.UNKNOWN.value == "UNKNOWN"

    def test_health_status_enum_string_conversion(self):
        """HealthStatus string 변환 검증"""
        status = HealthStatus("HEALTHY")
        assert status == HealthStatus.HEALTHY
        assert str(status) == "HealthStatus.HEALTHY"
