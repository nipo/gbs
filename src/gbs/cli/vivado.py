"""Xilinx Vivado CLI utilities.

`gbs vivado ip-check` checks that a packaged IP synthesizes out of
context, either a zip or directory given on the command line, or the
IPs a project packages.
"""

import asyncclick as click
from pathlib import Path

from ..cli import get_project_file
from .group import ReMatchGroup


@click.group("vivado", cls=ReMatchGroup)
def vivado():
    """Xilinx Vivado utilities."""
    pass


@vivado.command("ip-check")
@click.argument(
    "ip",
    required=False,
    type=click.Path(exists=True, path_type=Path),
)
@click.option(
    "--part",
    metavar="PART",
    help="Target part to synthesize the IP for (IP mode only).",
)
@click.option(
    "-c", "--config", "param_specs",
    multiple=True,
    metavar="NAME=VALUE",
    help="Set an IP parameter. May be given multiple times.",
)
@click.option(
    "--repo", "repos",
    multiple=True,
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="IP repository directory the IP depends on (IP mode only). "
         "May be given multiple times.",
)
@click.option(
    "--bus-zip", "bus_zips",
    multiple=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Archive of bus definitions the IP refers to (IP mode only). "
         "May be given multiple times.",
)
@click.option(
    "-o", "--output", "report",
    type=click.Path(dir_okay=False, path_type=Path),
    help="Utilization report path (default: "
         "gbs-build/<output group>/ip-utilization.rpt).",
)
@click.option(
    "--project", "project_mode",
    is_flag=True,
    help="Check the IPs the project packages instead of an IP file.",
)
@click.option(
    "-f", "--file", "project_file",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Project file (project mode only, auto-discovered if not "
         "specified).",
)
@click.option(
    "-g", "--group", "group_names",
    multiple=True,
    metavar="GROUP",
    help="Output group to check (project mode only; default: every group "
         "producing a packaged IP). May be given multiple times.",
)
@click.pass_context
async def ip_check(ctx, ip, part, param_specs, repos, bus_zips, report,
                   project_mode, project_file, group_names):
    """Check that a packaged IP synthesizes out of context.

    The IP is instantiated by VLNV in a scratch Vivado project, its
    targets are generated and it is synthesized with synth_ip. The
    utilization of the result is written as a report.

    Give either IP, a packaged IP zip or directory, with --part; or
    --project to package and check the IPs of a project.
    """
    from ..build.task import BuildError
    from ..builtin.vivado_ip.check import IpCheck, IpCheckError
    from .project import _project_build, _project_load

    gbs_config = ctx.obj.get("gbs_config")

    if (ip is None) == (not project_mode):
        raise click.UsageError("Give either an IP or --project")

    if project_mode:
        for name, given in (("--part", part), ("--repo", repos),
                            ("--bus-zip", bus_zips)):
            if given:
                raise click.UsageError(f"{name} only applies to an IP")
    else:
        if part is None:
            raise click.UsageError("Checking an IP needs --part")
        for name, given in (("-f/--file", project_file),
                            ("-g/--group", group_names)):
            if given:
                raise click.UsageError(f"{name} only applies with --project")

    try:
        params = IpCheck.params_parse(param_specs)
    except IpCheckError as e:
        raise click.UsageError(str(e))

    if project_mode:
        ctx.obj["project_file_option"] = project_file
        proj = await _project_load(get_project_file(ctx), gbs_config)
        try:
            checked = IpCheck.project_extend(proj, list(group_names),
                                             params, report)
        except IpCheckError as e:
            raise click.ClickException(str(e))
    else:
        try:
            proj = IpCheck.zip_project(ip, part, params, list(repos),
                                       list(bus_zips), report, gbs_config)
        except BuildError as e:
            raise click.ClickException(str(e))
        checked = [(proj.model.output_groups[0].name,
                    proj.model.output_groups[0].outputs[0].path)]

    await _project_build(proj, [name for name, _ in checked])

    for name, path in checked:
        click.echo(f"{name}: {path}")
