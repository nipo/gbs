"""openxc7 (Xilinx Series-7 flow) CLI utilities.

The openxc7 apio package ships a limited set of pre-built chipdb
`.bin` files under `<pkg>/chipdb`. Any Series-7 part not on that
short list needs a chipdb built from prjxray-db and
nextpnr-xilinx-meta before nextpnr-xilinx will accept it.

`gbs openxc7 chipdb build <part>` wraps the two-step
`bbaexport.py` + `bbasm` invocation so users do not need to know
the openxc7 install layout or Python entry points.
"""

import asyncclick as click
import asyncio
import os
import tempfile
from pathlib import Path

from ..logging import get_logger
from ..utils import expand_path
from ..builtin import xilinx_part
from .group import ReMatchGroup


@click.group("openxc7", cls=ReMatchGroup)
@click.option(
    "-t", "--tool",
    "openxc7_tool",
    default="bbasm",
    metavar="IDENTIFIER",
    help="Tool identifier picking the openxc7 install (default 'bbasm'; "
         "add :variant or @version to disambiguate when several apio "
         "installs are configured).",
)
@click.pass_context
async def openxc7(ctx, openxc7_tool: str):
    """openxc7 (Xilinx Series-7) utilities.

    Assumes an openxc7 apio package is installed and reachable via
    the GBS config (typically through the `apio` toolchain provider).
    """
    ctx.ensure_object(dict)
    ctx.obj["openxc7_tool"] = openxc7_tool


@openxc7.group("chipdb", cls=ReMatchGroup)
def chipdb_group():
    """Manage nextpnr-xilinx chipdb files."""
    pass


@chipdb_group.command("build")
@click.argument("part")
@click.option(
    "--output", "output_arg",
    type=click.Path(path_type=Path),
    help="Destination path for the .bin (default: <install>/chipdb/<key>.bin "
         "so nextpnr-xilinx picks it up automatically).",
)
@click.option(
    "--keep-bba",
    is_flag=True,
    help="Keep the intermediate .bba text file (roughly 250 MB per part).",
)
@click.pass_context
async def chipdb_build(ctx, part: str, output_arg: Path | None, keep_bba: bool):
    """Build a nextpnr-xilinx chipdb for PART.

    PART is in vivado form, e.g. `xc7a35t-1cpg236`. The chipdb file
    is named `<name><package>.bin` (speed grade stripped) because
    nextpnr-xilinx keys chipdbs without the speed grade even though
    bbaexport.py needs the speed grade in its `--device` argument.
    """
    logger = get_logger()
    gbs_config = ctx.obj["gbs_config"]
    anchor = ctx.obj["openxc7_tool"]

    install_root = _resolve_install_root(gbs_config, anchor)

    bbasm = install_root / "bin" / "bbasm"
    python = install_root / "libexec" / "python3.12"
    export_py = install_root / "share" / "nextpnr" / "python" / "bbaexport.py"
    chipdb_dir_default = install_root / "chipdb"

    missing = [p for p in (bbasm, python, export_py) if not p.is_file()]
    if missing:
        raise click.ClickException(
            f"openxc7 layout mismatch under {install_root}: missing "
            + ", ".join(str(p.relative_to(install_root)) for p in missing)
        )

    key = xilinx_part.chipdb_key(part)
    m = xilinx_part.parse_part(part)
    if key is None or m is None:
        raise click.ClickException(
            f"Cannot parse part '{part}'; expected the vivado form "
            f"xc<name>-<speed><package> (e.g. xc7a35t-1cpg236)."
        )
    speed = m.group("speed").lstrip("-")
    export_device = f"{key}-{speed}"

    output = expand_path(str(output_arg)) if output_arg else chipdb_dir_default / f"{key}.bin"
    output.parent.mkdir(parents=True, exist_ok=True)

    if output.exists():
        click.echo(f"Note: overwriting existing chipdb {output}")

    with tempfile.TemporaryDirectory(prefix="gbs-openxc7-") as td:
        bba = Path(td) / f"{key}.bba"

        env = os.environ.copy()
        env["PYTHONPATH"] = str(export_py.parent)

        click.echo(f"[1/2] bbaexport {export_device} -> {bba.name}")
        rc = await _run(
            [str(python), str(export_py),
             "--device", export_device,
             "--bba", str(bba)],
            env=env,
        )
        if rc != 0:
            raise click.ClickException(f"bbaexport.py exited with code {rc}")

        click.echo(f"[2/2] bbasm {bba.name} -> {output}")
        rc = await _run(
            [str(bbasm), "--le", "--files", str(bba), str(output)],
            env=None,
        )
        if rc != 0:
            output.unlink(missing_ok=True)
            raise click.ClickException(f"bbasm exited with code {rc}")

        if keep_bba:
            kept = output.with_suffix(".bba")
            bba.replace(kept)
            click.echo(f"Kept intermediate .bba at {kept}")

    click.echo(f"Wrote chipdb {output}")


def _resolve_install_root(gbs_config, anchor: str) -> Path:
    """Follow the anchor tool's executable back to the openxc7 install root.

    Every openxc7 binary lives under `<root>/bin/`, so stripping the
    last two path components from the executable yields the package
    root. Raises ClickException with a clear message if the tool is
    absent or has no executable set.
    """
    tool = gbs_config.get_tool(anchor)
    if tool is None:
        raise click.ClickException(
            f"Tool {anchor!r} is not configured. Enable the apio toolchain "
            f"in your .gbs.yaml, or add an explicit tool entry for the "
            f"openxc7 install you want to use."
        )
    exe = tool.config.get("executable")
    if not exe:
        raise click.ClickException(
            f"Tool {anchor!r} has no 'executable' config; cannot locate "
            f"the openxc7 install root."
        )
    resolved = expand_path(exe).resolve()
    root = resolved.parent.parent
    if not root.is_dir():
        raise click.ClickException(
            f"Derived openxc7 install root {root} does not exist "
            f"(from tool {anchor!r} = {resolved})."
        )
    return root


async def _run(cmd: list[str], env: dict | None) -> int:
    """Spawn a subprocess and stream its output straight through."""
    proc = await asyncio.create_subprocess_exec(*cmd, env=env)
    return await proc.wait()
