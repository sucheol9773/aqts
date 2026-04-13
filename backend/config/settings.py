"""
AQTS 애플리케이션 설정 모듈

모든 설정은 환경변수에서 로드되며, .env 파일을 통해 관리됩니다.
민감 정보는 절대 하드코딩하지 않습니다.

KIS 설정은 LIVE/DEMO/BACKTEST 3단계 모드를 지원하며,
모드에 따라 적절한 API 키, 계좌, URL이 자동 선택됩니다.
"""

import logging
from datetime import datetime, timezone
from enum import Enum
from functools import lru_cache
from typing import Optional
from urllib.parse import quote_plus

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


# ══════════════════════════════════════
# 거래 모드 열거형
# ══════════════════════════════════════
class TradingMode(str, Enum):
    """거래 모드"""

    LIVE = "LIVE"  # 실전 거래 (실제 자금)
    DEMO = "DEMO"  # 모의 거래 (가상 자금)
    BACKTEST = "BACKTEST"  # 백테스트 전용 (API 호출 없음)


# ══════════════════════════════════════
# KIS 단일 모드 인증 정보 (내부용)
# ══════════════════════════════════════
class KISCredential:
    """단일 모드(실전 또는 모의)의 KIS 인증 정보 컨테이너"""

    def __init__(
        self,
        app_key: str = "",
        app_secret: str = "",
        account_no: str = "",
        account_prod: str = "01",
        base_url: str = "",
        websocket_url: str = "",
    ):
        self.app_key = app_key
        self.app_secret = app_secret
        self.account_no = account_no
        self.account_prod = account_prod
        self.base_url = base_url
        self.websocket_url = websocket_url


