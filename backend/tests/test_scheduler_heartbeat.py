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


class TestHeartbeatSleepHelper:
    """_heartbeat_sleep 이 긴 대기 구간에서 heartbeat 를 chunk 단위로 갱신하는지 검증.

    2026-04-09 회귀:
        _scheduler_loop 이 "오늘 모든 이벤트 완료" 또는 "비거래일" 브랜치에
        진입하면 최대 3600 초 ``await asyncio.sleep(min(wait_seconds, 3600))``
        로 블로킹되는데, 이 구간 동안 heartbeat 가 갱신되지 않아 compose
        healthcheck 와 post-deploy smoke(C4) 가 동시에 실패했다.

    Fix:
        긴 대기를 chunk(기본 30 초) 단위로 분할하여 각 chunk 사이에
        write_heartbeat() 를 호출하는 _heartbeat_sleep helper 를 도입.
        본 테스트는 helper 가 실제로 chunk 단위 갱신을 수행하고,
        self._running 이 False 가 되면 즉시 조기 반환하는지 검증한다.
    """

    def _make_scheduler(self):
        """TradingScheduler 를 __init__ 우회로 생성 (이 테스트는 _heartbeat_sleep
        로직만 타게팅하므로 전체 초기화 비용은 불필요하다)."""
        from core.trading_scheduler import TradingScheduler

        scheduler = TradingScheduler.__new__(TradingScheduler)
        scheduler._running = True
        return scheduler

    def test_heartbeat_sleep_updates_file_multiple_times(self, tmp_path, monkeypatch):
        """1.5 초 대기를 0.3 초 chunk 로 돌리면 최소 3회 이상 mtime 갱신."""
        import asyncio

        target = tmp_path / "heartbeat"
        monkeypatch.setattr("core.scheduler_heartbeat.HEARTBEAT_PATH", target)

        scheduler = self._make_scheduler()

        call_mtimes: list[float] = []
        original_write = None

        def tracking_write(path=None):
            # 실제 파일을 쓰고 mtime 을 기록한다.
            nonlocal original_write
            original_write(path)
            if target.exists():
                call_mtimes.append(target.stat().st_mtime)

        import core.scheduler_heartbeat as hb_mod

        original_write = hb_mod.write_heartbeat
        monkeypatch.setattr(hb_mod, "write_heartbeat", tracking_write)

        asyncio.get_event_loop_policy().new_event_loop().run_until_complete(scheduler._heartbeat_sleep(1.5, chunk=0.3))

        # 1.5 / 0.3 = 5 chunk → 최소 5회 heartbeat 갱신.
        assert len(call_mtimes) >= 5, f"chunk 단위 heartbeat 갱신 횟수가 부족: {len(call_mtimes)}"
        # 마지막 mtime 은 현재 시각과 근사해야 한다 (stale 아님).
        assert (time.time() - call_mtimes[-1]) < 2.0, "마지막 heartbeat 가 stale"

    def test_heartbeat_sleep_respects_running_flag(self, tmp_path, monkeypatch):
        """self._running=False 로 바뀌면 즉시 조기 반환."""
        import asyncio

        target = tmp_path / "heartbeat"
        monkeypatch.setattr("core.scheduler_heartbeat.HEARTBEAT_PATH", target)

        scheduler = self._make_scheduler()

        async def stop_after_two_chunks():
            # 두 chunk (0.2초) 후 running 을 False 로 내린다.
            await asyncio.sleep(0.2)
            scheduler._running = False

        async def runner():
            await asyncio.gather(
                scheduler._heartbeat_sleep(10.0, chunk=0.1),
                stop_after_two_chunks(),
            )

        start = time.time()
        asyncio.get_event_loop_policy().new_event_loop().run_until_complete(runner())
        elapsed = time.time() - start

        # 10 초 요청이었지만 0.3 초 내외에 종료되어야 한다.
        assert elapsed < 1.0, f"_heartbeat_sleep 이 _running=False 에 조기 반환하지 않음: elapsed={elapsed:.2f}s"

    def test_heartbeat_sleep_zero_or_negative_returns_immediately(self, tmp_path, monkeypatch):
        """0 이하 대기는 즉시 반환하고 heartbeat 도 갱신하지 않는다."""
        import asyncio

        target = tmp_path / "heartbeat"
        monkeypatch.setattr("core.scheduler_heartbeat.HEARTBEAT_PATH", target)

        scheduler = self._make_scheduler()

        asyncio.get_event_loop_policy().new_event_loop().run_until_complete(scheduler._heartbeat_sleep(0))
        asyncio.get_event_loop_policy().new_event_loop().run_until_complete(scheduler._heartbeat_sleep(-5))

        # 둘 다 파일이 생성되지 않아야 한다 (write_heartbeat 미호출).
        assert not target.exists(), "0 이하 대기에서 heartbeat 가 갱신됨"

    def test_long_sleep_branches_use_heartbeat_sleep(self):
        """_scheduler_loop 의 긴 sleep 경로가 _heartbeat_sleep 으로 교체됐는지 정적 검증.

        L367(비거래일), L416(오늘 완료) 두 분기가 raw asyncio.sleep 대신
        self._heartbeat_sleep 를 호출해야 한다. 짧은 대기(L399, max 60s)는
        루프 상단 heartbeat 로 충분하므로 제외.
        """
        import inspect

        from core.trading_scheduler import TradingScheduler

        src = inspect.getsource(TradingScheduler._scheduler_loop)

        # _heartbeat_sleep 호출이 최소 2회 (비거래일 + 오늘 완료)
        assert (
            src.count("self._heartbeat_sleep(") >= 2
        ), "_scheduler_loop 의 긴 sleep 경로 2곳이 _heartbeat_sleep 으로 교체되지 않음"

        # raw asyncio.sleep(min(wait_seconds, 3600)) 패턴은 더 이상 존재하면 안 됨.
        assert (
            "asyncio.sleep(min(wait_seconds, 3600))" not in src
        ), "raw asyncio.sleep(min(wait_seconds, 3600)) 이 남아있음 — 회귀"


