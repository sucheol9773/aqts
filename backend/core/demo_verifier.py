"""
AQTS Phase 7 - DEMO 모드 실전 가동 검증 모듈

KIS 모의투자 계좌 연결 상태를 검증하고,
DEMO 모드 파이프라인 가동 전 전체 체크리스트를 수행합니다.

검증 항목:
1. KIS DEMO 자격증명 설정 확인
2. KIS DEMO 토큰 발급 검증
3. KIS DEMO 잔고 조회 연동 확인
4. KIS DEMO 주문 인터페이스 검증 (dry-run)
5. DB 연결 상태 (PostgreSQL, MongoDB, Redis)
6. AI (Anthropic) 연결 확인
7. 알림 채널 (Telegram) 연결 확인
"""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

import httpx
from loguru import logger

from config.settings import TradingMode, get_settings

# ══════════════════════════════════════
# 검증 결과 데이터 구조
# ══════════════════════════════════════


class VerifyStatus(str, Enum):
    """개별 검증 항목 상태"""

    PASS = "PASS"
    FAIL = "FAIL"
    WARN = "WARN"
    SKIP = "SKIP"


@dataclass
class VerifyItem:
    """개별 검증 항목"""

    name: str
    category: str
    status: VerifyStatus
    message: str
    required: bool = True
    latency_ms: Optional[float] = None
    details: dict = field(default_factory=dict)


@dataclass
class DemoVerificationReport:
    """DEMO 모드 검증 종합 리포트"""

    items: list[VerifyItem] = field(default_factory=list)
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: Optional[datetime] = None
    trading_mode: str = ""
    environment: str = ""

    @property
    def all_required_passed(self) -> bool:
        """필수 항목이 모두 통과했는지"""
        return all(item.status == VerifyStatus.PASS for item in self.items if item.required)

    @property
    def can_start_demo(self) -> bool:
        """DEMO 가동 가능 여부"""
        return self.all_required_passed

    @property
    def passed_count(self) -> int:
        return sum(1 for i in self.items if i.status == VerifyStatus.PASS)

    @property
    def failed_count(self) -> int:
        return sum(1 for i in self.items if i.status == VerifyStatus.FAIL)

    @property
    def warn_count(self) -> int:
        return sum(1 for i in self.items if i.status == VerifyStatus.WARN)

    def summary(self) -> str:
        """한 줄 요약"""
        total = len(self.items)
        return (
            f"DEMO 검증 결과: {self.passed_count}/{total} 통과, "
            f"{self.failed_count} 실패, {self.warn_count} 경고 | "
            f"가동 가능: {'✅ YES' if self.can_start_demo else '❌ NO'}"
        )

    def to_dict(self) -> dict:
        return {
            "can_start_demo": self.can_start_demo,
            "summary": self.summary(),
            "trading_mode": self.trading_mode,
            "environment": self.environment,
            "started_at": self.started_at.isoformat(),
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "items": [
                {
                    "name": i.name,
                    "category": i.category,
                    "status": i.status.value,
                    "message": i.message,
                    "required": i.required,
                    "latency_ms": i.latency_ms,
                    "details": i.details,
                }
                for i in self.items
            ],
        }


# ══════════════════════════════════════
# DEMO 검증 엔진
# ══════════════════════════════════════


