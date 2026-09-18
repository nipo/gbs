"""Common infrastructure for the Vivado-driven backends

Vivado serves several flows (synthesis, IP packaging, ...). They all talk
to the same tool through the same TCL session and describe themselves to
the planner the same way; this module holds what they share.
"""

from __future__ import annotations
from typing import Any

from ...base import BaseDispatcher, BasePass
from ...build.context import BuildContext
from ...utils import expand_path, resolve_tool_exe
from ..xilinx_part import XilinxPart
from .vivado_tcl import Session

__all__ = ["VivadoDispatcherBase", "VivadoPassBase"]


class VivadoDispatcherBase(BaseDispatcher):
    """Dispatcher owning a Vivado TCL session

    Subclasses implement process() and create their own tasks, handing
    them the session returned by session_get().
    """

    def __init__(
        self,
        context: BuildContext,
        name: str,
        vhdl_std: str = "2008",
        vivado_tool: str = "vivado",
        target: dict[str, str] | None = None,
    ):
        super().__init__(context, name, tool_name=vivado_tool)
        self.vhdl_std = vhdl_std
        self.target = target or {}
        self.session = None

    def session_get(self) -> Session:
        """Get or create the shared Vivado TCL session"""
        if self.session is None:
            vivado_path = expand_path(self.get_tool_option("path"))

            try:
                vivado_exe = resolve_tool_exe(vivado_path / "bin" / "vivado")
            except FileNotFoundError:
                raise RuntimeError(f"Vivado not found at {vivado_path}")

            self.session = Session(
                argv=[
                    str(vivado_exe),
                    "-mode", "tcl",
                    "-nojournal",
                    "-nolog",
                ],
                cwd=self.context.output_path,
                env=self.tool_env or None,
                use_pty=True,  # Vivado requires a tty for proper interactive behavior
            )

        return self.session

    def inputs_attach(self, task, accepted_types: set[str]) -> None:
        """Attach the pending inputs the task accepts, in compilation order

        Libraries come in dependency order, so a Vivado project built by
        handing the sources over in that order needs no scanning of its
        own to find the compilation order.
        """
        existing_paths = {r.path for r in task.inputs}

        for library, resources in self.context.get_pending_by_library_ordered():
            for source in resources:
                if source.file_type not in accepted_types:
                    continue
                if source.path in existing_paths:
                    continue

                self.debug(f"Attaching input: {source.path} "
                           f"(type={source.file_type}, lib={library})")
                task.add_input(self.context.get_resource(source.path))

    async def close(self) -> None:
        if self.session is not None:
            await self.session.close()
            self.session = None


class VivadoPassBase(BasePass):
    """Pass planning a build step run by Vivado

    Subclasses declare their input and output types, and create their
    dispatcher.
    """

    # Whether the flow this pass plans takes the design through
    # place-and-route
    runs_pnr = False

    def probe(self) -> str | None:
        """Reject targets Vivado cannot handle, then check the tool

        Vivado handles 7-series and later; earlier families are ISE
        territory.
        """
        target = self.config.get("target") or {}
        part = (target.get("part") or "").lower()
        if not part.startswith("xc"):
            return f"target part {part!r} is not a Xilinx device"
        if part[:3] in ("xc3", "xc4", "xc5", "xc6"):
            return (f"target part {part!r} is pre-7-series; Vivado only "
                    f"handles 7-series and later")
        return self.probe_tool("vivado")

    def filter_vars(self) -> dict[str, Any]:
        """Contribute canonical filter variables for a Vivado flow.

        Vivado is both the HDL frontend and the synthesis engine, and
        produces bitstreams on its own.
        """
        vhdl_std = self.config.get("vhdl_standard", "1993")

        filter_vars: dict[str, Any] = {
            "purpose": "synthesis",
            "vendor": "xilinx",
            "vhdl_frontend": "vivado",
            "verilog_frontend": "vivado",
            "synthesis_engine": "vivado",
            "bitstream_engine": "vivado",
            "vhdl_std": vhdl_std,
        }

        if self.runs_pnr:
            filter_vars["pnr_engine"] = "vivado"

        target = self.config.get("target", {})
        device = target.get("part")
        if device:
            filter_vars.update(XilinxPart.filter_vars_of(device))

        return filter_vars
