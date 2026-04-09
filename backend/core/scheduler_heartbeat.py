"""스케줄러 liveness heartbeat

`scheduler_main.py` 로 기동되는 scheduler 컨테이너는 FastAPI HTTP 프로세스가
아니므로 backend 의 `curl localhost:8000/health` 기반 Dockerfile HEALTHCHECK 이
구조적으로 통과할 수 없다. 이로 인해 scheduler 컨테이너가 무기한 unhealthy
상태로 누적되어 운영 가시성이 손상되고, `depends_on: condition: service_healthy`
로 scheduler 에 의존하는 후속 서비스가 있으면 블로킹된다.

본 모듈은 scheduler 루프 전용의 **파일 mtime 기반 heartbeat** 을 제공한다.
외부 의존성(redis, http)이 전혀 없어 healthcheck 자체가 또 다른 장애원이
되지 않는다.

규약:
  - `_scheduler_loop` 이 1회 iterate 할 때마다 `write_heartbeat()` 로
    `HEARTBEAT_PATH` 파일의 mtime 을 갱신한다.
  - healthcheck 는 파일 mtime 이 `HEARTBEAT_STALE_SECONDS` 이내인지를 본다.
  - `_scheduler_loop` 의 최대 sleep 은 60 초이므로 정상 동작 중이라면 mtime 은
    최소 60 초 이내로 갱신된다. stale 임계치 180 초는 2 사이클의 여유를 허용.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

# 기본 경로. 컨테이너 내부 /tmp 는 tmpfs 로 쓰기 가능하다.
HEARTBEAT_PATH = Path(os.environ.get("SCHEDULER_HEARTBEAT_PATH", "/tmp/scheduler.heartbeat"))
HEARTBEAT_STALE_SECONDS = int(os.environ.get("SCHEDULER_HEARTBEAT_STALE_SECONDS", "180"))


def write_heartbeat(path: Path | None = None) -> None:
    """heartbeat 파일의 mtime 을 현재 시각으로 갱신한다.

    파일이 없으면 생성한다. IO 오류는 호출자까지 전파되지 않고 조용히 무시한다
    (스케줄러 루프를 heartbeat 실패로 중단시키지 않기 위함).
    """
    target = path or HEARTBEAT_PATH
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        # touch 로 생성 + mtime 갱신
        target.touch(exist_ok=True)
        now = time.time()
        os.utime(target, (now, now))
    except OSError:
        # heartbeat 실패가 스케줄러 본 루프를 멈추게 하면 안 된다.
        # 실패 시 healthcheck 가 자연스럽게 unhealthy 로 반응한다.
        pass


def check_heartbeat_fresh(
    path: Path | None = None,
    max_age_seconds: int | None = None,
) -> bool:
    """heartbeat 파일 mtime 이 `max_age_seconds` 이내인지 반환한다.

    파일이 존재하지 않으면 False. 이 함수는 테스트에서 healthcheck 시뮬레이션에
    사용된다. 실제 컨테이너 healthcheck 는 docker-compose.yml 의 `healthcheck:`
    블록이 같은 로직을 수행한다.
    """
    target = path or HEARTBEAT_PATH
    max_age = max_age_seconds if max_age_seconds is not None else HEARTBEAT_STALE_SECONDS
    try:
        mtime = target.stat().st_mtime
    except FileNotFoundError:
        return False
    except OSError:
        return False
    return (time.time() - mtime) < max_age
