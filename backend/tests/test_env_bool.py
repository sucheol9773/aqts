"""env_bool() 단일 진입점 테스트.

CLAUDE.md 규칙: 기대값을 코드에 맞춰 수정하는 것이 아니라, 정책 문서
(``docs/conventions/boolean-config.md``)에 명시한 동작을 그대로 검증한다.
"""

from __future__ import annotations

import logging
import os

import pytest

from core.utils.env import _reset_warned_for_tests, env_bool

KEY = "AQTS_TEST_BOOL_VAR"
STRICT_KEY = "AQTS_STRICT_BOOL"


@pytest.fixture(autouse=True)
def _isolate_env():
    saved = {k: os.environ.get(k) for k in (KEY, STRICT_KEY)}
    for k in (KEY, STRICT_KEY):
        os.environ.pop(k, None)
    _reset_warned_for_tests()
    try:
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        _reset_warned_for_tests()


# ── 표준 표기 ─────────────────────────────────────────────


def test_standard_true():
    os.environ[KEY] = "true"
    assert env_bool(KEY, default=False) is True


def test_standard_false():
    os.environ[KEY] = "false"
    assert env_bool(KEY, default=True) is False


def test_standard_does_not_emit_warning(caplog):
    os.environ[KEY] = "true"
    with caplog.at_level(logging.WARNING, logger="core.utils.env"):
        env_bool(KEY, default=False)
    assert not any("non-standard" in r.message for r in caplog.records)


# ── 하위호환 표기 (Phase 1) ───────────────────────────────


@pytest.mark.parametrize("raw", ["1", "yes", "on", "YES", "On", "TRUE"])
def test_legacy_truthy_accepted(raw):
    os.environ[KEY] = raw
    assert env_bool(KEY, default=False) is True


@pytest.mark.parametrize("raw", ["0", "no", "off", "NO", "Off", "FALSE"])
def test_legacy_falsy_accepted(raw):
    os.environ[KEY] = raw
    assert env_bool(KEY, default=True) is False


def test_legacy_value_emits_warning_once(caplog):
    os.environ[KEY] = "1"
    with caplog.at_level(logging.WARNING, logger="core.utils.env"):
        env_bool(KEY, default=False)
        env_bool(KEY, default=False)
        env_bool(KEY, default=False)
    warnings = [r for r in caplog.records if "non-standard" in r.message]
    assert len(warnings) == 1


def test_legacy_warning_distinct_per_key_value(caplog):
    with caplog.at_level(logging.WARNING, logger="core.utils.env"):
        os.environ[KEY] = "1"
        env_bool(KEY, default=False)
        os.environ[KEY] = "yes"
        env_bool(KEY, default=False)
    warnings = [r for r in caplog.records if "non-standard" in r.message]
    assert len(warnings) == 2


# ── default / 미설정 ──────────────────────────────────────


def test_unset_returns_default_true():
    assert env_bool(KEY, default=True) is True


def test_unset_returns_default_false():
    assert env_bool(KEY, default=False) is False


def test_empty_string_returns_default():
    os.environ[KEY] = ""
    assert env_bool(KEY, default=True) is True


def test_whitespace_only_returns_default():
    os.environ[KEY] = "   "
    assert env_bool(KEY, default=False) is False


def test_unset_without_default_raises_keyerror():
    with pytest.raises(KeyError):
        env_bool(KEY)


# ── 알 수 없는 값 ─────────────────────────────────────────


@pytest.mark.parametrize("raw", ["maybe", "2", "tru", "yesno", "enabled"])
def test_invalid_value_raises(raw):
    os.environ[KEY] = raw
    with pytest.raises(ValueError):
        env_bool(KEY, default=False)


# ── Strict 모드 ───────────────────────────────────────────


def test_strict_param_rejects_legacy():
    os.environ[KEY] = "1"
    with pytest.raises(ValueError):
        env_bool(KEY, default=False, strict=True)


def test_strict_param_accepts_standard():
    os.environ[KEY] = "true"
    assert env_bool(KEY, default=False, strict=True) is True


def test_strict_via_env_global():
    os.environ[KEY] = "yes"
    os.environ[STRICT_KEY] = "true"
    with pytest.raises(ValueError):
        env_bool(KEY, default=False)


def test_strict_param_overrides_env_global_off():
    os.environ[KEY] = "1"
    os.environ[STRICT_KEY] = "false"
    with pytest.raises(ValueError):
        env_bool(KEY, default=False, strict=True)


def test_strict_env_global_off_allows_legacy(caplog):
    os.environ[KEY] = "yes"
    os.environ[STRICT_KEY] = "false"
    with caplog.at_level(logging.WARNING, logger="core.utils.env"):
        assert env_bool(KEY, default=False) is True


# ── Prometheus counter 연동 ──────────────────────────────


def test_legacy_increments_prometheus_counter():
    from core.monitoring.metrics import ENV_BOOL_NONSTANDARD_TOTAL

    os.environ[KEY] = "1"
    before = ENV_BOOL_NONSTANDARD_TOTAL.labels(key=KEY, value="1")._value.get()
    env_bool(KEY, default=False)
    after = ENV_BOOL_NONSTANDARD_TOTAL.labels(key=KEY, value="1")._value.get()
    assert after == before + 1


def test_standard_does_not_increment_counter():
    from core.monitoring.metrics import ENV_BOOL_NONSTANDARD_TOTAL

    os.environ[KEY] = "true"
    before = ENV_BOOL_NONSTANDARD_TOTAL.labels(key=KEY, value="true")._value.get()
    env_bool(KEY, default=False)
    after = ENV_BOOL_NONSTANDARD_TOTAL.labels(key=KEY, value="true")._value.get()
    assert after == before
