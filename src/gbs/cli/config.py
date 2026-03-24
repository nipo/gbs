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
            if tool.config:
                lines.append(f"    config:")
                for k, v in tool.config.items():
                    lines.append(f"      {k}: {v}")

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
