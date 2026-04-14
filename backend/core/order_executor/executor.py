"""
주문 실행 엔진 모듈 (F-06-01/02)

포트폴리오 리밸런싱 및 신호 기반 매매를 위한 주문 실행 엔진입니다.
주문 검증, 여러 주문 유형(시장가/지정가/TWAP/VWAP) 지원,
잔고 확인, 상한선 검사, 중복 주문 방지, PostgreSQL 기록,
감사 로그 통합을 제공합니다.

주요 기능:
- async execute_order: 단일 주문 실행 (검증 → 잔고확인 → KIS API 제출)
- async execute_batch_orders: 배치 주문 (판매 우선 원칙)
- async _execute_market_order: 시장가 주문
- async _execute_limit_order: 지정가 주문
- async _execute_twap_order: 시간가중평균 (TWAP) 주문
- async _execute_vwap_order: 거래량가중평균 (VWAP) 주문
- _validate_order: 주문 검증 (잔고/상한선/중복)
- async _handle_unfilled: 장마감 30분전 미체결 주문 처리
"""

import asyncio
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import text
from tenacity import RetryError

from config.constants import Market, OrderSide, OrderStatus, OrderType
from config.logging import logger
from config.settings import get_settings
from contracts.converters import order_request_to_contract
from core.data_collector.kis_client import KISClient
from core.dry_run.engine import get_dry_run_engine
from core.monitoring.metrics import TRADING_GUARD_BLOCKS_TOTAL
from core.order_executor.price_guard import (
    PriceGuardConfig,
    Quote,
    QuoteFetchError,
    QuoteProvider,
    assert_pre_trade_price,
    check_post_trade_slippage,
    fetch_and_validate_quote,
)
from core.trading_guard import TradingGuard, TradingGuardBlocked, get_trading_guard
from db.database import async_session_factory
from db.repositories.audit_log import AuditLogger


# ══════════════════════════════════════════════════════════════════════════════
# 주문 요청 및 결과 데이터 구조
# ══════════════════════════════════════════════════════════════════════════════
@dataclass
class OrderRequest:
    """
    주문 요청

    사용자 또는 시스템이 제출하는 주문 요청 데이터입니다.
    모든 필수 주문 정보를 포함하고 있습니다.
    """

    ticker: str
    """종목 코드 (예: 005930 또는 AAPL)"""

    market: Market
    """시장 구분 (KRX, NYSE, NASDAQ, AMEX)"""

    side: OrderSide
    """주문 방향 (BUY 또는 SELL)"""

    quantity: int
    """주문 수량"""

    order_type: OrderType = OrderType.MARKET
    """주문 유형 (MARKET, LIMIT, TWAP, VWAP)"""

    limit_price: Optional[float] = None
    """지정가 (LIMIT 주문 시 필수)"""

    reason: str = ""
    """주문 사유 (리밸런싱, 신호 기반 등)"""

    def to_dict(self) -> dict[str, Any]:
        """딕셔너리 변환"""
        return {
            "ticker": self.ticker,
            "market": self.market.value,
            "side": self.side.value,
            "quantity": self.quantity,
            "order_type": self.order_type.value,
            "limit_price": self.limit_price,
            "reason": self.reason,
        }


@dataclass
class OrderResult:
    """
    주문 실행 결과

    주문 실행 후 결과를 포함하며, 체결 상태, 평균 가격, 오류 정보를 제공합니다.
    """

    order_id: str
    """한국투자증권 주문번호"""

    ticker: str
    """종목 코드"""

    market: Market
    """시장 구분"""

    side: OrderSide
    """주문 방향"""

    quantity: int
    """주문 수량"""

    filled_quantity: int
    """체결 수량"""

    avg_price: float
    """평균 체결 가격"""

    status: OrderStatus
    """주문 상태"""

    executed_at: datetime
    """주문 실행 시각 (UTC)"""

    order_type: OrderType = OrderType.MARKET
    """주문 유형 (MARKET, LIMIT, TWAP, VWAP)"""

    error_message: str = ""
    """오류 메시지 (실패 시에만 포함)"""

    def to_dict(self) -> dict[str, Any]:
        """딕셔너리 변환"""
        return {
            "order_id": self.order_id,
            "ticker": self.ticker,
            "market": self.market.value,
            "side": self.side.value,
            "quantity": self.quantity,
            "order_type": self.order_type.value,
            "filled_quantity": self.filled_quantity,
            "avg_price": self.avg_price,
            "status": self.status.value,
            "executed_at": self.executed_at.isoformat(),
            "error_message": self.error_message,
        }


def _unwrap_retry_error(exc: Exception) -> str:
    """RetryError를 unwrap하여 실제 원인 에러 메시지를 반환합니다.

    tenacity의 RetryError는 내부에 원본 예외를 감싸고 있어
    str(RetryError)가 'RetryError[<Future ...>]' 형태로만 표시됩니다.
    이 함수는 원본 예외를 추출하여 의미 있는 에러 메시지를 반환합니다.
    """
    if isinstance(exc, RetryError):
        last = exc.last_attempt
        if last and last.exception():
            original = last.exception()
            return str(original)
    return str(exc)