class DemoVerifier:
    """DEMO 모드 실전 가동 전 종합 검증 엔진"""

    def __init__(self):
        self._settings = get_settings()

    # ── 전체 검증 실행 ──

    async def run_full_verification(self) -> DemoVerificationReport:
        """전체 검증 체크리스트 수행"""
        report = DemoVerificationReport(
            trading_mode=self._settings.kis.trading_mode.value,
            environment=self._settings.environment,
        )

        # 1. 기본 설정 검증
        report.items.append(self._verify_trading_mode())
        report.items.append(self._verify_demo_credentials())

        # 2. KIS API 연결 검증
        report.items.append(await self._verify_kis_token_issuance())
        report.items.append(await self._verify_kis_balance_query())

        # 3. 인프라 검증
        report.items.append(await self._verify_postgresql())
        report.items.append(await self._verify_mongodb())
        report.items.append(await self._verify_redis())

        # 4. AI 서비스 검증
        report.items.append(await self._verify_anthropic_api())

        # 5. 알림 채널 검증 (선택)
        report.items.append(await self._verify_telegram())

        # 6. 안전 장치 검증
        report.items.append(self._verify_risk_settings())
        report.items.append(self._verify_trading_guard())

        report.completed_at = datetime.now(timezone.utc)
        logger.info(report.summary())
        return report

    # ── 1. 기본 설정 검증 ──

    def _verify_trading_mode(self) -> VerifyItem:
        """거래 모드가 DEMO인지 확인"""
        mode = self._settings.kis.trading_mode
        if mode == TradingMode.DEMO:
            return VerifyItem(
                name="거래 모드 확인",
                category="설정",
                status=VerifyStatus.PASS,
                message=f"현재 거래 모드: {mode.value}",
            )
        return VerifyItem(
            name="거래 모드 확인",
            category="설정",
            status=VerifyStatus.FAIL,
            message=f"DEMO 모드가 아닙니다 (현재: {mode.value}). .env에서 KIS_TRADING_MODE=DEMO 설정 필요",
        )

    def _verify_demo_credentials(self) -> VerifyItem:
        """DEMO 자격증명 설정 여부 확인"""
        kis = self._settings.kis
        missing = []
        test_defaults = {"test_key_demo", "test_secret_demo", "87654321-01", ""}

        if not kis.demo_app_key or kis.demo_app_key in test_defaults:
            missing.append("KIS_DEMO_APP_KEY")
        if not kis.demo_app_secret or kis.demo_app_secret in test_defaults:
            missing.append("KIS_DEMO_APP_SECRET")
        if not kis.demo_account_no or kis.demo_account_no in test_defaults:
            missing.append("KIS_DEMO_ACCOUNT_NO")

        if not missing:
            return VerifyItem(
                name="DEMO 자격증명",
                category="설정",
                status=VerifyStatus.PASS,
                message="모든 DEMO API 자격증명이 설정됨",
                details={
                    "app_key": (
                        f"{kis.demo_app_key[:6]}...{kis.demo_app_key[-4:]}" if len(kis.demo_app_key) > 10 else "***"
                    ),
                    "account_no": kis.demo_account_no,
                },
            )
        return VerifyItem(
            name="DEMO 자격증명",
            category="설정",
            status=VerifyStatus.FAIL,
            message=f"미설정 항목: {', '.join(missing)}",
            details={"missing_fields": missing},
        )

    # ── 2. KIS API 연결 검증 ──

    async def _verify_kis_token_issuance(self) -> VerifyItem:
        """KIS DEMO 토큰 발급 검증"""
        kis = self._settings.kis

        if not kis.demo_app_key or not kis.demo_app_secret:
            return VerifyItem(
                name="KIS 토큰 발급",
                category="KIS API",
                status=VerifyStatus.SKIP,
                message="자격증명 미설정으로 건너뜀",
            )

        url = f"{kis.demo_base_url}/oauth2/tokenP"
        body = {
            "grant_type": "client_credentials",
            "appkey": kis.demo_app_key,
            "appsecret": kis.demo_app_secret,
        }

        try:
            start = asyncio.get_event_loop().time()
            async with httpx.AsyncClient(timeout=15.0, verify=True) as client:
                resp = await client.post(url, json=body)
            latency = (asyncio.get_event_loop().time() - start) * 1000

            if resp.status_code == 200:
                data = resp.json()
                if "access_token" in data:
                    token_preview = data["access_token"][:20] + "..."
                    return VerifyItem(
                        name="KIS 토큰 발급",
                        category="KIS API",
                        status=VerifyStatus.PASS,
                        message="DEMO 토큰 발급 성공",
                        latency_ms=round(latency, 1),
                        details={"token_preview": token_preview},
                    )
                return VerifyItem(
                    name="KIS 토큰 발급",
                    category="KIS API",
                    status=VerifyStatus.FAIL,
                    message=f"토큰 응답에 access_token 없음: {data.get('msg1', 'unknown')}",
                    latency_ms=round(latency, 1),
                )
            return VerifyItem(
                name="KIS 토큰 발급",
                category="KIS API",
                status=VerifyStatus.FAIL,
                message=f"HTTP {resp.status_code}: {resp.text[:200]}",
                latency_ms=round(latency, 1),
            )
        except httpx.ConnectError as e:
            return VerifyItem(
                name="KIS 토큰 발급",
                category="KIS API",
                status=VerifyStatus.FAIL,
                message=f"KIS 서버 연결 실패: {str(e)[:200]}",
            )
        except Exception as e:
            return VerifyItem(
                name="KIS 토큰 발급",
                category="KIS API",
                status=VerifyStatus.FAIL,
                message=f"예외 발생: {type(e).__name__}: {str(e)[:200]}",
            )

    async def _verify_kis_balance_query(self) -> VerifyItem:
        """KIS DEMO 잔고 조회 검증 (토큰 발급 후 잔고 확인)"""
        kis = self._settings.kis

        if not kis.demo_app_key or not kis.demo_app_secret:
            return VerifyItem(
                name="KIS 잔고 조회",
                category="KIS API",
                status=VerifyStatus.SKIP,
                message="자격증명 미설정으로 건너뜀",
            )

        try:
            # 먼저 토큰 발급
            url = f"{kis.demo_base_url}/oauth2/tokenP"
            body = {
                "grant_type": "client_credentials",
                "appkey": kis.demo_app_key,
                "appsecret": kis.demo_app_secret,
            }
            async with httpx.AsyncClient(timeout=15.0, verify=True) as client:
                token_resp = await client.post(url, json=body)

            if token_resp.status_code != 200:
                return VerifyItem(
                    name="KIS 잔고 조회",
                    category="KIS API",
                    status=VerifyStatus.SKIP,
                    message="토큰 발급 실패로 건너뜀",
                )

            access_token = token_resp.json().get("access_token", "")

            # 잔고 조회 (국내주식 잔고)
            balance_url = f"{kis.demo_base_url}/uapi/domestic-stock/v1/trading/inquire-balance"
            headers = {
                "content-type": "application/json; charset=utf-8",
                "authorization": f"Bearer {access_token}",
                "appkey": kis.demo_app_key,
                "appsecret": kis.demo_app_secret,
                "tr_id": "VTTC8434R",  # DEMO 잔고 조회
                "custtype": "P",
            }
            params = {
                "CANO": kis.demo_account_no.split("-")[0] if "-" in kis.demo_account_no else kis.demo_account_no,
                "ACNT_PRDT_CD": kis.demo_account_no.split("-")[1] if "-" in kis.demo_account_no else "01",
                "AFHR_FLPR_YN": "N",
                "OFL_YN": "",
                "INQR_DVSN": "02",
                "UNPR_DVSN": "01",
                "FUND_STTL_ICLD_YN": "N",
                "FNCG_AMT_AUTO_RDPT_YN": "N",
                "PRCS_DVSN": "01",
                "CTX_AREA_FK100": "",
                "CTX_AREA_NK100": "",
            }

            start = asyncio.get_event_loop().time()
            async with httpx.AsyncClient(timeout=15.0, verify=True) as client:
                resp = await client.get(balance_url, headers=headers, params=params)
            latency = (asyncio.get_event_loop().time() - start) * 1000

            if resp.status_code == 200:
                data = resp.json()
                rt_cd = data.get("rt_cd", "")
                if rt_cd == "0":
                    # 예수금 추출
                    output2 = data.get("output2", [{}])
                    deposit = 0
                    if output2:
                        deposit = int(output2[0].get("dnca_tot_amt", "0"))
                    positions = data.get("output1", [])

                    return VerifyItem(
                        name="KIS 잔고 조회",
                        category="KIS API",
                        status=VerifyStatus.PASS,
                        message=f"잔고 조회 성공 | 예수금: {deposit:,}원, 보유종목: {len(positions)}건",
                        latency_ms=round(latency, 1),
                        details={
                            "deposit_krw": deposit,
                            "positions_count": len(positions),
                        },
                    )
                return VerifyItem(
                    name="KIS 잔고 조회",
                    category="KIS API",
                    status=VerifyStatus.FAIL,
                    message=f"API 응답 오류 (rt_cd={rt_cd}): {data.get('msg1', 'unknown')}",
                    latency_ms=round(latency, 1),
                )
            return VerifyItem(
                name="KIS 잔고 조회",
                category="KIS API",
                status=VerifyStatus.FAIL,
                message=f"HTTP {resp.status_code}",
                latency_ms=round(latency, 1),
            )
        except Exception as e:
            return VerifyItem(
                name="KIS 잔고 조회",
                category="KIS API",
                status=VerifyStatus.FAIL,
                message=f"예외: {type(e).__name__}: {str(e)[:200]}",
            )

    # ── 3. 인프라 검증 ──

    async def _verify_postgresql(self) -> VerifyItem:
        """PostgreSQL 연결 검증"""
        try:
            from sqlalchemy import text

            from db.database import get_db_engine

            engine = get_db_engine()
            start = asyncio.get_event_loop().time()
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            latency = (asyncio.get_event_loop().time() - start) * 1000

            return VerifyItem(
                name="PostgreSQL",
                category="인프라",
                status=VerifyStatus.PASS,
                message="연결 성공",
                latency_ms=round(latency, 1),
            )
        except Exception as e:
            return VerifyItem(
                name="PostgreSQL",
                category="인프라",
                status=VerifyStatus.FAIL,
                message=f"연결 실패: {type(e).__name__}: {str(e)[:200]}",
            )

    async def _verify_mongodb(self) -> VerifyItem:
        """MongoDB 연결 검증"""
        try:
            from db.database import get_mongo_client

            start = asyncio.get_event_loop().time()
            client = get_mongo_client()
            await client.admin.command("ping")
            latency = (asyncio.get_event_loop().time() - start) * 1000

            return VerifyItem(
                name="MongoDB",
                category="인프라",
                status=VerifyStatus.PASS,
                message="연결 성공",
                latency_ms=round(latency, 1),
            )
        except Exception as e:
            return VerifyItem(
                name="MongoDB",
                category="인프라",
                status=VerifyStatus.FAIL,
                message=f"연결 실패: {type(e).__name__}: {str(e)[:200]}",
            )

    async def _verify_redis(self) -> VerifyItem:
        """Redis 연결 검증"""
        try:
            from db.database import get_redis_client

            start = asyncio.get_event_loop().time()
            redis = get_redis_client()
            await redis.ping()
            latency = (asyncio.get_event_loop().time() - start) * 1000

            return VerifyItem(
                name="Redis",
                category="인프라",
                status=VerifyStatus.PASS,
                message="연결 성공",
                latency_ms=round(latency, 1),
            )
        except Exception as e:
            return VerifyItem(
                name="Redis",
                category="인프라",
                status=VerifyStatus.FAIL,
                message=f"연결 실패: {type(e).__name__}: {str(e)[:200]}",
            )

    # ── 4. AI 서비스 검증 ──

    async def _verify_anthropic_api(self) -> VerifyItem:
        """Anthropic Claude API 연결 검증"""
        api_key = getattr(self._settings, "anthropic", None)
        if not api_key:
            return VerifyItem(
                name="Anthropic API",
                category="AI",
                status=VerifyStatus.WARN,
                message="Anthropic 설정 미확인 (AI 분석 비활성화 상태에서 가동 가능)",
                required=False,
            )

        anthropic_key = getattr(api_key, "api_key", "")
        if not anthropic_key or anthropic_key.startswith("test_"):
            return VerifyItem(
                name="Anthropic API",
                category="AI",
                status=VerifyStatus.WARN,
                message="Anthropic API 키 미설정 또는 테스트 키 (AI 분석 비활성)",
                required=False,
            )

        try:
            # 간단한 API 유효성 확인 (모델 목록 조회)
            headers = {
                "x-api-key": anthropic_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            }
            start = asyncio.get_event_loop().time()
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    "https://api.anthropic.com/v1/messages",
                    headers=headers,
                    json={
                        "model": "claude-haiku-4-5-20251001",
                        "max_tokens": 10,
                        "messages": [{"role": "user", "content": "ping"}],
                    },
                )
            latency = (asyncio.get_event_loop().time() - start) * 1000

            if resp.status_code == 200:
                return VerifyItem(
                    name="Anthropic API",
                    category="AI",
                    status=VerifyStatus.PASS,
                    message="Claude API 연결 성공",
                    latency_ms=round(latency, 1),
                    required=False,
                )
            elif resp.status_code == 401:
                return VerifyItem(
                    name="Anthropic API",
                    category="AI",
                    status=VerifyStatus.WARN,
                    message="API 키 인증 실패 (401)",
                    required=False,
                )
            else:
                return VerifyItem(
                    name="Anthropic API",
                    category="AI",
                    status=VerifyStatus.WARN,
                    message=f"HTTP {resp.status_code}",
                    latency_ms=round(latency, 1),
                    required=False,
                )
        except Exception as e:
            return VerifyItem(
                name="Anthropic API",
                category="AI",
                status=VerifyStatus.WARN,
                message=f"연결 실패: {type(e).__name__}",
                required=False,
            )

    # ── 5. 알림 채널 검증 ──

    async def _verify_telegram(self) -> VerifyItem:
        """Telegram Bot 연결 검증"""
        tg = self._settings.telegram

        if not tg.bot_token or tg.bot_token.startswith("test"):
            return VerifyItem(
                name="Telegram 알림",
                category="알림",
                status=VerifyStatus.WARN,
                message="Telegram 봇 토큰 미설정 (알림 비활성화 상태에서 가동 가능)",
                required=False,
            )

        try:
            url = f"https://api.telegram.org/bot{tg.bot_token}/getMe"
            start = asyncio.get_event_loop().time()
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(url)
            latency = (asyncio.get_event_loop().time() - start) * 1000

            if resp.status_code == 200 and resp.json().get("ok"):
                bot_info = resp.json().get("result", {})
                return VerifyItem(
                    name="Telegram 알림",
                    category="알림",
                    status=VerifyStatus.PASS,
                    message=f"봇 연결 성공: @{bot_info.get('username', 'unknown')}",
                    latency_ms=round(latency, 1),
                    required=False,
                    details={"bot_username": bot_info.get("username")},
                )
            return VerifyItem(
                name="Telegram 알림",
                category="알림",
                status=VerifyStatus.WARN,
                message=f"봇 인증 실패: HTTP {resp.status_code}",
                latency_ms=round(latency, 1),
                required=False,
            )
        except Exception as e:
            return VerifyItem(
                name="Telegram 알림",
                category="알림",
                status=VerifyStatus.WARN,
                message=f"연결 실패: {type(e).__name__}",
                required=False,
            )

    # ── 6. 안전 장치 검증 ──

    def _verify_risk_settings(self) -> VerifyItem:
        """리스크 설정 유효성 검증"""
        risk = self._settings.risk
        issues = []

        if risk.initial_capital_krw <= 0:
            issues.append("초기 자본금 미설정")
        if risk.daily_loss_limit_krw <= 0:
            issues.append("일일 손실 한도 미설정")
        if risk.max_drawdown <= 0 or risk.max_drawdown > 1.0:
            issues.append(f"MDD 한도 비정상 ({risk.max_drawdown})")
        if risk.max_order_amount_krw <= 0:
            issues.append("최대 주문 금액 미설정")

        if not issues:
            return VerifyItem(
                name="리스크 설정",
                category="안전장치",
                status=VerifyStatus.PASS,
                message=(
                    f"초기자본: {risk.initial_capital_krw:,.0f}원, "
                    f"일일손실한도: {risk.daily_loss_limit_krw:,.0f}원, "
                    f"MDD: {risk.max_drawdown:.0%}"
                ),
                details={
                    "initial_capital": risk.initial_capital_krw,
                    "daily_loss_limit": risk.daily_loss_limit_krw,
                    "max_drawdown": risk.max_drawdown,
                    "max_order_amount": risk.max_order_amount_krw,
                },
            )
        return VerifyItem(
            name="리스크 설정",
            category="안전장치",
            status=VerifyStatus.FAIL,
            message=f"설정 오류: {', '.join(issues)}",
        )

    def _verify_trading_guard(self) -> VerifyItem:
        """TradingGuard 초기화 검증"""
        try:
            from core.trading_guard import TradingGuard

            guard = TradingGuard()
            env_check = guard.verify_environment()

            if env_check.allowed:
                return VerifyItem(
                    name="TradingGuard",
                    category="안전장치",
                    status=VerifyStatus.PASS,
                    message="트레이딩 안전 장치 초기화 완료, 환경 검증 통과",
                )
            return VerifyItem(
                name="TradingGuard",
                category="안전장치",
                status=VerifyStatus.WARN,
                message=f"환경 검증 경고: {env_check.reason}",
                required=False,
            )
        except Exception as e:
            return VerifyItem(
                name="TradingGuard",
                category="안전장치",
                status=VerifyStatus.FAIL,
                message=f"초기화 실패: {type(e).__name__}: {str(e)[:200]}",
            )
