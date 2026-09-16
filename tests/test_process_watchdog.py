"""Tests for the stalled helper process watchdog"""

import asyncio
import os
import signal

import pytest

from gbs.build.platform import ProcessControl, ProcessInfo
from gbs.build.process_watchdog import ProcessWatchdog
from gbs.builtin.vivado import vivado_tcl
from gbs.ui.messages import MessageSeverity, ToolMessage


def stat_line(pid, comm, state="S", session=42, starttime=0, field_count=52):
    """Build a /proc/<pid>/stat line with the full field layout

    Fields, 1-based as in proc(5): 1 pid, 2 comm, 3 state, 4 ppid,
    5 pgrp, 6 session, 22 starttime.
    """
    rest = ["0"] * (field_count - 2)
    rest[0] = state
    rest[1] = "1"
    rest[2] = str(session)
    rest[3] = str(session)
    rest[19] = str(starttime)
    return f"{pid} ({comm}) " + " ".join(rest) + "\n"


def info_make(pid, name="srcscanner", session=1000, state="S", age=0.0, starttime=1):
    return ProcessInfo(
        pid=pid,
        name=name,
        session=session,
        state=state,
        age=age,
        starttime=starttime,
    )


class ScriptedLister:
    """Fake process lister replaying a script of per-tick results

    The last entry is repeated once the script is exhausted. An entry that
    is an exception instance is raised instead of returned.
    """

    def __init__(self, ticks):
        self.ticks = list(ticks)
        self.calls = 0
        self.sids = []

    def __call__(self, sid):
        self.sids.append(sid)
        entry = self.ticks[min(self.calls, len(self.ticks) - 1)]
        self.calls += 1
        if isinstance(entry, Exception):
            raise entry
        return list(entry)


class RecordingKiller:
    """Fake process killer recording its calls"""

    def __init__(self):
        self.kills = []

    def __call__(self, pid, force=False):
        self.kills.append((pid, force))


async def until(predicate, timeout=5.0):
    """Wait until predicate() is true, or fail the test"""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() > deadline:
            raise AssertionError(f"Condition not met within {timeout} s")
        await asyncio.sleep(0.01)


@pytest.mark.skipif(
    not hasattr(ProcessControl, "stat_parse"),
    reason="/proc stat parsing is Unix-only",
)
class TestStatParse:
    """Tests for ProcessControl.stat_parse()"""

    def test_simple_line(self):
        info = ProcessControl.stat_parse(
            stat_line(1234, "srcscanner", session=1000, starttime=25000),
            uptime=1000.0,
            clk_tck=100,
        )

        assert info is not None
        assert info.pid == 1234
        assert info.name == "srcscanner"
        assert info.session == 1000
        assert info.state == "S"
        assert info.starttime == 25000

    def test_comm_with_spaces_and_parentheses(self):
        comm = "we (cuddly) pine "
        info = ProcessControl.stat_parse(
            stat_line(7, comm, session=3, starttime=100),
            uptime=10.0,
            clk_tck=100,
        )

        assert info is not None
        assert info.name == comm
        assert info.pid == 7
        assert info.session == 3
        assert info.starttime == 100

    def test_comm_with_trailing_paren_only(self):
        info = ProcessControl.stat_parse(
            stat_line(9, "a)b", session=5, starttime=0),
            uptime=1.0,
            clk_tck=100,
        )

        assert info is not None
        assert info.name == "a)b"
        assert info.session == 5

    def test_zombie_state_is_parsed(self):
        # The parser reports the state as-is; filtering is the lister's job
        info = ProcessControl.stat_parse(
            stat_line(55, "sleep", state="Z", session=55, starttime=1000),
            uptime=100.0,
            clk_tck=100,
        )

        assert info is not None
        assert info.state == "Z"
        assert info.name == "sleep"

    def test_age_arithmetic(self):
        info = ProcessControl.stat_parse(
            stat_line(1, "init", starttime=25000),
            uptime=1000.0,
            clk_tck=100,
        )

        assert info.age == pytest.approx(750.0)

    def test_age_arithmetic_other_clk_tck(self):
        info = ProcessControl.stat_parse(
            stat_line(1, "init", starttime=2000),
            uptime=10.0,
            clk_tck=1000,
        )

        assert info.age == pytest.approx(8.0)

    def test_truncated_line_returns_none(self):
        line = stat_line(1234, "sh", starttime=10)
        # Cut in the middle of the numeric fields
        assert ProcessControl.stat_parse(line[:30], 1000.0, 100) is None

    def test_no_comm_returns_none(self):
        assert ProcessControl.stat_parse("1234 sh S 1 1 1\n", 1000.0, 100) is None

    def test_empty_line_returns_none(self):
        assert ProcessControl.stat_parse("", 1000.0, 100) is None

    def test_non_numeric_pid_returns_none(self):
        line = stat_line(1234, "sh", starttime=10).replace("1234 (", "abcd (", 1)
        assert ProcessControl.stat_parse(line, 1000.0, 100) is None

    def test_non_numeric_session_returns_none(self):
        line = stat_line(1234, "sh", session=42, starttime=10)
        head, _, tail = line.partition(") ")
        fields = tail.split()
        fields[3] = "bogus"
        assert ProcessControl.stat_parse(head + ") " + " ".join(fields), 1000.0, 100) is None


