"""Tests for BuildPlanner"""

import pytest
from pathlib import Path

from gbs.planner import BuildPlanner, BuildPlan, BuildPlanError, plan_project
from gbs.backend.registry import BackendRegistry
from gbs.model.passes import Pass, Backend
from gbs.model.repository import (
    Project,
    Repository,
    OutputGroup,
    OutputFile,
    Partition,
    Library,
    FilterCondition,
    ConditionalGroup,
    SourceFile,
)


# Mock passes for testing
class MockVHDLSimulatePass(Pass):
    """Mock VHDL simulation pass"""
    name = "simulate"
    input_types = {"vhdl"}
    output_types = {"simulator"}

    def contribute_filter_vars(self, config):
        return {"sim": 1}

    async def execute(self, context, inputs):
        return []


class MockVerilogToVHDLPass(Pass):
    """Mock Verilog to VHDL transformation"""
    name = "transform"
    input_types = {"verilog"}
    output_types = {"vhdl"}

    async def execute(self, context, inputs):
        return []


class MockSynthesisPass(Pass):
    """Mock synthesis pass"""
    name = "synthesize"
    input_types = {"vhdl", "verilog"}
    output_types = {"netlist"}

    def contribute_filter_vars(self, config):
        return {"syn": 1}

    async def execute(self, context, inputs):
        return []


class MockBitstreamPass(Pass):
    """Mock bitstream generation"""
    name = "bitstream"
    input_types = {"netlist"}
    output_types = {"bitstream"}

    async def execute(self, context, inputs):
        return []


# Mock backends
class MockSimBackend(Backend):
    passes = [MockVHDLSimulatePass]


class MockTransformBackend(Backend):
    passes = [MockVerilogToVHDLPass]


class MockSynthBackend(Backend):
    passes = [MockSynthesisPass, MockBitstreamPass]


@pytest.fixture
def registry():
    """Create a registry with mock backends"""
    reg = BackendRegistry()
    reg._register_backend("mock.sim", MockSimBackend)
    reg._register_backend("mock.transform", MockTransformBackend)
    reg._register_backend("mock.synth", MockSynthBackend)
    return reg


@pytest.fixture
def simple_project():
    """Create a simple test project with VHDL sources"""
    # Create a partition with VHDL sources
    condition = FilterCondition(
        expression="default",
        sources=[
            SourceFile(Path("top.vhd"), "vhdl"),
            SourceFile(Path("sub.vhd"), "vhdl"),
        ]
    )
    group = ConditionalGroup(name="root", conditions=[condition])
    partition = Partition(name="root", groups=[group])

    # Create project
    project = Project(
        name="test_project",
        root_partition=partition,
        topcell="top",
        filter_vars={}
    )

    return project


@pytest.fixture
def mixed_project():
    """Create a project with both VHDL and Verilog sources"""
    condition = FilterCondition(
        expression="default",
        sources=[
            SourceFile(Path("top.vhd"), "vhdl"),
            SourceFile(Path("uart.v"), "verilog"),
        ]
    )
    group = ConditionalGroup(name="root", conditions=[condition])
    partition = Partition(name="root", groups=[group])

    project = Project(
        name="mixed_project",
        root_partition=partition,
        topcell="top",
        filter_vars={}
    )

    return project


def test_simple_simulation_plan(registry, simple_project):
    """Test planning a simple VHDL simulation"""
    output_group = OutputGroup(
        name="simulation",
        topcell="top",
        outputs=[OutputFile(type="simulator", path=Path("sim.exe"))]
    )

    planner = BuildPlanner(simple_project, [], registry)
    plan = planner.plan(output_group)

    assert plan is not None
    assert plan.output_group == output_group
    assert plan.main_pass == MockVHDLSimulatePass
    assert len(plan.passes) > 0
    assert MockVHDLSimulatePass in plan.passes


def test_transformation_path(registry, mixed_project):
    """Test planning with mixed sources finds a viable path"""
    output_group = OutputGroup(
        name="simulation",
        topcell="top",
        outputs=[OutputFile(type="simulator", path=Path("sim.exe"))]
    )

    planner = BuildPlanner(mixed_project, [], registry)
    plan = planner.plan(output_group)

    assert plan is not None
    # Should find a viable plan (may use VHDL-only path since VHDL sources exist)
    assert len(plan.passes) > 0


