"""알림 파이프라인 재시도 정책 (Commit 3).

Decision 1 (설계 결정): 재시도 간격은 **고정 dict**로 정의한다.

    RETRY_BACKOFF_SECONDS = {1: 60, 2: 300, 3: 900}

근거 — 금융 운영 감사(auditability) 우선:
  - 수학식 `base * 2**(n-1) + jitter` 는 런북/감사 로그 대조가 번거롭고,
    jitter 가 포함되면 "언제 재시도가 일어났어야 하는가"를 재구성할 때
    시드 재현이 필요해 규제 감사 대응이 어려워진다.
  - 알림 파이프라인의 QPS 는 분당 수 건 수준이므로 jitter 로 얻을
    충돌 회피 이득이 감사 복잡도 비용보다 작다.
  - 운영자가 단일 상수만 보면 모든 재시도 일정을 이해할 수 있어야 한다.

단계 의미:
  키 `n` 은 "현재 시도 횟수(send_attempts)" 이다. `claim_for_sending` 이
  `$inc: 1` 을 먼저 적용하므로:
    - 첫 발송 실패 → send_attempts == 1 → 다음 재시도까지 60 초 대기
    - 두 번째 실패 → send_attempts == 2 → 300 초 대기
    - 세 번째 실패 → send_attempts == 3 → DEAD 전이 (백오프 미사용)

  즉 `RETRY_BACKOFF_SECONDS[n]` 은 "`n` 번째 시도 실패 직후의 대기 시간" 을
  뜻한다. DEAD 전이 조건과 일치하도록 3 까지만 정의한다.

MAX_SEND_ATTEMPTS 와의 관계:
  `mark_failed_with_retry` 의 `max_attempts` 기본값(3) 과 동일해야 한다.
  두 값이 불일치하면 백오프 dict 에 없는 attempts 가 조회되어 KeyError 가
  발생할 수 있으므로, 단일 상수로 묶어 export 한다.
"""

from typing import Mapping

# ══════════════════════════════════════
# 재시도 최대 횟수 (DEAD 전이 경계)
# ══════════════════════════════════════
# `mark_failed_with_retry(max_attempts=MAX_SEND_ATTEMPTS)` 와 일치시킨다.
# 변경 시 해당 호출 지점과 RETRY_BACKOFF_SECONDS 의 키 범위를 함께 갱신.
MAX_SEND_ATTEMPTS: int = 3

# ══════════════════════════════════════
# 고정 백오프 스케줄 (Decision 1)
# ══════════════════════════════════════
# 키: 현재 send_attempts 값 (claim 에서 $inc 적용 후의 값)
# 값: 다음 재시도까지 대기할 초 수
#
# 운영 감사 시 이 dict 와 Alert 문서의 `last_send_attempt_at` / `send_attempts`
# 만으로 정확한 재시도 타임라인을 재구성할 수 있어야 한다.
RETRY_BACKOFF_SECONDS: Mapping[int, int] = {
    1: 60,
    2: 300,
    3: 900,
}


def backoff_seconds_for(attempts: int) -> int:
    """주어진 send_attempts 값에 해당하는 백오프 초를 반환한다.

    범위 밖(0 이하 또는 정의되지 않은 상위 값)은 마지막 정의된 값으로
    clamp 한다. 호출자가 MAX_SEND_ATTEMPTS 경계를 이미 검사했다는
    계약을 전제로 하지만, 방어적으로 graceful degrade 한다.

    Args:
        attempts: 현재 send_attempts 값

    Returns:
        재시도까지 대기할 초 수 (항상 >= 0)
    """
    if attempts <= 0:
        return RETRY_BACKOFF_SECONDS[1]
    if attempts in RETRY_BACKOFF_SECONDS:
        return RETRY_BACKOFF_SECONDS[attempts]
    # 정의 범위 초과 — 마지막 값으로 clamp.
    last_key = max(RETRY_BACKOFF_SECONDS.keys())
    return RETRY_BACKOFF_SECONDS[last_key]
