"""Integration tests for the complete build flow

Tests the full build pipeline:
- Project loading
- Dependency resolution
- Build planning
- Build execution
- Output verification
"""

import pytest
from pathlib import Path
import shutil
import tempfile

from gbs.loaders import load_project_with_repositories
from gbs.resolver import resolve_project
from gbs.tasks import BuildContext
from gbs.backend.registry import BackendRegistry
from gbs.planner import plan_project
from gbs.executor import execute_project
from gbs.config import GBSConfig


@pytest.fixture
def temp_project_dir():
    """Create a temporary directory for test projects"""
    tmpdir = Path(tempfile.mkdtemp())
    yield tmpdir
    # Cleanup
    shutil.rmtree(tmpdir, ignore_errors=True)


@pytest.fixture
def gbs_config():
    """Create a minimal GBSConfig for testing"""
    # Create minimal config - just enough to not crash
    config = GBSConfig(tools={}, repositories=[])
    return config


@pytest.fixture
def simple_vhdl_project(temp_project_dir):
    """Create a simple VHDL project for testing"""
    # Create project YAML
    project_yaml = temp_project_dir / "project.gbs.yaml"
    project_yaml.write_text("""
name: test_project

root:
  name: work
  sources:
    - language: vhdl
      files:
        - test.vhd

output:
  - name: simulation
    topcell: test_entity
    filter_vars: {}
    backend_config:
      gbs.backend.ghdl:
        vhdl_standard: "93c"
        ghdl_tool: "ghdl:system"
    outputs:
      - type: ghdl-simulator
        path: build/test_entity
""")

    # Create simple VHDL file
    vhdl_file = temp_project_dir / "test.vhd"
    vhdl_file.write_text("""
entity test_entity is
end entity test_entity;

architecture rtl of test_entity is
begin
end architecture rtl;
""")

    return project_yaml


@pytest.mark.asyncio
async def test_complete_build_flow(simple_vhdl_project, gbs_config):
    """Test the complete build flow from loading to execution"""

    # 1. Load project
    project, repositories = load_project_with_repositories(simple_vhdl_project)

    assert project is not None
    assert project.name == "test_project"
    assert len(project.output_groups) == 1
    assert project.output_groups[0].name == "simulation"
    assert project.output_groups[0].topcell == "test_entity"

    # 2. Resolve dependencies
    build_set = resolve_project(project, repositories)

    assert build_set is not None
    assert len(build_set.get_all_files()) == 1
    assert "work" in build_set.libraries

    # 3. Create backend registry
    registry = BackendRegistry()
    registry.discover_backends()

    assert len(registry._backends) > 0
    assert "gbs.backend.ghdl" in registry._backends

    # 4. Plan build
    build_context = BuildContext(project=project, gbs_config=gbs_config)
    plans = plan_project(project, repositories, registry)

    assert len(plans) == 1
    plan = plans[0]
    assert plan.output_group.name == "simulation"
    assert len(plan.passes) > 0  # Should have at least the GHDL pass

    # 5. Execute build
    results = await execute_project(build_context, plans)

    assert len(results) == 1
    assert "simulation" in results

    fileset = results["simulation"]
    assert len(fileset) >= 1  # At least the source file


def test_multiple_output_groups(temp_project_dir):
    """Test project with multiple output groups"""

    # Create project with two output groups
    project_yaml = temp_project_dir / "project.gbs.yaml"
    project_yaml.write_text("""
name: multi_output_project

root:
  name: work
  sources:
    - language: vhdl
      files:
        - test.vhd

output:
  - name: sim1
    topcell: entity1
    filter_vars: {}
    backend_config: {}
    outputs:
      - type: ghdl-simulator
        path: build/sim1/entity1

  - name: sim2
    topcell: entity2
    filter_vars: {}
    backend_config: {}
    outputs:
      - type: ghdl-simulator
        path: build/sim2/entity2
""")

    vhdl_file = temp_project_dir / "test.vhd"
    vhdl_file.write_text("""
entity entity1 is
end entity entity1;

architecture rtl of entity1 is
begin
end architecture rtl;

entity entity2 is
end entity entity2;

architecture rtl of entity2 is
begin
end architecture rtl;
""")

    # Load and verify
    project, repositories = load_project_with_repositories(project_yaml)

    assert len(project.output_groups) == 2
    assert project.output_groups[0].name == "sim1"
    assert project.output_groups[1].name == "sim2"
    assert project.output_groups[0].topcell == "entity1"
    assert project.output_groups[1].topcell == "entity2"

    # Plan build - verify we can create plans for both
    registry = BackendRegistry()
    registry.discover_backends()

    plans = plan_project(project, repositories, registry)

    assert len(plans) == 2
    assert plans[0].output_group.name == "sim1"
    assert plans[1].output_group.name == "sim2"


