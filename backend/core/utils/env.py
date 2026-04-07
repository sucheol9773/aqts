"""환경변수 → bool 단일 진입점.

표준 표기는 소문자 ``"true"`` / ``"false"`` 이며, 신규 코드/문서/CI/Compose는
반드시 표준 표기만 사용한다. Phase 1(현재)에서는 하위호환을 위해
``1/0``, ``yes/no``, ``on/off`` (대소문자 무시) 도 허용하되 비표준 사용 시
경고 1회 + Prometheus counter 증가로 추적한다. Phase 2 (다음 마이너) 에서는
``AQTS_STRICT_BOOL=true`` 또는 ``strict=True`` 호출 시 비표준 값을
``ValueError`` 로 승격시킨다.

자세한 정책: ``docs/conventions/boolean-config.md``.
"""

from __future__ import annotations

import logging
import os
import threading

logger = logging.getLogger(__name__)

_TRUE_STANDARD = "true"
_FALSE_STANDARD = "false"
_TRUE_VALUES = {_TRUE_STANDARD, "1", "yes", "on"}
_FALSE_VALUES = {_FALSE_STANDARD, "0", "no", "off"}
_STANDARD_VALUES = {_TRUE_STANDARD, _FALSE_STANDARD}

_warned_lock = threading.Lock()
_warned: set[tuple[str, str]] = set()


def _record_nonstandard(key: str, raw: str) -> None:
    """비표준 표기 1회 경고 + Prometheus counter 증가."""

    fingerprint = (key, raw)
    with _warned_lock:
        if fingerprint in _warned:
            return
        _warned.add(fingerprint)

    logger.warning(
        "non-standard bool literal %r for env %r; use 'true'/'false'",
        raw,
        key,
    )

    # Prometheus counter (지연 import로 순환 의존 방지)
    try:
        from core.monitoring.metrics import ENV_BOOL_NONSTANDARD_TOTAL

        ENV_BOOL_NONSTANDARD_TOTAL.labels(key=key, value=raw).inc()
    except Exception:  # pragma: no cover - metrics 미초기화 환경
        pass


def _strict_enabled(strict: bool | None) -> bool:
    if strict is not None:
        return strict
    raw = os.environ.get("AQTS_STRICT_BOOL", _FALSE_STANDARD).strip().lower()
    return raw == _TRUE_STANDARD


def env_bool(
    key: str,
    default: bool | None = None,
    *,
    strict: bool | None = None,
) -> bool:
    """환경변수를 bool로 파싱한다.

    Parameters
    ----------
    key:
        환경변수 이름.
    default:
        미설정/빈 문자열일 때 반환할 값. ``None`` 이면 미설정 시 ``KeyError``.
    strict:
        ``True`` 면 비표준(하위호환) 값도 ``ValueError`` 로 승격.
        ``None`` (기본) 이면 ``AQTS_STRICT_BOOL`` 환경변수를 따른다.

    Returns
    -------
    bool

    Raises
    ------
    KeyError
        ``default`` 가 ``None`` 이고 환경변수가 설정되지 않았거나 빈 문자열일 때.
    ValueError
        값이 알 수 없는 표기이거나, strict 모드에서 비표준 표기일 때.
    """

    raw_original = os.environ.get(key)
    if raw_original is None or raw_original.strip() == "":
        if default is None:
            raise KeyError(f"environment variable {key!r} is not set")
        return default

    raw = raw_original.strip().lower()

    if raw in _STANDARD_VALUES:
        return raw == _TRUE_STANDARD

    if raw in _TRUE_VALUES or raw in _FALSE_VALUES:
        if _strict_enabled(strict):
            raise ValueError(
                f"non-standard bool literal {raw_original!r} for env {key!r}; "
                "use 'true'/'false' (AQTS_STRICT_BOOL is enabled)"
            )
        _record_nonstandard(key, raw_original)
        return raw in _TRUE_VALUES

    raise ValueError(f"invalid bool literal {raw_original!r} for env {key!r}; " "expected one of 'true'/'false'")


def _reset_warned_for_tests() -> None:
    """테스트 격리용 — 운영 코드에서는 호출하지 않는다."""

    with _warned_lock:
        _warned.clear()