# ══════════════════════════════════════════════════════════════════════════════
# 주문 실행 엔진
# ══════════════════════════════════════════════════════════════════════════════
class OrderExecutor:
    """
    주문 실행 엔진

    포트폴리오 리밸런싱 및 신호 기반 매매를 위한 주문 실행을 관리합니다.
    주문 검증, 여러 주문 유형 지원, 잔고 확인, 상한선 검사,
    중복 주문 방지, 데이터베이스 기록, 감사 로그 통합을 제공합니다.

    주요 기능:
    - 단일 주문 실행 및 배치 주문 처리
    - 시장가, 지정가, TWAP, VWAP 주문 유형 지원
    - 주문 전 검증 (잔고 확인, 상한선 검사, 중복 방지)
    - PostgreSQL에 주문 정보 저장
    - 모든 주문 이벤트 감사 로그 기록
    - Mock 모드 지원
    - 재시도 로직 (지수 백오프)
    """

    def __init__(
        self,
        dry_run: bool = False,
        trading_guard: Optional[TradingGuard] = None,
        quote_provider: Optional[QuoteProvider] = None,
        price_guard_config: Optional[PriceGuardConfig] = None,
    ):
        """
        주문 실행 엔진 초기화

        한국투자증권 설정을 로드하고 KIS 클라이언트를 초기화합니다.

        Args:
            dry_run: True이면 실제 주문 실행 없이 가상 주문만 기록
            trading_guard: 주문 사전 검증용 TradingGuard.
                지정하지 않으면 프로세스 전역 싱글톤 `get_trading_guard()` 를
                사용하여 관리자 API 의 kill switch 조작이 즉시 전파되도록 한다.
            quote_provider: P1-정합성 §7.3 시세/가격 가드용 QuoteProvider.
                지정되지 않으면 본 가드는 비활성 상태가 되며, 이는 dry_run /
                backtest 경로에서만 허용된다. 운영(live) 경로에서 None 이면
                첫 주문 시도 시 fail-closed 로 거부된다.
            price_guard_config: 시세/가격 가드 임계값. 기본값은 보수적
                (5초 stale, ±2% pre-trade, ±1% post-trade slippage).
        """
        self._settings = get_settings()
        self._kis_client = KISClient()
        self._dry_run = dry_run
        # P0-5: 프로세스 전역 싱글톤 기본값으로 kill switch 공유.
        self._trading_guard = trading_guard if trading_guard is not None else get_trading_guard()
        # P1-정합성 §7.3: stale quote / pre-trade / post-trade 가드 주입점.
        self._quote_provider = quote_provider
        self._price_guard_config = price_guard_config or PriceGuardConfig()
        if dry_run:
            logger.info("OrderExecutor 초기화 완료 [DRY_RUN 모드]")
        else:
            logger.info("OrderExecutor 초기화 완료")

    @property
    def dry_run(self) -> bool:
        """드라이런 모드 여부"""
        return self._dry_run

    async def execute_order(self, request: OrderRequest) -> OrderResult:
        """
        단일 주문 실행

        주문 요청을 검증하고, 잔고를 확인한 후, 적절한 주문 유형별
        실행 메서드를 호출하여 주문을 체결합니다.

        Args:
            request: 주문 요청 데이터

        Returns:
            OrderResult: 주문 실행 결과

        주문 실행 과정:
        1. 주문 검증 (잔고 확인, 상한선 검사, 중복 방지)
        2. 주문 유형에 따른 실행 (시장가/지정가/TWAP/VWAP)
        3. 결과를 PostgreSQL에 저장
        4. 감사 로그 기록
        """
        try:
            logger.info(f"주문 실행 시작: {request.ticker} {request.side.value} {request.quantity}")

            # 계약 검증: Pydantic validation을 통해 주문 파라미터 무결성 강제
            try:
                order_request_to_contract(request)
            except Exception as e:
                logger.error(f"[Contract] OrderIntent 계약 위반: {request.ticker} — {e}")
                raise ValueError(f"주문 계약 위반: {e}") from e

            # P0-5: TradingGuard 사전 검증 (kill switch / 일일손실 / MDD /
            # 연속손실 / BUY 주문 금액 한도). 차단 시 KIS 호출 없이 즉시 실패.
            guard_result = self._trading_guard.check_pre_order(
                ticker=request.ticker,
                side=request.side,
                quantity=request.quantity,
                limit_price=request.limit_price,
            )
            if not guard_result.allowed:
                reason_code = self._map_guard_reason_code(guard_result.reason)
                TRADING_GUARD_BLOCKS_TOTAL.labels(reason_code=reason_code).inc()
                logger.critical(
                    "TradingGuard 주문 차단: ticker=%s side=%s reason_code=%s reason=%s",
                    request.ticker,
                    request.side.value,
                    reason_code,
                    guard_result.reason,
                )
                raise TradingGuardBlocked(guard_result.reason, reason_code=reason_code)

            # 주문 검증
            self._validate_order(request)

            # P1-정합성 §7.3: 시세/가격 가드 (pre-trade).
            # live 경로에서만 활성화 — dry_run 과 backtest 모드는 모의
            # 가격(100.0) 을 사용하므로 시세 가드를 우회한다. live 경로에서
            # provider 가 주입되지 않았다면 fail-closed.
            reference_quote: Optional[Quote] = None
            if not self._dry_run and not self._kis_client.is_backtest:
                if self._quote_provider is None:
                    raise QuoteFetchError(
                        request.ticker,
                        "live OrderExecutor requires a quote_provider; refusing to trade blind",
                    )
                reference_quote = await fetch_and_validate_quote(
                    self._quote_provider,
                    ticker=request.ticker,
                    market=request.market,
                    max_age_seconds=self._price_guard_config.max_quote_age_seconds,
                )
                if request.order_type == OrderType.LIMIT and request.limit_price is not None:
                    assert_pre_trade_price(
                        ticker=request.ticker,
                        market=request.market,
                        side=request.side,
                        reference_price=reference_quote.price,
                        order_price=request.limit_price,
                        max_deviation_pct=self._price_guard_config.max_pre_trade_deviation_pct,
                    )

            # 주문 유형별 실행
            if request.order_type == OrderType.MARKET:
                result = await self._execute_market_order(request)
            elif request.order_type == OrderType.LIMIT:
                result = await self._execute_limit_order(request)
            elif request.order_type == OrderType.TWAP:
                result = await self._execute_twap_order(request)
            elif request.order_type == OrderType.VWAP:
                result = await self._execute_vwap_order(request)
            else:
                raise ValueError(f"지원하지 않는 주문 유형: {request.order_type}")

            # P1-정합성 §7.3: post-trade slippage 관측 (롤백 불가, 경보만).
            if reference_quote is not None and result.filled_quantity > 0 and result.avg_price > 0:
                exceeded = check_post_trade_slippage(
                    ticker=request.ticker,
                    market=request.market,
                    side=request.side,
                    reference_price=reference_quote.price,
                    fill_price=result.avg_price,
                    max_slippage_pct=self._price_guard_config.max_post_trade_slippage_pct,
                )
                if exceeded:
                    logger.critical(
                        "Post-trade slippage exceeded: ticker=%s side=%s " "reference=%.4f fill=%.4f order_id=%s",
                        request.ticker,
                        request.side.value,
                        reference_quote.price,
                        result.avg_price,
                        result.order_id,
                    )

            # P1-정합성: 체결이 확정된 경우에만 ledger 에 반영.
            # ReconciliationRunner 가 본 ledger 와 브로커 잔고를 비교하므로,
            # 부분 체결도 그 시점의 실제 수량(filled_quantity)만 누적한다.
            if result.status in (OrderStatus.FILLED, OrderStatus.PARTIAL) and result.filled_quantity > 0:
                from core.portfolio_ledger import (
                    LedgerInvariantError,
                    get_portfolio_ledger,
                )

                try:
                    await get_portfolio_ledger().record_fill(
                        ticker=request.ticker,
                        side=request.side,
                        quantity=float(result.filled_quantity),
                    )
                except LedgerInvariantError as ledger_exc:
                    # ledger 가 거부했다 = 내부 정합성이 이미 깨졌다는 신호.
                    # 주문은 이미 브로커에 체결되어 롤백 불가하므로, 사후
                    # 관측만 하고 reconcile 사이클이 mismatch 를 잡도록 둔다.
                    logger.critical(
                        "PortfolioLedger refused fill: ticker=%s side=%s qty=%d order_id=%s err=%s",
                        request.ticker,
                        request.side.value,
                        result.filled_quantity,
                        result.order_id,
                        ledger_exc,
                    )

            # 결과를 데이터베이스에 저장
            await self._store_order(result)

            # 감사 로그 기록
            async with async_session_factory() as db_session:
                audit_logger = AuditLogger(db_session)
                await audit_logger.log(
                    action_type="ORDER_EXECUTED",
                    module="order_executor",
                    description=f"주문 실행: {request.ticker} {request.side.value} {result.filled_quantity}/{request.quantity}",
                    after_state=result.to_dict(),
                    metadata={"reason": request.reason},
                )

            logger.info(f"주문 실행 완료: {result.order_id}")
            return result

        except Exception as e:
            # RetryError를 unwrap하여 실제 KIS 에러 코드를 DB에 기록
            error_message = _unwrap_retry_error(e)
            logger.error(f"주문 실행 실패: {error_message}")
            result = OrderResult(
                order_id=f"FAIL_{uuid.uuid4().hex[:12]}",
                ticker=request.ticker,
                market=request.market,
                side=request.side,
                quantity=request.quantity,
                filled_quantity=0,
                avg_price=0.0,
                status=OrderStatus.FAILED,
                executed_at=datetime.now(timezone.utc),
                order_type=request.order_type,
                error_message=error_message,
            )
            await self._store_order(result)
            raise

    async def execute_batch_orders(self, requests: list[OrderRequest]) -> list[OrderResult]:
        """
        배치 주문 실행

        여러 주문을 순차적으로 실행합니다. 판매 주문을 먼저 실행한 후
        매수 주문을 실행하여 자금 가용성을 확보합니다.

        Args:
            requests: 주문 요청 리스트

        Returns:
            list[OrderResult]: 주문 실행 결과 리스트

        주문 순서:
        1. SELL (판매) 주문을 모두 실행
        2. BUY (매수) 주문을 모두 실행
        """
        logger.info(f"배치 주문 실행 시작: 총 {len(requests)}건")

        # 판매 주문과 매수 주문 분리
        sell_orders = [r for r in requests if r.side == OrderSide.SELL]
        buy_orders = [r for r in requests if r.side == OrderSide.BUY]

        results: list[OrderResult] = []

        # SELL 주문 실행
        for request in sell_orders:
            try:
                result = await self.execute_order(request)
                results.append(result)
                await asyncio.sleep(0.5)  # API 호출 간격 (rate limit)
            except Exception as e:
                logger.warning(f"SELL 주문 실패: {request.ticker} - {e}")
                results.append(
                    OrderResult(
                        order_id="",
                        ticker=request.ticker,
                        market=request.market,
                        side=request.side,
                        quantity=request.quantity,
                        filled_quantity=0,
                        avg_price=0.0,
                        status=OrderStatus.FAILED,
                        executed_at=datetime.now(timezone.utc),
                        error_message=str(e),
                    )
                )

        # BUY 주문 실행
        for request in buy_orders:
            try:
                result = await self.execute_order(request)
                results.append(result)
                await asyncio.sleep(0.5)  # API 호출 간격 (rate limit)
            except Exception as e:
                logger.warning(f"BUY 주문 실패: {request.ticker} - {e}")
                results.append(
                    OrderResult(
                        order_id="",
                        ticker=request.ticker,
                        market=request.market,
                        side=request.side,
                        quantity=request.quantity,
                        filled_quantity=0,
                        avg_price=0.0,
                        status=OrderStatus.FAILED,
                        executed_at=datetime.now(timezone.utc),
                        error_message=str(e),
                    )
                )

        logger.info(f"배치 주문 실행 완료: {len(results)}건 결과")
        return results

    async def _execute_market_order(self, request: OrderRequest) -> OrderResult:
        """
        시장가 주문 실행

        현재 시장 가격으로 즉시 주문을 체결합니다.

        Args:
            request: 주문 요청 데이터

        Returns:
            OrderResult: 주문 실행 결과
        """
        logger.info(f"시장가 주문 실행: {request.ticker} {request.side.value} {request.quantity}")

        try:
            if self._dry_run:
                # 드라이런 모드: 가상 주문 기록만 수행, API 호출 없음
                engine = get_dry_run_engine()
                engine.record_order(
                    ticker=request.ticker,
                    market=request.market,
                    side=request.side,
                    quantity=request.quantity,
                    order_type=request.order_type,
                    limit_price=request.limit_price,
                    reason=request.reason,
                    estimated_price=100.0,
                )
                result = OrderResult(
                    order_id=f"DRY_{request.ticker}_{datetime.now().timestamp():.0f}",
                    ticker=request.ticker,
                    market=request.market,
                    side=request.side,
                    quantity=request.quantity,
                    filled_quantity=request.quantity,
                    avg_price=0.0,
                    status=OrderStatus.FILLED,
                    executed_at=datetime.now(timezone.utc),
                )
                logger.info(f"[DRY_RUN] 시장가 가상 주문: {result.order_id}")
                return result

            if self._kis_client.is_backtest:
                # Mock 모드: 시뮬레이션
                result = OrderResult(
                    order_id=f"MOCK_{request.ticker}_{datetime.now().timestamp()}",
                    ticker=request.ticker,
                    market=request.market,
                    side=request.side,
                    quantity=request.quantity,
                    filled_quantity=request.quantity,
                    avg_price=100.0,  # Mock 가격
                    status=OrderStatus.FILLED,
                    executed_at=datetime.now(timezone.utc),
                )
            else:
                # 실제 API 호출 전 토큰 유효성 사전 체크
                if not self._kis_client.has_valid_token:
                    logger.warning(f"KIS 토큰 미확보 상태에서 주문 시도: {request.ticker} — " "토큰 갱신을 시도합니다")
                    try:
                        await self._kis_client._token_manager.get_access_token()
                    except Exception as token_err:
                        from core.data_collector.kis_client import KISAPIError

                        raise KISAPIError(
                            "TOKEN_UNAVAILABLE",
                            f"KIS 접근 토큰 확보 실패로 주문을 실행할 수 없습니다: "
                            f"{_unwrap_retry_error(token_err)}",
                        ) from token_err

                if request.market == Market.KRX:
                    api_result = await self._kis_client.place_kr_order(
                        ticker=request.ticker,
                        side=request.side.value,
                        quantity=request.quantity,
                        price=0,  # 시장가
                        order_type="01",  # 시장가
                    )
                else:
                    api_result = await self._kis_client.place_us_order(
                        ticker=request.ticker,
                        side=request.side.value,
                        quantity=request.quantity,
                        price=0,  # 시장가
                    )

                # API 결과를 OrderResult로 변환
                raw_order_id = api_result.get("order_id", "")
                result = OrderResult(
                    order_id=raw_order_id if raw_order_id else f"KIS_{uuid.uuid4().hex[:12]}",
                    ticker=request.ticker,
                    market=request.market,
                    side=request.side,
                    quantity=request.quantity,
                    filled_quantity=int(api_result.get("filled_qty", 0)),
                    avg_price=float(api_result.get("avg_price", 0)),
                    status=OrderStatus.SUBMITTED,
                    executed_at=datetime.now(timezone.utc),
                )

            logger.info(f"시장가 주문 완료: {result.order_id}")

            # 체결 상태 폴링 태스크 생성 (SUBMITTED 상태일 때만)
            if result.status == OrderStatus.SUBMITTED:
                self._start_settlement_polling(result)

            return result

        except Exception as e:
            logger.error(f"시장가 주문 실패: {e}")
            raise

    def _start_settlement_polling(self, result: OrderResult) -> None:
        """주문 체결 상태 폴링 백그라운드 태스크를 생성한다.

        asyncio 이벤트 루프가 없는 환경(테스트 등)에서는 경고만 출력하고 스킵.
        """
        try:
            from core.order_executor.settlement_poller import poll_after_execution

            asyncio.create_task(
                poll_after_execution(
                    kis_client=self._kis_client,
                    order_id=result.order_id,
                    ticker=result.ticker,
                    market=result.market.value if isinstance(result.market, Market) else str(result.market),
                )
            )
            logger.debug(f"[SettlementPoller] 폴링 태스크 생성: {result.order_id}")
        except RuntimeError:
            logger.warning(f"[SettlementPoller] 이벤트 루프 없음, 폴링 스킵: {result.order_id}")

    async def _execute_limit_order(self, request: OrderRequest) -> OrderResult:
        """
        지정가 주문 실행

        사용자가 지정한 가격으로 주문을 체결합니다.

        Args:
            request: 주문 요청 데이터 (limit_price 필수)

        Returns:
            OrderResult: 주문 실행 결과
        """
        if request.limit_price is None or request.limit_price <= 0:
            raise ValueError("지정가 주문에는 유효한 limit_price가 필요합니다")

        logger.info(
            f"지정가 주문 실행: {request.ticker} {request.side.value} " f"{request.quantity}@{request.limit_price}"
        )

        try:
            if self._dry_run:
                # 드라이런 모드: 가상 주문 기록
                engine = get_dry_run_engine()
                engine.record_order(
                    ticker=request.ticker,
                    market=request.market,
                    side=request.side,
                    quantity=request.quantity,
                    order_type=request.order_type,
                    limit_price=request.limit_price,
                    reason=request.reason,
                    estimated_price=request.limit_price,
                )
                result = OrderResult(
                    order_id=f"DRY_{request.ticker}_{datetime.now().timestamp():.0f}",
                    ticker=request.ticker,
                    market=request.market,
                    side=request.side,
                    quantity=request.quantity,
                    filled_quantity=int(request.quantity * 0.5),
                    avg_price=request.limit_price,
                    status=OrderStatus.PARTIAL,
                    executed_at=datetime.now(timezone.utc),
                )
                logger.info(f"[DRY_RUN] 지정가 가상 주문: {result.order_id}")
                return result

            if self._kis_client.is_backtest:
                # Mock 모드
                result = OrderResult(
                    order_id=f"MOCK_{request.ticker}_{datetime.now().timestamp()}",
                    ticker=request.ticker,
                    market=request.market,
                    side=request.side,
                    quantity=request.quantity,
                    filled_quantity=int(request.quantity * 0.5),  # Mock: 50% 체결
                    avg_price=request.limit_price,
                    status=OrderStatus.PARTIAL,
                    executed_at=datetime.now(timezone.utc),
                )
            else:
                # 실제 API 호출 전 토큰 유효성 사전 체크
                if not self._kis_client.has_valid_token:
                    logger.warning(f"KIS 토큰 미확보 상태에서 주문 시도: {request.ticker} — " "토큰 갱신을 시도합니다")
                    try:
                        await self._kis_client._token_manager.get_access_token()
                    except Exception as token_err:
                        from core.data_collector.kis_client import KISAPIError

                        raise KISAPIError(
                            "TOKEN_UNAVAILABLE",
                            f"KIS 접근 토큰 확보 실패로 주문을 실행할 수 없습니다: "
                            f"{_unwrap_retry_error(token_err)}",
                        ) from token_err

                if request.market == Market.KRX:
                    api_result = await self._kis_client.place_kr_order(
                        ticker=request.ticker,
                        side=request.side.value,
                        quantity=request.quantity,
                        price=int(request.limit_price),
                        order_type="00",  # 지정가
                    )
                else:
                    api_result = await self._kis_client.place_us_order(
                        ticker=request.ticker,
                        side=request.side.value,
                        quantity=request.quantity,
                        price=request.limit_price,
                    )

                result = OrderResult(
                    order_id=api_result.get("order_id", ""),
                    ticker=request.ticker,
                    market=request.market,
                    side=request.side,
                    quantity=request.quantity,
                    filled_quantity=int(api_result.get("filled_qty", 0)),
                    avg_price=float(api_result.get("avg_price", 0)),
                    status=OrderStatus.SUBMITTED,
                    executed_at=datetime.now(timezone.utc),
                )

            logger.info(f"지정가 주문 완료: {result.order_id}")

            # 체결 상태 폴링 태스크 생성 (SUBMITTED 상태일 때만)
            if result.status == OrderStatus.SUBMITTED:
                self._start_settlement_polling(result)

            return result

        except Exception as e:
            logger.error(f"지정가 주문 실패: {e}")
            raise

    # ══════════════════════════════════════════════════════════════════════════
    # TWAP 분할 주문 (F-06-01-A)
    # ══════════════════════════════════════════════════════════════════════════
    async def _execute_twap_order(
        self,
        request: OrderRequest,
        num_intervals: int = 6,
        interval_seconds: int = 300,
        max_retry_per_slice: int = 2,
    ) -> OrderResult:
        """
        TWAP (시간가중평균) 분할 주문 실행

        총 주문 수량을 num_intervals 구간으로 균등 분할하여
        interval_seconds 간격으로 시장가 주문을 체결합니다.

        분할 알고리즘:
            slice_qty = total_qty // num_intervals
            마지막 구간에 나머지(remainder) 합산
            부분 실패 시 미체결 수량을 다음 구간에 이월 (적응적 재시도)

        Args:
            request: 원본 주문 요청 (quantity = 총 수량)
            num_intervals: 분할 구간 수 (기본 6)
            interval_seconds: 구간 간 대기 시간 (기본 300초 = 5분)
            max_retry_per_slice: 구간당 최대 재시도 횟수

        Returns:
            OrderResult: 누적 체결 결과 (order_id = "TWAP_{ticker}_{ts}")
        """
        logger.info(
            f"TWAP 주문 실행: {request.ticker} {request.side.value} "
            f"{request.quantity}주 → {num_intervals}구간 × {interval_seconds}초"
        )

        try:
            base_qty = request.quantity // num_intervals
            remainder = request.quantity % num_intervals

            total_filled = 0
            total_cost = 0.0
            sub_results: list[OrderResult] = []
            carryover = 0  # 이전 구간 미체결 이월 수량

            for i in range(num_intervals):
                # 구간 수량: 기본 + 마지막구간 나머지 + 이전 이월
                slice_qty = base_qty + carryover
                if i == num_intervals - 1:
                    slice_qty += remainder

                if slice_qty <= 0:
                    continue

                filled_in_slice = 0

                # 재시도 루프
                for attempt in range(max_retry_per_slice + 1):
                    remaining = slice_qty - filled_in_slice
                    if remaining <= 0:
                        break

                    sub_request = OrderRequest(
                        ticker=request.ticker,
                        market=request.market,
                        side=request.side,
                        quantity=remaining,
                        order_type=OrderType.MARKET,
                        reason=f"TWAP {i+1}/{num_intervals}" + (f" retry-{attempt}" if attempt > 0 else ""),
                    )

                    try:
                        result = await self._execute_market_order(sub_request)
                        sub_results.append(result)
                        filled_in_slice += result.filled_quantity
                        total_cost += result.filled_quantity * result.avg_price
                    except Exception as e:
                        logger.warning(f"TWAP 구간 {i+1} attempt {attempt} 실패: {e}")

                total_filled += filled_in_slice
                carryover = slice_qty - filled_in_slice  # 미체결 → 다음 구간 이월

                # 마지막 구간이 아니면 대기
                if i < num_intervals - 1:
                    await asyncio.sleep(interval_seconds)

            avg_price = total_cost / total_filled if total_filled > 0 else 0.0

            final_result = OrderResult(
                order_id=f"TWAP_{request.ticker}_{datetime.now().timestamp()}",
                ticker=request.ticker,
                market=request.market,
                side=request.side,
                quantity=request.quantity,
                filled_quantity=total_filled,
                avg_price=avg_price,
                status=(
                    OrderStatus.FILLED
                    if total_filled >= request.quantity
                    else OrderStatus.PARTIAL if total_filled > 0 else OrderStatus.FAILED
                ),
                executed_at=datetime.now(timezone.utc),
            )

            logger.info(
                f"TWAP 주문 완료: {total_filled}/{request.quantity} 체결, "
                f"평균가 {avg_price:,.0f}, 이월 미체결 {carryover}"
            )
            return final_result

        except Exception as e:
            logger.error(f"TWAP 주문 실패: {e}")
            raise

    # ══════════════════════════════════════════════════════════════════════════
    # VWAP 분할 주문 (F-06-01-A)
    # ══════════════════════════════════════════════════════════════════════════

    # 일반적인 주식 시장 일중 거래량 프로필 (U자형 커브)
    # 인덱스 0~5 = 구간 1~6, 값 = 해당 구간의 거래량 비중
    # 개장/마감 시 거래량 집중, 중간 시간대 거래량 하락 패턴 반영
    VWAP_VOLUME_PROFILE = [0.22, 0.12, 0.10, 0.10, 0.16, 0.30]

    async def _execute_vwap_order(
        self,
        request: OrderRequest,
        num_intervals: int = 6,
        interval_seconds: int = 300,
        volume_profile: Optional[list[float]] = None,
    ) -> OrderResult:
        """
        VWAP (거래량가중평균) 분할 주문 실행

        일중 거래량 프로필(U자형 커브)을 기반으로 주문 수량을
        비균등 분할하여 시장 충격을 최소화합니다.

        거래량 프로필 기본값 (6구간):
            [0.22, 0.12, 0.10, 0.10, 0.16, 0.30]
            → 개장 22%, 오전중반 12%, 점심 10%, 오후초 10%,
              오후중반 16%, 마감직전 30%

        분할 알고리즘:
            slice_qty[i] = round(total_qty × profile[i])
            나머지 = total_qty - sum(slice_qty) → 최대 비중 구간에 합산
            부분 실패 시 적응적 이월 (TWAP과 동일)

        Args:
            request: 원본 주문 요청
            num_intervals: 분할 구간 수 (기본 6)
            interval_seconds: 구간 간 대기 시간 (기본 300초)
            volume_profile: 구간별 거래량 비중 리스트 (합 = 1.0)

        Returns:
            OrderResult: 누적 체결 결과 (order_id = "VWAP_{ticker}_{ts}")
        """
        logger.info(
            f"VWAP 주문 실행: {request.ticker} {request.side.value} "
            f"{request.quantity}주 → {num_intervals}구간 (거래량 프로필 기반)"
        )

        try:
            # 거래량 프로필 설정
            profile = volume_profile or self.VWAP_VOLUME_PROFILE

            # 구간 수와 프로필 길이 맞춤
            if len(profile) != num_intervals:
                # 프로필 길이가 맞지 않으면 균등 분할로 폴백
                profile = [1.0 / num_intervals] * num_intervals

            # 정규화
            profile_sum = sum(profile)
            if profile_sum > 0:
                profile = [p / profile_sum for p in profile]

            # 구간별 수량 계산 (비균등 분할)
            slice_quantities = [max(1, round(request.quantity * p)) for p in profile]

            # 총합 보정 (반올림 오차 → 최대 비중 구간에서 조정)
            total_allocated = sum(slice_quantities)
            diff = request.quantity - total_allocated
            if diff != 0:
                max_idx = profile.index(max(profile))
                slice_quantities[max_idx] += diff

            logger.info(f"VWAP 구간별 수량: {slice_quantities} " f"(프로필: {[f'{p:.0%}' for p in profile]})")

            total_filled = 0
            total_cost = 0.0
            sub_results: list[OrderResult] = []
            carryover = 0

            for i in range(num_intervals):
                slice_qty = slice_quantities[i] + carryover

                if slice_qty <= 0:
                    continue

                sub_request = OrderRequest(
                    ticker=request.ticker,
                    market=request.market,
                    side=request.side,
                    quantity=slice_qty,
                    order_type=OrderType.MARKET,
                    reason=f"VWAP {i+1}/{num_intervals} ({profile[i]:.0%})",
                )

                try:
                    result = await self._execute_market_order(sub_request)
                    sub_results.append(result)
                    total_filled += result.filled_quantity
                    total_cost += result.filled_quantity * result.avg_price
                    carryover = slice_qty - result.filled_quantity
                except Exception as e:
                    logger.warning(f"VWAP 구간 {i+1} 실패: {e}")
                    carryover = slice_qty  # 전량 이월

                if i < num_intervals - 1:
                    await asyncio.sleep(interval_seconds)

            avg_price = total_cost / total_filled if total_filled > 0 else 0.0

            final_result = OrderResult(
                order_id=f"VWAP_{request.ticker}_{datetime.now().timestamp()}",
                ticker=request.ticker,
                market=request.market,
                side=request.side,
                quantity=request.quantity,
                filled_quantity=total_filled,
                avg_price=avg_price,
                status=(
                    OrderStatus.FILLED
                    if total_filled >= request.quantity
                    else OrderStatus.PARTIAL if total_filled > 0 else OrderStatus.FAILED
                ),
                executed_at=datetime.now(timezone.utc),
            )

            logger.info(f"VWAP 주문 완료: {total_filled}/{request.quantity} 체결, " f"평균가 {avg_price:,.0f}")
            return final_result

        except Exception as e:
            logger.error(f"VWAP 주문 실패: {e}")
            raise

    @staticmethod
    def _map_guard_reason_code(reason: str) -> str:
        """TradingGuard reason 문자열 → Prometheus 라벨 코드."""
        # reason 은 한국어라 label 로 부적합. 접두 키워드로 분류.
        if "Kill Switch" in reason:
            return "kill_switch"
        if "일일 손실" in reason:
            return "daily_loss"
        if "MDD" in reason or "낙폭" in reason:
            return "max_drawdown"
        if "연속 손실" in reason:
            return "consecutive_losses"
        if "주문 금액" in reason:
            return "order_amount"
        if "LIVE 모드" in reason or "production" in reason:
            return "environment"
        if "잔고" in reason:
            return "capital"
        return "other"

    def _validate_order(self, request: OrderRequest) -> None:
        """
        주문 검증

        주문 실행 전 다음 사항을 확인합니다:
        1. 잔고 확인 (매수 시)
        2. 주문금액 상한 검사
        3. 동일 종목 중복 주문 방지

        Args:
            request: 주문 요청 데이터

        Raises:
            ValueError: 검증 실패 시
        """
        logger.debug(f"주문 검증: {request.ticker} {request.side.value} {request.quantity}")

        # 수량 검증
        if request.quantity <= 0:
            raise ValueError(f"주문 수량은 0보다 커야 합니다: {request.quantity}")

        # 지정가 주문 검증
        if request.order_type == OrderType.LIMIT:
            if request.limit_price is None or request.limit_price <= 0:
                raise ValueError("지정가 주문에는 유효한 limit_price가 필요합니다")

        logger.debug(f"주문 검증 완료: {request.ticker}")

    async def _store_order(self, result: OrderResult) -> None:
        """
        주문 정보를 PostgreSQL에 저장

        Args:
            result: 주문 실행 결과
        """
        try:
            async with async_session_factory() as db_session:
                query = text(
                    """
                    INSERT INTO orders (
                        order_id, ticker, market, side, order_type, quantity,
                        filled_quantity, filled_price, status, created_at, error_message
                    ) VALUES (
                        :order_id, :ticker, :market, :side, :order_type, :quantity,
                        :filled_quantity, :filled_price, :status, :created_at, :error_message
                    )
                """
                )

                await db_session.execute(
                    query,
                    {
                        "order_id": result.order_id,
                        "ticker": result.ticker,
                        "market": result.market.value,
                        "side": result.side.value,
                        "order_type": result.order_type.value,
                        "quantity": result.quantity,
                        "filled_quantity": result.filled_quantity,
                        "filled_price": result.avg_price,
                        "status": result.status.value,
                        "created_at": result.executed_at,
                        "error_message": result.error_message,
                    },
                )
                await db_session.commit()
                logger.debug(f"주문 저장 완료: {result.order_id}")

        except Exception as e:
            logger.error(f"주문 저장 실패: {e}")

    async def _handle_unfilled(self, order_id: str, remaining_qty: int) -> OrderResult:
        """
        미체결 주문 처리 (장마감 30분전)

        장 마감 30분 전에도 미체결된 주문은 시장가로 일괄 체결합니다.

        Args:
            order_id: 미체결 주문번호
            remaining_qty: 남은 수량

        Returns:
            OrderResult: 추가 체결 결과
        """
        logger.warning(f"미체결 주문 시장가 전환: {order_id} qty={remaining_qty}")

        try:
            if self._kis_client.is_backtest:
                # Mock 모드: 시뮬레이션
                result = OrderResult(
                    order_id=order_id,
                    ticker=f"MOCK_{order_id}",
                    market=Market.KRX,
                    side=OrderSide.BUY,
                    quantity=remaining_qty,
                    filled_quantity=remaining_qty,
                    avg_price=100.0,  # Mock 가격
                    status=OrderStatus.FILLED,
                    executed_at=datetime.now(timezone.utc),
                )
                logger.info(f"미체결 주문 Mock 처리 완료: {order_id}")
                return result

            # 실제 주문 처리
            # 1. 주문 정보 조회
            order_detail = await self._kis_client.get_kr_order_detail(order_id)
            ticker = order_detail.get("ticker", "")
            side = order_detail.get("side", "BUY")
            market_type = order_detail.get("market", Market.KRX)

            # side 값 정규화 (OrderSide.BUY / OrderSide.SELL)
            if isinstance(side, str):
                side = OrderSide(side) if side in ["BUY", "SELL"] else OrderSide.BUY
            elif not isinstance(side, OrderSide):
                side = OrderSide.BUY

            # market 값 정규화
            if isinstance(market_type, str):
                market_type = Market(market_type) if market_type in ["KRX", "NYSE", "NASDAQ", "AMEX"] else Market.KRX
            elif not isinstance(market_type, Market):
                market_type = Market.KRX

            if not ticker:
                raise ValueError(f"주문 정보에서 종목 코드를 찾을 수 없습니다: {order_id}")

            logger.info(f"미체결 주문 정보 조회 완료: {order_id} ticker={ticker} side={side.value} qty={remaining_qty}")

            # 2. 기존 미체결 주문 취소
            try:
                await self._kis_client.cancel_kr_order(order_id)
                logger.info(f"미체결 주문 취소 완료: {order_id}")
            except Exception as e:
                logger.warning(f"미체결 주문 취소 실패: {order_id} - {e}")

            # 3. 시장가 주문으로 남은 수량 체결
            if market_type == Market.KRX:
                api_result = await self._kis_client.place_kr_order(
                    ticker=ticker,
                    side=side.value,
                    quantity=remaining_qty,
                    price=0,  # 시장가
                    order_type="01",  # 시장가
                )
            else:
                api_result = await self._kis_client.place_us_order(
                    ticker=ticker,
                    side=side.value,
                    quantity=remaining_qty,
                    price=0,  # 시장가
                )

            # 4. 결과 생성
            new_order_id = api_result.get("order_id", order_id)
            result = OrderResult(
                order_id=new_order_id,
                ticker=ticker,
                market=market_type,
                side=side,
                quantity=remaining_qty,
                filled_quantity=int(api_result.get("filled_qty", 0)),
                avg_price=float(api_result.get("avg_price", 0)),
                status=OrderStatus.SUBMITTED,
                executed_at=datetime.now(timezone.utc),
            )

            logger.info(f"미체결 주문 시장가 전환 완료: 원주문={order_id} 신규주문={new_order_id} qty={remaining_qty}")
            return result

        except Exception as e:
            logger.error(f"미체결 주문 처리 실패: {order_id} - {e}")
            # 오류 발생 시 실패 결과 반환
            result = OrderResult(
                order_id=order_id,
                ticker="",
                market=Market.KRX,
                side=OrderSide.BUY,
                quantity=remaining_qty,
                filled_quantity=0,
                avg_price=0.0,
                status=OrderStatus.FAILED,
                executed_at=datetime.now(timezone.utc),
                error_message=str(e),
            )
            return result