class TestProcessWatchdog:
    """Tests for ProcessWatchdog with an injected lister and killer"""

    @pytest.mark.asyncio
    async def test_young_process_not_killed(self):
        lister = ScriptedLister([[info_make(100, age=5.0)]])
        killer = RecordingKiller()
        killed = []
        wd = ProcessWatchdog(
            root_pid=10, process_name="srcscanner", grace_seconds=10,
            on_kill=killed.append,
            interval=0.01, lister=lister, killer=killer,
        )

        wd.start()
        try:
            await until(lambda: lister.calls >= 3)
        finally:
            await wd.stop()

        assert killer.kills == []
        assert killed == []
        assert set(lister.sids) == {10}

    @pytest.mark.asyncio
    async def test_old_process_killed_once(self):
        victim = info_make(100, age=25.0, starttime=777)
        lister = ScriptedLister([[victim]])
        killer = RecordingKiller()
        killed = []
        wd = ProcessWatchdog(
            root_pid=10, process_name="srcscanner", grace_seconds=10,
            on_kill=killed.append,
            interval=0.01, lister=lister, killer=killer,
        )

        wd.start()
        try:
            await until(lambda: killed)
            # Let a few more scans run over the same still-listed process
            calls = lister.calls
            await until(lambda: lister.calls >= calls + 3)
        finally:
            await wd.stop()

        assert killer.kills == [(100, True)]
        assert killed == [victim]
        assert wd.killed == {(100, 777)}

    @pytest.mark.asyncio
    async def test_other_name_untouched(self):
        lister = ScriptedLister([[info_make(100, name="ld", age=100.0)]])
        killer = RecordingKiller()
        killed = []
        wd = ProcessWatchdog(
            root_pid=10, process_name="srcscanner", grace_seconds=10,
            on_kill=killed.append,
            interval=0.01, lister=lister, killer=killer,
        )

        wd.start()
        try:
            await until(lambda: lister.calls >= 3)
        finally:
            await wd.stop()

        assert killer.kills == []
        assert killed == []

    @pytest.mark.asyncio
    async def test_relisted_process_not_killed_again(self):
        victim = info_make(100, age=25.0, starttime=777)
        lister = ScriptedLister([
            [victim],
            [],
            [victim._replace(age=40.0)],
        ])
        killer = RecordingKiller()
        killed = []
        wd = ProcessWatchdog(
            root_pid=10, process_name="srcscanner", grace_seconds=10,
            on_kill=killed.append,
            interval=0.01, lister=lister, killer=killer,
        )

        wd.start()
        try:
            await until(lambda: lister.calls >= 6)
        finally:
            await wd.stop()

        assert killer.kills == [(100, True)]
        assert len(killed) == 1

    @pytest.mark.asyncio
    async def test_root_never_killed(self):
        lister = ScriptedLister([[
            info_make(10, age=1000.0, starttime=1),
            info_make(0, age=1000.0, starttime=2),
            info_make(1, age=1000.0, starttime=3),
        ]])
        killer = RecordingKiller()
        killed = []
        wd = ProcessWatchdog(
            root_pid=10, process_name="srcscanner", grace_seconds=10,
            on_kill=killed.append,
            interval=0.01, lister=lister, killer=killer,
        )

        wd.start()
        try:
            await until(lambda: lister.calls >= 3)
        finally:
            await wd.stop()

        assert killer.kills == []
        assert killed == []

    @pytest.mark.asyncio
    async def test_root_death_ends_task(self):
        alive = [True]
        lister = ScriptedLister([[]])
        wd = ProcessWatchdog(
            root_pid=10, process_name="srcscanner", grace_seconds=10,
            on_kill=lambda info: None,
            root_alive=lambda: alive[0],
            interval=0.01, lister=lister, killer=RecordingKiller(),
        )

        wd.start()
        task = wd.task
        try:
            await until(lambda: lister.calls >= 2)
            alive[0] = False
            await asyncio.wait_for(task, timeout=5.0)
        finally:
            await wd.stop()

        assert task.done()
        assert not task.cancelled()

    @pytest.mark.asyncio
    async def test_lister_failure_does_not_end_task(self):
        victim = info_make(100, age=25.0, starttime=777)
        lister = ScriptedLister([
            RuntimeError("no /proc for you"),
            [victim],
        ])
        killer = RecordingKiller()
        killed = []
        wd = ProcessWatchdog(
            root_pid=10, process_name="srcscanner", grace_seconds=10,
            on_kill=killed.append,
            interval=0.01, lister=lister, killer=killer,
        )

        wd.start()
        task = wd.task
        try:
            await until(lambda: killed)
        finally:
            await wd.stop()

        assert lister.calls >= 2
        assert killer.kills == [(100, True)]
        assert task.cancelled()

    @pytest.mark.asyncio
    async def test_stop_is_idempotent(self):
        lister = ScriptedLister([[]])
        wd = ProcessWatchdog(
            root_pid=10, process_name="srcscanner", grace_seconds=10,
            on_kill=lambda info: None,
            interval=0.01, lister=lister, killer=RecordingKiller(),
        )

        # Before start()
        await asyncio.wait_for(wd.stop(), timeout=1.0)

        wd.start()
        task = wd.task
        await until(lambda: lister.calls >= 1)

        await asyncio.wait_for(wd.stop(), timeout=1.0)
        assert task.cancelled()
        assert wd.task is None

        await asyncio.wait_for(wd.stop(), timeout=1.0)

    @pytest.mark.asyncio
    async def test_start_is_idempotent(self):
        lister = ScriptedLister([[]])
        wd = ProcessWatchdog(
            root_pid=10, process_name="srcscanner", grace_seconds=10,
            on_kill=lambda info: None,
            interval=0.01, lister=lister, killer=RecordingKiller(),
        )

        wd.start()
        task = wd.task
        wd.start()
        try:
            assert wd.task is task
        finally:
            await wd.stop()

    def test_default_interval(self):
        wd = ProcessWatchdog(
            root_pid=1, process_name="x", grace_seconds=20.0,
            on_kill=lambda info: None,
        )
        assert wd.interval == 1.0

        wd = ProcessWatchdog(
            root_pid=1, process_name="x", grace_seconds=2.0,
            on_kill=lambda info: None,
        )
        assert wd.interval == 0.5


