"""
사용자 투자 프로필 관리 모듈 (F-05-01)

사용자 투자 성향, 목표 자본, 리밸런싱 주기 등을 관리하며,
포트폴리오 구성 및 리밸런싱에 필요한 프로필 정보를 제공합니다.

InvestorProfile: 사용자 투자 프로필 데이터 컨테이너
InvestorProfileManager: 비동기 프로필 생성/조회/갱신 엔진
"""

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import text

from config.constants import (
    ENSEMBLE_DEFAULT_WEIGHTS,
    HOLDING_PERIOD_MAP,
    InvestmentStyle,
    RebalancingFrequency,
    RiskProfile,
    StrategyType,
)
from config.logging import logger
from db.database import async_session_factory


# ══════════════════════════════════════
# 사용자 프로필 데이터 구조
# ══════════════════════════════════════
@dataclass
class InvestorProfile:
    """
    사용자 투자 프로필

    사용자의 투자 성향, 자본, 목표, 스타일, 손실 허용도,
    섹터 필터, 지정 종목, 리밸런싱 주기를 포함합니다.
    """

    user_id: str
    risk_profile: RiskProfile  # 투자 성향 (보수/균형/공격/배당)
    seed_amount: float  # 초기 자본 (원) — DB: seed_amount
    investment_goal: str  # 투자 목적 — DB: investment_goal
    investment_style: InvestmentStyle  # 투자 스타일 (일임형/자문형)
    loss_tolerance: float  # 손실 허용도 (%)
    sector_filter: list[str] = field(default_factory=list)  # 제외 섹터 목록 — DB: sector_filter (ARRAY)
    designated_tickers: list[str] = field(default_factory=list)  # 지정 종목 목록
    rebalancing_frequency: RebalancingFrequency = RebalancingFrequency.MONTHLY  # 리밸런싱 주기
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        """딕셔너리 변환 (DB 저장용)"""
        return {
            "user_id": self.user_id,
            "risk_profile": self.risk_profile.value,
            "seed_amount": self.seed_amount,
            "investment_goal": self.investment_goal,
            "investment_style": self.investment_style.value,
            "loss_tolerance": self.loss_tolerance,
            "sector_filter": self.sector_filter,
            "designated_tickers": self.designated_tickers,
            "rebalancing_frequency": self.rebalancing_frequency.value,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "InvestorProfile":
        """딕셔너리에서 생성"""
        # sector_filter: DB 는 ARRAY(Text) 타입이므로 이미 list 일 수 있다.
        sector_raw = data.get("sector_filter", [])
        if isinstance(sector_raw, str):
            sector_raw = json.loads(sector_raw)
        # designated_tickers: 동일
        tickers_raw = data.get("designated_tickers", [])
        if isinstance(tickers_raw, str):
            tickers_raw = json.loads(tickers_raw)

        return cls(
            user_id=data["user_id"],
            risk_profile=RiskProfile(data["risk_profile"]),
            seed_amount=float(data["seed_amount"]),
            investment_goal=data["investment_goal"],
            investment_style=InvestmentStyle(data["investment_style"]),
            loss_tolerance=float(data["loss_tolerance"]),
            sector_filter=sector_raw if sector_raw else [],
            designated_tickers=tickers_raw if tickers_raw else [],
            rebalancing_frequency=RebalancingFrequency(data.get("rebalancing_frequency", "MONTHLY")),
            created_at=data.get("created_at", datetime.now(timezone.utc)),
            updated_at=data.get("updated_at", datetime.now(timezone.utc)),
        )


# ══════════════════════════════════════
# 투자 프로필 관리 엔진
# ══════════════════════════════════════
class InvestorProfileManager:
    """
    사용자 투자 프로필 관리 엔진

    프로필 생성, 조회, 갱신을 담당하며 PostgreSQL 데이터베이스에 저장합니다.
    프로필 정보는 포트폴리오 구성 및 리밸런싱의 기반 정보입니다.

    주요 기능:
    - async create_profile: 새로운 사용자 프로필 생성
    - async get_profile: 사용자 프로필 조회
    - async update_profile: 사용자 프로필 갱신
    - _apply_profile_to_strategy: 프로필 기반 전략 파라미터 매핑
    """

    async def create_profile(
        self,
        user_id: str,
        risk_profile: RiskProfile,
        seed_amount: float,
        investment_goal: str,
        investment_style: InvestmentStyle,
        loss_tolerance: float,
        sector_filter: Optional[list[str]] = None,
        designated_tickers: Optional[list[str]] = None,
        rebalancing_frequency: Optional[RebalancingFrequency] = None,
    ) -> InvestorProfile:
        """
        새로운 사용자 투자 프로필을 생성합니다.

        Args:
            user_id: 사용자 ID
            risk_profile: 투자 성향
            seed_amount: 초기 자본 (원)
            investment_goal: 투자 목적
            investment_style: 투자 스타일
            loss_tolerance: 손실 허용도 (%)
            sector_filter: 제외 섹터 목록 (선택)
            designated_tickers: 지정 종목 목록 (선택)
            rebalancing_frequency: 리밸런싱 주기 (선택, 기본값: MONTHLY)

        Returns:
            생성된 InvestorProfile 인스턴스

        Raises:
            Exception: DB 저장 실패 시
        """
        profile = InvestorProfile(
            user_id=user_id,
            risk_profile=risk_profile,
            seed_amount=seed_amount,
            investment_goal=investment_goal,
            investment_style=investment_style,
            loss_tolerance=loss_tolerance,
            sector_filter=sector_filter or [],
            designated_tickers=designated_tickers or [],
            rebalancing_frequency=rebalancing_frequency or RebalancingFrequency.MONTHLY,
        )

        try:
            async with async_session_factory() as session:
                query = text("""
                    INSERT INTO user_profiles (
                        user_id, risk_profile, seed_amount, investment_goal,
                        investment_style, loss_tolerance, sector_filter,
                        designated_tickers, rebalancing_frequency, created_at, updated_at
                    )
                    VALUES (
                        :user_id, :risk_profile, :seed_amount, :investment_goal,
                        :investment_style, :loss_tolerance, :sector_filter,
                        :designated_tickers, :rebalancing_frequency, :created_at, :updated_at
                    )
                """)
                await session.execute(query, profile.to_dict())
                await session.commit()

            logger.info(
                f"Profile created for user {user_id}: " f"risk={risk_profile.value}, style={investment_style.value}"
            )
            return profile

        except Exception as e:
            logger.error(f"Failed to create profile for user {user_id}: {e}")
            raise

    async def get_profile(self, user_id: str) -> Optional[InvestorProfile]:
        """
        사용자 투자 프로필을 조회합니다.

        Args:
            user_id: 사용자 ID

        Returns:
            InvestorProfile 인스턴스, 존재하지 않으면 None

        Raises:
            Exception: DB 조회 실패 시
        """
        try:
            async with async_session_factory() as session:
                query = text("""
                    SELECT user_id, risk_profile, seed_amount, investment_goal,
                           investment_style, loss_tolerance, sector_filter,
                           designated_tickers, rebalancing_frequency, created_at, updated_at
                    FROM user_profiles
                    WHERE user_id = :user_id
                """)
                result = await session.execute(query, {"user_id": user_id})
                row = result.fetchone()

                if not row:
                    logger.warning(f"Profile not found for user {user_id}")
                    return None

                data = {
                    "user_id": row[0],
                    "risk_profile": row[1],
                    "seed_amount": row[2],
                    "investment_goal": row[3],
                    "investment_style": row[4],
                    "loss_tolerance": row[5],
                    "sector_filter": row[6],
                    "designated_tickers": row[7],
                    "rebalancing_frequency": row[8],
                    "created_at": row[9],
                    "updated_at": row[10],
                }
                return InvestorProfile.from_dict(data)

        except Exception as e:
            logger.error(f"Failed to get profile for user {user_id}: {e}")
            raise

    async def update_profile(self, user_id: str, **kwargs) -> InvestorProfile:
        """
        사용자 투자 프로필을 갱신합니다.

        Args:
            user_id: 사용자 ID
            **kwargs: 갱신할 프로필 필드 (risk_profile, seed_amount, etc.)

        Returns:
            갱신된 InvestorProfile 인스턴스

        Raises:
            ValueError: 프로필 미존재 시
            Exception: DB 갱신 실패 시
        """
        try:
            # 기존 프로필 조회
            profile = await self.get_profile(user_id)
            if not profile:
                raise ValueError(f"Profile not found for user {user_id}")

            # 필드 갱신
            for key, value in kwargs.items():
                if hasattr(profile, key):
                    setattr(profile, key, value)

            profile.updated_at = datetime.now(timezone.utc)

            # DB 갱신
            async with async_session_factory() as session:
                query = text("""
                    UPDATE user_profiles
                    SET risk_profile = :risk_profile,
                        seed_amount = :seed_amount,
                        investment_goal = :investment_goal,
                        investment_style = :investment_style,
                        loss_tolerance = :loss_tolerance,
                        sector_filter = :sector_filter,
                        designated_tickers = :designated_tickers,
                        rebalancing_frequency = :rebalancing_frequency,
                        updated_at = :updated_at
                    WHERE user_id = :user_id
                """)
                await session.execute(query, profile.to_dict())
                await session.commit()

            logger.info(f"Profile updated for user {user_id}: {list(kwargs.keys())}")
            return profile

        except ValueError as e:
            logger.warning(str(e))
            raise
        except Exception as e:
            logger.error(f"Failed to update profile for user {user_id}: {e}")
            raise

    def _apply_profile_to_strategy(self, profile: InvestorProfile) -> dict[str, Any]:
        """
        사용자 프로필을 전략 파라미터로 매핑합니다.

        프로필의 투자 성향과 리밸런싱 주기에 따라
        거래 빈도와 앙상블 가중치를 결정합니다.

        Args:
            profile: 사용자 투자 프로필

        Returns:
            전략 파라미터 딕셔너리:
            {
                "trading_frequency": str,  # 거래 빈도 레이블
                "min_holding_days": int,   # 최소 보유 기간 (일)
                "max_holding_days": int,   # 최대 보유 기간 (일)
                "ensemble_weights": dict,  # 전략별 가중치
                "rebalancing_frequency": str,  # 리밸런싱 주기
            }
        """
        # HOLDING_PERIOD_MAP에서 거래 빈도 정보 추출
        holding_info = HOLDING_PERIOD_MAP.get(profile.risk_profile, HOLDING_PERIOD_MAP[RiskProfile.BALANCED])

        # ENSEMBLE_DEFAULT_WEIGHTS에서 전략 가중치 추출
        ensemble_weights = ENSEMBLE_DEFAULT_WEIGHTS.get(
            profile.risk_profile, ENSEMBLE_DEFAULT_WEIGHTS[RiskProfile.BALANCED]
        )

        # 문자열 키 처리 (StrategyType enum → string)
        normalized_weights = {}
        for key, value in ensemble_weights.items():
            if isinstance(key, StrategyType):
                normalized_weights[key.value] = value
            else:
                normalized_weights[str(key)] = value

        return {
            "trading_frequency": holding_info["label"],
            "min_holding_days": holding_info["min_days"],
            "max_holding_days": holding_info["max_days"],
            "ensemble_weights": normalized_weights,
            "rebalancing_frequency": profile.rebalancing_frequency.value,
            "loss_tolerance": profile.loss_tolerance,
            "investment_style": profile.investment_style.value,
        }
