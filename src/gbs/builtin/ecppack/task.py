"""ecppack bitstream generation tasks"""

from __future__ import annotations
import re

from ...build.task import Task, BuildError
from ...build.subprocess import MessageSubprocess
from ...ui.messages import MessageSeverity, ToolMessage


class EcppackInvocation(MessageSubprocess):
    """Message parser for ecppack output"""

    # ecppack message format: "Level: message"
    msg_pattern = re.compile(r'^(?P<level>Info|Warning|Error|Fatal):\s+(?P<message>.*)$')

    level_map = {
        "info": MessageSeverity.INFO,
        "warning": MessageSeverity.WARNING,
        "error": MessageSeverity.ERROR,
        "fatal": MessageSeverity.FATAL,
    }

    async def stderr_transform(self, lines):
        """Parse ecppack stderr output into ToolMessage objects"""
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
    """ecppack bitstream generation task"""

    def __init__(
        self,
        dispatcher: "Dispatcher",
        inputs: list,
        outputs: list,
    ):
        topcell = dispatcher.context.get_topcell()
        super().__init__(
            dispatcher=dispatcher,
            name=f"ecppack_{topcell}",
            inputs=inputs,
            outputs=outputs,
            description=f"ecppack {topcell}",
        )

    async def work(self) -> None:
        """Execute ecppack to generate binary bitstream"""
        # Ensure output directory exists
        output, = self.outputs
        output_path = output.path
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # Get input config
        input, = self.inputs
        config_path = input.path

        self.info(f"Generating binary bitstream: {output_path.name}")

        # Run ecppack
        process = EcppackInvocation(env=self.dispatcher.tool_env or None, argv=[
            self.dispatcher._get_ecppack_executable(),
            "--input", str(config_path),
            "--bit", str(output_path),
        ])

        async for msg in process:
            await self.add_message_obj(msg)

        if process.returncode != 0:
            # ecppack may create an empty file in case of error... delete it.
            output_path.unlink(missing_ok=True)
            raise BuildError(f"ecppack failed with exit code {process.returncode}")

        self.info("Bitstream generation complete")
