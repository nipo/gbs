"""Output group simulation settings: loading, injection into every
backend's config, and transport to a remote segment"""

from pathlib import Path

import pytest

from gbs.config.model import GBSConfig, ToolConfig
from gbs.planner.planner import BuildPlanner
from gbs.plugins import get_plugin_registry
from gbs.project.model import OutputFile, OutputGroup
from gbs.project.partition import ConditionalGroup, FilterCondition, PartitionTemplate
from gbs.remote.segment import OutputGroupDescriptor
from gbs.repository.loader import LoadError, load_project
from gbs.repository.model import SourceFile


class SimulationSettings:
    SETTINGS = {
        "success_regex": "Terminating with error level: 0",
        "max_simulation_time": "10 ms",
    }

    @staticmethod
    def project(tmp_path: Path, simulation: str) -> Path:
        path = tmp_path / "project.gbs.yaml"
        path.write_text(f"""\
name: proj
root:
  name: top
  sources:
    - file_type: vhdl
      files: [tb.vhd]
output:
  - name: simulation
    topcell: tb
{simulation}
    outputs:
      - type: simulation-log
        path: simulation.log
""")
        return path

    TEMPLATE = PartitionTemplate(
        name="top",
        groups=[ConditionalGroup(
            name="root",
            conditions=[FilterCondition(
                expression="default",
                sources=[SourceFile(path=None, file_type="vhdl")])],
        )],
    )


def test_simulation_block_is_loaded(tmp_path):
    path = SimulationSettings.project(tmp_path, """\
    simulation:
      success_regex: "Terminating with error level: 0"
      max_simulation_time: "10 ms"
""")
    og, = load_project(path).output_groups
    assert og.simulation == SimulationSettings.SETTINGS


def test_simulation_block_is_optional(tmp_path):
    og, = load_project(SimulationSettings.project(tmp_path, "")).output_groups
    assert og.simulation == {}


@pytest.mark.parametrize("block, message", [
    ("    simulation: [success_regex]\n", "must be a dictionary"),
    ("    simulation:\n      stop: 1 ms\n", "unknown 'simulation' keys: stop"),
    ("    simulation:\n      max_simulation_time: 10\n", "must be a string"),
    ("    simulation:\n      success_regex: \"(\"\n", "not a valid regular expression"),
])
def test_invalid_simulation_block_is_rejected(tmp_path, block, message):
    with pytest.raises(LoadError, match=message):
        load_project(SimulationSettings.project(tmp_path, block))


async def test_simulation_settings_reach_the_run_pass(tmp_path):
    exe = tmp_path / "yavs"
    exe.write_text("")
    gbs_config = GBSConfig(tools=[ToolConfig("yavs", None, None, {"executable": str(exe)})])
    planner = BuildPlanner(
        [], get_plugin_registry().get_all_backends(), {}, gbs_config,
        root_partition_template=SimulationSettings.TEMPLATE,
    )
    og = OutputGroup(
        name="simulation", topcell="tb",
        simulation=dict(SimulationSettings.SETTINGS),
        outputs=[OutputFile(type="simulation-log", path=Path("simulation.log"))],
    )

    plan = await planner.plan(og)

    run, = [pm for pm in plan.passes if pm.name == "yavs-run"]
    assert run.config["simulation"] == SimulationSettings.SETTINGS


def test_descriptor_carries_simulation_settings():
    descriptor = OutputGroupDescriptor(
        name="simulation", topcell="tb", topcell_library="work",
        target={}, simulation=dict(SimulationSettings.SETTINGS),
        backend_config={}, exclude_dispatchers=[], outputs=[])

    copy = OutputGroupDescriptor.from_json(descriptor.to_json())

    assert copy.simulation == SimulationSettings.SETTINGS