# ══════════════════════════════════════
# 한국투자증권 OpenAPI 설정
# ══════════════════════════════════════
class KISSettings(BaseSettings):
    """
    한국투자증권 OpenAPI 통합 설정

    KIS_TRADING_MODE에 따라 LIVE 또는 DEMO 인증 정보가 자동 선택됩니다.
    BACKTEST 모드에서는 API 호출이 발생하지 않습니다.
    """

    model_config = SettingsConfigDict(env_prefix="KIS_")

    # 거래 모드
    trading_mode: TradingMode = Field(
        default=TradingMode.DEMO,
        description="거래 모드 (LIVE/DEMO/BACKTEST)",
    )

    # ── 실전 거래 인증 정보 ──
    live_app_key: str = Field(default="", description="실전 API App Key")
    live_app_secret: str = Field(default="", description="실전 API App Secret")
    live_account_no: str = Field(default="", description="실전 계좌번호")
    live_account_prod: str = Field(default="01", description="실전 계좌 상품코드")
    live_base_url: str = Field(
        default="https://openapi.koreainvestment.com:9443",
        description="실전 REST API URL",
    )
    live_websocket_url: str = Field(
        default="ws://ops.koreainvestment.com:21000",
        description="실전 WebSocket URL",
    )

    # ── 모의 거래 인증 정보 ──
    demo_app_key: str = Field(default="", description="모의 API App Key")
    demo_app_secret: str = Field(default="", description="모의 API App Secret")
    demo_account_no: str = Field(default="", description="모의 계좌번호")
    demo_account_prod: str = Field(default="01", description="모의 계좌 상품코드")
    demo_base_url: str = Field(
        default="https://openapivts.koreainvestment.com:29443",
        description="모의 REST API URL",
    )
    demo_websocket_url: str = Field(
        default="ws://ops.koreainvestment.com:31000",
        description="모의 WebSocket URL",
    )

    # ── 공통 설정 ──
    token_refresh_interval: int = Field(default=3600, description="토큰 갱신 주기 (초)")
    api_timeout: int = Field(default=10, description="API 요청 타임아웃 (초)")
    api_retry_count: int = Field(default=3, description="API 재시도 횟수")
    token_retry_count: int = Field(
        default=5,
        description="토큰 발급 재시도 횟수 (EGW00133 1분 제한 대응)",
    )
    token_retry_max_wait: int = Field(
        default=60,
        description="토큰 발급 재시도 최대 대기 시간 (초)",
    )

    # ── WebSocket 보안 예외 설정 ──
    # 단일 소스: pydantic 필드로 선언, validate_websocket_security()에서 직접 참조.
    # os.environ 직접 조회(env_bool 등)를 병행하지 않는다.
    ws_insecure_allow: str = Field(
        default="false",
        description="운영+LIVE에서 ws:// 허용 여부 (기본: false → 부팅 차단). 'true'/'false' 표준 표기.",
    )
    ws_exception_ticket: str = Field(
        default="",
        description="ws:// 예외 승인 변경번호 (예: CHG-2026-0042)",
    )
    ws_exception_expires_at: str = Field(
        default="",
        description="ws:// 예외 만료일 YYYY-MM-DD (예: 2026-06-30). 당일 23:59:59 UTC까지 유효.",
    )

    # WebSocket URL에 허용되는 스킴
    _WS_ALLOWED_SCHEMES = ("ws://", "wss://")

    @property
    def is_live(self) -> bool:
        return self.trading_mode == TradingMode.LIVE

    @staticmethod
    def _parse_ws_insecure_allow(raw: str) -> bool:
        """ws_insecure_allow 필드값을 bool로 파싱한다.

        env_bool()을 사용하지 않고 pydantic 필드값을 직접 파싱하여
        설정 소스를 단일화한다.
        """
        normalized = raw.strip().lower()
        if normalized == "true":
            return True
        if normalized == "false":
            return False
        raise ValueError(f"KIS_WS_INSECURE_ALLOW={raw!r} 은 유효하지 않습니다. " f"'true' 또는 'false'만 허용됩니다.")

    def validate_websocket_security(self, environment: str) -> None:
        """운영+LIVE 환경에서 ws:// 사용 시 부팅을 차단한다.

        호출 위치: main.py lifespan 최초 구간 (DB 연결 전, fail-fast 보장).

        만료일 경계 정책: YYYY-MM-DD는 당일 23:59:59 UTC까지 유효.
        예: 2026-06-30 → 2026-06-30T23:59:59Z까지 허용, 2026-07-01T00:00:00Z부터 차단.

        예외 조건: KIS_WS_INSECURE_ALLOW=true + 유효한 티켓 + 만료일 미경과.
        예외가 허용되더라도 경고 로그를 남긴다.
        """
        is_production = environment == "production"
        ws_url = self.active_credential.websocket_url

        # 미설정이면 검증 건너뜀
        if not ws_url:
            return

        # ── URL scheme allowlist 검증 ──
        if not any(ws_url.startswith(scheme) for scheme in self._WS_ALLOWED_SCHEMES):
            raise RuntimeError(
                f"[보안 차단] WebSocket URL의 스킴이 허용 목록에 없습니다. "
                f"현재 URL: {ws_url}\n"
                f"허용 스킴: {', '.join(self._WS_ALLOWED_SCHEMES)}"
            )

        # wss:// → 안전한 프로토콜, 통과
        if ws_url.startswith("wss://"):
            return

        # ── 이하 ws:// (비암호화) 경로 ──
        if not (is_production and self.is_live):
            # 개발/DEMO/BACKTEST 환경에서는 ws:// 경고만
            logger.info(
                "KIS WebSocket이 ws:// (비암호화)를 사용 중입니다. "
                "현재 환경(%s/%s)에서는 허용되지만 운영+LIVE에서는 차단됩니다.",
                environment,
                self.trading_mode.value,
            )
            return

        # ── 운영 + LIVE + ws:// → 차단 또는 예외 확인 ──
        insecure_allow = self._parse_ws_insecure_allow(self.ws_insecure_allow)

        if not insecure_allow:
            raise RuntimeError(
                f"[보안 차단] 운영+LIVE 환경에서 ws:// WebSocket은 허용되지 않습니다. "
                f"현재 URL: {ws_url}\n"
                f"해결 방법:\n"
                f"  1. (권장) wss:// 엔드포인트로 변경\n"
                f"  2. (임시) KIS_WS_INSECURE_ALLOW=true + "
                f"KIS_WS_EXCEPTION_TICKET + KIS_WS_EXCEPTION_EXPIRES_AT 설정"
            )

        # 예외 허용 경로: 티켓 + 만료일 검증
        ticket = self.ws_exception_ticket.strip()
        expires_at_raw = self.ws_exception_expires_at.strip()

        if not ticket:
            raise RuntimeError(
                "[보안 차단] KIS_WS_INSECURE_ALLOW=true이지만 "
                "KIS_WS_EXCEPTION_TICKET이 비어있습니다. "
                "변경 승인번호를 설정하세요."
            )

        if not expires_at_raw:
            raise RuntimeError(
                "[보안 차단] KIS_WS_INSECURE_ALLOW=true이지만 "
                "KIS_WS_EXCEPTION_EXPIRES_AT이 비어있습니다. "
                "예외 만료일을 설정하세요 (예: 2026-06-30)."
            )

        try:
            # 당일 23:59:59 UTC까지 유효 (당일 종료 기준)
            expires_at = datetime.strptime(expires_at_raw, "%Y-%m-%d").replace(
                hour=23, minute=59, second=59, tzinfo=timezone.utc
            )
        except ValueError:
            raise RuntimeError(
                f"[보안 차단] KIS_WS_EXCEPTION_EXPIRES_AT={expires_at_raw!r} "
                f"형식 오류. YYYY-MM-DD 형식으로 설정하세요."
            )

        now = datetime.now(tz=timezone.utc)
        if now > expires_at:
            raise RuntimeError(
                f"[보안 차단] ws:// 예외가 만료되었습니다. "
                f"만료일: {expires_at_raw} (23:59:59 UTC), "
                f"현재: {now.strftime('%Y-%m-%dT%H:%M:%SZ')}. "
                f"예외를 갱신하거나 wss://로 전환하세요."
            )

        # 예외 유효 — 경고 로그 후 계속
        days_remaining = (expires_at - now).days
        logger.warning(
            "[보안 예외] ws:// WebSocket이 임시 허용됩니다. " "ticket=%s, 만료일=%s 23:59:59 UTC (%d일 남음), URL=%s",
            ticket,
            expires_at_raw,
            days_remaining,
            ws_url,
        )

    @property
    def is_demo(self) -> bool:
        return self.trading_mode == TradingMode.DEMO

    @property
    def is_backtest(self) -> bool:
        return self.trading_mode == TradingMode.BACKTEST

    @property
    def active_credential(self) -> KISCredential:
        """현재 모드에 해당하는 인증 정보 반환"""
        if self.is_live:
            return KISCredential(
                app_key=self.live_app_key,
                app_secret=self.live_app_secret,
                account_no=self.live_account_no,
                account_prod=self.live_account_prod,
                base_url=self.live_base_url,
                websocket_url=self.live_websocket_url,
            )
        elif self.is_demo:
            return KISCredential(
                app_key=self.demo_app_key,
                app_secret=self.demo_app_secret,
                account_no=self.demo_account_no,
                account_prod=self.demo_account_prod,
                base_url=self.demo_base_url,
                websocket_url=self.demo_websocket_url,
            )
        else:
            return KISCredential()

    # ── 현재 모드의 값에 대한 편의 프로퍼티 ──
    @property
    def app_key(self) -> str:
        return self.active_credential.app_key

    @property
    def app_secret(self) -> str:
        return self.active_credential.app_secret

    @property
    def account_no(self) -> str:
        return self.active_credential.account_no

    @property
    def account_prod(self) -> str:
        return self.active_credential.account_prod

    @property
    def base_url(self) -> str:
        return self.active_credential.base_url

    @property
    def websocket_url(self) -> str:
        return self.active_credential.websocket_url


