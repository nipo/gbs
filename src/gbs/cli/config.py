"""GBS Config Commands

Commands for inspecting GBS configuration.
"""

import asyncclick as click

from ..logging import get_logger
from .group import ReMatchGroup

@click.group(invoke_without_command=False, cls = ReMatchGroup)
@click.pass_context
async def config(ctx):
    """Configuration inspection commands"""
    pass


@config.command()
@click.pass_context
async def dump(ctx):
    """Dump merged configuration with origin annotations"""
    gbs_config = ctx.obj.get("gbs_config")

    if gbs_config is None:
        click.echo("No configuration loaded.", err=True)
        return

    lines = []

    # Config files loaded
    if gbs_config.loaded_files:
        lines.append("# Configuration files loaded:")
        for f in gbs_config.loaded_files:
            lines.append(f"#   - {f}")
        lines.append("")

    # Global settings
    if gbs_config.max_parallel is not None:
        lines.append(f"max_parallel: {gbs_config.max_parallel}")
    if gbs_config.max_log_count is not None:
        lines.append(f"max_log_count: {gbs_config.max_log_count}")
    from ..config.model import DEFAULT_FILE_URL_TEMPLATE
    if gbs_config.file_url_template != DEFAULT_FILE_URL_TEMPLATE:
        lines.append(f"file_url_template: {gbs_config.file_url_template}")

    # Tools
    if gbs_config.tools:
        lines.append("")
        lines.append("tools:")
        for tool in gbs_config.tools:
            origin = f"  # from {tool.origin}" if tool.origin else ""
            lines.append(f"  - name: {tool.name}{origin}")
            if tool.variant is not None:
                lines.append(f"    variant: {tool.variant}")
            if tool.version is not None:
                lines.append(f"    version: {tool.version}")
            if tool.config:
                lines.append(f"    config:")
                for k, v in tool.config.items():
                    lines.append(f"      {k}: {v}")

    # Toolchains (unexpanded specs, for context)
    if gbs_config.toolchains:
        lines.append("")
        lines.append("toolchains:")
        for spec in gbs_config.toolchains:
            origin = f"  # from {spec.origin}" if spec.origin else ""
            lines.append(f"  - type: {spec.type}{origin}")
            for k, v in spec.options.items():
                lines.append(f"    {k}: {v}")

    # Repositories
    if gbs_config.repositories:
        lines.append("")
        lines.append("repositories:")
        for repo in gbs_config.repositories:
            origin = repo.get('_origin')
            origin_comment = f"  # from {origin}" if origin else ""
            path = repo.get('path', '?')
            loader = repo.get('loader')
            lines.append(f"  - path: {path}{origin_comment}")
            if loader:
                lines.append(f"    loader: {loader}")
            if repo.get('name'):
                lines.append(f"    name: {repo['name']}")

    click.echo('\n'.join(lines))


def _format_annotation(*parts) -> str:
    """Join non-empty pieces into a trailing '  # a, b' comment."""
    populated = [p for p in parts if p]
    if not populated:
        return ""
    return "  # " + ", ".join(populated)


@config.command()
@click.argument("identifier", required=False)
@click.pass_context
async def tool(ctx, identifier: str | None):
    """List configured tools, optionally filtered by identifier.

    IDENTIFIER is 'name[:variant][@version]'. Any component may be
    omitted to widen the match; each specified component filters by
    exact equality (same rule as GBSConfig.get_tool).

    With no argument, lists every tool. The '(default)' marker on a
    line means that entry is the first-matching one for its name and
    would be picked by an unqualified selection.
    """
    from ..config.model import GBSConfig

    gbs_config = ctx.obj.get("gbs_config")
    if gbs_config is None:
        click.echo("No configuration loaded.", err=True)
        return

    if identifier is not None:
        name_f, variant_f, version_f = GBSConfig._parse_identifier(identifier)
        # A leading ':' or '@' means the user only wants to filter by
        # variant/version - treat empty name as 'any'.
        if name_f == "":
            name_f = None
    else:
        name_f = variant_f = version_f = None

    matches = []
    for t in gbs_config.tools:
        if name_f is not None and t.name != name_f:
            continue
        if variant_f is not None and t.variant != variant_f:
            continue
        if version_f is not None and t.version != version_f:
            continue
        matches.append(t)

    if not matches:
        if identifier:
            click.echo(f"No tools matching '{identifier}'.", err=True)
        else:
            click.echo("No tools configured.", err=True)
        return

    # A tool is 'default' if it's the first entry for its name in the
    # full config (not just among filtered matches) - matches the
    # first-match rule in GBSConfig.get_tool.
    seen_names: set[str] = set()
    default_ids: set[int] = set()
    for t in gbs_config.tools:
        if t.name not in seen_names:
            seen_names.add(t.name)
            default_ids.add(id(t))

    for t in matches:
        marker = " (default)" if id(t) in default_ids else ""
        origin = f"from {t.origin}" if t.origin else ""
        via = f"via {t.via}" if t.via else ""
        annotation = _format_annotation(origin, via)
        click.echo(f"  {t.identifier}{marker}{annotation}")
        for k, v in t.config.items():
            click.echo(f"    {k}: {v}")


@config.command()
@click.argument("identifier", required=False)
@click.pass_context
async def toolchain(ctx, identifier: str | None):
    """List configured toolchains, optionally filtered by identifier.

    IDENTIFIER is 'type[:variant]'. Any component may be omitted;
    each specified component filters by exact equality.

    Each entry shows its options and every tool it expanded to.
    """
    gbs_config = ctx.obj.get("gbs_config")
    if gbs_config is None:
        click.echo("No configuration loaded.", err=True)
        return

    type_f: str | None
    variant_f: str | None
    if identifier is None:
        type_f = variant_f = None
    elif ':' in identifier:
        type_f, variant_f = identifier.split(':', 1)
    else:
        type_f, variant_f = identifier, None
    # Leading ':' means variant-only filter (e.g. ':apio-2026').
    if type_f == "":
        type_f = None

    matches = []
    for spec in gbs_config.toolchains:
        if type_f is not None and spec.type != type_f:
            continue
        if variant_f is not None and spec.variant != variant_f:
            continue
        matches.append(spec)

    if not matches:
        if identifier:
            click.echo(f"No toolchains matching '{identifier}'.", err=True)
        else:
            click.echo("No toolchains configured.", err=True)
        return

    for spec in matches:
        origin = f"from {spec.origin}" if spec.origin else ""
        annotation = _format_annotation(origin)
        click.echo(f"  {spec.identifier}{annotation}")
        for k, v in spec.options.items():
            click.echo(f"    {k}: {v}")

        expanded = [t for t in gbs_config.tools if t.via == spec.identifier]
        if expanded:
            click.echo("    expanded tools:")
            for t in expanded:
                click.echo(f"      {t.identifier}")
