"""Tests for the new type-based build planner"""

import pytest
from pathlib import Path

from gbs.planner import BuildPlanner, BuildPlan, PlanningError, plan_project
from gbs.model.passes import Pass
from gbs.model.backend import BaseBackend
from gbs.model.repository import (
    Repository, Library, Partition, ConditionalGroup, FilterCondition,
    SourceFile, OutputGroup, OutputFile, Project
)


# Mock passes for testing
class VhdlToSimulatorPass(Pass):
    """VHDL → simulator"""
    name = "vhdl-simulate"
    input_types = {"vhdl"}
    output_types = {"simulator"}

    def contribute_filter_vars(self, config):
        return {"target-usage": "simulation"}


class VerilogToVhdlPass(Pass):
    """Verilog → VHDL transpilation"""
    name = "verilog-to-vhdl"
    input_types = {"verilog"}
    output_types = {"vhdl"}

    def contribute_filter_vars(self, config):
        return {"transpiled": True}


class VhdlToNetlistPass(Pass):
    """VHDL → netlist synthesis"""
    name = "vhdl-synthesize"
    input_types = {"vhdl"}
    output_types = {"netlist"}

    def contribute_filter_vars(self, config):
        return {"target-usage": "synthesis"}


class NetlistToBitstreamPass(Pass):
    """Netlist → bitstream"""
    name = "netlist-to-bitstream"
    input_types = {"netlist"}
    output_types = {"bitstream"}


# Mock backends
class SimulatorBackend(BaseBackend):
    """Provides simulation pass"""

    def __init__(self):
        super().__init__("test.simulator")

    def contribute_passes(self, config, output_types):
        if "simulator" in output_types:
            return [VhdlToSimulatorPass]
        return []

    def create_dispatcher(self, config):
        from gbs.model.dispatcher import BaseDispatcher

        class TestDispatcher(BaseDispatcher):
            def get_filter_variables(self, context):
                return {}

            async def process(self, context, fileset):
                pass

        return TestDispatcher("test")


class TranspilerBackend(BaseBackend):
    """Provides Verilog → VHDL transpilation"""

    def __init__(self):
        super().__init__("test.transpiler")

    def contribute_passes(self, config, output_types):
        # Always offer transpilation (it might be needed for intermediate steps)
        return [VerilogToVhdlPass]

    def create_dispatcher(self, config):
        from gbs.model.dispatcher import BaseDispatcher

        class TestDispatcher(BaseDispatcher):
            def get_filter_variables(self, context):
                return {}

            async def process(self, context, fileset):
                pass

        return TestDispatcher("test")


class SynthesisBackend(BaseBackend):
    """Provides synthesis passes"""

    def __init__(self):
        super().__init__("test.synthesis")

    def contribute_passes(self, config, output_types):
        passes = []
        if "netlist" in output_types or "bitstream" in output_types:
            passes.append(VhdlToNetlistPass)
        if "bitstream" in output_types:
            passes.append(NetlistToBitstreamPass)
        return passes

    def create_dispatcher(self, config):
        from gbs.model.dispatcher import BaseDispatcher

        class TestDispatcher(BaseDispatcher):
            def get_filter_variables(self, context):
                return {}

            async def process(self, context, fileset):
                pass

        return TestDispatcher("test")


@pytest.fixture
def vhdl_repository():
    """Repository with VHDL sources"""
    repo = Repository(name="test_repo", root=Path("/test"))
    lib = Library(name="work")

    partition = Partition(
        name="sources",
        groups=[
            ConditionalGroup(
                name="main",
                conditions=[
                    FilterCondition(
                        expression="default",
                        sources=[
                            SourceFile(Path("a.vhd"), "vhdl", "2008"),
                            SourceFile(Path("b.vhd"), "vhdl", "2008"),
                        ]
                    )
                ]
            )
        ]
    )

    lib.add_partition(partition)
    repo.add_library(lib)
    return repo


