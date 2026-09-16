"""Common infrastructure for the Vivado-driven backends

Vivado serves several flows (synthesis, IP packaging, ...). They all talk
to the same tool through the same TCL session and describe themselves to
the planner the same way; this module holds what they share.
"""

from __future__ import annotations
from typing import Any

from ...base import BaseDispatcher
from ...build.context import BuildContext
from ...utils import expand_path, resolve_tool_exe
from .vivado_tcl import Session

__all__ = ["VivadoDispatcherBase"]


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

    async def close(self) -> None:
        if self.session is not None:
            await self.session.close()
            self.session = None