class TestSessionListing:
    """End-to-end tests against real processes"""

    @staticmethod
    async def spawn_session():
        """Spawn an sh in its own session, holding a sleep child"""
        return await asyncio.create_subprocess_exec(
            "sh", "-c", "sleep 100 & wait $!",
            start_new_session=True,
        )

    @staticmethod
    async def reap(proc):
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        await proc.wait()

    @staticmethod
    def names(sid):
        return [i.name for i in ProcessControl.list_session_processes(sid)]

    @pytest.mark.asyncio
    @pytest.mark.skipif(
        not ProcessControl.can_list_processes,
        reason="Process listing needs /proc",
    )
    async def test_session_listing_and_kill(self):
        a = await self.spawn_session()
        b = await self.spawn_session()

        try:
            # The fork of the sleep child may lag behind the sh spawn
            await until(lambda: {"sh", "sleep"} <= set(self.names(a.pid)))
            await until(lambda: {"sh", "sleep"} <= set(self.names(b.pid)))

            a_procs = ProcessControl.list_session_processes(a.pid)
            a_pids = {i.pid for i in a_procs}
            b_pids = {i.pid for i in ProcessControl.list_session_processes(b.pid)}

            assert a.pid in a_pids
            assert os.getpid() not in a_pids
            assert not (a_pids & b_pids)
            assert all(i.session == a.pid for i in a_procs)

            killed = []
            event = asyncio.Event()

            def on_kill(info):
                killed.append(info)
                event.set()

            wd = ProcessWatchdog(
                root_pid=a.pid, process_name="sleep", grace_seconds=0.5,
                on_kill=on_kill, interval=0.1,
            )
            wd.start()
            try:
                await asyncio.wait_for(event.wait(), timeout=5.0)
            finally:
                await wd.stop()

            assert len(killed) == 1
            assert killed[0].name == "sleep"
            assert killed[0].pid in a_pids
            assert killed[0].pid != a.pid

            # The killed sleep is reaped by its sh, and zombies are filtered
            # out anyway, so it leaves A's listing
            await until(lambda: "sleep" not in self.names(a.pid))
            assert "sleep" in self.names(b.pid)
        finally:
            await self.reap(a)
            await self.reap(b)

    @pytest.mark.asyncio
    @pytest.mark.skipif(
        not ProcessControl.can_list_processes,
        reason="Process listing needs /proc",
    )
    async def test_own_session_lists_self(self):
        procs = ProcessControl.list_session_processes(os.getsid(0))
        assert os.getpid() in {i.pid for i in procs}


