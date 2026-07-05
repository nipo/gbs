"""openxc7 dispatcher: FASM -> frames -> bitstream."""

from __future__ import annotations
from pathlib import Path

from ...utils import expand_path
from ...base import BaseDispatcher
from ...build.context import BuildContext
from ...build.task import ResourceTypology, BuildError
from .. import xilinx_part
from . import task


class Openxc7Dispatcher(BaseDispatcher):
    """Chains fasm2frames and xc7frames2bit for Series-7 bitstream generation.

    The dispatcher's primary tool_name is `fasm2frames`; the secondary
    tool `xc7frames2bit` is resolved through the build context. Both
    tools expose a `prjxray_db_root` config key that points at the
    prjxray database - the dispatcher joins it with the part's family
    subdirectory and the chipdb key.
    """

    def __init__(
        self,
        context: BuildContext,
        part: str,
        fasm2frames_tool: str = "fasm2frames",
        xc7frames2bit_tool: str = "xc7frames2bit",
    ):
        super().__init__(context, "openxc7", tool_name=fasm2frames_tool)
        self.part = part
        self.xc7frames2bit_tool_name = xc7frames2bit_tool

    def _get_prjxray_part_dir(self) -> Path:
        """Return the prjxray-db subdirectory for the target part."""
        db_root = self.get_tool_option("prjxray_db_root", None)
        if db_root is None:
            raise BuildError(
                f"fasm2frames needs 'prjxray_db_root' in tool config; "
                f"install openxc7 via apio or set it manually on "
                f"tools:{self.tool_name}."
            )
        family = xilinx_part.family_name(self.part)
        chip_key = xilinx_part.chipdb_key(self.part)
        if family is None or chip_key is None:
            raise BuildError(
                f"Cannot derive prjxray part directory: '{self.part}' is "
                f"not a recognized Xilinx part name (want e.g. xc7a35t-1cpg236)."
            )
        # openxc7's prjxray-db includes speed grade in the leaf dir name:
        # `.../artix7/xc7a35tcpg236-1/part.json`.
        m = xilinx_part.parse_part(self.part)
        speed_digit = m.group("speed").lstrip("-")
        part_dir = expand_path(db_root) / family / f"{chip_key}-{speed_digit}"
        if not part_dir.is_dir():
            raise BuildError(
                f"prjxray part directory {part_dir} not found. Extend the "
                f"openxc7 install with additional parts if needed."
            )
        return part_dir

    def _get_prjxray_family_dir(self) -> Path:
        """Return the prjxray-db family-level directory (fasm2frames --db-root)."""
        return self._get_prjxray_part_dir().parent

    def _get_fasm2frames_executable(self) -> str:
        return str(expand_path(self.get_tool_option("executable", "fasm2frames")))

    def _get_xc7frames2bit_config(self) -> dict:
        cfg = self.context.get_tool(self.xc7frames2bit_tool_name, required=False)
        if cfg is None:
            raise BuildError(
                f"Tool '{self.xc7frames2bit_tool_name}' is not configured. "
                f"Install openxc7 via apio, or add an explicit entry."
            )
        return cfg

    def _get_xc7frames2bit_executable(self) -> str:
        cfg = self._get_xc7frames2bit_config()
        return str(expand_path(cfg.get("executable", "xc7frames2bit")))

    async def process(self) -> None:
        """Register fasm2frames and xc7frames2bit tasks once inputs appear."""
        fasm_resources = list(self.context.filter_pending(file_type=["nextpnr-fasm"]))
        if not fasm_resources:
            self.debug("No FASM output yet, waiting")
            return

        if len(fasm_resources) > 1:
            self.warning(f"Multiple FASM files; using {fasm_resources[0].path}")

        fasm = fasm_resources[0]
        topcell = self.context.get_topcell()
        topcell_library = self.context.get_topcell_library()

        # Intermediate .frm file
        frames_path = self.context.output_path / f"{topcell}.frm"
        frames_resource = self.context.get_resource(
            frames_path,
            file_type="xc7-frames",
            library=topcell_library,
            typology=ResourceTypology.INTERMEDIATE,
            generated_by=self.name,
        )

        # Final .bit file
        bit_path = self.context.output_path / f"{topcell}.bit"
        bit_resource = self.context.get_resource(
            bit_path,
            file_type="xilinx-bitstream",
            library=topcell_library,
            typology=ResourceTypology.OUTPUT,
            generated_by=self.name,
        )

        task.Fasm2Frames(
            dispatcher=self,
            inputs=[fasm],
            outputs=[frames_resource],
        )
        task.Frames2Bit(
            dispatcher=self,
            inputs=[frames_resource],
            outputs=[bit_resource],
        )

        # FASM is consumed by the chain
        dependents = self.context.remove_pending(fasm.path)
        for dep in dependents:
            pass  # dependents propagate through frames+bit natively via input chain