# ══════════════════════════════════════
# 데이터베이스 설정
# ══════════════════════════════════════
class DatabaseSettings(BaseSettings):
    """PostgreSQL (TimescaleDB) 설정"""

    model_config = SettingsConfigDict(env_prefix="DB_")

    host: str = Field(default="postgres")
    port: int = Field(default=5432)
    name: str = Field(default="aqts")
    user: str = Field(default="aqts_user")
    password: str = Field(..., description="DB 비밀번호")
    pool_size: int = Field(default=20)
    max_overflow: int = Field(default=10)

    @property
    def async_url(self) -> str:
        return f"postgresql+asyncpg://{self.user}:{quote_plus(self.password)}@{self.host}:{self.port}/{self.name}"

    @property
    def sync_url(self) -> str:
        return f"postgresql+psycopg2://{self.user}:{quote_plus(self.password)}@{self.host}:{self.port}/{self.name}"


class MongoSettings(BaseSettings):
    """MongoDB 설정"""

    model_config = SettingsConfigDict(env_prefix="MONGO_")

    host: str = Field(default="mongodb")
    port: int = Field(default=27017)
    db: str = Field(default="aqts")
    user: str = Field(default="aqts_user")
    password: str = Field(..., description="MongoDB 비밀번호")

    @property
    def uri(self) -> str:
        return f"mongodb://{self.user}:{quote_plus(self.password)}@{self.host}:{self.port}/{self.db}?authSource=admin"