@pytest.fixture
def verilog_repository():
    """Repository with Verilog sources"""
    repo = Repository(name="verilog_repo", root=Path("/verilog"))
    lib = Library(name="work")

    partition = Partition(
        name="sources",
        groups=[
            ConditionalGroup(
                name="main",
                conditions=[
                    FilterCondition(
                        expression="default",
                        sources=[
                            SourceFile(Path("x.v"), "verilog"),
                            SourceFile(Path("y.v"), "verilog"),
                        ]
                    )
                ]
            )
        ]
    )

    lib.add_partition(partition)
    repo.add_library(lib)
    return repo


class TestBuildPlanner:
    """Test BuildPlanner class"""

    def test_planner_initialization(self, vhdl_repository):
        """Test planner initializes correctly"""
        backends = [SimulatorBackend()]
        planner = BuildPlanner([vhdl_repository], backends)

        assert len(planner.repositories) == 1
        assert len(planner.backends) == 1
        assert "vhdl" in planner.available_source_types

    def test_simple_single_pass_plan(self, vhdl_repository):
        """Test planning with single pass: VHDL → simulator"""
        backends = [SimulatorBackend()]
        planner = BuildPlanner([vhdl_repository], backends)

        output_group = OutputGroup(
            name="simulation",
            topcell="testbench",
            filter_vars={},
            outputs=[OutputFile(type="simulator", path=Path("sim"))]
        )

        plan = planner.plan(output_group)

        assert isinstance(plan, BuildPlan)
        assert len(plan.passes) == 1
        assert plan.passes[0].name == "vhdl-simulate"
        assert plan.combined_filter_vars["target-usage"] == "simulation"

    def test_multi_pass_chain(self, verilog_repository):
        """Test planning with pass chain: Verilog → VHDL → simulator"""
        backends = [SimulatorBackend(), TranspilerBackend()]
        planner = BuildPlanner([verilog_repository], backends)

        output_group = OutputGroup(
            name="simulation",
            topcell="testbench",
            filter_vars={},
            outputs=[OutputFile(type="simulator", path=Path("sim"))]
        )

        plan = planner.plan(output_group)

        assert len(plan.passes) == 2
        # Should have verilog-to-vhdl first, then vhdl-simulate
        pass_names = [p.name for p in plan.passes]
        assert "verilog-to-vhdl" in pass_names
        assert "vhdl-simulate" in pass_names

    def test_synthesis_chain(self, vhdl_repository):
        """Test planning synthesis chain: VHDL → netlist → bitstream"""
        backends = [SynthesisBackend()]
        planner = BuildPlanner([vhdl_repository], backends)

        output_group = OutputGroup(
            name="synthesis",
            topcell="top",
            filter_vars={},
            outputs=[OutputFile(type="bitstream", path=Path("out.bit"))]
        )

        plan = planner.plan(output_group)

        assert len(plan.passes) == 2
        pass_names = [p.name for p in plan.passes]
        assert "vhdl-synthesize" in pass_names
        assert "netlist-to-bitstream" in pass_names

    def test_filter_vars_combination(self, vhdl_repository):
        """Test filter variables are combined correctly"""
        backends = [SimulatorBackend()]
        planner = BuildPlanner([vhdl_repository], backends)

        output_group = OutputGroup(
            name="simulation",
            topcell="testbench",
            filter_vars={"base-var": "base-value"},
            outputs=[OutputFile(type="simulator", path=Path("sim"))]
        )

        plan = planner.plan(output_group)

        # Should have both base filter_vars and pass filter_vars
        assert plan.combined_filter_vars["base-var"] == "base-value"
        assert plan.combined_filter_vars["target-usage"] == "simulation"

    def test_no_backend_for_output(self, vhdl_repository):
        """Test error when no backend can produce desired output"""
        backends = [SimulatorBackend()]  # Only provides simulator
        planner = BuildPlanner([vhdl_repository], backends)

        output_group = OutputGroup(
            name="unknown",
            topcell="top",
            filter_vars={},
            outputs=[OutputFile(type="unknown-type", path=Path("out"))]
        )

        with pytest.raises(PlanningError, match="No passes found"):
            planner.plan(output_group)

    def test_no_source_for_input(self):
        """Test error when no sources match pass requirements"""
        # Repository with only text files
        repo = Repository(name="test", root=Path("/test"))
        lib = Library(name="work")
        partition = Partition(
            name="sources",
            groups=[
                ConditionalGroup(
                    name="main",
                    conditions=[
                        FilterCondition(
                            expression="default",
                            sources=[SourceFile(Path("readme.txt"), "text")]
                        )
                    ]
                )
            ]
        )
        lib.add_partition(partition)
        repo.add_library(lib)

        backends = [SimulatorBackend()]  # Needs VHDL
        planner = BuildPlanner([repo], backends)

        output_group = OutputGroup(
            name="simulation",
            topcell="test",
            filter_vars={},
            outputs=[OutputFile(type="simulator", path=Path("sim"))]
        )

        with pytest.raises(PlanningError, match="No transformation path"):
            planner.plan(output_group)

    def test_multiple_repositories(self, vhdl_repository, verilog_repository):
        """Test planner with multiple repositories"""
        backends = [SimulatorBackend(), TranspilerBackend()]
        planner = BuildPlanner([vhdl_repository, verilog_repository], backends)

        # Both VHDL and Verilog should be available
        assert "vhdl" in planner.available_source_types
        assert "verilog" in planner.available_source_types

    def test_backend_config_passed_to_passes(self, vhdl_repository):
        """Test backend configuration is passed to passes"""

        class ConfigurablePass(Pass):
            name = "configurable"
            input_types = {"vhdl"}
            output_types = {"custom"}

            def contribute_filter_vars(self, config):
                return {"mode": config.get("mode", "default")}

        class ConfigurableBackend(BaseBackend):
            def __init__(self):
                super().__init__("test.configurable")

            def contribute_passes(self, config, output_types):
                if "custom" in output_types:
                    return [ConfigurablePass]
                return []

            def create_dispatcher(self, config):
                from gbs.model.dispatcher import BaseDispatcher

                class TestDispatcher(BaseDispatcher):
                    def get_filter_variables(self, context):
                        return {}

                    async def process(self, context, fileset):
                        pass

                return TestDispatcher("test")

        backends = [ConfigurableBackend()]
        planner = BuildPlanner([vhdl_repository], backends)

        output_group = OutputGroup(
            name="custom",
            topcell="top",
            filter_vars={},
            backend_config={"test.configurable": {"mode": "special"}},
            outputs=[OutputFile(type="custom", path=Path("out"))]
        )

        plan = planner.plan(output_group)

        # Check that backend config was passed through
        assert plan.combined_filter_vars["mode"] == "special"


