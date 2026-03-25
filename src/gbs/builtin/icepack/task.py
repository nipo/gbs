"""icepack bitstream generation tasks"""

from __future__ import annotations
import re

from ...build.task import Task, BuildError
from ...build.subprocess import MessageSubprocess
from ...ui.messages import MessageSeverity, ToolMessage

class IcepackInvocation(MessageSubprocess):
    """Message parser for icepack output"""

    # nextpnr message format: "Level: message"
    msg_pattern = re.compile(r'^(?P<level>Info|Warning|Error|Fatal):\s+(?P<message>.*)$')

    level_map = {
        "info": MessageSeverity.INFO,
        "warning": MessageSeverity.WARNING,
        "error": MessageSeverity.ERROR,
        "fatal": MessageSeverity.FATAL,
    }

    async def stderr_transform(self, lines):
        """Parse nextpnr stderr output into ToolMessage objects"""
        async for line in lines:
            match = self.msg_pattern.match(line)
            if match:
                level_str = match.group('level').lower()
                message = match.group('message')
                severity = self.level_map.get(level_str, MessageSeverity.INFO)
                yield ToolMessage(
                    severity=severity,
                    message=message,
                )
            else:
                # Unstructured output
                yield ToolMessage(
                    severity=MessageSeverity.DEBUG,
                    message=line,
                )

class Pack(Task):
    """icepack bitstream generation task"""

    def __init__(
        self,
        dispatcher: "Dispatcher",
        inputs: list,
        outputs: list,
    ):
        topcell = dispatcher.context.get_topcell()
        super().__init__(
            dispatcher=dispatcher,
            name=f"icepack_{topcell}",
            inputs=inputs,
            outputs=outputs,
            description=f"icepack {topcell}",
        )

    async def work(self) -> None:
        """Execute icepack to generate binary bitstream"""
        # Ensure output directory exists
        output, = self.outputs
        output_path = output.path
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # Get input ASC
        input, = self.inputs
        asc_path = input.path

        self.info(f"Generating binary bitstream: {output_path.name}")

        # Run icepack
        process = IcepackInvocation(env=self.dispatcher.tool_env or None, argv=[
            self.dispatcher._get_icepack_executable(),
            str(asc_path),
            str(output_path),
        ])

        async for msg in process:
            await self.add_message_obj(msg)

        if process.returncode != 0:
            # icepack still creates an empty file in case of
            # error... delete it.
            output_path.unlink(missing_ok = True)
            raise BuildError(f"icepack failed with exit code {process.returncode}")

        self.info("Bitstream generation complete")
