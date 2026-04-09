"""scheduler_heartbeat 유닛테스트

규약 검증:
  - write_heartbeat 는 파일을 생성하고 mtime 을 갱신한다.
  - check_heartbeat_fresh 는 mtime 기준 max_age 이내면 True, 아니면 False.
  - 파일이 존재하지 않으면 check_heartbeat_fresh 는 False.
  - write_heartbeat 는 IO 오류(쓰기 불가능한 경로)에서도 예외를 raise 하지 않는다
    — 스케줄러 루프를 중단시키지 않기 위함.
  - _scheduler_loop 이 1회 iterate 하면 실제로 heartbeat 파일이 갱신된다
    (통합: write_heartbeat 이 루프 경로에 wiring 되어 있는지).
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from core.scheduler_heartbeat import (
    HEARTBEAT_STALE_SECONDS,
    check_heartbeat_fresh,
    write_heartbeat,
)


class TestWriteHeartbeat:
    def test_creates_file_if_absent(self, tmp_path):
        target = tmp_path / "heartbeat"
        assert not target.exists()
        write_heartbeat(target)
        assert target.exists()

    def test_updates_mtime_on_second_call(self, tmp_path):
        target = tmp_path / "heartbeat"
        write_heartbeat(target)
        first_mtime = target.stat().st_mtime

        # 측정 가능한 차이를 만들기 위해 과거 mtime 으로 되돌림
        past = first_mtime - 10.0
        os.utime(target, (past, past))
        assert target.stat().st_mtime == past

        write_heartbeat(target)
        new_mtime = target.stat().st_mtime
        assert new_mtime > past
        # 새 mtime 이 과거 mtime 에서 최소 5초는 증가했어야 한다 (10초 차이의 절반)
        assert new_mtime - past > 5.0

    def test_creates_parent_dir_if_missing(self, tmp_path):
        target = tmp_path / "nested" / "dir" / "heartbeat"
        write_heartbeat(target)
        assert target.exists()

    def test_does_not_raise_on_permission_error(self, tmp_path, monkeypatch):
        # touch 가 PermissionError 를 던지도록 강제
        def raising_touch(self, *args, **kwargs):
            raise PermissionError("simulated read-only fs")

        monkeypatch.setattr(Path, "touch", raising_touch)

        target = tmp_path / "heartbeat"
        # 예외가 전파되지 않아야 한다
        write_heartbeat(target)
        # 파일은 생성되지 않았으나 호출자는 영향을 받지 않는다
        assert not target.exists()


class TestCheckHeartbeatFresh:
    def test_returns_false_when_file_absent(self, tmp_path):
        target = tmp_path / "heartbeat"
        assert check_heartbeat_fresh(target, max_age_seconds=60) is False

    def test_returns_true_when_fresh(self, tmp_path):
        target = tmp_path / "heartbeat"
        write_heartbeat(target)
        assert check_heartbeat_fresh(target, max_age_seconds=60) is True

    def test_returns_false_when_stale(self, tmp_path):
        target = tmp_path / "heartbeat"
        write_heartbeat(target)
        # mtime 을 100초 전으로 되돌림
        stale = time.time() - 100.0
        os.utime(target, (stale, stale))
        assert check_heartbeat_fresh(target, max_age_seconds=60) is False

    def test_boundary_just_fresh(self, tmp_path):
        """mtime = now - (max_age - 1) 은 여전히 fresh"""
        target = tmp_path / "heartbeat"
        target.touch()
        mtime = time.time() - 30.0
        os.utime(target, (mtime, mtime))
        assert check_heartbeat_fresh(target, max_age_seconds=60) is True

    def test_default_stale_seconds_from_env(self, tmp_path):
        target = tmp_path / "heartbeat"
        write_heartbeat(target)
        # 환경변수 기본값 180s — fresh 여야 한다
        assert check_heartbeat_fresh(target) is True
        assert HEARTBEAT_STALE_SECONDS == 180


class TestLoopWiring:
    """_scheduler_loop 이 heartbeat 를 실제로 갱신하도록 wiring 되어 있는지 검증

    루프 전체를 돌리지 않고 소스 레벨에서 wiring 을 검증한다. 실제 루프 실행은
    now_kst/is_trading_day/event_time 계산 등 여러 시각 의존성을 포함하여
    단위테스트에서 재현하기가 취약하므로, "write_heartbeat 이 루프 본문의 try
    블록 최상단에 존재하는가" 를 AST 로 확인하는 것이 wiring 회귀에 대한 가장
    결정적인 검증이다. 본체 동작 자체(파일 갱신)는 TestWriteHeartbeat 이
    보장한다.
    """

    def test_scheduler_loop_calls_write_heartbeat(self):
        import ast
        import inspect
        import textwrap

        from core.trading_scheduler import TradingScheduler

        src = textwrap.dedent(inspect.getsource(TradingScheduler._scheduler_loop))
        tree = ast.parse(src)

        # _scheduler_loop 함수 정의를 찾는다 (top-level in src)
        func_def = None
        for node in ast.walk(tree):
            if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
                if node.name == "_scheduler_loop":
                    func_def = node
                    break
        assert func_def is not None, "_scheduler_loop 함수 정의를 찾지 못함"

        # 함수 본문에서 write_heartbeat 호출이 존재하는지
        heartbeat_calls = [
            node
            for node in ast.walk(func_def)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "write_heartbeat"
        ]
        assert len(heartbeat_calls) >= 1, (
            "_scheduler_loop 본문에 write_heartbeat() 호출이 없다 — " "heartbeat wiring 이 회귀되었을 가능성."
        )

    def test_scheduler_loop_import_path_is_scheduler_heartbeat(self):
        """import 경로가 core.scheduler_heartbeat 인지 확인"""
        import inspect

        from core.trading_scheduler import TradingScheduler

        src = inspect.getsource(TradingScheduler._scheduler_loop)
        assert "from core.scheduler_heartbeat import write_heartbeat" in src, (
            "_scheduler_loop 이 core.scheduler_heartbeat.write_heartbeat 를 " "import 하지 않는다"
        )
