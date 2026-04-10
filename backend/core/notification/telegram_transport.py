"""
Telegram Bot API HTTP 전송 레이어 (TelegramTransport)

단일 책임: HTTP POST 로 텔레그램 메시지를 전송한다.
  - 재시도: 최대 max_retries 회, 지수 backoff (1s × attempt)
  - 메시지 분할: 4096 자 초과 시 줄바꿈 기준으로 분할
  - 연속 발송 간 0.5 초 딜레이 (Telegram rate limit 회피)

이 모듈은 AlertManager, AlertLevel 등 도메인 객체에 의존하지 않는다.
포맷팅, 레벨 필터링, 상태 관리는 호출자(TelegramNotifier,
TelegramChannelAdapter) 의 책임이다.

SSOT 원칙: 텔레그램 Bot API 호출은 이 모듈의 send_text() 만 수행한다.
다른 모듈이 httpx/requests 로 직접 호출하는 것은 금지한다.
"""

import asyncio
from typing import Optional

import httpx

from config.logging import logger

# 텔레그램 메시지 최대 길이
TELEGRAM_MAX_LENGTH = 4096

# 텔레그램 Bot API 기본 URL 템플릿
_TELEGRAM_API_BASE = "https://api.telegram.org/bot{token}"


class TelegramTransport:
    """텔레그램 Bot API HTTP 전송 계층.

    Parameters
    ----------
    bot_token : str
        텔레그램 봇 토큰.
    chat_id : str
        대상 채팅 ID.
    max_retries : int, optional
        단일 메시지 전송 최대 재시도 횟수. 기본 3.
    timeout : float, optional
        HTTP 요청 타임아웃 (초). 기본 10.
    """

    def __init__(
        self,
        bot_token: str,
        chat_id: str,
        *,
        max_retries: int = 3,
        timeout: float = 10.0,
    ):
        self._bot_token = bot_token
        self._chat_id = chat_id
        self._max_retries = max_retries
        self._timeout = timeout
        self._base_url = _TELEGRAM_API_BASE.format(token=bot_token)

    # ── 공개 API ────────────────────────────

    @property
    def bot_token(self) -> str:
        return self._bot_token

    @property
    def chat_id(self) -> str:
        return self._chat_id

    def is_configured(self) -> bool:
        """봇 토큰과 채팅 ID 가 모두 비어있지 않으면 True."""
        return bool(self._bot_token) and bool(self._chat_id)

    async def send_text(self, text: str, parse_mode: str = "HTML") -> bool:
        """긴 텍스트를 분할하여 텔레그램으로 전송한다.

        4096 자를 초과하면 줄바꿈 기준으로 분할하여 순차 전송한다.
        하나라도 실패하면 즉시 False 를 반환한다.

        Parameters
        ----------
        text : str
            전송할 텍스트 (HTML 허용).
        parse_mode : str, optional
            텔레그램 parse_mode. 기본 ``"HTML"``.

        Returns
        -------
        bool
            모든 청크가 성공적으로 전송됐으면 True.
        """
        chunks = split_message(text)
        for i, chunk in enumerate(chunks):
            success = await self._send_single(chunk, parse_mode)
            if not success:
                return False
            # 연속 발송 시 딜레이 (rate limit 회피)
            if len(chunks) > 1 and i < len(chunks) - 1:
                await asyncio.sleep(0.5)
        return True

    # ── 내부 구현 ────────────────────────────

    async def _send_single(self, text: str, parse_mode: str) -> bool:
        """단일 메시지 전송 (재시도 포함)."""
        url = f"{self._base_url}/sendMessage"
        payload = {
            "chat_id": self._chat_id,
            "text": text,
            "parse_mode": parse_mode,
        }

        for attempt in range(1, self._max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    response = await client.post(url, json=payload)
                    if response.status_code == 200:
                        return True

                    logger.warning(
                        f"Telegram send failed (attempt {attempt}/{self._max_retries}): "
                        f"status={response.status_code}, body={response.text}"
                    )
            except Exception as e:
                logger.warning(f"Telegram send error (attempt {attempt}/{self._max_retries}): {e}")

            if attempt < self._max_retries:
                await asyncio.sleep(1.0 * attempt)

        logger.error("Telegram message send failed after max retries " f"(retries={self._max_retries})")
        return False


# ── 유틸리티 (모듈 레벨) ────────────────────


def split_message(text: str, max_length: int = TELEGRAM_MAX_LENGTH) -> list[str]:
    """긴 메시지를 max_length 이하 청크로 분할한다.

    줄바꿈 기준으로 분할하며, 줄바꿈이 없으면 max_length 에서 강제 절단한다.
    """
    if len(text) <= max_length:
        return [text]

    messages: list[str] = []
    while text:
        if len(text) <= max_length:
            messages.append(text)
            break

        split_idx = text.rfind("\n", 0, max_length)
        if split_idx == -1:
            split_idx = max_length

        messages.append(text[:split_idx])
        text = text[split_idx:].lstrip("\n")

    return messages


def create_transport(
    bot_token: Optional[str] = None,
    chat_id: Optional[str] = None,
    **kwargs,
) -> TelegramTransport:
    """설정에서 bot_token / chat_id 를 읽어 Transport 를 생성하는 팩토리.

    명시적으로 전달된 값이 있으면 그것을 우선한다.
    """
    from config.settings import get_settings

    settings = get_settings()
    return TelegramTransport(
        bot_token=bot_token if bot_token is not None else settings.telegram.bot_token,
        chat_id=chat_id if chat_id is not None else settings.telegram.chat_id,
        **kwargs,
    )