class TestPlanProject:
    """Test plan_project convenience function"""

    def test_plan_project_single_output_group(self, vhdl_repository):
        """Test planning entire project"""
        backends = [SimulatorBackend()]

        # Create project with output groups
        root_partition = Partition(name="root", groups=[])
        project = Project(
            name="test_project",
            root_partition=root_partition,
            topcell="top",  # Legacy, not used by new planner
            filter_vars={},  # Legacy, not used by new planner
            output_groups=[
                OutputGroup(
                    name="simulation",
                    topcell="testbench",
                    filter_vars={},
                    outputs=[OutputFile(type="simulator", path=Path("sim"))]
                )
            ]
        )

        plans = plan_project(project, [vhdl_repository], backends)

        assert len(plans) == 1
        assert plans[0].output_group.name == "simulation"
        assert len(plans[0].passes) == 1

    def test_plan_project_multiple_output_groups(self, vhdl_repository):
        """Test planning project with multiple output groups"""
        backends = [SimulatorBackend(), SynthesisBackend()]

        root_partition = Partition(name="root", groups=[])
        project = Project(
            name="test_project",
            root_partition=root_partition,
            topcell="top",
            filter_vars={},
            output_groups=[
                OutputGroup(
                    name="simulation",
                    topcell="testbench",
                    filter_vars={},
                    outputs=[OutputFile(type="simulator", path=Path("sim"))]
                ),
                OutputGroup(
                    name="synthesis",
                    topcell="top",
                    filter_vars={},
                    outputs=[OutputFile(type="bitstream", path=Path("out.bit"))]
                ),
            ]
        )

        plans = plan_project(project, [vhdl_repository], backends)

        assert len(plans) == 2
        assert plans[0].output_group.name == "simulation"
        assert plans[1].output_group.name == "synthesis"
