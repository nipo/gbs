"""Tests for the -t/--tool and --tool-version backend matching."""

import pytest
from asyncclick.testing import CliRunner

from gbs.cli import cli


@pytest.mark.asyncio
async def test_tool_rejects_unmatched_backend():
    result = await CliRunner().invoke(
        cli, ["-t", "gbs.builtin.gowin:gowin=1.9.12.04", "config", "dump"])

    assert result.exit_code != 0
    assert "--tool gbs.builtin.gowin:gowin=1.9.12.04" in result.output
    assert "gbs.builtin.gowin," in result.output
    assert "BACKEND=TOOL[:VARIANT][@VERSION]" in result.output


@pytest.mark.asyncio
async def test_tool_version_rejects_unmatched_backend():
    result = await CliRunner().invoke(
        cli, ["--tool-version", "nosuch=1", "config", "dump"])

    assert result.exit_code != 0
    assert "--tool-version nosuch=1" in result.output
    assert "gbs.builtin.gowin," in result.output
    assert "BACKEND=VERSION" in result.output


@pytest.mark.asyncio
async def test_tool_accepts_matched_backend():
    result = await CliRunner().invoke(
        cli, ["-t", "gowin=gowin:1.9.12.04", "config", "dump"])

    assert result.exit_code == 0, result.output
