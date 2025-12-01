"""Tests for BuildPlanExecutor"""

import pytest
from pathlib import Path

from gbs.executor import BuildPlanExecutor, execute_project
from gbs.planner import BuildPlan
from gbs.model.build import BuildContext
from gbs.model.repository import (
    OutputGroup,
    OutputFile,
    SourceFileSet,
)
from gbs.model.passes import Pass


class DummyPass(Pass):
    """Dummy pass for testing"""
    name = "dummy"
    input_types = {"vhdl"}
    output_types = {"dummy_out"}

    async def execute(self, context, inputs):
        return []


@pytest.mark.asyncio
async def test_executor_creation():
    """Test creating an executor"""
    context = BuildContext()
    executor = BuildPlanExecutor(context)

    assert executor.context is context


@pytest.mark.asyncio
async def test_execute_plan_stub():
    """Test executing a build plan (stub implementation)"""
    context = BuildContext()
    executor = BuildPlanExecutor(context)

    output_group = OutputGroup(
        name="test",
        topcell="top",
        outputs=[OutputFile(type="dummy_out", path=Path("out.txt"))]
    )

    plan = BuildPlan(
        output_group=output_group,
        main_pass=DummyPass,
        source_fileset=SourceFileSet(),
        passes=[DummyPass]
    )

    # Execute (currently a stub, just verifies it doesn't crash)
    fileset = await executor.execute_plan(plan)

    assert fileset is not None


@pytest.mark.asyncio
async def test_execute_project():
    """Test executing multiple plans for a project"""
    context = BuildContext()

    output_group1 = OutputGroup(
        name="test1",
        topcell="top",
        outputs=[OutputFile(type="dummy_out", path=Path("out1.txt"))]
    )

    output_group2 = OutputGroup(
        name="test2",
        topcell="top",
        outputs=[OutputFile(type="dummy_out", path=Path("out2.txt"))]
    )

    plan1 = BuildPlan(
        output_group=output_group1,
        main_pass=DummyPass,
        source_fileset=SourceFileSet(),
        passes=[DummyPass]
    )

    plan2 = BuildPlan(
        output_group=output_group2,
        main_pass=DummyPass,
        source_fileset=SourceFileSet(),
        passes=[DummyPass]
    )

    results = await execute_project(context, [plan1, plan2])

    assert len(results) == 2
    assert "test1" in results
    assert "test2" in results
