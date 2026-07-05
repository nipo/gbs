"""openxc7 tasks: fasm2frames then xc7frames2bit."""

from __future__ import annotations
import re

from ...build.task import Task, BuildError
from ...build.subprocess import MessageSubprocess
from ...ui.messages import MessageSeverity, ToolMessage
from .. import xilinx_part


class _ScriptInvocation(MessageSubprocess):
    """Minimal message parser for the openxc7 python scripts.

    They mostly print bare progress lines to stderr; only classify
    obvious level prefixes and let everything else land as DEBUG.
    """

    msg_pattern = re.compile(
        r'^(?P<level>INFO|WARNING|ERROR|CRITICAL|FATAL)\b[: ]\s*(?P<message>.*)$',
        re.IGNORECASE,
    )

    level_map = {
        "info": MessageSeverity.INFO,
        "warning": MessageSeverity.WARNING,
        "error": MessageSeverity.ERROR,
        "critical": MessageSeverity.ERROR,
        "fatal": MessageSeverity.FATAL,
    }

    async def stderr_transform(self, lines):
        async for line in lines:
            m = self.msg_pattern.match(line)
            if m:
                sev = self.level_map.get(m.group("level").lower(), MessageSeverity.INFO)
                yield ToolMessage(severity=sev, message=m.group("message"))
            else:
                yield ToolMessage(severity=MessageSeverity.DEBUG, message=line)


class Fasm2Frames(Task):
    """Run fasm2frames: FASM -> binary frame deltas."""

    def __init__(self, dispatcher, inputs, outputs):
        topcell = dispatcher.context.get_topcell()
        super().__init__(
            dispatcher=dispatcher,
            name=f"fasm2frames_{topcell}",
            inputs=inputs,
            outputs=outputs,
            description=f"fasm2frames {topcell}",
        )

    async def work(self) -> None:
        output, = self.outputs
        output.path.parent.mkdir(parents=True, exist_ok=True)
        fasm, = self.inputs

        family_dir = self.dispatcher._get_prjxray_family_dir()
        chipdb = xilinx_part.chipdb_key(self.dispatcher.part)
        m = xilinx_part.parse_part(self.dispatcher.part)
        speed = m.group("speed").lstrip("-")
        part_key = f"{chipdb}-{speed}"

        cmd = [
            self.dispatcher._get_fasm2frames_executable(),
            "--db-root", str(family_dir),
            "--part", part_key,
            str(fasm.path),
            str(output.path),
        ]

        proc = _ScriptInvocation(argv=cmd, env=self.dispatcher.tool_env or None)
        async for msg in proc:
            await self.add_message_obj(msg)
        if proc.returncode != 0:
            output.path.unlink(missing_ok=True)
            raise BuildError(f"fasm2frames failed with exit code {proc.returncode}")
        self.info("FASM to frames complete")


class Frames2Bit(Task):
    """Run xc7frames2bit: frame deltas -> Xilinx .bit."""

    def __init__(self, dispatcher, inputs, outputs):
        topcell = dispatcher.context.get_topcell()
        super().__init__(
            dispatcher=dispatcher,
            name=f"xc7frames2bit_{topcell}",
            inputs=inputs,
            outputs=outputs,
            description=f"xc7frames2bit {topcell}",
        )

    async def work(self) -> None:
        output, = self.outputs
        output.path.parent.mkdir(parents=True, exist_ok=True)
        frames, = self.inputs

        part_dir = self.dispatcher._get_prjxray_part_dir()
        # xc7frames2bit needs the YAML flavor; part.json is present too
        # but only for tooling that speaks project X-ray's JSON schema.
        part_file = part_dir / "part.yaml"
        if not part_file.is_file():
            raise BuildError(f"prjxray part.yaml missing at {part_file}")

        m = xilinx_part.parse_part(self.dispatcher.part)
        chipdb = xilinx_part.chipdb_key(self.dispatcher.part)
        speed = m.group("speed").lstrip("-")
        part_name = f"{chipdb}-{speed}"

        cmd = [
            self.dispatcher._get_xc7frames2bit_executable(),
            f"--frm_file={frames.path}",
            f"--output_file={output.path}",
            f"--part_file={part_file}",
            f"--part_name={part_name}",
        ]

        proc = _ScriptInvocation(argv=cmd, env=self.dispatcher.tool_env or None)
        async for msg in proc:
            await self.add_message_obj(msg)
        if proc.returncode != 0:
            output.path.unlink(missing_ok=True)
            raise BuildError(f"xc7frames2bit failed with exit code {proc.returncode}")
        self.info("Frames to bitstream complete")