class TestHeartbeatBackgroundLoop:
    """_heartbeat_loop 백그라운드 태스크 회귀 검증.

    2026-04-09 회귀:
        _scheduler_loop 안에 heartbeat 갱신을 묶어두면 `_execute_event`,
        DB 블로킹, 비동기 대기 구간 등 임의의 블로킹 경로에서 mtime 이
        starve 된다. 독립 백그라운드 태스크로 분리하여 `_scheduler_loop`
        이 무엇을 하든 고정 주기로 heartbeat 가 갱신되는지 검증한다.

    Fix:
        TradingScheduler.start() 가 동기 write_heartbeat() 1회 + 백그라운드
        _heartbeat_loop 태스크 생성을 수행하고, stop() 이 태스크를 취소한다.
    """

    def _make_scheduler(self):
        from core.trading_scheduler import TradingScheduler

        scheduler = TradingScheduler.__new__(TradingScheduler)
        scheduler._running = True
        return scheduler

    def test_heartbeat_loop_writes_periodically(self, tmp_path, monkeypatch):
        """_heartbeat_loop 이 interval 주기로 write_heartbeat 을 호출한다."""
        import asyncio

        target = tmp_path / "heartbeat"
        monkeypatch.setattr("core.scheduler_heartbeat.HEARTBEAT_PATH", target)

        scheduler = self._make_scheduler()
        # 테스트 속도를 위해 클래스 속성을 override (인스턴스에 shadow).
        scheduler._HEARTBEAT_INTERVAL_SEC = 0.1

        call_count = {"n": 0}
        import core.scheduler_heartbeat as hb_mod

        original_write = hb_mod.write_heartbeat

        def tracking_write(path=None):
            original_write(path)
            call_count["n"] += 1

        monkeypatch.setattr(hb_mod, "write_heartbeat", tracking_write)

        async def runner():
            task = asyncio.create_task(scheduler._heartbeat_loop())
            await asyncio.sleep(0.55)
            scheduler._running = False
            try:
                await asyncio.wait_for(task, timeout=1.0)
            except asyncio.CancelledError:
                pass

        asyncio.get_event_loop_policy().new_event_loop().run_until_complete(runner())

        # 0.55s / 0.1s ≈ 5회 이상 갱신.
        assert call_count["n"] >= 5, f"heartbeat 갱신 횟수 부족: {call_count['n']}"
        assert target.exists()

    def test_heartbeat_loop_cancels_quickly(self, tmp_path, monkeypatch):
        """stop() 이 태스크를 cancel 하면 즉시 종료된다."""
        import asyncio

        target = tmp_path / "heartbeat"
        monkeypatch.setattr("core.scheduler_heartbeat.HEARTBEAT_PATH", target)

        scheduler = self._make_scheduler()
        scheduler._HEARTBEAT_INTERVAL_SEC = 10.0  # 긴 sleep 이지만 cancel 되어야 함

        async def runner():
            task = asyncio.create_task(scheduler._heartbeat_loop())
            await asyncio.sleep(0.1)
            task.cancel()
            start = time.time()
            try:
                await task
            except asyncio.CancelledError:
                pass
            return time.time() - start

        elapsed = asyncio.get_event_loop_policy().new_event_loop().run_until_complete(runner())
        assert elapsed < 0.5, f"_heartbeat_loop cancel 지연: {elapsed:.2f}s"

    def test_heartbeat_loop_swallows_write_errors(self, tmp_path, monkeypatch):
        """write 경로에서 예외가 발생해도 루프가 멈추지 않는다."""
        import asyncio

        target = tmp_path / "heartbeat"
        monkeypatch.setattr("core.scheduler_heartbeat.HEARTBEAT_PATH", target)

        scheduler = self._make_scheduler()
        scheduler._HEARTBEAT_INTERVAL_SEC = 0.05

        import core.scheduler_heartbeat as hb_mod

        call_count = {"n": 0}

        def raising_write(path=None):
            call_count["n"] += 1
            raise RuntimeError("simulated write error")

        monkeypatch.setattr(hb_mod, "write_heartbeat", raising_write)

        async def runner():
            task = asyncio.create_task(scheduler._heartbeat_loop())
            await asyncio.sleep(0.25)
            scheduler._running = False
            try:
                await asyncio.wait_for(task, timeout=1.0)
            except asyncio.CancelledError:
                pass

        asyncio.get_event_loop_policy().new_event_loop().run_until_complete(runner())

        # 예외가 발생했어도 루프가 계속 돌아 여러 번 호출됐어야 한다.
        assert call_count["n"] >= 3, f"예외 경로에서 루프가 멈춤: {call_count['n']}"

    def test_start_creates_heartbeat_task_and_writes_sync(self):
        """start() 가 동기 write_heartbeat() 후 _heartbeat_task 를 생성한다 (소스 검증)."""
        import ast
        import inspect
        import textwrap

        from core.trading_scheduler import TradingScheduler

        src = textwrap.dedent(inspect.getsource(TradingScheduler.start))
        tree = ast.parse(src)

        # write_heartbeat() 호출과 create_task(self._heartbeat_loop()) 호출을 모두 찾는다.
        has_sync_write = False
        has_task_create = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "write_heartbeat":
                has_sync_write = True
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "create_task":
                # 인자가 self._heartbeat_loop() 인지 확인
                if node.args and isinstance(node.args[0], ast.Call):
                    inner = node.args[0].func
                    if isinstance(inner, ast.Attribute) and inner.attr == "_heartbeat_loop":
                        has_task_create = True

        assert has_sync_write, "start() 에 동기 write_heartbeat() 호출이 없다"
        assert has_task_create, "start() 가 _heartbeat_loop 태스크를 생성하지 않는다"

    def test_stop_cancels_heartbeat_task(self):
        """stop() 이 _heartbeat_task 를 cancel 한다 (소스 검증)."""
        import inspect

        from core.trading_scheduler import TradingScheduler

        src = inspect.getsource(TradingScheduler.stop)
        assert "_heartbeat_task" in src, "stop() 에 _heartbeat_task 처리 없음"
        assert "_heartbeat_task.cancel()" in src, "stop() 이 _heartbeat_task.cancel() 을 호출하지 않음"
