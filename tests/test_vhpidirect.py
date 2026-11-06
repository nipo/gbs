"""Tests for the VHPIDIRECT C compilation shared by simulator backends"""

import pytest

from gbs.builtin.vhpidirect import CCompilerInvocation
from gbs.ui.messages import MessageSeverity


class TestCCompilerDiagnostics:
    @staticmethod
    async def transform(lines):
        async def gen():
            for line in lines:
                yield line

        proc = CCompilerInvocation(argv=["true"])
        return [m async for m in proc.stderr_transform(gen())]

    @pytest.mark.asyncio
    async def test_located(self):
        m, = await self.transform(["/a/b.c:3:14: error: use of undeclared identifier 'x'"])
        assert (m.severity, m.file_path, m.line, m.column, m.message) == (
            MessageSeverity.ERROR, "/a/b.c", 3, 14, "use of undeclared identifier 'x'")

    @pytest.mark.asyncio
    async def test_fatal_and_warning(self):
        fatal, warning = await self.transform([
            "b.c:1:10: fatal error: 'x.h' file not found",
            "b.c:2:1: warning: unused variable 'y'",
        ])
        assert fatal.severity == MessageSeverity.ERROR
        assert warning.severity == MessageSeverity.WARNING

    @pytest.mark.asyncio
    async def test_unlocated(self):
        m, = await self.transform(["ld: symbol(s) not found for architecture arm64"])
        assert (m.severity, m.file_path) == (MessageSeverity.INFO, None)