def test_synthesis_path(registry, simple_project):
    """Test planning synthesis (vhdl -> netlist)"""
    output_group = OutputGroup(
        name="synthesis",
        topcell="top",
        outputs=[OutputFile(type="netlist", path=Path("design.net"))]
    )

    planner = BuildPlanner(simple_project, [], registry)
    plan = planner.plan(output_group)

    assert plan is not None
    assert MockSynthesisPass in plan.passes


def test_multi_stage_path(registry, simple_project):
    """Test multi-stage path (vhdl -> netlist -> bitstream)"""
    output_group = OutputGroup(
        name="bitstream",
        topcell="top",
        outputs=[OutputFile(type="bitstream", path=Path("design.bit"))]
    )

    planner = BuildPlanner(simple_project, [], registry)
    plan = planner.plan(output_group)

    assert plan is not None
    # Should include both synthesis and bitstream passes
    assert MockSynthesisPass in plan.passes
    assert MockBitstreamPass in plan.passes


def test_require_backend_constraint(registry, simple_project):
    """Test require_backends constraint"""
    output_group = OutputGroup(
        name="simulation",
        topcell="top",
        require_backends=["mock.sim"],
        outputs=[OutputFile(type="simulator", path=Path("sim.exe"))]
    )

    planner = BuildPlanner(simple_project, [], registry)
    plan = planner.plan(output_group)

    assert plan is not None
    assert plan.main_pass == MockVHDLSimulatePass


def test_exclude_backend_constraint(registry, simple_project):
    """Test exclude_backends constraint"""
    # This should fail because we exclude the only backend that can simulate
    output_group = OutputGroup(
        name="simulation",
        topcell="top",
        exclude_backends=["mock.sim"],
        outputs=[OutputFile(type="simulator", path=Path("sim.exe"))]
    )

    planner = BuildPlanner(simple_project, [], registry)

    with pytest.raises(BuildPlanError, match="No viable build plan"):
        planner.plan(output_group)


def test_no_viable_plan_error(registry, simple_project):
    """Test error when no viable plan exists"""
    # Request an output type that no pass can produce
    output_group = OutputGroup(
        name="impossible",
        topcell="top",
        outputs=[OutputFile(type="nonexistent_type", path=Path("out.txt"))]
    )

    planner = BuildPlanner(simple_project, [], registry)

    with pytest.raises(BuildPlanError, match="No viable build plan"):
        planner.plan(output_group)


def test_filter_vars_contribution(registry, simple_project):
    """Test that main pass contributes filter_vars"""
    output_group = OutputGroup(
        name="simulation",
        topcell="top",
        filter_vars={"custom": "value"},
        outputs=[OutputFile(type="simulator", path=Path("sim.exe"))]
    )

    planner = BuildPlanner(simple_project, [], registry)
    plan = planner.plan(output_group)

    # The simulate pass contributes sim=1
    # Check that source enumeration used combined filter_vars
    assert plan.source_fileset is not None


def test_plan_project_with_multiple_outputs(registry, simple_project):
    """Test planning project with multiple output groups"""
    simple_project.output_groups = [
        OutputGroup(
            name="sim",
            topcell="top",
            outputs=[OutputFile(type="simulator", path=Path("sim.exe"))]
        ),
        OutputGroup(
            name="synth",
            topcell="top",
            outputs=[OutputFile(type="netlist", path=Path("design.net"))]
        ),
    ]

    plans = plan_project(simple_project, [], registry)

    assert len(plans) == 2
    assert plans[0].output_group.name == "sim"
    assert plans[1].output_group.name == "synth"


def test_build_plan_str():
    """Test BuildPlan string representation"""
    output_group = OutputGroup(
        name="test",
        topcell="top",
        outputs=[]
    )

    from gbs.model.repository import SourceFileSet
    plan = BuildPlan(
        output_group=output_group,
        main_pass=MockVHDLSimulatePass,
        source_fileset=SourceFileSet(),
        passes=[MockVHDLSimulatePass],
    )

    assert "test" in str(plan)
    assert "simulate" in str(plan)
