"""
AQTS 로깅 설정 모듈

loguru 기반 구조화된 로깅.
모든 모듈에서 'from config.logging import logger'로 사용합니다.
"""

import sys

from loguru import logger

from config.settings import get_settings


def setup_logging() -> None:
    """로깅 초기 설정"""
    settings = get_settings()

    # 기본 핸들러 제거 후 재설정
    logger.remove()

    # 콘솔 출력
    logger.add(
        sys.stdout,
        level=settings.log_level,
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
            "<level>{message}</level>"
        ),
        colorize=True,
    )

    # 파일 출력 (운영 환경)
    if settings.is_production:
        logger.add(
            "logs/aqts_{time:YYYY-MM-DD}.log",
            level="INFO",
            rotation="00:00",
            retention="30 days",
            compression="gz",
            format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {name}:{function}:{line} | {message}",
        )

        # 에러 전용 로그
        logger.add(
            "logs/aqts_error_{time:YYYY-MM-DD}.log",
            level="ERROR",
            rotation="00:00",
            retention="90 days",
            compression="gz",
            format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {name}:{function}:{line} | {message}",
        )

    logger.info(f"Logging initialized. Level: {settings.log_level}, Env: {settings.environment}")
