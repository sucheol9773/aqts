"""
AQTS 로깅 설정 모듈

loguru 기반 구조화된 로깅.
운영 환경에서는 JSON 포맷으로 출력하여 로그 분석 도구와 호환한다.
모든 모듈에서 'from config.logging import logger'로 사용합니다.
"""

import json
import sys

from loguru import logger

from config.settings import get_settings


def _json_sink(message) -> None:
    """운영 환경용 JSON 로그 포매터

    구조화된 JSON 로그를 stdout으로 출력한다.
    Docker 환경에서 로그 수집기(Loki, Fluentd 등)가 파싱하기 용이하다.
    """
    record = message.record
    log_entry = {
        "timestamp": record["time"].strftime("%Y-%m-%dT%H:%M:%S.%f%z"),
        "level": record["level"].name,
        "logger": record["name"],
        "function": record["function"],
        "line": record["line"],
        "message": record["message"],
    }

    # extra 필드 추가 (request_id, correlation_id 등)
    if record["extra"]:
        log_entry["extra"] = {k: str(v) for k, v in record["extra"].items() if k != "_"}

    # 예외 정보 추가
    if record["exception"] is not None:
        log_entry["exception"] = {
            "type": str(record["exception"].type.__name__),
            "value": str(record["exception"].value),
        }

    sys.stdout.write(json.dumps(log_entry, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def setup_logging() -> None:
    """로깅 초기 설정"""
    settings = get_settings()

    # 기본 핸들러 제거 후 재설정
    logger.remove()

    if settings.is_production:
        # 운영 환경: JSON 구조화 로그 (stdout)
        logger.add(
            _json_sink,
            level=settings.log_level,
        )

        # 파일 출력 (운영 환경 — JSON 포맷)
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
    else:
        # 개발 환경: 컬러 콘솔 출력
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

    logger.info(f"Logging initialized. Level: {settings.log_level}, Env: {settings.environment}")