class TestVivadoSessionHook:
    """Tests for the srcscanner hook of the Vivado session"""

    @pytest.mark.asyncio
    async def test_defaults(self):
        session = vivado_tcl.Session(argv=["vivado"])

        assert session.srcscanner_grace == 20.0
        assert session.srcscanner_name == "srcscanner"
        assert session._srcscanner_watchdog is None

    @pytest.mark.asyncio
    async def test_kill_is_reported_as_a_warning(self):
        session = vivado_tcl.Session(argv=["vivado"])

        session._srcscanner_killed(info_make(4321, age=25.0))

        msg = session._queue.get_nowait()
        assert isinstance(msg, ToolMessage)
        assert msg.severity == MessageSeverity.WARNING
        assert msg.identifier == "GBS-SRCSCANNER"
        assert "4321" in msg.message
        assert session._queue.empty()

    @pytest.mark.asyncio
    async def test_close_without_launch(self):
        session = vivado_tcl.Session(argv=["vivado"])

        await asyncio.wait_for(session.close(), timeout=5.0)

        assert session._srcscanner_watchdog is None

    @pytest.mark.asyncio
    async def test_close_stops_the_watchdog(self):
        session = vivado_tcl.Session(argv=["vivado"])
        lister = ScriptedLister([[]])
        wd = ProcessWatchdog(
            root_pid=10, process_name="srcscanner", grace_seconds=10,
            on_kill=lambda info: None,
            interval=0.01, lister=lister, killer=RecordingKiller(),
        )
        wd.start()
        task = wd.task
        session._srcscanner_watchdog = wd

        await asyncio.wait_for(session.close(), timeout=5.0)

        assert task.cancelled()
        assert session._srcscanner_watchdog is None
