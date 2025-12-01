import asyncclick as click
import re
import sys

class ReMatchGroup(click.Group):
    """A group that resolves commands by name where any amount of
    letters can be removed as long as it is non-ambiguous.
    """
    def get_command(self, ctx, cmd_name):
        rv = super().get_command(ctx, cmd_name)
        if rv is not None:
            return rv

        if sys.stdout.isatty():
            r = re.compile('.*'.join(cmd_name))

            matches = [
                x for x in self.list_commands(ctx)
                if r.match(x)
            ]

            if not matches:
                return None

            if len(matches) == 1:
                return click.Group.get_command(self, ctx, matches[0])

            ctx.fail(f"{cmd_name} is ambiguous, matches {', '.join(sorted(matches))}")
        else:
            sys.stderr.write("Warning: short commands are not accepted from scripts\n")
            return None

    async def resolve_command(self, ctx, args):
        # always return the full command name
        _, cmd, args = await super().resolve_command(ctx, args)
        return cmd.name, cmd, args