@pytest.mark.asyncio
async def test_backend_config_propagation(temp_project_dir):
    """Test that backend_config is properly passed to passes"""

    project_yaml = temp_project_dir / "project.gbs.yaml"
    project_yaml.write_text("""
name: config_test_project

root:
  name: work
  sources:
    - language: vhdl
      files:
        - test.vhd

output:
  - name: simulation
    topcell: test_entity
    filter_vars:
      target-usage: simulation
    backend_config:
      gbs.backend.ghdl:
        vhdl_standard: "2008"
        ghdl_tool: "ghdl:jit"
    outputs:
      - type: ghdl-simulator
        path: build/test_entity
""")

    vhdl_file = temp_project_dir / "test.vhd"
    vhdl_file.write_text("""
entity test_entity is
end entity test_entity;

architecture rtl of test_entity is
begin
end architecture rtl;
""")

    project, repositories = load_project_with_repositories(project_yaml)

    # Verify backend config was loaded
    output_group = project.output_groups[0]
    assert "gbs.backend.ghdl" in output_group.backend_config
    assert output_group.backend_config["gbs.backend.ghdl"]["vhdl_standard"] == "2008"
    assert output_group.backend_config["gbs.backend.ghdl"]["ghdl_tool"] == "ghdl:jit"

    # Verify filter vars
    assert output_group.filter_vars["target-usage"] == "simulation"

    # Plan build
    registry = BackendRegistry()
    registry.discover_backends()

    build_context = BuildContext(project=project, gbs_config={})
    plans = plan_project(project, repositories, registry)

    # Verify backend config is in the plan
    plan = plans[0]
    assert "gbs.backend.ghdl" in plan.backend_configs


def test_output_file_specifications(temp_project_dir):
    """Test that output file specifications are properly loaded"""

    project_yaml = temp_project_dir / "project.gbs.yaml"
    project_yaml.write_text("""
name: output_test_project

root:
  name: work
  sources:
    - language: vhdl
      files:
        - test.vhd

output:
  - name: simulation
    topcell: test_entity
    filter_vars: {}
    backend_config: {}
    outputs:
      - type: ghdl-simulator
        path: build/simulator/test_entity
      - type: vcd-trace
        path: build/traces/simulation.vcd
""")

    vhdl_file = temp_project_dir / "test.vhd"
    vhdl_file.write_text("""
entity test_entity is
end entity test_entity;

architecture rtl of test_entity is
begin
end architecture rtl;
""")

    project, _ = load_project_with_repositories(project_yaml)

    output_group = project.output_groups[0]
    assert len(output_group.outputs) == 2

    # Verify first output
    assert output_group.outputs[0].type == "ghdl-simulator"
    assert str(output_group.outputs[0].path) == "build/simulator/test_entity"

    # Verify second output
    assert output_group.outputs[1].type == "vcd-trace"
    assert str(output_group.outputs[1].path) == "build/traces/simulation.vcd"


@pytest.mark.asyncio
async def test_constraint_fields(temp_project_dir):
    """Test that constraint fields (require/exclude) are properly loaded"""

    project_yaml = temp_project_dir / "project.gbs.yaml"
    project_yaml.write_text("""
name: constraint_test_project

root:
  name: work
  sources:
    - language: vhdl
      files:
        - test.vhd

output:
  - name: simulation
    topcell: test_entity
    filter_vars: {}
    backend_config: {}
    require_passes:
      - gbs.backend.ghdl:simulate
    exclude_passes:
      - gbs.backend.gowin:synthesize
    require_backends:
      - gbs.backend.ghdl
    exclude_backends:
      - gbs.backend.gowin
    outputs:
      - type: ghdl-simulator
        path: build/test_entity
""")

    vhdl_file = temp_project_dir / "test.vhd"
    vhdl_file.write_text("""
entity test_entity is
end entity test_entity;

architecture rtl of test_entity is
begin
end architecture rtl;
""")

    project, _ = load_project_with_repositories(project_yaml)

    output_group = project.output_groups[0]

    # Verify constraint fields
    assert "gbs.backend.ghdl:simulate" in output_group.require_passes
    assert "gbs.backend.gowin:synthesize" in output_group.exclude_passes
    assert "gbs.backend.ghdl" in output_group.require_backends
    assert "gbs.backend.gowin" in output_group.exclude_backends
