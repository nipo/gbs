"""Utility functions for GBS"""

import os
from pathlib import Path


def expand_path(path_str: str) -> Path:
    """Expand ~ and environment variables in a path string

    Performs the following expansions on path strings from configuration:
    1. Tilde expansion (~) to user home directory
    2. Environment variable expansion ($VAR or ${VAR})

    Args:
        path_str: Path string that may contain ~ and/or environment variables

    Returns:
        Expanded Path object

    Examples:
        >>> expand_path("~/projects/mylib")
        Path('/home/user/projects/mylib')

        >>> expand_path("$HOME/projects/mylib")
        Path('/home/user/projects/mylib')

        >>> expand_path("${FPGA_LIBS}/common")
        Path('/opt/fpga/libs/common')

        >>> expand_path("~/tools/$TOOL_VERSION/bin")
        Path('/home/user/tools/1.2.3/bin')
    """
    # First expand environment variables
    expanded = os.path.expandvars(path_str)

    # Then expand tilde
    path = Path(expanded).expanduser()

    return path


__all__ = ['expand_path']
