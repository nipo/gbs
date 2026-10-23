"""Tests for interactive session startup failures"""

import sys

import pytest

from gbs.build.interactive import Session
from gbs.build.platform import PtyProvider
from gbs.build.task import ToolFailure


class ShellSession(Session[list[str]]):
    """Session over a plain argv, enough to exercise startup"""

    def _cmd_serialize(self, cmd: list[str]) -> str:
        return " ".join(cmd)

    def _exit_command(self) -> list[str]:
        return ["exit"]


class TestSessionStartupFailure:
    """A tool dying before its first prompt reports what it printed"""

    SCRIPT = (
        "import os, sys\n"
        "print('banner line')\n"
        "sys.stdout.flush()\n"
        "sys.stderr.write('fatal: cannot start')\n"
        "sys.stderr.flush()\n"
        "os.abort()\n"
    )

    @pytest.mark.asyncio
    @pytest.mark.parametrize("use_pty", [False, True])
    async def test_output_tail_attached(self, tmp_path, use_pty):
        if use_pty and (not PtyProvider.available or PtyProvider.use_conpty):
            pytest.skip("Unix PTY not available")

        session = ShellSession(
            argv=[sys.executable, "-c", self.SCRIPT],
            cwd=tmp_path,
            env={"GBS_TEST": "1"},
            use_pty=use_pty,
        )

        with pytest.raises(ToolFailure) as info:
            await session._launch()

        failure = info.value
        assert failure.returncode != 0
        assert failure.env_extra == {"GBS_TEST": "1"}
        assert "out: banner line" in failure.log_tail
        assert "err: fatal: cannot start" in failure.log_tail