class RedisSettings(BaseSettings):
    """Redis 설정"""

    model_config = SettingsConfigDict(env_prefix="REDIS_")

    host: str = Field(default="redis")
    port: int = Field(default=6379)
    password: str = Field(..., description="Redis 비밀번호")
    db: int = Field(default=0)

    @property
    def url(self) -> str:
        return f"redis://:{quote_plus(self.password)}@{self.host}:{self.port}/{self.db}"


# ══════════════════════════════════════
# AI / LLM 설정
# ══════════════════════════════════════
class AnthropicSettings(BaseSettings):
    """Claude API 설정 - 기본/고급 모델 분리"""

    model_config = SettingsConfigDict(env_prefix="ANTHROPIC_")

    api_key: str = Field(..., description="Anthropic API Key")
    default_model: str = Field(
        default="claude-haiku-4-5-20251001",
        description="기본 모델 (비용 효율: 감성 분석, 뉴스 요약)",
    )
    advanced_model: str = Field(
        default="claude-sonnet-4-20250514",
        description="고급 모델 (거시경제 분석, 투자 의견 생성)",
    )
    api_timeout: int = Field(default=30, description="API 타임아웃 (초)")
    cache_ttl: int = Field(default=14400, description="캐시 TTL (초, 기본 4시간)")


# ══════════════════════════════════════
# 알림 설정
# ══════════════════════════════════════
class TelegramSettings(BaseSettings):
    """텔레그램 알림 설정"""

    model_config = SettingsConfigDict(env_prefix="TELEGRAM_")

    bot_token: str = Field(..., description="텔레그램 봇 토큰")
    chat_id: str = Field(..., description="알림 수신 채팅 ID")
    alert_level: str = Field(default="IMPORTANT", description="알림 레벨 (ALL/IMPORTANT/ERROR)")


# ══════════════════════════════════════
# 대시보드 설정
# ══════════════════════════════════════
class DashboardSettings(BaseSettings):
    """대시보드 설정

    RBAC v1.29+: DASHBOARD_PASSWORD 제거, ADMIN_BOOTSTRAP_* 환경변수로 admin 시드 생성
    """

    model_config = SettingsConfigDict(env_prefix="DASHBOARD_")

    secret_key: str = Field(..., description="JWT 시크릿 키 (현재 활성)")
    previous_secret_key: Optional[str] = Field(
        default=None,
        description="이전 JWT 시크릿 키 (key rotation 기간 동안 검증용)",
    )
    access_token_expire_hours: int = Field(default=24)
    refresh_token_expire_days: int = Field(default=7)


