"""Tests for global CLI verbosity controls."""

import io

import pytest
from asyncclick.testing import CliRunner

from gbs.cli import cli
from gbs.ui.backends.simple import SimpleBackend
from gbs.ui.messages import BuildStatus, LogLevel, LogMessage, MessageSeverity, ToolMessage


@pytest.mark.asyncio
async def test_help_lists_quiet_option():
    result = await CliRunner().invoke(cli, ["--help"])

    assert result.exit_code == 0
    assert "-q, --quiet" in result.output
    assert "Only show errors" in result.output


@pytest.mark.asyncio
async def test_quiet_rejects_verbose_and_debug():
    for verbosity in ("--verbose", "--debug"):
        result = await CliRunner().invoke(
            cli,
            ["--quiet", verbosity, "config", "dump"],
        )

        assert result.exit_code == 2
        assert "--quiet cannot be combined" in result.output


@pytest.mark.asyncio
async def test_quiet_backend_only_renders_errors_and_failures():
    output = io.StringIO()
    error = io.StringIO()
    backend = SimpleBackend(
        output=output,
        error=error,
        show_progress=False,
        min_severity=MessageSeverity.ERROR,
        min_log_level=LogLevel.ERROR,
        quiet=True,
    )

    await backend.render(ToolMessage(MessageSeverity.WARNING, "warning"))
    await backend.render(ToolMessage(MessageSeverity.ERROR, "tool error"))
    await backend.render(LogMessage(LogLevel.WARNING, "warning"))
    await backend.render(LogMessage(LogLevel.ERROR, "log error"))
    await backend.render(BuildStatus("success", "build"))
    await backend.render(BuildStatus("failure", "build"))

    assert output.getvalue() == "[FAILED] build\n"
    assert "tool error" in error.getvalue()
    assert "log error" in error.getvalue()
    assert "warning" not in error.getvalue()
