"""Remote execution commands

`gbs remote serve --stdio` is what a local gbs runs on a remote host,
through ssh, to delegate work to it. `gbs remote info` connects to a
remote host and shows what it provides.
"""

import asyncclick as click
from pathlib import Path

from .group import ReMatchGroup


@click.group("remote", cls=ReMatchGroup)
def remote():
    """Remote execution commands."""
    pass


@remote.command("serve")
@click.option(
    "--stdio",
    is_flag=True,
    help="Serve one client over standard input and output.",
)
@click.option(
    "--blob-store",
    type=click.Path(file_okay=False, path_type=Path),
    help="Blob store directory (default: $XDG_CACHE_HOME/gbs/remote-blobs).",
)
@click.option(
    "--keep",
    is_flag=True,
    help="Keep the temporary workspace when the connection ends.",
)
@click.pass_context
async def serve(ctx, stdio: bool, blob_store: Path | None, keep: bool):
    """Serve a local gbs instance delegating work to this host."""
    from ..remote.manifest import BlobStore
    from ..remote.server import RemoteServer, StdioChannel, Workspace

    if not stdio:
        raise click.UsageError("Only --stdio serving is supported")
    channel = await StdioChannel.open()
    server = RemoteServer(
        channel,
        ctx.obj["gbs_config"],
        BlobStore(blob_store or RemoteServer.blob_store_default()),
        Workspace(keep=keep),
    )
    await server.run()


@remote.command("info")
@click.argument("destination")
@click.pass_context
async def info(ctx, destination: str):
    """Show what the gbs on DESTINATION provides.

    DESTINATION is a host from `remote_hosts:` in the configuration,
    or else an ssh destination.
    """
    from ..remote.client import RemoteHost, RemoteHostError

    try:
        host = await RemoteHost.open(destination, ctx.obj["gbs_config"])
    except RemoteHostError as e:
        raise click.ClickException(str(e))
    async with host:
        identity = host.hello.identity
        click.echo(f"gbs {identity.gbs}, protocol {identity.protocol}")
        click.echo("tools:")
        for tool in host.hello.tools:
            status = "" if tool.problem is None else f"  # unusable: {tool.problem}"
            click.echo(f"  {tool.identifier}{status}")