# ══════════════════════════════════════
# 외부 API 설정
# ══════════════════════════════════════
class ExternalAPISettings(BaseSettings):
    """외부 데이터 제공자 API 설정"""

    dart_api_key: Optional[str] = Field(default=None, alias="DART_API_KEY")
    fred_api_key: Optional[str] = Field(default=None, alias="FRED_API_KEY")
    ecos_api_key: Optional[str] = Field(default=None, alias="ECOS_API_KEY")
    reddit_client_id: Optional[str] = Field(default=None, alias="REDDIT_CLIENT_ID")
    reddit_client_secret: Optional[str] = Field(default=None, alias="REDDIT_CLIENT_SECRET")
    reddit_user_agent: str = Field(default="AQTS/1.0", alias="REDDIT_USER_AGENT")


# ══════════════════════════════════════
# 리스크 관리 설정
# ══════════════════════════════════════
class RiskManagementSettings(BaseSettings):
    """거래 실행 및 리스크 관리 설정 (모든 금액 단위: 원)"""

    initial_capital_krw: int = Field(default=50_000_000, alias="INITIAL_CAPITAL_KRW")
    daily_loss_limit_krw: int = Field(default=5_000_000, alias="DAILY_LOSS_LIMIT_KRW")
    max_order_amount_krw: int = Field(default=10_000_000, alias="MAX_ORDER_AMOUNT_KRW")
    max_positions: int = Field(default=20, alias="MAX_POSITIONS")
    max_position_weight: float = Field(default=0.20, alias="MAX_POSITION_WEIGHT")
    max_sector_weight: float = Field(default=0.40, alias="MAX_SECTOR_WEIGHT")
    consecutive_loss_limit: int = Field(default=5, alias="CONSECUTIVE_LOSS_LIMIT")
    max_drawdown: float = Field(default=0.15, alias="MAX_DRAWDOWN")
    stop_loss_percent: float = Field(default=-0.10, alias="STOP_LOSS_PERCENT")
    commission_kr: float = Field(default=0.00015, alias="COMMISSION_KR")
    commission_us: float = Field(default=0.001, alias="COMMISSION_US")


# ══════════════════════════════════════
# 최상위 애플리케이션 설정
# ══════════════════════════════════════
class AppSettings(BaseSettings):
    """최상위 애플리케이션 설정 - 모든 하위 설정을 통합"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    environment: str = Field(default="development", alias="ENVIRONMENT")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    cors_allowed_origins: str = Field(
        default="http://localhost:3000,http://localhost:8000",
        alias="CORS_ALLOWED_ORIGINS",
        description="허용 Origin 목록 (콤마 구분). 예: http://localhost:3000,https://aqts.example.com",
    )

    # 하위 설정 그룹
    kis: KISSettings = Field(default_factory=KISSettings)
    db: DatabaseSettings = Field(default_factory=DatabaseSettings)
    mongo: MongoSettings = Field(default_factory=MongoSettings)
    redis: RedisSettings = Field(default_factory=RedisSettings)
    anthropic: AnthropicSettings = Field(default_factory=AnthropicSettings)
    telegram: TelegramSettings = Field(default_factory=TelegramSettings)
    dashboard: DashboardSettings = Field(default_factory=DashboardSettings)
    external: ExternalAPISettings = Field(default_factory=ExternalAPISettings)
    risk: RiskManagementSettings = Field(default_factory=RiskManagementSettings)

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def is_live_trading(self) -> bool:
        """실전 거래 활성 여부 (환경이 production이고 모드가 LIVE일 때만)"""
        return self.is_production and self.kis.is_live


@lru_cache()
def get_settings() -> AppSettings:
    """설정 싱글턴 인스턴스 반환 (캐싱)"""
    return AppSettings()
