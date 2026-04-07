"""KIS 토큰 발급 startup 단계 jittered backoff.

배경
----
CD 배포 직후 다수 컨테이너가 동시에 부팅되면 KIS 의 "1분 1회 토큰 발급 제한"
(EGW00133) 에 1차 충돌하는 경우가 잦다. lifespan startup 이 즉시 1회만 발급을
시도하면 그 1차 충돌이 바로 ``app.state.kis_degraded = True`` 로 이어지고, 이후
``kis_recovery.try_recover_kis()`` 의 75초 쿨다운 동안 health 가 degraded 로 노출
된다. 비록 자동 복원이 동작하더라도 "배포 직후 약 75초 동안 무조건 degraded" 라는
가시적 회귀가 남는다.

본 모듈은 startup 시점에 짧은 jitter (기본 0~15초) 를 두어 동시 부팅 컨테이너들이
KIS 발급 윈도우를 균등하게 나눠 쓰도록 한다. jitter 만으로는 EGW00133 을 완전히
없애지 못하지만 1차 충돌 빈도를 통계적으로 감소시켜, degraded 진입 자체를 줄인다.
1차 시도가 실패하면 그 이상의 in-startup 재시도는 하지 않고 ``kis_recovery`` 의
정상 회복 경로에 위임한다 (k8s readiness probe 와의 충돌 회피).

설계 원칙
---------
- **단일 책임**: jitter + 토큰 발급 1회만 책임. retry 로직은 ``kis_recovery`` 가
  담당 — 책임 범위 분리.
- **테스트 가능성**: ``sleep_fn`` / ``random_fn`` 을 주입 가능하게 하여 실제 시간
  대기 없이 단위 테스트 가능.
- **환경변수 1개**: ``KIS_STARTUP_JITTER_MAX_SECONDS`` (기본 15.0). 0 이하 또는
  파싱 실패 시 jitter 없이 즉시 시도 (기존 동작 유지 — 명시적 opt-out 가능).
"""

from __future__ import annotations

import asyncio
import random
from typing import Awaitable, Callable

from loguru import logger

from core.data_collector.kis_client import KISClient

DEFAULT_JITTER_MAX_SECONDS = 15.0


async def jittered_token_issue(
    client_factory: Callable[[], KISClient],
    *,
    jitter_max_seconds: float = DEFAULT_JITTER_MAX_SECONDS,
    sleep_fn: Callable[[float], Awaitable[None]] = asyncio.sleep,
    random_fn: Callable[[float, float], float] = random.uniform,
) -> KISClient:
    """[0, jitter_max_seconds) 구간 jitter 후 KIS 토큰을 발급한다.

    Args:
        client_factory: ``KISClient`` 인스턴스를 생성하는 동기 콜백. lifespan
            안에서 글로벌 클라이언트를 교체할 수 있도록 lambda 로 주입한다.
        jitter_max_seconds: jitter 상한 (초). 0 이하면 jitter 없이 즉시 시도.
        sleep_fn: 비동기 sleep 함수 — 테스트 주입용. 기본은 ``asyncio.sleep``.
        random_fn: 균등 분포 난수 함수 — 테스트 주입용. 기본은 ``random.uniform``.

    Returns:
        토큰 발급에 성공한 ``KISClient`` 인스턴스.

    Raises:
        ``client_factory`` 또는 ``get_access_token()`` 이 raise 한 예외를 그대로
        상위로 전파한다. lifespan 이 이를 잡아서 degraded 처리한다.
    """
    if jitter_max_seconds > 0:
        delay = random_fn(0.0, jitter_max_seconds)
        logger.info(f"KIS startup jitter: {delay:.2f}s 대기 후 토큰 발급 시도 " f"(max={jitter_max_seconds}s)")
        await sleep_fn(delay)
    else:
        logger.info("KIS startup jitter 비활성 (즉시 토큰 발급 시도)")

    client = client_factory()
    await client._token_manager.get_access_token()
    return client
